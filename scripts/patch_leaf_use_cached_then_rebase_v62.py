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
REBASE = BASE / 'leaf_percent_target_rebase.py'
ENV_FILE = BASE / 'telegram_gate_bot.env'
PYTHON = '/opt/trc-tuya/venv/bin/python3'
MARKER = 'LEAF_CACHED_SOC_REBASE_V62'


def replace_function(source: str, name: str, replacement: str) -> str:
    start = source.find(f'def {name}(')
    if start < 0:
        raise RuntimeError(f'Function {name} not found')
    end = source.find('\ndef ', start + 1)
    if end < 0:
        raise RuntimeError(f'End of function {name} not found')
    return source[:start] + replacement.rstrip() + '\n\n' + source[end + 1:]


def insert_before_function(source: str, name: str, block: str) -> str:
    pos = source.find(f'def {name}(')
    if pos < 0:
        raise RuntimeError(f'Anchor function {name} not found')
    return source[:pos] + block.rstrip() + '\n\n\n' + source[pos:]


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


def patch_auto(source: str) -> str:
    # v61 may already have force support. Do not insert it twice.
    if 'LEAF_PANDORA_FORCE_WAKE' in source:
        return source

    now_anchor = '    now = int(time.time())\n'
    if now_anchor not in source:
        raise RuntimeError('Auto-wakeup timestamp anchor not found')
    source = source.replace(
        now_anchor,
        now_anchor + "    force_wake = os.environ.get('LEAF_PANDORA_FORCE_WAKE', '').strip() == '1'\n",
        1,
    )

    replacements = [
        (
            "    if not target.get('enabled') or target.get('mode') != 'percent':",
            "    if not force_wake and (not target.get('enabled') or target.get('mode') != 'percent'):",
        ),
        (
            "    if leaf.get('switch') is not True or str(leaf.get('charging_state', '')).upper() != 'CHARGING':",
            "    if not force_wake and (leaf.get('switch') is not True or str(leaf.get('charging_state', '')).upper() != 'CHARGING'):",
        ),
        (
            '    if last and now - last < interval:',
            '    if not force_wake and last and now - last < interval:',
        ),
    ]
    for old, new in replacements:
        if old not in source:
            raise RuntimeError(f'Auto-wakeup guard not found: {old.strip()}')
        source = source.replace(old, new, 1)

    return source


def patch_bot(source: str) -> str:
    if MARKER in source:
        return source

    helper = r'''# LEAF_CACHED_SOC_REBASE_V62
LEAF_PERCENT_REBASE_SCRIPT = "/opt/trc-tuya/leaf_percent_target_rebase.py"
PANDORA_VERIFIED_STATE_FILE = "/opt/trc-tuya/pandora_soc_auto_wakeup_state.json"


def load_last_known_pandora_soc():
    # Prefer the value that was positively verified by a charge-flap wake-up,
    # even when it is no longer fresh. This lets charging start immediately.
    verified = load_json(PANDORA_VERIFIED_STATE_FILE, {})
    try:
        soc = verified.get("last_verified_soc")
        verified_at = int(verified.get("last_verified_at") or 0)
        if soc is not None:
            age = max(0.0, (now() - verified_at) / 60.0) if verified_at else None
            return {
                "soc": float(soc),
                "age": round(age, 1) if age is not None else None,
                "status": "fresh" if age is not None and age <= 30 else "stale",
                "source": verified.get("last_verified_source") or "verified_cache",
            }
    except Exception:
        pass

    # Fall back to the combined status and raw Pandora cache.
    for path, source_name in [
        (LEAF_STATUS_FILE, "leaf_status_cache"),
        (PANDORA_SOC_FILE, "pandora_cache"),
    ]:
        data = load_json(path, {})
        soc = data.get("pandora_soc_percent")
        if soc is None:
            continue
        try:
            soc = float(soc)
        except Exception:
            continue
        age = data.get("pandora_age_minutes")
        try:
            age = float(age) if age is not None else None
        except Exception:
            age = None
        return {
            "soc": soc,
            "age": round(age, 1) if age is not None else None,
            "status": str(data.get("pandora_status") or "stale"),
            "source": source_name,
        }

    return None


def start_percent_target_rebase_background():
    try:
        target = load_json(LEAF_TARGET_FILE, {"enabled": False})
        if not (
            target.get("enabled")
            and target.get("mode") == "percent"
            and target.get("needs_pandora_rebase")
        ):
            return False

        subprocess.Popen(
            [PYTHON, LEAF_PERCENT_REBASE_SCRIPT],
            stdout=open("/opt/trc-tuya/leaf_percent_target_rebase.log", "ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return True
    except Exception:
        return False
'''
    source = insert_before_function(source, 'show_leaf_percent_menu', helper)

    show_menu = r'''def show_leaf_percent_menu(chat_id, message_id=None):
    # Use the last known Pandora SOC immediately. A fresh value will be obtained
    # after the relay is ON and the target kWh will then be corrected.
    pandora = load_last_known_pandora_soc()

    if pandora:
        soc = pandora["soc"]
        age = pandora.get("age")
        status = str(pandora.get("status") or "stale")

        if age is None:
            age_text = "давность неизвестна"
        else:
            age_text = f"{round(float(age), 1)} мин назад"

        if status == "fresh":
            freshness_text = f"Подтверждено: <b>{h(age_text)}</b>"
        else:
            freshness_text = (
                f"Последнее сохранённое значение: <b>{h(age_text)}</b>\n"
                "После включения зарядки Pandora обновится автоматически, "
                "а расчёт kWh будет скорректирован."
            )

        text = (
            "🔋 <b>Зарядка до выбранного процента</b>\n\n"
            f"Текущий известный заряд Leaf: <b>{h(format_percent_number(soc))}%</b>\n"
            f"{freshness_text}\n\n"
            "Выберите желаемый уровень заряда или введите его вручную:"
        )
        keyboard = leaf_percent_target_from_pandora_keyboard(soc)
    else:
        text = (
            "🔋 <b>Зарядка до выбранного процента</b>\n\n"
            "Сохранённый заряд Pandora отсутствует.\n"
            "Укажите текущий заряд автомобиля вручную:"
        )
        keyboard = leaf_percent_current_keyboard()

    if message_id is None:
        return send_message(chat_id, text, keyboard)
    return edit_message(chat_id, message_id, text, keyboard)'''
    source = replace_function(source, 'show_leaf_percent_menu', show_menu)

    create_target = r'''def create_charge_target_by_percent(current_percent, target_percent, chat_id):
    status = read_leaf_status()

    if not status.get("ok"):
        return False, "Cannot read Leaf charger status."

    try:
        current_percent = float(current_percent)
        target_percent = float(target_percent)
    except Exception:
        return False, "Bad percent values."

    if target_percent <= current_percent:
        return False, "Target percent must be higher than current percent."

    if current_percent < 0 or current_percent > 100 or target_percent < 0 or target_percent > 100:
        return False, "Percent values must be between 0 and 100."

    # LeafSpy reference supplied by the owner on 2026-07-27:
    # estimated usable capacity 54.5 kWh, SOH 98.13%.
    battery_kwh = env_float("LEAF_BATTERY_KWH", 54.5)
    efficiency = env_float("LEAF_CHARGE_EFFICIENCY", 0.88)
    if efficiency <= 0 or efficiency > 1.0:
        efficiency = 0.88

    wall_kwh_per_percent = battery_kwh / 100.0 / efficiency
    battery_needed_kwh = battery_kwh * ((target_percent - current_percent) / 100.0)
    wall_needed_kwh = battery_needed_kwh / efficiency
    cached = load_last_known_pandora_soc()

    data = {
        "enabled": True,
        "mode": "percent",
        "current_percent": current_percent,
        "target_percent": target_percent,
        "battery_kwh": battery_kwh,
        "efficiency": efficiency,
        "wall_kwh_per_percent": round(wall_kwh_per_percent, 6),
        "battery_needed_kwh": round(battery_needed_kwh, 2),
        "target_add_kwh": round(wall_needed_kwh, 2),
        "raw_wall_needed_kwh": round(wall_needed_kwh, 2),
        "safety_max_add_kwh": round(wall_needed_kwh + max(0.5, wall_kwh_per_percent), 2),
        "start_energy_kwh": status.get("energy_kwh"),
        "created_at": now(),
        "created_by": str(chat_id),
        "added_kwh": 0,
        "remaining_kwh": round(wall_needed_kwh, 2),
        "estimated_percent": current_percent,
        "needs_pandora_rebase": True,
        "rebase_requested_at": now(),
        "initial_soc_source": cached.get("source") if cached else "manual",
        "initial_soc_status": cached.get("status") if cached else "manual",
        "leafspy_reference_capacity_kwh": 54.5,
        "leafspy_reference_soh_percent": 98.13,
    }

    save_json(LEAF_TARGET_FILE, data)
    return True, data'''
    source = replace_function(source, 'create_charge_target_by_percent', create_target)

    run_action = r'''def run_leaf_action(action):
    try:
        p = subprocess.run(
            [LEAF_CONTROL, action],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90
        )

        output = (p.stdout or "").strip()
        error = (p.stderr or "").strip()

        if p.returncode != 0:
            return False, output + ("\n" + error if error else "")

        if action == "on":
            start_percent_target_rebase_background()

        return True, output

    except Exception as e:
        return False, repr(e)'''
    source = replace_function(source, 'run_leaf_action', run_action)

    return source


def rebase_script() -> str:
    return r'''#!/opt/trc-tuya/venv/bin/python3
import fcntl
import html
import json
import os
import subprocess
import time
from pathlib import Path

import requests

BASE = Path('/opt/trc-tuya')
TARGET = BASE / 'leaf_charge_target.json'
LEAF_STATUS = Path('/var/www/html/trc/leaf_status.json')
VERIFIED = BASE / 'pandora_soc_auto_wakeup_state.json'
AUTO = BASE / 'pandora_soc_auto_wakeup.py'
WATCHER = BASE / 'leaf_charger_watcher.py'
ENV_FILE = BASE / 'telegram_gate_bot.env'
LOCK = BASE / 'leaf_percent_target_rebase.lock'
PYTHON = '/opt/trc-tuya/venv/bin/python3'


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path, data):
    path = Path(path)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def load_env(path):
    out = {}
    try:
        for raw in Path(path).read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def fnum(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def send_message(chat_id, text):
    env = load_env(ENV_FILE)
    token = env.get('TELEGRAM_BOT_TOKEN', '').strip()
    if not token or not chat_id:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={
                'chat_id': str(chat_id),
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
            },
            timeout=15,
        )
    except Exception:
        pass


def wait_until_charging(timeout_seconds=100):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        data = load_json(LEAF_STATUS, {})
        current = abs(fnum(data.get('current_a')))
        state = str(data.get('charging_state') or '').upper()
        switch_on = data.get('switch') is True
        if switch_on and (current >= 0.5 or state == 'CHARGING'):
            return True
        time.sleep(5)
    return False


def main():
    LOCK.touch(exist_ok=True)
    with LOCK.open('r+', encoding='utf-8') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return

        target = load_json(TARGET, {})
        if not (
            target.get('enabled')
            and target.get('mode') == 'percent'
            and target.get('needs_pandora_rebase')
        ):
            return

        requested_at = int(target.get('rebase_requested_at') or target.get('created_at') or 0)
        chat_id = target.get('created_by')

        # The cached SOC has already been used to start charging. Now wait until
        # current actually flows, wake the Leaf, and obtain a new verified SOC.
        wait_until_charging()

        completed = subprocess.run(
            [PYTHON, str(AUTO)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=260,
            env={**os.environ, 'LEAF_PANDORA_FORCE_WAKE': '1'},
        )

        verified = load_json(VERIFIED, {})
        verified_at = int(verified.get('last_verified_at') or 0)
        verified_soc = verified.get('last_verified_soc')

        target = load_json(TARGET, {})
        if not target.get('enabled') or target.get('mode') != 'percent':
            return

        if (
            completed.returncode != 0
            or verified_soc is None
            or not verified_at
            or verified_at < requested_at
        ):
            target['last_rebase_ok'] = False
            target['last_rebase_at'] = int(time.time())
            target['last_rebase_error'] = (
                completed.stdout + '\n' + completed.stderr
            )[-1000:]
            save_json(TARGET, target)
            send_message(
                chat_id,
                '⚠️ <b>Цель зарядки запущена по сохранённому SOC</b>\n\n'
                'Pandora пока не подтвердила новый процент. '
                'Расчёт kWh будет скорректирован при следующем успешном обновлении.',
            )
            return

        leaf = load_json(LEAF_STATUS, {})
        current_energy = fnum(leaf.get('energy_kwh'), fnum(target.get('last_energy_kwh')))
        start_energy = fnum(target.get('start_energy_kwh'), current_energy)
        added_kwh = max(0.0, current_energy - start_energy)

        target_percent = fnum(target.get('target_percent'))
        old_start_percent = fnum(target.get('current_percent'))
        old_target_kwh = fnum(target.get('target_add_kwh'))
        fresh_soc = fnum(verified_soc)

        factor = fnum(target.get('wall_kwh_per_percent'))
        if factor <= 0:
            battery_kwh = fnum(target.get('battery_kwh'), 54.5)
            efficiency = fnum(target.get('efficiency'), 0.88)
            if efficiency <= 0:
                efficiency = 0.88
            factor = battery_kwh / 100.0 / efficiency

        # Reconstruct the SOC at the original energy-meter start point so the
        # existing added-kWh counter remains continuous after rebasing.
        corrected_start_percent = fresh_soc - (added_kwh / factor if factor > 0 else 0)
        corrected_start_percent = max(0.0, min(100.0, corrected_start_percent))

        remaining_kwh = max(0.0, (target_percent - fresh_soc) * factor)
        corrected_total_kwh = added_kwh + remaining_kwh

        target['initial_percent_before_rebase'] = old_start_percent
        target['initial_target_add_kwh_before_rebase'] = round(old_target_kwh, 2)
        target['current_percent'] = round(corrected_start_percent, 2)
        target['target_add_kwh'] = round(corrected_total_kwh, 2)
        target['raw_wall_needed_kwh'] = round(corrected_total_kwh, 2)
        target['safety_max_add_kwh'] = round(
            corrected_total_kwh + max(0.5, factor), 2
        )
        target['added_kwh'] = round(added_kwh, 2)
        target['remaining_kwh'] = round(remaining_kwh, 2)
        target['estimated_percent'] = round(fresh_soc, 1)
        target['pandora_soc_last'] = round(fresh_soc, 1)
        target['pandora_soc_last_at'] = verified_at
        target['needs_pandora_rebase'] = False
        target['last_rebase_ok'] = True
        target['last_rebase_at'] = int(time.time())
        target['rebased_verified_at'] = verified_at
        target['rebased_soc'] = round(fresh_soc, 1)
        target['rebased_source'] = verified.get('last_verified_source') or 'charge_flap_wakeup'
        save_json(TARGET, target)

        # Run the watcher once more with the corrected target.
        subprocess.run(
            [PYTHON, str(WATCHER)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=160,
            check=False,
        )

        delta = round(fresh_soc - old_start_percent, 1)
        sign = '+' if delta > 0 else ''
        send_message(
            chat_id,
            '✅ <b>Pandora обновлена — цель скорректирована</b>\n\n'
            f'Было принято при запуске: <b>{old_start_percent:g}%</b>\n'
            f'Подтверждено после начала зарядки: <b>{fresh_soc:g}%</b> '
            f'({sign}{delta:g} п.п.)\n\n'
            f'Старый расчёт: <b>{old_target_kwh:.2f} kWh</b>\n'
            f'Скорректированный общий лимит: <b>{corrected_total_kwh:.2f} kWh</b>\n'
            f'Уже добавлено: <b>{added_kwh:.2f} kWh</b>\n'
            f'Осталось: <b>{remaining_kwh:.2f} kWh</b>\n'
            f'Цель: <b>{target_percent:g}%</b>',
        )


if __name__ == '__main__':
    main()
'''


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
    rebase_new = rebase_script()

    compile_candidate(BOT, bot_new, '.candidate-v62')
    compile_candidate(AUTO, auto_new, '.candidate-v62')
    compile_candidate(REBASE, rebase_new, '.candidate-v62')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backups = []
    for path in (BOT, AUTO, REBASE, ENV_FILE):
        if path.exists():
            backup = path.with_name(path.name + f'.before-v62.{stamp}')
            shutil.copy2(path, backup)
            backups.append((path, backup))

    try:
        BOT.write_text(bot_new, encoding='utf-8')
        AUTO.write_text(auto_new, encoding='utf-8')
        REBASE.write_text(rebase_new, encoding='utf-8')
        for path in (BOT, AUTO, REBASE):
            os.chmod(path, 0o750)

        # LeafSpy reference: 54.5 kWh estimated capacity, SOH 98.13%.
        # Keep the existing efficiency setting if the owner has calibrated it.
        env = {}
        if ENV_FILE.exists():
            for raw in ENV_FILE.read_text(encoding='utf-8').splitlines():
                if '=' in raw and not raw.lstrip().startswith('#'):
                    k, v = raw.split('=', 1)
                    env[k.strip()] = v.strip()
        values = {
            'LEAF_BATTERY_KWH': '54.5',
            'LEAF_LEAFSPY_SOH_PERCENT': '98.13',
        }
        if 'LEAF_CHARGE_EFFICIENCY' not in env:
            values['LEAF_CHARGE_EFFICIENCY'] = '0.88'
        set_env(ENV_FILE, values)

        subprocess.run([PYTHON, '-m', 'py_compile', str(BOT)], check=True)
        subprocess.run([PYTHON, '-m', 'py_compile', str(AUTO)], check=True)
        subprocess.run([PYTHON, '-m', 'py_compile', str(REBASE)], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=True)
    except Exception:
        for original, backup in backups:
            shutil.copy2(backup, original)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=False)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=False)
        raise

    print('PATCH_OK')
    print('Cached Pandora SOC is used immediately; fresh SOC rebases target kWh after charging starts')
    print('LeafSpy battery reference: 54.5 kWh, SOH 98.13%')
    print('Rebase worker:', REBASE)
    for _, backup in backups:
        print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
