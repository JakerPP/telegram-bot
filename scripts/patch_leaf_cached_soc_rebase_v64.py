#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import patch_leaf_use_cached_then_rebase_v62 as v62

BASE = Path('/opt/trc-tuya')
BOT = BASE / 'telegram_gate_bot.py'
AUTO = BASE / 'pandora_soc_auto_wakeup.py'
REBASE = BASE / 'leaf_percent_target_rebase.py'
ENV_FILE = BASE / 'telegram_gate_bot.env'
PYTHON = '/opt/trc-tuya/venv/bin/python3'
MARKER = 'LEAF_CACHED_SOC_REBASE_V64'


def replace_function(source: str, name: str, replacement: str) -> str:
    start = source.find(f'def {name}(')
    if start < 0:
        raise RuntimeError(f'Function {name} not found')
    end = source.find('\ndef ', start + 1)
    if end < 0:
        raise RuntimeError(f'End of function {name} not found')
    return source[:start] + replacement.rstrip() + '\n\n' + source[end + 1:]


def patch_auto_robust(source: str) -> str:
    if 'LEAF_PANDORA_FORCE_WAKE' in source:
        return source

    now_anchor = '    now = int(time.time())\n'
    if now_anchor not in source:
        raise RuntimeError('Auto-wakeup timestamp anchor not found')
    source = source.replace(
        now_anchor,
        now_anchor + "    force_wake = os.environ.get('LEAF_PANDORA_FORCE_WAKE', '').strip() == '1'\n",
        1,
    )

    target_guard = "    if not target.get('enabled') or target.get('mode') != 'percent':"
    if target_guard not in source:
        raise RuntimeError('Active percent-target guard not found')
    source = source.replace(
        target_guard,
        "    if not force_wake and (not target.get('enabled') or target.get('mode') != 'percent'):",
        1,
    )

    # Current production script has the v52 breaker-ON / cable-connected block.
    v52_block = """    if leaf.get('switch') is not True:
        print(json.dumps({'ok': True, 'action': 'skip', 'reason': 'breaker_off'}))
        return

    charging_state = str(leaf.get('charging_state', '')).upper()
    cable_connected = leaf.get('pandora_charging_connected') is True

    if charging_state not in {'CHARGING', 'ON'} and not cable_connected:
        print(json.dumps({
            'ok': True,
            'action': 'skip',
            'reason': 'cable_not_connected',
            'charging_state': charging_state,
        }))
        return
"""
    simple_block = """    if leaf.get('switch') is not True or str(leaf.get('charging_state', '')).upper() != 'CHARGING':
        print(json.dumps({'ok': True, 'action': 'skip', 'reason': 'not_charging'}))
        return
"""

    if v52_block in source:
        guarded = "    if not force_wake:\n" + ''.join(
            ('    ' + line if line.strip() else line)
            for line in v52_block.splitlines(keepends=True)
        )
        source = source.replace(v52_block, guarded, 1)
    elif simple_block in source:
        guarded = "    if not force_wake:\n" + ''.join(
            ('    ' + line if line.strip() else line)
            for line in simple_block.splitlines(keepends=True)
        )
        source = source.replace(simple_block, guarded, 1)
    else:
        raise RuntimeError('Neither v52 nor legacy charging guard was found')

    due_guard = '    if last and now - last < interval:'
    if due_guard not in source:
        raise RuntimeError('Wake interval guard not found')
    source = source.replace(
        due_guard,
        '    if not force_wake and last and now - last < interval:',
        1,
    )

    return source


def patch_bot_corrected(source: str) -> str:
    source = v62.patch_bot(source)

    create_target = r'''def create_charge_target_by_percent(current_percent, target_percent, chat_id):
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

    # Use the previously calibrated wall-energy factor. This is more useful for
    # EVSE cut-off than trying to infer total pack capacity from one LeafSpy page.
    wall_kwh_per_percent = env_float("LEAF_WALL_KWH_PER_PERCENT", 0.5455)
    if wall_kwh_per_percent <= 0:
        wall_kwh_per_percent = 0.5455

    wall_needed_kwh = (target_percent - current_percent) * wall_kwh_per_percent
    cached = load_last_known_pandora_soc()

    data = {
        "enabled": True,
        "mode": "percent",
        "current_percent": current_percent,
        "target_percent": target_percent,
        "wall_kwh_per_percent": round(wall_kwh_per_percent, 6),
        "target_add_kwh": round(wall_needed_kwh, 2),
        "raw_wall_needed_kwh": round(wall_needed_kwh, 2),
        "safety_max_add_kwh": round(wall_needed_kwh + max(0.5, wall_kwh_per_percent), 2),
        "start_energy_kwh": status.get("energy_kwh"),
        "created_at": now(),
        "created_by": str(chat_id),
        "added_kwh": 0,
        "remaining_kwh": round(wall_needed_kwh, 2),
        "estimated_percent": current_percent,
        "needs_pandora_rebase": True,
        "rebase_requested_at": now(),
        "initial_soc_source": cached.get("source") if cached else "manual",
        "initial_soc_status": cached.get("status") if cached else "manual",
        "leafspy_reference_soc_percent": 76.5,
        "leafspy_reference_ahr": 124.2,
        "leafspy_reference_soh_percent": 93.75,
    }

    save_json(LEAF_TARGET_FILE, data)
    return True, data'''
    source = replace_function(source, 'create_charge_target_by_percent', create_target)

    # The bot itself does not define the installer's PYTHON constant.
    source = source.replace(
        '[PYTHON, LEAF_PERCENT_REBASE_SCRIPT]',
        '["/opt/trc-tuya/venv/bin/python3", LEAF_PERCENT_REBASE_SCRIPT]',
        1,
    )

    if MARKER not in source:
        source = source.replace(
            '# LEAF_CACHED_SOC_REBASE_V62',
            '# LEAF_CACHED_SOC_REBASE_V62\n# LEAF_CACHED_SOC_REBASE_V64',
            1,
        )

    return source


def set_env(path: Path, values: dict):
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    lines = text.splitlines()
    out = []
    seen = set()
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            key = line.split('=', 1)[0].strip()
            if key in values:
                out.append(f'{key}={values[key]}')
                seen.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f'{key}={value}')
    path.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')


def main():
    for path in (BOT, AUTO):
        if not path.exists():
            raise RuntimeError(f'Missing {path}')

    bot_old = BOT.read_text(encoding='utf-8')
    auto_old = AUTO.read_text(encoding='utf-8')

    if MARKER in bot_old:
        print('PATCH_ALREADY_APPLIED')
        return

    bot_new = patch_bot_corrected(bot_old)
    auto_new = patch_auto_robust(auto_old)
    rebase_new = v62.rebase_script()

    v62.compile_candidate(BOT, bot_new, '.candidate-v64')
    v62.compile_candidate(AUTO, auto_new, '.candidate-v64')
    v62.compile_candidate(REBASE, rebase_new, '.candidate-v64')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backups = []
    for path in (BOT, AUTO, REBASE, ENV_FILE):
        if path.exists():
            backup = path.with_name(path.name + f'.before-v64.{stamp}')
            shutil.copy2(path, backup)
            backups.append((path, backup))

    try:
        BOT.write_text(bot_new, encoding='utf-8')
        AUTO.write_text(auto_new, encoding='utf-8')
        REBASE.write_text(rebase_new, encoding='utf-8')
        for path in (BOT, AUTO, REBASE):
            os.chmod(path, 0o750)

        set_env(ENV_FILE, {
            'LEAF_WALL_KWH_PER_PERCENT': '0.5455',
            'LEAF_LEAFSPY_SOC_PERCENT': '76.5',
            'LEAF_LEAFSPY_AHR': '124.2',
            'LEAF_LEAFSPY_SOH_PERCENT': '93.75',
        })

        subprocess.run([PYTHON, '-m', 'py_compile', str(BOT)], check=True)
        subprocess.run([PYTHON, '-m', 'py_compile', str(AUTO)], check=True)
        subprocess.run([PYTHON, '-m', 'py_compile', str(REBASE)], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=True)
    except Exception:
        for original, backup in backups:
            shutil.copy2(backup, original)
        subprocess.run(['systemctl', 'restart', 'trc-telegram-gate-bot.service'], check=False)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=False)
        raise

    print('PATCH_OK')
    print('Cached Pandora SOC is used immediately')
    print('Fresh SOC after charging starts rebases target kWh')
    print('Energy calibration preserved: 0.5455 kWh per percentage point')
    print('LeafSpy reference stored: SOC 76.5%, 124.2 Ah, SOH 93.75%')
    for _, backup in backups:
        print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
