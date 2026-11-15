"""A writer's lock of a Codex thread held as a Codex that has the thread open
holds it (``<CODEX_HOME>/thread-writer-locks/<id>.lock``: a byte-range lock
on Windows, an exclusive ``flock`` elsewhere), standard library only:
``hold`` in a test's own process, or ``python hold_lock.py <path>`` from
another (the browser's scenario), which prints ``held`` once it holds the
lock and lets it go, removing the file, when its stdin ends."""

import os
import sys
from contextlib import contextmanager


@contextmanager
def hold(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        f.close()   # the lock goes with the handle
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    with hold(sys.argv[1]):
        print("held", flush=True)
        sys.stdin.read()
