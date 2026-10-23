#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.'; exit 1; }
BACKUP_DEST=${1:-/var/backups/pi-decision}
install -d -m 700 "$BACKUP_DEST"
BACKUP_STAGE=$(mktemp -d)
trap 'rm -rf -- "$BACKUP_STAGE"' EXIT
install -d -m 700 "$BACKUP_STAGE/data" "$BACKUP_STAGE/config"
if [[ -f /etc/pi-decision/runtime.env ]]; then
    set -a
    source /etc/pi-decision/runtime.env
    set +a
fi
/opt/pi-decision-orchestrator/.venv/bin/python - "$BACKUP_STAGE/data/pi-decision.db" <<'PY'
import sqlite3, sys
with sqlite3.connect('/var/lib/pi-decision/pi-decision.db') as source, sqlite3.connect(sys.argv[1]) as target:
    source.backup(target)
    assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
PY
cp -a /etc/pi-decision/. "$BACKUP_STAGE/config/"
printf '%s\n' 'pi-decision-backup-v1' > "$BACKUP_STAGE/FORMAT"
BACKUP_FILE="$BACKUP_DEST/pi-decision-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
tar -C "$BACKUP_STAGE" -czf "$BACKUP_FILE" FORMAT data config
chmod 600 "$BACKUP_FILE"
echo "Backup saved: $BACKUP_FILE (contains encrypted credentials and their key; keep private)"
