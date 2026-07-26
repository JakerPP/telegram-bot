#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BOT = Path('/opt/trc-tuya/telegram_gate_bot.py')
PYTHON = '/opt/trc-tuya/venv/bin/python3'
MARKER = 'LEAF_BOT_V58_VERIFIED_SOC'


def replace_function(source: str, function_name: str, replacement: str) -> str:
    start_token = f'def {function_name}('
    start = source.find(start_token)
    if start < 0:
        raise RuntimeError(f'Function {function_name} was not found')
    next_def = source.find('\ndef ', start + len(start_token))
    if next_def < 0:
        raise RuntimeError(f'Could not find end of {function_name}')
    return source[:start] + replacement.rstrip() + '\n\n' + source[next_def + 1:]


def main():
    if not BOT.exists():
        raise RuntimeError(f'Missing {BOT}')

    original = BOT.read_text(encoding='utf-8')
    if MARKER in original:
        print('PATCH_ALREADY_APPLIED')
        return

    source = original

    replacement = r'''def load_pandora_soc_for_target(max_age_minutes=30):
    # LEAF_BOT_V58_VERIFIED_SOC
    # 1) A successful charge-flap wake-up is the strongest source because it
    #    proves the Leaf was awake when Pandora returned the SOC.
    wake_state = load_json('/opt/trc-tuya/pandora_soc_auto_wakeup_state.json', {})
    try:
        verified_at = int(wake_state.get('last_verified_at') or 0)
        verified_soc = wake_state.get('last_verified_soc')
        verified_age = max(0.0, (now() - verified_at) / 60.0) if verified_at else None
        if (
            verified_soc is not None
            and verified_age is not None
            and verified_age <= float(max_age_minutes)
        ):
            return {
                'soc': float(verified_soc),
                'age': round(verified_age, 1),
                'status': 'fresh',
                'source': 'charge_flap_wakeup',
            }
    except Exception:
        pass

    # 2) Fall back to the combined public status and then the raw Pandora file.
    candidates = [
        load_json(LEAF_STATUS_FILE, {}),
        load_json(PANDORA_SOC_FILE, {}),
    ]

    for data in candidates:
        soc = data.get('pandora_soc_percent')
        status = data.get('pandora_status')
        age = data.get('pandora_age_minutes')

        if soc is None:
            continue

        try:
            soc = float(soc)
            age_float = float(age)
        except Exception:
            continue

        if status == 'fresh' and age_float <= float(max_age_minutes):
            return {
                'soc': soc,
                'age': round(age_float, 1),
                'status': status,
                'source': 'status_file',
            }

    return None'''
    source = replace_function(source, 'load_pandora_soc_for_target', replacement)

    # Add useful command aliases before the percent-command block inserted by v57.
    alias_anchor = '    if command in ["/charge_by_percent", "/charge_percent", "/leaf_percent"]:'
    if alias_anchor not in source:
        raise RuntimeError('v57 percent command block was not found')

    alias_block = r'''    if command in ["/leaf_status", "/charger_status", "/leafcharger"]:
        if not is_authorized(chat_id):
            send_message(chat_id, whoami_text(chat_id), request_access_keyboard())
            return
        send_message(chat_id, format_leaf_status(), leaf_keyboard())
        return

'''
    source = source.replace(alias_anchor, alias_block + alias_anchor, 1)

    # Do not leak the bot token in HTTP exception messages written to journal.
    old_telegram = '''    r = requests.post(f"{API}/{method}", json=payload, timeout=timeout)\n    r.raise_for_status()\n    return r.json()'''
    new_telegram = '''    r = requests.post(f"{API}/{method}", json=payload, timeout=timeout)\n    if r.status_code >= 400:\n        try:\n            detail = r.json().get("description", r.text[:500])\n        except Exception:\n            detail = r.text[:500]\n        raise RuntimeError(f"Telegram {method} HTTP {r.status_code}: {detail}")\n    return r.json()'''
    if old_telegram in source:
        source = source.replace(old_telegram, new_telegram, 1)

    # Make the fallback Russian too.
    source = source.replace(
        '"Use /panel for main menu.\\nUse /leaf for Leaf charger.\\nUse /status for gate status."',
        '"Используйте /menu для главного меню.\\n/leaf — панель Leaf.\\n/leaf_status — статус зарядки.\\n/charge_by_percent — зарядка до выбранного процента."',
    )

    candidate = BOT.with_name(BOT.name + '.candidate-v58')
    candidate.write_text(source, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(candidate)], check=True)
    finally:
        candidate.unlink(missing_ok=True)

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = BOT.with_name(BOT.name + f'.before-v58.{stamp}')
    shutil.copy2(BOT, backup)

    try:
        BOT.write_text(source, encoding='utf-8')
        os.chmod(BOT, 0o750)
        subprocess.run([PYTHON, '-m', 'py_compile', str(BOT)], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=True)
    except Exception:
        shutil.copy2(backup, BOT)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=False)
        raise

    print('PATCH_OK')
    print('Bot now reads verified wake-up SOC directly and supports /leaf_status aliases')
    print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
