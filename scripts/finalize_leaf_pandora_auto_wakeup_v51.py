#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path('/opt/trc-tuya')
PANDORA = BASE / 'pandora_leaf_soc.py'
PANDORA_ENV = BASE / 'pandora.env'
BOT_ENV = BASE / 'telegram_gate_bot.env'
AUTO = BASE / 'pandora_soc_auto_wakeup.py'
SERVICE = Path('/etc/systemd/system/trc-pandora-soc-auto-wakeup.service')
TIMER = Path('/etc/systemd/system/trc-pandora-soc-auto-wakeup.timer')
PYTHON = '/opt/trc-tuya/venv/bin/python3'


def set_env(path: Path, values: dict):
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    lines = text.splitlines()
    out = []
    seen = set()
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            key = line.split('=', 1)[0].strip()
            if key in values:
                out.append(f'{key}={values[key]}')
                seen.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f'{key}={value}')
    path.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')


def patch_reader_safely():
    original = PANDORA.read_text(encoding='utf-8')
    candidate = original

    # Use state/CAN timestamp only, never online timestamp fallback.
    old_block = '''        timestamp_raw = (\n            info.get("state_timestamp_utc")\n            or info.get("state_timestamp")\n            or info.get("online_timestamp_utc")\n            or info.get("online_timestamp")\n        )'''
    new_block = '''        timestamp_raw = (\n            info.get("state_timestamp_utc")\n            or info.get("state_timestamp")\n        )'''
    if old_block in candidate:
        candidate = candidate.replace(old_block, new_block, 1)

    # Define freshness threshold immediately after env load, in a known-safe scope.
    marker = '    env = load_env(ENV_FILE)\n'
    fresh_block = (
        '    env = load_env(ENV_FILE)\n'
        '    try:\n'
        '        fresh_seconds = int(env.get("PANDORA_SOC_FRESH_SECONDS", "300"))\n'
        '    except Exception:\n'
        '        fresh_seconds = 300\n'
    )
    if 'fresh_seconds = int(env.get("PANDORA_SOC_FRESH_SECONDS"' not in candidate:
        if marker not in candidate:
            raise RuntimeError('Could not find env load marker in pandora_leaf_soc.py')
        candidate = candidate.replace(marker, fresh_block, 1)

    candidate = re.sub(
        r'"pandora_status":\s*"fresh"\s*if\s*age_seconds\s*is\s*not\s*None\s*and\s*age_seconds\s*<=\s*\d+\s*else\s*"stale"',
        '"pandora_status": "fresh" if age_seconds is not None and age_seconds <= fresh_seconds else "stale"',
        candidate,
        count=1,
    )

    tmp = PANDORA.with_name(PANDORA.name + '.candidate-v51')
    tmp.write_text(candidate, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(tmp)], check=True)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    PANDORA.write_text(candidate, encoding='utf-8')
    tmp.unlink(missing_ok=True)


def main():
    if not PANDORA.exists():
        raise RuntimeError(f'Missing {PANDORA}')
    if not AUTO.exists():
        raise RuntimeError(f'Missing {AUTO}; run v5 installer once to generate it')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backups = []
    for path in [PANDORA, PANDORA_ENV, BOT_ENV, AUTO, SERVICE, TIMER]:
        if path.exists():
            dst = path.with_name(path.name + f'.before-v51.{stamp}')
            shutil.copy2(path, dst)
            backups.append((path, dst))

    try:
        subprocess.run(['systemctl', 'stop', 'trc-pandora-soc-auto-wakeup.timer'], check=False)
        subprocess.run(['systemctl', 'stop', 'trc-pandora-soc-auto-wakeup.service'], check=False)

        patch_reader_safely()
        set_env(PANDORA_ENV, {'PANDORA_SOC_FRESH_SECONDS': '300'})
        set_env(BOT_ENV, {
            'LEAF_WALL_KWH_PER_PERCENT': '0.5455',
            'LEAF_PANDORA_WAKE_FAR_SECONDS': '3600',
            'LEAF_PANDORA_WAKE_NEAR_SECONDS': '900',
            'LEAF_PANDORA_WAKE_FINAL_SECONDS': '300',
            'LEAF_PANDORA_WAKE_WAIT_SECONDS': '50',
            'LEAF_TARGET_PANDORA_REFRESH_SECONDS': '600',
        })

        subprocess.run([PYTHON, '-m', 'py_compile', str(PANDORA)], check=True)
        subprocess.run([PYTHON, '-m', 'py_compile', str(AUTO)], check=True)

        SERVICE.write_text('''[Unit]\nDescription=Wake Leaf via Pandora charge flap and refresh SOC\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=oneshot\nUser=root\nWorkingDirectory=/opt/trc-tuya\nExecStart=/opt/trc-tuya/venv/bin/python3 /opt/trc-tuya/pandora_soc_auto_wakeup.py\nTimeoutStartSec=240\n''', encoding='utf-8')

        TIMER.write_text('''[Unit]\nDescription=Adaptive Pandora SOC wake-up check every 5 minutes\n\n[Timer]\nOnBootSec=2min\nOnUnitActiveSec=5min\nAccuracySec=20s\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n''', encoding='utf-8')

        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', '--now', 'trc-pandora-soc-auto-wakeup.timer'], check=True)

    except Exception:
        for original, saved in backups:
            if saved.exists():
                shutil.copy2(saved, original)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        raise

    print('FINALIZE_OK')
    print('Pandora SOC auto wake-up timer enabled')
    print('Calibration: 0.5455 kWh/%')
    print('Freshness: CAN/state timestamp, max 300 seconds')
    print('Wake intervals: 60m far, 15m near, 5m final')
    print('Backups:')
    for _, saved in backups:
        print(' -', saved)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('FINALIZE_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
