#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path('/opt/trc-tuya')
AUTO = BASE / 'pandora_soc_auto_wakeup.py'
WATCHER = BASE / 'leaf_charger_watcher.py'
PYTHON = '/opt/trc-tuya/venv/bin/python3'


def compile_candidate(path: Path, content: str, suffix: str):
    tmp = path.with_name(path.name + suffix)
    tmp.write_text(content, encoding='utf-8')
    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(tmp)], check=True)
    finally:
        tmp.unlink(missing_ok=True)


def patch_auto(text: str) -> str:
    if "state['last_verified_soc']" in text:
        return text

    lines = text.splitlines()
    start = None
    save_idx = None

    for i, line in enumerate(lines):
        if "state['last_reader_ok'] = reader.returncode == 0" in line:
            start = i
            break

    if start is None:
        raise RuntimeError('Could not find last_reader_ok block in auto-wakeup script')

    for i in range(start + 1, min(len(lines), start + 25)):
        if lines[i].strip() == 'save_json(STATE, state)':
            save_idx = i
            break

    if save_idx is None:
        raise RuntimeError('Could not find state save after Pandora reader')

    indent = lines[save_idx][:len(lines[save_idx]) - len(lines[save_idx].lstrip())]
    block = [
        f"{indent}if reader.returncode == 0:",
        f"{indent}    try:",
        f"{indent}        verified_payload = load_json(BASE / 'pandora_leaf_soc.json', {{}})",
        f"{indent}        verified_soc = verified_payload.get('pandora_soc_percent')",
        f"{indent}        if verified_soc is not None:",
        f"{indent}            state['last_verified_at'] = int(time.time())",
        f"{indent}            state['last_verified_soc'] = float(verified_soc)",
        f"{indent}            state['last_verified_source'] = 'charge_flap_wakeup'",
        f"{indent}    except Exception as exc:",
        f"{indent}        state['last_verified_error'] = repr(exc)",
    ]

    lines[save_idx:save_idx] = block
    return '\n'.join(lines).rstrip() + '\n'


def patch_watcher(text: str) -> str:
    if 'verified_fresh_soc = False' in text:
        return text

    lines = text.splitlines()
    func_idx = None
    soc_idx = None
    if_idx = None

    for i, line in enumerate(lines):
        if line.startswith('def update_target_progress'):
            func_idx = i
            break

    if func_idx is None:
        raise RuntimeError('Could not find update_target_progress in watcher')

    for i in range(func_idx, len(lines)):
        if 'pandora_soc = data.get("pandora_soc_percent")' in lines[i]:
            soc_idx = i
            break

    if soc_idx is None:
        raise RuntimeError('Could not find Pandora SOC target block in watcher')

    for i in range(soc_idx, min(len(lines), soc_idx + 80)):
        if lines[i].strip() == 'if fresh_soc:':
            if_idx = i
            break

    if if_idx is None:
        raise RuntimeError('Could not find if fresh_soc block in watcher')

    indent = lines[if_idx][:len(lines[if_idx]) - len(lines[if_idx].lstrip())]
    block = [
        f"{indent}verified_fresh_soc = False",
        f"{indent}verified_age_seconds = None",
        f"{indent}try:",
        f"{indent}    wake_state = load_json('/opt/trc-tuya/pandora_soc_auto_wakeup_state.json', {{}})",
        f"{indent}    verified_at = int(wake_state.get('last_verified_at') or 0)",
        f"{indent}    verified_soc = wake_state.get('last_verified_soc')",
        f"{indent}    verified_age_seconds = max(0, now() - verified_at) if verified_at else None",
        f"{indent}    verified_fresh_soc = (",
        f"{indent}        pandora_soc is not None",
        f"{indent}        and verified_soc is not None",
        f"{indent}        and abs(float(pandora_soc) - float(verified_soc)) < 0.01",
        f"{indent}        and verified_age_seconds is not None",
        f"{indent}        and verified_age_seconds <= float(max_age) * 60.0",
        f"{indent}    )",
        f"{indent}except Exception:",
        f"{indent}    verified_fresh_soc = False",
        '',
        f"{indent}if verified_fresh_soc:",
        f"{indent}    fresh_soc = True",
        f"{indent}    pandora_status = 'fresh'",
        f"{indent}    pandora_age = round(verified_age_seconds / 60.0, 1)",
        f"{indent}    data['pandora_status'] = pandora_status",
        f"{indent}    data['pandora_age_minutes'] = pandora_age",
        f"{indent}    data['pandora_verified_at'] = verified_at",
        f"{indent}    data['pandora_verified_source'] = 'charge_flap_wakeup'",
        f"{indent}    try:",
        f"{indent}        save_json('/var/www/html/trc/leaf_status.json', data, mode=0o644)",
        f"{indent}    except Exception:",
        f"{indent}        pass",
        '',
    ]

    lines[if_idx:if_idx] = block
    return '\n'.join(lines).rstrip() + '\n'


def main():
    for path in (AUTO, WATCHER):
        if not path.exists():
            raise RuntimeError(f'Missing {path}')

    auto_old = AUTO.read_text(encoding='utf-8')
    watcher_old = WATCHER.read_text(encoding='utf-8')
    auto_new = patch_auto(auto_old)
    watcher_new = patch_watcher(watcher_old)

    compile_candidate(AUTO, auto_new, '.candidate-v56')
    compile_candidate(WATCHER, watcher_new, '.candidate-v56')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    auto_backup = AUTO.with_name(AUTO.name + f'.before-v56.{stamp}')
    watcher_backup = WATCHER.with_name(WATCHER.name + f'.before-v56.{stamp}')
    shutil.copy2(AUTO, auto_backup)
    shutil.copy2(WATCHER, watcher_backup)

    try:
        subprocess.run(['systemctl', 'stop', 'trc-pandora-soc-auto-wakeup.timer'], check=False)
        subprocess.run(['systemctl', 'stop', 'trc-leaf-charger-watcher.timer'], check=False)
        subprocess.run(['systemctl', 'stop', 'trc-pandora-soc-auto-wakeup.service'], check=False)
        subprocess.run(['systemctl', 'stop', 'trc-leaf-charger-watcher.service'], check=False)

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
    print('Verified Pandora SOC is now stored in wake-up state and trusted by watcher')
    print('Auto backup:', auto_backup)
    print('Watcher backup:', watcher_backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
