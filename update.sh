#!/usr/bin/env bash
set -euo pipefail
TASK_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.'; exit 1; }
if [[ -f /var/lib/pi-decision/pi-decision.db ]]; then
    "$TASK_DIR/backup.sh" /var/backups/pi-decision
fi
exec "$TASK_DIR/install.sh"
