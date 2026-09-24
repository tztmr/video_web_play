import asyncio
import os
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.responses import Response, JSONResponse, StreamingResponse
from types import SimpleNamespace
import server


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network_patch=patch.dict(os.environ, {'HONGGUO_NETWORK_MODE':'direct'})
        self.network_patch.start()
        self.directory = tempfile.TemporaryDirectory()
        self.cache = Path(self.directory.name)
        self.cache_patch = patch.object(server, 'CACHE', self.cache)
        self.cache_patch.start()
        self.data_patch = patch.object(server, 'DATA', self.cache / 'auth')
        self.data_patch.start()
        async def publish(source, target):
            target.write_bytes(source.read_bytes())
        self.publish_patch = patch('playback_jobs.publish_cache', side_effect=publish)
        self.publish_patch.start()
        self.lifespan = server.app.router.lifespan_context(server.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://testserver')
        from accounts import password_hash
        uid = server.app.state.accounts.create_user('testadmin', password_hash('TestingPassword123'), 'admin')
        self.client.cookies.set('hongguo_session', server.app.state.accounts.login(uid))

    async def asyncTearDown(self):
        self.network_patch.stop()
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.publish_patch.stop()
        self.cache_patch.stop()
        self.data_patch.stop()
        self.directory.cleanup()

    async def test_prepare_reuses_cache_and_serves_seekable_ranges(self):
        download = AsyncMock(return_value=Response(b'0123456789abcdef', media_type='video/mp4', headers={'X-Duanju-Definition': '540p'}))
        with patch.object(server.duanju, 'duanju_download', download):
            first = await self.client.post('/api/play?item_id=123&definition=720p')
            second = await self.client.post('/api/play?item_id=123&definition=720p')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['definition'], '540p')
        self.assertFalse(first.json()['cached'])
        self.assertTrue(second.json()['cached'])
        self.assertEqual(download.await_count, 1)
        response = await self.client.get(first.json()['url'], headers={'Range': 'bytes=4-8'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b'45678')
        self.assertEqual(response.headers['content-range'], 'bytes 4-8/16')
        invalid = await self.client.get(first.json()['url'], headers={'Range': 'bytes=100-200'})
        self.assertEqual(invalid.status_code, 416)

    async def test_concurrent_same_episode_prepares_once(self):
        async def slow_download(*args):
            await asyncio.sleep(.05)
            return Response(b'video', media_type='video/mp4')
        mock = AsyncMock(side_effect=slow_download)
        with patch.object(server.duanju, 'duanju_download', mock):
            responses = await asyncio.gather(*(self.client.post('/api/play?item_id=123') for _ in range(3)))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(mock.await_count, 1)

    async def test_failed_prepare_is_not_cached_and_preserves_diagnosis(self):
        failed = JSONResponse({'code': -10, 'msg': '视频兼容处理失败'}, status_code=502)
        with patch.object(server.duanju, 'duanju_download', AsyncMock(return_value=failed)):
            response = await self.client.post('/api/play?item_id=123')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['detail'], '视频兼容处理失败')
        self.assertFalse(list(self.cache.glob('*.mp4')))
        self.assertFalse(list(self.cache.glob('*.part')))
        self.assertEqual(server.app.state.prepare_slot._value, 1)

    async def test_local_boundary_and_input_validation(self):
        for path in ['/api/play?item_id=../../private', '/api/play?item_id=1&definition=garbage']:
            self.assertEqual((await self.client.post(path)).status_code, 422)
        self.assertEqual((await self.client.post('/api/play?item_id=1', headers={'Origin': 'https://unrelated.example'})).status_code, 403)
        self.assertEqual((await self.client.get('/', headers={'Host': 'unrelated.example'})).status_code, 400)
        self.assertEqual((await self.client.get('/media/secret.json')).status_code, 404)
        self.assertEqual((await self.client.get('/.data/device_pool.json')).status_code, 404)
        self.assertEqual((await self.client.get('/api/duanju/key?item_id=1')).status_code, 404)
        self.assertEqual((await self.client.get('/media/123_720p.mp4')).status_code, 404)

    async def test_cancelled_prepare_releases_slot_and_leaves_no_partial_file(self):
        started = asyncio.Event()
        finished = asyncio.Event()
        async def slow_download(*args):
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                finished.set()
        with patch.object(server.duanju, 'duanju_download', AsyncMock(side_effect=slow_download)):
            task = asyncio.create_task(self.client.post('/api/play?item_id=123'))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await asyncio.wait_for(finished.wait(), 2)
        self.assertEqual(server.app.state.prepare_slot._value, 1)
        self.assertFalse(list(self.cache.glob('*.mp4')))
        self.assertFalse(list(self.cache.glob('*.part')))

    async def test_cache_eviction_removes_metadata_and_keeps_current(self):
        old = self.cache / '1_720p.mp4'
        old.write_bytes(b'old')
        old.with_suffix('.json').write_text(json.dumps({'definition': '720p'}))
        import os
        os.utime(old, (time.time()-server.CACHE_TTL-1,)*2)
        current = self.cache / '2_720p.mp4'
        current.write_bytes(b'new')
        server.trim_cache(keep=current)
        self.assertFalse(old.exists())
        self.assertFalse(old.with_suffix('.json').exists())
        self.assertTrue(current.exists())

    async def test_stream_publishes_cache_only_after_completion(self):
        async def upstream():
            yield b'first'
            await asyncio.sleep(.03)
            self.assertFalse((self.cache/'123_720p.mp4').exists())
            yield b'last'
        source = StreamingResponse(upstream(), headers={'X-Duanju-Definition':'540p','X-Playback-Mime':'video/mp4'})
        with patch.object(server,'load_video',AsyncMock(return_value=source)):
            r = await self.client.post('/api/play/stream?item_id=123')
        self.assertEqual(r.content,b'firstlast')
        self.assertEqual(r.headers['x-playback-mime'],'video/mp4')
        self.assertEqual((self.cache/'123_720p.mp4').read_bytes(),b'firstlast')
        self.assertEqual(server.app.state.prepare_slot._value,1)
        # A cached video must remain immediately available while another episode prepares.
        await server.app.state.prepare_slot.acquire()
        try:
            r = await asyncio.wait_for(self.client.post('/api/play/stream?item_id=123'),1)
            self.assertTrue(r.json()['cached'])
            self.assertEqual(r.json()['definition'],'540p')
        finally:
            server.app.state.prepare_slot.release()

    async def test_stream_first_chunk_and_close_release_upstream(self):
        closed = asyncio.Event()
        async def upstream():
            try:
                yield b'first'
                await asyncio.sleep(30)
            finally:
                closed.set()
        request = SimpleNamespace(app=server.app,is_disconnected=AsyncMock(return_value=False))
        with patch.object(server,'load_video',AsyncMock(return_value=StreamingResponse(upstream()))):
            response = await server.stream_video(request,'123','720p')
        self.assertEqual(await asyncio.wait_for(anext(response.body_iterator),1),b'first')
        await response.body_iterator.aclose()
        await response.cleanup()  # also safe when both generator and response close
        await asyncio.wait_for(closed.wait(),1)
        self.assertEqual(server.app.state.prepare_slot._value,1)
        self.assertFalse(list(self.cache.glob('*.part')))
        self.assertFalse(list(self.cache.glob('*.mp4')))

    async def test_stream_failure_releases_slot(self):
        source = JSONResponse({'msg':'上游不可用'},status_code=502)
        with patch.object(server,'load_video',AsyncMock(return_value=source)):
            r = await self.client.post('/api/play/stream?item_id=123')
        self.assertEqual(r.status_code,502)
        self.assertEqual(server.app.state.prepare_slot._value,1)

    async def test_foreground_joins_prefetch_and_other_viewer_without_restarting(self):
        first = asyncio.Event()
        finish = asyncio.Event()
        async def upstream():
            yield b'first'
            first.set()
            await finish.wait()
            yield b'last'
        request = SimpleNamespace(app=server.app,is_disconnected=AsyncMock(return_value=False))
        loader = AsyncMock(return_value=StreamingResponse(upstream()))
        with patch.object(server,'load_video',loader):
            r = await self.client.post('/api/play/prefetch?item_id=123')
            self.assertEqual(r.json()['status'],'preparing')
            await asyncio.wait_for(first.wait(),1)
            one = await server.stream_video(request,'123','720p')
            two = await server.stream_video(request,'123','720p')
            self.assertEqual(await anext(one.body_iterator),b'first')
            self.assertEqual(await anext(two.body_iterator),b'first')
            await one.body_iterator.aclose()
            await one.cleanup()
            self.assertEqual(loader.await_count,1)
            finish.set()
            self.assertEqual(b''.join([part async for part in two.body_iterator]),b'last')
            await two.cleanup()
        self.assertEqual((self.cache/'123_720p.mp4').read_bytes(),b'firstlast')
        self.assertEqual(server.app.state.prepare_slot._value,1)

    async def test_foreground_preempts_unrelated_prefetch(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def load(request,item_id,definition):
            if item_id=='123':
                started.set()
                try:
                    await asyncio.sleep(30)
                finally:
                    cancelled.set()
            return Response(b'foreground')
        with patch.object(server,'load_video',AsyncMock(side_effect=load)):
            await self.client.post('/api/play/prefetch?item_id=123')
            await asyncio.wait_for(started.wait(),1)
            response = await asyncio.wait_for(self.client.post('/api/play/stream?item_id=456'),1)
        self.assertEqual(response.status_code,200)
        self.assertTrue(cancelled.is_set())
        self.assertFalse((self.cache/'123_720p.mp4').exists())
        self.assertEqual((self.cache/'456_720p.mp4').read_bytes(),b'foreground')
        self.assertFalse(list(self.cache.glob('*.part')))

    async def test_cancel_queued_job_before_start_does_not_poison_retry(self):
        jobs = server.app.state.playback_jobs
        old = jobs.get(('123','720p'))
        await jobs.release(old)
        self.assertFalse(jobs.jobs)
        self.assertEqual(server.app.state.prepare_slot._value,1)
        with patch.object(server,'load_video',AsyncMock(return_value=Response(b'new'))):
            response = await asyncio.wait_for(self.client.post('/api/play/stream?item_id=123'),1)
        self.assertEqual(response.status_code,200)
        self.assertEqual((self.cache/'123_720p.mp4').read_bytes(),b'new')


if __name__ == '__main__':
    unittest.main()
