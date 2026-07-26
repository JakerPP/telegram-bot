#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BOT = Path('/opt/trc-tuya/telegram_gate_bot.py')
PYTHON = '/opt/trc-tuya/venv/bin/python3'
MARKER = 'ASTERISK_CALLBACK_MERGED_V59'


def insert_before(source: str, anchor: str, block: str) -> str:
    idx = source.find(anchor)
    if idx < 0:
        raise RuntimeError(f'Anchor not found: {anchor}')
    return source[:idx] + block.rstrip() + '\n\n' + source[idx:]


def main():
    if not BOT.exists():
        raise RuntimeError(f'Missing {BOT}')

    original = BOT.read_text(encoding='utf-8')
    if MARKER in original:
        print('PATCH_ALREADY_APPLIED')
        return

    source = original

    # The main bot does not currently import re.
    if '\nimport re\n' not in source:
        source = source.replace('\nimport os\n', '\nimport os\nimport re\n', 1)

    helpers = r'''# ASTERISK_CALLBACK_MERGED_V59
ASTERISK_CALLBACK_ENV = "/etc/asterisk/telegram.env"


def load_asterisk_callback_config():
    env = load_env(ASTERISK_CALLBACK_ENV)
    return {
        "chat_id": str(env.get("CHAT_ID", "")).strip(),
        "callback_to": str(env.get("CALLBACK_TO", "")).strip(),
    }


def normalize_callback_number(value):
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) == 10:
        digits = "1" + digits
    if re.fullmatch(r"[1-9][0-9]{2,14}", digits):
        return digits
    return None


def callback_channel(value):
    v = str(value or "").strip().lower()

    if v in ("tg100", "gsm", "sim"):
        return "PJSIP/s@tg100"

    digits = normalize_callback_number(v)
    if not digits:
        return None

    return f"PJSIP/{digits}@zadarma_endpoint"


def asterisk_rx(command):
    p = subprocess.run(
        ["/usr/sbin/asterisk", "-rx", command],
        text=True,
        capture_output=True,
        timeout=20,
    )
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def originate_callback(target):
    cfg = load_asterisk_callback_config()
    chan = callback_channel(cfg.get("callback_to"))
    if not chan:
        return 1, "", "Некорректный CALLBACK_TO в /etc/asterisk/telegram.env"

    command = f"channel originate {chan} extension {target}@callback-out"
    return asterisk_rx(command)


def callback_command_allowed(chat_id):
    cfg = load_asterisk_callback_config()
    configured_chat_id = cfg.get("chat_id")
    if configured_chat_id and str(chat_id) != configured_chat_id:
        return False
    return is_admin(chat_id)
'''

    source = insert_before(source, 'def handle_message(', helpers)

    command_block = r'''    if command in ["/callback_help", "/call_help"]:
        if not callback_command_allowed(chat_id):
            send_message(chat_id, "❌ Нет доступа к callback-командам.")
            return
        cfg = load_asterisk_callback_config()
        send_message(
            chat_id,
            "☎️ <b>Callback-команды</b>\n\n"
            "/call 1647XXXXXXX — сначала позвонить на ваш номер, затем соединить с указанным номером\n"
            "/asterisk_status — показать контакты Asterisk\n\n"
            f"Номер обратного вызова: <code>{h(cfg.get('callback_to'))}</code>",
            main_menu_keyboard(),
        )
        return

    if command == "/asterisk_status":
        if not callback_command_allowed(chat_id):
            send_message(chat_id, "❌ Нет доступа к Asterisk.")
            return
        code, out, err = asterisk_rx("pjsip show contacts")
        body = out or err or "Нет ответа от Asterisk"
        send_message(
            chat_id,
            ("✅ <b>Asterisk contacts</b>\n\n" if code == 0 else "❌ <b>Asterisk error</b>\n\n")
            + "<code>" + h(body[:3500]) + "</code>",
            main_menu_keyboard(),
        )
        return

    if command == "/call":
        if not callback_command_allowed(chat_id):
            send_message(chat_id, "❌ Нет доступа к callback-командам.")
            return

        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_message(chat_id, "Используйте: <code>/call 1647XXXXXXX</code>")
            return

        target = normalize_callback_number(parts[1])
        if not target:
            send_message(chat_id, "❌ Некорректный номер. Пример: <code>/call 1647XXXXXXX</code>")
            return

        cfg = load_asterisk_callback_config()
        send_message(
            chat_id,
            "☎️ <b>Запускаю callback</b>\n\n"
            f"Сначала звонок на: <code>{h(cfg.get('callback_to'))}</code>\n"
            f"Затем соединение с: <code>{h(target)}</code>",
        )

        code, out, err = originate_callback(target)
        if code == 0:
            send_message(
                chat_id,
                "✅ <b>Callback-команда отправлена</b>\n\n"
                f"Номер: <code>{h(target)}</code>",
                main_menu_keyboard(),
            )
        else:
            send_message(
                chat_id,
                "❌ <b>Не удалось запустить callback</b>\n\n"
                + "<code>" + h((out + "\n" + err)[-3000:]) + "</code>",
                main_menu_keyboard(),
            )
        return

'''

    # Insert after command extraction in handle_message.
    handle_start = source.find('def handle_message(')
    if handle_start < 0:
        raise RuntimeError('handle_message not found')
    handle_end = source.find('\ndef ', handle_start + 1)
    segment = source[handle_start:handle_end]
    anchor = '    command = text.split()[0].split("@", 1)[0].lower() if text else ""\n'
    if anchor not in segment:
        raise RuntimeError('command parsing anchor not found in handle_message')
    segment = segment.replace(anchor, anchor + '\n' + command_block, 1)
    source = source[:handle_start] + segment + source[handle_end:]

    # Extend help fallback in Russian.
    source = source.replace(
        '"Используйте /menu для главного меню.\\n/leaf — панель Leaf.\\n/leaf_status — статус зарядки.\\n/charge_by_percent — зарядка до выбранного процента."',
        '"Используйте /menu для главного меню.\\n/leaf — панель Leaf.\\n/leaf_status — статус зарядки.\\n/charge_by_percent — зарядка до выбранного процента.\\n/call — callback через Asterisk.\\n/asterisk_status — статус Asterisk."',
    )

    candidate = BOT.with_name(BOT.name + '.candidate-v59')
    candidate.write_text(source, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(candidate)], check=True)
    finally:
        candidate.unlink(missing_ok=True)

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = BOT.with_name(BOT.name + f'.before-v59.{stamp}')
    shutil.copy2(BOT, backup)

    try:
        BOT.write_text(source, encoding='utf-8')
        os.chmod(BOT, 0o750)
        subprocess.run([PYTHON, '-m', 'py_compile', str(BOT)], check=True)
        subprocess.run(['systemctl', 'disable', '--now', 'telegram-callback-bot.service'], check=False)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=True)
    except Exception:
        shutil.copy2(backup, BOT)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=False)
        raise

    print('PATCH_OK')
    print('Merged /call and /asterisk_status into the main Telegram bot')
    print('Disabled local telegram-callback-bot.service')
    print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
