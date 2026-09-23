#!/usr/bin/env python3
"""Edgario: local-first encrypted contacts application, optional account server.

Python 3.10+, standard library only. Run `python server.py --open`.
The default listener is loopback-only. Public deployment is intentionally gated
behind EDGARIO_ORIGIN=https://... and an HTTPS reverse proxy. This is an MVP,
not an audited production identity platform. See README_RU.md.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('EDGARIO_DATA_DIR', str(ROOT / 'data'))).resolve()
DB_PATH = DATA_DIR / 'edgario.sqlite3'
ITERATIONS = 600_000
MAX_REQUEST = 92 * 1024 * 1024
MAX_CIPHER_BYTES = 64 * 1024 * 1024 + 32
SESSION_TTL = 12 * 60 * 60
ORIGIN = ''
HOST_HEADER = ''
SECURE_COOKIE = False
# Registration is opt-in for a public deployment; enabled locally by default.
ALLOW_REGISTRATION = True
RATE_LOCK = threading.Lock()
RATE_BUCKETS: dict[str, list[float]] = {}
AUTH_SEMAPHORE = threading.BoundedSemaphore(3)


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def setup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS accounts (
            email TEXT PRIMARY KEY,
            password_salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            salt TEXT NOT NULL,
            iv TEXT NOT NULL,
            ciphertext TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            email TEXT NOT NULL REFERENCES accounts(email) ON DELETE CASCADE,
            expires INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sessions_email ON sessions(email);
        ''')
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


def get_password(value: object, new: bool = False) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= 256):
        raise APIError(400, 'Некорректный пароль.')
    if new and len(value) < 10:
        raise APIError(400, 'Пароль должен содержать минимум 10 символов.')
    return value


def password_hash(password: str, salt: bytes) -> bytes:
    # Bounded parallelism prevents a burst of scrypt calls exhausting memory.
    if not AUTH_SEMAPHORE.acquire(blocking=False):
        raise APIError(429, 'Слишком много попыток входа. Повтори чуть позже.')
    try:
        return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768,
                              r=8, p=3, dklen=32, maxmem=128 * 1024 * 1024)
    finally:
        AUTH_SEMAPHORE.release()


def rate_limit(key: str, limit: int, window: int = 600) -> None:
    now = time.time()
    with RATE_LOCK:
        if len(RATE_BUCKETS) > 10000:
            for oldkey in list(RATE_BUCKETS):
                if not RATE_BUCKETS[oldkey] or RATE_BUCKETS[oldkey][-1] < now - 3600:
                    del RATE_BUCKETS[oldkey]
        hits = [t for t in RATE_BUCKETS.get(key, []) if now - t < window]
        if len(hits) >= limit:
            raise APIError(429, 'Слишком много попыток. Повтори через несколько минут.')
        hits.append(now)
        RATE_BUCKETS[key] = hits


def valid_email(value: object) -> str:
    """Legacy database field 'email' also accepts case-insensitive usernames."""
    if not isinstance(value, str):
        raise APIError(400, 'Укажи логин или email.')
    login = value.strip().lower()
    if len(login) > 254 or not (
        re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,39}', login)
        or re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', login)
    ):
        raise APIError(400, 'Логин: 3–40 латинских букв, цифр, точек, дефисов или подчёркиваний. Можно использовать email.')
    return login


def decode64(value: object, exact: int | None = None, maximum: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise APIError(400, 'Некорректный формат зашифрованных данных.')
    if maximum and len(value) > (maximum * 4 // 3) + 8:
        raise APIError(413, 'Хранилище слишком большое.')
    if exact and len(value) > 100:
        raise APIError(400, 'Некорректный параметр шифрования.')
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise APIError(400, 'Некорректная кодировка данных.') from None
    if exact is not None and len(decoded) != exact:
        raise APIError(400, 'Некорректный параметр шифрования.')
    if maximum and not (16 <= len(decoded) <= maximum):
        raise APIError(413, 'Хранилище слишком большое или повреждено.')
    return decoded


def validate_record(record: object) -> dict:
    if not isinstance(record, dict) or record.get('iterations') != ITERATIONS:
        raise APIError(400, 'Неподдерживаемый формат хранилища.')
    decode64(record.get('salt'), exact=16)
    decode64(record.get('iv'), exact=12)
    decode64(record.get('data'), maximum=MAX_CIPHER_BYTES)
    return record


def pack_record(row: sqlite3.Row) -> dict:
    return dict(email=row['email'], salt=row['salt'], iv=row['iv'],
                data=row['ciphertext'], iterations=ITERATIONS, version=row['version'])


class Handler(BaseHTTPRequestHandler):
    server_version = 'Edgario/1.0'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt: str, *args: object) -> None:
        # Never log emails, request bodies, passwords, or contact contents.
        if os.environ.get('EDGARIO_LOG_REQUESTS') == '1':
            print('[request]', self.command, self.path.split('?')[0], flush=True)

    def headers_common(self) -> None:
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        # Script is inline to keep the downloadable HTML self-contained.
        # Hash-based CSP permits only this exact application script.
        self.send_header('Content-Security-Policy', self.server.csp)
        if SECURE_COOKIE:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')

    def send_json(self, status: int, value: dict, cookie: str | None = None) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
        self.send_response(status)
        self.headers_common()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, head: bool = False) -> None:
        body = self.server.html
        self.send_response(200)
        self.headers_common()
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def ensure_host(self) -> None:
        # Blocks hostile Host headers / DNS-rebinding access to local accounts.
        if self.headers.get('Host', '').lower() != HOST_HEADER:
            raise APIError(403, 'Недопустимый адрес сервера.')

    def read_body(self) -> dict:
        self.ensure_host()
        origin = self.headers.get('Origin')
        if origin is not None and origin.rstrip('/') != ORIGIN:
            raise APIError(403, 'Запрос с другого сайта отклонён.')
        if self.headers.get('Sec-Fetch-Site', '').lower() == 'cross-site':
            raise APIError(403, 'Запрос с другого сайта отклонён.')
        if self.headers.get('X-Edgario-Request') != '1':
            raise APIError(403, 'Некорректный запрос.')
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise APIError(415, 'Ожидается JSON.')
        if self.headers.get('Transfer-Encoding'):
            raise APIError(400, 'Потоковый запрос не поддерживается.')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise APIError(400, 'Некорректная длина запроса.') from None
        if not 2 <= length <= MAX_REQUEST:
            raise APIError(413, 'Запрос слишком большой или пустой.')
        data = self.rfile.read(length)
        if len(data) != length:
            raise APIError(400, 'Запрос получен не полностью.')
        try:
            value = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise APIError(400, 'Некорректный JSON.') from None
        if not isinstance(value, dict):
            raise APIError(400, 'Ожидается объект JSON.')
        return value

    @staticmethod
    def cookie(token: str, ttl: int = SESSION_TTL) -> str:
        return (f'edgario_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={ttl}'
                + ('; Secure' if SECURE_COOKIE else ''))

    def token_hash(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            token = cookie['edgario_session'].value
        except (KeyError, ValueError):
            return ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return ''
        return hashlib.sha256(token.encode()).hexdigest()

    def session(self, db: sqlite3.Connection) -> sqlite3.Row:
        row = db.execute('''SELECT a.*, s.token_hash FROM sessions s JOIN accounts a
                          ON a.email=s.email WHERE s.token_hash=? AND s.expires>?''',
                         (self.token_hash(), int(time.time()))).fetchone()
        if not row:
            raise APIError(401, 'Сессия истекла. Выйди и войди в аккаунт снова.')
        return row

    def new_session(self, db: sqlite3.Connection, email: str) -> str:
        token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires<=?', (int(time.time()),))
        db.execute('DELETE FROM sessions WHERE token_hash=?', (self.token_hash(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), email, int(time.time())+SESSION_TTL))
        # Bound retained sessions per account without preventing multiple devices.
        db.execute('''DELETE FROM sessions WHERE email=? AND token_hash NOT IN
                   (SELECT token_hash FROM sessions WHERE email=? ORDER BY expires DESC LIMIT 10)''',
                   (email, email))
        return self.cookie(token)

    def route_get(self, head: bool = False) -> None:
        try:
            path = urlsplit(self.path).path
            if path == '/healthz':
                # Railway health probes use a separate Host header. No user data.
                if head:
                    self.send_response(200)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                else:
                    self.send_json(200, {'ok': True})
                return
            self.ensure_host()
            if path in ('/', '/index.html'):
                self.send_html(head=head)
            elif path == '/favicon.ico':
                self.send_response(204)
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                self.send_json(404, {'error': 'Страница не найдена.'})
        except APIError as exc:
            self.send_json(exc.status, {'error': exc.message})

    def do_GET(self) -> None:
        self.route_get()

    def do_HEAD(self) -> None:
        self.route_get(head=True)

    def do_POST(self) -> None:
        self.route_write()

    def do_PUT(self) -> None:
        self.route_write()

    def do_DELETE(self) -> None:
        self.route_write()

    def route_write(self) -> None:
        try:
            body = self.read_body()
            path = urlsplit(self.path).path
            if path in ('/api/register', '/api/login') and self.command == 'POST':
                email = valid_email(body.get('email'))
                rate_limit('ip:'+self.client_address[0], 35)
                rate_limit('account:'+email, 15)
                password = get_password(body.get('password'), new=path.endswith('register'))
                with connect() as db:
                    if path.endswith('register'):
                        if not ALLOW_REGISTRATION:
                            raise APIError(403, 'Регистрация отключена владельцем сервера.')
                        record = validate_record(body)
                        if db.execute('SELECT 1 FROM accounts WHERE email=?', (email,)).fetchone():
                            raise APIError(409, 'Этот логин уже зарегистрирован. Нажми «Войти».')
                        salt = secrets.token_bytes(16)
                        hashed = password_hash(password, salt)
                        try:
                            db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,1,?)',
                                       (email, salt, hashed, record['salt'], record['iv'], record['data'], int(time.time())))
                        except sqlite3.IntegrityError:
                            raise APIError(409, 'Этот логин уже зарегистрирован.') from None
                        cookie = self.new_session(db, email)
                        db.commit()
                        self.send_json(201, {'ok': True}, cookie)
                    else:
                        row = db.execute('SELECT * FROM accounts WHERE email=?', (email,)).fetchone()
                        hashed = password_hash(password, row['password_salt'] if row else bytes(16))
                        if row is None or not hmac.compare_digest(hashed, row['password_hash']):
                            raise APIError(401, 'Неверный логин или пароль.')
                        cookie = self.new_session(db, email)
                        db.commit()
                        self.send_json(200, {'record': pack_record(row)}, cookie)
                return
            with connect() as db:
                if path == '/api/logout' and self.command == 'POST':
                    db.execute('DELETE FROM sessions WHERE token_hash=?', (self.token_hash(),))
                    db.commit()
                    self.send_json(200, {'ok': True}, self.cookie('', 0))
                    return
                row = self.session(db)
                if path == '/api/vault' and self.command == 'PUT':
                    record = validate_record(body)
                    expected = body.get('expectedVersion')
                    if type(expected) is not int or expected < 1:
                        raise APIError(400, 'Некорректная версия данных.')
                    if record.get('email') != row['email'] or record['salt'] != row['salt']:
                        raise APIError(400, 'Некорректные параметры аккаунта.')
                    cursor = db.execute('''UPDATE accounts SET iv=?,ciphertext=?,version=version+1
                                       WHERE email=? AND version=?''',
                                        (record['iv'], record['data'], row['email'], expected))
                    if cursor.rowcount != 1:
                        raise APIError(409, 'Данные изменены на другом устройстве. Выйди и войди снова.')
                    db.commit()
                    self.send_json(200, {'ok': True, 'version': expected+1})
                elif path == '/api/password' and self.command == 'PUT':
                    rate_limit('pw:'+row['email'], 8)
                    old = get_password(body.get('oldPassword'))
                    new = get_password(body.get('newPassword'), new=True)
                    if not hmac.compare_digest(password_hash(old, row['password_salt']), row['password_hash']):
                        raise APIError(401, 'Текущий пароль неверный.')
                    record = validate_record(body.get('record'))
                    expected = body.get('expectedVersion')
                    if type(expected) is not int or record.get('email') != row['email']:
                        raise APIError(400, 'Некорректная версия данных.')
                    salt = secrets.token_bytes(16)
                    hashed = password_hash(new, salt)
                    cursor = db.execute('''UPDATE accounts SET password_salt=?,password_hash=?,salt=?,iv=?,
                                       ciphertext=?,version=version+1 WHERE email=? AND version=?''',
                                       (salt, hashed, record['salt'], record['iv'], record['data'], row['email'], expected))
                    if cursor.rowcount != 1:
                        raise APIError(409, 'Аккаунт изменён в другой вкладке. Войди снова.')
                    db.execute('DELETE FROM sessions WHERE email=? AND token_hash<>?',
                               (row['email'], self.token_hash()))
                    db.commit()
                    self.send_json(200, {'ok': True})
                elif path == '/api/account' and self.command == 'DELETE':
                    rate_limit('pw:'+row['email'], 8)
                    password = get_password(body.get('password'))
                    if not hmac.compare_digest(password_hash(password, row['password_salt']), row['password_hash']):
                        raise APIError(401, 'Неверный пароль. Ничего не удалено.')
                    db.execute('DELETE FROM accounts WHERE email=?', (row['email'],))
                    db.commit()
                    self.send_json(200, {'ok': True}, self.cookie('', 0))
                else:
                    raise APIError(404, 'Маршрут не найден.')
        except APIError as exc:
            self.close_connection = True
            self.send_json(exc.status, {'error': exc.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception as exc:
            self.close_connection = True
            print('[error]', type(exc).__name__, flush=True)
            self.send_json(500, {'error': 'Ошибка сервера. Данные не сохранены. Повтори попытку.'})


def seed_accounts_once() -> None:
    """Import only encrypted bootstrap records, once per database.

    Deleting an account never recreates it on later server restarts.
    The file contains no plaintext passwords and is never served over HTTP.
    """
    path = ROOT / 'bootstrap_accounts.json'
    if not path.exists():
        return
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        if db.execute("SELECT 1 FROM app_meta WHERE key='bootstrap-v1'").fetchone():
            return
        items = json.loads(path.read_text(encoding='utf-8'))
        for item in items:
            login = valid_email(item['email'])
            record = validate_record(item)
            ps = decode64(item['password_salt'], exact=16)
            ph = decode64(item['password_hash'], exact=32)
            db.execute('INSERT OR IGNORE INTO accounts VALUES(?,?,?,?,?,?,1,?)',
                       (login, ps, ph, record['salt'], record['iv'], record['data'], int(time.time())))
        db.execute("INSERT INTO app_meta VALUES('bootstrap-v1', 'done')")


def main() -> None:
    global ORIGIN, HOST_HEADER, SECURE_COOKIE, ALLOW_REGISTRATION
    parser = argparse.ArgumentParser(description='Эдгарио — личное пространство')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8765')))
    parser.add_argument('--open', action='store_true', help='Open the site in the default browser')
    args = parser.parse_args()
    railway_domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '').strip()
    default_origin = f'https://{railway_domain}' if railway_domain else f'http://127.0.0.1:{args.port}'
    ORIGIN = os.environ.get('EDGARIO_ORIGIN', default_origin).rstrip('/')
    parsed = urlsplit(ORIGIN)
    public = parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or args.host not in ('127.0.0.1', 'localhost', '::1')
    if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path
            or parsed.query or parsed.fragment or parsed.username or parsed.password):
        parser.error('EDGARIO_ORIGIN must be a complete origin, without a path or credentials.')
    if public and parsed.scheme != 'https':
        parser.error('Public listeners require EDGARIO_ORIGIN=https://your-domain and an HTTPS reverse proxy.')
    SECURE_COOKIE = parsed.scheme == 'https'
    HOST_HEADER = parsed.netloc.lower()
    ALLOW_REGISTRATION = os.environ.get('EDGARIO_ALLOW_REGISTRATION', '0' if public else '1') == '1'
    setup()
    seed_accounts_once()
    html = (ROOT / 'index.html').read_text(encoding='utf-8').replace('const SERVER_MODE = false;', 'const SERVER_MODE = true;')
    html = html.replace('const REGISTRATION_OPEN = true;', 'const REGISTRATION_OPEN = ' + ('true' if ALLOW_REGISTRATION else 'false') + ';')
    script = re.search(r'<script>(.*?)</script>', html, flags=re.S).group(1)
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    csp = ("default-src 'none'; script-src 'sha256-" + script_hash + "'; style-src 'unsafe-inline'; "
           "img-src data: blob:; connect-src 'self'; font-src 'none'; object-src 'none'; "
           "base-uri 'none'; frame-ancestors 'none'; form-action 'self';")
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    httpd.html = html.encode('utf-8')
    httpd.csp = csp
    print(f'Edgario: {ORIGIN}', flush=True)
    print(f'Data: {DB_PATH}', flush=True)
    print('Keep this window open. Press Ctrl+C to stop.', flush=True)
    if args.open:
        threading.Timer(0.7, lambda: webbrowser.open(ORIGIN)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.', flush=True)
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
