import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

import httpx
from country_access import CountryAccess
from scripts.check_https import check
import server


COUNTRIES = {'223.5.5.5': 'CN', '2400:3200::1': 'CN', '8.8.8.8': 'US',
             '2001:4860:4860::8888': 'US', '1.36.0.1': 'HK',
             '202.175.0.1': 'MO', '168.95.1.1': 'TW'}


def reader():
    return Mock(get=lambda ip: {'country': {'iso_code': COUNTRIES[ip]}} if ip in COUNTRIES else None)


class CountryAccessTests(unittest.TestCase):
    def test_ipv4_ipv6_and_mapped_addresses(self):
        with patch('country_access.maxminddb.open_database', return_value=reader()):
            access = CountryAccess('/test.mmdb')
        for host, country in COUNTRIES.items():
            with self.subTest(host=host):
                self.assertEqual(access.status(host), 403 if country == 'CN' else 200)
        self.assertEqual(access.status('::ffff:223.5.5.5'), 403)
        self.assertEqual(access.status('::ffff:8.8.8.8'), 200)
        original = access.reader
        access.close()
        original.close.assert_called_once()

    def test_missing_database_unknown_and_malformed_addresses_fail_closed(self):
        with patch('country_access.maxminddb.open_database', side_effect=FileNotFoundError):
            access = CountryAccess('/absent.mmdb')
        for host in ('8.8.8.8', '223.5.5.5', '', None, 'invalid', '8.8.8.8, 127.0.0.1'):
            self.assertEqual(access.status(host), 503)
        for host in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
            self.assertEqual(access.status(host), 200)
        for record in (None, {}, {'country': None}, {'country': {'iso_code': 'ZZ'}}, {'country': {'iso_code': 'cn'}}):
            access.reader = Mock(get=Mock(return_value=record))
            self.assertEqual(access.status('8.8.8.8'), 503)

    def test_https_probe_accepts_only_the_expected_geoblock(self):
        def error(body):
            return urllib.error.HTTPError('https://video.taco.net/healthz', 403, 'Forbidden', {}, io.BytesIO(json.dumps(body).encode()))
        with patch('scripts.check_https.urllib.request.urlopen', side_effect=error({'code':'REGION_BLOCKED'})):
            self.assertTrue(check('https://video.taco.net', timeout=.02))
        with patch('scripts.check_https.urllib.request.urlopen', side_effect=error({'code':'unrelated'})):
            self.assertFalse(check('https://video.taco.net', timeout=.02))


class CountryBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.cache = self.folder/'videos'
        self.cache.mkdir()
        self.patches = [patch.object(server, 'DATA', self.folder), patch.object(server, 'CACHE', self.cache),
                        patch.dict(os.environ, {'HONGGUO_NETWORK_MODE': 'direct'}),
                        patch('country_access.maxminddb.open_database', return_value=reader())]
        for item in self.patches:
            item.start()
        self.lifespan = server.app.router.lifespan_context(server.app)
        await self.lifespan.__aenter__()
        self.db = server.app.state.accounts
        uid = self.db.create_user('tester', 'unused', role='admin')
        self.cookie = self.db.login(uid)
        share = self.db.create_share(uid, {'book_id':'999', 'title':'地区测试'}, [{'item_id':'123','index':1}], False, 0)
        self.token = share['token']
        (self.cache/'123_720p.mp4').write_bytes(b'0123456789')

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def client(self, ip):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app, client=(ip, 1234)), base_url='http://testserver')

    async def test_cn_blocks_login_admin_shared_cached_media_and_post_even_when_authenticated(self):
        for ip in ('223.5.5.5', '2400:3200::1', '::ffff:223.5.5.5'):
            async with self.client(ip) as client:
                client.cookies.set('hongguo_session', self.cookie)
                client.headers.update({'X-Forwarded-For':'8.8.8.8', 'X-Real-IP':'127.0.0.1', 'CF-Connecting-IP':'8.8.8.8'})
                for path in ('/', '/login', '/setup', '/admin', '/healthz', '/static/style.css',
                             '/api/auth/status', '/api/auth/me', '/media/123_720p.mp4',
                             '/s/'+self.token, '/api/shared/'+self.token, '/shared-media/'+self.token+'/123_720p.mp4'):
                    response = await client.get(path, headers={'Range':'bytes=0-3'})
                    self.assertEqual(response.status_code, 403, (ip, path))
                    self.assertEqual(response.json()['code'], 'REGION_BLOCKED')
                    self.assertEqual(response.headers['cache-control'], 'no-store')
                for path in ('/api/auth/login', '/api/play/stream?item_id=123', '/api/shared/'+self.token+'/stream?item_id=123'):
                    self.assertEqual((await client.post(path)).json()['code'], 'REGION_BLOCKED')
                html = await client.get('/login', headers={'Accept':'text/html'})
                self.assertIn('暂不支持中国大陆 IP', html.text)

    async def test_non_cn_guests_can_use_valid_share_but_still_need_login_elsewhere(self):
        for ip, country in COUNTRIES.items():
            if country == 'CN':
                continue
            async with self.client(ip) as client:
                self.assertEqual((await client.get('/')).status_code, 303)
                self.assertEqual((await client.get('/login')).status_code, 200)
                self.assertEqual((await client.get('/s/'+self.token)).status_code, 200)
                part = await client.get('/shared-media/'+self.token+'/123_720p.mp4', headers={'Range':'bytes=2-4'})
                self.assertEqual((part.status_code, part.content), (206, b'234'))
                self.assertEqual((await client.get('/media/123_720p.mp4')).status_code, 401)

    async def test_unknown_country_or_failed_database_does_not_bypass_gate(self):
        async with self.client('192.0.2.1') as client:
            self.assertEqual((await client.get('/login')).status_code, 503)
        server.app.state.country_access.close()
        async with self.client('8.8.8.8') as client:
            self.assertEqual((await client.get('/s/'+self.token)).status_code, 503)
        with patch.dict(os.environ, {'HONGGUO_ENV': 'production'}):
            async with self.client('127.0.0.1') as client:
                self.assertEqual((await client.get('/healthz')).status_code, 503)
