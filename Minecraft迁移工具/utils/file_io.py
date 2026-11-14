"""Exclusive temporary files; only remove files created by the current operation."""
import json
import os
import tempfile
from pathlib import Path


def check_writable(directory):
    fd, name = tempfile.mkstemp(prefix='.mctool-probe-', dir=directory)
    try:
        os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_json(path, data, indent=2):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.mctool-', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=indent)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
