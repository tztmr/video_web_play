"""One producer per episode, streaming subscribers, and preemptible next-episode work."""
import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from core.playback import _tools, _run


async def publish_cache(source, target):
    """Keep the live fMP4 spool; publish a seekable MP4 with its index at the front."""
    ffmpeg, _ = _tools()
    indexed = source.with_suffix('.indexed.mp4')
    try:
        await _run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy',
            '-movflags', '+faststart', '-f', 'mp4', str(indexed)])
        if not indexed.is_file() or not indexed.stat().st_size:
            raise RuntimeError('播放缓存索引生成失败')
        indexed.replace(target)
    finally:
        indexed.unlink(missing_ok=True)


@dataclass(eq=False)
class Job:
    key: tuple
    path: Path
    background: bool
    readers: int = 0
    headers: dict = field(default_factory=dict)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: Exception | None = None
    task: asyncio.Task | None = None

    def notify(self):
        old, self.changed = self.changed, asyncio.Event()
        old.set()


class PlaybackJobs:
    def __init__(self, app, cache, slots, loader, trim):
        self.app, self.cache, self.slots, self.loader, self.trim = app, cache, slots, loader, trim
        self.jobs = {}
        self.tasks = set()
        self.closed = False

    def prioritize(self, key):
        for other in list(self.jobs.values()):
            if other.key != key and other.background and not other.readers:
                other.task.cancel()
                self.jobs.pop(other.key, None)

    def get(self, key, background=False):
        if self.closed:
            raise HTTPException(503, '服务正在重启，请稍后重试')
        if not background:
            self.prioritize(key)
        job = self.jobs.get(key)
        if job is None:
            if background:
                # Only one speculative episode per process; current viewers always win.
                if any(j.background for j in self.jobs.values()):
                    return None
            path = self.cache / f'{key[0]}_{key[1]}_{uuid.uuid4().hex}.part'
            job = Job(key, path, background)
            self.jobs[key] = job
            job.task = asyncio.create_task(self.produce(job))
            self.tasks.add(job.task)
            job.task.add_done_callback(lambda task: self.finished(job, task))
        if not background:
            job.background = False
            job.readers += 1
        return job

    def finished(self, job, task):
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()
        # A queued task can be cancelled before its coroutine starts.
        if not job.done.is_set():
            job.error = HTTPException(409, '视频准备已取消，请重试')
            job.done.set()
            job.ready.set()
            job.notify()
        self.discard(job)

    async def produce(self, job):
        response = None
        try:
            async with self.slots:
                item_id, definition = job.key
                final = self.cache / f'{item_id}_{definition}.mp4'
                if final.is_file():
                    job.path = final
                    return
                async def connected():
                    return False  # Lifetime belongs to this job, not its first subscriber.
                request = SimpleNamespace(app=self.app, state=SimpleNamespace(), is_disconnected=connected)
                async with asyncio.timeout(300):
                    response = await self.loader(request, item_id, definition)
                    if response.status_code != 200:
                        try:
                            payload = json.loads(response.body)
                            detail = payload.get('detail') or payload.get('msg') or '视频准备失败，请重试'
                        except (ValueError, AttributeError):
                            detail = '视频准备失败，请重试'
                        raise HTTPException(response.status_code, detail)
                    job.headers = dict(response.headers)
                    size = 0
                    with job.path.open('wb', buffering=0) as output:
                        job.ready.set()
                        if isinstance(response, StreamingResponse):
                            async for chunk in response.body_iterator:
                                output.write(chunk)
                                size += len(chunk)
                                job.notify()
                        else:
                            output.write(response.body)
                            size = len(response.body)
                            job.notify()
                    if not size:
                        raise HTTPException(502, '视频内容为空，请重试')
                    await publish_cache(job.path, final)
                    final.with_suffix('.json').write_text(json.dumps({'definition': response.headers.get('X-Duanju-Definition', definition), 'indexed': True}))
                    self.trim(keep=final)
        except asyncio.CancelledError:
            job.error = HTTPException(409, '视频准备已取消，请重试')
        except TimeoutError:
            job.error = HTTPException(504, '视频加载超时，请重试')
        except HTTPException as error:
            job.error = error
        except Exception:
            job.error = HTTPException(502, '视频处理失败，请重试或切换清晰度')
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    if isinstance(response, StreamingResponse):
                        await response.body_iterator.aclose()
                finally:
                    job.done.set()
                    job.ready.set()
                    job.notify()
                    self.discard(job)

    def discard(self, job):
        if job.readers == 0 and job.done.is_set():
            if self.jobs.get(job.key) is job:
                self.jobs.pop(job.key, None)
            if job.path.suffix == '.part':
                job.path.unlink(missing_ok=True)

    async def release(self, job):
        job.readers -= 1
        if job.readers == 0 and not job.background and not job.done.is_set():
            if self.jobs.get(job.key) is job:
                self.jobs.pop(job.key, None)
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
        self.discard(job)

    async def read(self, job):
        with job.path.open('rb') as source:
            while True:
                changed = job.changed
                chunk = source.read(64 * 1024)
                if chunk:
                    yield chunk
                elif job.done.is_set():
                    if job.error:
                        raise job.error
                    break
                else:
                    await changed.wait()

    async def close(self):
        self.closed = True
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
