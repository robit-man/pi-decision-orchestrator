import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from backend.errors import DomainError


def new_id() -> str:
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        os.chmod(path, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        migrations = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
        for index, migration in enumerate(migrations, 1):
            if version < index:
                self.connection.executescript(
                    "BEGIN IMMEDIATE;\n" + migration.read_text() + f"\nPRAGMA user_version={index};\nCOMMIT;"
                )
        self.execute(
            "UPDATE executions SET status='interrupted',error='Service restarted during execution',finished=? WHERE status IN ('queued','running')",
            (time.time(),),
        )

    def close(self):
        self.connection.close()

    def execute(self, sql: str, args=()):
        with self.lock:
            return self.connection.execute(sql, args)

    def rows(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        result = self.rows(sql, args)
        return result[0] if result else None

    @contextmanager
    def transaction(self):
        with self.lock:
            nested = self.connection.in_transaction
            if not nested:
                self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
                if not nested:
                    self.connection.execute("COMMIT")
            except BaseException:
                if not nested:
                    self.connection.execute("ROLLBACK")
                raise

    def find(self, kind: str, **filters):
        # Internal field names only. Values always remain SQLite parameters.
        if any(not key.replace("_", "").isalnum() for key in filters):
            raise ValueError("Invalid object field")
        where = "kind=?" + "".join(" AND json_extract(payload,?)=?" for _ in filters)
        args = [kind]
        for key, value in filters.items():
            args.extend(["$." + key, value])
        rows = self.rows(f"SELECT id FROM objects WHERE {where} ORDER BY updated DESC", args)  # noqa: S608 -- constant predicates with bound JSON paths and values.
        return [self.get(kind, row["id"]) for row in rows]

    def get(self, kind: str, object_id: str) -> dict:
        row = self.one("SELECT * FROM objects WHERE kind=? AND id=?", (kind, object_id))
        if not row:
            raise DomainError("not_found", f"{kind.replace('_', ' ').capitalize()} not found", 404)
        return {
            **json.loads(row["payload"]),
            "id": row["id"],
            "revision": row["revision"],
            "created_at": row["created"],
            "updated_at": row["updated"],
        }

    def list(self, kind: str, limit: int = 100, offset: int = 0, search: str = "") -> dict:
        limit, offset = min(max(limit, 1), 200), max(offset, 0)
        where, args = "kind=?", [kind]
        if search:
            where += " AND json_extract(payload,'$.name') LIKE ? ESCAPE '\\'"
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.append(f"%{escaped}%")
        rows = self.rows(
            f"SELECT id FROM objects WHERE {where} ORDER BY updated DESC LIMIT ? OFFSET ?",  # noqa: S608 -- SQL fragments are constants; all request values are bound parameters.
            (*args, limit, offset),
        )
        total = self.one(f"SELECT COUNT(*) AS n FROM objects WHERE {where}", args)["n"]  # noqa: S608 -- SQL fragments are constants; all request values are bound parameters.
        return {
            "items": [self.get(kind, row["id"]) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def put(
        self, kind: str, payload: dict, object_id: str | None = None, expected: int | None = None
    ) -> dict:
        object_id = object_id or new_id()
        clean = {
            key: value
            for key, value in payload.items()
            if key not in {"id", "revision", "created_at", "updated_at"}
        }
        now = time.time()
        with self.transaction():
            row = self.one("SELECT revision FROM objects WHERE kind=? AND id=?", (kind, object_id))
            if row and expected is not None and expected != row["revision"]:
                raise DomainError("revision_conflict", "This record changed. Reload it before saving.", 409)
            if row:
                self.connection.execute(
                    "UPDATE objects SET payload=?,revision=revision+1,updated=? WHERE kind=? AND id=?",
                    (json.dumps(clean, allow_nan=False), now, kind, object_id),
                )
            else:
                self.connection.execute(
                    "INSERT INTO objects VALUES(?,?,?,1,?,?)",
                    (kind, object_id, json.dumps(clean, allow_nan=False), now, now),
                )
        return self.get(kind, object_id)

    def delete(self, kind: str, object_id: str):
        self.get(kind, object_id)
        self.execute("DELETE FROM objects WHERE kind=? AND id=?", (kind, object_id))

    def event(self, event_type: str, payload: dict):
        self.execute(
            "INSERT INTO events(type,payload,created) VALUES(?,?,?)",
            (event_type, json.dumps(payload, allow_nan=False), time.time()),
        )

    def audit(self, actor: str, action: str, object_id: str = ""):
        self.execute(
            "INSERT INTO audit(actor,action,object_id,created) VALUES(?,?,?,?)",
            (actor, action, object_id, time.time()),
        )

    def setting(self, name: str, default=None):
        row = self.one("SELECT payload FROM objects WHERE kind='setting' AND id=?", (name,))
        return json.loads(row["payload"])["value"] if row else default

    def set_setting(self, name: str, value):
        self.put("setting", {"value": value}, name)

    def prune(self, limit: int):
        self.execute(
            "DELETE FROM executions WHERE id IN (SELECT id FROM executions WHERE status NOT IN ('running','queued') ORDER BY created DESC LIMIT -1 OFFSET ?)",
            (limit,),
        )
        self.execute("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0)-5000 FROM events)")
        self.execute("DELETE FROM audit WHERE id < (SELECT COALESCE(MAX(id),0)-10000 FROM audit)")
        self.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        self.execute("DELETE FROM action_keys WHERE updated<?", (time.time() - 86400 * 30,))
