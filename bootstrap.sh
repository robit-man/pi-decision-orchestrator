#!/usr/bin/env bash
# One-shot install or update: clones (or pulls) the repository, then runs update.sh,
# which backs up existing data before reinstalling the service.
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.'; exit 1; }
REPO_URL=${PI_REPO_URL:-https://github.com/robit-man/pi-decision-orchestrator.git}
BRANCH=${PI_BRANCH:-main}
SOURCE_DIR=${PI_SOURCE_DIR:-/opt/pi-decision-orchestrator/src}
MISSING=()
command -v git >/dev/null || MISSING+=(git)
command -v curl >/dev/null || MISSING+=(curl ca-certificates)
python3 -c 'import ensurepip' 2>/dev/null || MISSING+=(python3-venv)
if [[ ${#MISSING[@]} -gt 0 ]]; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"
fi
if ! curl --fail --silent --max-time 5 http://127.0.0.1:11434/api/version >/dev/null; then
    if [[ ${PI_SKIP_OLLAMA:-0} == 1 ]]; then
        echo 'Ollama is not reachable on 127.0.0.1:11434 and PI_SKIP_OLLAMA=1 was set.'
        exit 1
    fi
    command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
    systemctl enable --now ollama.service 2>/dev/null || true
    for attempt in $(seq 1 30); do
        curl --fail --silent --max-time 2 http://127.0.0.1:11434/api/version >/dev/null && break
        sleep 1
    done
fi
if [[ -d "$SOURCE_DIR/.git" ]]; then
    git -C "$SOURCE_DIR" fetch --quiet origin "$BRANCH"
    git -C "$SOURCE_DIR" checkout --quiet "$BRANCH"
    git -C "$SOURCE_DIR" reset --quiet --hard "origin/$BRANCH"
else
    install -d -m 755 "$(dirname -- "$SOURCE_DIR")"
    git clone --quiet --branch "$BRANCH" --depth 1 "$REPO_URL" "$SOURCE_DIR"
fi
echo "Deploying $(git -C "$SOURCE_DIR" rev-parse --short HEAD) from $BRANCH"
exec "$SOURCE_DIR/update.sh"
