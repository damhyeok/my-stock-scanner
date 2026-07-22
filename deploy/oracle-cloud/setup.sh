#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${1:-$HOME/my-stock-scanner}"
CURRENT_USER="$(id -un)"
UNIT_SOURCE="$PROJECT_DIR/deploy/oracle-cloud"

if [[ ! -f "$PROJECT_DIR/cloud_job.py" ]]; then
  echo "Project not found: $PROJECT_DIR" >&2
  exit 1
fi

sudo timedatectl set-timezone Asia/Seoul
sudo apt-get update
sudo apt-get install -y git python3 python3-venv

if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

cd "$PROJECT_DIR"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

git config user.name "stock-scanner-cloud"
git config user.email "stock-scanner-cloud@users.noreply.github.com"

render_unit() {
  local source_file="$1"
  local target_file="/etc/systemd/system/$(basename "$source_file")"
  sed \
    -e "s|__USER__|$CURRENT_USER|g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$source_file" | sudo tee "$target_file" >/dev/null
}

render_unit "$UNIT_SOURCE/stock-scanner@.service"
render_unit "$UNIT_SOURCE/stock-trigger.service"
for timer_file in "$UNIT_SOURCE"/*.timer; do
  render_unit "$timer_file"
done

sudo systemctl daemon-reload
sudo systemctl disable --now morning-strength.timer 2>/dev/null || true
sudo systemctl enable --now \
  stock-trigger.service \
  morning-program.timer \
  afternoon-program.timer \
  afternoon-strength.timer \
  closing-program.timer \
  closing-strength.timer \
  index-bars.timer \
  sector-flow.timer \
  stock-analysis.timer

echo "Installed timers:"
systemctl list-timers --all | grep -E 'morning-|afternoon-|closing-|index-bars|sector-flow|stock-analysis' || true
