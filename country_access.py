"""Offline country check for every web request; never trust client-supplied headers."""
import ipaddress
from pathlib import Path

import maxminddb
from maxminddb.errors import InvalidDatabaseError


class CountryAccess:
    def __init__(self, database):
        self.reader = None
        try:
            self.reader = maxminddb.open_database(str(Path(database)))
        except (OSError, ValueError, InvalidDatabaseError):
            pass

    def status(self, host):
        try:
            address = ipaddress.ip_address(host or '')
            address = getattr(address, 'ipv4_mapped', None) or address
        except ValueError:
            return 503
        # Local viewing and in-container health checks stay available.
        if address.is_loopback:
            return 200
        if not self.reader:
            return 503
        try:
            record = self.reader.get(str(address)) or {}
            country = record.get('country', {}).get('iso_code')
        except (OSError, ValueError, AttributeError, InvalidDatabaseError):
            return 503
        if not isinstance(country, str) or len(country) != 2 or not country.isascii() or not country.isalpha() or not country.isupper() or country == 'ZZ':
            return 503
        return 403 if country == 'CN' else 200

    def close(self):
        if self.reader:
            self.reader.close()
            self.reader = None
