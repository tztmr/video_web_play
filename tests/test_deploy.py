import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.deploy_config import read_settings, validate_settings
from scripts.deploy_config import main as configure
from scripts.check_https import check


ROOT = Path(__file__).resolve().parents[1]


class DeployConfigTests(unittest.TestCase):
    def test_overseas_is_default_and_requires_a_proxy(self):
        with self.assertRaisesRegex(ValueError, '代理'):
            validate_settings({'DOMAIN': 'video.taco.net'})
        settings = validate_settings({'DOMAIN': 'VIDEO.TACO.NET.', 'HONGGUO_UPSTREAM_PROXY': 'socks5://proxy.taco.net:1080'})
        self.assertEqual(settings['DOMAIN'], 'video.taco.net')
        self.assertEqual(settings['HONGGUO_NETWORK_MODE'], 'overseas')

    def test_rejects_config_injection_example_domains_and_loopback(self):
        for domain in ('https://video.taco.net', 'video.taco.net:443', 'x.taco.net\nEVIL=1', '*.taco.net', '127.0.0.1', 'cinema.example.com'):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                validate_settings({'DOMAIN': domain, 'HONGGUO_NETWORK_MODE': 'direct'})
        for proxy in ('socks5://localhost:1080', 'socks5://127.0.0.1:1080', 'socks5://[::1]:1080', 'socks5://0.0.0.0:1080', 'ftp://proxy.taco.net:80', "socks5://a'b@proxy.taco.net:80", 'http://proxy.taco.net:99999'):
            with self.subTest(proxy=proxy), self.assertRaises(ValueError):
                validate_settings({'DOMAIN':'video.taco.net', 'HONGGUO_UPSTREAM_PROXY':proxy})

    def test_direct_is_explicit_and_clears_stale_proxy(self):
        settings = validate_settings({'DOMAIN': 'video.taco.net', 'HONGGUO_NETWORK_MODE': 'direct', 'HONGGUO_UPSTREAM_PROXY':'socks5://old.taco.net:1080'})
        self.assertEqual(settings['HONGGUO_UPSTREAM_PROXY'], '')

    def test_repeated_configuration_preserves_project_and_literal_password(self):
        with tempfile.TemporaryDirectory() as folder:
            existing, output = Path(folder)/'.env', Path(folder)/'next.env'
            existing.write_text("DOMAIN='video.taco.net'\nHONGGUO_NETWORK_MODE='overseas'\nHONGGUO_UPSTREAM_PROXY='socks5://user:p$a%23ss@proxy.taco.net:1080'\nCOMPOSE_PROJECT_NAME='existing-cinema'\n")
            before = existing.read_bytes()
            run = subprocess.run([sys.executable, str(ROOT/'scripts/deploy_config.py'), '--existing', str(existing), '--output', str(output), '--non-interactive'], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(existing.read_bytes(), before)
            self.assertEqual(read_settings(output), read_settings(existing))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(run.stdout, 'video.taco.net\n')
            self.assertNotIn('p$a', run.stdout+run.stderr)

    def test_invalid_config_does_not_write_output_or_run_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            existing, output, sentinel = Path(folder)/'.env', Path(folder)/'next.env', Path(folder)/'executed'
            existing.write_text(f'DOMAIN=$(touch {sentinel})\nHONGGUO_NETWORK_MODE=direct\n')
            run = subprocess.run([sys.executable, str(ROOT/'scripts/deploy_config.py'), '--existing', str(existing), '--output', str(output), '--non-interactive'], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse(sentinel.exists())
            self.assertFalse(output.exists())

    def test_menu_reconfiguration_preserves_secrets_or_explicitly_switches_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            existing, output = Path(folder)/'.env', Path(folder)/'next.env'
            existing.write_text("DOMAIN='video.taco.net'\nHONGGUO_NETWORK_MODE='overseas'\nHONGGUO_UPSTREAM_PROXY='socks5://user:secret@proxy.taco.net:1080'\nCOMPOSE_PROJECT_NAME='existing-cinema'\n")
            args = ['deploy_config', '--existing', str(existing), '--output', str(output), '--reconfigure']
            with patch.object(sys, 'argv', args), patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['', '']), patch('getpass.getpass', return_value=''):
                configure()
            self.assertEqual(read_settings(existing), read_settings(output))
            with patch.object(sys, 'argv', args), patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['next.taco.net', '2']):
                configure()
            settings = read_settings(output)
            self.assertEqual(settings['DOMAIN'], 'next.taco.net')
            self.assertEqual(settings['HONGGUO_NETWORK_MODE'], 'direct')
            self.assertEqual(settings['HONGGUO_UPSTREAM_PROXY'], '')
            self.assertEqual(settings['COMPOSE_PROJECT_NAME'], 'existing-cinema')

    def test_legacy_project_name_keeps_existing_data_volumes(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder)/'.env'
            env.write_text('DOMAIN=video.taco.net\nHONGGUO_SOURCE_DIR=../old-downloader\nHONGGUO_NETWORK_MODE=direct\n')
            self.assertEqual(validate_settings(read_settings(env))['COMPOSE_PROJECT_NAME'], 'hongguo-cinema')

    def test_https_probe_rejects_wrong_response_and_retries(self):
        from unittest.mock import MagicMock
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.side_effect = [b'{"status":"wrong"}', b'{"status":"ok"}']
        with patch('scripts.check_https.urllib.request.urlopen', return_value=response) as request, patch('scripts.check_https.time.sleep'):
            self.assertTrue(check('https://video.taco.net', timeout=1))
            self.assertEqual(request.call_count, 2)


class DeployShellTests(unittest.TestCase):
    def test_deploy_update_and_https_failure_preserve_data_and_report_correctly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for path in ('scripts/deploy_stack.sh', 'compose.yaml', 'scripts/deploy_config.py', 'scripts/check_https.py', 'scripts/https_compat.py', 'vendor/hongguo/endpoints/duanju.py'):
                target = root/path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT/path, target)
            binary = root/'bin'
            binary.mkdir()
            log = root/'calls.log'
            programs = {
                'uname': '#!/bin/sh\necho Linux\n',
                'docker': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DEPLOY_TEST_LOG"\ncase "$*" in\n  *"up --help") echo --wait-timeout;;\n  *"up -d --wait "*) exit "${DEPLOY_UP_RESULT:-0}";;\n  *"exec -T web python scripts/setup_link.py --if-needed") echo "setup checked";;\nesac\n',
                'python3': '#!/bin/sh\ncase "$1" in\n  */check_https.py) printf "HTTPS probe %s\\n" "$2" >> "$DEPLOY_TEST_LOG"; exit "$DEPLOY_HTTPS_RESULT";;\n  *) exec "$DEPLOY_PYTHON" "$@";;\nesac\n',
            }
            for name, text in programs.items():
                path = binary/name
                path.write_text(text)
                path.chmod(0o755)
            environ = {**os.environ, 'PATH':str(binary)+os.pathsep+os.environ['PATH'], 'DEPLOY_TEST_LOG':str(log), 'DEPLOY_PYTHON':sys.executable, 'DEPLOY_HTTPS_RESULT':'0'}
            def deploy(*args):
                return subprocess.run(['bash', str(root/'scripts/deploy_stack.sh'), '--no-install', *args], env=environ, capture_output=True, text=True)
            first = deploy('--domain', 'video.taco.net', '--direct')
            self.assertEqual(first.returncode, 0, first.stdout+first.stderr)
            before = (root/'.env').read_bytes()
            self.assertEqual((root/'.env').stat().st_mode & 0o777, 0o600)
            calls = log.read_text()
            self.assertLess(calls.index('build --pull --build-arg GEOIP_MONTH='), calls.index('up -d --wait'))
            self.assertLess(calls.index('HTTPS probe'), calls.index('setup_link.py --if-needed'))
            repeat = deploy()
            self.assertEqual(repeat.returncode, 0, repeat.stdout+repeat.stderr)
            self.assertEqual((root/'.env').read_bytes(), before)
            self.assertNotIn('down', log.read_text())
            log.write_text('')
            environ['DEPLOY_HTTPS_RESULT'] = '1'
            failed = deploy()
            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn('TACO小剧场已启动', failed.stdout)
            self.assertNotIn('setup_link.py', log.read_text())
            self.assertEqual((root/'.env').read_bytes(), before)
            log.write_text('')
            environ['DEPLOY_UP_RESULT'] = '1'
            unhealthy = deploy()
            self.assertNotEqual(unhealthy.returncode, 0)
            self.assertIn('容器启动或健康检查失败', unhealthy.stderr)
            self.assertIn(f'bash {root}/deploy.sh --logs', unhealthy.stderr)
            self.assertNotIn('HTTPS probe', log.read_text())
            self.assertNotIn('setup_link.py', log.read_text())
            self.assertNotIn('down', log.read_text())
            self.assertNotIn('TACO小剧场已启动', unhealthy.stdout)
            self.assertEqual((root/'.env').read_bytes(), before)


    def test_occupied_ports_reuse_existing_cert_and_probe_alternate_https_port(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for path in ('scripts/deploy_stack.sh', 'compose.yaml', 'scripts/deploy_config.py', 'scripts/check_https.py', 'scripts/https_compat.py', 'vendor/hongguo/endpoints/duanju.py'):
                target = root/path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT/path, target)
            cert_dir = root/'deploy'/'certs'
            cert_dir.mkdir(parents=True)
            cert, key = cert_dir/'fullchain.pem', cert_dir/'privkey.pem'
            made = subprocess.run(
                ['openssl', 'req', '-x509', '-nodes', '-newkey', 'rsa:2048',
                 '-keyout', str(key), '-out', str(cert), '-days', '30',
                 '-subj', '/CN=video.taco.net'],
                capture_output=True, text=True,
            )
            self.assertEqual(made.returncode, 0, made.stderr)
            marker = root/'x-ui.db'
            marker.write_text('xui')
            binary = root/'bin'
            binary.mkdir()
            log = root/'calls.log'
            programs = {
                'uname': '#!/bin/sh\necho Linux\n',
                'docker': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DEPLOY_TEST_LOG"\ncase "$*" in\n  *"up --help") echo --wait-timeout;;\n  *"up -d --wait "*) exit 0;;\n  *"exec -T web python scripts/setup_link.py --if-needed") echo "setup checked";;\nesac\n',
                'python3': '#!/bin/sh\ncase "$1" in\n  */check_https.py) printf "HTTPS probe %s\\n" "$2" >> "$DEPLOY_TEST_LOG"; exit 0;;\n  *) exec "$DEPLOY_PYTHON" "$@";;\nesac\n',
                'curl': '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$DEPLOY_TEST_LOG"; exit 91\n',
                'iptables': '#!/bin/sh\nprintf "iptables %s\\n" "$*" >> "$DEPLOY_TEST_LOG"; exit 91\n',
                'systemctl': '#!/bin/sh\nprintf "systemctl %s\\n" "$*" >> "$DEPLOY_TEST_LOG"; exit 91\n',
            }
            for name, content in programs.items():
                path = binary/name
                path.write_text(content)
                path.chmod(0o755)
            environ = {
                **os.environ,
                'PATH': str(binary)+os.pathsep+os.environ['PATH'],
                'DEPLOY_TEST_LOG': str(log),
                'DEPLOY_PYTHON': sys.executable,
                'TACO_LISTEN_PORTS': '80,443',
                'TACO_XUI_MARKERS': str(marker),
                'TACO_CERT_SEARCH_ROOTS': str(cert_dir),
            }
            run = subprocess.run(['bash', str(root/'scripts/deploy_stack.sh'), '--no-install', '--domain', 'video.taco.net', '--direct'], env=environ, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            self.assertIn('不会停止 3x-ui', run.stdout)
            self.assertIn('https://video.taco.net:8443', run.stdout)
            calls = log.read_text()
            self.assertIn('HTTPS probe https://video.taco.net:8443', calls)
            self.assertIn('--env-file '+str(root/'deploy'/'runtime.env'), calls)
            self.assertNotIn('curl ', calls)
            self.assertNotIn('iptables', calls)
            self.assertNotIn('systemctl', calls)
            runtime = (root/'deploy'/'runtime.env').read_text()
            self.assertIn('TACO_HTTPS_PUBLISH=8443:443\n', runtime)
            self.assertIn('TACO_CADDYFILE=./deploy/Caddyfile.tls\n', runtime)
            self.assertIn('HONGGUO_PUBLIC_URL=https://video.taco.net:8443\n', runtime)

    def test_check_mode_never_starts_containers_or_changes_existing_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for path in ('scripts/deploy_stack.sh', 'compose.yaml', 'scripts/deploy_config.py', 'vendor/hongguo/endpoints/duanju.py'):
                target = root/path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT/path, target)
            env = root/'.env'
            env.write_text('DOMAIN=video.taco.net\nHONGGUO_NETWORK_MODE=direct\n')
            before = env.read_bytes()
            binary = root/'bin'
            binary.mkdir()
            log = root/'docker.log'
            docker = binary/'docker'
            docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DEPLOY_TEST_LOG"\ncase "$*" in\n  "compose version"|*"config --quiet") exit 0;;\n  *) exit 91;;\nesac\n')
            docker.chmod(0o755)
            run = subprocess.run(['bash', str(root/'scripts/deploy_stack.sh'), '--check'], capture_output=True, text=True, env={**os.environ, 'PATH':str(binary)+os.pathsep+os.environ['PATH'], 'DEPLOY_TEST_LOG':str(log)})
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            self.assertEqual(env.read_bytes(), before)
            self.assertNotIn('build', log.read_text())
            self.assertNotIn('up -d', log.read_text())


if __name__ == '__main__':
    unittest.main()
