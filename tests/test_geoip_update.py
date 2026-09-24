import gzip
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from scripts.update_geoip import install_database


class GeoipUpdateTests(unittest.TestCase):
    def test_new_month_404_falls_back_and_publishes_validated_database(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)/'country.mmdb'
            missing = urllib.error.HTTPError('https://download.db-ip.com', 404, 'missing', {}, None)
            database = MagicMock()
            database.__enter__.return_value = database
            database.metadata.return_value.ip_version = 6
            database.get.return_value = {'country': {'iso_code':'CN'}}
            compressed = io.BytesIO(gzip.compress(b'validated fixture'))
            with patch('scripts.update_geoip.urllib.request.urlopen', side_effect=[missing, compressed]) as download, patch('scripts.update_geoip.maxminddb.open_database', return_value=database):
                metadata = install_database(destination, '2026-01')
            self.assertEqual(metadata['edition'], '2025-12')
            self.assertEqual(destination.read_bytes(), b'validated fixture')
            self.assertTrue(destination.with_suffix('.json').exists())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
            self.assertIn('TACO-Cinema', download.call_args.args[0].get_header('User-agent'))

    def test_failed_download_or_invalid_database_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)/'country.mmdb'
            destination.write_bytes(b'old working database')
            for failure in ('forbidden', 'bad-gzip', 'bad-database'):
                result = io.BytesIO(b'invalid gzip' if failure == 'bad-gzip' else gzip.compress(b'invalid database'))
                error = urllib.error.HTTPError('https://download.db-ip.com', 403, 'forbidden', {}, None) if failure == 'forbidden' else None
                with self.subTest(failure=failure), patch('scripts.update_geoip.urllib.request.urlopen', return_value=result, side_effect=error) as download, patch('scripts.update_geoip.maxminddb.open_database', side_effect=ValueError('invalid database')):
                    with self.assertRaises((OSError, ValueError)):
                        install_database(destination, '2026-09')
                self.assertEqual(download.call_count, 1)
                self.assertEqual(destination.read_bytes(), b'old working database')
                self.assertEqual(list(Path(folder).iterdir()), [destination])
