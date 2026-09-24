"""Exercise the production server with proxy-provided IPv4/IPv6 and real MMDB data.

Run inside the disposable CI web container, whose loopback/private proxy peers
are trusted. The public Caddy path is checked separately for header spoofing.
"""
import json
import sys
import time
import urllib.error
import urllib.request


def request(path, ip=None):
    headers = {'X-Forwarded-For': ip} if ip else {}
    try:
        response = urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8787'+path, headers=headers), timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


for ip in ('223.5.5.5', '2400:3200::1', '::ffff:223.5.5.5'):
    for path in ('/login', '/healthz', '/s/invalid', '/shared-media/invalid/123_720p.mp4', '/static/style.css'):
        status, body = request(path, ip)
        assert status == 403 and json.loads(body)['code'] == 'REGION_BLOCKED', (ip, path, status)
for ip in ('8.8.8.8', '2001:4860:4860::8888', '1.36.0.1', '202.175.0.1', '168.95.1.1'):
    status, body = request('/login', ip)
    assert status == 200 and 'TACO小剧场' in body.decode(), (ip, status)
    assert request('/api/auth/me', ip)[0] == 401
assert request('/login', '192.0.2.1')[0] == 503
print('Real country database: CN IPv4/IPv6 blocked; non-CN login and authentication boundaries passed.')

if len(sys.argv) > 1:
    # The TCP peer of this disposable Caddy hop is a private Docker address.
    # Forged public or loopback headers must never turn its 503 into a 200.
    for ip in ('8.8.8.8', '127.0.0.1'):
        spoofed = urllib.request.Request(sys.argv[1].rstrip('/')+'/login', headers={
            'Host':'cinema.invalid', 'X-Forwarded-For':ip, 'CF-Connecting-IP':ip,
            'X-Real-IP':ip, 'True-Client-IP':ip, 'Forwarded':'for='+ip})
        deadline = time.monotonic()+20
        while True:
            try:
                with urllib.request.urlopen(spoofed, timeout=3) as response:
                    raise AssertionError(f'Forged forwarding headers were accepted: {response.status}')
            except urllib.error.HTTPError as error:
                with error:
                    assert error.code == 503 and json.loads(error.read())['code'] == 'REGION_UNAVAILABLE', error.code
                break
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
    print('Public Caddy configuration strips spoofed forwarding headers.')
