"""Website playback transport; reuse the downloader's source selection and codecs."""
import asyncio
import time

import httpx
from fastapi.responses import JSONResponse, StreamingResponse
from endpoints import duanju
from core.playback import prepare_streaming_video


async def video_model(request, item_id):
    tasks = request.app.state.model_tasks
    task = tasks.get(item_id)
    if task is None:
        task = asyncio.create_task(duanju._fetch_video_model(request, item_id))
        tasks[item_id] = task
        def finished(task):
            if tasks.get(item_id) is task:
                tasks.pop(item_id, None)
            if not task.cancelled():
                task.exception()  # a cancelled viewer can leave metadata warming alone
        task.add_done_callback(finished)
    return await asyncio.shield(task)


async def download_ranges(source, client):
    """Four disjoint ranges, with exact validation and the original CDN fallback."""
    size = int(source.get('size') or 0)
    if size < 2 * 1024**2 or size > 256 * 1024**2:
        return await duanju._download_encrypted(source['urls'], client)
    step = (size + 3) // 4

    async def part(start, end):
        async with client.stream('GET', source['urls'][0],
                headers={'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'},
                timeout=httpx.Timeout(4, connect=3, pool=8)) as response:
            expected = f'bytes {start}-{end}/{size}'
            if response.status_code != 206 or response.headers.get('content-range') != expected:
                raise ValueError('CDN does not support exact ranges')
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > end - start + 1:
                    raise ValueError('Invalid CDN range length')
            if len(data) != end - start + 1:
                raise ValueError('Incomplete CDN range')
            return bytes(data)

    tasks = [asyncio.create_task(part(start, min(size - 1, start + step - 1)))
             for start in range(0, size, step)]
    try:
        return b''.join(await asyncio.gather(*tasks))
    except (httpx.HTTPError, ValueError):
        # Stop every partial request before trying the established backup-CDN flow.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return await duanju._download_encrypted(source['urls'], client)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def load_video(request, item_id, definition):
    started = time.monotonic()
    try:
        data = await video_model(request, item_id)
        source = duanju._pick_source(data['sources'], definition, prefer_h264=True, compatible_only=True)
        key = duanju.derive_key_from_spade_a(source['spade_a'])
        modeled = time.monotonic()
        encrypted = await download_ranges(source, request.app.state.video_client)
        downloaded = time.monotonic()
        decrypted = await asyncio.to_thread(duanju.decrypt_mp4, encrypted, key)
        decrypted_at = time.monotonic()
        stream, metadata = await prepare_streaming_video(decrypted, request.is_disconnected)
        timings = {'model': modeled-started, 'download': downloaded-modeled,
                   'decrypt': decrypted_at-downloaded, 'first_output': time.monotonic()-decrypted_at}
        return StreamingResponse(stream, media_type='video/mp4', headers={
            'Cache-Control': 'no-store', 'X-Duanju-Definition': str(source['definition']),
            'X-Playback-Mime': metadata['mime'], 'X-Playback-Duration': metadata['duration'],
            'Server-Timing': ', '.join(f'{key};dur={value*1000:.1f}' for key, value in timings.items()),
        })
    except (RuntimeError, ValueError, httpx.HTTPError) as error:
        # Transport exceptions can contain signed URLs. Keep them out of responses.
        message = '视频网络暂时不可用，请重试' if isinstance(error, httpx.HTTPError) else str(error)
        return JSONResponse({'detail': message}, status_code=502)
