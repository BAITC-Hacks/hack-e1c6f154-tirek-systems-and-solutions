"""Private workspaces and cookie authentication without a third-party identity service."""
from hashlib import pbkdf2_hmac, sha256
from contextlib import nullcontext
import hmac
import os
import re
import secrets
import sqlite3
import time
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse

from .contracts import DomainError
from .demo import demo_dataset, make_demo, now
from .storage import workspace_scope


COOKIE = 'tirek_session'
ITERATIONS = 600_000
SESSION_SECONDS = 12 * 60 * 60
DEFAULT_ORIGINS = ('http://127.0.0.1:5173', 'http://localhost:5173',
                   'http://127.0.0.1:8000', 'http://localhost:8000')


def allowed_origins():
    return [part.strip().rstrip('/') for part in os.getenv(
        'TIREK_ALLOWED_ORIGINS', ','.join(DEFAULT_ORIGINS)).split(',') if part.strip()]


def password_hash(password):
    salt = secrets.token_hex(16)
    encoded = pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${encoded}'


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        actual = pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def _text(payload, key, minimum, maximum):
    value = payload.get(key)
    if not isinstance(value, str):
        raise DomainError('INVALID_PARAMETERS', f'Заполните поле {key}.')
    value = value if 'password' in key else value.strip()
    if not minimum <= len(value) <= maximum or '\x00' in value:
        raise DomainError('INVALID_PARAMETERS', f'Поле {key}: от {minimum} до {maximum} символов.')
    return value


def _email(payload):
    value = _text(payload, 'email', 3, 254).casefold()
    if not re.fullmatch(r'[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+', value):
        raise DomainError('INVALID_PARAMETERS', 'Введите корректный адрес электронной почты.')
    return value


class Authentication:
    def __init__(self, store):
        self.store = store
        self.enabled = os.getenv('TIREK_AUTH_DISABLED') != '1'
        self.secure_cookie = os.getenv('TIREK_COOKIE_SECURE') == '1'
        self.origins = allowed_origins()
        # The dummy comparison takes the same expensive path for an unknown email.
        self.dummy_hash = password_hash(secrets.token_urlsafe(32))
        with self.store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL, workspace_id TEXT UNIQUE NOT NULL,
                    workspace_name TEXT NOT NULL, password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL, expires_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES auth_users(id));
                CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id);
                CREATE TABLE IF NOT EXISTS auth_rate_limits (
                    bucket TEXT PRIMARY KEY, started_at REAL NOT NULL, attempts INTEGER NOT NULL);
            ''')

    def check_origin(self, request):
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') not in self.origins:
            raise DomainError('UNTRUSTED_ORIGIN', 'Этот адрес сайта не разрешён для входа.', 403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise DomainError('UNTRUSTED_ORIGIN', 'Запрос с другого сайта отклонён.', 403)

    def throttle(self, request, action, identity='', limit=10, period=900, *, units=1, per_identity=False, db=None):
        address = request.client.host if request.client else 'unknown'
        principal = identity if per_identity else f'{address}:{identity}'
        key = sha256(f'{action}:{principal}'.encode()).hexdigest()
        blocked = False
        with (self.store.transaction() if db is None else nullcontext(db)) as connection:
            instant = time.time()
            connection.execute('DELETE FROM auth_rate_limits WHERE started_at < ?', (instant - 86400,))
            row = connection.execute('SELECT started_at,attempts FROM auth_rate_limits WHERE bucket=?', (key,)).fetchone()
            if not row or row[0] + period <= instant:
                connection.execute('INSERT INTO auth_rate_limits VALUES (?,?,?) ON CONFLICT(bucket) '
                                   'DO UPDATE SET started_at=excluded.started_at,attempts=excluded.attempts',
                                   (key, instant, units))
            elif row[1] + units > limit:
                blocked = True
            else:
                connection.execute('UPDATE auth_rate_limits SET attempts=attempts+? WHERE bucket=?', (units, key))
        if blocked:
            raise DomainError('RATE_LIMITED', 'Слишком много попыток. Повторите позже.', 429)

    @staticmethod
    def public_user(user):
        return {key: user[key] for key in ('id', 'name', 'email', 'workspace_name')}

    def session(self, request):
        token = request.cookies.get(COOKIE, '')
        if len(token) < 32 or len(token) > 256:
            raise DomainError('AUTH_REQUIRED', 'Войдите в аккаунт, чтобы продолжить.', 401)
        hashed = sha256(token.encode()).hexdigest()
        with self.store.transaction() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT u.*, s.csrf_token, s.expires_at FROM auth_sessions s '
                             'JOIN auth_users u ON u.id=s.user_id WHERE s.token_hash=?', (hashed,)).fetchone()
            if row is None or row['expires_at'] <= time.time():
                raise DomainError('AUTH_REQUIRED', 'Сессия истекла. Войдите снова.', 401)
            return dict(row)

    def issue_session(self, user, request, status=200):
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            current = db.execute('SELECT password_hash FROM auth_users WHERE id=?', (user['id'],)).fetchone()
            if not current or not hmac.compare_digest(current[0], user['password_hash']):
                raise DomainError('AUTH_REQUIRED', 'Учётные данные изменились. Войдите снова.', 401)
            db.execute('DELETE FROM auth_sessions WHERE expires_at<=?', (time.time(),))
            # Rotate the current browser session when logging into another account.
            previous = request.cookies.get(COOKIE, '')
            if previous:
                db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (sha256(previous.encode()).hexdigest(),))
            db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?)',
                       (sha256(token.encode()).hexdigest(), user['id'], csrf, time.time() + SESSION_SECONDS))
        response = JSONResponse({'user': self.public_user(user), 'csrf_token': csrf, 'auth_enabled': True}, status_code=status)
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                            secure=self.secure_cookie or request.url.scheme == 'https', samesite='lax', path='/')
        response.headers['Cache-Control'] = 'no-store'
        return response

    def install(self, app, error_body):
        app.state.auth = self

        @app.middleware('http')
        async def guard(request, call_next):
            path = request.url.path
            public = path in ('/api/v1/health', '/api/v1/auth/login', '/api/v1/auth/register')
            protected = path.startswith('/api/v1/') and not public and request.method != 'OPTIONS'
            try:
                if self.enabled and path.startswith('/api/v1/auth/') and request.method != 'GET':
                    self.check_origin(request)
                if self.enabled and protected:
                    user = self.session(request)
                    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                        self.check_origin(request)
                        provided = request.headers.get('X-CSRF-Token', '')
                        if not hmac.compare_digest(provided.encode(), user['csrf_token'].encode()):
                            raise DomainError('CSRF_REQUIRED', 'Обновите страницу и повторите действие.', 403)
                    request.state.user = user
                    with workspace_scope(user['workspace_id'], user['id']):
                        response = await call_next(request)
                else:
                    response = await call_next(request)
            except DomainError as exc:
                response = JSONResponse(error_body(exc), status_code=exc.status)
            if path.startswith('/api/'):
                response.headers['Cache-Control'] = 'no-store'
                response.headers['X-Content-Type-Options'] = 'nosniff'
                response.headers['Referrer-Policy'] = 'same-origin'
            return response

        @app.post('/api/v1/auth/register', status_code=201)
        def register(payload: dict, request: Request):
            self.check_origin(request)
            self.throttle(request, 'register', limit=5, period=3600)
            email = _email(payload)
            name = _text(payload, 'name', 1, 80)
            workspace_name = _text(payload, 'workspace_name', 1, 120)
            password = _text(payload, 'password', 12, 128)
            if set(payload) != {'email', 'name', 'workspace_name', 'password'}:
                raise DomainError('INVALID_PARAMETERS', 'Проверьте поля регистрации.')
            user = {'id': uuid4().hex, 'email': email, 'name': name, 'workspace_id': uuid4().hex,
                    'workspace_name': workspace_name, 'password_hash': password_hash(password), 'created_at': now()}
            try:
                with self.store.transaction() as db:
                    db.execute('INSERT INTO auth_users VALUES (:id,:email,:name,:workspace_id,:workspace_name,:password_hash,:created_at)', user)
                    with workspace_scope(user['workspace_id'], user['id']):
                        self.store.put(db, 'dataset', 'demo-systeme-v1', demo_dataset())
                        self.store.put(db, 'calculation', 'demo-calc-001', make_demo())
                        self.store.put(db, 'audit', uuid4().hex, {'action': 'account.register', 'actor_id': user['id'], 'created_at': now()})
            except sqlite3.IntegrityError as exc:
                raise DomainError('ACCOUNT_EXISTS', 'Не удалось зарегистрировать этот адрес. Попробуйте войти.', 409) from exc
            return self.issue_session(user, request, 201)

        @app.post('/api/v1/auth/login')
        def login(payload: dict, request: Request):
            self.check_origin(request)
            email = _email(payload)
            password = _text(payload, 'password', 1, 128)
            self.throttle(request, 'login-ip', limit=50)
            self.throttle(request, 'login-email', email, limit=10)
            with self.store.transaction() as db:
                db.row_factory = sqlite3.Row
                found = db.execute('SELECT * FROM auth_users WHERE email=?', (email,)).fetchone()
                user = dict(found) if found else None
            if not verify_password(password, user['password_hash'] if user else self.dummy_hash) or not user:
                raise DomainError('INVALID_CREDENTIALS', 'Неверный email или пароль.', 401)
            return self.issue_session(user, request)

        @app.get('/api/v1/auth/me')
        def me(request: Request):
            if not self.enabled:
                return {'user': None, 'csrf_token': None, 'auth_enabled': False}
            return {'user': self.public_user(request.state.user), 'csrf_token': request.state.user['csrf_token'], 'auth_enabled': True}

        @app.post('/api/v1/auth/logout')
        def logout(request: Request):
            token = request.cookies.get(COOKIE, '')
            with self.store.transaction() as db:
                db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (sha256(token.encode()).hexdigest(),))
            response = JSONResponse({'ok': True})
            response.delete_cookie(COOKIE, httponly=True, secure=self.secure_cookie or request.url.scheme == 'https', samesite='lax', path='/')
            return response

        @app.post('/api/v1/auth/password')
        def change_password(payload: dict, request: Request):
            if not self.enabled:
                raise DomainError('AUTH_DISABLED', 'В режиме без авторизации пароль недоступен.', 409)
            self.throttle(request, 'password', request.state.user['id'], limit=5)
            current = _text(payload, 'current_password', 1, 128)
            new = _text(payload, 'new_password', 12, 128)
            if not verify_password(current, request.state.user['password_hash']):
                raise DomainError('INVALID_CREDENTIALS', 'Текущий пароль неверен.', 401)
            if hmac.compare_digest(current.encode(), new.encode()):
                raise DomainError('INVALID_PARAMETERS', 'Новый пароль должен отличаться от текущего.')
            replacement = password_hash(new)
            with self.store.transaction() as db:
                updated = db.execute('UPDATE auth_users SET password_hash=? WHERE id=? AND password_hash=?',
                                     (replacement, request.state.user['id'], request.state.user['password_hash']))
                if updated.rowcount != 1:
                    raise DomainError('AUTH_REQUIRED', 'Пароль уже изменён. Войдите снова.', 401)
                db.execute('DELETE FROM auth_sessions WHERE user_id=?', (request.state.user['id'],))
                self.store.put(db, 'audit', uuid4().hex, {'action': 'account.password_change', 'actor_id': request.state.user['id'], 'created_at': now()})
            request.state.user['password_hash'] = replacement
            return self.issue_session(request.state.user, request)
