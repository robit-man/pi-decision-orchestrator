CREATE TABLE IF NOT EXISTS objects (
 kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(kind,id)
);
CREATE INDEX IF NOT EXISTS objects_updated ON objects(kind,updated DESC);
CREATE TABLE IF NOT EXISTS executions (
 id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, version INTEGER NOT NULL,
 status TEXT NOT NULL, dry_run INTEGER NOT NULL, created REAL NOT NULL,
 started REAL, finished REAL, latency_ms REAL, inference_ms REAL,
 model TEXT, model_digest TEXT, input TEXT NOT NULL, result TEXT, error TEXT,
 idempotency_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS executions_recent ON executions(created DESC);
CREATE INDEX IF NOT EXISTS executions_decision ON executions(decision_id,created DESC);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
 role TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL,
 expires REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tokens (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
 role TEXT NOT NULL, rate INTEGER NOT NULL, created REAL NOT NULL, last_used REAL
);
CREATE TABLE IF NOT EXISTS secrets (
 owner TEXT PRIMARY KEY, encrypted BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
 payload TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
 action TEXT NOT NULL, object_id TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS action_keys (
 key TEXT PRIMARY KEY, status TEXT NOT NULL, updated REAL NOT NULL
);
