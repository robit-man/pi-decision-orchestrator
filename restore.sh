#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ ${EUID} -eq 0 && $# -eq 1 ]] || { echo 'Usage: sudo ./restore.sh /path/to/backup.tar.gz'; exit 1; }
RESTORE_STAGE=$(mktemp -d)
trap 'rm -rf -- "$RESTORE_STAGE"' EXIT
python3 - "$1" "$RESTORE_STAGE" <<'PY'
import pathlib, sys, tarfile
root = pathlib.Path(sys.argv[2]).resolve()
with tarfile.open(sys.argv[1]) as archive:
    for member in archive.getmembers():
        target = (root / member.name).resolve()
        if root not in target.parents or not (member.isfile() or member.isdir()):
            raise SystemExit('Unsafe backup entry rejected.')
    archive.extractall(root)
assert (root / 'FORMAT').read_text().strip() == 'pi-decision-backup-v1'
PY
[[ -f "$RESTORE_STAGE/data/pi-decision.db" && -f "$RESTORE_STAGE/config/secrets.key" ]] || { echo 'Backup is incomplete.'; exit 1; }
systemctl stop pi-decision.service
rm -f /var/lib/pi-decision/pi-decision.db-wal /var/lib/pi-decision/pi-decision.db-shm
install -m 600 -o pi-decision -g pi-decision "$RESTORE_STAGE/data/pi-decision.db" /var/lib/pi-decision/pi-decision.db
cp -a "$RESTORE_STAGE/config/." /etc/pi-decision/
chown -R pi-decision:pi-decision /etc/pi-decision
chmod 700 /etc/pi-decision
systemctl start pi-decision.service
echo 'Backup restored. Check sudo systemctl status pi-decision.service.'
