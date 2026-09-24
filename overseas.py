"""Website-only outbound route; never modify v2rayN or persist node credentials."""
import asyncio
import copy
import json
import os
from pathlib import Path
import socket


def dedicated_config(source, port):
    outbounds = copy.deepcopy(source.get('outbounds', []))
    proxy = next((o for o in outbounds if o.get('tag') == 'proxy' and o.get('protocol') not in {'freedom', 'blackhole', 'dns'}), None)
    if not proxy:
        raise RuntimeError('当前 v2rayN 配置没有可用的 proxy 节点，请选择海外节点后重启网站')
    return {
        'log': {'loglevel': 'none'},
        'inbounds': [{'tag': 'cinema-overseas', 'listen': '127.0.0.1', 'port': port, 'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': outbounds,
        'routing': {'domainStrategy': 'AsIs', 'rules': [{'type': 'field', 'inboundTag': ['cinema-overseas'], 'outboundTag': 'proxy'}]},
    }


class OverseasRoute:
    def __init__(self):
        self.process = None
        self.previous = {}
        self.proxy = None

    async def start(self):
        mode = os.environ.get('HONGGUO_NETWORK_MODE', 'overseas')
        if mode == 'direct':
            return
        explicit = os.environ.get('HONGGUO_UPSTREAM_PROXY')
        if explicit:
            self.proxy = explicit
        else:
            base = Path.home() / 'Library/Application Support/v2rayN'
            binary = Path(os.environ.get('HONGGUO_XRAY_BIN', str(base / 'bin/xray/xray')))
            config = Path(os.environ.get('HONGGUO_XRAY_CONFIG', str(base / 'binConfigs/config.json')))
            if not binary.is_file() or not config.is_file():
                raise RuntimeError('默认海外线路不可用：请配置 HONGGUO_UPSTREAM_PROXY；海外服务器可明确设置 HONGGUO_NETWORK_MODE=direct')
            # Reserve an unused loopback port; Xray binds it immediately after release.
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            try:
                source = json.loads(config.read_text())
                payload = json.dumps(dedicated_config(source, port)).encode()
            except (OSError, ValueError) as exc:
                raise RuntimeError('无法读取当前代理配置，请重启 v2rayN 后再试') from None
            self.process = await asyncio.create_subprocess_exec(str(binary), 'run', '-config', 'stdin:', '-format', 'json', cwd=binary.parent,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            self.process.stdin.write(payload)
            await self.process.stdin.drain()
            self.process.stdin.close()
            del source, payload
            self.proxy = f'socks5://127.0.0.1:{port}'
            try:
                async with asyncio.timeout(8):
                    while True:
                        if self.process.returncode is not None:
                            raise RuntimeError('网站专用海外线路启动失败，请检查当前代理节点')
                        try:
                            reader, writer = await asyncio.open_connection('127.0.0.1', port)
                            writer.close()
                            await writer.wait_closed()
                            break
                        except OSError:
                            await asyncio.sleep(.05)
            except BaseException:
                await self.stop()
                raise
        # Source modules create their own HTTP clients. Apply one explicit route to
        # all of them, including device registration and public catalog requests.
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'NO_PROXY', 'no_proxy'):
            self.previous[key] = os.environ.get(key)
            os.environ[key] = 'localhost,127.0.0.1,::1' if key.lower() == 'no_proxy' else self.proxy

    async def stop(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.previous.clear()
