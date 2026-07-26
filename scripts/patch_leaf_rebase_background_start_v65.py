#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BOT = Path('/opt/trc-tuya/telegram_gate_bot.py')
TARGET = Path('/opt/trc-tuya/leaf_charge_target.json')
REBASE = Path('/opt/trc-tuya/leaf_percent_target_rebase.py')
PYTHON = '/opt/trc-tuya/venv/bin/python3'
LOG = Path('/opt/trc-tuya/leaf_percent_target_rebase.log')
MARKER = 'LEAF_REBASE_BG_START_V65'


def replace_function(source: str, name: str, replacement: str) -> str:
    start = source.find(f'def {name}(')
    if start < 0:
        raise RuntimeError(f'Function {name} not found')
    end = source.find('\ndef ', start + 1)
    if end < 0:
        raise RuntimeError(f'End of function {name} not found')
    return source[:start] + replacement.rstrip() + '\n\n' + source[end + 1:]


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def start_existing_rebase_if_needed():
    target = load_json(TARGET, {})
    if not (
        target.get('enabled')
        and target.get('mode') == 'percent'
        and target.get('needs_pandora_rebase')
        and REBASE.exists()
    ):
        return False

    log = open(LOG, 'ab')
    subprocess.Popen(
        [PYTHON, str(REBASE)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return True


def main():
    if not BOT.exists():
        raise RuntimeError(f'Missing {BOT}')
    if not REBASE.exists():
        raise RuntimeError(f'Missing {REBASE}; install v64 first')

    original = BOT.read_text(encoding='utf-8')
    if MARKER in original:
        started = start_existing_rebase_if_needed()
        print('PATCH_ALREADY_APPLIED')
        print('Existing target rebase started:', started)
        return

    replacement = r'''def run_leaf_action_background(action):
    # LEAF_REBASE_BG_START_V65
    if action not in ["on", "off"]:
        return False, "bad action"

    try:
        subprocess.Popen(
            ["/opt/trc-tuya/leaf_bg_action.py", action],
            stdout=open("/opt/trc-tuya/leaf_background_actions.log", "ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True
        )

        # Percentage charging uses the background ON path. Start the rebase
        # worker here; it waits for real charging current before refreshing SOC.
        if action == "on":
            start_percent_target_rebase_background()

        return True, "started"
    except Exception as e:
        return False, repr(e)'''

    candidate = replace_function(original, 'run_leaf_action_background', replacement)

    tmp = BOT.with_name(BOT.name + '.candidate-v65')
    tmp.write_text(candidate, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(tmp)], check=True)
    finally:
        tmp.unlink(missing_ok=True)

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = BOT.with_name(BOT.name + f'.before-v65.{stamp}')
    shutil.copy2(BOT, backup)

    try:
        BOT.write_text(candidate, encoding='utf-8')
        os.chmod(BOT, 0o750)
        subprocess.run([PYTHON, '-m', 'py_compile', str(BOT)], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=True)
        started = start_existing_rebase_if_needed()
    except Exception:
        shutil.copy2(backup, BOT)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=False)
        raise

    print('PATCH_OK')
    print('Background charger ON now starts Pandora SOC target rebase')
    print('Existing target rebase started:', started)
    print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
