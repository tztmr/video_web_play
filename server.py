"""TACO cinema, using the bundled Hongguo API and playback runtime."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import anyio
import httpx
from typing import Literal

ROOT = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get("HONGGUO_SOURCE_DIR", str(ROOT / "vendor/hongguo"))).resolve()
if not (SOURCE / "endpoints/duanju.py").is_file():
    raise RuntimeError("缺少 vendor/hongguo 运行模块，请完整下载本仓库，或设置 HONGGUO_SOURCE_DIR")
DATA = Path(os.environ.get('HONGGUO_WEB_DATA_DIR', str(ROOT / '.data'))).resolve()
CACHE = DATA / "videos"
CACHE.mkdir(parents=True, exist_ok=True)
# Set before importing the source: never write into the downloader's device pool.
os.environ["HONGGUO_DATA_DIR"] = str(DATA)
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE))
if not os.environ.get("HONGGUO_PLAYBACK_TOOLS_DIR"):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg and ffprobe and Path(ffmpeg).parent == Path(ffprobe).parent:
        os.environ["HONGGUO_PLAYBACK_TOOLS_DIR"] = str(Path(ffmpeg).parent)

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from core.http_client import PureSignedClient
from core.playback import while_connected
from endpoints import duanju, web_catalog
from accounts import Accounts
from country_access import CountryAccess
from overseas import OverseasRoute
from video_loader import load_video, video_model
from playback_jobs import PlaybackJobs, publish_cache
from auth_routes import router as auth_router, COOKIE, require_admin

CACHE_LIMIT = 2 * 1024**3
CACHE_TTL = 24 * 3600


def trim_cache(keep: Path | None = None):
    files = sorted(CACHE.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    total, now = 0, time.time()
    for path in files:
        size = path.stat().st_size
        total += size
        if path != keep and (now - path.stat().st_mtime > CACHE_TTL or total > CACHE_LIMIT):
            path.unlink(missing_ok=True)
            path.with_suffix(".json").unlink(missing_ok=True)
            total -= size


@asynccontextmanager
async def lifespan(app):
    app.state.accounts = Accounts(DATA)
    app.state.country_access = CountryAccess(os.environ.get('TACO_GEOIP_DATABASE', str(DATA/'geoip-country.mmdb')))
    route = OverseasRoute()
    try:
        await route.start()
        app.state.client = PureSignedClient(timeout=15)
        from core.video_download import VIDEO_UA
        app.state.video_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=8, pool=15), verify=False,
            follow_redirects=True, headers={'User-Agent': VIDEO_UA},
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16, keepalive_expiry=120))
        app.state.prepare_slot = asyncio.Semaphore(1)
        app.state.model_tasks = {}
        app.state.playback_jobs = PlaybackJobs(app, CACHE, app.state.prepare_slot,
            lambda request, item_id, definition: load_video(request, item_id, definition), trim_cache)
        for pattern in ('*.part', '*.indexed.mp4'):
            for unfinished in CACHE.glob(pattern):
                unfinished.unlink(missing_ok=True)
        trim_cache()
        # Previous versions cached fMP4 directly; repair before accepting requests,
        # so no Range reader can switch between old and new byte offsets.
        for path in CACHE.glob('*.mp4'):
            if not re.fullmatch(r'\d{1,24}_(1080p|720p|540p|480p|360p)\.mp4', path.name):
                continue
            try:
                try:
                    metadata = json.loads(path.with_suffix('.json').read_text())
                except (OSError, ValueError):
                    metadata = {'definition': path.stem.rsplit('_', 1)[-1]}
                if not isinstance(metadata, dict):
                    metadata = {'definition': path.stem.rsplit('_', 1)[-1]}
                if metadata.get('indexed'):
                    continue
                await publish_cache(path, path)
                metadata['indexed'] = True
                path.with_suffix('.json').write_text(json.dumps(metadata))
            except (OSError, ValueError, RuntimeError):
                pass  # Preserve playable old caches if optional re-indexing fails.
        try:
            yield
        finally:
            await app.state.playback_jobs.close()
            model_tasks = list(app.state.model_tasks.values())
            for task in model_tasks:
                task.cancel()
            await asyncio.gather(*model_tasks, return_exceptions=True)
            await app.state.client.close()
            await app.state.video_client.aclose()
    finally:
        try:
            await route.stop()
        finally:
            app.state.country_access.close()


app = FastAPI(title="TACO小剧场", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
HOSTS = os.environ.get('HONGGUO_ALLOWED_HOSTS', '127.0.0.1,localhost,[::1],testserver').split(',')
PUBLIC_URL = os.environ.get('HONGGUO_PUBLIC_URL', '').rstrip('/')
if os.environ.get('HONGGUO_ENV') == 'production' and (not PUBLIC_URL.startswith('https://') or '*' in HOSTS):
    raise RuntimeError('生产环境请设置 HTTPS HONGGUO_PUBLIC_URL 和明确的 HONGGUO_ALLOWED_HOSTS')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=HOSTS)
app.include_router(auth_router)


@app.middleware("http")
async def access_control(request: Request, call_next):
    # Uvicorn receives the peer address set by our private Caddy hop. Headers such
    # as X-Real-IP and CF-Connecting-IP supplied by visitors are never read here.
    status = request.app.state.country_access.status(request.client.host if request.client else None)
    if status != 200:
        message = '暂不支持中国大陆 IP 访问。' if status == 403 else '暂时无法确认访问地区，请稍后再试。'
        headers = {'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'}
        if 'text/html' in request.headers.get('accept', ''):
            return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TACO小剧场</title><body><h1>TACO小剧场</h1><p>'+message+'</p><small><a href="https://db-ip.com">IP Geolocation by DB-IP</a></small></body></html>', status_code=status, headers=headers)
        return JSONResponse({'detail':message, 'code':'REGION_BLOCKED' if status == 403 else 'REGION_UNAVAILABLE'}, status_code=status, headers=headers)
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != (PUBLIC_URL or str(request.base_url).rstrip('/')):
        return JSONResponse({"detail": "请求来源不匹配，请从网站页面操作"}, status_code=403)
    request.state.user = request.app.state.accounts.session(request.cookies.get(COOKIE))
    path = request.url.path
    public = path in {'/login', '/setup', '/api/auth/status', '/api/auth/setup', '/api/auth/login', '/healthz'} or path.startswith(('/s/', '/api/shared/', '/shared-media/'))
    assets = path.startswith('/static/') and not path.endswith('.html')
    if not public and not assets and not request.state.user:
        if path.startswith(('/api/', '/media/')):
            return JSONResponse({'detail': '请先登录'}, status_code=401, headers={'Cache-Control': 'no-store'})
        return RedirectResponse('/login', status_code=303, headers={'Cache-Control': 'no-store'})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: http: data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


# Reuse the actual route handlers, exposing only the browsing operations needed here.
allowed = {"/duanju/search", "/duanju/catalog", "/duanju/discovery", "/duanju/discovery/more", "/duanju/rank"}
for route in duanju.router.routes:
    if route.path in allowed:
        app.router.routes.append(type(route)(path="/api" + route.path, endpoint=route.endpoint, methods=route.methods))
app.include_router(web_catalog.router, prefix="/api")


@app.get("/api/health")
async def health():
    folder = Path(os.environ.get("HONGGUO_PLAYBACK_TOOLS_DIR", "/missing"))
    suffix = ".exe" if sys.platform == "win32" else ""
    ready = all((folder / (tool + suffix)).is_file() for tool in ("ffmpeg", "ffprobe"))
    return {"status": "ok", "playback_ready": ready, "cache_bytes": sum(p.stat().st_size for p in CACHE.glob("*.mp4")), 'network_mode': os.environ.get('HONGGUO_NETWORK_MODE', 'overseas')}


def cached_video(item_id, definition):
    path = CACHE / f'{item_id}_{definition}.mp4'
    if not path.is_file():
        return None
    try:
        actual = json.loads(path.with_suffix('.json').read_text())['definition']
    except (OSError, ValueError, KeyError):
        actual = definition
    path.touch()
    return {'url': '/media/'+path.name, 'definition': actual, 'cached': True, 'prepare_ms': 0, 'bytes': path.stat().st_size}


class ManagedStream(StreamingResponse):
    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    await self.cleanup()


@app.post('/api/play/stream')
async def stream_video(request: Request, item_id: str = Query(pattern=r'^\d{1,24}$'), definition: Literal['1080p', '720p', '540p', '480p', '360p'] = '720p'):
    cached = cached_video(item_id, definition)
    if cached:
        return JSONResponse(cached)
    jobs = request.app.state.playback_jobs
    job = jobs.get((item_id, definition))
    released = False
    async def cleanup():
        nonlocal released
        if released:
            return
        released = True
        await jobs.release(job)
    try:
        await while_connected(job.ready.wait(), request.is_disconnected)
        if job.error:
            raise job.error
        cached = cached_video(item_id, definition)
        if cached:
            await cleanup()
            return JSONResponse(cached)
        return ManagedStream(jobs.read(job), cleanup=cleanup, media_type='video/mp4',
            headers={key: value for key, value in job.headers.items() if key.lower().startswith('x-') or key.lower() in {'cache-control', 'server-timing'}})
    except BaseException:
        with anyio.CancelScope(shield=True):
            await cleanup()
        raise


def prefetch_video(request, item_id, definition):
    if cached_video(item_id, definition):
        return {'status': 'cached'}
    job = request.app.state.playback_jobs.get((item_id, definition), background=True)
    return {'status': 'preparing' if job else 'busy'}


@app.post('/api/play/prefetch')
async def prefetch(request: Request, item_id: str = Query(pattern=r'^\d{1,24}$'), definition: Literal['1080p', '720p', '540p', '480p', '360p'] = '720p'):
    if not request.app.state.accounts.limit('prefetch:'+str(request.state.user['id']), maximum=60):
        raise HTTPException(429, '请求过于频繁')
    return prefetch_video(request, item_id, definition)


@app.post('/api/play/warm')
async def warm_model(request: Request, item_id: str = Query(pattern=r'^\d{1,24}$')):
    if len(request.app.state.model_tasks) >= 4 or not request.app.state.accounts.limit('warm:'+str(request.state.user['id']), maximum=40):
        return {'status': 'busy'}
    try:
        await video_model(request, item_id)
    except (RuntimeError, ValueError, httpx.HTTPError):
        return {'status': 'unavailable'}
    return {'status': 'ready'}


@app.post("/api/play")
async def prepare(request: Request, item_id: str = Query(pattern=r"^\d{1,24}$"), definition: Literal["1080p", "720p", "540p", "480p", "360p"] = "720p"):
    """Resolve/decrypt/convert once, then serve seekable MP4 via Range requests."""
    started = time.monotonic()
    filename = f"{item_id}_{definition}.mp4"
    path = CACHE / filename
    cached = cached_video(item_id, definition)
    if cached:
        return cached
    request.app.state.playback_jobs.prioritize((item_id, definition))

    async def work():
        async with request.app.state.prepare_slot:
            cached = path.is_file()
            if not cached:
                response = await duanju.duanju_download(request, item_id, definition, True, False)
                if response.status_code != 200:
                    try:
                        message = json.loads(response.body).get("msg", "视频准备失败")
                    except (ValueError, AttributeError):
                        message = "视频准备失败，请重试"
                    raise HTTPException(response.status_code, message)
                if not response.body:
                    raise HTTPException(502, "视频内容为空，请重试")
                actual = response.headers.get("X-Duanju-Definition", definition)
                temp = path.with_suffix(".part")
                try:
                    # Atomic publication: a player never sees a partial MP4.
                    temp.write_bytes(response.body)
                    temp.replace(path)
                    path.with_suffix(".json").write_text(json.dumps({"definition": actual, 'indexed': True}))
                finally:
                    temp.unlink(missing_ok=True)
            path.touch()
            trim_cache(keep=path)
            try:
                actual = json.loads(path.with_suffix(".json").read_text())["definition"]
            except (OSError, ValueError, KeyError):
                actual = definition
            return {"url": f"/media/{filename}", "definition": actual, "cached": cached, 'prepare_ms': round((time.monotonic()-started)*1000), 'bytes': path.stat().st_size}

    try:
        async with asyncio.timeout(300):
            return await while_connected(work(), request.is_disconnected)
    except TimeoutError:
        raise HTTPException(504, "视频准备超时，请降低清晰度后重试")


@app.get("/media/{filename}")
async def media(filename: str):
    import re
    if not re.fullmatch(r"\d{1,24}_(1080p|720p|540p|480p|360p)\.mp4", filename):
        raise HTTPException(404, "视频不存在")
    path = CACHE / filename
    if not path.is_file():
        raise HTTPException(404, "播放缓存已清理，请重新选择剧集")
    path.touch()
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})


def valid_share(request, token):
    share = request.app.state.accounts.get_share(token)
    if not share:
        raise HTTPException(410, '分享链接已过期或被管理员撤销')
    return share


@app.get('/s/{token}')
async def shared_page(request: Request, token: str):
    valid_share(request, token)
    return FileResponse(ROOT / 'static/shared.html')


@app.get('/api/shared/{token}')
async def shared_data(request: Request, token: str):
    share = valid_share(request, token)
    return {'item': share['item'], 'episodes': share['episodes'], 'expires': share['expires']}


@app.post('/api/shared/{token}/play')
async def shared_play(request: Request, token: str, item_id: str = Query(pattern=r'^\d{1,24}$'), definition: Literal['1080p', '720p', '540p', '480p', '360p'] = '720p'):
    share = valid_share(request, token)
    if item_id not in {str(ep['item_id']) for ep in share['episodes']}:
        raise HTTPException(403, '此链接只允许观看分享的短剧')
    if not request.app.state.accounts.limit('share-play:'+token+':'+request.client.host, maximum=90):
        raise HTTPException(429, '切集过于频繁，请稍后再试')
    result = await prepare(request, item_id, definition)
    valid_share(request, token)
    result['url'] = result['url'].replace('/media/', '/shared-media/'+token+'/')
    return result


@app.post('/api/shared/{token}/stream')
async def shared_stream(request: Request, token: str, item_id: str = Query(pattern=r'^\d{1,24}$'), definition: Literal['1080p', '720p', '540p', '480p', '360p'] = '720p'):
    share = valid_share(request, token)
    if item_id not in {str(ep['item_id']) for ep in share['episodes']}:
        raise HTTPException(403, '此链接只允许观看分享的短剧')
    if not request.app.state.accounts.limit('share-play:'+token+':'+request.client.host, maximum=90):
        raise HTTPException(429, '切集过于频繁，请稍后再试')
    result = await stream_video(request, item_id, definition)
    try:
        valid_share(request, token)
    except HTTPException:
        if isinstance(result, ManagedStream):
            await result.cleanup()
        raise
    if isinstance(result, JSONResponse) and result.status_code == 200:
        payload = json.loads(result.body)
        payload['url'] = payload['url'].replace('/media/', '/shared-media/'+token+'/')
        return JSONResponse(payload)
    return result


@app.post('/api/shared/{token}/prefetch')
async def shared_prefetch(request: Request, token: str, item_id: str = Query(pattern=r'^\d{1,24}$'), definition: Literal['1080p', '720p', '540p', '480p', '360p'] = '720p'):
    share = valid_share(request, token)
    if item_id not in {str(ep['item_id']) for ep in share['episodes']}:
        raise HTTPException(403, '此链接只允许观看分享的短剧')
    if not request.app.state.accounts.limit('share-prefetch:'+token+':'+request.client.host, maximum=60):
        raise HTTPException(429, '请求过于频繁')
    return prefetch_video(request, item_id, definition)


@app.get('/shared-media/{token}/{filename}')
async def shared_media(request: Request, token: str, filename: str):
    share = valid_share(request, token)
    if filename.split('_')[0] not in {str(ep['item_id']) for ep in share['episodes']}:
        raise HTTPException(403, '此链接只允许观看分享的短剧')
    return await media(filename)


@app.get('/healthz')
async def readiness(request: Request):
    if os.environ.get('HONGGUO_ENV') == 'production' and request.app.state.country_access.status('223.5.5.5') != 403:
        return JSONResponse({'status': 'unavailable', 'code': 'REGION_UNAVAILABLE'}, status_code=503)
    return {'status': 'ok'}


@app.get('/login')
@app.get('/setup')
async def login_page():
    return FileResponse(ROOT / 'static/login.html')


@app.get('/admin')
async def admin_page(request: Request):
    require_admin(request)
    return FileResponse(ROOT / 'static/admin.html')


@app.get("/")
async def index():
    return FileResponse(ROOT / "static/index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description="TACO小剧场 · 本地在线观看")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, proxy_headers=True,
                forwarded_allow_ips=os.environ.get('FORWARDED_ALLOW_IPS', '127.0.0.1'))
