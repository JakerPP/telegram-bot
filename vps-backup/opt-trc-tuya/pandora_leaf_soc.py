#!/opt/trc-tuya/venv/bin/python3

import asyncio
import json
import os
import time
from datetime import datetime, timezone

import aiohttp
from pandora_cas.account import PandoraOnlineAccount

BASE_DIR = "/opt/trc-tuya"
ENV_FILE = "/opt/trc-tuya/pandora.env"

PRIVATE_OUT = "/opt/trc-tuya/pandora_leaf_soc.json"
PUBLIC_LEAF_STATUS = "/var/www/html/trc/leaf_status.json"

os.chdir(BASE_DIR)


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


def clean_value(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, (str, int, float, bool)):
        return value

    try:
        return float(value)
    except Exception:
        return str(value)


def timestamp_to_unix(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())

    if isinstance(value, (int, float)):
        # Pandora sometimes returns ms timestamps
        if value > 100000000000:
            return int(value / 1000)
        return int(value)

    if isinstance(value, str):
        s = value.strip()

        if not s:
            return None

        try:
            n = float(s)
            if n > 100000000000:
                return int(n / 1000)
            return int(n)
        except Exception:
            pass

        try:
            return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except Exception:
            return None

    return None


def device_to_dict(device):
    state = getattr(device, "state", None)

    data = {
        "device_id": clean_value(getattr(device, "device_id", None)),
        "name": clean_value(getattr(device, "name", None)),
        "state_available": state is not None
    }

    if state is None:
        return data

    fields = [
        "ev_state_of_charge",
        "ev_state_of_health",
        "ev_charging_connected",
        "ev_charging_slow",
        "ev_charging_fast",
        "battery_temperature",
        "fuel",
        "voltage",
        "can_mileage",
        "mileage",
        "state_timestamp",
        "state_timestamp_utc",
        "online_timestamp",
        "online_timestamp_utc"
    ]

    for field in fields:
        data[field] = clean_value(getattr(state, field, None))

    return data


async def main_async():
    env = load_env(ENV_FILE)

    username = env.get("PANDORA_USERNAME", "").strip()
    password = env.get("PANDORA_PASSWORD", "").strip()
    device_id_raw = env.get("PANDORA_DEVICE_ID", "").strip()

    if not username or not password:
        raise SystemExit("PANDORA_USERNAME / PANDORA_PASSWORD missing in /opt/trc-tuya/pandora.env")

    async with aiohttp.ClientSession() as session:
        account = PandoraOnlineAccount(
            session,
            username=username,
            password=password,
            language="ru"
        )

        await account.async_authenticate()
        await account.async_refresh_devices()
        await account.async_request_updates()

        devices = list(account.devices.values())

        if not devices:
            raise SystemExit("No Pandora devices found")

        all_devices = [device_to_dict(d) for d in devices]

        if not device_id_raw:
            result = {
                "ok": True,
                "mode": "list_devices",
                "message": "Set PANDORA_DEVICE_ID in /opt/trc-tuya/pandora.env",
                "devices": all_devices,
                "checked_at": int(time.time())
            }

            save_json(PRIVATE_OUT, result)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        try:
            device_id = int(device_id_raw)
        except Exception:
            device_id = device_id_raw

        device = account.devices.get(device_id)

        if device is None:
            result = {
                "ok": False,
                "error": f"Device ID {device_id_raw} not found",
                "devices": all_devices,
                "checked_at": int(time.time())
            }

            save_json(PRIVATE_OUT, result)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            raise SystemExit(1)

        info = device_to_dict(device)

        soc = info.get("ev_state_of_charge")
        soh = info.get("ev_state_of_health")

        timestamp_raw = (
            info.get("state_timestamp_utc")
            or info.get("state_timestamp")
            or info.get("online_timestamp_utc")
            or info.get("online_timestamp")
        )

        ts_unix = timestamp_to_unix(timestamp_raw)
        age_seconds = None

        if ts_unix:
            age_seconds = max(0, int(time.time()) - int(ts_unix))

        result = {
            "ok": True,
            "device_id": device_id,
            "name": info.get("name"),

            "pandora_soc_percent": soc,
            "pandora_soh_percent": soh,
            "pandora_charging_connected": info.get("ev_charging_connected"),
            "pandora_charging_slow": info.get("ev_charging_slow"),
            "pandora_charging_fast": info.get("ev_charging_fast"),
            "pandora_battery_temperature": info.get("battery_temperature"),
            "pandora_12v_voltage": info.get("voltage"),
            "pandora_can_mileage": info.get("can_mileage"),
            "pandora_mileage": info.get("mileage"),

            "pandora_timestamp_raw": timestamp_raw,
            "pandora_timestamp_unix": ts_unix,
            "pandora_age_seconds": age_seconds,
            "pandora_age_minutes": round(age_seconds / 60, 1) if age_seconds is not None else None,
            "pandora_status": "fresh" if age_seconds is not None and age_seconds <= 1800 else "stale",

            "raw_state": info,
            "checked_at": int(time.time())
        }

        save_json(PRIVATE_OUT, result)

        leaf = load_json(PUBLIC_LEAF_STATUS, {})

        leaf.update({
            "pandora_soc_percent": result.get("pandora_soc_percent"),
            "pandora_soh_percent": result.get("pandora_soh_percent"),
            "pandora_charging_connected": result.get("pandora_charging_connected"),
            "pandora_charging_slow": result.get("pandora_charging_slow"),
            "pandora_charging_fast": result.get("pandora_charging_fast"),
            "pandora_battery_temperature": result.get("pandora_battery_temperature"),
            "pandora_12v_voltage": result.get("pandora_12v_voltage"),
            "pandora_can_mileage": result.get("pandora_can_mileage"),
            "pandora_age_minutes": result.get("pandora_age_minutes"),
            "pandora_status": result.get("pandora_status"),
            "pandora_checked_at": result.get("checked_at")
        })

        save_json(PUBLIC_LEAF_STATUS, leaf, mode=0o644)

        print(json.dumps(result, indent=2, ensure_ascii=False))


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
