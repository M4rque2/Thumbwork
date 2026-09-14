"""Independent configuration/project roots and atomic local persistence."""
from __future__ import annotations
import contextlib
import json
import os
import tempfile
from pathlib import Path


def config_root(override=None):
    return Path(override or os.environ.get('THUMBWORK_CONFIG_DIR') or Path.home() / '.thumbwork').expanduser().resolve()


def projects_root(override=None):
    return Path(override or os.environ.get('THUMBWORK_PROJECTS_DIR') or Path.home() / 'thumbwork_projects').expanduser().resolve()


def atomic_json(path: Path, value, *, private=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    if private and os.name == 'posix':
        path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def file_lock(path):
    """OS-backed advisory lock; released even when a process is killed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            try: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: raise RuntimeError('Another process is using this run or task.') from None
        else:
            import fcntl
            try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise RuntimeError('Another process is using this run or task.') from None
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()

