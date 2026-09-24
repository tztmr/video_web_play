import asyncio
import hashlib
import json
import logging
import random
import time
from email.utils import parsedate_to_datetime
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from core.pure_sign import generate_all_headers
from core.device_pool import device_pool, DeviceEntry
from core.device_register import register_device_and_key

logger = logging.getLogger("fanqie.client")
UA = "com.dragon.read"


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "").strip()
    try:
        if value.isdigit():
            return float(int(value))
        if value:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _is_device_error(message: str) -> bool:
    # Unknown business codes may describe unavailable content or bad arguments.
    # Only explicit identity errors justify replacing a device.
    message = message.casefold().replace("_", " ")
    return any(marker in message for marker in (
        "设备已失效", "设备失效", "设备已过期", "设备过期", "无效设备", "设备无效", "设备未注册",
        "invalid device", "device invalid", "device expired", "device not registered",
        "device not found", "invalid install id",
    ))


def _with_device_identity(url: str, device: DeviceEntry) -> str:
    """Make the request identity match the device used for signing/failover."""

    parsed = urlparse(url)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    identity = {
        "device_id": str(device.device_id),
        "iid": str(device.install_id),
    }
    for key, value in identity.items():
        params = [(existing_key, existing_value) for existing_key, existing_value in params if existing_key != key]
        params.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(params)))


def _trigger_ensure_pool() -> None:
    """请求成功后异步补池到常备数量(调度器未启动时静默跳过)。"""

    try:
        from core.scheduler import scheduler
        scheduler.ensure_pool_soon()
    except Exception:
        pass

class PureSignedClient:

    # Includes device lookup/registration, retry delays and all HTTP attempts.
    CALL_TIMEOUT = 90.0
    # 并发注册新设备的信号量, 防止高并发失败时注册风暴
    REGISTER_SEMAPHORE = asyncio.Semaphore(3)

    def __init__(self, timeout: float = 15.0):
        self._pool_registration: asyncio.Task | None = None
        self._pool_registration_waiters = 0
        self._client = httpx.AsyncClient(
            timeout=timeout,
            verify=False,
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
        )

    async def close(self):
        if self._pool_registration is not None:
            self._pool_registration.cancel()
            await asyncio.gather(self._pool_registration, return_exceptions=True)
        await self._client.aclose()

    async def _register_fresh_device(self, *, only_if_empty: bool = False) -> Optional[DeviceEntry]:
        """注册一台全新设备并返回; 注册失败返回 None。"""

        async with PureSignedClient.REGISTER_SEMAPHORE:
            # Another registration (including the scheduler) may have filled
            # the pool while this request waited for the shared admission gate.
            if only_if_empty:
                entry = device_pool.get_best_device()
                if entry and entry.secret_key:
                    return entry
            try:
                dev = await register_device_and_key()
            except Exception as e:
                logger.warning("注册新设备失败: %s", type(e).__name__)
                return None
        return DeviceEntry(
            device_id=dev["device_id"],
            install_id=dev["install_id"],
            secret_key=dev["secret_key"],
        )

    async def _get_device_with_auto_register(
        self, preferred_device_id: str | None = None,
    ) -> DeviceEntry:

        if preferred_device_id:
            preferred = device_pool.get_device(preferred_device_id)
            if preferred and preferred.secret_key:
                return preferred

        entry = device_pool.get_best_device()
        if entry and entry.secret_key:
            return entry

        # All requests through the app's shared client await the same refill,
        # including its failure. Cancelling one waiter must not cancel peers.
        if self._pool_registration is None:
            logger.info("设备池为空, 自动注册新设备...")
            self._pool_registration = asyncio.create_task(
                self._register_fresh_device(only_if_empty=True),
            )
        task = self._pool_registration
        self._pool_registration_waiters += 1
        try:
            entry = await asyncio.shield(task)
            if entry is None:
                raise RuntimeError("设备注册暂不可用，请稍后重试")
            return entry
        finally:
            self._pool_registration_waiters -= 1
            if self._pool_registration_waiters == 0:
                # Do not leave orphan network work after the last request has
                # disconnected or exhausted its total time budget.
                if self._pool_registration is task:
                    self._pool_registration = None
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def _make_headers(self, query_string: str, body_bytes: Optional[bytes] = None,
                      aid: int = 1967) -> dict:

        sign_headers = generate_all_headers(query_string, body_bytes=body_bytes, aid=aid)
        ticket = str(int(time.time() * 1000))
        extra = {
            "x-ss-req-ticket": ticket,
            "x-reading-request": f"{int(ticket) + 1}-{random.randint(10000000, 99999999)}",
        }
        result = dict(sign_headers)
        result.update(extra)
        return result

    async def signed_get(
        self,
        url: str,
        aid: int = 1967,
        device: DeviceEntry | None = None,
    ) -> httpx.Response:

        if device is not None:
            url = _with_device_identity(url, device)
        parsed = urlparse(url)
        query_string = parsed.query or ""
        sign_headers = self._make_headers(query_string, aid=aid)
        headers = {"User-Agent": UA, **sign_headers}
        return await self._client.get(url, headers=headers)

    async def signed_post(self, url: str, data: str = "",
                          content_type: str = "application/x-www-form-urlencoded",
                          aid: int = 1967,
                          device: DeviceEntry | None = None) -> httpx.Response:

        if device is not None:
            url = _with_device_identity(url, device)
        parsed = urlparse(url)
        query_string = parsed.query or ""
        body_bytes = data.encode("utf-8") if isinstance(data, str) else data
        sign_headers = self._make_headers(query_string, body_bytes=body_bytes, aid=aid)
        headers = {
            "User-Agent": UA,
            "Content-Type": content_type,
            **sign_headers,
        }
        return await self._client.post(url, content=body_bytes, headers=headers)

    async def call_with_device(
        self,
        url_builder,
        method: str = "GET",
        data: str = "",
        aid: int = 1967,
        max_device_retries: int = 3,
        need_key: bool = False,
        content_type: str = "application/x-www-form-urlencoded",
        preferred_device_id: str | None = None,
        return_device_id: bool = False,
        validate_upstream: Callable[[dict], str | None] | None = None,
    ) -> dict:
        """最多尝试 max_device_retries 台设备，仅设备错误触发身份切换。"""
        rounds = max(1, int(max_device_retries))
        deadline = asyncio.get_running_loop().time() + self.CALL_TIMEOUT
        last_err = ""
        tried_devices: set[str] = set()
        try:
            async with asyncio.timeout_at(deadline):
                for attempt in range(rounds):
                    try:
                        entry = await self._get_device_with_auto_register(
                            preferred_device_id if attempt == 0 else None,
                        )
                    except Exception as exc:
                        logger.warning("设备获取失败: %s", type(exc).__name__)
                        return {"ok": False, "msg": "设备注册暂不可用，请稍后重试", "failure_kind": "transient"}

                    # Prefer existing unused devices; register only when the
                    # available pool has actually been exhausted by this call.
                    if entry.device_id in tried_devices:
                        entry = await self._register_fresh_device()
                        if entry is None:
                            return {"ok": False, "msg": "设备注册暂不可用，请稍后重试", "failure_kind": "transient"}
                    device_id = entry.device_id
                    tried_devices.add(device_id)
                    result = await self._do_call(
                        url_builder(device_id), method, data, aid,
                        content_type=content_type, device=entry, _deadline=deadline,
                    )
                    if result["ok"] and validate_upstream is not None:
                        validation_error = validate_upstream(result["upstream"])
                        if validation_error:
                            result = {"ok": False, "msg": validation_error, "failure_kind": "device"}
                    if result["ok"]:
                        device_pool.report_success(device_id)
                        if need_key:
                            result["device_id"] = device_id
                            result["secret_key"] = entry.secret_key
                        elif return_device_id:
                            result["device_id"] = device_id
                        _trigger_ensure_pool()
                        return result
                    if result.get("failure_kind") != "device":
                        return result
                    last_err = result["msg"]
                    device_pool.report_failure(device_id)
                    logger.warning("设备请求失败 (attempt %d/%d): %s", attempt + 1, rounds, last_err)
                    # No registration or wait after the final failed round.
                    if attempt + 1 < rounds:
                        await asyncio.sleep(0.2)
        except TimeoutError:
            return {"ok": False, "msg": "上游请求超过总等待时限，请稍后重试", "failure_kind": "transient"}
        return {"ok": False, "msg": f"尝试 {len(tried_devices)} 台设备后仍失败: {last_err}", "failure_kind": "device"}

    async def _do_call(self, url: str, method: str, data: str, aid: int,
                       _retry: int = 1,
                       content_type: str = "application/x-www-form-urlencoded",
                       device: DeviceEntry | None = None,
                       _deadline: float | None = None) -> dict:

        deadline = _deadline if _deadline is not None else asyncio.get_running_loop().time() + self.CALL_TIMEOUT
        result = {"ok": False, "msg": "上游请求失败", "failure_kind": "permanent"}
        for attempt in range(_retry + 1):
            try:
                if method.upper() == "POST":
                    resp = await self.signed_post(
                        url, data, content_type=content_type, aid=aid, device=device,
                    )
                else:
                    resp = await self.signed_get(url, aid=aid, device=device)

                if resp.status_code != 200:
                    kind = "transient" if resp.status_code == 429 or resp.status_code >= 500 else "device" if resp.status_code == 401 else "permanent"
                    result = {"ok": False, "msg": f"上游 HTTP {resp.status_code}", "failure_kind": kind}
                    retry_after = _retry_after_seconds(resp)
                    if retry_after is not None:
                        result["retry_after"] = retry_after
                elif not resp.text:
                    result = {"ok": False, "msg": "上游返回空响应", "failure_kind": "transient"}
                else:
                    try:
                        upstream = resp.json()
                    except json.JSONDecodeError:
                        upstream = None
                    if not isinstance(upstream, dict):
                        result = {"ok": False, "msg": "上游返回无效 JSON", "failure_kind": "transient"}
                    elif upstream.get("code") not in (None, 0):
                        message = str(upstream.get("message") or upstream.get("msg") or "未知")
                        result = {"ok": False, "msg": f"上游业务错误: {message}",
                                  "failure_kind": "device" if _is_device_error(message) else "permanent"}
                    else:
                        return {"ok": True, "upstream": upstream}
            except httpx.TimeoutException:
                result = {"ok": False, "msg": "上游请求超时", "failure_kind": "transient"}
            except httpx.TransportError:
                result = {"ok": False, "msg": "上游网络连接失败，请稍后重试", "failure_kind": "transient"}
            except Exception as exc:
                result = {"ok": False, "msg": f"请求异常: {type(exc).__name__}", "failure_kind": "permanent"}
            if result["failure_kind"] != "transient" or attempt >= _retry:
                return result
            delay = max(0.5 * (2 ** attempt), result.get("retry_after", 0.0))
            if delay >= deadline - asyncio.get_running_loop().time():
                return result
            await asyncio.sleep(delay)
        return result
