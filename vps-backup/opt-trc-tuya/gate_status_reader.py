#!/opt/trc-tuya/venv/bin/python3

import json
import os
import sys
import time
import tinytuya

BASE_DIR = "/opt/trc-tuya"
GATE_ID = "bf24459658c5e8411b9l0q"
STATUS_FILE = "/var/www/html/trc/status.json"

os.chdir(BASE_DIR)


def load_existing_status():
    data = {
        "vps": "ONLINE",
        "asterisk": "ONLINE",
        "gate": "UNKNOWN",
        "gate_motion": "UNKNOWN"
    }

    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
                if isinstance(existing, dict):
                    data.update(existing)
    except Exception:
        pass

    return data


def get_prop(props, code, default=None):
    for item in props:
        if item.get("code") == code:
            return item.get("value", default)
    return default


def get_prop_time(props, code, default=None):
    for item in props:
        if item.get("code") == code:
            return item.get("time", default)
    return default


def determine_gate_status(l_close, l_open):
    if l_close is True and l_open is False:
        return "CLOSED"

    if l_open is True and l_close is False:
        return "OPENED"

    if l_close is True and l_open is True:
        return "SENSOR_ERR"

    if l_close is False and l_open is False:
        return "PARTIAL"

    return "UNKNOWN"


def determine_motion(old_data, gate_status, return_state):
    old_position = old_data.get("gate_position")

    if gate_status in ["CLOSED", "OPENED", "SENSOR_ERR"]:
        return gate_status, 0

    try:
        if old_position is not None and return_state is not None:
            delta = int(return_state) - int(old_position)

            if delta > 0:
                return "OPENING", delta
            if delta < 0:
                return "CLOSING", delta

            return "STOPPED", 0
    except Exception:
        pass

    return "PARTIAL", None


def save_status(gate_status, props):
    old_data = load_existing_status()

    l_close = get_prop(props, "l_close")
    l_open = get_prop(props, "l_open")
    return_state = get_prop(props, "return_state")
    rssi = get_prop(props, "wfh_rssi")
    inrf = get_prop(props, "inrf")
    fsw = get_prop(props, "fsw")

    gate_motion, position_delta = determine_motion(old_data, gate_status, return_state)

    data = dict(old_data)
    data["vps"] = "ONLINE"
    data["asterisk"] = data.get("asterisk", "ONLINE")
    data["gate"] = gate_status
    data["gate_motion"] = gate_motion
    data["gate_source"] = "tuya_shadow"
    data["gate_position"] = return_state
    data["gate_position_delta"] = position_delta
    data["gate_l_close"] = l_close
    data["gate_l_open"] = l_open
    data["gate_infrared"] = inrf
    data["gate_ground_sensor"] = fsw
    data["gate_rssi"] = rssi
    data["gate_checked_at"] = int(time.time())
    data["gate_l_close_time"] = get_prop_time(props, "l_close")
    data["gate_l_open_time"] = get_prop_time(props, "l_open")
    data["gate_position_time"] = get_prop_time(props, "return_state")

    tmp = STATUS_FILE + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    os.replace(tmp, STATUS_FILE)
    os.chmod(STATUS_FILE, 0o664)

    return data


def read_gate_shadow():
    cloud = tinytuya.Cloud()

    result = cloud.cloudrequest(
        f"/v2.0/cloud/thing/{GATE_ID}/shadow/properties"
    )

    if not result or not result.get("success"):
        return {
            "ok": False,
            "error": "Cannot read Tuya shadow properties",
            "tuya_response": result
        }

    props = result.get("result", {}).get("properties", [])

    l_close = get_prop(props, "l_close")
    l_open = get_prop(props, "l_open")
    return_state = get_prop(props, "return_state")

    gate_status = determine_gate_status(l_close, l_open)
    saved = save_status(gate_status, props)

    return {
        "ok": True,
        "gate": gate_status,
        "motion": saved.get("gate_motion"),
        "position": return_state,
        "l_close": l_close,
        "l_open": l_open,
        "rssi": get_prop(props, "wfh_rssi"),
        "saved_status": saved
    }


def main():
    result = read_gate_shadow()
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
