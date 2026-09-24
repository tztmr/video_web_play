"""Stream a consistent account backup; exclude video cache and SQLite WAL files."""
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile


def backup(folder, stream):
    folder = Path(folder)
    database = folder/'accounts.sqlite3'
    if not database.is_file() or database.is_symlink():
        raise RuntimeError('账号数据库不存在，未生成备份')
    with tempfile.TemporaryDirectory(prefix='taco-backup-') as temporary:
        snapshot = Path(temporary)/'accounts.sqlite3'
        source = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=10)
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target, pages=256)
        finally:
            target.close()
            source.close()
        with tarfile.open(fileobj=stream, mode='w|') as archive:
            archive.add(snapshot, arcname='accounts.sqlite3')
            for name in ('device_pool.json', 'setup-token'):
                path = folder/name
                if path.is_file() and not path.is_symlink():
                    archive.add(path, arcname=name)


if __name__ == '__main__':
    backup(Path(os.environ.get('HONGGUO_WEB_DATA_DIR', '/data')).resolve(), sys.stdout.buffer)
