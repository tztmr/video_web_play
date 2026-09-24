import asyncio
import base64
import json
from pathlib import Path
import shutil
import struct
import subprocess
import unittest
from unittest.mock import AsyncMock, patch

import server  # initialize the bundled runtime import path
from core.mp4_decrypt import decrypt_mp4
from Crypto.Cipher import AES
from video_loader import browser_source

ROOT = Path(__file__).resolve().parents[1]


class DirectSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_hevc_capability_and_quality_fallback_are_explicit(self):
        sources = [{'definition':quality, 'codec_type':codec, 'size':3000,
                    'urls':['https://v3-reading-video.qznovelvod.com/source.mp4'], 'spade_a':'02'*16}
                   for quality, codec in [('720p', 'bytevc2'), ('1080p', 'bytevc1')]]
        with patch('video_loader.video_model', AsyncMock(return_value={'sources':sources})):
            with self.assertRaisesRegex(ValueError, '兼容模式'):
                await browser_source(None, '123', '720p', False)
            descriptor = await browser_source(None, '123', '720p', True)
            self.assertEqual(descriptor['definition'], '1080p')
            self.assertEqual(descriptor['codec'], 'bytevc1')

    async def test_direct_sources_cannot_inject_arbitrary_fetch_targets(self):
        for url in ('http://127.0.0.1/source', 'https://qznovelvod.com.evil.invalid/a', 'https://evilqznovelvod.com/a', 'https://user:pass@v3.qznovelvod.com/a', 'javascript:alert(1)', 'https://v3.qznovelvod.com:9999/a'):
            model = {'sources':[{'definition':'720p', 'codec_type':'h264', 'spade_a':'03'*16, 'urls':[url]}]}
            with self.subTest(url=url), patch('video_loader.video_model', AsyncMock(return_value=model)), self.assertRaises(ValueError):
                await browser_source(None, '123', '720p')


def atom(kind, payload):
    return struct.pack('>I4s', len(payload)+8, kind.encode())+payload


def encrypted_fixture(wide=False, fixed=False, invalid_offset=False):
    key = bytes(range(16))
    sizes = ([48,48,48] if fixed else [35,29,64]) + [17,31,16]
    clear = [bytes((index*37+n)%256 for n in range(size)) for index,size in enumerate(sizes)]
    ivs = [(index+1).to_bytes(8, 'big') for index in range(6)]
    encrypted = [AES.new(key, AES.MODE_CTR, nonce=iv, initial_value=0).encrypt(sample) for sample,iv in zip(clear,ivs)]
    ftyp = atom('ftyp', b'isom\0\0\0\0isom')
    def moov(base):
        tracks=[]
        for start,kind,codec in [(0,'encv','hvc1'),(3,'enca','mp4a')]:
            group=sizes[start:start+3]
            stsz=atom('stsz',b'\0'*4+struct.pack('>II',48 if fixed and start==0 else 0,3)+(b'' if fixed and start==0 else struct.pack('>III',*group)))
            stsc=atom('stsc',b'\0'*4+struct.pack('>IIIIIII',2,1,2,1,2,1,1))
            offsets=[base+sum(sizes[:start]),base+sum(sizes[:start+2])]
            if invalid_offset: offsets[0]=4
            stco=atom('co64' if wide else 'stco', b'\0'*4+struct.pack('>I',2)+struct.pack('>QQ' if wide else '>II',*offsets))
            senc=atom('senc',b'\0'*4+struct.pack('>I',3)+b''.join(ivs[start:start+3]))
            entry=atom(kind,b'\0'*8+atom('sinf',atom('frma',codec.encode())))
            stsd=atom('stsd',b'\0'*4+struct.pack('>I',1)+entry)
            tracks.append(atom('trak',atom('mdia',atom('minf',atom('stbl',stsd+stsz+stsc+stco+senc)))))
        return atom('moov',b''.join(tracks))
    base=len(ftyp)+len(moov(0))+8
    return ftyp+moov(base)+atom('mdat',b''.join(encrypted)), key.hex(), b''.join(clear)


@unittest.skipUnless(shutil.which('node'), 'Browser algorithm tests require Node.js')
class BrowserDecryptTests(unittest.TestCase):
    def decrypt(self, source, key):
        script = """
import fs from 'node:fs';
import {webcrypto} from 'node:crypto';
globalThis.crypto??=webcrypto;
const {decryptMp4}=await import('data:text/javascript;base64,'+fs.readFileSync('static/mp4-decrypt.js').toString('base64'));
const input=JSON.parse(fs.readFileSync(0,'utf8'));
try{const output=await decryptMp4(new Uint8Array(Buffer.from(input.source,'base64')),input.key);console.log(JSON.stringify({data:Buffer.from(output).toString('base64')}));}
catch(error){console.log(JSON.stringify({error:error.message}));}
"""
        run = subprocess.run(['node','--input-type=module','-e',script], input=json.dumps({'source':base64.b64encode(source).decode(),'key':key}), capture_output=True, text=True, check=True, cwd=ROOT, timeout=15)
        return json.loads(run.stdout)

    def test_browser_decryption_matches_original_and_plaintext_with_stco_co64_and_fixed_sizes(self):
        for wide, fixed in [(False,False),(True,False),(False,True),(True,True)]:
            with self.subTest(wide=wide,fixed=fixed):
                source,key,clear=encrypted_fixture(wide,fixed)
                result=self.decrypt(source,key)
                self.assertNotIn('error',result)
                output=base64.b64decode(result['data'])
                self.assertEqual(output,decrypt_mp4(source,key))
                self.assertTrue(output.endswith(clear))
                self.assertNotIn(b'encv',output)
                self.assertNotIn(b'enca',output)

    def test_corrupt_structure_or_key_is_rejected_before_playback(self):
        source,key,_=encrypted_fixture(invalid_offset=True)
        self.assertIn('error',self.decrypt(source,key))
        source,key,_=encrypted_fixture()
        self.assertIn('error',self.decrypt(source,'bad-key'))
        self.assertIn('error',self.decrypt(source[:60],key))
