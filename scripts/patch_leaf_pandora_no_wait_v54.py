#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PATH = Path('/opt/trc-tuya/pandora_soc_auto_wakeup.py')
PYTHON = '/opt/trc-tuya/venv/bin/python3'

OLD = """        result = await fn()
        return bool(result) if result is not None else True
"""

NEW = """        result = await fn(ensure_complete=False)
        return bool(result) if result is not None else True
"""


def main():
    if not PATH.exists():
        raise RuntimeError(f'Missing {PATH}')

    text = PATH.read_text(encoding='utf-8')
    if NEW in text:
        print('PATCH_ALREADY_APPLIED')
        return
    if OLD not in text:
        raise RuntimeError('Expected trunk command block was not found')

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = PATH.with_name(PATH.name + f'.before-v54.{stamp}')
    shutil.copy2(PATH, backup)

    candidate = text.replace(OLD, NEW, 1)
    tmp = PATH.with_name(PATH.name + '.candidate-v54')
    tmp.write_text(candidate, encoding='utf-8')

    try:
        subprocess.run([PYTHON, '-m', 'py_compile', str(tmp)], check=True)
        PATH.write_text(candidate, encoding='utf-8')
        os.chmod(PATH, 0o750)
        subprocess.run([PYTHON, '-m', 'py_compile', str(PATH)], check=True)
        subprocess.run(['systemctl', 'reset-failed', 'trc-pandora-soc-auto-wakeup.service'], check=False)
        subprocess.run(['systemctl', 'restart', 'trc-pandora-soc-auto-wakeup.timer'], check=True)
    except Exception:
        shutil.copy2(backup, PATH)
        raise
    finally:
        tmp.unlink(missing_ok=True)

    print('PATCH_OK')
    print('Charge-flap command is sent without waiting for Pandora completion confirmation')
    print('Backup:', backup)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('PATCH_FAILED:', exc, file=sys.stderr)
        sys.exit(1)
