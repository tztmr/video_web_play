import asyncio
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import server
from core.playback import _tools
from playback_jobs import publish_cache


class CacheIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stream_cache_has_front_index_and_preserves_decoded_frames(self):
        ffmpeg, ffprobe = _tools()
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder)/'stream.part', Path(folder)/'indexed.mp4'
            def run(*args):
                return subprocess.run([str(a) for a in args],check=True,capture_output=True,timeout=15).stdout
            await asyncio.to_thread(run,ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=size=64x64:duration=1',
                '-f','lavfi','-i','sine=frequency=1000:duration=1','-c:v','libx264','-c:a','aac',
                '-movflags','frag_keyframe+empty_moov+default_base_moof','-f','mp4',source)
            await publish_cache(source,target)
            self.assertTrue(source.exists())  # live readers keep their original spool
            data=target.read_bytes();offset=0;types=[]
            while offset+8<=len(data):
                size,kind=struct.unpack_from('>I4s',data,offset)
                self.assertGreaterEqual(size,8)
                types.append(kind)
                offset+=size
            self.assertLess(types.index(b'moov'),types.index(b'mdat'))
            self.assertNotIn(b'moof',types)
            async def hashes(path):
                text=(await asyncio.to_thread(run,ffmpeg,'-v','error','-i',path,'-map','0:v:0','-f','framemd5','-')).decode()
                return [line.rsplit(',',1)[-1].strip() for line in text.splitlines() if line and not line.startswith('#')]
            self.assertEqual(await hashes(source),await hashes(target))
            info=json.loads(await asyncio.to_thread(run,ffprobe,'-v','error','-show_entries','format=duration','-of','json',target))
            self.assertAlmostEqual(float(info['format']['duration']),1,delta=.1)
            self.assertFalse(list(Path(folder).glob('*.indexed.mp4')))
