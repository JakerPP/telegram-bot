# Backup manifest

Created UTC: 2026-06-13-104001
Hostname: vps-bb725d93
Kernel: Linux vps-bb725d93 6.12.85+deb13-cloud-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.85-1 (2026-04-30) x86_64 GNU/Linux

## Services
  apache-htcacheclean.service                                                                   loaded    active     running       Disk Cache Cleaning Daemon for Apache HTTP Server
  apache2.service                                                                               loaded    active     running       The Apache HTTP Server
  asterisk.service                                                                              loaded    active     running       LSB: Asterisk PBX
● trc-gate-status.service                                                                       loaded    activating start   start TRC Tuya Gate Status Reader
  trc-gate-watcher.service                                                                      loaded    inactive   dead          TRC Gate Watcher Notifications
  trc-leaf-charger-watcher.service                                                              loaded    inactive   dead          TRC Leaf Charger Watcher
  trc-pandora-leaf-soc.service                                                                  loaded    inactive   dead          TRC Pandora Leaf SOC Reader
  trc-telegram-gate-bot.service                                                                 loaded    active     running       TRC Telegram Gate and Leaf Bot
  xray.service                                                                                  loaded    active     running       Xray Service

## Timers
Sat 2026-06-13 10:40:08 UTC        6s Sat 2026-06-13 10:39:38 UTC      23s ago trc-gate-watcher.timer         trc-gate-watcher.service
Sat 2026-06-13 10:40:47 UTC       45s Sat 2026-06-13 10:39:47 UTC      14s ago trc-leaf-charger-watcher.timer trc-leaf-charger-watcher.service
Sat 2026-06-13 10:47:02 UTC      7min Sat 2026-06-13 10:37:02 UTC 2min 59s ago trc-pandora-leaf-soc.timer     trc-pandora-leaf-soc.service
-                                   - Sat 2026-06-13 10:40:02 UTC     69ms ago trc-gate-status.timer          trc-gate-status.service

## Files
vps-backup/MANIFEST.md
vps-backup/opt-trc-tuya/gate_control.py
vps-backup/opt-trc-tuya/gate_status_reader.py
vps-backup/opt-trc-tuya/gate_watcher.py
vps-backup/opt-trc-tuya/leaf_bg_action.py
vps-backup/opt-trc-tuya/leaf_charger_control.py
vps-backup/opt-trc-tuya/leaf_charger_watcher.py
vps-backup/opt-trc-tuya/pandora_leaf_soc.py
vps-backup/opt-trc-tuya/telegram_gate_bot.py
vps-backup/runtime-examples/pandora.env.example
vps-backup/runtime-examples/telegram_gate_bot.env.example
vps-backup/runtime-examples/telegram_gate_users.json.example
vps-backup/runtime-examples/tinytuya.json.example
vps-backup/systemd/trc-gate-watcher.service
vps-backup/systemd/trc-gate-watcher.timer
vps-backup/systemd/trc-leaf-charger-watcher.service
vps-backup/systemd/trc-leaf-charger-watcher.timer
vps-backup/systemd/trc-pandora-leaf-soc.service
vps-backup/systemd/trc-pandora-leaf-soc.timer
vps-backup/systemd/trc-telegram-gate-bot.service
vps-backup/var-www-html-trc/gate.php
vps-backup/var-www-html-trc/gate_mode.php
vps-backup/var-www-html-trc/gate_status_text.php
vps-backup/var-www-html-trc/leaf_status.json
vps-backup/var-www-html-trc/status.json
