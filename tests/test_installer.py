import io
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest

from scripts.backup_data import backup

ROOT = Path(__file__).resolve().parents[1]
REPO = 'https://github.com/tztmr/video_web_play.git'


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder/'source'
        self.target = self.folder/'installed cinema'
        self.state = self.folder/'state'
        self.launcher = self.folder/'deploy.sh'
        shutil.copyfile(ROOT/'deploy.sh', self.launcher)
        self.source.mkdir()
        (self.source/'scripts').mkdir()
        (self.source/'scripts/deploy_stack.sh').write_text('''#!/usr/bin/env bash
set -e
printf 'stack:%s\\n' "$*" >> "$INSTALLER_LOG"
if [[ "${INSTALLER_FAIL:-0}" == 1 ]]; then echo backend-failed >&2; exit 17; fi
[[ -f "$(dirname "$0")/../.env" ]] || echo 'DOMAIN=video.taco.net' > "$(dirname "$0")/../.env"
''')
        for name in ('compose.yaml', 'server.py'):
            (self.source/name).touch()
        (self.source/'.gitignore').write_text('.env\nbackups/\n')
        self.binary = self.folder/'bin'
        self.binary.mkdir()
        programs = {
            'uname': '#!/bin/sh\necho Linux\n',
            'docker': '''#!/bin/sh
printf 'docker:%s\\n' "$*" >> "$INSTALLER_LOG"
test -z "$COMPOSE_PROJECT_NAME" || exit 19
case "$*" in
  *backup_data.py) test "${BACKUP_FAIL:-0}" != 1 || exit 18; cat "$BACKUP_FIXTURE";;
esac
''',
        }
        for name, content in programs.items():
            path = self.binary/name
            path.write_text(content)
            path.chmod(0o755)
        self.log = self.folder/'calls'
        self.env = {**os.environ, 'PATH': str(self.binary)+os.pathsep+os.environ['PATH'],
                    'TACO_STATE_DIR':str(self.state), 'INSTALLER_LOG':str(self.log),
                    'GIT_CONFIG_NOSYSTEM':'1', 'GIT_CONFIG_GLOBAL':os.devnull,
                    'GIT_CONFIG_COUNT':'1', 'GIT_CONFIG_KEY_0':f'url.{self.source.as_uri()}.insteadOf',
                    'GIT_CONFIG_VALUE_0':REPO, 'GIT_ALLOW_PROTOCOL':'file'}
        self.git(self.source, 'init', '-b', 'main')
        self.commit('initial fixture')

    def git(self, folder, *args):
        return subprocess.run(['git', '-C', str(folder), *args], env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git(self.source, 'add', '.')
        self.git(self.source, '-c', 'user.name=Installer Test', '-c', 'user.email=test@invalid.local', 'commit', '-m', message)

    def run_installer(self, *args, input=''):
        return subprocess.run(['bash', str(self.launcher), *args], env=self.env, input=input, text=True, capture_output=True, timeout=20)

    def install(self):
        result = self.run_installer('--directory', str(self.target), '--install', '--domain', 'video.taco.net', '--direct', '--no-install')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return result

    def test_standalone_clone_saved_directory_update_and_dirty_tree(self):
        self.install()
        # Preserve the installer's private host checkout; Docker must normalize
        # its own runtime copies instead of exposing config/backup directories.
        self.assertEqual((self.target/'server.py').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.target/'scripts').stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.git(self.target, 'config', '--get', 'remote.origin.url'), REPO)
        self.assertEqual((self.state/'install-dir').read_text().strip(), str(self.target))
        self.assertEqual((self.state/'install-dir').stat().st_mode & 0o777, 0o600)
        self.assertIn('stack:--domain video.taco.net --direct --no-install', self.log.read_text())
        before = (self.target/'.env').read_bytes()
        (self.source/'new-version').write_text('updated')
        self.commit('update fixture')
        update = self.run_installer('--update')
        self.assertEqual(update.returncode, 0, update.stdout+update.stderr)
        self.assertEqual(self.git(self.target, 'rev-parse', 'HEAD'), self.git(self.source, 'rev-parse', 'HEAD'))
        self.assertEqual((self.target/'.env').read_bytes(), before)
        (self.target/'server.py').write_text('local changes')
        calls = self.log.read_text()
        refused = self.run_installer('--update')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('未提交修改', refused.stderr)
        self.assertEqual(self.log.read_text(), calls)

    def test_menu_keeps_selected_install_directory_and_continues_after_failure(self):
        self.env['INSTALLER_FAIL'] = '1'
        result = self.run_installer(input=f'1\n{self.target}\n4\n0\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('backend-failed', result.stderr)
        self.assertNotIn('安装完成', result.stdout)
        self.assertIn(str(self.target), result.stdout)
        self.assertNotIn('docker:compose --project-directory', self.log.read_text())
        self.env.pop('INSTALLER_FAIL')
        self.assertEqual(self.run_installer('--install').returncode, 0)
        status = self.run_installer('--status')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn(f'--project-directory {self.target}', self.log.read_text())

    def test_wrong_repository_and_nonempty_directory_are_not_overwritten(self):
        self.target.mkdir()
        (self.target/'important').write_text('keep')
        result = self.run_installer('--directory', str(self.target), '--install')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.target/'important').read_text(), 'keep')
        (self.target/'important').unlink()
        self.install()
        self.git(self.target, 'remote', 'set-url', 'origin', 'https://github.com/other/project.git')
        result = self.run_installer('--update')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('origin', result.stderr)

    def test_uninstall_scopes_project_and_keeps_configuration_and_volumes(self):
        self.install()
        before = (self.target/'.env').read_bytes()
        cancelled = self.run_installer('--uninstall', input='no\n')
        self.assertEqual(cancelled.returncode, 0)
        self.assertNotIn('down', self.log.read_text())
        result = self.run_installer('--uninstall', input='UNINSTALL\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('down --remove-orphans', self.log.read_text())
        self.assertNotIn('down -v', self.log.read_text())
        self.assertEqual((self.target/'.env').read_bytes(), before)

    def test_backup_has_private_permissions_and_cleans_partial_failure(self):
        self.install()
        fixture = self.folder/'fixture.tar'
        fixture.write_bytes(b'tar fixture')
        self.env['BACKUP_FIXTURE'] = str(fixture)
        result = self.run_installer('--backup')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        directory = self.target/'backups'
        archive, = directory.glob('*.tar.gz')
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        with tarfile.open(archive) as data:
            self.assertEqual(set(data.getnames()), {'data.tar', 'site.env', 'REVISION'})
            self.assertEqual(data.extractfile('data.tar').read(), fixture.read_bytes())
        self.env['BACKUP_FAIL'] = '1'
        failed = self.run_installer('--backup')
        self.assertNotEqual(failed.returncode, 0)
        self.assertNotIn('备份完成', failed.stdout)
        self.assertEqual(list(directory.iterdir()), [archive])

    def test_reconfigure_and_refresh_dispatch_to_existing_checkout(self):
        self.install()
        result = self.run_installer('--configure', '--domain', 'next.taco.net', '--direct')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('stack:--domain next.taco.net --direct --reconfigure', self.log.read_text())
        self.assertEqual(self.run_installer('--refresh-geoip').returncode, 0)


class BackupTests(unittest.TestCase):
    def test_online_backup_contains_committed_wal_data_and_excludes_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            connection = sqlite3.connect(folder/'accounts.sqlite3')
            self.addCleanup(connection.close)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE data (value TEXT)')
            connection.execute("INSERT INTO data VALUES ('latest')")
            connection.commit()
            (folder/'device_pool.json').write_text('{}')
            (folder/'setup-token').symlink_to(folder/'device_pool.json')
            (folder/'video.mp4').write_bytes(b'unrelated cache')
            stream = io.BytesIO()
            backup(folder, stream)
            stream.seek(0)
            with tarfile.open(fileobj=stream) as archive:
                self.assertEqual(set(archive.getnames()), {'accounts.sqlite3', 'device_pool.json'})
                snapshot = folder/'restored.sqlite3'
                snapshot.write_bytes(archive.extractfile('accounts.sqlite3').read())
            restored = sqlite3.connect(snapshot)
            try:
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(restored.execute('SELECT value FROM data').fetchone()[0], 'latest')
            finally:
                restored.close()
