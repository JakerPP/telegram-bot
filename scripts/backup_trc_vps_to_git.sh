#!/usr/bin/env bash
set -euo pipefail

# TRC VPS backup helper.
# Run on the VPS from a local clone of this repository.
# It copies source code and service definitions, creates redacted examples for secrets,
# and commits the result. Review the diff before pushing, especially if the repo is public.

REPO_DIR="${REPO_DIR:-$PWD}"
SRC_ROOT="${SRC_ROOT:-/opt/trc-tuya}"
WEB_ROOT="${WEB_ROOT:-/var/www/html/trc}"
BACKUP_ROOT="$REPO_DIR/vps-backup"
DATE_UTC="$(date -u +%F-%H%M%S)"

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "ERROR: run this from inside the git repository clone, or set REPO_DIR=/path/to/repo" >&2
  exit 1
fi

mkdir -p "$BACKUP_ROOT/opt-trc-tuya" "$BACKUP_ROOT/var-www-html-trc" "$BACKUP_ROOT/systemd" "$BACKUP_ROOT/runtime-examples" "$BACKUP_ROOT/logs"

copy_if_exists() {
  local src="$1"
  local dst="$2"
  if [ -e "$src" ]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
  else
    echo "missing: $src" >> "$BACKUP_ROOT/logs/missing-$DATE_UTC.txt"
  fi
}

# Main scripts
for f in \
  telegram_gate_bot.py \
  gate_control.py \
  gate_status_reader.py \
  gate_watcher.py \
  leaf_charger_control.py \
  leaf_charger_watcher.py \
  leaf_bg_action.py \
  pandora_leaf_soc.py
 do
  copy_if_exists "$SRC_ROOT/$f" "$BACKUP_ROOT/opt-trc-tuya/$f"
done

# Web endpoints and cached JSON examples
for f in \
  gate.php \
  gate_mode.php \
  gate_status_text.php \
  status.json \
  leaf_status.json
 do
  copy_if_exists "$WEB_ROOT/$f" "$BACKUP_ROOT/var-www-html-trc/$f"
done

# Systemd units
for unit in \
  trc-telegram-gate-bot.service \
  trc-leaf-charger-watcher.service \
  trc-leaf-charger-watcher.timer \
  trc-gate-watcher.service \
  trc-gate-watcher.timer \
  trc-pandora-leaf-soc.service \
  trc-pandora-leaf-soc.timer
 do
  copy_if_exists "/etc/systemd/system/$unit" "$BACKUP_ROOT/systemd/$unit"
done

# Redacted examples for secrets/configs. Do not copy live secret values into GitHub.
cat > "$BACKUP_ROOT/runtime-examples/telegram_gate_bot.env.example" <<'EOF'
TELEGRAM_BOT_TOKEN=REDACTED
ALLOWED_CHAT_ID=REDACTED
LEAF_BATTERY_KWH=62
LEAF_CHARGE_EFFICIENCY=0.88
LEAF_LONG_CHARGE_SECONDS=43200
LEAF_LONG_CHARGE_REPEAT_SECONDS=3600
LEAF_ON_NOT_CHARGING_SECONDS=600
LEAF_ON_NOT_CHARGING_REPEAT_SECONDS=1800
LEAF_TUYA_LAG_REPEAT_SECONDS=900
LEAF_IDLE_ZERO_CURRENT_A_MAX=0.01
LEAF_IDLE_ZERO_POWER_KW_MAX=0.01
LEAF_CHARGE_CURRENT_A_MIN=0.5
LEAF_CHARGE_POWER_KW_MIN=0.1
GATE_OPEN_ALERT_SECONDS=300
GATE_OPEN_REPEAT_SECONDS=300
GATE_OFFLINE_STALE_SECONDS=120
GATE_OFFLINE_REPEAT_SECONDS=300
EOF

cat > "$BACKUP_ROOT/runtime-examples/tinytuya.json.example" <<'EOF'
{
  "apiKey": "REDACTED",
  "apiSecret": "REDACTED",
  "apiRegion": "eu",
  "apiDeviceID": "REDACTED",
  "devices": []
}
EOF

cat > "$BACKUP_ROOT/runtime-examples/pandora.env.example" <<'EOF'
PANDORA_USERNAME=REDACTED
PANDORA_PASSWORD=REDACTED
PANDORA_DEVICE_ID=1080983499
EOF

cat > "$BACKUP_ROOT/runtime-examples/telegram_gate_users.json.example" <<'EOF'
{
  "owner_chat_id": "REDACTED"
}
EOF

# Manifest
{
  echo "# Backup manifest"
  echo
  echo "Created UTC: $DATE_UTC"
  echo "Hostname: $(hostname)"
  echo "Kernel: $(uname -a)"
  echo
  echo "## Services"
  systemctl list-units --type=service --all | grep -E 'trc|asterisk|apache|xray' || true
  echo
  echo "## Timers"
  systemctl list-timers --all | grep -E 'trc|leaf|gate|pandora' || true
  echo
  echo "## Files"
  find "$BACKUP_ROOT" -type f | sed "s#^$REPO_DIR/##" | sort
} > "$BACKUP_ROOT/MANIFEST.md"

# Safety scan for obvious live secrets in tracked backup area.
if grep -RInE 'TELEGRAM_BOT_TOKEN=.*[0-9]{6,}:|apiSecret|PANDORA_PASSWORD=|X-TRC-Token|Bearer [A-Za-z0-9._-]+' "$BACKUP_ROOT" \
  --exclude='*.example' --exclude='MANIFEST.md'; then
  echo
  echo "WARNING: possible secrets found above. Review before committing/pushing." >&2
  echo "Commit was not created." >&2
  exit 2
fi

git add README.md scripts/backup_trc_vps_to_git.sh vps-backup

git commit -m "Backup TRC VPS automation $DATE_UTC" || echo "No git changes to commit"

echo
 echo "Backup staged/committed locally. Review with:"
echo "  git status"
echo "  git show --stat --oneline HEAD"
echo
echo "Push only after review:"
echo "  git push"
