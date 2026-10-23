import base64
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict, deque

from fastapi import Request

from backend.errors import DomainError
from backend.storage.db import new_id


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(value: str) -> str:
    if not 10 <= len(value) <= 256:
        raise DomainError("password_length", "Use a password between 10 and 256 characters.")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(value.encode(), salt=salt, n=16384, r=8, p=1)
    return base64.b64encode(salt + derived).decode()


def verify_password(value: str, encoded: str) -> bool:
    try:
        data = base64.b64decode(encoded)
        actual = hashlib.scrypt(value[:256].encode(), salt=data[:16], n=16384, r=8, p=1)
        return hmac.compare_digest(actual, data[16:])
    except (ValueError, TypeError):
        return False


class RateLimiter:
    def __init__(self, capacity: int = 4096):
        self.entries: OrderedDict[str, deque] = OrderedDict()
        self.capacity = capacity

    def check(self, key: str, limit: int, window: float = 60):
        now = time.monotonic()
        bucket = self.entries.setdefault(key, deque())
        self.entries.move_to_end(key)
        while bucket and bucket[0] < now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            raise DomainError("rate_limited", "Too many requests. Wait before trying again.", 429)
        bucket.append(now)
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)


def principal(request: Request, minimum: str = "viewer") -> dict:
    store = request.app.state.store
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        token = store.one("SELECT * FROM tokens WHERE token_hash=?", (digest(authorization[7:]),))
        if not token:
            raise DomainError("unauthenticated", "Sign in or supply a valid API token.", 401)
        request.app.state.limiter.check("token:" + token["id"], token["rate"])
        store.execute("UPDATE tokens SET last_used=? WHERE id=?", (time.time(), token["id"]))
        user = {"id": token["id"], "username": token["name"], "role": token["role"], "method": "token"}
    else:
        value = request.cookies.get("pi_session", "")
        row = store.one(
            "SELECT s.csrf,s.expires,u.id,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
            (digest(value),),
        )
        if not row or row["expires"] < time.time():
            raise DomainError("unauthenticated", "Your session has expired. Sign in again.", 401)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not hmac.compare_digest(
                request.headers.get("x-csrf-token", "").encode(), row["csrf"].encode()
            ):
                raise DomainError("csrf_rejected", "Refresh this page and try again.", 403)
        user = {**row, "method": "session"}
        request.app.state.limiter.check("session:" + user["id"], 600)
    if {"viewer": 0, "operator": 1, "admin": 2}.get(user["role"], -1) < {
        "viewer": 0,
        "operator": 1,
        "admin": 2,
    }[minimum]:
        raise DomainError("forbidden", "Your role does not permit this action.", 403)
    request.state.actor = user["username"]
    return user


def create_user(store, username: str, password: str, role: str = "admin") -> dict:
    if not username.strip() or len(username) > 80 or role not in {"admin", "operator", "viewer"}:
        raise DomainError("invalid_user", "Use a valid username and role.")
    user_id = new_id()
    store.execute(
        "INSERT INTO users VALUES(?,?,?,?,?)", (user_id, username, hash_password(password), role, time.time())
    )
    return {"id": user_id, "username": username, "role": role}
