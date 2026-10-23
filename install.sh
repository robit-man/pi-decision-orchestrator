#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo: sudo ./install.sh'; exit 1; }
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APP_ROOT=/opt/pi-decision-orchestrator
[[ $(uname -m) == aarch64 || ${PI_ALLOW_NON_ARM:-0} == 1 ]] || { echo 'A 64-bit ARM kernel is required.'; exit 1; }
[[ -f "$SOURCE_DIR/backend/static/index.html" ]] || { echo 'Production assets missing. Run npm ci and npm run build in frontend first.'; exit 1; }
AVAILABLE_KB=$(awk '/MemTotal/{print $2}' /proc/meminfo)
[[ $AVAILABLE_KB -ge 2000000 ]] || { echo 'At least 2 GB RAM is required; 8 GB is recommended.'; exit 1; }
FREE_KB=$(df -Pk /opt | awk 'NR==2 {print $4}')
[[ $FREE_KB -ge 1048576 ]] || { echo 'At least 1 GB free application storage is required, plus model storage.'; exit 1; }
command -v curl >/dev/null || { echo 'Install curl and ca-certificates first.'; exit 1; }
OLLAMA_VERSION=$(curl --fail --silent --max-time 10 http://127.0.0.1:11434/api/version | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')
python3 - "$OLLAMA_VERSION" <<'PY'
import re, sys
version = tuple(map(int, re.match(r'(\d+)\.(\d+)\.(\d+)', sys.argv[1]).groups()))
if version < (0, 40, 0):
    raise SystemExit('Ollama 0.40.0 or newer is required.')
PY
getent group pi-decision >/dev/null || groupadd --system pi-decision
id pi-decision >/dev/null 2>&1 || useradd --system --gid pi-decision --home-dir /var/lib/pi-decision --shell /usr/sbin/nologin pi-decision
install -d -m 700 -o pi-decision -g pi-decision /var/lib/pi-decision /etc/pi-decision
install -d -m 755 "$APP_ROOT" "$APP_ROOT/app"
PYTHON_BIN=${PI_PYTHON:-python3}
if [[ -x /opt/pi-decision-runtime/python/bin/python3.12 ]]; then
    PYTHON_BIN=/opt/pi-decision-runtime/python/bin/python3.12
    export LD_LIBRARY_PATH=/opt/pi-decision-runtime/python/lib:/opt/pi-decision-runtime/lib
    printf '%s\n' 'LD_LIBRARY_PATH=/opt/pi-decision-runtime/python/lib:/opt/pi-decision-runtime/lib' > /etc/pi-decision/runtime.env
    chmod 640 /etc/pi-decision/runtime.env
    chown root:pi-decision /etc/pi-decision/runtime.env
fi
"$PYTHON_BIN" -c 'import sys,struct; assert sys.version_info >= (3,11) and struct.calcsize("P")==8, "Python 3.11+ with 64-bit userspace is required"'
if [[ "$SOURCE_DIR" != "$APP_ROOT/app" ]]; then
    tar -C "$SOURCE_DIR" --exclude=.git --exclude=.venv --exclude=node_modules --exclude=artifacts --exclude=.pytest_cache --exclude=.ruff_cache --exclude=__pycache__ --exclude=test-results --exclude=playwright-report -cf - . | tar -C "$APP_ROOT/app" -xf -
fi
"$PYTHON_BIN" -m venv "$APP_ROOT/.venv"
"$APP_ROOT/.venv/bin/python" -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes -r "$APP_ROOT/app/requirements.lock"
if [[ ! -f /etc/pi-decision/app.env ]]; then
    install -m 640 -o root -g pi-decision "$SOURCE_DIR/.env.example" /etc/pi-decision/app.env
fi
chown -R root:root "$APP_ROOT/app"
chmod -R go-w "$APP_ROOT/app"
install -m 644 "$SOURCE_DIR/systemd/pi-decision.service" /etc/systemd/system/pi-decision.service
if [[ -n ${LD_LIBRARY_PATH:-} && -f /etc/ld.so.preload ]]; then
    install -d -m 755 /etc/systemd/system/pi-decision.service.d
    printf '%s\n' '[Service]' 'BindReadOnlyPaths=/dev/null:/etc/ld.so.preload' > /etc/systemd/system/pi-decision.service.d/legacy-runtime.conf
fi
systemctl daemon-reload
systemctl enable pi-decision.service
systemctl restart pi-decision.service
for attempt in $(seq 1 30); do
    if curl --silent --fail --max-time 2 http://127.0.0.1:8765/api/v1/health >/dev/null; then
        PI_ADDRESS=$(hostname -I | awk '{print $1}')
        echo "Dashboard ready: http://${PI_ADDRESS}:8765"
        echo 'Administrator credentials: sudo cat /etc/pi-decision/initial-admin.txt'
        echo 'No models were downloaded. Install or verify a model in the dashboard.'
        exit 0
    fi
    sleep 1
done
systemctl --no-pager status pi-decision.service
exit 1
