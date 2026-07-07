#!/usr/bin/env python3
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path("/opt/trc-tuya")
WATCHER = BASE / "leaf_charger_watcher.py"
ENV = BASE / "telegram_gate_bot.env"
PYTHON = "/opt/trc-tuya/venv/bin/python3"

def replace_one(text, pattern, replacement, label):
    new_text, n = re.subn(pattern, lambda _m: replacement, text, count=1, flags=re.S | re.M)
    if n != 1:
        raise RuntimeError(f"{label}: expected one replacement, got {n}")
    return new_text

def main():
    if not WATCHER.exists():
        raise RuntimeError(f"Missing {WATCHER}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = WATCHER.with_name(WATCHER.name + f".before-pandora-primary.{stamp}")
    shutil.copy2(WATCHER, backup)

    try:
        s = WATCHER.read_text(encoding="utf-8")

        if 'PANDORA_SCRIPT = "/opt/trc-tuya/pandora_leaf_soc.py"' not in s:
            old = 'LEAF_CONTROL = "/opt/trc-tuya/leaf_charger_control.py"\n'
            new = old + 'PANDORA_SCRIPT = "/opt/trc-tuya/pandora_leaf_soc.py"\nPANDORA_LOCK = "/opt/trc-tuya/pandora_leaf_soc.lock"\n'
            if old not in s:
                raise RuntimeError("LEAF_CONTROL marker not found")
            s = s.replace(old, new, 1)

        # Replace standby / actual-charging / lag logic as one coherent group.
        electrical = r'''def is_zero_consumption(data, env):
    # 0.033 A / 0.0 kW is normal EVSE idle draw.
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
    return current_a < active_current_min and power_kw < active_power_min


def is_actual_charging(data, env):
    # Relay OFF wins over stale Tuya current for the few seconds after OFF.
    if data.get("switch") is False:
        return False

    state = str(data.get("charging_state", "")).upper()
    event = str(data.get("event", "")).upper()

    if state == "OFF" or event == "REMOTE_OFF" or data.get("switch") is not True:
        return False

    current_min = env_float(env, "LEAF_CHARGE_CURRENT_A_MIN", 0.5)
    power_min = env_float(env, "LEAF_CHARGE_POWER_KW_MIN", 0.1)

    return (
        is_power_charging(data, current_min, power_min)
        or data.get("charging") is True
        or state == "CHARGING"
    )


def tuya_lag_detected(data):
    if data.get("switch") is not False:
        return False

    current_a = abs(get_float_value(data, "current_a", 0.0))
    power_kw = abs(get_float_value(data, "power_kw", 0.0))
    return current_a >= 5.0 or power_kw >= 0.20


'''
        s = replace_one(
            s,
            r"^def is_standby_consumption\(.*?(?=^def fmt_status\()",
            electrical,
            "electrical state functions",
        )

        # Hide SOH in every watcher-generated message.
        lines = []
        for line in s.splitlines():
            if "SOH:" in line:
                continue
            if re.match(r"\s*pandora_soh\s*=", line):
                continue
            lines.append(line)
        s = "\n".join(lines) + "\n"

        # Pandora SOC is primary; energy is prediction + safety cap.
        target_progress = r'''def refresh_pandora_for_active_percent_target(target, ts):
    if not target.get("enabled") or target.get("mode") != "percent":
        return False

    env = load_env(ENV_FILE)
    refresh_seconds = env_int(env, "LEAF_TARGET_PANDORA_REFRESH_SECONDS", 60)
    last_request = int(target.get("last_pandora_refresh_request_at") or 0)

    if refresh_seconds > 0 and ts - last_request < refresh_seconds:
        return False

    target["last_pandora_refresh_request_at"] = ts
    save_json(LEAF_TARGET_FILE, target)

    try:
        p = subprocess.run(
            [
                "/usr/bin/flock",
                "-n",
                PANDORA_LOCK,
                "/opt/trc-tuya/venv/bin/python3",
                PANDORA_SCRIPT,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=100,
        )

        target["last_pandora_refresh_at"] = now()
        target["last_pandora_refresh_ok"] = p.returncode == 0
        if p.returncode != 0:
            target["last_pandora_refresh_error"] = (p.stdout + "\n" + p.stderr)[-500:]
        save_json(LEAF_TARGET_FILE, target)
        return p.returncode == 0

    except Exception as e:
        target["last_pandora_refresh_at"] = now()
        target["last_pandora_refresh_ok"] = False
        target["last_pandora_refresh_error"] = repr(e)
        save_json(LEAF_TARGET_FILE, target)
        return False


def update_target_progress(data):
    target = load_json(LEAF_TARGET_FILE, {"enabled": False})

    if not target.get("enabled"):
        return target, False, None

    # SOC needs frequent data during an active percentage target.
    if target.get("mode") == "percent":
        if refresh_pandora_for_active_percent_target(target, now()):
            refreshed = read_leaf_status()
            if refreshed.get("ok"):
                data = refreshed

    try:
        start_energy = float(target.get("start_energy_kwh"))
        current_energy = float(data.get("energy_kwh") or 0)
        target_add_kwh = float(target.get("target_add_kwh") or 0)

        added_kwh = max(0.0, current_energy - start_energy)
        remaining_kwh = max(0.0, target_add_kwh - added_kwh)

        target["added_kwh"] = round(added_kwh, 2)
        target["remaining_kwh"] = round(remaining_kwh, 2)
        target["last_energy_kwh"] = current_energy
        target["last_checked_at"] = now()

        if target.get("mode") == "percent":
            start_percent = float(target.get("current_percent"))
            target_percent = float(target.get("target_percent"))
            wall_kwh_per_percent = float(target.get("wall_kwh_per_percent") or 0.5465)

            if wall_kwh_per_percent > 0:
                estimated_percent = start_percent + added_kwh / wall_kwh_per_percent
            elif target_add_kwh > 0:
                estimated_percent = start_percent + (added_kwh / target_add_kwh) * (target_percent - start_percent)
            else:
                estimated_percent = start_percent

            target["estimated_percent"] = round(min(100.0, estimated_percent), 1)
            data["target_added_kwh"] = target["added_kwh"]
            data["target_remaining_kwh"] = target["remaining_kwh"]
            data["target_estimated_percent"] = target["estimated_percent"]

            pandora_soc = data.get("pandora_soc_percent")
            pandora_status = data.get("pandora_status")
            pandora_age = data.get("pandora_age_minutes")
            max_age = env_float(load_env(ENV_FILE), "LEAF_TARGET_PANDORA_MAX_AGE_MINUTES", 3)

            fresh_soc = False
            try:
                fresh_soc = (
                    pandora_soc is not None
                    and pandora_status == "fresh"
                    and pandora_age is not None
                    and float(pandora_age) <= max_age
                )
            except Exception:
                pass

            if fresh_soc:
                target["pandora_soc_last"] = float(pandora_soc)
                target["pandora_soc_last_at"] = now()

                if float(pandora_soc) >= target_percent:
                    target["reached_by"] = "pandora_soc"
                    target["pandora_soc_at_reached"] = float(pandora_soc)
                    save_json(LEAF_TARGET_FILE, target)
                    return target, True, {
                        "reason": "pandora_soc",
                        "pandora_soc": float(pandora_soc),
                        "target_percent": target_percent,
                        "added_kwh": added_kwh,
                        "target_add_kwh": target_add_kwh,
                    }

            # Only a fail-safe if Pandora cannot be refreshed.
            safety_max = float(
                target.get("safety_max_add_kwh")
                or target.get("raw_wall_needed_kwh")
                or (target_add_kwh + 1.0)
            )

            if added_kwh >= safety_max:
                target["reached_by"] = "energy_safety_cap"
                target["safety_max_add_kwh"] = round(safety_max, 2)
                save_json(LEAF_TARGET_FILE, target)
                return target, True, {
                    "reason": "energy_safety_cap",
                    "added_kwh": added_kwh,
                    "target_add_kwh": target_add_kwh,
                    "safety_max_add_kwh": safety_max,
                }

        elif added_kwh >= target_add_kwh:
            target["reached_by"] = "energy_kwh"
            save_json(LEAF_TARGET_FILE, target)
            return target, True, {
                "reason": "energy_kwh",
                "added_kwh": added_kwh,
                "target_add_kwh": target_add_kwh,
            }

        save_json(LEAF_TARGET_FILE, target)

    except Exception as e:
        target["last_error"] = repr(e)
        target["last_checked_at"] = now()
        save_json(LEAF_TARGET_FILE, target)

    return target, False, None


'''
        s = replace_one(
            s,
            r"^def update_target_progress\(.*?(?=^def main\()",
            target_progress,
            "target progress",
        )

        # Delete the extra experimental Pandora OFF path: the function above is
        # now the only location that can decide the target is reached.
        s = re.sub(
            r'^    # Percent target safety:.*?(?=^    switch_value = data\.get\("switch"\))',
            "",
            s,
            count=1,
            flags=re.S | re.M,
        )

        milestones = r'''    # Notify each crossed boundary: 39 -> 56 produces 40% and 50%.
    soc, soc_source = get_soc_for_milestone(data)
    if soc is not None and (actual_charging or target.get("enabled")):
        bucket = int(float(soc) // 10) * 10
        last_bucket = state.get("last_soc_bucket")

        if last_bucket is None:
            state["last_soc_bucket"] = bucket
        else:
            last_bucket = int(last_bucket)

            if bucket < last_bucket - 10:
                state["last_soc_bucket"] = bucket
            elif bucket > last_bucket:
                for crossed_bucket in range(last_bucket + 10, bucket + 1, 10):
                    ok, msg = send_telegram(
                        f"🔋 <b>Заряд Leaf достиг {h(crossed_bucket)}%</b>\n\n"
                        f"Источник: <b>{h(soc_source)}</b>\n\n"
                        + fmt_status(data)
                    )
                    sent.append({
                        "type": "soc_10_percent",
                        "bucket": crossed_bucket,
                        "ok": ok,
                        "msg": msg,
                    })

                state["last_soc_bucket"] = bucket

'''
        s = replace_one(
            s,
            r"^    # SOC each 10% notification\n.*?(?=^    if reached:)",
            milestones,
            "milestone notifications",
        )

        # Attribute the OFF action to its actual reason.
        s = s.replace(
            '        ok, output = leaf_off("target_kwh")\n',
            '        stop_reason = str(reached_info.get("reason") or "energy_kwh")\n'
            '        ok, output = leaf_off("target_" + stop_reason)\n',
            1,
        )
        s = s.replace(
            '        target["completed_at"] = ts\n',
            '        target["completed_at"] = ts\n'
            '        target["completed_by"] = reached_info.get("reason")\n',
            1,
        )

        WATCHER.write_text(s, encoding="utf-8")
        subprocess.run([PYTHON, "-m", "py_compile", str(WATCHER)], check=True)

    except Exception:
        shutil.copy2(backup, WATCHER)
        raise

    print("PATCH_OK")
    print("Backup:", backup)
    print("Pandora SOC is now the primary percent-target stop condition.")
    print("Meter energy is only a prediction and a safety cap.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("PATCH_FAILED:", e, file=sys.stderr)
        sys.exit(1)
