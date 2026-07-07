#!/usr/bin/env python3
"""One-time safe patch for the TRC Leaf charger scripts.

Changes:
- removes Pandora SOH from bot-facing data/messages;
- uses wall-kWh-per-percent calibration for percent targets;
- disables percent target auto-off by Pandora SOC;
- treats breaker OFF as not charging even with stale Tuya current;
- treats 0.033 A / 0.0 kW as idle;
- sends every crossed 10% milestone, including skipped buckets.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path("/opt/trc-tuya")
BOT = BASE / "telegram_gate_bot.py"
WATCHER = BASE / "leaf_charger_watcher.py"
BG = BASE / "leaf_bg_action.py"
PANDORA = BASE / "pandora_leaf_soc.py"
ENV = BASE / "telegram_gate_bot.env"
TARGET = BASE / "leaf_charge_target.json"
MONITOR = BASE / "leaf_charger_monitor_state.json"
PUBLIC_STATUS = Path("/var/www/html/trc/leaf_status.json")
PRIVATE_PANDORA = BASE / "pandora_leaf_soc.json"


def replace_one(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S | re.M)
    if count != 1:
        raise RuntimeError(f"Could not replace {label}; found {count} matching blocks")
    return updated


def strip_soh_display_lines(text: str) -> str:
    kept = []
    for line in text.splitlines():
        if "SOH:" in line:
            continue
        if re.match(r"\s*pandora_soh\s*=", line):
            continue
        kept.append(line)
    return "\n".join(kept) + "\n"


def load_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)


def save_json(path: Path, data: dict, mode: int = 0o600) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(mode)
    except Exception:
        pass


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []

    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)

    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    for path in (BOT, WATCHER, BG, PANDORA):
        if not path.exists():
            raise SystemExit(f"Missing required file: {path}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups = []
    for path in (BOT, WATCHER, BG, PANDORA, ENV):
        if path.exists():
            backup = path.with_name(path.name + f".before-leaf-v3.{stamp}")
            shutil.copy2(path, backup)
            backups.append(str(backup))

    # --- Telegram bot: calibrated percent target ---
    bot = BOT.read_text(encoding="utf-8")
    new_target_function = '''def create_charge_target_by_percent(current_percent, target_percent, chat_id):
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

    # Empirical calibration from the wall-energy meter.
    # Do not use Pandora SOH for calculations.
    wall_kwh_per_percent = env_float("LEAF_WALL_KWH_PER_PERCENT", 0.5465)
    stop_reserve_kwh = env_float("LEAF_TARGET_STOP_RESERVE_KWH", 0.10)

    if wall_kwh_per_percent <= 0:
        return False, "LEAF_WALL_KWH_PER_PERCENT must be greater than zero."

    raw_wall_needed_kwh = (target_percent - current_percent) * wall_kwh_per_percent
    target_add_kwh = max(0.05, raw_wall_needed_kwh - max(0.0, stop_reserve_kwh))

    data = {
        "enabled": True,
        "mode": "percent",
        "current_percent": current_percent,
        "target_percent": target_percent,
        "calibration": "wall_kwh_per_percent",
        "wall_kwh_per_percent": round(wall_kwh_per_percent, 4),
        "target_stop_reserve_kwh": round(max(0.0, stop_reserve_kwh), 2),
        "raw_wall_needed_kwh": round(raw_wall_needed_kwh, 2),
        "target_add_kwh": round(target_add_kwh, 2),
        "start_energy_kwh": status.get("energy_kwh"),
        "created_at": now(),
        "created_by": str(chat_id),
        "added_kwh": 0,
        "remaining_kwh": round(target_add_kwh, 2),
        "estimated_percent": current_percent
    }

    save_json(LEAF_TARGET_FILE, data)
    return True, data


'''
    bot = replace_one(
        bot,
        r"^def create_charge_target_by_percent\(.*?(?=^def refresh_pandora_soc\()",
        new_target_function,
        "create_charge_target_by_percent",
    )
    bot = strip_soh_display_lines(bot)

    # --- Watcher: stable charging state, idle threshold, milestones, energy target ---
    watcher = WATCHER.read_text(encoding="utf-8")

    new_idle_functions = '''def is_zero_consumption(data, env):
    # Tiny EVSE standby draw, e.g. 0.033 A / 0.0 kW, is not a warning.
    zero_current_max = env_float(env, "LEAF_IDLE_ZERO_CURRENT_A_MAX", 0.05)
    zero_power_max = env_float(env, "LEAF_IDLE_ZERO_POWER_KW_MAX", 0.02)

    current_a = abs(get_float_value(data, "current_a", 0.0))
    power_kw = abs(get_float_value(data, "power_kw", 0.0))
    return current_a <= zero_current_max and power_kw <= zero_power_max


def is_standby_consumption(data, env):
    if is_zero_consumption(data, env):
        return False

    active_current_min = env_float(env, "LEAF_CHARGE_CURRENT_A_MIN", 0.5)
    active_power_min = env_float(env, "LEAF_CHARGE_POWER_KW_MIN", 0.1)

    current_a = abs(get_float_value(data, "current_a", 0.0))
    power_kw = abs(get_float_value(data, "power_kw", 0.0))

    if current_a >= active_current_min or power_kw >= active_power_min:
        return False

    return True


'''
    if re.search(r"^def is_zero_consumption\(", watcher, flags=re.M):
        watcher = replace_one(
            watcher,
            r"^def is_zero_consumption\(.*?(?=^def is_actual_charging\()",
            new_idle_functions,
            "idle consumption functions",
        )
    else:
        watcher = replace_one(
            watcher,
            r"^def is_standby_consumption\(.*?(?=^def is_actual_charging\()",
            new_idle_functions,
            "idle consumption functions",
        )

    new_actual_charging = '''def is_actual_charging(data, env):
    # Relay OFF always wins over stale Tuya current/power telemetry.
    switch_value = data.get("switch")
    charging_state = str(data.get("charging_state", "")).upper()
    event = str(data.get("event", "")).upper()

    if switch_value is False or charging_state == "OFF" or event == "REMOTE_OFF":
        return False

    current_threshold = env_float(env, "LEAF_CHARGE_CURRENT_A_MIN", 0.5)
    power_threshold = env_float(env, "LEAF_CHARGE_POWER_KW_MIN", 0.1)

    if switch_value is not True:
        return False

    if is_power_charging(data, current_threshold, power_threshold):
        return True

    if data.get("charging") is True:
        return True

    return charging_state == "CHARGING"


'''
    watcher = replace_one(
        watcher,
        r"^def is_actual_charging\(.*?(?=^def tuya_lag_detected\()",
        new_actual_charging,
        "is_actual_charging",
    )

    new_tuya_lag = '''def tuya_lag_detected(data):
    # Ignore short stale readings after OFF; notify only if it still looks substantial.
    if data.get("switch") is not False:
        return False

    current_a = abs(get_float_value(data, "current_a", 0.0))
    power_kw = abs(get_float_value(data, "power_kw", 0.0))
    return current_a >= 5.0 or power_kw >= 0.20


'''
    watcher = replace_one(
        watcher,
        r"^def tuya_lag_detected\(.*?(?=^def fmt_status\()",
        new_tuya_lag,
        "tuya_lag_detected",
    )

    new_milestone_source = '''def get_soc_for_milestone(data):
    # During a percent target, use the wall-energy meter, not Pandora SOH/SOC.
    target = load_json(LEAF_TARGET_FILE, {"enabled": False})

    if target.get("enabled") and target.get("mode") == "percent":
        try:
            start_energy = float(target.get("start_energy_kwh"))
            current_energy = float(data.get("energy_kwh"))
            start_percent = float(target.get("current_percent"))
            wall_kwh_per_percent = float(target.get("wall_kwh_per_percent") or 0.5465)

            if wall_kwh_per_percent > 0:
                added_kwh = max(0.0, current_energy - start_energy)
                estimated_percent = min(100.0, start_percent + added_kwh / wall_kwh_per_percent)
                return estimated_percent, "счётчик энергии"
        except Exception:
            pass

    # Outside an active percent target, Pandora SOC remains a best-effort fallback.
    soc = data.get("pandora_soc_percent")
    if soc is not None and data.get("pandora_status") == "fresh":
        try:
            return float(soc), "Pandora"
        except Exception:
            pass

    return None, None


'''
    watcher = replace_one(
        watcher,
        r"^def get_soc_for_milestone\(.*?(?=^def update_target_progress\()",
        new_milestone_source,
        "get_soc_for_milestone",
    )

    new_target_progress = '''def update_target_progress(data):
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
            start_percent = float(target.get("current_percent"))
            wall_kwh_per_percent = float(target.get("wall_kwh_per_percent") or 0.5465)
            if wall_kwh_per_percent > 0:
                target["estimated_percent"] = round(
                    min(100.0, start_percent + added_kwh / wall_kwh_per_percent),
                    1,
                )

        save_json(LEAF_TARGET_FILE, target)

        # Percent targets stop only by the meter target, never by Pandora SOH/SOC.
        if added_kwh >= target_add_kwh:
            return target, True, {
                "added_kwh": added_kwh,
                "target_add_kwh": target_add_kwh,
                "reason": "energy_meter",
            }

    except Exception as e:
        target["last_error"] = repr(e)
        target["last_checked_at"] = now()
        save_json(LEAF_TARGET_FILE, target)

    return target, False, None


'''
    watcher = replace_one(
        watcher,
        r"^def update_target_progress\(.*?(?=^def main\()",
        new_target_progress,
        "update_target_progress",
    )

    # Remove a previous experimental auto-OFF block that used fresh Pandora SOC.
    watcher, _ = re.subn(
        r"^    # Percent target safety:.*?(?=^    switch_value = data\.get\(\"switch\"\))",
        "",
        watcher,
        count=1,
        flags=re.S | re.M,
    )

    new_milestones = '''    # Notify every crossed 10% boundary. If one polling cycle jumps
    # from 39% to 56%, both the 40% and 50% messages are sent.
    soc, soc_source = get_soc_for_milestone(data)
    if soc is not None and (actual_charging or target.get("enabled")):
        bucket = int(float(soc) // 10) * 10
        last_bucket = state.get("last_soc_bucket")

        if last_bucket is None:
            state["last_soc_bucket"] = bucket
        else:
            last_bucket = int(last_bucket)

            if bucket < last_bucket - 10:
                # New session / discharge / new selected starting percentage.
                state["last_soc_bucket"] = bucket
            elif bucket > last_bucket:
                for crossed_bucket in range(last_bucket + 10, bucket + 1, 10):
                    ok, msg = send_telegram(
                        f"🔋 <b>Заряд Leaf достиг {h(crossed_bucket)}%</b>\n\n"
                        f"Источник: <b>{h(soc_source)}</b>\n"
                        f"Текущая оценка: <b>{h(round(float(soc), 1))}%</b>\n\n"
                        + fmt_status(data)
                    )
                    sent.append({"type": "soc_10_percent", "bucket": crossed_bucket, "ok": ok, "msg": msg})

                state["last_soc_bucket"] = bucket

'''
    watcher = replace_one(
        watcher,
        r"^    # SOC each 10% notification\n.*?(?=^    if reached:)",
        new_milestones,
        "10 percent notification block",
    )

    watcher = strip_soh_display_lines(watcher)
    bg = strip_soh_display_lines(BG.read_text(encoding="utf-8"))

    # Stop collecting/publishing the untrusted Pandora SOH value entirely.
    pandora = PANDORA.read_text(encoding="utf-8")
    pandora = pandora.replace('        "ev_state_of_health",\n', "")
    pandora = pandora.replace('        soh = info.get("ev_state_of_health")\n', "")
    pandora = pandora.replace('            "pandora_soh_percent": soh,\n', "")
    pandora = pandora.replace('            "pandora_soh_percent": result.get("pandora_soh_percent"),\n', "")

    BOT.write_text(bot, encoding="utf-8")
    WATCHER.write_text(watcher, encoding="utf-8")
    BG.write_text(bg, encoding="utf-8")
    PANDORA.write_text(pandora, encoding="utf-8")

    set_env_values(
        ENV,
        {
            "LEAF_WALL_KWH_PER_PERCENT": "0.5465",
            "LEAF_TARGET_STOP_RESERVE_KWH": "0.10",
            "LEAF_IDLE_ZERO_CURRENT_A_MAX": "0.05",
            "LEAF_IDLE_ZERO_POWER_KW_MAX": "0.02",
        },
    )

    # Existing active target used the old 62 kWh formula; disable it safely.
    target = load_json(TARGET, {"enabled": False})
    if target.get("enabled"):
        target["enabled"] = False
        target["cancel_reason"] = "recalibrated_to_wall_kwh_per_percent"
        save_json(TARGET, target)

    # First watcher run after the patch establishes a quiet new baseline.
    state = load_json(MONITOR, {})
    for key in (
        "initialized",
        "last_actual_charging",
        "last_switch",
        "last_charging_state",
        "last_soc_bucket",
        "charging_started_at",
        "charging_stopped_alert_sent",
        "last_tuya_lag_alert_at",
        "tuya_lag_since",
    ):
        state.pop(key, None)
    save_json(MONITOR, state)

    # Remove legacy SOH field from cached JSON too.
    for path, mode in ((PUBLIC_STATUS, 0o644), (PRIVATE_PANDORA, 0o600)):
        data = load_json(path, {})
        if "pandora_soh_percent" in data:
            data.pop("pandora_soh_percent", None)
            save_json(path, data, mode)

    print("PATCH_OK")
    print("Backups:")
    for backup in backups:
        print(" -", backup)
    print("Calibration: 0.5465 kWh from wall per 1% SOC")
    print("Target reserve: 0.10 kWh")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"PATCH_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
