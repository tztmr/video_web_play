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
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(min(3, max(0, deadline-time.monotonic())))
    return False


if __name__ == '__main__':
    sys.exit(0 if check(sys.argv[1].rstrip('/')) else 1)
