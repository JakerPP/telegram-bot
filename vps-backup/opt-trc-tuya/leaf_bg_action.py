#!/opt/trc-tuya/venv/bin/python3
import html, json, os, subprocess, sys, time, requests

ENV_FILE = "/opt/trc-tuya/telegram_gate_bot.env"
LEAF_CONTROL = "/opt/trc-tuya/leaf_charger_control.py"
STATUS_FILE = "/var/www/html/trc/leaf_status.json"

def h(x):
    return html.escape(str(x))

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def load_env():
    data = {}
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip()
    except Exception:
        pass
    return data

def run_control(action):
    return subprocess.run(
        [LEAF_CONTROL, action],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120
    )

def refresh_status():
    run_control("status")
    return load_json(STATUS_FILE, {})

def is_on(data):
    state = str(data.get("charging_state", "")).upper()
    return data.get("switch") is True or state in ["ON", "CHARGING"]

def is_off(data):
    state = str(data.get("charging_state", "")).upper()
    return data.get("switch") is False or state == "OFF"

def fmt(data):
    state = str(data.get("charging_state") or "UNKNOWN").upper()
    icon = "🟡" if state == "ON" else "⚡" if state == "CHARGING" else "🔴"

    connected = "yes" if data.get("pandora_charging_connected") else "no"
    fast = "yes" if data.get("pandora_charging_fast") else "no"

    return "\n".join([
        "🚗 <b>Leaf Charger Breaker</b>",
        "",
        f"{icon} State: <b>{h(state)}</b>",
        f"🔘 Switch: <b>{h(data.get('switch'))}</b>",
        f"🌐 Online: <b>{h(data.get('online_state'))}</b>",
        "",
        f"⚡ Voltage: <b>{h(data.get('voltage_v'))} V</b>",
        f"🔌 Current: <b>{h(data.get('current_a'))} A</b>",
        f"📈 Power: <b>{h(data.get('power_kw'))} kW</b>",
        f"🔋 Energy total: <b>{h(data.get('energy_kwh'))} kWh</b>",
        f"🌡 Breaker temp: <b>{h(data.get('temperature_c'))} °C</b>",
        f"🧯 Leakage: <b>{h(data.get('leakage_current_ma'))} mA</b>",
        "",
        f"🔋 Pandora SOC: <b>{h(data.get('pandora_soc_percent'))}%</b> ({h(data.get('pandora_status'))}, {h(data.get('pandora_age_minutes'))} min ago)",
        f"🪫 SOH: <b>{h(data.get('pandora_soh_percent'))}%</b>",
        f"🔌 Pandora connected: <b>{connected}</b>",
        f"🚗 Pandora fast charge: <b>{fast}</b>",
        f"🌡 EV battery temp: <b>{h(data.get('pandora_battery_temperature'))} °C</b>",
        "",
        f"📟 Event: <b>{h(data.get('event'))}</b>",
        f"🔁 Relay memory: <b>{h(data.get('relay_status'))}</b>",
        f"⏱ Timer: <b>{h(data.get('timer_display', 'OFF'))}</b>",
        f"🎯 Target: <b>{h(data.get('target_display', 'OFF'))}</b>",
    ])

def keyboard(data):
    if is_on(data):
        action = {"text": "🛑 Charger OFF", "callback_data": "leaf_off"}
    else:
        action = {"text": "🟢 Charger ON", "callback_data": "leaf_ask_on"}

    return {
        "inline_keyboard": [
            [{"text": "🔄 Charger Status", "callback_data": "leaf_status"}, action],
            [{"text": "🚗 Leaf Panel", "callback_data": "leaf_panel"}],
        ]
    }

def send(text, data):
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("ALLOWED_CHAT_ID")

    if not token or not chat_id or chat_id == "0":
        print("telegram not configured")
        return

    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": keyboard(data),
        },
        timeout=20
    )
    print(r.status_code, r.text[:300])

def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action not in ["on", "off"]:
        print("bad action")
        sys.exit(2)

    print("start action", action, time.strftime("%Y-%m-%d %H:%M:%S"))

    result = run_control(action)
    print("command rc", result.returncode)
    print(result.stdout[-1000:])
    print(result.stderr[-1000:])

    desired = is_on if action == "on" else is_off
    title_ok = "🟢 <b>Leaf charger breaker turned ON</b>" if action == "on" else "🛑 <b>Leaf charger breaker turned OFF</b>"
    title_fail = "⚠️ <b>Leaf charger command sent, but status was not confirmed</b>"

    data = {}

    for i in range(24):
        time.sleep(5)
        data = refresh_status()
        print("poll", i + 1, data.get("switch"), data.get("charging_state"), data.get("current_a"), data.get("power_kw"))

        if desired(data):
            send(title_ok + "\n\n" + fmt(data), data)
            return

    data = refresh_status()
    send(title_fail + "\n\n" + fmt(data), data)

if __name__ == "__main__":
    main()
