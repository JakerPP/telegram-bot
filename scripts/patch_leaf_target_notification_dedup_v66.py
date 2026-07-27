#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

WATCHER = Path('/opt/trc-tuya/leaf_charger_watcher.py')
ENV_FILE = Path('/opt/trc-tuya/telegram_gate_bot.env')
PYTHON = '/opt/trc-tuya/venv/bin/python3'
MARKER = 'LEAF_TARGET_NOTIFICATION_DEDUP_V66'


def set_env(path: Path, key: str, value: str):
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    lines = text.splitlines()
    out = []
    seen = False
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            current_key = line.split('=', 1)[0].strip()
            if current_key == key:
                out.append(f'{key}={value}')
                seen = True
                continue
        out.append(line)
    if not seen:
        out.append(f'{key}={value}')
    path.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')


def main():
    if not WATCHER.exists():
        raise RuntimeError(f'Missing {WATCHER}')

    source = WATCHER.read_text(encoding='utf-8')
    if MARKER in source:
        print('PATCH_ALREADY_APPLIED')
        return

    anchor = '''    last_switch = state.get("last_switch")
    last_actual_charging = state.get("last_actual_charging")
'''
    if anchor not in source:
        raise RuntimeError('Watcher state-transition anchor was not found')

    block = '''    last_switch = state.get("last_switch")
    last_actual_charging = state.get("last_actual_charging")

    # LEAF_TARGET_NOTIFICATION_DEDUP_V66
    # After target auto-off, the next watcher poll sees both relay OFF and
    # charging stopped. The dedicated target-reached notification was already
    # sent, so synchronize transition state and suppress the two generic copies.
    recent_target_completion = False
    try:
        completed_target = load_json(LEAF_TARGET_FILE, {"enabled": False})
        completed_at = int(completed_target.get("completed_at") or 0)
        dedup_seconds = env_int(env, "LEAF_TARGET_COMPLETION_DEDUP_SECONDS", 300)
        recent_target_completion = (
            completed_at > 0
            and 0 <= ts - completed_at <= max(30, dedup_seconds)
            and completed_target.get("completed_ok") is True
        )
    except Exception:
        recent_target_completion = False

    if recent_target_completion:
        last_switch = switch_value
        last_actual_charging = actual_charging
        state["last_switch"] = switch_value
        state["last_actual_charging"] = actual_charging
        state["target_completion_transitions_suppressed_at"] = ts
'''

    candidate = source.replace(anchor, block, 1)

    tmp = WATCHER.with_name(WATCHER.name + '.candidate-v66')
    tmp.write_text(candidate, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(tmp)], check=True)
    finally:
        tmp.unlink(missing_ok=True)

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = WATCHER.with_name(WATCHER.name + f'.before-v66.{stamp}')
    shutil.copy2(WATCHER, backup)
    env_backup = None
    if ENV_FILE.exists():
        env_backup = ENV_FILE.with_name(ENV_FILE.name + f'.before-v66.{stamp}')
        shutil.copy2(ENV_FILE, env_backup)

    try:
        WATCHER.write_text(candidate, encoding='utf-8')
        os.chmod(WATCHER, 0o750)
        set_env(ENV_FILE, 'LEAF_TARGET_COMPLETION_DEDUP_SECONDS', '300')
        subprocess.run([PYTHON, '-m', 'py_compile', str(WATCHER)], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-leaf-charger-watcher.timer'], check=True)
    except Exception:
        shutil.copy2(backup, WATCHER)
        if env_backup and env_backup.exists():
            shutil.copy2(env_backup, ENV_FILE)
        subprocess.run(['systemctl', 'restart', 'trc-leaf-charger-watcher.timer'], check=False)
        raise

    print('PATCH_OK')
    print('After target completion, only the dedicated target-reached notification remains')
    print('Generic breaker-OFF and charging-stopped copies are suppressed for 5 minutes')
    print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
