#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path('/opt/trc-tuya')
BOT = BASE / 'telegram_gate_bot.py'
WATCHER = BASE / 'leaf_charger_watcher.py'
PYTHON = '/opt/trc-tuya/venv/bin/python3'
MARKER = 'VERIFIED_PANDORA_AGE_V60'


def replace_function(source: str, name: str, replacement: str) -> str:
    start = source.find(f'def {name}(')
    if start < 0:
        raise RuntimeError(f'Function {name} not found')
    end = source.find('\ndef ', start + 1)
    if end < 0:
        raise RuntimeError(f'End of function {name} not found')
    return source[:start] + replacement.rstrip() + '\n\n' + source[end + 1:]


def insert_before_function(source: str, name: str, block: str) -> str:
    anchor = f'def {name}('
    pos = source.find(anchor)
    if pos < 0:
        raise RuntimeError(f'Anchor function {name} not found')
    return source[:pos] + block.rstrip() + '\n\n\n' + source[pos:]


def patch_bot(source: str) -> str:
    if MARKER in source:
        return source

    helper = r'''# VERIFIED_PANDORA_AGE_V60
PANDORA_VERIFIED_STATE_FILE = "/opt/trc-tuya/pandora_soc_auto_wakeup_state.json"


def overlay_verified_pandora_status(data):
    if not isinstance(data, dict):
        return data

    state = load_json(PANDORA_VERIFIED_STATE_FILE, {})
    try:
        verified_at = int(state.get("last_verified_at") or 0)
        verified_soc = state.get("last_verified_soc")
        if not verified_at or verified_soc is None:
            return data

        age_minutes = max(0.0, (now() - verified_at) / 60.0)
        max_age = env_float("LEAF_VERIFIED_SOC_DISPLAY_MAX_AGE_MINUTES", 30)

        data["pandora_soc_percent"] = float(verified_soc)
        data["pandora_age_minutes"] = round(age_minutes, 1)
        data["pandora_status"] = "fresh" if age_minutes <= max_age else "stale"
        data["pandora_verified_at"] = verified_at
        data["pandora_verified_source"] = state.get("last_verified_source") or "charge_flap_wakeup"
    except Exception:
        pass

    return data
'''
    source = insert_before_function(source, 'read_leaf_status', helper)

    replacement = r'''def read_leaf_status():
    # Fast mode: read cached leaf_status.json, then overlay the last SOC that
    # was positively verified by waking the Leaf through Pandora.
    data = load_json(LEAF_STATUS_FILE, {})

    if not data:
        return {
            "ok": False,
            "error": "leaf_status.json is empty or missing"
        }

    if "ok" not in data:
        data["ok"] = True

    return overlay_verified_pandora_status(data)'''
    source = replace_function(source, 'read_leaf_status', replacement)
    return source


def patch_watcher(source: str) -> str:
    if MARKER in source:
        return source

    helper = r'''# VERIFIED_PANDORA_AGE_V60
PANDORA_VERIFIED_STATE_FILE = "/opt/trc-tuya/pandora_soc_auto_wakeup_state.json"


def overlay_verified_pandora_status(data):
    if not isinstance(data, dict):
        return data

    state = load_json(PANDORA_VERIFIED_STATE_FILE, {})
    try:
        verified_at = int(state.get("last_verified_at") or 0)
        verified_soc = state.get("last_verified_soc")
        if not verified_at or verified_soc is None:
            return data

        age_minutes = max(0.0, (now() - verified_at) / 60.0)
        env = load_env(ENV_FILE)
        max_age = env_float(env, "LEAF_VERIFIED_SOC_DISPLAY_MAX_AGE_MINUTES", 30)

        data["pandora_soc_percent"] = float(verified_soc)
        data["pandora_age_minutes"] = round(age_minutes, 1)
        data["pandora_status"] = "fresh" if age_minutes <= max_age else "stale"
        data["pandora_verified_at"] = verified_at
        data["pandora_verified_source"] = state.get("last_verified_source") or "charge_flap_wakeup"
    except Exception:
        pass

    return data
'''
    source = insert_before_function(source, 'read_leaf_status', helper)

    replacement = r'''def read_leaf_status():
    p = subprocess.run(
        [LEAF_CONTROL, "status"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=90
    )

    if p.returncode != 0:
        return {
            "ok": False,
            "error": p.stdout + "\n" + p.stderr
        }

    try:
        return overlay_verified_pandora_status(json.loads(p.stdout))
    except Exception as e:
        return {
            "ok": False,
            "error": repr(e),
            "raw": p.stdout
        }'''
    source = replace_function(source, 'read_leaf_status', replacement)
    return source


def compile_candidate(path: Path, content: str, suffix: str):
    candidate = path.with_name(path.name + suffix)
    candidate.write_text(content, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(candidate)], check=True)
    finally:
        candidate.unlink(missing_ok=True)


def main():
    for path in (BOT, WATCHER):
        if not path.exists():
            raise RuntimeError(f'Missing {path}')

    bot_old = BOT.read_text(encoding='utf-8')
    watcher_old = WATCHER.read_text(encoding='utf-8')
    bot_new = patch_bot(bot_old)
    watcher_new = patch_watcher(watcher_old)

    if bot_new == bot_old and watcher_new == watcher_old:
        print('PATCH_ALREADY_APPLIED')
        return

    compile_candidate(BOT, bot_new, '.candidate-v60')
    compile_candidate(WATCHER, watcher_new, '.candidate-v60')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    bot_backup = BOT.with_name(BOT.name + f'.before-v60.{stamp}')
    watcher_backup = WATCHER.with_name(WATCHER.name + f'.before-v60.{stamp}')
    shutil.copy2(BOT, bot_backup)
    shutil.copy2(WATCHER, watcher_backup)

    try:
        BOT.write_text(bot_new, encoding='utf-8')
        WATCHER.write_text(watcher_new, encoding='utf-8')
        os.chmod(BOT, 0o750)
        os.chmod(WATCHER, 0o750)

        subprocess.run([PYTHON, '-m', 'py_compile', str(BOT)], check=True)
        subprocess.run([PYTHON, '-m', 'py_compile', str(WATCHER)], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-leaf-charger-watcher.timer'], check=True)
    except Exception:
        shutil.copy2(bot_backup, BOT)
        shutil.copy2(watcher_backup, WATCHER)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=False)
        subprocess.run(['systemctl', 'restart', 'trc-leaf-charger-watcher.timer'], check=False)
        raise

    print('PATCH_OK')
    print('Leaf status now shows the verified Pandora SOC age instead of stale/None')
    print('Bot backup:', bot_backup)
    print('Watcher backup:', watcher_backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
