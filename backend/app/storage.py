"""SQLite transactions serialize revisions and persist idempotency and snapshots."""
import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import re
from .contracts import DomainError


_workspace = ContextVar('tirek_workspace', default=None)
_actor = ContextVar('tirek_actor', default=None)


def current_workspace():
    return _workspace.get()


def current_actor():
    return _actor.get()


@contextmanager
def workspace_scope(workspace_id, actor_id=None):
    # Identifiers come only from the authenticated server-side account record.
    if workspace_id is not None and not re.fullmatch(r'[a-f0-9]{32}', workspace_id):
        raise ValueError('Invalid workspace identity')
    workspace_token = _workspace.set(workspace_id)
    actor_token = _actor.set(actor_id)
    try:
        yield
    finally:
        _actor.reset(actor_token)
        _workspace.reset(workspace_token)


def scoped_data_directory(base):
    owner = current_workspace()
    return Path(base) / 'workspaces' / owner if owner else Path(base)


def _namespace(value):
    owner = current_workspace()
    return f'{owner}:{value}' if owner else value


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS objects (
                    kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (kind, id));
                CREATE TABLE IF NOT EXISTS idempotency (
                    scope TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY (scope, key));
            ''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def get(db, kind, object_id):
        row = db.execute('SELECT payload FROM objects WHERE kind=? AND id=?', (_namespace(kind), object_id)).fetchone()
        if not row:
            raise DomainError('NOT_FOUND', 'Запись не найдена. Обновите страницу.', 404)
        return json.loads(row[0])

    @staticmethod
    def put(db, kind, object_id, value):
        db.execute('INSERT INTO objects VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload',
                   (_namespace(kind), object_id, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    @staticmethod
    def all(db, kind):
        return [json.loads(row[0]) for row in db.execute('SELECT payload FROM objects WHERE kind=? ORDER BY rowid DESC', (_namespace(kind),))]

    @staticmethod
    def replay(db, scope, key, digest):
        row = db.execute('SELECT digest,payload FROM idempotency WHERE scope=? AND key=?', (_namespace(scope), key)).fetchone()
        if row:
            if row[0] != digest:
                raise DomainError('IDEMPOTENCY_CONFLICT', 'Этот ключ уже использован для другого запроса.', 409)
            return json.loads(row[1])

    @staticmethod
    def remember(db, scope, key, digest, value):
        db.execute('INSERT INTO idempotency VALUES (?,?,?,?)', (_namespace(scope), key, digest, json.dumps(value)))
