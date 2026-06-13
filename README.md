# TRC VPS automation backup

This repository is intended to keep a recoverable backup of the VPS automation used for:

- TRC garage gate control through Tuya and Telegram
- Nissan Leaf charger breaker control through Tuya
- Pandora SOC refresh and Leaf charging targets
- Telegram bot UI and notifications
- Apache/PHP public status endpoints
- systemd services and timers for background monitoring

> Security note: this repository is public at the time this README was created. Do **not** commit live secrets here: Telegram bot token, Tuya API secret, Tuya local keys, Pandora password, gate API token, chat IDs, or private customer data. Use `*.env.example` files and keep real secrets only on the VPS or in a private secret manager.

## Current VPS

- Hostname: `vps-bb725d93`
- Public IP: `167.114.155.193`
- OS: Debian GNU/Linux 13
- Main project directory: `/opt/trc-tuya`
- Apache public directory: `/var/www/html/trc`

## Main runtime files on the VPS

### Python scripts

- `/opt/trc-tuya/telegram_gate_bot.py` — main Telegram bot for gate and Leaf charger control.
- `/opt/trc-tuya/gate_control.py` — Tuya garage gate commands and gate status.
- `/opt/trc-tuya/gate_status_reader.py` — gate status cache updater.
- `/opt/trc-tuya/gate_watcher.py` — gate event/offline/left-open notifications.
- `/opt/trc-tuya/leaf_charger_control.py` — Tuya charger breaker commands and status.
- `/opt/trc-tuya/leaf_charger_watcher.py` — Leaf charger notifications, target/timer watcher, charging started/stopped, long-charge, standby, SOC milestones.
- `/opt/trc-tuya/leaf_bg_action.py` — background ON/OFF action executor with confirmation polling.
- `/opt/trc-tuya/pandora_leaf_soc.py` — Pandora Leaf SOC refresh and cache merger.

### Public web/API files

- `/var/www/html/trc/gate.php` — HTTP API for gate actions.
- `/var/www/html/trc/gate_status_text.php` — one-word Russian gate status for Siri/Shortcuts.
- `/var/www/html/trc/gate_mode.php` — mode endpoint.
- `/var/www/html/trc/status.json` — cached gate/PBX/VPS status.
- `/var/www/html/trc/leaf_status.json` — cached Leaf charger/Pandora status.

### Runtime state files

These are useful for recovery but should not be treated as source code:

- `/opt/trc-tuya/leaf_charge_target.json`
- `/opt/trc-tuya/leaf_charger_timer.json`
- `/opt/trc-tuya/leaf_charger_monitor_state.json`
- `/opt/trc-tuya/gate_watcher_state.json`
- `/opt/trc-tuya/pandora_leaf_soc.json`
- `/opt/trc-tuya/gate_audit.log`

### Secret/config files

Real versions must not be committed to a public repo:

- `/opt/trc-tuya/telegram_gate_bot.env`
- `/opt/trc-tuya/tinytuya.json`
- `/opt/trc-tuya/pandora.env`
- `/opt/trc-tuya/telegram_gate_users.json`

Keep redacted examples only.

## Main behavior documented

### Gate

- Full open always requires confirmation in Telegram.
- `Pedestrian` mode: open for about 2 seconds, stop, wait about 3 seconds, then close.
- `Partial` mode: open for about 3 seconds, then stop.
- Dynamic buttons:
  - If gate is fully closed, hide `Close`.
  - If gate is fully open, hide `Open`.
- Notifications:
  - gate command events from audit log,
  - gate opened/closed/status changed,
  - repeated offline/stale status warning,
  - repeated left-open warning.

### Leaf charger

- `/leaf` should be fast and read cached `leaf_status.json`.
- `Charger ON` asks whether to charge by percentage or just turn the charger on.
- ON/OFF commands return immediately with a pending message; real confirmation is sent separately after Tuya reports the new state.
- Dynamic buttons:
  - If charger is ON/CHARGING, show only `Charger OFF`.
  - If charger is OFF, show only `Charger ON`.
  - Show `Cancel Target/Timer` only when a target or timer is active.
- `Cancel Target/Timer` cancels target/timer and turns the charger OFF.
- Notifications:
  - charger breaker ON/OFF,
  - charging started/stopped,
  - long charging warning,
  - Tuya lag warning,
  - SOC milestone every 10%,
  - target reached and auto-OFF,
  - timer completed and auto-OFF,
  - zero-consumption warning,
  - small standby-consumption question once per consumption state.

## Recovery order after a VPS loss

1. Install Debian packages: Python, venv, Apache, PHP, jq, curl, git, systemd units.
2. Restore `/opt/trc-tuya` scripts and create Python venv.
3. Restore `/var/www/html/trc` PHP endpoints.
4. Restore secret files from a private location, not from public GitHub.
5. Restore systemd service/timer units.
6. Run syntax checks:

```bash
/opt/trc-tuya/venv/bin/python3 -m py_compile /opt/trc-tuya/*.py
```

7. Restart services/timers:

```bash
systemctl daemon-reload
systemctl restart trc-telegram-gate-bot.service
systemctl restart trc-leaf-charger-watcher.timer
systemctl restart trc-gate-watcher.timer
systemctl restart trc-pandora-leaf-soc.timer
```

8. Test manually:

```bash
/opt/trc-tuya/gate_control.py status | jq .
/opt/trc-tuya/leaf_charger_control.py status | jq .
/opt/trc-tuya/pandora_leaf_soc.py | jq .
curl -s http://127.0.0.1/trc/status.json | jq .
curl -s http://127.0.0.1/trc/leaf_status.json | jq .
```

## How to create/update a sanitized backup from the VPS

Use `scripts/backup_trc_vps_to_git.sh` from this repository on the VPS. It copies source files, redacts secret configs into `.example` files, records systemd units, and commits the backup. Review the diff before pushing if this repository remains public.
