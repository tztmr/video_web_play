"""Bounded public HTTPS readiness check, including certificate validation."""
import json
import sys
import time
import urllib.error
import urllib.request


def check(base_url, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            remaining = max(0.1, deadline - time.monotonic())
            with urllib.request.urlopen(base_url+'/healthz', timeout=min(5, remaining)) as response:
                if response.status == 200 and json.loads(response.read(1024)).get('status') == 'ok':
                    return True
        except urllib.error.HTTPError as error:
            # A probe originating in mainland China must be blocked as well.
            # Validate the app's response rather than opening a health bypass.
            with error:
                try:
                    if error.code == 403 and json.loads(error.read(1024)).get('code') == 'REGION_BLOCKED':
                        print('HTTPS 已连接；检测出口为大陆 IP，已按网站规则拒绝访问。')
                        return True
                except (ValueError, OSError):
                    pass
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(min(3, max(0, deadline-time.monotonic())))
    return False


if __name__ == '__main__':
    sys.exit(0 if check(sys.argv[1].rstrip('/')) else 1)
