#!/opt/trc-tuya/venv/bin/python3

import base64
import json
import os
import sys
import time
import tinytuya

BASE_DIR = "/opt/trc-tuya"
DEVICE_ID = "bfc518708417c50977tlrg"

STATUS_FILE = "/opt/trc-tuya/leaf_charger_status.json"
PUBLIC_STATUS_FILE = "/var/www/html/trc/leaf_status.json"
TARGET_FILE = "/opt/trc-tuya/leaf_charge_target.json"
PANDORA_SOC_FILE = "/opt/trc-tuya/pandora_leaf_soc.json"
AUDIT_LOG = "/opt/trc-tuya/leaf_charger_audit.log"

os.chdir(BASE_DIR)


def get_prop(props, code, default=None):
    for item in props:
        if item.get("code") == code:
            return item.get("value", default)
    return default


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


def decode_phase_a(value):
    result = {
        "voltage_v": None,
        "current_a": None,
        "power_w": None,
        "power_kw": None,
        "raw_hex": None
    }

    if not value:
        return result

    try:
        raw = base64.b64decode(value)
        result["raw_hex"] = raw.hex()

        if len(raw) >= 8:
            voltage_raw = int.from_bytes(raw[0:2], "big")
            current_raw = int.from_bytes(raw[2:5], "big")
            power_raw = int.from_bytes(raw[5:8], "big")

            result["voltage_v"] = round(voltage_raw / 10.0, 1)
            result["current_a"] = round(current_raw / 1000.0, 3)
            result["power_w"] = round(power_raw / 10.0, 1)
            result["power_kw"] = round((power_raw / 10.0) / 1000.0, 3)
    except Exception:
        pass

    return result


def add_target_info(data):
    target = load_json(TARGET_FILE, {"enabled": False})

    data["target_enabled"] = bool(target.get("enabled"))
    data["target_status"] = "ON" if target.get("enabled") else "OFF"
    data["target_mode"] = target.get("mode")
    data["target_add_kwh"] = target.get("target_add_kwh")
    data["target_added_kwh"] = target.get("added_kwh")
    data["target_remaining_kwh"] = target.get("remaining_kwh")
    data["target_estimated_percent"] = target.get("estimated_percent")
    data["target_current_percent"] = target.get("current_percent")
    data["target_percent"] = target.get("target_percent")

    if not target.get("enabled"):
        data["target_display"] = "OFF"
    elif target.get("remaining_kwh") is not None:
        try:
            data["target_display"] = f"REM {float(target.get('remaining_kwh')):.1f}kWh"
        except Exception:
            data["target_display"] = "ACTIVE"
    else:
        data["target_display"] = "ACTIVE"

    return data


def merge_pandora_data(data):
    pandora = load_json(PANDORA_SOC_FILE, {})

    if not pandora.get("ok"):
        return data

    keys = [
        "pandora_soc_percent",
        "pandora_soh_percent",
        "pandora_charging_connected",
        "pandora_charging_slow",
        "pandora_charging_fast",
        "pandora_battery_temperature",
        "pandora_12v_voltage",
        "pandora_can_mileage",
        "pandora_mileage",
        "pandora_age_minutes",
        "pandora_status",
        "pandora_checked_at"
    ]

    for key in keys:
        if key in pandora:
            data[key] = pandora.get(key)

    if "checked_at" in pandora:
        data["pandora_checked_at"] = pandora.get("checked_at")

    return data


def write_status_files(data):
    data = add_target_info(data)
    data = merge_pandora_data(data)

    save_json(STATUS_FILE, data)

    try:
        save_json(PUBLIC_STATUS_FILE, data)
        os.chmod(PUBLIC_STATUS_FILE, 0o644)
    except Exception:
        pass

def read_shadow():
    cloud = tinytuya.Cloud()

    result = cloud.cloudrequest(
        f"/v2.0/cloud/thing/{DEVICE_ID}/shadow/properties"
    )

    if not result or not result.get("success"):
        return {
            "ok": False,
            "device_id": DEVICE_ID,
            "error": "Cannot read Leaf charger shadow",
            "tuya_response": result,
            "checked_at": int(time.time())
        }

    props = result.get("result", {}).get("properties", [])

    phase_raw = get_prop(props, "phase_a")
    phase = decode_phase_a(phase_raw)

    switch_value = get_prop(props, "switch")
    online_state = get_prop(props, "online_state")
    event = get_prop(props, "event")
    relay_status = get_prop(props, "relay_status")

    forward_energy_total_raw = get_prop(props, "forward_energy_total")
    if forward_energy_total_raw is None:
        forward_energy_total_raw = get_prop(props, "add_ele")

    temp_a_raw = get_prop(props, "temp_a")
    leakage_current = get_prop(props, "leakage_current")

    energy_kwh = None
    temp_c = None

    try:
        if forward_energy_total_raw is not None:
            energy_kwh = round(float(forward_energy_total_raw) / 100.0, 2)
    except Exception:
        pass

    try:
        if temp_a_raw is not None:
            temp_c = round(float(temp_a_raw) / 10.0, 1)
    except Exception:
        pass

    charging = False
    charging_state = "OFF"

    if switch_value is True:
        charging_state = "ON"

        if phase.get("current_a") is not None and phase["current_a"] >= 0.5:
            charging = True
            charging_state = "CHARGING"

        if phase.get("power_kw") is not None and phase["power_kw"] >= 0.1:
            charging = True
            charging_state = "CHARGING"

    data = {
        "ok": True,
        "device_id": DEVICE_ID,
        "name": "Charger Breaker",
        "online_state": online_state,
        "switch": switch_value,
        "charging": charging,
        "charging_state": charging_state,
        "voltage_v": phase.get("voltage_v"),
        "current_a": phase.get("current_a"),
        "power_kw": phase.get("power_kw"),
        "power_w": phase.get("power_w"),
        "energy_kwh": energy_kwh,
        "leakage_current_ma": leakage_current,
        "temperature_c": temp_c,
        "event": event,
        "relay_status": relay_status,
        "phase_a_raw": phase_raw,
        "phase_a_hex": phase.get("raw_hex"),
        "checked_at": int(time.time())
    }

    write_status_files(data)
    return data


def write_audit(action, ok, before=None, after=None, message=""):
    entry = {
        "time": int(time.time()),
        "time_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": os.environ.get("TRC_LEAF_SOURCE", "cli"),
        "user": os.environ.get("TRC_LEAF_USER", ""),
        "action": action,
        "ok": bool(ok),
        "message": message,
        "before_switch": (before or {}).get("switch"),
        "before_state": (before or {}).get("charging_state"),
        "before_power_kw": (before or {}).get("power_kw"),
        "after_switch": (after or {}).get("switch"),
        "after_state": (after or {}).get("charging_state"),
        "after_power_kw": (after or {}).get("power_kw")
    }

    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def send_switch(value):
    cloud = tinytuya.Cloud()

    result = cloud.cloudrequest(
        f"/v2.0/cloud/thing/{DEVICE_ID}/shadow/properties/issue",
        action="POST",
        post={
            "properties": json.dumps({
                "switch": bool(value)
            })
        }
    )

    if result and result.get("success"):
        return {
            "v2": result,
            "success": True
        }

    result_v1 = cloud.cloudrequest(
        f"/v1.0/devices/{DEVICE_ID}/commands",
        action="POST",
        post={
            "commands": [
                {
                    "code": "switch",
                    "value": bool(value)
                }
            ]
        }
    )

    return {
        "v2": result,
        "v1": result_v1,
        "success": bool(result_v1 and result_v1.get("success"))
    }


def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "ok": False,
            "error": "Usage: leaf_charger_control.py status|on|off"
        }, indent=2))
        sys.exit(1)

    action = sys.argv[1].lower().strip()

    if action == "status":
        data = read_shadow()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        if not data.get("ok"):
            sys.exit(1)
        return

    if action not in ["on", "off"]:
        print(json.dumps({
            "ok": False,
            "error": "Invalid action. Use status, on, or off."
        }, indent=2))
        sys.exit(1)

    before = read_shadow()
    value = action == "on"
    result = send_switch(value)

    time.sleep(2)
    after = read_shadow()

    ok = bool(result and result.get("success"))

    write_audit(
        action=action,
        ok=ok,
        before=before,
        after=after,
        message=json.dumps(result, ensure_ascii=False)
    )

    print(json.dumps({
        "ok": ok,
        "action": action,
        "before": before,
        "after": after,
        "tuya_response": result
    }, indent=2, ensure_ascii=False))

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
