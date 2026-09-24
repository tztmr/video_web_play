import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.https_compat import (
    copy_cert,
    listening_ports,
    parse_proc_net,
    plan,
    runtime_env,
    write_runtime_env,
    xui_detected,
)


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = 'video.taco.net'


def proc_net_text(*ports):
    lines = ['  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode']
    for index, port in enumerate(ports):
        lines.append(
            f'{index:4d}: 00000000:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1 1 0000000000000000 100 0 0 10 0'
        )
    return '\n'.join(lines) + '\n'


def make_cert(directory, domain=DOMAIN, days=30):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / 'fullchain.pem', directory / 'privkey.pem'
    result = subprocess.run(
        ['openssl', 'req', '-x509', '-nodes', '-newkey', 'rsa:2048',
         '-keyout', str(key), '-out', str(cert), '-days', str(days),
         '-subj', f'/CN={domain}'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    os.chmod(cert, 0o600)
    os.chmod(key, 0o600)
    return cert, key


class HttpsCompatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.saved = {key: os.environ.get(key) for key in (
            'TACO_LISTEN_PORTS', 'TACO_PROC_NET', 'TACO_CERT_SEARCH_ROOTS', 'TACO_XUI_MARKERS',
        )}
        for key in self.saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_parse_proc_net_listening_hex_ports(self):
        ports = parse_proc_net(proc_net_text(80, 443, 8443))
        self.assertEqual(ports, {80, 443, 8443})

    def test_listening_ports_reads_proc_net_and_ignores_symlinks(self):
        proc = self.folder / 'proc'
        proc.mkdir()
        (proc / 'tcp').write_text(proc_net_text(80))
        (proc / 'tcp6').write_text(proc_net_text(443))
        os.environ['TACO_PROC_NET'] = str(proc)
        self.assertEqual(listening_ports(), {80, 443})
        other = self.folder / 'other'
        other.write_text(proc_net_text(22))
        (proc / 'tcp').unlink()
        (proc / 'tcp').symlink_to(other)
        os.environ['TACO_LISTEN_PORTS'] = '9443'
        self.assertEqual(listening_ports(), {443, 9443})

    def test_free_ports_keep_caddy_auto_https(self):
        values = plan(DOMAIN)
        self.assertEqual(values['tls_mode'], 'auto')
        self.assertEqual(values['https_port'], 443)
        self.assertEqual(values['http_publish'], '80:80')
        self.assertEqual(values['https_publish'], '443:443')
        self.assertEqual(values['caddyfile'], './deploy/Caddyfile')
        self.assertEqual(values['public_url'], f'https://{DOMAIN}')
        self.assertFalse(values['need_http01_redirect'])

    def test_busy_http_only_uses_tls_alpn_and_loopback_http(self):
        os.environ['TACO_LISTEN_PORTS'] = '80'
        values = plan(DOMAIN)
        self.assertEqual(values['tls_mode'], 'auto')
        self.assertTrue(values['http_busy'])
        self.assertFalse(values['https_busy'])
        self.assertEqual(values['https_port'], 443)
        self.assertEqual(values['http_publish'], '127.0.0.1:18080:80')
        self.assertEqual(values['caddyfile'], './deploy/Caddyfile.alpn')
        self.assertFalse(values['need_http01_redirect'])
        self.assertEqual(values['public_url'], f'https://{DOMAIN}')

    def test_busy_https_reuses_3xui_cert_without_http01_redirect(self):
        cert_dir = self.folder / 'root' / 'cert' / DOMAIN
        cert, key = make_cert(cert_dir)
        marker = self.folder / 'x-ui.db'
        marker.write_text('xui')
        os.environ['TACO_LISTEN_PORTS'] = '80,443'
        os.environ['TACO_CERT_SEARCH_ROOTS'] = str(cert_dir)
        os.environ['TACO_XUI_MARKERS'] = str(marker)
        values = plan(DOMAIN, extra_cert_dirs=(str(self.folder / 'unused'),))
        self.assertTrue(values['xui_detected'])
        self.assertTrue(xui_detected())
        self.assertEqual(values['tls_mode'], 'file')
        self.assertEqual(values['https_port'], 8443)
        self.assertEqual(values['https_publish'], '8443:443')
        self.assertEqual(values['https_udp_publish'], '8443:443/udp')
        self.assertEqual(values['public_url'], f'https://{DOMAIN}:8443')
        self.assertEqual(values['public_port_suffix'], ':8443')
        self.assertEqual(values['caddyfile'], './deploy/Caddyfile.tls')
        self.assertFalse(values['need_http01_redirect'])
        self.assertEqual(values['existing_cert'], str(cert))
        self.assertEqual(values['existing_key'], str(key))
        self.assertEqual(values['acme_port'], 8880)

    def test_busy_https_without_cert_needs_temporary_http01_redirect(self):
        os.environ['TACO_LISTEN_PORTS'] = '80,443,8443'
        os.environ['TACO_CERT_SEARCH_ROOTS'] = str(self.folder / 'empty')
        values = plan(DOMAIN)
        self.assertEqual(values['https_port'], 9443)
        self.assertTrue(values['need_http01_redirect'])
        self.assertEqual(values['existing_cert'], '')
        self.assertEqual(values['acme_port'], 8880)

    def test_runtime_env_is_unquoted_and_copy_cert_is_silent_when_missing(self):
        os.environ['TACO_LISTEN_PORTS'] = '80,443'
        os.environ['TACO_CERT_SEARCH_ROOTS'] = str(self.folder / 'missing')
        values = plan(DOMAIN)
        text = runtime_env(values)
        self.assertIn('TACO_HTTPS_PUBLISH=8443:443\n', text)
        self.assertIn(f'HONGGUO_PUBLIC_URL=https://{DOMAIN}:8443\n', text)
        self.assertNotIn("'", text)
        path = self.folder / 'runtime.env'
        write_runtime_env(path, values)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.read_text(encoding='utf-8'), text)
        missing = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/https_compat.py'),
             '--domain', DOMAIN, '--copy-cert', '--dest', str(self.folder / 'dest')],
            capture_output=True, text=True, env={**os.environ, 'TACO_CERT_SEARCH_ROOTS': str(self.folder / 'missing')},
        )
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stderr, '')
        self.assertEqual(missing.stdout, '')

    def test_copy_cert_reuses_existing_files_without_self_copy_error(self):
        cert_dir = self.folder / 'certs'
        make_cert(cert_dir)
        copied = copy_cert(DOMAIN, cert_dir, extra_dirs=(str(cert_dir),))
        self.assertEqual(copied[0], cert_dir / 'fullchain.pem')
        dest = self.folder / 'deploy' / 'certs'
        copied = copy_cert(DOMAIN, dest, extra_dirs=(str(cert_dir),))
        self.assertEqual((dest / 'fullchain.pem').stat().st_mode & 0o777, 0o600)
        self.assertGreater((dest / 'fullchain.pem').stat().st_size, 0)
        self.assertEqual(copied[1], dest / 'privkey.pem')

    def test_caddyfiles_disable_http01_or_http3_in_compat_modes(self):
        tls = (ROOT / 'deploy/Caddyfile.tls').read_text(encoding='utf-8')
        alpn = (ROOT / 'deploy/Caddyfile.alpn').read_text(encoding='utf-8')
        stack = (ROOT / 'scripts/deploy_stack.sh').read_text(encoding='utf-8')
        self.assertIn('tls /certs/fullchain.pem /certs/privkey.pem', tls)
        self.assertIn('protocols h1 h2', tls)
        self.assertNotIn('h3', tls)
        self.assertIn('disable_http_challenge', alpn)
        self.assertIn('auto_https disable_redirects', alpn)
        self.assertIn('deploy/acme', stack)
        self.assertIn('LE_WORKING_DIR="$ACME_HOME"', stack)
        self.assertNotIn('systemctl stop x-ui', stack)
        self.assertNotIn('systemctl stop xray', stack)
        self.assertNotIn('/root/.acme.sh', stack)


if __name__ == '__main__':
    unittest.main()
