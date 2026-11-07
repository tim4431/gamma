"""What a pytest run does about the files it writes and the stalls it can fall
into, the same for the server's suite and the account server's: both conftests
import this before they import their server (docs/dev/debugging.md, "What a
run leaves behind").

Every process of a run owns one folder in the system temp directory, holding
its data directory and, through ``tempfile``, whatever else it writes: import
staging, export spools, pytest's own ``tmp_path``. The folder goes when the
process exits. One that was killed cannot see to that, so the next run sweeps
what is older than a run could be.

pytest-timeout bounds a test and nothing else, so a run stuck between tests
would wait for good: an xdist controller waiting for a worker that was waiting
for it sat for five days. A controller, or a run without workers, that sees no
test finish for STALL_LIMIT ends itself, and its workers go with it as their
channel closes. A worker cannot judge that, being idle while the others finish,
so it only has WORKER_LIMIT, for one too stuck to notice its channel. The stacks
go to ``stalled.log`` in the folder, which an ended process leaves behind, and
not to stderr: a full pipe would stop the dump and the exit after it.
"""
import atexit
import faulthandler
import os
import shutil
import tempfile
import time
from pathlib import Path

LEFTOVER_AGE = 24 * 60 * 60
STALL_LIMIT = 15 * 60  # past the 300 s backend/pytest.ini gives one test
WORKER_LIMIT = 2 * 60 * 60  # past a whole run on one core
REWATCH_AFTER = 30  # a watch costs a thread, and a run has thousands of reports

_worker = "PYTEST_XDIST_WORKER" in os.environ
_folder = None
_log = None
_watched = 0.0


def own_folder(prefix):
    """Make this process's folder and send ``tempfile`` into it → the path of
    its data directory. What a killed run left under ``prefix`` is swept first."""
    global _folder
    if not _worker:
        _sweep(prefix)
    _folder = tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)
    folder = Path(_folder.name)
    (folder / "data").mkdir()
    (folder / "tmp").mkdir()
    tempfile.tempdir = str(folder / "tmp")
    atexit.register(_leave)
    return str(folder / "data")


def _sweep(prefix):
    for old in Path(tempfile.gettempdir()).glob(f"{prefix}*"):
        try:
            if time.time() - old.stat().st_mtime > LEFTOVER_AGE:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass  # another run swept it first


def _leave():
    faulthandler.cancel_dump_traceback_later()
    if _log:
        _log.close()
    _folder.cleanup()


def _watch():
    global _watched
    _watched = time.monotonic()
    faulthandler.dump_traceback_later(WORKER_LIMIT if _worker else STALL_LIMIT, exit=True, file=_log)


def pytest_configure():
    global _log
    _log = open(Path(_folder.name) / "stalled.log", "w")
    _watch()


def pytest_runtest_logreport():
    if not _worker and time.monotonic() - _watched > REWATCH_AFTER:
        _watch()


def pytest_enter_pdb():
    faulthandler.cancel_dump_traceback_later()


def pytest_leave_pdb():
    _watch()
