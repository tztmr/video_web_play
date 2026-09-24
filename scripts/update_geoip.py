"""Download the monthly DB-IP Lite country database over verified HTTPS."""
import argparse
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request

import maxminddb


def install_database(destination, month=None):
    current = month or date.today().strftime('%Y-%m')
    if not re.fullmatch(r'\d{4}-\d{2}', current):
        raise ValueError('地址库月份必须为 YYYY-MM')
    first = datetime.strptime(current, '%Y-%m').date()
    editions = [current, (first-timedelta(days=1)).strftime('%Y-%m')]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.geoip-', dir=destination.parent) as folder:
        compressed, output = Path(folder)/'country.gz', Path(folder)/'country.mmdb'
        for edition in editions:
            url = f'https://download.db-ip.com/free/dbip-country-lite-{edition}.mmdb.gz'
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'TACO-Cinema/1.0 (+https://github.com/tztmr/video_web_play)'})
                with urllib.request.urlopen(request, timeout=45) as response, compressed.open('wb') as stream:
                    total = 0
                    while chunk := response.read(256*1024):
                        total += len(chunk)
                        if total > 64*1024*1024:
                            raise ValueError('地址库压缩文件超出大小限制')
                        stream.write(chunk)
            except urllib.error.HTTPError as error:
                if error.code == 404 and edition != editions[-1]:
                    continue  # A new month's database may not have been published yet.
                raise
            with gzip.open(compressed, 'rb') as source, output.open('wb') as target:
                total = 0
                while chunk := source.read(256*1024):
                    total += len(chunk)
                    if total > 128*1024*1024:
                        raise ValueError('地址库解压后超出大小限制')
                    target.write(chunk)
            with maxminddb.open_database(str(output)) as reader:
                if reader.metadata().ip_version != 6 or (reader.get('223.5.5.5') or {}).get('country', {}).get('iso_code') != 'CN':
                    raise ValueError('地址库校验失败，需要含 IPv4/IPv6 的国家数据库')
            checksum = hashlib.sha256(output.read_bytes()).hexdigest()
            output.chmod(0o644)
            output.replace(destination)
            metadata = {'provider':'DB-IP Lite', 'edition':edition, 'sha256':checksum,
                        'downloaded_at':datetime.now(timezone.utc).isoformat(), 'url':url}
            destination.with_suffix('.json').write_text(json.dumps(metadata, indent=2)+'\n')
            print(f'大陆 IP 地址库已更新：DB-IP Lite {edition}')
            return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, default=Path(__file__).resolve().parents[1]/'.data/geoip-country.mmdb')
    parser.add_argument('--month', default=None)
    args = parser.parse_args()
    install_database(args.destination, args.month or None)
