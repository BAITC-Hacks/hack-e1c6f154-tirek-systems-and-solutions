"""SQLite transactions serialize revisions and persist idempotency and snapshots."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .contracts import DomainError


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
        row = db.execute('SELECT payload FROM objects WHERE kind=? AND id=?', (kind, object_id)).fetchone()
        if not row:
            raise DomainError('NOT_FOUND', 'Запись не найдена. Обновите страницу.', 404)
        return json.loads(row[0])

    @staticmethod
    def put(db, kind, object_id, value):
        db.execute('INSERT INTO objects VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload',
                   (kind, object_id, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    @staticmethod
    def all(db, kind):
        return [json.loads(row[0]) for row in db.execute('SELECT payload FROM objects WHERE kind=? ORDER BY rowid DESC', (kind,))]

    @staticmethod
    def replay(db, scope, key, digest):
        row = db.execute('SELECT digest,payload FROM idempotency WHERE scope=? AND key=?', (scope, key)).fetchone()
        if row:
            if row[0] != digest:
                raise DomainError('IDEMPOTENCY_CONFLICT', 'Этот ключ уже использован для другого запроса.', 409)
            return json.loads(row[1])

    @staticmethod
    def remember(db, scope, key, digest, value):
        db.execute('INSERT INTO idempotency VALUES (?,?,?,?)', (scope, key, digest, json.dumps(value)))
