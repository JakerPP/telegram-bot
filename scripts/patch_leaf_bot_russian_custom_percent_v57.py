#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BOT = Path('/opt/trc-tuya/telegram_gate_bot.py')
PYTHON = '/opt/trc-tuya/venv/bin/python3'
MARKER = 'LEAF_PERCENT_UI_V57'


def replace_function(source: str, function_name: str, replacement: str) -> str:
    start_token = f'def {function_name}('
    start = source.find(start_token)
    if start < 0:
        raise RuntimeError(f'Function {function_name} was not found')

    next_def = source.find('\ndef ', start + len(start_token))
    if next_def < 0:
        raise RuntimeError(f'Could not find the end of {function_name}')

    return source[:start] + replacement.rstrip() + '\n\n' + source[next_def + 1:]


def insert_after_text_assignment(source: str, block: str) -> str:
    start = source.find('def handle_message(')
    if start < 0:
        raise RuntimeError('handle_message was not found')

    end = source.find('\ndef ', start + 1)
    if end < 0:
        raise RuntimeError('Could not find end of handle_message')

    segment = source[start:end]
    lines = segment.splitlines(keepends=True)
    insert_at = None

    for i, line in enumerate(lines[:50]):
        stripped = line.strip()
        if stripped.startswith('text ='):
            insert_at = i + 1
            break

    if insert_at is None:
        raise RuntimeError('Text assignment was not found in handle_message')

    lines.insert(insert_at, '\n' + block.rstrip() + '\n')
    return source[:start] + ''.join(lines) + source[end:]


def insert_before_unknown_button(source: str, block: str) -> str:
    start = source.find('def handle_callback(')
    if start < 0:
        raise RuntimeError('handle_callback was not found')

    end = source.find('\ndef ', start + 1)
    if end < 0:
        end = len(source)

    segment = source[start:end]
    candidates = [
        'edit_message(chat_id, message_id, "Unknown button:',
        "edit_message(chat_id, message_id, 'Unknown button:",
    ]

    pos = -1
    for token in candidates:
        pos = segment.find(token)
        if pos >= 0:
            break

    if pos < 0:
        raise RuntimeError('Unknown-button fallback was not found')

    absolute = start + pos
    line_start = source.rfind('\n', start, absolute) + 1
    return source[:line_start] + block.rstrip() + '\n\n' + source[line_start:]


def main():
    if not BOT.exists():
        raise RuntimeError(f'Missing {BOT}')

    original = BOT.read_text(encoding='utf-8')
    if MARKER in original:
        print('PATCH_ALREADY_APPLIED')
        return

    source = original

    # Persistent pending input file.
    constant_anchor = 'PANDORA_NOTIFY_SCRIPT = "/opt/trc-tuya/pandora_refresh_soc_notify.py"'
    if 'LEAF_PENDING_INPUT_FILE' not in source:
        if constant_anchor in source:
            source = source.replace(
                constant_anchor,
                constant_anchor + '\nLEAF_PENDING_INPUT_FILE = "/opt/trc-tuya/leaf_percent_pending_input.json"',
                1,
            )
        else:
            base_anchor = 'BASE_DIR = "/opt/trc-tuya"'
            if base_anchor not in source:
                raise RuntimeError('Could not insert LEAF_PENDING_INPUT_FILE constant')
            source = source.replace(
                base_anchor,
                base_anchor + '\nLEAF_PENDING_INPUT_FILE = "/opt/trc-tuya/leaf_percent_pending_input.json"',
                1,
            )

    load_soc_function = r'''def load_pandora_soc_for_target(max_age_minutes=30):
    # Prefer the public combined Leaf status because the automatic charge-flap
    # wake-up marks a successfully verified SOC there even when Pandora omits
    # its original state timestamp.
    candidates = [
        load_json(LEAF_STATUS_FILE, {}),
        load_json(PANDORA_SOC_FILE, {}),
    ]

    for data in candidates:
        soc = data.get("pandora_soc_percent")
        status = data.get("pandora_status")
        age = data.get("pandora_age_minutes")

        if soc is None:
            continue

        try:
            soc = float(soc)
            age_float = float(age)
        except Exception:
            continue

        if status == "fresh" and age_float <= float(max_age_minutes):
            return {
                "soc": soc,
                "age": age_float,
                "status": status,
            }

    return None'''
    source = replace_function(source, 'load_pandora_soc_for_target', load_soc_function)

    manual_target_keyboard = r'''def leaf_percent_target_keyboard(current):
    rows = []
    row = []

    for pct in [60, 70, 80, 90, 100]:
        if float(pct) <= float(current):
            continue

        row.append({
            "text": f"{pct}%",
            "callback_data": f"leaf_target_pct_{current}_{pct}",
        })

        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        {
            "text": "✍️ Ввести желаемый % вручную",
            "callback_data": f"leaf_custom_target_from_{current}",
        }
    ])
    rows.append([
        {"text": "⬅️ Изменить текущий заряд", "callback_data": "leaf_percent_manual_menu"}
    ])

    return {"inline_keyboard": rows}'''
    source = replace_function(source, 'leaf_percent_target_keyboard', manual_target_keyboard)

    pandora_target_keyboard = r'''def leaf_percent_target_from_pandora_keyboard(current_percent):
    rows = []
    row = []

    for target in [60, 70, 80, 90, 100]:
        if float(target) <= float(current_percent):
            continue

        row.append({
            "text": f"{target}%",
            "callback_data": f"leaf_start_pct_pandora_{target}",
        })

        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        {
            "text": "✍️ Ввести желаемый % вручную",
            "callback_data": "leaf_custom_target_prompt",
        }
    ])
    rows.append([
        {"text": "🧭 Указать текущий заряд вручную", "callback_data": "leaf_percent_manual_menu"}
    ])
    rows.append([
        {"text": "⬅️ Назад к Leaf", "callback_data": "leaf_panel"}
    ])

    return {"inline_keyboard": rows}'''
    source = replace_function(
        source,
        'leaf_percent_target_from_pandora_keyboard',
        pandora_target_keyboard,
    )

    helpers = r'''# LEAF_PERCENT_UI_V57

def load_leaf_pending_inputs():
    return load_json(LEAF_PENDING_INPUT_FILE, {})


def set_leaf_pending_target(chat_id, current_percent):
    pending = load_leaf_pending_inputs()
    pending[str(chat_id)] = {
        "action": "custom_target_percent",
        "current_percent": float(current_percent),
        "created_at": now(),
    }
    save_json(LEAF_PENDING_INPUT_FILE, pending)


def pop_leaf_pending_target(chat_id):
    pending = load_leaf_pending_inputs()
    value = pending.pop(str(chat_id), None)
    save_json(LEAF_PENDING_INPUT_FILE, pending)
    return value


def get_leaf_pending_target(chat_id):
    pending = load_leaf_pending_inputs()
    value = pending.get(str(chat_id))
    if not value:
        return None

    try:
        if now() - int(value.get("created_at") or 0) > 15 * 60:
            pending.pop(str(chat_id), None)
            save_json(LEAF_PENDING_INPUT_FILE, pending)
            return None
    except Exception:
        return None

    return value


def format_percent_number(value):
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return str(round(value, 1))


def show_leaf_percent_menu(chat_id, message_id=None):
    pandora = load_pandora_soc_for_target(max_age_minutes=30)

    if pandora:
        soc = pandora["soc"]
        age = pandora["age"]
        text = (
            "🔋 <b>Зарядка до выбранного процента</b>\n\n"
            f"Текущий заряд Leaf по Pandora: <b>{h(format_percent_number(soc))}%</b>\n"
            f"Статус Pandora: <b>свежий</b>, {h(round(age, 1))} мин назад\n\n"
            "Выберите желаемый уровень заряда или введите его вручную:"
        )
        keyboard = leaf_percent_target_from_pandora_keyboard(soc)
    else:
        text = (
            "🔋 <b>Зарядка до выбранного процента</b>\n\n"
            "Свежий уровень заряда Pandora сейчас недоступен.\n"
            "Сначала укажите текущий заряд автомобиля:"
        )
        keyboard = leaf_percent_current_keyboard()

    if message_id is None:
        return send_message(chat_id, text, keyboard)
    return edit_message(chat_id, message_id, text, keyboard)'''

    handle_message_anchor = 'def handle_message('
    if helpers not in source:
        pos = source.find(handle_message_anchor)
        if pos < 0:
            raise RuntimeError('Could not insert Leaf percent helpers')
        source = source[:pos] + helpers.rstrip() + '\n\n\n' + source[pos:]

    message_block = r'''    command = text.split()[0].split("@", 1)[0].lower() if text else ""

    if command in ["/charge_by_percent", "/charge_percent", "/leaf_percent"]:
        if not is_authorized(chat_id):
            send_message(chat_id, whoami_text(chat_id), request_access_keyboard())
            return
        show_leaf_percent_menu(chat_id)
        return

    if command == "/cancel" and is_authorized(chat_id):
        if pop_leaf_pending_target(chat_id):
            send_message(chat_id, "❌ Ввод желаемого процента отменён.", leaf_keyboard())
            return

    pending_target = get_leaf_pending_target(chat_id) if is_authorized(chat_id) else None
    if pending_target and text and not text.startswith("/"):
        normalized = text.strip().replace("%", "").replace(",", ".")

        try:
            target_percent = float(normalized)
            current_percent = float(pending_target.get("current_percent"))
        except Exception:
            send_message(
                chat_id,
                "❌ Введите число, например <b>85</b>. Для отмены отправьте /cancel.",
            )
            return

        if target_percent <= current_percent:
            send_message(
                chat_id,
                f"❌ Желаемый заряд должен быть выше текущих <b>{h(format_percent_number(current_percent))}%</b>.",
            )
            return

        if target_percent > 100:
            send_message(chat_id, "❌ Максимальный уровень заряда — <b>100%</b>.")
            return

        ok, result = create_charge_target_by_percent(current_percent, target_percent, chat_id)
        if not ok:
            send_message(chat_id, "❌ " + h(result), leaf_keyboard())
            return

        pop_leaf_pending_target(chat_id)
        on_ok, on_output = run_leaf_action("on")
        send_message(
            chat_id,
            "🔋🎯 <b>Цель зарядки установлена</b>\n\n"
            f"Сейчас: <b>{h(format_percent_number(current_percent))}%</b>\n"
            f"Зарядить до: <b>{h(format_percent_number(target_percent))}%</b>\n"
            f"Расчётно из розетки: <b>{h(result.get('target_add_kwh'))} kWh</b>\n"
            f"Команда включения отправлена: <b>{h(on_ok)}</b>\n\n"
            + format_leaf_status(),
            leaf_keyboard(),
        )
        return'''
    source = insert_after_text_assignment(source, message_block)

    callback_block = r'''    if data == "leaf_target_cancel":
        cancel_leaf_target()
        pop_leaf_pending_target(chat_id)
        edit_message(
            chat_id,
            message_id,
            "❌ <b>Цель зарядки отменена</b>\n\n" + format_leaf_status(),
            leaf_keyboard(),
        )
        return

    if data == "leaf_custom_target_prompt" or data.startswith("leaf_custom_target_from_"):
        if data.startswith("leaf_custom_target_from_"):
            try:
                current_percent = float(data.replace("leaf_custom_target_from_", "", 1))
            except Exception:
                edit_message(chat_id, message_id, "❌ Не удалось определить текущий заряд.", leaf_keyboard())
                return
        else:
            pandora = load_pandora_soc_for_target(max_age_minutes=30)
            if not pandora:
                edit_message(
                    chat_id,
                    message_id,
                    "❌ Свежий заряд Pandora недоступен. Сначала укажите текущий заряд автомобиля:",
                    leaf_percent_current_keyboard(),
                )
                return
            current_percent = float(pandora["soc"])

        set_leaf_pending_target(chat_id, current_percent)
        edit_message(
            chat_id,
            message_id,
            "✍️ <b>Введите желаемый уровень заряда</b>\n\n"
            f"Сейчас: <b>{h(format_percent_number(current_percent))}%</b>\n"
            "Отправьте одним сообщением нужный процент, например <b>85</b>.\n"
            "Для отмены отправьте /cancel.",
            {"inline_keyboard": [[{"text": "⬅️ Назад к выбору", "callback_data": "leaf_percent_menu"}]]},
        )
        return'''
    source = insert_before_unknown_button(source, callback_block)

    replacements = {
        '"✍️ Manual current %"': '"✍️ Ввести желаемый % вручную"',
        '"✍ Manual current %"': '"✍️ Ввести желаемый % вручную"',
        '"🔋 <b>Charge by %</b>': '"🔋 <b>Зарядка до выбранного процента</b>',
        'Current Leaf SOC from Pandora:': 'Текущий заряд Leaf по Pandora:',
        'Pandora status:': 'Статус Pandora:',
        'Choose target battery percent:': 'Выберите, до какого процента зарядить автомобиль:',
        'Pandora SOC is not fresh or not available.': 'Свежий заряд Pandora недоступен.',
        'Choose current Leaf battery percent manually:': 'Укажите текущий заряд автомобиля:',
        '✍️ <b>Manual Charge by %</b>': '✍️ <b>Текущий заряд вручную</b>',
        'Choose current Leaf battery percent from the car display:': 'Выберите текущий заряд по показанию автомобиля:',
        'Current battery:': 'Текущий заряд:',
        'Choose target percent:': 'Выберите желаемый заряд:',
        'Leaf percent charge target set': 'Цель зарядки по процентам установлена',
        'Pandora SOC is not fresh anymore.': 'Данные Pandora больше не являются свежими.',
        'Choose current percent manually:': 'Укажите текущий заряд вручную:',
        '🔄 <b>Pandora SOC refreshed</b>': '🔄 <b>Заряд Pandora обновлён</b>',
        '❌ <b>Pandora refresh failed</b>': '❌ <b>Не удалось обновить заряд Pandora</b>',
        '"🔄 Charger Status"': '"🔄 Статус зарядки"',
        '"🔄 Refresh Pandora SOC"': '"🔄 Обновить заряд Pandora"',
        '"🛑 Charger OFF"': '"🛑 Выключить зарядку"',
        '"🟢 Charger ON"': '"🟢 Включить зарядку"',
        '"🚗 Leaf Panel"': '"🚗 Панель Leaf"',
        '"❌ Cancel Target/Timer"': '"❌ Отменить цель/таймер"',
        'Unknown command. Send /help': 'Неизвестная команда. Откройте /menu или /help.',
    }

    for old, new in replacements.items():
        source = source.replace(old, new)

    # Validate before touching the active file.
    candidate = BOT.with_name(BOT.name + '.candidate-v57')
    candidate.write_text(source, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(candidate)], check=True)
    finally:
        candidate.unlink(missing_ok=True)

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = BOT.with_name(BOT.name + f'.before-v57.{stamp}')
    shutil.copy2(BOT, backup)

    try:
        BOT.write_text(source, encoding='utf-8')
        os.chmod(BOT, 0o750)
        subprocess.run([PYTHON, '-m', 'py_compile', str(BOT)], check=True)

        restarted = []
        units_text = subprocess.run(
            ['systemctl', 'list-unit-files', '--type=service', '--no-legend', '--no-pager'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        ).stdout

        for line in units_text.splitlines():
            unit = line.split()[0] if line.split() else ''
            if not unit:
                continue
            lowered = unit.lower()
            if 'telegram' not in lowered and 'gate' not in lowered and 'bot' not in lowered:
                continue

            cat = subprocess.run(
                ['systemctl', 'cat', unit],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            ).stdout
            if str(BOT) not in cat:
                continue

            subprocess.run(['systemctl', 'restart', unit], check=True)
            restarted.append(unit)

    except Exception:
        shutil.copy2(backup, BOT)
        raise

    print('PATCH_OK')
    print('Fixed /charge_by_percent, leaf_target_cancel, Russian percent UI, and custom target input')
    print('Backup:', backup)
    if restarted:
        print('Restarted:', ', '.join(restarted))
    else:
        print('WARNING: bot service was not auto-detected; restart the process that runs telegram_gate_bot.py')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
