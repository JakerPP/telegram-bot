#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path('/opt/trc-tuya')
PANDORA = BASE / 'pandora_leaf_soc.py'
PANDORA_ENV = BASE / 'pandora.env'
BOT_ENV = BASE / 'telegram_gate_bot.env'
AUTO = BASE / 'pandora_soc_auto_wakeup.py'
SERVICE = Path('/etc/systemd/system/trc-pandora-soc-auto-wakeup.service')
TIMER = Path('/etc/systemd/system/trc-pandora-soc-auto-wakeup.timer')
PYTHON = '/opt/trc-tuya/venv/bin/python3'


def backup(path: Path, stamp: str):
    if path.exists():
        dst = path.with_name(path.name + f'.before-auto-wakeup-v5.{stamp}')
        shutil.copy2(path, dst)
        return dst
    return None


def set_env(path: Path, values: dict):
    text = path.read_text(encoding='utf-8') if path.exists() else ''
    lines = text.splitlines()
    seen = set()
    out = []
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


def patch_pandora_reader():
    s = PANDORA.read_text(encoding='utf-8')

    # SOC freshness must use the CAN/state timestamp only. Online timestamps
    # can move while the SOC value itself remains stale for hours.
    pattern = re.compile(
        r'timestamp_raw\s*=\s*\(\s*'
        r'info\.get\("state_timestamp_utc"\)\s*'
        r'or\s+info\.get\("state_timestamp"\)\s*'
        r'or\s+info\.get\("online_timestamp_utc"\)\s*'
        r'or\s+info\.get\("online_timestamp"\)\s*'
        r'\)',
        re.S,
    )
    replacement = '''timestamp_raw = (\n            info.get("state_timestamp_utc")\n            or info.get("state_timestamp")\n        )'''
    s, n = pattern.subn(replacement, s, count=1)
    if n == 0 and 'or info.get("online_timestamp")' in s:
        raise RuntimeError('Could not patch Pandora timestamp source')

    if 'PANDORA_SOC_FRESH_SECONDS' not in s:
        old = '        result = {\n'
        new = (
            '        try:\n'
            '            fresh_seconds = int(env.get("PANDORA_SOC_FRESH_SECONDS", "300"))\n'
            '        except Exception:\n'
            '            fresh_seconds = 300\n\n'
            + old
        )
        if old not in s:
            raise RuntimeError('Could not insert fresh_seconds')
        s = s.replace(old, new, 1)

    s = re.sub(
        r'"pandora_status":\s*"fresh"\s*if\s*age_seconds\s*is\s*not\s*None\s*and\s*age_seconds\s*<=\s*1800\s*else\s*"stale"',
        '"pandora_status": "fresh" if age_seconds is not None and age_seconds <= fresh_seconds else "stale"',
        s,
        count=1,
    )

    # Remove Pandora SOH collection/publication; it is not a trusted Leaf BMS SOH.
    filtered = []
    for line in s.splitlines():
        if '"ev_state_of_health",' in line:
            continue
        if re.match(r'\s*soh\s*=\s*info\.get\("ev_state_of_health"\)', line):
            continue
        if '"pandora_soh_percent": soh,' in line:
            continue
        if '"pandora_soh_percent": result.get("pandora_soh_percent"),' in line:
            continue
        filtered.append(line)
    PANDORA.write_text('\n'.join(filtered).rstrip() + '\n', encoding='utf-8')


def auto_script():
    return r'''#!/opt/trc-tuya/venv/bin/python3
import asyncio
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
from pandora_cas.account import PandoraOnlineAccount

BASE = Path('/opt/trc-tuya')
PANDORA_ENV = BASE / 'pandora.env'
BOT_ENV = BASE / 'telegram_gate_bot.env'
TARGET = BASE / 'leaf_charge_target.json'
LEAF_STATUS = Path('/var/www/html/trc/leaf_status.json')
STATE = BASE / 'pandora_soc_auto_wakeup_state.json'
PANDORA_LOCK = BASE / 'pandora_leaf_soc.lock'
PANDORA_READER = BASE / 'pandora_leaf_soc.py'
WATCHER = BASE / 'leaf_charger_watcher.py'
PYTHON = '/opt/trc-tuya/venv/bin/python3'


def load_env(path):
    out = {}
    try:
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path, data):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def fnum(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def inum(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def interval_for(target, env):
    target_percent = fnum(target.get('target_percent'))
    estimated = target.get('estimated_percent')
    if estimated is None:
        start = fnum(target.get('current_percent'))
        added = fnum(target.get('added_kwh'))
        factor = fnum(target.get('wall_kwh_per_percent'), 0.5455)
        estimated = start + (added / factor if factor > 0 else 0)
    remaining = max(0.0, target_percent - fnum(estimated))

    far = inum(env.get('LEAF_PANDORA_WAKE_FAR_SECONDS'), 3600)
    near = inum(env.get('LEAF_PANDORA_WAKE_NEAR_SECONDS'), 900)
    final = inum(env.get('LEAF_PANDORA_WAKE_FINAL_SECONDS'), 300)

    if remaining <= 3:
        return final, remaining
    if remaining <= 10:
        return near, remaining
    return far, remaining


async def trigger_charge_flap():
    env = load_env(PANDORA_ENV)
    username = env.get('PANDORA_USERNAME', '').strip()
    password = env.get('PANDORA_PASSWORD', '').strip()
    raw_id = env.get('PANDORA_DEVICE_ID', '').strip()
    if not username or not password or not raw_id:
        raise RuntimeError('Pandora credentials/device id are missing')

    try:
        device_id = int(raw_id)
    except Exception:
        device_id = raw_id

    async with aiohttp.ClientSession() as session:
        account = PandoraOnlineAccount(
            session,
            username=username,
            password=password,
            language='ru',
        )
        await account.async_authenticate()
        await account.async_refresh_devices()
        device = account.devices.get(device_id)
        if device is None:
            raise RuntimeError(f'Pandora device {raw_id} not found')
        fn = getattr(device, 'async_remote_trigger_trunk', None)
        if not callable(fn):
            raise RuntimeError('async_remote_trigger_trunk is unavailable')
        result = await fn()
        return bool(result) if result is not None else True


def main():
    target = load_json(TARGET, {'enabled': False})
    leaf = load_json(LEAF_STATUS, {})
    env = load_env(BOT_ENV)
    now = int(time.time())

    if not target.get('enabled') or target.get('mode') != 'percent':
        print(json.dumps({'ok': True, 'action': 'skip', 'reason': 'no_active_percent_target'}))
        return

    if leaf.get('switch') is not True or str(leaf.get('charging_state', '')).upper() != 'CHARGING':
        print(json.dumps({'ok': True, 'action': 'skip', 'reason': 'not_charging'}))
        return

    state = load_json(STATE, {})
    interval, remaining = interval_for(target, env)
    last = inum(state.get('last_trigger_at'), 0)
    if last and now - last < interval:
        print(json.dumps({
            'ok': True,
            'action': 'skip',
            'reason': 'not_due',
            'remaining_percent': round(remaining, 1),
            'next_in_seconds': interval - (now - last),
        }))
        return

    with open(PANDORA_LOCK, 'a+', encoding='utf-8') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'ok': True, 'action': 'skip', 'reason': 'pandora_busy'}))
            return

        flap_ok = asyncio.run(trigger_charge_flap())
        state.update({
            'last_trigger_at': now,
            'last_trigger_ok': flap_ok,
            'remaining_percent_at_trigger': round(remaining, 1),
            'interval_seconds': interval,
        })
        save_json(STATE, state)

        wait_seconds = inum(env.get('LEAF_PANDORA_WAKE_WAIT_SECONDS'), 50)
        time.sleep(max(20, wait_seconds))

        reader = subprocess.run(
            [PYTHON, str(PANDORA_READER)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )

        state['last_reader_at'] = int(time.time())
        state['last_reader_ok'] = reader.returncode == 0
        if reader.returncode != 0:
            state['last_error'] = (reader.stdout + '\n' + reader.stderr)[-1000:]
        save_json(STATE, state)

    # Let the normal watcher process the newly awakened SOC and stop the charger
    # when Pandora reaches the requested percentage.
    watcher = subprocess.run(
        [PYTHON, str(WATCHER)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=150,
        env={**os.environ, 'LEAF_WATCHER_TEST': '0'},
    )

    print(json.dumps({
        'ok': reader.returncode == 0 and watcher.returncode == 0,
        'action': 'wake_refresh',
        'charge_flap_command': flap_ok,
        'remaining_percent_before': round(remaining, 1),
        'reader_returncode': reader.returncode,
        'watcher_returncode': watcher.returncode,
        'watcher_output': watcher.stdout[-1000:],
        'error': (reader.stderr + '\n' + watcher.stderr)[-1000:],
    }, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': repr(exc)}, ensure_ascii=False))
        sys.exit(1)
'''


def main():
    if not PANDORA.exists():
        raise RuntimeError(f'Missing {PANDORA}')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backups = [backup(p, stamp) for p in [PANDORA, PANDORA_ENV, BOT_ENV, AUTO, SERVICE, TIMER]]

    try:
        subprocess.run(['systemctl', 'stop', 'trc-pandora-soc-auto-wakeup.timer'], check=False)
        subprocess.run(['systemctl', 'stop', 'trc-pandora-soc-auto-wakeup.service'], check=False)

        patch_pandora_reader()
        set_env(PANDORA_ENV, {
            'PANDORA_SOC_FRESH_SECONDS': '300',
        })
        set_env(BOT_ENV, {
            'LEAF_WALL_KWH_PER_PERCENT': '0.5455',
            'LEAF_PANDORA_WAKE_FAR_SECONDS': '3600',
            'LEAF_PANDORA_WAKE_NEAR_SECONDS': '900',
            'LEAF_PANDORA_WAKE_FINAL_SECONDS': '300',
            'LEAF_PANDORA_WAKE_WAIT_SECONDS': '50',
            'LEAF_TARGET_PANDORA_REFRESH_SECONDS': '600',
        })

        AUTO.write_text(auto_script(), encoding='utf-8')
        os.chmod(AUTO, 0o750)

        SERVICE.write_text('''[Unit]\nDescription=Wake Leaf via Pandora charge flap and refresh SOC\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=oneshot\nUser=root\nWorkingDirectory=/opt/trc-tuya\nExecStart=/opt/trc-tuya/venv/bin/python3 /opt/trc-tuya/pandora_soc_auto_wakeup.py\nTimeoutStartSec=240\n''', encoding='utf-8')

        TIMER.write_text('''[Unit]\nDescription=Adaptive Pandora SOC wake-up check every 5 minutes\n\n[Timer]\nOnBootSec=2min\nOnUnitActiveSec=5min\nAccuracySec=20s\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n''', encoding='utf-8')

        subprocess.run([PYTHON, '-m', 'py_compile', str(PANDORA)], check=True)
        subprocess.run([PYTHON, '-m', 'py_compile', str(AUTO)], check=True)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', '--now', 'trc-pandora-soc-auto-wakeup.timer'], check=True)

    except Exception:
        # Restore only files that had backups. New files are intentionally left
        # visible for diagnosis rather than silently deleting evidence.
        for original, saved in zip([PANDORA, PANDORA_ENV, BOT_ENV, AUTO, SERVICE, TIMER], backups):
            if saved and saved.exists():
                shutil.copy2(saved, original)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        raise

    print('INSTALL_OK')
    print('Calibration: 0.5455 kWh wall energy per 1% SOC')
    print('Wake schedule: 60 min far, 15 min within 10%, 5 min within 3%')
    print('SOC freshness now uses Pandora state/CAN timestamp only')
    print('Backups:')
    for saved in backups:
        if saved:
            print(' -', saved)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('INSTALL_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
