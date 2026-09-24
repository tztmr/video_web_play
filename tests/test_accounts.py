import asyncio
import os
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.responses import JSONResponse, Response
import server
from accounts import password_hash


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.network_patch=patch.dict(os.environ, {'HONGGUO_NETWORK_MODE':'direct'})
        self.network_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.cache = self.folder/'videos'
        self.cache.mkdir()
        self.patches = [patch.object(server, 'DATA', self.folder), patch.object(server, 'CACHE', self.cache)]
        for p in self.patches:
            p.start()
        self.lifespan = server.app.router.lifespan_context(server.app)
        await self.lifespan.__aenter__()
        self.db = server.app.state.accounts
        self.clients = []
        self.guest = self.client()

    def client(self):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://testserver')
        self.clients.append(client)
        return client

    async def admin(self):
        client = self.client()
        response = await client.post('/api/auth/setup', json={'token':self.db.setup_file.read_text(),'username':'admin','password':'AdminPassword123!'})
        self.assertEqual(response.status_code, 200, response.text)
        return client

    async def asyncTearDown(self):
        self.network_patch.stop()
        for c in self.clients:
            await c.aclose()
        await self.lifespan.__aexit__(None,None,None)
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    async def test_login_required_for_pages_apis_and_video(self):
        for path in ['/', '/admin', '/static/index.html', '/static/admin.html']:
            r = await self.guest.get(path)
            self.assertEqual(r.status_code, 303, path)
            self.assertEqual(r.headers['location'], '/login')
        for path in ['/api/duanju/catalog?book_id=1','/api/admin/users','/media/1_720p.mp4','/api/auth/me']:
            self.assertEqual((await self.guest.get(path)).status_code, 401, path)
        self.assertEqual((await self.guest.post('/api/play?item_id=1')).status_code, 401)
        self.assertEqual((await self.guest.post('/api/play/prefetch?item_id=1')).status_code, 401)
        self.assertEqual((await self.guest.post('/api/play/warm?item_id=1')).status_code, 401)
        self.assertEqual((await self.guest.post('/api/play/source?item_id=1')).status_code, 401)
        self.assertEqual((await self.guest.get('/login')).status_code, 200)
        self.assertEqual((await self.guest.get('/healthz')).status_code, 200)

    async def test_setup_one_time_hashing_cookie_and_logout(self):
        token = self.db.setup_file.read_text()
        admin = await self.admin()
        self.assertFalse(self.db.setup_file.exists())
        second = await self.guest.post('/api/auth/setup',json={'token':token,'username':'another','password':'AdminPassword123!'})
        self.assertEqual(second.status_code,403)
        old_cookie = admin.cookies.get('hongguo_session')
        with self.db.db() as db:
            self.assertNotEqual(db.execute('SELECT token_hash FROM sessions').fetchone()[0], old_cookie)
            self.assertNotIn('AdminPassword123!', db.execute('SELECT password FROM users').fetchone()[0])
        self.assertEqual((await admin.get('/')).status_code,200)
        await admin.post('/api/auth/logout')
        self.guest.cookies.set('hongguo_session',old_cookie)
        self.assertEqual((await self.guest.get('/api/auth/me')).status_code,401)

    async def test_viewer_cannot_manage_and_disable_invalidates_existing_session(self):
        admin = await self.admin()
        created = await admin.post('/api/admin/users',json={'username':'viewer','password':'ViewerPassword123!'})
        uid=created.json()['id']
        login=await self.guest.post('/api/auth/login',json={'username':'viewer','password':'ViewerPassword123!'})
        self.assertEqual(login.status_code,200)
        cookie=login.headers['set-cookie'].lower()
        self.assertIn('httponly',cookie)
        self.assertIn('samesite=lax',cookie)
        self.assertEqual((await self.guest.get('/admin')).status_code,403)
        self.assertEqual((await self.guest.get('/api/admin/users')).status_code,403)
        self.assertEqual((await self.guest.post('/api/admin/shares',json={'book_id':'1','title':'test'})).status_code,403)
        self.assertEqual((await admin.patch('/api/admin/users/'+str(uid),json={'disabled':True})).status_code,200)
        self.assertEqual((await self.guest.get('/api/auth/me')).status_code,401)
        self.assertEqual((await self.guest.post('/api/auth/login',json={'username':'viewer','password':'ViewerPassword123!'})).status_code,401)

    async def test_password_reset_invalidates_sessions_and_old_password(self):
        admin=await self.admin()
        uid=(await admin.post('/api/admin/users',json={'username':'viewer','password':'ViewerPassword123!'})).json()['id']
        await self.guest.post('/api/auth/login',json={'username':'viewer','password':'ViewerPassword123!'})
        await admin.patch('/api/admin/users/'+str(uid),json={'password':'NewPassword456!'})
        self.assertEqual((await self.guest.get('/api/auth/me')).status_code,401)
        self.assertEqual((await self.guest.post('/api/auth/login',json={'username':'viewer','password':'ViewerPassword123!'})).status_code,401)
        self.assertEqual((await self.guest.post('/api/auth/login',json={'username':'viewer','password':'NewPassword456!'})).status_code,200)

    async def test_login_rate_limit_and_cross_origin(self):
        await self.admin()
        for _ in range(10):
            self.assertEqual((await self.guest.post('/api/auth/login',json={'username':'admin','password':'WrongPassword123!'})).status_code,401)
        self.assertEqual((await self.guest.post('/api/auth/login',json={'username':'admin','password':'AdminPassword123!'})).status_code,429)
        self.assertEqual((await self.guest.post('/api/auth/logout',headers={'Origin':'https://unrelated.example'})).status_code,403)

    async def share(self, admin):
        response=JSONResponse({'code':0,'data':{'items':[{'item_id':'123','index':1,'title':'第一集'}]}})
        with patch.object(server.duanju,'duanju_catalog',AsyncMock(return_value=response)):
            r=await admin.post('/api/admin/shares',json={'book_id':'999','title':'被分享的剧','hours':1})
        self.assertEqual(r.status_code,200,r.text)
        return r.json(),r.json()['path'].split('/')[-1]

    async def test_guest_share_scope_range_revoke_and_private_media(self):
        admin=await self.admin()
        link,token=await self.share(admin)
        self.assertEqual((await self.guest.get(link['path'])).status_code,200)
        self.assertEqual((await self.guest.get('/api/shared/'+token)).json()['item']['book_id'],'999')
        self.assertEqual((await self.guest.post('/api/shared/'+token+'/play?item_id=456')).status_code,403)
        with patch.object(server.duanju,'duanju_download',AsyncMock(return_value=Response(b'0123456789',media_type='video/mp4'))):
            result=await self.guest.post('/api/shared/'+token+'/play?item_id=123')
        self.assertEqual(result.status_code,200,result.text)
        url=result.json()['url']
        part=await self.guest.get(url,headers={'Range':'bytes=2-4'})
        self.assertEqual(part.status_code,206)
        self.assertEqual(part.content,b'234')
        self.assertEqual(part.headers['cache-control'],'no-store')
        self.assertEqual((await self.guest.get('/media/123_720p.mp4?token='+token)).status_code,401)
        self.assertEqual((await self.guest.get('/shared-media/'+token+'/456_720p.mp4')).status_code,403)
        await admin.delete('/api/admin/shares/'+link['id'])
        for path in [url,'/api/shared/'+token,link['path']]:
            self.assertEqual((await self.guest.get(path)).status_code,410,path)
        self.assertEqual((await self.guest.post('/api/shared/'+token+'/play?item_id=123')).status_code,410)

    async def test_expired_share_cannot_read_already_cached_video(self):
        admin=await self.admin()
        _,token=await self.share(admin)
        (self.cache/'123_720p.mp4').write_bytes(b'video')
        with self.db.db() as db:
            db.execute('UPDATE shares SET expires=? WHERE token=?',(time.time()-1,token))
        self.assertEqual((await self.guest.get('/shared-media/'+token+'/123_720p.mp4')).status_code,410)

    async def test_default_permanent_share_and_custom_days(self):
        admin = await self.admin()
        catalog = JSONResponse({'code':0,'data':{'items':[{'item_id':'123','index':1}]}})
        with patch.object(server.duanju,'duanju_catalog',AsyncMock(return_value=catalog)):
            for days in (None, 0, 2):
                body = {'book_id':'999','title':'test'}
                if days is not None:
                    body['days'] = days
                before = time.time()
                r = await admin.post('/api/admin/shares',json=body)
                self.assertEqual(r.status_code, 200, r.text)
                link = r.json()
                if days:
                    self.assertAlmostEqual(link['expires'] - before, days*86400, delta=2)
                else:
                    self.assertEqual(link['expires'], 0)
                token = link['path'].split('/')[-1]
                (self.cache/'123_720p.mp4').write_bytes(b'cached-video')
                result = await self.guest.post('/api/shared/'+token+'/stream?item_id=123')
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['url'],'/shared-media/'+token+'/123_720p.mp4')
                self.assertEqual((await self.guest.get(result.json()['url'])).content,b'cached-video')
                self.assertEqual((await self.guest.post('/api/shared/'+token+'/stream?item_id=456')).status_code,403)
                self.assertEqual((await self.guest.post('/api/shared/'+token+'/prefetch?item_id=456')).status_code,403)
                self.assertEqual((await self.guest.post('/api/shared/'+token+'/prefetch?item_id=123')).json()['status'],'cached')
                await admin.delete('/api/admin/shares/'+link['id'])
                self.assertEqual((await self.guest.post('/api/shared/'+token+'/stream?item_id=123')).status_code,410)
                self.assertEqual((await self.guest.post('/api/shared/'+token+'/prefetch?item_id=123')).status_code,410)
            for days in (-1, 3651, 1.5):
                r = await admin.post('/api/admin/shares',json={'book_id':'999','title':'test','days':days})
                self.assertEqual(r.status_code,422)
        self.assertEqual((await self.guest.post('/api/play/stream?item_id=123')).status_code,401)

    async def test_share_creation_requires_real_catalog(self):
        admin=await self.admin()
        bad=JSONResponse({'code':-3,'msg':'目录不可用'},status_code=502)
        with patch.object(server.duanju,'duanju_catalog',AsyncMock(return_value=bad)):
            r=await admin.post('/api/admin/shares',json={'book_id':'999','title':'test'})
        self.assertEqual(r.status_code,502)
        self.assertEqual(self.db.shares(),[])

    async def test_direct_descriptor_downloads_no_media_and_enforces_share_scope(self):
        admin = await self.admin()
        link, token = await self.share(admin)
        model = {'sources':[{'definition':'720p', 'codec_type':'h264', 'size':8000000,
                  'urls':['http://v3-reading-video.qznovelvod.com/video.mp4?test=1'], 'spade_a':'01'*16}]}
        with patch('video_loader.video_model', AsyncMock(return_value=model)) as metadata, patch('video_loader.download_ranges', AsyncMock(side_effect=AssertionError('Server downloaded video'))) as download, patch('video_loader.prepare_streaming_video', AsyncMock(side_effect=AssertionError('Server transcoded video'))) as transcode:
            response = await admin.post('/api/play/source?item_id=123')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['delivery'], 'direct')
            self.assertEqual(response.json()['urls'], ['https://v3-reading-video.qznovelvod.com/video.mp4?test=1'])
            self.assertEqual(response.json()['key'], '01'*16)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            guest = await self.guest.post('/api/shared/'+token+'/source?item_id=123')
            self.assertEqual(guest.status_code, 200, guest.text)
            calls = metadata.await_count
            self.assertEqual((await self.guest.post('/api/shared/'+token+'/source?item_id=456')).status_code, 403)
            self.assertEqual((await admin.post('/api/play/source?item_id=123', headers={'Origin':'https://unrelated.invalid'})).status_code, 403)
            await admin.delete('/api/admin/shares/'+link['id'])
            self.assertEqual((await self.guest.post('/api/shared/'+token+'/source?item_id=123')).status_code, 410)
            self.assertEqual(metadata.await_count, calls)
            download.assert_not_awaited()
            transcode.assert_not_awaited()
            self.assertFalse(list(self.cache.iterdir()))

    async def test_expired_share_cannot_obtain_a_new_direct_source(self):
        admin = await self.admin()
        _, token = await self.share(admin)
        with self.db.db() as db:
            db.execute('UPDATE shares SET expires=? WHERE token=?', (time.time()-1, token))
        with patch('server.browser_source', AsyncMock()) as source:
            self.assertEqual((await self.guest.post('/api/shared/'+token+'/source?item_id=123')).status_code, 410)
            source.assert_not_awaited()

    async def test_share_revoked_during_metadata_lookup_withholds_direct_source(self):
        admin = await self.admin()
        _, token = await self.share(admin)
        async def lookup(*args):
            with self.db.db() as db:
                db.execute('UPDATE shares SET revoked=1 WHERE token=?', (token,))
            return {'urls':['https://v3.qznovelvod.com/test.mp4'], 'key':'01'*16}
        with patch('server.browser_source', side_effect=lookup):
            response = await self.guest.post('/api/shared/'+token+'/source?item_id=123')
        self.assertEqual(response.status_code, 410)
        self.assertNotIn('key', response.json())

    async def test_secure_production_cookie(self):
        with patch('auth_routes.SECURE',True):
            token=self.db.setup_file.read_text()
            response=await self.guest.post('/api/auth/setup',json={'token':token,'username':'admin','password':'AdminPassword123!'})
        self.assertIn('Secure',response.headers['set-cookie'])

    async def test_playback_reports_are_admin_only(self):
        admin=await self.admin()
        uid=self.db.create_user('viewer',await asyncio.to_thread(password_hash,'ViewerPassword123!'))
        self.guest.cookies.set('hongguo_session',self.db.login(uid))
        payload={'title':'测试剧','item_id':'123','definition':'720p','startup_ms':500,'preparation_ms':100,'watched_seconds':61,'stall_count':0,'stall_seconds':0,'dropped_frames':0,'total_frames':1200,'cached':True}
        self.assertEqual((await self.guest.post('/api/reports',json=payload)).status_code,200)
        self.assertEqual((await self.guest.get('/api/admin/reports')).status_code,403)
        rows=(await admin.get('/api/admin/reports')).json()['items']
        self.assertEqual(rows[0]['payload']['region'],'unverified')
