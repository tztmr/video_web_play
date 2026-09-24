"""SQLite accounts, revocable sessions, and scoped expiring share links."""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
from contextlib import contextmanager


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt}${key.hex()}"


def password_matches(password, encoded):
    try:
        _, salt, _ = encoded.split('$')
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = password_hash('not-a-real-account-password', '00' * 16)


class Accounts:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'accounts.sqlite3'
        self.setup_file = self.folder / 'setup-token'
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE NOT NULL,
                    password TEXT NOT NULL, role TEXT NOT NULL, disabled INTEGER DEFAULT 0,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS shares (
                    id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, owner_id INTEGER NOT NULL,
                    book_id TEXT NOT NULL, item TEXT NOT NULL, episodes TEXT NOT NULL,
                    require_login INTEGER NOT NULL, expires REAL NOT NULL, revoked INTEGER DEFAULT 0,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
            ''')
        self.path.chmod(0o600)
        if self.needs_setup() and not self.setup_file.exists():
            self.setup_file.write_text(secrets.token_urlsafe(32))
            self.setup_file.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def needs_setup(self):
        with self.db() as db:
            return db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is None

    def setup(self, token, username, encoded):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise ValueError('网站已完成初始化，请登录')
            if not self.setup_file.exists() or not hmac.compare_digest(token, self.setup_file.read_text().strip()):
                raise ValueError('初始化链接无效')
            db.execute('INSERT INTO users(username,password,role,created) VALUES (?,?,?,?)', (username, encoded, 'admin', time.time()))
        self.setup_file.unlink(missing_ok=True)

    def create_user(self, username, encoded, role='viewer'):
        with self.db() as db:
            cursor = db.execute('INSERT INTO users(username,password,role,created) VALUES (?,?,?,?)', (username, encoded, role, time.time()))
            return cursor.lastrowid

    def authenticate(self, username, password):
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        valid = password_matches(password, row['password'] if row else DUMMY_HASH)
        return self.public_user(row) if row and valid and not row['disabled'] else None

    @staticmethod
    def public_user(row):
        return {key: row[key] for key in ('id', 'username', 'role', 'disabled')}

    def session(self, token):
        if not token or len(token) > 128:
            return None
        with self.db() as db:
            row = db.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.disabled=0', (digest(token), time.time())).fetchone()
        return self.public_user(row) if row else None

    def login(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (digest(token), user_id, time.time()+7*86400))
        return token

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token or ''),))

    def limit(self, key, maximum=12, seconds=300):
        now = time.time()
        with self.db() as db:
            db.execute('DELETE FROM attempts WHERE expires<?', (now,))
            db.execute('INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (digest(key), now+seconds))
            row = db.execute('SELECT count FROM attempts WHERE key=?', (digest(key),)).fetchone()
            return row['count'] <= maximum

    def users(self):
        with self.db() as db:
            return [self.public_user(row) for row in db.execute('SELECT * FROM users ORDER BY id')]

    def update_user(self, user_id, disabled=None, encoded=None):
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not row:
                raise ValueError('账号不存在')
            if disabled is not None:
                if row['role'] == 'admin':
                    raise ValueError('不能停用管理员账号')
                db.execute('UPDATE users SET disabled=? WHERE id=?', (int(disabled), user_id))
            if encoded:
                db.execute('UPDATE users SET password=? WHERE id=?', (encoded, user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))

    def create_share(self, owner_id, item, episodes, require_login, hours):
        sid, token = secrets.token_hex(12), secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('INSERT INTO shares VALUES (?,?,?,?,?,?,?,?,0,?)', (sid, token, owner_id, item['book_id'], json.dumps(item), json.dumps(episodes), int(require_login), time.time()+hours*3600 if hours else 0, time.time()))
        return self.get_share(token)

    def get_share(self, token):
        if not token or len(token) > 128:
            return None
        with self.db() as db:
            row = db.execute('SELECT s.* FROM shares s JOIN users u ON u.id=s.owner_id WHERE s.token=? AND s.revoked=0 AND (s.expires=0 OR s.expires>?) AND u.disabled=0', (token, time.time())).fetchone()
        if not row:
            return None
        result = dict(row)
        result['item'], result['episodes'] = json.loads(row['item']), json.loads(row['episodes'])
        return result

    def shares(self):
        with self.db() as db:
            return [dict(row) | {'item': json.loads(row['item'])} for row in db.execute('SELECT id,token,item,require_login,expires,revoked,created FROM shares ORDER BY created DESC LIMIT 200')]

    def revoke(self, sid):
        with self.db() as db:
            return db.execute('UPDATE shares SET revoked=1 WHERE id=?', (sid,)).rowcount

    def report(self, user_id, payload):
        with self.db() as db:
            db.execute('INSERT INTO reports(user_id,payload,created) VALUES (?,?,?)', (user_id, json.dumps(payload), time.time()))
            db.execute('DELETE FROM reports WHERE id NOT IN (SELECT id FROM reports ORDER BY id DESC LIMIT 2000)')

    def reports(self):
        with self.db() as db:
            return [dict(row) | {'payload': json.loads(row['payload'])} for row in db.execute('SELECT r.*,u.username FROM reports r JOIN users u ON u.id=r.user_id ORDER BY r.id DESC LIMIT 200')]
