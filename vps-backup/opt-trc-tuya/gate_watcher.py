#!/opt/trc-tuya/venv/bin/python3

import html
import json
import os
import subprocess
import time
import requests

BASE_DIR = "/opt/trc-tuya"
ENV_FILE = "/opt/trc-tuya/telegram_gate_bot.env"
GATE_CONTROL = "/opt/trc-tuya/gate_control.py"
STATUS_FILE = "/var/www/html/trc/status.json"
STATE_FILE = "/opt/trc-tuya/gate_watcher_state.json"
AUDIT_LOG = "/opt/trc-tuya/gate_audit.log"

os.chdir(BASE_DIR)


def now():
    return int(time.time())


def h(value):
    return html.escape(str(value))


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


def env_int(env, key, default):
    try:
        return int(env.get(key, default))
    except Exception:
        return int(default)


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def send_telegram(text):
    env = load_env(ENV_FILE)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("ALLOWED_CHAT_ID", "0")

    if not token or chat_id == "0":
        return False, "Telegram not configured"

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "🔄 Gate Status", "callback_data": "gate_status"},
                            {"text": "🛑 Stop", "callback_data": "gate_stop"}
                        ],
                        [
                            {"text": "🔵 Close Gate", "callback_data": "gate_close"},
                            {"text": "🚪 Gate Panel", "callback_data": "gate_panel"}
                        ]
                    ]
                }
            },
            timeout=15
        )

        if r.status_code == 200:
            return True, "sent"

        return False, r.text[:500]

    except Exception as e:
        return False, repr(e)


def refresh_status():
    try:
        subprocess.run(
            [GATE_CONTROL, "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=45
        )
    except Exception:
        pass


def read_gate_status():
    refresh_status()
    return load_json(STATUS_FILE, {
        "gate": "UNKNOWN",
        "gate_motion": "UNKNOWN",
        "gate_position": None,
        "gate_l_close": None,
        "gate_l_open": None,
        "gate_rssi": None
    })


def is_closed(data):
    gate = str(data.get("gate", "")).upper()
    motion = str(data.get("gate_motion", "")).upper()
    return gate == "CLOSED" or motion == "CLOSED" or data.get("gate_l_close") is True


def gate_summary(data):
    return (
        f"Gate: <b>{h(data.get('gate'))}</b>\n"
        f"Motion: <b>{h(data.get('gate_motion'))}</b>\n"
        f"Position: <b>{h(data.get('gate_position'))}</b>\n"
        f"Closed limit: <b>{h(data.get('gate_l_close'))}</b>\n"
        f"Open limit: <b>{h(data.get('gate_l_open'))}</b>\n"
        f"RSSI: <b>{h(data.get('gate_rssi'))}</b>\n"
        f"Source: <b>{h(data.get('gate_source'))}</b>"
    )


def is_offline_or_stale(data, stale_seconds):
    ts = data.get("gate_checked_at")

    if str(data.get("gate", "")).upper() == "UNKNOWN":
        return True, "gate UNKNOWN"

    if data.get("gate_source") != "tuya_shadow":
        return True, "source is not tuya_shadow"

    try:
        age = now() - int(ts)
        if age > stale_seconds:
            return True, f"status stale {age} sec"
    except Exception:
        return True, "bad gate_checked_at"

    return False, ""


def read_new_audit_events(state):
    last_time = int(state.get("last_audit_time") or 0)
    events = []

    if not os.path.exists(AUDIT_LOG):
        return events, last_time

    try:
        with open(AUDIT_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()[-100:]
    except Exception:
        return events, last_time

    max_time = last_time

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            item = json.loads(line)
        except Exception:
            continue

        t = int(item.get("time") or 0)

        if t <= last_time:
            continue

        max_time = max(max_time, t)

        if item.get("action") in ["open", "full", "close", "stop", "partial", "pedestrian"]:
            events.append(item)

    return events, max_time


def audit_text(event, status):
    action = event.get("action")
    ok = event.get("ok")
    source = event.get("source")

    titles = {
        "open": "🟢 <b>Gate OPEN command</b>",
        "full": "🟢 <b>Gate FULL OPEN command</b>",
        "close": "🔵 <b>Gate CLOSE command</b>",
        "stop": "🛑 <b>Gate STOP command</b>",
        "partial": "↔️ <b>Gate PARTIAL command</b>",
        "pedestrian": "🚶 <b>Gate PEDESTRIAN command</b>"
    }

    title = titles.get(action, "ℹ️ <b>Gate command</b>")

    if not ok:
        title = "❌ " + title.replace("<b>", "<b>FAILED: ")

    return (
        f"{title}\n\n"
        f"Source: <b>{h(source)}</b>\n"
        f"Result: <b>{h(ok)}</b>\n\n"
        + gate_summary(status)
    )


def main():
    env = load_env(ENV_FILE)

    open_alert_seconds = env_int(env, "GATE_OPEN_ALERT_SECONDS", 300)
    repeat_seconds = env_int(env, "GATE_OPEN_REPEAT_SECONDS", 300)
    offline_stale_seconds = env_int(env, "GATE_OFFLINE_STALE_SECONDS", 120)
    offline_repeat_seconds = env_int(env, "GATE_OFFLINE_REPEAT_SECONDS", 300)

    ts = now()
    status = read_gate_status()
    state = load_json(STATE_FILE, {})

    sent = []

    if os.environ.get("GATE_WATCHER_TEST") == "1":
        ok, msg = send_telegram("✅ <b>Gate watcher test</b>\n\n" + gate_summary(status))
        sent.append({"type": "test", "ok": ok, "msg": msg})

    # Audit-based command notifications. This catches short actions like pedestrian.
    events, max_audit_time = read_new_audit_events(state)

    for event in events:
        ok, msg = send_telegram(audit_text(event, status))
        sent.append({"type": "audit_" + str(event.get("action")), "ok": ok, "msg": msg})

    if max_audit_time:
        state["last_audit_time"] = max_audit_time

    closed = is_closed(status)
    offline, offline_reason = is_offline_or_stale(status, offline_stale_seconds)

    current_gate = str(status.get("gate"))
    current_motion = str(status.get("gate_motion"))
    current_position = status.get("gate_position")

    last_gate = state.get("last_gate")
    last_motion = state.get("last_motion")
    last_closed = state.get("last_closed")
    last_offline = state.get("last_offline")

    if state.get("initialized"):
        if last_closed is not None and bool(last_closed) != bool(closed):
            if closed:
                ok, msg = send_telegram("✅ <b>Gate closed</b>\n\n" + gate_summary(status))
            else:
                ok, msg = send_telegram("🚪 <b>Gate is not closed</b>\n\n" + gate_summary(status))
            sent.append({"type": "closed_changed", "ok": ok, "msg": msg})

        elif last_gate is not None and (last_gate != current_gate or last_motion != current_motion):
            important = current_motion.upper() in ["OPENING", "CLOSING", "STOPPED"] or current_gate.upper() in ["OPEN", "OPENED", "PARTIAL"]

            if important:
                ok, msg = send_telegram(
                    "ℹ️ <b>Gate status changed</b>\n\n"
                    f"Before: <code>{h(last_gate)} / {h(last_motion)}</code>\n"
                    f"Now: <code>{h(current_gate)} / {h(current_motion)}</code>\n\n"
                    + gate_summary(status)
                )
                sent.append({"type": "status_changed", "ok": ok, "msg": msg})

    else:
        state["initialized"] = True
        state["first_seen_at"] = ts

    # Left open repeated warning
    if closed:
        state["not_closed_since"] = None
        state["last_open_alert_at"] = None
    else:
        if not state.get("not_closed_since"):
            state["not_closed_since"] = ts

        open_for = ts - int(state.get("not_closed_since") or ts)
        last_alert = int(state.get("last_open_alert_at") or 0)

        if open_for >= open_alert_seconds and ts - last_alert >= repeat_seconds:
            minutes = round(open_for / 60, 1)
            ok, msg = send_telegram(
                "⚠️ <b>Gate may have been left open</b>\n\n"
                f"Not closed for: <b>{h(minutes)} min</b>\n\n"
                + gate_summary(status)
            )
            state["last_open_alert_at"] = ts
            sent.append({"type": "left_open_alert", "ok": ok, "msg": msg})

    # Offline/stale repeated warning
    if offline:
        offline_since = int(state.get("offline_since") or ts)
        state["offline_since"] = offline_since

        last_offline_alert = int(state.get("last_offline_alert_at") or 0)

        if last_offline is not True or ts - last_offline_alert >= offline_repeat_seconds:
            minutes = round((ts - offline_since) / 60, 1)

            ok, msg = send_telegram(
                "⚠️ <b>Gate may be offline / status stale</b>\n\n"
                f"Reason: <b>{h(offline_reason)}</b>\n"
                f"Offline/stale for: <b>{h(minutes)} min</b>\n\n"
                + gate_summary(status)
            )

            state["last_offline_alert_at"] = ts
            sent.append({"type": "offline_alert", "ok": ok, "msg": msg})
    else:
        if last_offline is True:
            ok, msg = send_telegram("✅ <b>Gate is back online</b>\n\n" + gate_summary(status))
            sent.append({"type": "back_online", "ok": ok, "msg": msg})

        state["offline_since"] = None
        state["last_offline_alert_at"] = None

    state["last_gate"] = current_gate
    state["last_motion"] = current_motion
    state["last_position"] = current_position
    state["last_closed"] = closed
    state["last_offline"] = offline
    state["last_checked_at"] = ts
    state["last_status"] = status

    save_json(STATE_FILE, state)

    print(json.dumps({
        "ok": True,
        "closed": closed,
        "offline": offline,
        "offline_reason": offline_reason,
        "gate": current_gate,
        "motion": current_motion,
        "position": current_position,
        "sent": sent
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
