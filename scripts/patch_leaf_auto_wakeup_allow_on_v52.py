#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PATH = Path('/opt/trc-tuya/pandora_soc_auto_wakeup.py')
PYTHON = '/opt/trc-tuya/venv/bin/python3'

OLD = """    if leaf.get('switch') is not True or str(leaf.get('charging_state', '')).upper() != 'CHARGING':
        print(json.dumps({'ok': True, 'action': 'skip', 'reason': 'not_charging'}))
        return
"""

NEW = """    if leaf.get('switch') is not True:
        print(json.dumps({'ok': True, 'action': 'skip', 'reason': 'breaker_off'}))
        return

    charging_state = str(leaf.get('charging_state', '')).upper()
    cable_connected = leaf.get('pandora_charging_connected') is True

    if charging_state not in {'CHARGING', 'ON'} and not cable_connected:
        print(json.dumps({
            'ok': True,
            'action': 'skip',
            'reason': 'cable_not_connected',
            'charging_state': charging_state,
        }))
        return
"""


def main():
    if not PATH.exists():
        raise RuntimeError(f'Missing {PATH}')

    text = PATH.read_text(encoding='utf-8')
    if NEW in text:
        print('PATCH_ALREADY_APPLIED')
        return
    if OLD not in text:
        raise RuntimeError('Expected not_charging block was not found')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = PATH.with_name(PATH.name + f'.before-v52.{stamp}')
    shutil.copy2(PATH, backup)

    candidate = text.replace(OLD, NEW, 1)
    tmp = PATH.with_name(PATH.name + '.candidate-v52')
    tmp.write_text(candidate, encoding='utf-8')

    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(tmp)], check=True)
        PATH.write_text(candidate, encoding='utf-8')
        os.chmod(PATH, 0o750)
        subprocess.run([PYTHON, '-m', 'py_compile', str(PATH)], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=True)
    except Exception:
        shutil.copy2(backup, PATH)
        raise
    finally:
        tmp.unlink(missing_ok=True)

    print('PATCH_OK')
    print('Breaker ON + charging_state ON + Pandora cable connected now permits SOC wake-up')
    print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
