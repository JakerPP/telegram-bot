#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path('/opt/trc-tuya')
AUTO = BASE / 'pandora_soc_auto_wakeup.py'
WATCHER = BASE / 'leaf_charger_watcher.py'
PYTHON = '/opt/trc-tuya/venv/bin/python3'

AUTO_OLD = """        state['last_reader_at'] = int(time.time())
        state['last_reader_ok'] = reader.returncode == 0
        if reader.returncode != 0:
            state['last_error'] = (reader.stdout + '\\n' + reader.stderr)[-1000:]
        save_json(STATE, state)
"""

AUTO_NEW = """        verified_at = int(time.time())
        state['last_reader_at'] = verified_at
        state['last_reader_ok'] = reader.returncode == 0
        if reader.returncode == 0:
            # Pandora does not always return a state timestamp for this Leaf.
            # A successful read immediately after the charge-flap wake command
            # is nevertheless a verified live SOC sample.
            public_status = load_json(LEAF_STATUS, {})
            public_status['pandora_verified_at'] = verified_at
            public_status['pandora_verified_source'] = 'charge_flap_wakeup'
            public_status['pandora_status'] = 'fresh'
            public_status['pandora_age_minutes'] = 0.0
            public_status['pandora_checked_at'] = verified_at
            public_tmp = Path(str(LEAF_STATUS) + '.verified.tmp')
            public_tmp.write_text(
                json.dumps(public_status, indent=2, ensure_ascii=False),
                encoding='utf-8',
            )
            os.replace(public_tmp, LEAF_STATUS)
            os.chmod(LEAF_STATUS, 0o644)
            state['last_verified_at'] = verified_at
        else:
            state['last_error'] = (reader.stdout + '\\n' + reader.stderr)[-1000:]
        save_json(STATE, state)
"""


def patch_auto(text: str) -> str:
    if "pandora_verified_source'] = 'charge_flap_wakeup'" in text:
        return text
    if AUTO_OLD not in text:
        raise RuntimeError('Expected reader result block not found in auto-wakeup script')
    return text.replace(AUTO_OLD, AUTO_NEW, 1)


def patch_watcher(text: str) -> str:
    if 'verified_fresh_soc' in text:
        return text

    pattern = re.compile(
        r"(?P<indent>\s*)fresh_soc\s*=\s*False\n"
        r"(?P=indent)try:\n"
        r"(?P=indent)    fresh_soc\s*=\s*\(\n"
        r"(?P=indent)        pandora_soc\s+is\s+not\s+None\n"
        r"(?P=indent)        and\s+pandora_status\s*==\s*[\"']fresh[\"']\n"
        r"(?P=indent)        and\s+pandora_age\s+is\s+not\s+None\n"
        r"(?P=indent)        and\s+float\(pandora_age\)\s*<=\s*max_age\n"
        r"(?P=indent)    \)\n"
        r"(?P=indent)except\s+Exception:\n"
        r"(?P=indent)    fresh_soc\s*=\s*False",
        re.M,
    )

    match = pattern.search(text)
    if not match:
        raise RuntimeError('Expected fresh_soc block not found in watcher')

    i = match.group('indent')
    replacement = (
        f"{i}fresh_soc = False\n"
        f"{i}verified_fresh_soc = False\n"
        f"{i}try:\n"
        f"{i}    fresh_soc = (\n"
        f"{i}        pandora_soc is not None\n"
        f"{i}        and pandora_status == \"fresh\"\n"
        f"{i}        and pandora_age is not None\n"
        f"{i}        and float(pandora_age) <= max_age\n"
        f"{i}    )\n"
        f"{i}except Exception:\n"
        f"{i}    fresh_soc = False\n\n"
        f"{i}# Successful SOC read immediately after waking the Leaf by opening\n"
        f"{i}# the charge flap is trusted even when Pandora omits state_timestamp.\n"
        f"{i}try:\n"
        f"{i}    verified_at = int(data.get(\"pandora_verified_at\") or 0)\n"
        f"{i}    verified_age_seconds = max(0, now() - verified_at) if verified_at else None\n"
        f"{i}    verified_fresh_soc = (\n"
        f"{i}        pandora_soc is not None\n"
        f"{i}        and verified_age_seconds is not None\n"
        f"{i}        and verified_age_seconds <= float(max_age) * 60.0\n"
        f"{i}    )\n"
        f"{i}except Exception:\n"
        f"{i}    verified_fresh_soc = False\n\n"
        f"{i}fresh_soc = fresh_soc or verified_fresh_soc"
    )
    return text[:match.start()] + replacement + text[match.end():]


def compile_candidate(path: Path, content: str, suffix: str):
    tmp = path.with_name(path.name + suffix)
    tmp.write_text(content, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(tmp)], check=True)
    finally:
        tmp.unlink(missing_ok=True)


def main():
    for path in (AUTO, WATCHER):
        if not path.exists():
            raise RuntimeError(f'Missing {path}')

    auto_old = AUTO.read_text(encoding='utf-8')
    watcher_old = WATCHER.read_text(encoding='utf-8')
    auto_new = patch_auto(auto_old)
    watcher_new = patch_watcher(watcher_old)

    compile_candidate(AUTO, auto_new, '.candidate-v55')
    compile_candidate(WATCHER, watcher_new, '.candidate-v55')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    auto_backup = AUTO.with_name(AUTO.name + f'.before-v55.{stamp}')
    watcher_backup = WATCHER.with_name(WATCHER.name + f'.before-v55.{stamp}')
    shutil.copy2(AUTO, auto_backup)
    shutil.copy2(WATCHER, watcher_backup)

    try:
        subprocess.run(['systemctl', 'stop', 'trc-pandora-soc-auto-wakeup.timer'], check=False)
        subprocess.run(['systemctl', 'stop', 'trc-leaf-charger-watcher.timer'], check=False)

        AUTO.write_text(auto_new, encoding='utf-8')
        WATCHER.write_text(watcher_new, encoding='utf-8')
        os.chmod(AUTO, 0o750)
        os.chmod(WATCHER, 0o750)

        subprocess.run([PYTHON, '-m', 'py_compile', str(AUTO)], check=True)
        subprocess.run([PYTHON, '-m', 'py_compile', str(WATCHER)], check=True)
        subprocess.run(['systemctl', 'reset-failed', 'trc-pandora-soc-auto-wakeup.service'], check=False)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=True)
        subprocess.run(['systemctl', 'restart', 'trc-leaf-charger-watcher.timer'], check=True)
    except Exception:
        shutil.copy2(auto_backup, AUTO)
        shutil.copy2(watcher_backup, WATCHER)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=False)
        subprocess.run(['systemctl', 'restart', 'trc-leaf-charger-watcher.timer'], check=False)
        raise

    print('PATCH_OK')
    print('SOC read after charge-flap wake-up is now marked and trusted as fresh')
    print('Auto backup:', auto_backup)
    print('Watcher backup:', watcher_backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
