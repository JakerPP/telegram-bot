#!/opt/trc-tuya/venv/bin/python3

import html
import json
import os
import subprocess
import time
import requests

BASE_DIR = "/opt/trc-tuya"
ENV_FILE = "/opt/trc-tuya/telegram_gate_bot.env"
LEAF_CONTROL = "/opt/trc-tuya/leaf_charger_control.py"

LEAF_STATE_FILE = "/opt/trc-tuya/leaf_charger_monitor_state.json"
LEAF_TIMER_FILE = "/opt/trc-tuya/leaf_charger_timer.json"
LEAF_TARGET_FILE = "/opt/trc-tuya/leaf_charge_target.json"

os.chdir(BASE_DIR)


def now():
    return int(time.time())


def h(value):
    return html.escape(str(value))


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


def env_float(env, key, default):
    try:
        return float(env.get(key, default))
    except Exception:
        return float(default)


def leaf_notification_keyboard():
    data = load_json("/var/www/html/trc/leaf_status.json", {})

    switch_value = data.get("switch")
    state = str(data.get("charging_state", "")).upper()

    charger_on = switch_value is True or state in ["ON", "CHARGING"]

    if charger_on:
        action_button = {"text": "🛑 Charger OFF", "callback_data": "leaf_off"}
    else:
        action_button = {"text": "🟢 Charger ON", "callback_data": "leaf_ask_on"}

    return {
        "inline_keyboard": [
            [
                {"text": "🔄 Charger Status", "callback_data": "leaf_status"},
                action_button
            ],
            [
                {"text": "🚗 Leaf Panel", "callback_data": "leaf_panel"}
            ]
        ]
    }


def send_telegram(text):
    env = load_env(ENV_FILE)

    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("ALLOWED_CHAT_ID", "0")

    if not token or chat_id == "0":
        return False, "Telegram is not configured"

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": leaf_notification_keyboard()
            },
            timeout=15
        )

        if r.status_code == 200:
            return True, "sent"

        return False, r.text[:500]

    except Exception as e:
        return False, repr(e)

def read_leaf_status():
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
        return json.loads(p.stdout)
    except Exception as e:
        return {
            "ok": False,
            "error": repr(e),
            "raw": p.stdout
        }


def leaf_off(reason):
    p = subprocess.run(
        [LEAF_CONTROL, "off"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=90,
        env={**os.environ, "TRC_LEAF_SOURCE": reason}
    )

    return p.returncode == 0, p.stdout + "\n" + p.stderr


def is_power_charging(data, current_threshold=0.5, power_threshold=0.1):
    try:
        current_a = float(data.get("current_a") or 0)
    except Exception:
        current_a = 0.0

    try:
        power_kw = float(data.get("power_kw") or 0)
    except Exception:
        power_kw = 0.0

    return current_a >= current_threshold or power_kw >= power_threshold



def get_float_value(data, key, default=0.0):
    try:
        return float(data.get(key) or default)
    except Exception:
        return default


def standby_consumption_key(data):
    # Round small standby values so 0.031/0.032 does not create repeated messages.
    current_a = get_float_value(data, "current_a", 0.0)
    power_kw = get_float_value(data, "power_kw", 0.0)

    current_bucket_a = round(current_a, 2)
    power_bucket_kw = round(power_kw, 2)

    return f"{current_bucket_a:.2f}A/{power_bucket_kw:.2f}kW"


def is_standby_consumption(data, env):
    # Small non-zero consumption: something may be connected in standby/maintenance mode.
    zero_current_max = env_float(env, "LEAF_IDLE_ZERO_CURRENT_A_MAX", 0.01)
    zero_power_max = env_float(env, "LEAF_IDLE_ZERO_POWER_KW_MAX", 0.01)

    active_current_min = env_float(env, "LEAF_CHARGE_CURRENT_A_MIN", 0.5)
    active_power_min = env_float(env, "LEAF_CHARGE_POWER_KW_MIN", 0.1)

    current_a = abs(get_float_value(data, "current_a", 0.0))
    power_kw = abs(get_float_value(data, "power_kw", 0.0))

    if current_a <= zero_current_max and power_kw <= zero_power_max:
        return False

    if current_a >= active_current_min or power_kw >= active_power_min:
        return False

    return True


def is_actual_charging(data, env):
    current_threshold = env_float(env, "LEAF_CHARGE_CURRENT_A_MIN", 0.5)
    power_threshold = env_float(env, "LEAF_CHARGE_POWER_KW_MIN", 0.1)

    power_charging = is_power_charging(data, current_threshold, power_threshold)

    if power_charging:
        return True

    if data.get("charging") is True:
        return True

    if str(data.get("charging_state", "")).upper() == "CHARGING":
        return True

    if data.get("pandora_charging_connected") is True and str(data.get("pandora_status")) == "fresh":
        return True

    return False


def tuya_lag_detected(data):
    switch_value = data.get("switch")
    return switch_value is False and is_power_charging(data)


def fmt_status(data):
    if not data.get("ok"):
        return "❌ Leaf status error:\n<code>" + h(data.get("error", "unknown")) + "</code>"

    timer = load_json(LEAF_TIMER_FILE, {"enabled": False})

    state = str(data.get("charging_state") or "UNKNOWN").upper()
    state_icon = "🔴"

    if state == "CHARGING":
        state_icon = "⚡"
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
        f"{state_icon} State: <b>{h(state)}</b>",
        f"🔘 Switch: <b>{h(switch_text)}</b>",
        f"🌐 Online: <b>{h(data.get('online_state'))}</b>",
        "",
        f"⚡ Voltage: <b>{h(data.get('voltage_v'))} V</b>",
        f"🔌 Current: <b>{h(data.get('current_a'))} A</b>",
        f"📈 Power: <b>{h(data.get('power_kw'))} kW</b>",
        f"🔋 Energy: <b>{h(data.get('energy_kwh'))} kWh</b>",
        f"🌡 Breaker temp: <b>{h(data.get('temperature_c'))} °C</b>",
        f"📟 Event: <b>{h(data.get('event'))}</b>",
    ]

    if tuya_lag_detected(data):
        lines += [
            "",
            "⚠️ <b>Tuya status lag detected</b>",
            "Breaker already reports OFF, but current/power still show charging.",
            "This usually clears on the next Tuya update."
        ]

    lines += [""]

    if pandora_soc is not None:
        lines += [
            f"🔋 Pandora SOC: <b>{h(pandora_soc)}%</b> / {h(pandora_status)} / {h(pandora_age)} min ago",
            f"🪫 SOH: <b>{h(pandora_soh)}%</b>",
            f"🔌 Pandora connected: <b>{h(pandora_connected)}</b>",
            f"🚗 Pandora fast charge: <b>{h(pandora_fast)}</b>",
            f"🌡 EV battery temp: <b>{h(data.get('pandora_battery_temperature'))} °C</b>",
        ]
    else:
        lines += [
            "🔋 Pandora SOC: <b>not available</b>"
        ]

    target = load_json(LEAF_TARGET_FILE, {"enabled": False})

    if target.get("enabled"):
        lines += [
            "",
            f"⏱ Timer: <b>{h(timer_text)}</b>",
            f"🎯 Target: <b>ON</b>",
        ]

        if target.get("mode") == "percent":
            lines += [
                f"From/To: <b>{h(target.get('current_percent'))}% → {h(target.get('target_percent'))}%</b>",
                f"Need: <b>{h(target.get('target_add_kwh'))} kWh</b>",
            ]

        lines += [
            f"Added: <b>{h(data.get('target_added_kwh'))} kWh</b>",
            f"Remaining: <b>{h(data.get('target_remaining_kwh'))} kWh</b>",
            f"Estimated SOC: <b>{h(data.get('target_estimated_percent'))}%</b>"
        ]

    return "\n".join(lines)


def fmt_full_panel(data):
    return "🚗 <b>Leaf Charger Breaker</b>\n\n" + fmt_status(data)


def get_soc_for_milestone(data):
    soc = data.get("pandora_soc_percent")
    status = data.get("pandora_status")

    if soc is not None and status == "fresh":
        try:
            return float(soc), "Pandora"
        except Exception:
            pass

    est = data.get("target_estimated_percent")
    if est is not None:
        try:
            return float(est), "estimated"
        except Exception:
            pass

    return None, None


def update_target_progress(data):
    target = load_json(LEAF_TARGET_FILE, {"enabled": False})

    if not target.get("enabled"):
        return target, False, None

    try:
        start_energy = float(target.get("start_energy_kwh"))
        target_add_kwh = float(target.get("target_add_kwh"))
        current_energy = float(data.get("energy_kwh") or 0)

        added_kwh = max(0.0, current_energy - start_energy)
        remaining_kwh = max(0.0, target_add_kwh - added_kwh)

        target["added_kwh"] = round(added_kwh, 2)
        target["remaining_kwh"] = round(remaining_kwh, 2)
        target["last_energy_kwh"] = current_energy
        target["last_checked_at"] = now()

        if target.get("mode") == "percent":
            current_percent = float(target.get("current_percent"))
            target_percent = float(target.get("target_percent"))

            estimated_percent = current_percent + (added_kwh / target_add_kwh) * (target_percent - current_percent)
            target["estimated_percent"] = round(min(target_percent, estimated_percent), 1)

        save_json(LEAF_TARGET_FILE, target)

        if added_kwh >= target_add_kwh:
            return target, True, {
                "added_kwh": added_kwh,
                "target_add_kwh": target_add_kwh
            }

    except Exception as e:
        target["last_error"] = repr(e)
        target["last_checked_at"] = now()
        save_json(LEAF_TARGET_FILE, target)

    return target, False, None


def main():
    env = load_env(ENV_FILE)

    long_charge_seconds = env_int(env, "LEAF_LONG_CHARGE_SECONDS", 12 * 3600)
    long_charge_repeat_seconds = env_int(env, "LEAF_LONG_CHARGE_REPEAT_SECONDS", 3600)

    idle_on_seconds = env_int(env, "LEAF_ON_NOT_CHARGING_SECONDS", 10 * 60)
    idle_on_repeat_seconds = env_int(env, "LEAF_ON_NOT_CHARGING_REPEAT_SECONDS", 30 * 60)

    tuya_lag_repeat_seconds = env_int(env, "LEAF_TUYA_LAG_REPEAT_SECONDS", 15 * 60)

    ts = now()
    state = load_json(LEAF_STATE_FILE, {})
    data = read_leaf_status()

    if not data.get("ok"):
        state["last_error"] = data.get("error")
        state["last_checked_at"] = ts
        save_json(LEAF_STATE_FILE, state)
        print(json.dumps({"ok": False, "state": state, "status": data}, indent=2, ensure_ascii=False))
        return

    switch_value = data.get("switch")
    charging_state = data.get("charging_state")
    actual_charging = is_actual_charging(data, env)
    power_charging = is_power_charging(data)
    lag = tuya_lag_detected(data)

    last_switch = state.get("last_switch")
    last_actual_charging = state.get("last_actual_charging")

    sent = []

    if os.environ.get("LEAF_WATCHER_TEST") == "1":
        ok, msg = send_telegram("✅ <b>Leaf watcher advanced notification test</b>\n\n" + fmt_full_panel(data))
        sent.append({"type": "test", "ok": ok, "msg": msg})

    # First run initializes state without spamming all old events
    first_run = "initialized" not in state

    if first_run:
        state["initialized"] = True
        state["first_seen_at"] = ts
        state["last_actual_charging"] = actual_charging
        state["last_switch"] = switch_value
        state["last_charging_state"] = charging_state

        soc, soc_source = get_soc_for_milestone(data)
        if soc is not None:
            state["last_soc_bucket"] = int(soc // 10) * 10

    else:
        # Breaker ON/OFF notifications
        if last_switch is not None and last_switch != switch_value:
            if switch_value is True:
                ok, msg = send_telegram("🟢 <b>Leaf charger breaker turned ON</b>\n\n" + fmt_status(data))
                sent.append({"type": "breaker_on", "ok": ok, "msg": msg})
            elif switch_value is False:
                ok, msg = send_telegram("🛑 <b>Leaf charger breaker turned OFF</b>\n\n" + fmt_status(data))
                sent.append({"type": "breaker_off", "ok": ok, "msg": msg})

        # Charging started/stopped notifications
        if last_actual_charging is not None and bool(last_actual_charging) != bool(actual_charging):
            if actual_charging:
                state["charging_started_at"] = ts
                state["last_long_charge_alert_at"] = None

                ok, msg = send_telegram("⚡ <b>Leaf charging started</b>\n\n" + fmt_status(data))
                sent.append({"type": "charging_started", "ok": ok, "msg": msg})
            else:
                ok, msg = send_telegram("✅ <b>Leaf charging stopped</b>\n\n" + fmt_status(data))
                sent.append({"type": "charging_stopped", "ok": ok, "msg": msg})

        # If charging is already true but start time missing
        if actual_charging and not state.get("charging_started_at"):
            state["charging_started_at"] = ts

        if not actual_charging:
            state["charging_started_at"] = None
            state["last_long_charge_alert_at"] = None

    # ON but not charging / small standby-consumption warning
    if switch_value is True and not actual_charging:
        if is_zero_consumption(data, env):
            if not state.get("on_not_charging_since"):
                state["on_not_charging_since"] = ts

            idle_for = ts - int(state.get("on_not_charging_since") or ts)
            last_idle_alert = int(state.get("last_on_not_charging_alert_at") or 0)

            if idle_on_repeat_seconds <= 0:
                repeat_ok = last_idle_alert == 0
            else:
                repeat_ok = ts - last_idle_alert >= idle_on_repeat_seconds

            if idle_for >= idle_on_seconds and repeat_ok:
                minutes = round(idle_for / 60, 1)

                ok, msg = send_telegram(
                    "⚠️ <b>Leaf charger is ON but not charging</b>\n\n"
                    f"ON without charging for: <b>{h(minutes)} min</b>\n\n"
                    + fmt_status(data)
                )

                state["last_on_not_charging_alert_at"] = ts
                sent.append({"type": "on_not_charging_zero", "ok": ok, "msg": msg})

            state["standby_since"] = None

        elif is_standby_consumption(data, env):
            if not state.get("standby_since"):
                state["standby_since"] = ts

            standby_for = ts - int(state.get("standby_since") or ts)
            standby_key = standby_consumption_key(data)
            last_key = state.get("last_standby_question_key")

            if standby_for >= idle_on_seconds and standby_key != last_key:
                minutes = round(standby_for / 60, 1)

                ok, msg = send_telegram(
                    "🟡 <b>Small standby consumption detected</b>\n\n"
                    f"Breaker is ON, but this is not active charging.\n"
                    f"Current consumption: <b>{h(data.get('current_a'))} A</b> / <b>{h(data.get('power_kw'))} kW</b>\n"
                    f"Duration: <b>{h(minutes)} min</b>\n\n"
                    "Is something connected in standby/maintenance mode?\n"
                    "If yes — you can ignore this message.\n"
                    "If no — press <b>Charger OFF</b>.\n\n"
                    + fmt_status(data)
                )

                state["last_standby_question_key"] = standby_key
                state["last_standby_question_at"] = ts
                sent.append({"type": "standby_question", "ok": ok, "msg": msg})

            state["on_not_charging_since"] = None
            state["last_on_not_charging_alert_at"] = None

        else:
            state["on_not_charging_since"] = None
            state["last_on_not_charging_alert_at"] = None
            state["standby_since"] = None

    else:
        state["on_not_charging_since"] = None
        state["last_on_not_charging_alert_at"] = None
        state["standby_since"] = None

        # If breaker is OFF or real charging started, allow a new standby question next time.
        if switch_value is not True or actual_charging:
            state["last_standby_question_key"] = None
            state["last_standby_question_at"] = None

    # Long charging warning
    if actual_charging:
        if not state.get("charging_started_at"):
            state["charging_started_at"] = ts

        charging_for = ts - int(state.get("charging_started_at") or ts)
        last_long_alert = int(state.get("last_long_charge_alert_at") or 0)

        if charging_for >= long_charge_seconds and ts - last_long_alert >= long_charge_repeat_seconds:
            hours = round(charging_for / 3600, 1)

            ok, msg = send_telegram(
                "⚠️ <b>Leaf has been charging for a long time</b>\n\n"
                f"Charging for: <b>{h(hours)} hours</b>\n\n"
                + fmt_status(data)
            )

            state["last_long_charge_alert_at"] = ts
            sent.append({"type": "long_charge", "ok": ok, "msg": msg})
    else:
        state["last_long_charge_alert_at"] = None

    # Tuya lag warning
    if lag:
        last_lag_alert = int(state.get("last_tuya_lag_alert_at") or 0)

        if ts - last_lag_alert >= tuya_lag_repeat_seconds:
            ok, msg = send_telegram("⚠️ <b>Tuya status lag detected</b>\n\n" + fmt_status(data))
            state["last_tuya_lag_alert_at"] = ts
            sent.append({"type": "tuya_lag", "ok": ok, "msg": msg})
    else:
        state["last_tuya_lag_alert_at"] = None

    # Target kWh / percent auto-off
    target, reached, reached_info = update_target_progress(data)

    # SOC each 10% notification
    soc, soc_source = get_soc_for_milestone(data)
    if soc is not None and (actual_charging or target.get("enabled")):
        bucket = int(soc // 10) * 10
        last_bucket = state.get("last_soc_bucket")

        if last_bucket is None:
            state["last_soc_bucket"] = bucket
        elif bucket > int(last_bucket):
            ok, msg = send_telegram(
                f"🔋 <b>Leaf SOC reached {h(bucket)}%</b>\n\n"
                f"Source: <b>{h(soc_source)}</b>\n\n"
                + fmt_status(data)
            )
            state["last_soc_bucket"] = bucket
            sent.append({"type": "soc_10_percent", "ok": ok, "msg": msg})
        elif bucket < int(last_bucket) - 10:
            # Reset after discharge / new day / stale value
            state["last_soc_bucket"] = bucket

    if reached:
        ok, output = leaf_off("target_kwh")

        target["enabled"] = False
        target["completed_at"] = ts
        target["completed_ok"] = ok
        target["completed_output"] = output[-3000:]
        save_json(LEAF_TARGET_FILE, target)

        fresh = read_leaf_status()

        if ok:
            ok2, msg = send_telegram(
                "🎯🛑 <b>Leaf charge target reached</b>\n\n"
                f"Added: <b>{round(reached_info.get('added_kwh'), 2)} kWh</b>\n"
                f"Target: <b>{round(reached_info.get('target_add_kwh'), 2)} kWh</b>\n\n"
                + fmt_status(fresh)
            )
            sent.append({"type": "target_reached", "ok": ok2, "msg": msg})
        else:
            ok2, msg = send_telegram(
                "❌ <b>Leaf target reached, but OFF failed</b>\n\n"
                f"<code>{h(output[-3000:])}</code>\n\n"
                + fmt_status(fresh)
            )
            sent.append({"type": "target_off_failed", "ok": ok2, "msg": msg})

    # Timer auto-off
    timer = load_json(LEAF_TIMER_FILE, {"enabled": False})

    if timer.get("enabled") and timer.get("off_at"):
        off_at = int(timer.get("off_at"))

        if ts >= off_at:
            ok, output = leaf_off("timer")

            timer["enabled"] = False
            timer["completed_at"] = ts
            timer["last_result_ok"] = ok
            timer["last_result"] = output[-3000:]
            save_json(LEAF_TIMER_FILE, timer)

            fresh = read_leaf_status()

            if ok:
                ok2, msg = send_telegram(
                    "⏱🛑 <b>Leaf charger timer completed</b>\n\n"
                    "Breaker OFF command sent.\n\n"
                    + fmt_status(fresh)
                )
                sent.append({"type": "timer_completed", "ok": ok2, "msg": msg})
            else:
                ok2, msg = send_telegram(
                    "❌ <b>Leaf timer failed to turn OFF</b>\n\n"
                    f"<code>{h(output[-3000:])}</code>\n\n"
                    + fmt_status(fresh)
                )
                sent.append({"type": "timer_failed", "ok": ok2, "msg": msg})

    # Temperature warning
    try:
        temp_c = data.get("temperature_c")
        if temp_c is not None and float(temp_c) >= 55:
            last_temp_alert = int(state.get("last_temp_alert_at", 0))

            if ts - last_temp_alert > 15 * 60:
                ok, msg = send_telegram("🌡 <b>Leaf charger temperature warning</b>\n\n" + fmt_status(data))
                state["last_temp_alert_at"] = ts
                sent.append({"type": "temperature", "ok": ok, "msg": msg})
    except Exception:
        pass

    state["last_switch"] = switch_value
    state["last_charging_state"] = charging_state
    state["last_actual_charging"] = actual_charging
    state["last_power_kw"] = data.get("power_kw")
    state["last_checked_at"] = ts
    state["last_status"] = data

    save_json(LEAF_STATE_FILE, state)

    print(json.dumps({
        "ok": True,
        "actual_charging": actual_charging,
        "switch": switch_value,
        "charging_state": charging_state,
        "power_kw": data.get("power_kw"),
        "soc": data.get("pandora_soc_percent"),
        "sent": sent
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
