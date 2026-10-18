#!/usr/bin/env bash
# Install only the new collector; do not reinstall or reschedule existing jobs.
set -euo pipefail
PROJECT_DIR="${1:-$HOME/my-stock-scanner}"
CURRENT_USER="$(id -un)"
for unit in foreign-brokers.service foreign-brokers.timer; do
  sed -e "s|__USER__|$CURRENT_USER|g" -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$PROJECT_DIR/deploy/oracle-cloud/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now foreign-brokers.timer
sudo systemctl list-timers foreign-brokers.timer --no-pager
