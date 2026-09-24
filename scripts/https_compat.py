"""Plan host HTTPS ports and reuse existing certificates when 80/443 are busy."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


HTTPS_CANDIDATES = (8443, 9443, 10443, 2443, 3443, 4443, 5443, 6443, 7443, 11443)
ACME_CANDIDATES = (8880, 8888, 18080, 18088, 28080, 28088)
LOCAL_HTTP_CANDIDATES = (18080, 18081, 18082, 18083, 18084)
XUI_MARKERS = (
    '/usr/local/x-ui/x-ui',
    '/etc/x-ui/x-ui.db',
    '/usr/local/x-ui/bin/xray-linux-amd64',
    '/usr/local/x-ui/bin/xray-linux-arm64',
    '/etc/systemd/system/x-ui.service',
)
SAFE_PUBLISH = re.compile(r'^[0-9.:]+(?:/udp)?$')
SAFE_TOKEN = re.compile(r'^[A-Za-z0-9._:/-]*$')
SAFE_URL = re.compile(r'^https://[A-Za-z0-9.-]+(?::[0-9]{2,5})?$')


def proc_net_dir():
    return Path(os.environ.get('TACO_PROC_NET', '/proc/net'))


def parse_proc_net(text):
    ports = set()
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != '0A':
            continue
        local = parts[1]
        if ':' not in local:
            continue
        try:
            ports.add(int(local.rsplit(':', 1)[1], 16))
        except ValueError:
            continue
    return ports


def listening_ports(directory=None):
    directory = Path(directory or proc_net_dir())
    ports = set()
    for name in ('tcp', 'tcp6'):
        path = directory / name
        if path.is_file() and not path.is_symlink():
            ports.update(parse_proc_net(path.read_text(encoding='ascii', errors='ignore')))
    extra = os.environ.get('TACO_LISTEN_PORTS', '')
    if extra.strip():
        for item in extra.split(','):
            item = item.strip()
            if item.isdigit():
                ports.add(int(item))
    return ports


def xui_detected():
    markers = os.environ.get('TACO_XUI_MARKERS')
    paths = [item for item in markers.split(':') if item] if markers else list(XUI_MARKERS)
    return any(Path(path).exists() for path in paths)


def pick_free(candidates, busy, taken=()):
    used = set(busy)
    used.update(taken)
    for port in candidates:
        if port not in used:
            return port
    raise ValueError('没有可用的备用端口，请关闭占用 80/443 的入站后再安装。')


def public_url(domain, https_port):
    if https_port == 443:
        return f'https://{domain}'
    return f'https://{domain}:{https_port}'


def choose_caddyfile(tls_mode, http_busy):
    if tls_mode == 'file':
        return './deploy/Caddyfile.tls'
    if http_busy:
        return './deploy/Caddyfile.alpn'
    return './deploy/Caddyfile'


def cert_search_roots(domain):
    roots = os.environ.get('TACO_CERT_SEARCH_ROOTS')
    if roots:
        return [Path(item) for item in roots.split(':') if item]
    home = Path(os.environ.get('HOME') or '/root')
    return [
        Path('/root/cert') / domain,
        Path('/root/.acme.sh') / f'{domain}_ecc',
        Path('/root/.acme.sh') / domain,
        home / '.acme.sh' / f'{domain}_ecc',
        home / '.acme.sh' / domain,
        Path('/etc/letsencrypt/live') / domain,
    ]


def cert_pair_in(directory, domain):
    directory = Path(directory)
    candidates = (
        (directory / 'fullchain.pem', directory / 'privkey.pem'),
        (directory / 'fullchain.cer', directory / f'{domain}.key'),
        (directory / 'fullchain.cer', directory / 'privkey.pem'),
        (directory / 'cert.pem', directory / 'privkey.pem'),
    )
    for cert, key in candidates:
        if cert.is_file() and key.is_file() and not cert.is_symlink() and not key.is_symlink():
            return cert, key
    return None


def openssl(*args):
    try:
        return subprocess.run(['openssl', *args], check=False, capture_output=True, text=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(['openssl', *args], 1, '', '')


def openssl_available():
    return openssl('version').returncode == 0


def cert_covers_domain(cert, domain):
    names = set()
    subject = openssl('x509', '-noout', '-in', str(cert), '-subject')
    if subject.returncode == 0:
        match = re.search(r'CN\s*=\s*([^,\n/]+)', subject.stdout)
        if match:
            names.add(match.group(1).strip().lower().rstrip('.'))
    san = openssl('x509', '-noout', '-in', str(cert), '-ext', 'subjectAltName')
    if san.returncode == 0:
        for match in re.finditer(r'DNS:([^,\s]+)', san.stdout):
            names.add(match.group(1).strip().lower().rstrip('.'))
    return domain.lower().rstrip('.') in names


def cert_unexpired(cert, seconds=7 * 24 * 3600):
    result = openssl('x509', '-noout', '-checkend', str(seconds), '-in', str(cert))
    return result.returncode == 0


def cert_usable(cert, domain):
    if not openssl_available():
        return True
    return cert_covers_domain(cert, domain) and cert_unexpired(cert)


def find_existing_cert(domain, extra_dirs=()):
    directories = [Path(item) for item in extra_dirs if item]
    directories.extend(cert_search_roots(domain))
    seen = set()
    for directory in directories:
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        pair = cert_pair_in(directory, domain)
        if pair is None:
            continue
        cert, key_file = pair
        if cert_usable(cert, domain):
            return cert, key_file
    return None


def copy_private(source, dest):
    source = Path(source)
    dest = Path(dest)
    if source.resolve() == dest.resolve():
        os.chmod(dest, 0o600)
        return dest
    staging = dest.with_name(dest.name + '.tmp')
    shutil.copyfile(source, staging)
    os.chmod(staging, 0o600)
    staging.replace(dest)
    return dest


def copy_cert(domain, dest, extra_dirs=()):
    pair = find_existing_cert(domain, extra_dirs=extra_dirs)
    if pair is None:
        return None
    dest = Path(dest)
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    cert, key = pair
    return copy_private(cert, dest / 'fullchain.pem'), copy_private(key, dest / 'privkey.pem')


def plan(domain, extra_cert_dirs=()):
    busy = listening_ports()
    http_busy = 80 in busy
    https_busy = 443 in busy
    taken = set(busy)
    xui = xui_detected()
    existing = find_existing_cert(domain, extra_dirs=extra_cert_dirs)
    if https_busy:
        https_port = pick_free(HTTPS_CANDIDATES, busy, taken)
        taken.add(https_port)
        tls_mode = 'file'
        reason = 'https_busy'
    else:
        https_port = 443
        tls_mode = 'auto'
        reason = 'http_busy' if http_busy else 'free'
    if http_busy:
        local_http = pick_free(LOCAL_HTTP_CANDIDATES, busy, taken)
        taken.add(local_http)
        http_publish = f'127.0.0.1:{local_http}:80'
    else:
        http_publish = '80:80'
    acme_port = 80 if not http_busy else pick_free(ACME_CANDIDATES, busy, taken)
    need_redirect = tls_mode == 'file' and http_busy and existing is None
    return {
        'tls_mode': tls_mode,
        'http_busy': http_busy,
        'https_busy': https_busy,
        'xui_detected': xui,
        'http_publish': http_publish,
        'https_publish': f'{https_port}:443',
        'https_udp_publish': f'{https_port}:443/udp',
        'https_port': https_port,
        'public_port_suffix': '' if https_port == 443 else f':{https_port}',
        'public_url': public_url(domain, https_port),
        'acme_port': acme_port,
        'need_http01_redirect': need_redirect,
        'existing_cert': str(existing[0]) if existing else '',
        'existing_key': str(existing[1]) if existing else '',
        'reason': reason,
        'caddyfile': choose_caddyfile(tls_mode, http_busy),
        'cert_dir': './deploy/certs',
    }


def validate_plan(values):
    for key in ('http_publish', 'https_publish', 'https_udp_publish'):
        if not SAFE_PUBLISH.fullmatch(str(values[key])):
            raise ValueError('端口映射无效。')
    if values['tls_mode'] not in ('auto', 'file'):
        raise ValueError('TLS 模式无效。')
    if not isinstance(values['https_port'], int) or not (1 <= values['https_port'] <= 65535):
        raise ValueError('HTTPS 端口无效。')
    suffix = values['public_port_suffix']
    if suffix not in ('', f":{values['https_port']}"):
        raise ValueError('公网端口后缀无效。')
    if not SAFE_URL.fullmatch(values['public_url']):
        raise ValueError('公网地址无效。')
    if not SAFE_TOKEN.fullmatch(values['caddyfile']) or not SAFE_TOKEN.fullmatch(values['cert_dir']):
        raise ValueError('证书路径无效。')
    return values


def runtime_env(values):
    values = validate_plan(values)
    lines = [
        f"TACO_HTTP_PUBLISH={values['http_publish']}",
        f"TACO_HTTPS_PUBLISH={values['https_publish']}",
        f"TACO_HTTPS_UDP_PUBLISH={values['https_udp_publish']}",
        f"TACO_PUBLIC_PORT={values['public_port_suffix']}",
        f"TACO_CADDYFILE={values['caddyfile']}",
        f"TACO_CERT_DIR={values['cert_dir']}",
        f"HONGGUO_PUBLIC_URL={values['public_url']}",
    ]
    return '# TACO runtime bind settings; not secrets.\n' + '\n'.join(lines) + '\n'


def write_runtime_env(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = runtime_env(values)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(content)
    os.chmod(path, 0o600)


def main():
    parser = argparse.ArgumentParser(description='TACO HTTPS 端口与证书兼容')
    parser.add_argument('--domain', required=True)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--copy-cert', action='store_true')
    parser.add_argument('--write-runtime', type=Path)
    parser.add_argument('--dest', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--get')
    parser.add_argument('--extra-cert-dir', action='append', default=[])
    args = parser.parse_args()
    try:
        values = validate_plan(plan(args.domain, extra_cert_dirs=args.extra_cert_dir))
        if args.write_runtime:
            write_runtime_env(args.write_runtime, values)
        if args.output:
            args.output.write_text(json.dumps(values, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        if args.copy_cert:
            if args.dest is None:
                raise ValueError('--copy-cert 需要 --dest。')
            extra = list(args.extra_cert_dir)
            if values['existing_cert']:
                extra.insert(0, str(Path(values['existing_cert']).parent))
            copied = copy_cert(args.domain, args.dest, extra_dirs=extra)
            if copied is None:
                raise FileNotFoundError('没有可复用的有效证书。')
        if args.get:
            value = values[args.get]
            if isinstance(value, bool):
                print('1' if value else '0')
            else:
                print(value)
        elif args.plan and args.output is None and not args.copy_cert:
            json.dump(values, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write('\n')
    except FileNotFoundError as exc:
        if args.copy_cert:
            parser.exit(2)
        parser.exit(2, str(exc) + '\n')
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
