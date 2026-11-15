"""Immutable candidate records and atomic best-manifest replacement."""
import json
import os
from pathlib import Path
import tempfile


def write_once(path, data):
    with Path(path).open('x', encoding='utf-8') as out:
        if isinstance(data, str):
            out.write(data)
        else:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write('\n')


def atomic_json(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.manifest-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
