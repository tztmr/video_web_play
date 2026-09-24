"""Verify a freshly launched container without creating a permanent account."""
import json
import sys
import time
import urllib.error
import urllib.request

base = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else 'http://127.0.0.1:18787'
deadline = time.monotonic()+60
while True:
    try:
        with urllib.request.urlopen(base+'/healthz', timeout=3) as response:
            assert json.loads(response.read())['status'] == 'ok'
        break
    except (OSError, ValueError):
        if time.monotonic() >= deadline:
            raise SystemExit('Container did not become ready within 60 seconds')
        time.sleep(1)
with urllib.request.urlopen(base+'/', timeout=3) as response:
    assert response.geturl() == base+'/login'
    assert 'TACO小剧场' in response.read().decode()
with urllib.request.urlopen(base+'/api/auth/status', timeout=3) as response:
    assert json.loads(response.read())['setup_required'] is True
for path in ('/static/style.css', '/static/playback.js', '/static/direct-worker.js', '/static/mp4-decrypt.js'):
    with urllib.request.urlopen(base+path, timeout=3) as response:
        assert response.status == 200 and response.read(), path
for path, method in [('/api/auth/me', 'GET'), ('/api/play/prefetch?item_id=1', 'POST'), ('/api/play/source?item_id=1', 'POST')]:
    try:
        urllib.request.urlopen(urllib.request.Request(base+path, method=method), timeout=3)
        raise AssertionError('Unauthenticated endpoint was allowed: '+path)
    except urllib.error.HTTPError as error:
        assert error.code == 401, (path, error.code)
print('Container ready; TACO branding, initialization, and login boundaries verified.')
