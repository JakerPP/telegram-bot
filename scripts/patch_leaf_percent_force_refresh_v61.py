#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path('/opt/trc-tuya')
BOT = BASE / 'telegram_gate_bot.py'
AUTO = BASE / 'pandora_soc_auto_wakeup.py'
PYTHON = '/opt/trc-tuya/venv/bin/python3'
MARKER = 'LEAF_PERCENT_FORCE_REFRESH_V61'


def replace_function(source: str, function_name: str, replacement: str) -> str:
    start_token = f'def {function_name}('
    start = source.find(start_token)
    if start < 0:
        raise RuntimeError(f'Function {function_name} was not found')

    end = source.find('\ndef ', start + len(start_token))
    if end < 0:
        raise RuntimeError(f'Could not find end of {function_name}')

    return source[:start] + replacement.rstrip() + '\n\n' + source[end + 1:]


def patch_auto(source: str) -> str:
    if MARKER in source:
        return source

    now_anchor = '    now = int(time.time())\n'
    if now_anchor not in source:
        raise RuntimeError('Could not find auto-wakeup timestamp anchor')

    source = source.replace(
        now_anchor,
        now_anchor
        + "    # LEAF_PERCENT_FORCE_REFRESH_V61\n"
        + "    force_wake = os.environ.get('LEAF_PANDORA_FORCE_WAKE', '').strip() == '1'\n",
        1,
    )

    old_target = "    if not target.get('enabled') or target.get('mode') != 'percent':"
    new_target = "    if not force_wake and (not target.get('enabled') or target.get('mode') != 'percent'):"
    if old_target not in source:
        raise RuntimeError('Could not find active-target guard in auto-wakeup script')
    source = source.replace(old_target, new_target, 1)

    old_charging = "    if leaf.get('switch') is not True or str(leaf.get('charging_state', '')).upper() != 'CHARGING':"
    new_charging = "    if not force_wake and (leaf.get('switch') is not True or str(leaf.get('charging_state', '')).upper() != 'CHARGING'):"
    if old_charging not in source:
        raise RuntimeError('Could not find charging guard in auto-wakeup script')
    source = source.replace(old_charging, new_charging, 1)

    old_due = '    if last and now - last < interval:'
    new_due = '    if not force_wake and last and now - last < interval:'
    if old_due not in source:
        raise RuntimeError('Could not find interval guard in auto-wakeup script')
    source = source.replace(old_due, new_due, 1)

    return source


def patch_bot(source: str) -> str:
    if MARKER in source:
        return source

    replacement = r'''def show_leaf_percent_menu(chat_id, message_id=None):
    # LEAF_PERCENT_FORCE_REFRESH_V61
    pandora = load_pandora_soc_for_target(max_age_minutes=30)
    output_message_id = message_id
    refresh_error = None

    # A historical stale SOC may still be displayed in the status card. Before
    # asking the user for a manual value, wake the Leaf and request a verified
    # SOC from Pandora automatically.
    if not pandora:
        progress_text = (
            "🔄 <b>Обновляю заряд Pandora</b>\n\n"
            "Пробуждаю Leaf через Pandora и запрашиваю свежий SOC.\n"
            "Обычно это занимает около минуты…"
        )

        if output_message_id is None:
            try:
                response = send_message(chat_id, progress_text)
                output_message_id = (
                    response.get("result", {}).get("message_id")
                    if isinstance(response, dict)
                    else None
                )
            except Exception:
                output_message_id = None
        else:
            edit_message(chat_id, output_message_id, progress_text)

        try:
            completed = subprocess.run(
                [PYTHON, "/opt/trc-tuya/pandora_soc_auto_wakeup.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=210,
                env={**os.environ, "LEAF_PANDORA_FORCE_WAKE": "1"},
            )
            if completed.returncode != 0:
                refresh_error = (completed.stdout + "\n" + completed.stderr)[-700:]
        except Exception as exc:
            refresh_error = repr(exc)

        pandora = load_pandora_soc_for_target(max_age_minutes=30)

    if pandora:
        soc = pandora["soc"]
        age = pandora["age"]
        text = (
            "🔋 <b>Зарядка до выбранного процента</b>\n\n"
            f"Текущий заряд Leaf по Pandora: <b>{h(format_percent_number(soc))}%</b>\n"
            f"Подтверждено: <b>{h(round(float(age), 1))} мин назад</b>\n\n"
            "Выберите желаемый уровень заряда или введите его вручную:"
        )
        keyboard = leaf_percent_target_from_pandora_keyboard(soc)
    else:
        text = (
            "🔋 <b>Зарядка до выбранного процента</b>\n\n"
            "Pandora не смогла подтвердить свежий заряд после автоматического пробуждения.\n"
            "Укажите текущий заряд автомобиля вручную:"
        )
        if refresh_error:
            text += "\n\n<code>" + h(refresh_error[-400:]) + "</code>"
        keyboard = leaf_percent_current_keyboard()

    if output_message_id is not None:
        return edit_message(chat_id, output_message_id, text, keyboard)
    return send_message(chat_id, text, keyboard)'''

    source = replace_function(source, 'show_leaf_percent_menu', replacement)

    # Ensure the ordinary Leaf status card shows the age from the verified wake
    # state, never the misleading string "None min ago".
    start = source.find('def format_leaf_status(')
    if start < 0:
        raise RuntimeError('format_leaf_status was not found')
    end = source.find('\ndef ', start + 1)
    if end < 0:
        raise RuntimeError('Could not find end of format_leaf_status')

    segment = source[start:end]
    anchor = '    data = read_leaf_status()\n'
    if anchor not in segment:
        raise RuntimeError('Could not find data load in format_leaf_status')

    overlay = r'''    data = read_leaf_status()

    try:
        verified_state = load_json("/opt/trc-tuya/pandora_soc_auto_wakeup_state.json", {})
        verified_at = int(verified_state.get("last_verified_at") or 0)
        verified_soc = verified_state.get("last_verified_soc")
        if verified_at and verified_soc is not None:
            verified_age = max(0.0, (now() - verified_at) / 60.0)
            data = dict(data)
            data["pandora_soc_percent"] = float(verified_soc)
            data["pandora_age_minutes"] = round(verified_age, 1)
            data["pandora_status"] = "fresh" if verified_age <= 30 else "stale"
    except Exception:
        pass
'''
    segment = segment.replace(anchor, overlay, 1)
    source = source[:start] + segment + source[end:]

    return source


def compile_candidate(path: Path, content: str, suffix: str):
    candidate = path.with_name(path.name + suffix)
    candidate.write_text(content, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(candidate)], check=True)
    finally:
        candidate.unlink(missing_ok=True)


def main():
    for path in (BOT, AUTO):
        if not path.exists():
            raise RuntimeError(f'Missing {path}')

    bot_old = BOT.read_text(encoding='utf-8')
    auto_old = AUTO.read_text(encoding='utf-8')
    bot_new = patch_bot(bot_old)
    auto_new = patch_auto(auto_old)

    if bot_new == bot_old and auto_new == auto_old:
        print('PATCH_ALREADY_APPLIED')
        return

    compile_candidate(BOT, bot_new, '.candidate-v61')
    compile_candidate(AUTO, auto_new, '.candidate-v61')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    bot_backup = BOT.with_name(BOT.name + f'.before-v61.{stamp}')
    auto_backup = AUTO.with_name(AUTO.name + f'.before-v61.{stamp}')
    shutil.copy2(BOT, bot_backup)
    shutil.copy2(AUTO, auto_backup)

    try:
        BOT.write_text(bot_new, encoding='utf-8')
        AUTO.write_text(auto_new, encoding='utf-8')
        os.chmod(BOT, 0o750)
        os.chmod(AUTO, 0o750)

        subprocess.run([PYTHON, '-m', 'py_compile', str(BOT)], check=True)
        subprocess.run([PYTHON, '-m', 'py_compile', str(AUTO)], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=True)
    except Exception:
        shutil.copy2(bot_backup, BOT)
        shutil.copy2(auto_backup, AUTO)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=False)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=False)
        raise

    print('PATCH_OK')
    print('Percentage menu now wakes Leaf and refreshes Pandora SOC automatically')
    print('Status card now shows a real verified age instead of None')
    print('Bot backup:', bot_backup)
    print('Auto-wakeup backup:', auto_backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
