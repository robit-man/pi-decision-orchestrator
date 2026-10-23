#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.'; exit 1; }
systemctl disable --now pi-decision.service || true
rm -f /etc/systemd/system/pi-decision.service
rm -rf /etc/systemd/system/pi-decision.service.d
systemctl daemon-reload
echo 'Service removed. Application files, data, configuration, models, and backups were retained.'
