#!/usr/bin/env python3
"""Container entry point: make the mounted data directory writable, then drop root."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
uid = gid = 10001
path = Path(os.environ.get('EDGARIO_DATA_DIR', '/app/data')).resolve()
path.mkdir(parents=True, exist_ok=True)
if hasattr(os, 'geteuid') and os.geteuid() == 0:
    os.chown(path, uid, gid)
    os.chmod(path, 0o700)
    for file in path.iterdir():
        if file.is_file() and not file.is_symlink() and file.name.startswith('edgario.sqlite3'):
            os.chown(file, uid, gid)
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
os.execv(sys.executable, [sys.executable, str(ROOT / 'server.py'), '--host', '0.0.0.0'])
