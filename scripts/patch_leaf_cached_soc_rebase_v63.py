#!/usr/bin/env python3
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import patch_leaf_use_cached_then_rebase_v62 as v62

BOT = Path('/opt/trc-tuya/telegram_gate_bot.py')
PYTHON = '/opt/trc-tuya/venv/bin/python3'


def main():
    # Install the full v62 cached-SOC/rebase workflow first.
    v62.main()

    source = BOT.read_text(encoding='utf-8')
    old = '[PYTHON, LEAF_PERCENT_REBASE_SCRIPT]'
    new = '["/opt/trc-tuya/venv/bin/python3", LEAF_PERCENT_REBASE_SCRIPT]'

    if old not in source:
        if new in source:
            print('PATCH_OK')
            print('Cached SOC rebasing is installed and worker launch is already fixed')
            return
        raise RuntimeError('Could not find Leaf rebase worker launch in active bot')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = BOT.with_name(BOT.name + f'.before-v63.{stamp}')
    shutil.copy2(BOT, backup)

    candidate = source.replace(old, new, 1)
    tmp = BOT.with_name(BOT.name + '.candidate-v63')
    tmp.write_text(candidate, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(tmp)], check=True)
    finally:
        tmp.unlink(missing_ok=True)

    try:
        BOT.write_text(candidate, encoding='utf-8')
        subprocess.run([PYTHON, '-m', 'py_compile', str(BOT)], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=True)
    except Exception:
        shutil.copy2(backup, BOT)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=False)
        raise

    print('PATCH_OK')
    print('Cached Pandora SOC is used immediately')
    print('After charging begins, Pandora refreshes and target kWh is rebased')
    print('LeafSpy reference: 54.5 kWh capacity, 98.13% SOH')
    print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
