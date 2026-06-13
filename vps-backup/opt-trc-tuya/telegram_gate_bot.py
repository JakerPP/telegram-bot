#!/opt/trc-tuya/venv/bin/python3

import html
import json
import os
import subprocess
import time
import requests

BASE_DIR = "/opt/trc-tuya"
ENV_FILE = "/opt/trc-tuya/telegram_gate_bot.env"
USERS_FILE = "/opt/trc-tuya/telegram_gate_users.json"

STATUS_FILE = "/var/www/html/trc/status.json"
LEAF_STATUS_FILE = "/var/www/html/trc/leaf_status.json"

GATE_CONTROL = "/opt/trc-tuya/gate_control.py"
LEAF_CONTROL = "/opt/trc-tuya/leaf_charger_control.py"

LEAF_TIMER_FILE = "/opt/trc-tuya/leaf_charger_timer.json"
LEAF_TARGET_FILE = "/opt/trc-tuya/leaf_charge_target.json"
PANDORA_SOC_FILE = "/opt/trc-tuya/pandora_leaf_soc.json"
PANDORA_SCRIPT = "/opt/trc-tuya/pandora_leaf_soc.py"
PANDORA_NOTIFY_SCRIPT = "/opt/trc-tuya/pandora_refresh_soc_notify.py"

os.chdir(BASE_DIR)


def h(value):
    return html.escape(str(value))


def now():
    return int(time.time())


def load_env(path):
    data = {}

    if not os.path.exists(path):
        return data

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()

    return data


ENV = load_env(ENV_FILE)
BOT_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "").strip()
LEGACY_ALLOWED_CHAT_ID = str(ENV.get("ALLOWED_CHAT_ID", "0")).strip()

if not BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN is missing in /opt/trc-tuya/telegram_gate_bot.env")

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def env_float(key, default):
    try:
        return float(ENV.get(key, default))
    except Exception:
        return float(default)


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass

    return default


def save_json(path, data, mode=0o600):
    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    os.replace(tmp, path)
    os.chmod(path, mode)


def telegram(method, payload=None, timeout=30):
    if payload is None:
        payload = {}

    r = requests.post(f"{API}/{method}", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def send_message(chat_id, text, keyboard=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if keyboard is not None:
        payload["reply_markup"] = keyboard

    return telegram("sendMessage", payload)


def edit_message(chat_id, message_id, text, keyboard=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if keyboard is not None:
        payload["reply_markup"] = keyboard

    try:
        return telegram("editMessageText", payload)
    except Exception:
        return send_message(chat_id, text, keyboard)


def answer_callback(callback_id, text=""):
    try:
        return telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False
        }, timeout=10)
    except Exception:
        return None


def load_users():
    data = load_json(USERS_FILE, {"users": {}})

    if "users" not in data:
        data["users"] = {}

    if LEGACY_ALLOWED_CHAT_ID and LEGACY_ALLOWED_CHAT_ID != "0":
        if LEGACY_ALLOWED_CHAT_ID not in data["users"]:
            data["users"][LEGACY_ALLOWED_CHAT_ID] = {
                "role": "owner",
                "name": "Owner",
                "username": "",
                "created_at": now(),
                "created_by": "env"
            }
            save_json(USERS_FILE, data)

    return data


def save_users(data):
    save_json(USERS_FILE, data)


def user_count():
    return len(load_users().get("users", {}))


def get_role(chat_id):
    users = load_users().get("users", {})
    info = users.get(str(chat_id))

    if not info:
        return None

    return info.get("role")


def is_admin(chat_id):
    return get_role(chat_id) in ["owner", "admin"]


def is_authorized(chat_id):
    return get_role(chat_id) in ["owner", "admin", "user"]


def add_or_update_user(chat_id, role, name="", username="", created_by="admin"):
    data = load_users()

    data["users"][str(chat_id)] = {
        "role": role,
        "name": name or "",
        "username": username or "",
        "created_at": now(),
        "created_by": str(created_by)
    }

    save_users(data)


def remove_user(chat_id):
    data = load_users()

    if str(chat_id) in data.get("users", {}):
        del data["users"][str(chat_id)]
        save_users(data)
        return True

    return False


def get_admin_chat_ids():
    data = load_users()
    result = []

    for cid, info in data.get("users", {}).items():
        if info.get("role") in ["owner", "admin"]:
            result.append(cid)

    return result


def bootstrap_first_user(chat_id, name="", username=""):
    if user_count() == 0:
        add_or_update_user(chat_id, "owner", name=name, username=username, created_by="first_start")
        return True

    return False


def main_menu_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🚪 Gate", "callback_data": "gate_panel"},
                {"text": "🚗 Leaf Charger", "callback_data": "leaf_panel"}
            ],
            [
                {"text": "👤 Who am I", "callback_data": "whoami"}
            ]
        ]
    }


def gate_keyboard():
    data = read_gate_status()

    gate = str(data.get("gate", "")).upper()
    motion = str(data.get("gate_motion", "")).upper()
    l_close = data.get("gate_l_close")
    l_open = data.get("gate_l_open")

    closed = gate == "CLOSED" or motion == "CLOSED" or l_close is True
    opened = gate in ["OPEN", "OPENED"] or motion in ["OPEN", "OPENED"] or l_open is True

    rows = [
        [
            {"text": "🔄 Gate Status", "callback_data": "gate_status"}
        ],
        [
            {"text": "🚶 Pedestrian", "callback_data": "gate_pedestrian"},
            {"text": "↔️ Partial", "callback_data": "gate_partial"}
        ]
    ]

    action_row = []

    if not opened:
        action_row.append({"text": "🟢 Open", "callback_data": "gate_ask_open"})

    action_row.append({"text": "🛑 Stop", "callback_data": "gate_stop"})

    if not closed:
        action_row.append({"text": "🔵 Close", "callback_data": "gate_close"})

    rows.append(action_row)

    rows.append([
        {"text": "⬅️ Main Menu", "callback_data": "main_menu"}
    ])

    return {
        "inline_keyboard": rows
    }

def gate_confirm_open_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm OPEN", "callback_data": "gate_open_yes"},
                {"text": "❌ Cancel", "callback_data": "gate_panel"}
            ],
            [
                {"text": "🔄 Gate Status", "callback_data": "gate_status"}
            ]
        ]
    }


def leaf_keyboard():
    timer = load_json(LEAF_TIMER_FILE, {"enabled": False})
    target = load_json(LEAF_TARGET_FILE, {"enabled": False})
    data = load_json(LEAF_STATUS_FILE, {})

    switch_value = data.get("switch")
    state = str(data.get("charging_state", "")).upper()

    charger_on = switch_value is True or state in ["ON", "CHARGING"]

    rows = [
        [
            {"text": "🔄 Charger Status", "callback_data": "leaf_status"}
        ],
        [
            {"text": "🔄 Refresh Pandora SOC", "callback_data": "leaf_refresh_pandora_soc"}
        ]
    ]

    if charger_on:
        rows.append([
            {"text": "🛑 Charger OFF", "callback_data": "leaf_off"}
        ])
    else:
        rows.append([
            {"text": "🟢 Charger ON", "callback_data": "leaf_ask_on"}
        ])

    rows += [
        [
            {"text": "🎯 Зарядить по kWh", "callback_data": "leaf_kwh_menu"},
            {"text": "🔋 Зарядить по %", "callback_data": "leaf_percent_menu"}
        ],
        [
            {"text": "⏱ OFF 1h", "callback_data": "leaf_timer_1"},
            {"text": "⏱ OFF 2h", "callback_data": "leaf_timer_2"},
            {"text": "⏱ OFF 4h", "callback_data": "leaf_timer_4"}
        ]
    ]

    if timer.get("enabled") or target.get("enabled"):
        rows.append([
            {"text": "❌ Cancel Target/Timer", "callback_data": "leaf_cancel_all"}
        ])

    rows.append([
        {"text": "⬅️ Main Menu", "callback_data": "main_menu"}
    ])

    return {
        "inline_keyboard": rows
    }

def leaf_confirm_on_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🔋 Зарядить по %", "callback_data": "leaf_percent_menu"}
            ],
            [
                {"text": "🟢 Просто включить зарядку", "callback_data": "leaf_on_yes"}
            ],
            [
                {"text": "⬅️ Назад к Leaf", "callback_data": "leaf_panel"}
            ]
        ]
    }


def leaf_timer_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "1h", "callback_data": "leaf_timer_1"},
                {"text": "2h", "callback_data": "leaf_timer_2"},
                {"text": "4h", "callback_data": "leaf_timer_4"}
            ],
            [
                {"text": "6h", "callback_data": "leaf_timer_6"},
                {"text": "8h", "callback_data": "leaf_timer_8"},
                {"text": "10h", "callback_data": "leaf_timer_10"}
            ],
            [
                {"text": "❌ Cancel Timer", "callback_data": "leaf_timer_cancel"}
            ],
            [
                {"text": "⬅️ Назад к Leaf", "callback_data": "leaf_panel"}
            ]
        ]
    }


def leaf_kwh_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "+5 kWh", "callback_data": "leaf_start_kwh_5"},
                {"text": "+10 kWh", "callback_data": "leaf_start_kwh_10"},
                {"text": "+15 kWh", "callback_data": "leaf_start_kwh_15"}
            ],
            [
                {"text": "+20 kWh", "callback_data": "leaf_start_kwh_20"},
                {"text": "+25 kWh", "callback_data": "leaf_start_kwh_25"},
                {"text": "+30 kWh", "callback_data": "leaf_start_kwh_30"}
            ],
            [
                {"text": "⬅️ Назад к Leaf", "callback_data": "leaf_panel"}
            ]
        ]
    }


def leaf_percent_current_keyboard():
    rows = []
    row = []

    for pct in [10, 20, 30, 40, 50, 60, 70, 80, 90]:
        row.append({
            "text": f"{pct}%",
            "callback_data": f"leaf_current_pct_{pct}"
        })

        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        {"text": "⬅️ Назад к Leaf", "callback_data": "leaf_panel"}
    ])

    return {"inline_keyboard": rows}


def leaf_percent_target_keyboard(current):
    rows = []
    row = []

    for pct in [60, 70, 80, 90, 100]:
        if pct <= current:
            continue

        row.append({
            "text": f"{pct}%",
            "callback_data": f"leaf_target_pct_{current}_{pct}"
        })

        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        {"text": "⬅️ Current %", "callback_data": "leaf_percent_manual_menu"}
    ])

    return {"inline_keyboard": rows}


def leaf_percent_target_from_pandora_keyboard(current_percent):
    rows = []
    row = []

    for target in [60, 70, 80, 90, 100]:
        if float(target) <= float(current_percent):
            continue

        row.append({
            "text": f"{target}%",
            "callback_data": f"leaf_start_pct_pandora_{target}"
        })

        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([
        {"text": "✍️ Ввести текущий % вручную", "callback_data": "leaf_percent_manual_menu"}
    ])

    rows.append([
        {"text": "⬅️ Назад к Leaf", "callback_data": "leaf_panel"}
    ])

    return {"inline_keyboard": rows}


def request_access_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🔐 Request access", "callback_data": "request_access"}
            ]
        ]
    }


def approve_keyboard(request_chat_id):
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve User", "callback_data": f"approve_user:{request_chat_id}"}
            ],
            [
                {"text": "🛡 Approve Admin", "callback_data": f"approve_admin:{request_chat_id}"}
            ],
            [
                {"text": "❌ Deny", "callback_data": f"deny:{request_chat_id}"}
            ]
        ]
    }


def read_gate_status():
    # Fast mode: read cached status.json.
    # It is updated by trc-gate-status.timer / gate_watcher.
    return load_json(STATUS_FILE, {
        "vps": "ONLINE",
        "asterisk": "UNKNOWN",
        "gate": "UNKNOWN",
        "gate_motion": "UNKNOWN",
        "gate_position": None
    })

def format_gate_status():
    data = read_gate_status()

    return (
        "🚪 <b>TRC Gate Status</b>\n\n"
        f"Gate: <b>{h(data.get('gate', 'UNKNOWN'))}</b>\n"
        f"Motion: <b>{h(data.get('gate_motion', '-'))}</b>\n"
        f"Position: <b>{h(data.get('gate_position', '-'))}</b>\n"
        f"Closed limit: <b>{h(data.get('gate_l_close', '-'))}</b>\n"
        f"Open limit: <b>{h(data.get('gate_l_open', '-'))}</b>\n"
        f"RSSI: <b>{h(data.get('gate_rssi', '-'))}</b>\n"
        f"Source: <b>{h(data.get('gate_source', '-'))}</b>\n\n"
        f"VPS: <b>{h(data.get('vps', 'UNKNOWN'))}</b>\n"
        f"PBX: <b>{h(data.get('asterisk', 'UNKNOWN'))}</b>"
    )


def run_gate_action(action):
    try:
        p = subprocess.run(
            [GATE_CONTROL, action],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90
        )

        output = (p.stdout or "").strip()
        error = (p.stderr or "").strip()

        if p.returncode != 0:
            return False, output + ("\n" + error if error else "")

        return True, output

    except Exception as e:
        return False, repr(e)


def read_leaf_status():
    # Fast mode: read cached leaf_status.json.
    # It is updated by trc-leaf-charger-watcher.timer and Pandora timer.
    data = load_json(LEAF_STATUS_FILE, {})

    if not data:
        return {
            "ok": False,
            "error": "leaf_status.json is empty or missing"
        }

    if "ok" not in data:
        data["ok"] = True

    return data

def load_pandora_soc_for_target(max_age_minutes=30):
    data = load_json(PANDORA_SOC_FILE, {})

    soc = data.get("pandora_soc_percent")
    status = data.get("pandora_status")
    age = data.get("pandora_age_minutes")

    if soc is None:
        return None

    try:
        soc = float(soc)
    except Exception:
        return None

    try:
        age_float = float(age)
    except Exception:
        age_float = 9999

    if status != "fresh" or age_float > max_age_minutes:
        return None

    return {
        "soc": soc,
        "age": age_float,
        "status": status
    }


def format_leaf_status():
    data = read_leaf_status()

    if not data.get("ok"):
        return "❌ <b>Leaf Charger Error</b>\n\n<code>" + h(data.get("error", "unknown")) + "</code>"

    timer = load_json(LEAF_TIMER_FILE, {"enabled": False})

    state = str(data.get("charging_state") or "UNKNOWN").upper()
    state_icon = "🔴"

    if state == "CHARGING":
        state_icon = "🟢"
    elif state == "ON":
        state_icon = "🟡"
    elif state == "OFF":
        state_icon = "🔴"

    switch_value = data.get("switch")
    switch_text = "True" if switch_value is True else "False" if switch_value is False else str(switch_value)

    pandora_soc = data.get("pandora_soc_percent")
    pandora_soh = data.get("pandora_soh_percent")
    pandora_status = data.get("pandora_status")
    pandora_age = data.get("pandora_age_minutes")

    pandora_connected = "yes" if data.get("pandora_charging_connected") else "no"
    pandora_fast = "yes" if data.get("pandora_charging_fast") else "no"

    timer_text = "OFF"

    if timer.get("enabled"):
        try:
            remaining = max(0, int(timer.get("off_at")) - now())
            timer_text = f"ON, {round(remaining / 60)} min left"
        except Exception:
            timer_text = "ON"

    target_text = data.get("target_display", "OFF")

    lines = [
        "🚗 <b>Leaf Charger Breaker</b>",
        "",
        f"{state_icon} State: <b>{h(state)}</b>",
        f"🔘 Switch: <b>{h(switch_text)}</b>",
        f"🌐 Online: <b>{h(data.get('online_state'))}</b>",
        "",
        f"⚡ Voltage: <b>{h(data.get('voltage_v'))} V</b>",
        f"🔌 Current: <b>{h(data.get('current_a'))} A</b>",
        f"📈 Power: <b>{h(data.get('power_kw'))} kW</b>",
        f"🔋 Energy total: <b>{h(data.get('energy_kwh'))} kWh</b>",
        f"🌡 Breaker temp: <b>{h(data.get('temperature_c'))} °C</b>",
        f"🧯 Leakage: <b>{h(data.get('leakage_current_ma'))} mA</b>",
        "",
    ]

    if pandora_soc is not None:
        lines += [
            f"🔋 Pandora SOC: <b>{h(pandora_soc)}%</b> ({h(pandora_status)}, {h(pandora_age)} min ago)",
            f"🪫 SOH: <b>{h(pandora_soh)}%</b>",
            f"🔌 Pandora connected: <b>{h(pandora_connected)}</b>",
            f"🚗 Pandora fast charge: <b>{h(pandora_fast)}</b>",
            f"🌡 EV battery temp: <b>{h(data.get('pandora_battery_temperature'))} °C</b>",
            "",
        ]
    else:
        lines += [
            "🔋 Pandora SOC: <b>not available</b>",
            "",
        ]

    lines += [
        f"📟 Event: <b>{h(data.get('event'))}</b>",
        f"🔁 Relay memory: <b>{h(data.get('relay_status'))}</b>",
        f"⏱ Таймер: <b>{h(timer_text)}</b>",
        f"🎯 Цель: <b>{h(target_text)}</b>"
    ]

    if data.get("target_enabled"):
        lines += [
            "",
            f"Добавлено: <b>{h(data.get('target_added_kwh'))} kWh</b>",
            f"Осталось: <b>{h(data.get('target_remaining_kwh'))} kWh</b>",
            f"Примерный заряд: <b>{h(data.get('target_estimated_percent'))}%</b>"
        ]

    return "\n".join(lines)

def run_leaf_action_background(action):
    if action not in ["on", "off"]:
        return False, "bad action"

    try:
        subprocess.Popen(
            ["/opt/trc-tuya/leaf_bg_action.py", action],
            stdout=open("/opt/trc-tuya/leaf_background_actions.log", "ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
        return True, "started"
    except Exception as e:
        return False, repr(e)


def run_leaf_action(action):
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

        return True, output

    except Exception as e:
        return False, repr(e)


def save_leaf_timer(hours, chat_id):
    off_at = now() + int(float(hours) * 3600)

    data = {
        "enabled": True,
        "hours": float(hours),
        "created_at": now(),
        "off_at": off_at,
        "created_by": str(chat_id)
    }

    save_json(LEAF_TIMER_FILE, data)
    return data


def cancel_leaf_timer():
    data = load_json(LEAF_TIMER_FILE, {"enabled": False})
    data["enabled"] = False
    data["cancelled_at"] = now()
    save_json(LEAF_TIMER_FILE, data)
    return data


def cancel_leaf_target():
    data = load_json(LEAF_TARGET_FILE, {"enabled": False})
    data["enabled"] = False
    data["cancelled_at"] = now()
    save_json(LEAF_TARGET_FILE, data)
    return data


def create_charge_target_by_kwh(kwh, chat_id):
    status = read_leaf_status()

    if not status.get("ok"):
        return False, "Cannot read Leaf charger status."

    try:
        kwh = float(kwh)
    except Exception:
        return False, "Bad kWh value."

    if kwh <= 0 or kwh > 80:
        return False, "kWh target must be between 0 and 80."

    data = {
        "enabled": True,
        "mode": "kwh",
        "target_add_kwh": round(kwh, 2),
        "start_energy_kwh": status.get("energy_kwh"),
        "created_at": now(),
        "created_by": str(chat_id),
        "added_kwh": 0,
        "remaining_kwh": round(kwh, 2)
    }

    save_json(LEAF_TARGET_FILE, data)
    return True, data


def create_charge_target_by_percent(current_percent, target_percent, chat_id):
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

    battery_kwh = env_float("LEAF_BATTERY_KWH", 62)
    efficiency = env_float("LEAF_CHARGE_EFFICIENCY", 0.88)

    battery_needed_kwh = battery_kwh * ((target_percent - current_percent) / 100.0)
    wall_needed_kwh = battery_needed_kwh / efficiency

    data = {
        "enabled": True,
        "mode": "percent",
        "current_percent": current_percent,
        "target_percent": target_percent,
        "battery_kwh": battery_kwh,
        "efficiency": efficiency,
        "battery_needed_kwh": round(battery_needed_kwh, 2),
        "target_add_kwh": round(wall_needed_kwh, 2),
        "start_energy_kwh": status.get("energy_kwh"),
        "created_at": now(),
        "created_by": str(chat_id),
        "added_kwh": 0,
        "remaining_kwh": round(wall_needed_kwh, 2),
        "estimated_percent": current_percent
    }

    save_json(LEAF_TARGET_FILE, data)
    return True, data


def refresh_pandora_soc():
    scripts = [PANDORA_NOTIFY_SCRIPT, PANDORA_SCRIPT]

    for script in scripts:
        if os.path.exists(script) and os.access(script, os.X_OK):
            try:
                p = subprocess.run(
                    [script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=120
                )

                if p.returncode == 0:
                    return True, p.stdout.strip()

                return False, (p.stdout + "\n" + p.stderr).strip()

            except Exception as e:
                return False, repr(e)

    return False, "Pandora script is not installed yet."


def whoami_text(chat_id):
    role = get_role(chat_id)

    return (
        "👤 <b>Your Telegram access</b>\n\n"
        f"Chat ID: <code>{h(chat_id)}</code>\n"
        f"Role: <b>{h(role or 'not authorized')}</b>"
    )


def users_text():
    data = load_users()
    lines = ["👥 <b>Authorized users</b>", ""]

    for cid, info in data.get("users", {}).items():
        lines.append(
            f"<code>{h(cid)}</code> — <b>{h(info.get('role'))}</b> "
            f"{h(info.get('name', ''))} @{h(info.get('username', ''))}"
        )

    return "\n".join(lines)


def handle_message(msg):
    chat = msg.get("chat", {})
    user = msg.get("from", {})
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    name = user.get("first_name", "") or user.get("last_name", "")
    username = user.get("username", "")

    bootstrapped = bootstrap_first_user(chat_id, name=name, username=username)

    if bootstrapped:
        send_message(
            chat_id,
            "✅ You are the first user and have been added as <b>owner</b>.\n\n" + whoami_text(chat_id),
            main_menu_keyboard()
        )
        return

    if text in ["/start", "/panel", "/menu"]:
        if not is_authorized(chat_id):
            send_message(
                chat_id,
                "🔒 Access is not configured for this chat.\n\n" + whoami_text(chat_id),
                request_access_keyboard()
            )
            return

        send_message(chat_id, "🏠 <b>TRC Control Panel</b>", main_menu_keyboard())
        return

    if text == "/status":
        if not is_authorized(chat_id):
            send_message(chat_id, whoami_text(chat_id), request_access_keyboard())
            return

        send_message(chat_id, format_gate_status(), gate_keyboard())
        return

    if text == "/leaf":
        if not is_authorized(chat_id):
            send_message(chat_id, whoami_text(chat_id), request_access_keyboard())
            return

        send_message(chat_id, format_leaf_status(), leaf_keyboard())
        return

    if text == "/whoami":
        send_message(chat_id, whoami_text(chat_id), main_menu_keyboard() if is_authorized(chat_id) else request_access_keyboard())
        return

    if text == "/users":
        if not is_admin(chat_id):
            send_message(chat_id, "❌ Admin only.")
            return

        send_message(chat_id, users_text(), main_menu_keyboard())
        return

    send_message(
        chat_id,
        "Use /panel for main menu.\nUse /leaf for Leaf charger.\nUse /status for gate status.",
        main_menu_keyboard() if is_authorized(chat_id) else request_access_keyboard()
    )


def handle_callback(callback):
    callback_id = callback.get("id")
    msg = callback.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    data = callback.get("data", "")
    from_user = callback.get("from", {})
    name = from_user.get("first_name", "") or from_user.get("last_name", "")
    username = from_user.get("username", "")

    answer_callback(callback_id)

    bootstrap_first_user(chat_id, name=name, username=username)

    if data == "request_access":
        for admin_id in get_admin_chat_ids():
            try:
                send_message(
                    admin_id,
                    "🔐 <b>Access request</b>\n\n"
                    f"Name: <b>{h(name)}</b>\n"
                    f"Username: @{h(username)}\n"
                    f"Chat ID: <code>{h(chat_id)}</code>",
                    approve_keyboard(chat_id)
                )
            except Exception:
                pass

        edit_message(chat_id, message_id, "✅ Access request sent to admins.")
        return

    if data.startswith("approve_user:") or data.startswith("approve_admin:") or data.startswith("deny:"):
        if not is_admin(chat_id):
            edit_message(chat_id, message_id, "❌ Admin only.")
            return

        action, target = data.split(":", 1)

        if action == "deny":
            edit_message(chat_id, message_id, f"❌ Access denied for <code>{h(target)}</code>.")
            try:
                send_message(target, "❌ Your access request was denied.")
            except Exception:
                pass
            return

        role = "user" if action == "approve_user" else "admin"
        add_or_update_user(target, role, created_by=chat_id)

        edit_message(chat_id, message_id, f"✅ Approved <code>{h(target)}</code> as <b>{role}</b>.")
        try:
            send_message(target, f"✅ Access approved as <b>{role}</b>.", main_menu_keyboard())
        except Exception:
            pass
        return

    if not is_authorized(chat_id):
        edit_message(chat_id, message_id, "🔒 Not authorized.", request_access_keyboard())
        return

    if data == "whoami":
        edit_message(chat_id, message_id, whoami_text(chat_id), main_menu_keyboard())
        return

    if data == "main_menu":
        edit_message(chat_id, message_id, "🏠 <b>TRC Control Panel</b>", main_menu_keyboard())
        return

    if data == "gate_panel":
        edit_message(chat_id, message_id, format_gate_status(), gate_keyboard())
        return

    if data == "gate_status":
        edit_message(chat_id, message_id, format_gate_status(), gate_keyboard())
        return

    if data == "gate_ask_open":
        edit_message(
            chat_id,
            message_id,
            "⚠️ <b>Confirm full gate OPEN</b>\n\n" + format_gate_status(),
            gate_confirm_open_keyboard()
        )
        return

    if data == "gate_open_yes":
        ok, output = run_gate_action("open")
        edit_message(
            chat_id,
            message_id,
            ("🟢 <b>OPEN command sent</b>\n\n" if ok else "❌ <b>OPEN failed</b>\n\n<code>" + h(output[-800:]) + "</code>\n\n")
            + format_gate_status(),
            gate_keyboard()
        )
        return

    if data == "gate_close":
        ok, output = run_gate_action("close")
        edit_message(
            chat_id,
            message_id,
            ("🔵 <b>CLOSE command sent</b>\n\n" if ok else "❌ <b>CLOSE failed</b>\n\n<code>" + h(output[-800:]) + "</code>\n\n")
            + format_gate_status(),
            gate_keyboard()
        )
        return

    if data == "gate_stop":
        ok, output = run_gate_action("stop")
        edit_message(
            chat_id,
            message_id,
            ("🛑 <b>STOP command sent</b>\n\n" if ok else "❌ <b>STOP failed</b>\n\n<code>" + h(output[-800:]) + "</code>\n\n")
            + format_gate_status(),
            gate_keyboard()
        )
        return

    if data == "gate_pedestrian":
        ok, output = run_gate_action("pedestrian")
        edit_message(
            chat_id,
            message_id,
            ("🚶 <b>Pedestrian command sent</b>\n\n" if ok else "❌ <b>Pedestrian failed</b>\n\n<code>" + h(output[-800:]) + "</code>\n\n")
            + format_gate_status(),
            gate_keyboard()
        )
        return

    if data == "gate_partial":
        ok, output = run_gate_action("partial")
        edit_message(
            chat_id,
            message_id,
            ("↔️ <b>Partial command sent</b>\n\n" if ok else "❌ <b>Partial failed</b>\n\n<code>" + h(output[-800:]) + "</code>\n\n")
            + format_gate_status(),
            gate_keyboard()
        )
        return

    if data == "leaf_panel":
        edit_message(chat_id, message_id, format_leaf_status(), leaf_keyboard())
        return

    if data == "leaf_status":
        edit_message(chat_id, message_id, format_leaf_status(), leaf_keyboard())
        return

    if data == "leaf_ask_on":
        text = "🔋 <b>Как включить зарядку?</b>\n\n"
        text += "Выбери <b>Зарядить по %</b>, если нужно автоматически остановить зарядку на нужном проценте.\n"
        text += "Выбери <b>Просто включить зарядку</b>, если нужно только включить автомат без цели."

        edit_message(chat_id, message_id, text, leaf_confirm_on_keyboard())
        return

    if data == "leaf_on_yes":
        ok, output = run_leaf_action_background("on")

        try:
            answer_callback(callback_id, "ON command sent. Waiting for confirmation." if ok else "Charger ON failed")
        except Exception:
            pass

        if ok:
            text = "🟢 <b>Команда на включение зарядки отправлена</b>\n"
            text += "⏳ <b>Ждём, пока автомат реально включится...</b>\n\n"
            text += "Статус придёт отдельным сообщением после реального подтверждения."
        else:
            text = "❌ <b>Не удалось отправить команду включения зарядки</b>\n\n"
            text += "<code>" + h(output[-800:]) + "</code>"

        edit_message(
            chat_id,
            message_id,
            text,
            {"inline_keyboard": [[{"text": "🔄 Charger Status", "callback_data": "leaf_status"}], [{"text": "🚗 Leaf Panel", "callback_data": "leaf_panel"}]]}
        )
        return

    if data == "leaf_off":
        ok, output = run_leaf_action_background("off")

        try:
            answer_callback(callback_id, "OFF command sent. Waiting for confirmation." if ok else "Charger OFF failed")
        except Exception:
            pass

        if ok:
            text = "🛑 <b>Команда на выключение зарядки отправлена</b>\n"
            text += "⏳ <b>Ждём, пока автомат реально выключится...</b>\n\n"
            text += "Статус придёт отдельным сообщением после реального подтверждения."
        else:
            text = "❌ <b>Не удалось отправить команду выключения зарядки</b>\n\n"
            text += "<code>" + h(output[-800:]) + "</code>"

        edit_message(
            chat_id,
            message_id,
            text,
            {"inline_keyboard": [[{"text": "🔄 Charger Status", "callback_data": "leaf_status"}], [{"text": "🚗 Leaf Panel", "callback_data": "leaf_panel"}]]}
        )
        return

    if data == "leaf_timer_menu":
        edit_message(chat_id, message_id, "⏱ <b>Choose charger auto-OFF timer</b>", leaf_timer_keyboard())
        return

    if data.startswith("leaf_timer_") and data != "leaf_timer_cancel":
        hours = data.replace("leaf_timer_", "")
        timer = save_leaf_timer(hours, chat_id)
        edit_message(
            chat_id,
            message_id,
            "⏱ <b>Leaf charger timer set</b>\n\n"
            f"Auto-OFF in: <b>{h(hours)} hours</b>\n"
            f"Off at Unix: <code>{h(timer.get('off_at'))}</code>\n\n"
            + format_leaf_status(),
            leaf_keyboard()
        )
        return

    if data == "leaf_timer_cancel":
        cancel_leaf_timer()
        edit_message(chat_id, message_id, "❌ Timer cancelled.\n\n" + format_leaf_status(), leaf_keyboard())
        return

    if data == "leaf_kwh_menu":
        edit_message(chat_id, message_id, "🎯 <b>Choose kWh to add from wall</b>", leaf_kwh_keyboard())
        return

    if data.startswith("leaf_start_kwh_"):
        kwh = data.replace("leaf_start_kwh_", "")
        ok, result = create_charge_target_by_kwh(kwh, chat_id)

        if not ok:
            edit_message(chat_id, message_id, "❌ " + h(result), leaf_keyboard())
            return

        on_ok, on_output = run_leaf_action("on")

        edit_message(
            chat_id,
            message_id,
            "🎯 <b>Цель зарядки по kWh установлена</b>\n\n"
            f"Добавить: <b>{h(kwh)} kWh</b>\n"
            f"Стартовый счётчик энергии: <b>{h(result.get('start_energy_kwh'))} kWh</b>\n"
            f"Команда включения отправлена: <b>{h(on_ok)}</b>\n\n"
            + format_leaf_status(),
            leaf_keyboard()
        )
        return

    if data == "leaf_cancel_all":
        cancel_leaf_timer()
        cancel_leaf_target()

        try:
            answer_callback(callback_id, "Target and timer cancelled")
        except Exception:
            pass

        send_message(
            chat_id,
            "❌ <b>Leaf target and timer cancelled</b>\n\n"
            "Цель: <b>OFF</b>\n"
            "Таймер: <b>OFF</b>",
            leaf_keyboard()
        )
        return

    if data == "leaf_cancel_all":
        cancel_leaf_timer()
        cancel_leaf_target()

        try:
            answer_callback(callback_id, "Target and timer cancelled")
        except Exception:
            pass

        edit_message(
            chat_id,
            message_id,
            format_leaf_status(),
            leaf_keyboard()
        )
        return

    if data == "leaf_percent_menu":
        pandora = load_pandora_soc_for_target(max_age_minutes=30)

        if pandora:
            soc = pandora["soc"]
            age = pandora["age"]

            edit_message(
                chat_id,
                message_id,
                "🔋 <b>Зарядка по процентам</b>\n\n"
                f"Текущий заряд Leaf по Pandora: <b>{h(soc)}%</b>\n"
                f"Статус Pandora: <b>fresh</b>, {h(age)} min ago\n\n"
                "Выбери, до какого процента зарядить:",
                leaf_percent_target_from_pandora_keyboard(soc)
            )
            return

        edit_message(
            chat_id,
            message_id,
            "🔋 <b>Зарядка по процентам</b>\n\n"
            "Pandora SOC is not fresh or not available.\n"
            "Choose current Leaf battery percent manually:",
            leaf_percent_current_keyboard()
        )
        return

    if data == "leaf_percent_manual_menu":
        edit_message(
            chat_id,
            message_id,
            "✍️ <b>Manual Charge by %</b>\n\n"
            "Choose current Leaf battery percent from the car display:",
            leaf_percent_current_keyboard()
        )
        return

    if data.startswith("leaf_current_pct_"):
        current = int(data.replace("leaf_current_pct_", ""))
        edit_message(
            chat_id,
            message_id,
            f"🔋 Current battery: <b>{current}%</b>\n\nChoose target percent:",
            leaf_percent_target_keyboard(current)
        )
        return

    if data.startswith("leaf_target_pct_"):
        parts = data.replace("leaf_target_pct_", "").split("_")
        current = float(parts[0])
        target = float(parts[1])

        ok, result = create_charge_target_by_percent(current, target, chat_id)

        if not ok:
            edit_message(chat_id, message_id, "❌ " + h(result), leaf_keyboard())
            return

        on_ok, on_output = run_leaf_action("on")

        edit_message(
            chat_id,
            message_id,
            "🔋🎯 <b>Leaf percent charge target set</b>\n\n"
            f"From: <b>{h(current)}%</b>\n"
            f"До: <b>{h(target)}%</b>\n"
            f"Нужно из розетки: <b>{h(result.get('target_add_kwh'))} kWh</b>\n"
            f"Команда включения отправлена: <b>{h(on_ok)}</b>\n\n"
            + format_leaf_status(),
            leaf_keyboard()
        )
        return

    if data.startswith("leaf_start_pct_pandora_"):
        pandora = load_pandora_soc_for_target(max_age_minutes=30)

        if not pandora:
            edit_message(
                chat_id,
                message_id,
                "❌ Pandora SOC is not fresh anymore.\n\nChoose current percent manually:",
                leaf_percent_current_keyboard()
            )
            return

        current = float(pandora["soc"])
        target = float(data.replace("leaf_start_pct_pandora_", ""))

        ok, result = create_charge_target_by_percent(current, target, chat_id)

        if not ok:
            edit_message(chat_id, message_id, "❌ " + h(result), leaf_keyboard())
            return

        on_ok, on_output = run_leaf_action("on")

        edit_message(
            chat_id,
            message_id,
            "🔋🎯 <b>Цель зарядки по % установлена по данным Pandora</b>\n\n"
            f"Сейчас по Pandora: <b>{h(current)}%</b>\n"
            f"До: <b>{h(target)}%</b>\n"
            f"Нужно из розетки: <b>{h(result.get('target_add_kwh'))} kWh</b>\n"
            f"Команда включения отправлена: <b>{h(on_ok)}</b>\n\n"
            + format_leaf_status(),
            leaf_keyboard()
        )
        return

    if data == "leaf_refresh_pandora_soc":
        ok, output = refresh_pandora_soc()

        edit_message(
            chat_id,
            message_id,
            ("🔄 <b>Pandora SOC refreshed</b>\n\n" if ok else "❌ <b>Pandora refresh failed</b>\n\n")
            + "<code>" + h(output[-2000:]) + "</code>\n\n"
            + format_leaf_status(),
            leaf_keyboard()
        )
        return

    edit_message(chat_id, message_id, "Unknown button: " + h(data), main_menu_keyboard())


def main():
    offset = 0

    print("TRC Telegram Gate/Leaf bot started", flush=True)

    while True:
        try:
            result = telegram("getUpdates", {
                "offset": offset,
                "timeout": 45,
                "allowed_updates": ["message", "callback_query"]
            }, timeout=60)

            for upd in result.get("result", []):
                offset = max(offset, upd.get("update_id", 0) + 1)

                if "message" in upd:
                    handle_message(upd["message"])

                if "callback_query" in upd:
                    handle_callback(upd["callback_query"])

        except KeyboardInterrupt:
            print("Stopped", flush=True)
            break

        except Exception as e:
            print("ERROR:", repr(e), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
