"""Compare explicit direct and user-provided proxy paths; never claim geographic nodes."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import server
from endpoints.duanju import _fetch_video_model, _pick_source, duanju_download
from core.video_download import VIDEO_UA


async def sample(url, proxy, maximum):
    started = time.perf_counter()
    received, first_byte, previous, longest_gap = 0, None, None, 0
    try:
        async with httpx.AsyncClient(proxy=proxy, trust_env=False, timeout=30, follow_redirects=True, headers={'User-Agent': VIDEO_UA}) as client:
            async with client.stream('GET', url, headers={'Range': f'bytes=0-{maximum-1}'}) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    now = time.perf_counter()
                    if first_byte is None:
                        first_byte = now-started
                    if previous is not None:
                        longest_gap = max(longest_gap, now-previous)
                    previous = now
                    received += len(chunk)
                    if received >= maximum:
                        break
                elapsed = time.perf_counter()-started
                return {'ok': True, 'status': response.status_code, 'bytes': received, 'seconds': round(elapsed, 3), 'ttfb_ms': round((first_byte or elapsed)*1000, 1), 'mbps': round(received*8/elapsed/1e6, 3), 'largest_chunk_gap_ms': round(longest_gap*1000, 1)}
    except Exception as exc:
        # Exceptions can include signed video URLs; only retain their type.
        return {'ok': False, 'error_type': type(exc).__name__, 'seconds': round(time.perf_counter()-started, 3)}


async def exit_country(proxy):
    try:
        async with httpx.AsyncClient(proxy=proxy, trust_env=False, timeout=15) as client:
            response = await client.get('https://www.cloudflare.com/cdn-cgi/trace')
            response.raise_for_status()
            values = dict(line.split('=', 1) for line in response.text.splitlines() if '=' in line)
            return {'country_for_cloudflare_only': values.get('loc'), 'edge': values.get('colo')}
    except Exception as exc:
        return {'error_type': type(exc).__name__}


async def prepare_sample(proxy, item_id):
    async def connected():
        return False
    await server.app.state.video_client.aclose()
    server.app.state.video_client = httpx.AsyncClient(proxy=proxy, trust_env=False, follow_redirects=True, timeout=30, headers={'User-Agent': VIDEO_UA})
    request = SimpleNamespace(app=server.app, state=SimpleNamespace(), is_disconnected=connected)
    started = time.perf_counter()
    try:
        response = await duanju_download(request, item_id, '720p', True, False)
        return {'status': response.status_code, 'seconds': round(time.perf_counter()-started, 3), 'video_bytes': len(response.body) if response.status_code == 200 else 0, 'actual_definition': response.headers.get('X-Duanju-Definition'), 'note': 'Fresh CDN download, decrypt, H.264/AAC preparation. Playback model was already resolved; no browser client leg.'}
    except Exception as exc:
        return {'error_type': type(exc).__name__, 'seconds': round(time.perf_counter()-started, 3)}


async def main(args):
    async with server.app.router.lifespan_context(server.app):
        started = time.perf_counter()
        model = await _fetch_video_model(SimpleNamespace(app=server.app), args.item_id)
        source = _pick_source(model['sources'], '720p', prefer_h264=True, compatible_only=True)
        resolution_ms = round((time.perf_counter()-started)*1000, 1)
        url = source['urls'][0]
        results = {'at': datetime.now(timezone.utc).isoformat(), 'item_id': args.item_id, 'video_host': urlparse(url).hostname, 'model_resolution_ms': resolution_ms, 'sample_bytes': args.megabytes*1024**2, 'routes': {}, 'limitations': ['Both tests run on this computer. They are not mainland and overseas viewer nodes.', 'Proxy routing may bypass mainland video domains; the Cloudflare country only describes that separate request.', 'HTTP sample speed is not browser stall measurement and does not test a future deployed server.']}
        for name, proxy in [('direct', None), ('configured_proxy', args.proxy)]:
            samples = []
            for _ in range(args.rounds):
                samples.append(await sample(url, proxy, args.megabytes*1024**2))
            successful = [s for s in samples if s['ok']]
            results['routes'][name] = {'exit_check': await exit_country(proxy), 'samples': samples, 'median_mbps': round(statistics.median(s['mbps'] for s in successful), 3) if successful else None, 'median_ttfb_ms': round(statistics.median(s['ttfb_ms'] for s in successful), 1) if successful else None}
            if args.prepare_video:
                results['routes'][name]['fresh_video_preparation'] = await prepare_sample(proxy, args.item_id)
            print(name, json.dumps(results['routes'][name], ensure_ascii=False), flush=True)
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print('Report:', path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--item-id', required=True)
    parser.add_argument('--proxy', default='socks5://127.0.0.1:10808')
    parser.add_argument('--rounds', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--megabytes', type=int, choices=range(1, 17), default=4)
    parser.add_argument('--output', default='output/network/upstream-paths.json')
    parser.add_argument('--prepare-video', action='store_true')
    asyncio.run(main(parser.parse_args()))
