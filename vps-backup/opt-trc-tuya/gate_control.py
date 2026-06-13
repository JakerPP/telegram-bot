#!/opt/trc-tuya/venv/bin/python3

import json
import os
import sys
import time
import tinytuya

BASE_DIR = "/opt/trc-tuya"
GATE_ID = "bf24459658c5e8411b9l0q"
STATUS_FILE = "/var/www/html/trc/status.json"
STATUS_READER = "/opt/trc-tuya/gate_status_reader.py"
AUDIT_LOG = "/opt/trc-tuya/gate_audit.log"

os.chdir(BASE_DIR)


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


def read_status_file():
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_audit(action, ok, message="", before=None, after=None):
    entry = {
        "time": int(time.time()),
        "time_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": os.environ.get("TRC_GATE_SOURCE", "cli"),
        "user": os.environ.get("TRC_GATE_USER", ""),
        "remote_ip": os.environ.get("TRC_GATE_REMOTE_IP", ""),
        "action": action,
        "ok": bool(ok),
        "message": message,
        "before_gate": (before or {}).get("gate"),
        "before_motion": (before or {}).get("gate_motion"),
        "before_position": (before or {}).get("gate_position"),
        "after_gate": (after or {}).get("gate"),
        "after_motion": (after or {}).get("gate_motion"),
        "after_position": (after or {}).get("gate_position"),
    }

    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def fail(message, code=1, action="unknown", before=None):
    after = read_status_file()

    write_audit(
        action=action,
        ok=False,
        message=message,
        before=before,
        after=after
    )

    print(json.dumps({
        "ok": False,
        "error": message
    }, indent=2, ensure_ascii=False))

    sys.exit(code)


def send_property(code, value=True):
    cloud = tinytuya.Cloud()

    body = {
        "properties": json.dumps({
            code: value
        })
    }

    result = cloud.cloudrequest(
        f"/v2.0/cloud/thing/{GATE_ID}/shadow/properties/issue",
        action="POST",
        post=body
    )

    return result


def refresh_status():
    try:
        os.system(f"{STATUS_READER} >/tmp/trc_gate_status_reader.log 2>&1")
    except Exception:
        pass


def short_status(data):
    return {
        "gate": data.get("gate"),
        "motion": data.get("gate_motion"),
        "position": data.get("gate_position")
    }


def main():
    if len(sys.argv) < 2:
        fail("Usage: gate_control.py open|close|stop|partial|pedestrian|status", action="missing")

    action = sys.argv[1].lower().strip()

    if action == "status":
        refresh_status()

        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                print(f.read())
        except Exception as e:
            fail(str(e), action="status")

        return

    command_map = {
        "open": "wfh_open",
        "full": "wfh_open",
        "close": "wfh_close",
        "stop": "wfh_stop"
    }

    if action not in command_map and action not in ["partial", "pedestrian"]:
        fail("Invalid action. Use open, full, partial, pedestrian, close, stop, or status.", action=action)

    before = read_status_file()

    if action == "partial":
        open_seconds = env_float("GATE_PARTIAL_OPEN_SECONDS", os.environ.get("GATE_PARTIAL_SECONDS", "3.0"))

        open_result = send_property("wfh_open", True)
        open_ok = bool(open_result and open_result.get("success"))

        time.sleep(open_seconds)

        stop_result = send_property("wfh_stop", True)
        stop_ok = bool(stop_result and stop_result.get("success"))

        time.sleep(1)
        refresh_status()

        after = read_status_file()
        ok = open_ok and stop_ok

        message = f"Partial: open {open_seconds} sec, then stop"

        write_audit(action, ok, message, before, after)

        print(json.dumps({
            "ok": ok,
            "action": action,
            "mode": "open_stop",
            "open_seconds": open_seconds,
            "before": short_status(before),
            "after": short_status(after),
            "open_response": open_result,
            "stop_response": stop_result
        }, indent=2, ensure_ascii=False))

        if not ok:
            sys.exit(1)

        return

    if action == "pedestrian":
        open_seconds = env_float("GATE_PEDESTRIAN_OPEN_SECONDS", os.environ.get("GATE_PEDESTRIAN_SECONDS", "2.0"))
        pause_seconds = env_float("GATE_PEDESTRIAN_PAUSE_SECONDS", "3.0")

        open_result = send_property("wfh_open", True)
        open_ok = bool(open_result and open_result.get("success"))

        time.sleep(open_seconds)

        stop_result = send_property("wfh_stop", True)
        stop_ok = bool(stop_result and stop_result.get("success"))

        time.sleep(pause_seconds)

        close_result = send_property("wfh_close", True)
        close_ok = bool(close_result and close_result.get("success"))

        time.sleep(1)
        refresh_status()

        after = read_status_file()
        ok = open_ok and stop_ok and close_ok

        message = f"Pedestrian: open {open_seconds} sec, stop, wait {pause_seconds} sec, close"

        write_audit(action, ok, message, before, after)

        print(json.dumps({
            "ok": ok,
            "action": action,
            "mode": "open_stop_wait_close",
            "open_seconds": open_seconds,
            "pause_seconds": pause_seconds,
            "before": short_status(before),
            "after": short_status(after),
            "open_response": open_result,
            "stop_response": stop_result,
            "close_response": close_result
        }, indent=2, ensure_ascii=False))

        if not ok:
            sys.exit(1)

        return

    code = command_map[action]
    result = send_property(code, True)

    time.sleep(1)
    refresh_status()

    after = read_status_file()
    ok = bool(result and result.get("success"))

    if ok:
        message = "Tuya command accepted"
    else:
        message = json.dumps(result, ensure_ascii=False)

    write_audit(action, ok, message, before, after)

    print(json.dumps({
        "ok": ok,
        "action": action,
        "tuya_code": code,
        "before": short_status(before),
        "after": short_status(after),
        "tuya_response": result
    }, indent=2, ensure_ascii=False))

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
