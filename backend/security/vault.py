import json
import os
from pathlib import Path

from cryptography.fernet import Fernet


class Vault:
    def __init__(self, store, key_path: Path):
        self.store = store
        key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not key_path.exists():
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(Fernet.generate_key())
        if key_path.stat().st_mode & 0o077:
            raise RuntimeError("Secret encryption key must have permissions 0600")
        self.cipher = Fernet(key_path.read_bytes())

    def set(self, owner: str, values: dict):
        encrypted = self.cipher.encrypt(json.dumps(values).encode())
        self.store.execute(
            "INSERT INTO secrets VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET encrypted=excluded.encrypted",
            (owner, encrypted),
        )

    def get(self, owner: str) -> dict:
        row = self.store.one("SELECT encrypted FROM secrets WHERE owner=?", (owner,))
        return json.loads(self.cipher.decrypt(row["encrypted"])) if row else {}

    def delete(self, owner: str):
        self.store.execute("DELETE FROM secrets WHERE owner=?", (owner,))

    def scrub(self, owner: str, value):
        secrets = [item for key, item in self.get(owner).items() if key != "username" and item]

        def clean(item):
            if isinstance(item, dict):
                return {key: clean(v) for key, v in item.items()}
            if isinstance(item, list):
                return [clean(v) for v in item]
            if isinstance(item, str):
                for secret in secrets:
                    if len(secret) >= 3:
                        item = item.replace(secret, "[redacted]")
                    elif item == secret:
                        item = "[redacted]"
            return item

        return redact(clean(value))


SENSITIVE_KEYS = {
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "credentials",
    "cookie",
}


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]" if any(part in key.lower() for part in SENSITIVE_KEYS) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
