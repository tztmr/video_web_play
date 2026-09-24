import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import server  # sets the sibling source import path
from video_loader import download_ranges, video_model


class LoaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_ranges_are_disjoint_and_reassemble_exactly(self):
        data = bytes(range(256)) * 8193
        ranges = []
        async def handle(request):
            a,b = map(int,request.headers['range'].removeprefix('bytes=').split('-'))
            ranges.append((a,b))
            return httpx.Response(206,headers={'content-range':f'bytes {a}-{b}/{len(data)}'},content=data[a:b+1])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await download_ranges({'urls':['https://cdn.test/video'],'size':len(data)},client)
        self.assertEqual(result,data)
        self.assertEqual(len(ranges),4)
        ranges.sort()
        self.assertEqual(ranges[0][0],0)
        self.assertEqual(ranges[-1][1],len(data)-1)
        self.assertTrue(all(ranges[i][1]+1==ranges[i+1][0] for i in range(3)))

    async def test_unsupported_truncated_and_mismatched_ranges_fall_back(self):
        for case in ('ignored','short','wrong-offset'):
            async def handle(request):
                a,b = map(int,request.headers['range'].removeprefix('bytes=').split('-'))
                if case=='ignored':
                    return httpx.Response(200,content=b'wrong')
                return httpx.Response(206,headers={'content-range':f'bytes {a if case=="short" else a+1}-{b}/2097152'},content=b'short')
            fallback=AsyncMock(return_value=b'valid-video')
            with patch('video_loader.duanju._download_encrypted',fallback):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                    result=await download_ranges({'urls':['https://cdn.test/video'],'size':2097152},client)
            self.assertEqual(result,b'valid-video',case)
            fallback.assert_awaited_once()

    async def test_cancel_ranges_stops_every_request_without_retry(self):
        started,closed=0,0
        ready=asyncio.Event()
        async def handle(request):
            nonlocal started,closed
            started+=1
            if started==4:ready.set()
            try:
                await asyncio.sleep(30)
            finally:
                closed+=1
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch('video_loader.duanju._download_encrypted',AsyncMock()) as fallback:
                task=asyncio.create_task(download_ranges({'urls':['https://cdn.test/video'],'size':2097152},client))
                await asyncio.wait_for(ready.wait(),1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                self.assertEqual(closed,4)
                fallback.assert_not_awaited()

    async def test_model_warming_is_shared_and_survives_one_waiter_cancelling(self):
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(model_tasks={})))
        started,finish=asyncio.Event(),asyncio.Event()
        async def fetch(*args):
            started.set()
            await finish.wait()
            return {'sources':[]}
        with patch('video_loader.duanju._fetch_video_model',AsyncMock(side_effect=fetch)) as upstream:
            first=asyncio.create_task(video_model(request,'123'))
            await started.wait()
            second=asyncio.create_task(video_model(request,'123'))
            await asyncio.sleep(0)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            finish.set()
            self.assertEqual(await second,{'sources':[]})
            upstream.assert_awaited_once()
        self.assertFalse(request.app.state.model_tasks)
