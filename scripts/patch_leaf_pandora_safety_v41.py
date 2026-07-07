#!/usr/bin/env python3
"""Small follow-up patch for patch_leaf_pandora_primary_v4.py.

It keeps Pandora SOC as the actual stop condition and uses the wall-energy
limit only when Pandora has not been fresh for a grace period. It also makes
10% notifications use the meter estimate while a percentage target is active,
so they are not delayed by Pandora polling.
"""

import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path("/opt/trc-tuya")
WATCHER = BASE / "leaf_charger_watcher.py"
PYTHON = "/opt/trc-tuya/venv/bin/python3"


def replace_one(text, pattern, replacement, label):
    out, count = re.subn(pattern, lambda _m: replacement, text, count=1, flags=re.S | re.M)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, got {count}")
    return out


def main():
    if not WATCHER.exists():
        raise RuntimeError(f"Missing {WATCHER}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = WATCHER.with_name(WATCHER.name + f".before-pandora-safety-v41.{stamp}")
    shutil.copy2(WATCHER, backup)

    try:
        s = WATCHER.read_text(encoding="utf-8")

        new_milestone_function = r'''def get_soc_for_milestone(data):
    # During an active percentage target, use the meter estimate. It updates
    # every watcher run, so 40/50/60/etc. notifications are timely.
    target = load_json(LEAF_TARGET_FILE, {"enabled": False})

    if target.get("enabled") and target.get("mode") == "percent":
        estimated = data.get("target_estimated_percent")
        if estimated is None:
            estimated = target.get("estimated_percent")

        try:
            if estimated is not None:
                return float(estimated), "расчёт по энергии"
        except Exception:
            pass

    soc = data.get("pandora_soc_percent")
    status = data.get("pandora_status")

    if soc is not None and status == "fresh":
        try:
            return float(soc), "Pandora"
        except Exception:
            pass

    return None, None


'''
        s = replace_one(
            s,
            r"^def get_soc_for_milestone\(.*?(?=^def refresh_pandora_for_active_percent_target\()",
            new_milestone_function,
            "get_soc_for_milestone",
        )

        old_block = r'''            if fresh_soc:
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
'''

        new_block = r'''            if fresh_soc:
                target["pandora_soc_last"] = float(pandora_soc)
                target["pandora_soc_last_at"] = now()
                target["last_pandora_fresh_at"] = now()

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

            # Energy is a safety cap only when Pandora has been unavailable for
            # the grace window. A fresh Pandora SOC below target must continue
            # charging even when the estimate is already high.
            env = load_env(ENV_FILE)
            no_fresh_grace = env_int(env, "LEAF_TARGET_PANDORA_NO_FRESH_GRACE_SECONDS", 300)
            safety_extra_kwh = env_float(env, "LEAF_TARGET_ENERGY_SAFETY_EXTRA_KWH", 1.0)
            last_fresh_at = int(target.get("last_pandora_fresh_at") or target.get("created_at") or now())
            no_fresh_for = max(0, now() - last_fresh_at)

            safety_base = float(
                target.get("raw_wall_needed_kwh")
                or target_add_kwh
                or 0.0
            )
            safety_max = safety_base + max(0.0, safety_extra_kwh)

            if (not fresh_soc and no_fresh_for >= no_fresh_grace and added_kwh >= safety_max):
                target["reached_by"] = "energy_safety_cap"
                target["safety_max_add_kwh"] = round(safety_max, 2)
                target["pandora_unavailable_seconds"] = no_fresh_for
                save_json(LEAF_TARGET_FILE, target)
                return target, True, {
                    "reason": "energy_safety_cap",
                    "added_kwh": added_kwh,
                    "target_add_kwh": target_add_kwh,
                    "safety_max_add_kwh": safety_max,
                    "pandora_unavailable_seconds": no_fresh_for,
                }
'''

        if old_block not in s:
            raise RuntimeError("expected Pandora safety block not found")
        s = s.replace(old_block, new_block, 1)

        WATCHER.write_text(s, encoding="utf-8")
        subprocess.run([PYTHON, "-m", "py_compile", str(WATCHER)], check=True)

    except Exception:
        shutil.copy2(backup, WATCHER)
        raise

    print("PATCH_OK")
    print("Backup:", backup)
    print("Pandora SOC stays primary; energy cap works only after 5 minutes without fresh Pandora.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PATCH_FAILED:", exc, file=sys.stderr)
        sys.exit(1)
