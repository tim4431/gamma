"""Entry module of the Gamma server inside the iPad app.

The Swift side (GammaIPad/Server/PythonRuntime.swift) sets the environment
before the interpreter starts (GAMMA_DATA_DIR, GAMMA_STATIC_DIR, GAMMA_PORT,
GAMMA_VERSION, GAMMA_CLOUD_DEFAULT_ISSUER, and GAMMA_ADMIN_* on a first
start), then runs ``import gamma_ios; gamma_ios.main()`` on a thread of its
own, where it blocks for the app's life. This is desktop/backend_entry.py
for the iPad: there the configuration comes from argv and the shell keeps
the output; here it is already in the environment and the output goes to
a file.

The interpreter starts once per process and is never finalized, so the
server is run under a supervisor (``supervise``): when it stops, or could
not listen, the supervisor writes why to ``<data dir>/logs/server.state``
and waits; ``restart()``, which the app calls on another thread
(PythonRuntime.restart), stops a server still running and runs it again on
the same port. That is how a failed start is retried and a listening
socket iOS reclaimed while the app was suspended is replaced, without
opening the app again.

ipad/scripts/build-python.sh copies this file into the app bundle's
python/app/, next to app.py and the gamma package.
"""

import faulthandler
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

# The supervisor's state for the app, in logs/ beside the log: {"state":
# "starting" | "stopped", "detail", "at"}. The app reads it while it waits
# for /api/health to answer: "stopped" is a start that failed (detail says
# how), shown with a Try again. Written whole and renamed into place, so a
# read never sees half of it.
STATE_FILE = "server.state"

_restart = threading.Event()
_current = None  # the uvicorn.Server serving now, for restart()
_state_path = None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _redirect_output(data_dir):
    """Send print, logging and tracebacks to <data dir>/logs/server.log.

    An iOS app has no console that outlives the run, so a file is where a
    failed start can be read: the app shows its tail, and the Files app
    reaches the data directory. Line-buffered so the tail is current, and
    appended across launches like the desktop's per-server log, each start
    marked the same way."""
    logs = os.path.join(data_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    stream = open(os.path.join(logs, "server.log"), "a", buffering=1,
                  encoding="utf-8", errors="backslashreplace")
    sys.stdout = sys.stderr = stream
    # A crash in native code (a wheel's extension, the stdlib's C modules)
    # kills the app without a Python traceback; this writes the threads'
    # stacks to the same file first.
    faulthandler.enable(file=stream)
    return stream


def write_state(path, state, detail=""):
    """The supervisor's state file (STATE_FILE), replaced in one step. Never
    raises: the file is advice for the app, and an OSError (a full disk; on
    Windows, a reader holding the file open while it is replaced) must not
    end the supervisor. A few tries, then False."""
    tmp = path + ".tmp"
    for _attempt in range(10):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"state": state, "detail": detail, "at": _now()}, f)
            os.replace(tmp, path)
            return True
        except OSError:
            time.sleep(0.01)
    return False


def supervise(make_server, state_path, log):
    """Run the server ``make_server()`` returns (a uvicorn.Server: ``run()``
    blocks while it serves, ``should_exit`` stops it, ``started`` says it
    listened, a bind that failed is a SystemExit), and when it stops, run a
    new one once ``restart()`` is called. Never returns. A stop nobody
    asked for is written to the state file as "stopped", with why; one a
    restart asked for is not, since the next server starts at once."""
    global _current, _state_path
    _state_path = state_path
    while True:
        _restart.clear()
        write_state(state_path, "starting")
        server = _current = make_server()
        try:
            server.run()
            detail = "the server stopped" if getattr(server, "started", True) else "the server did not start"
        except SystemExit:
            # uvicorn could not listen (the port is taken): it logged the error
            detail = "the server could not listen on its port; the log says why"
        except BaseException as error:  # noqa: BLE001 - whatever ends the server is reported, not raised
            log.write(traceback.format_exc())
            detail = f"the server failed: {error}"
        _current = None
        if not _restart.is_set():
            write_state(state_path, "stopped", detail)
            log.write(f"\n--- {_now()} {detail}; waiting for a restart ---\n")
        _restart.wait()
        log.write(f"\n--- {_now()} restart ---\n")


def restart():
    """Asked by the app (PythonRuntime.restart), from a thread that holds the
    GIL for this call: a server still running is told to exit, and the
    supervisor runs a new one. The state file says "starting" before this
    returns, so the app never reads the old failure as the new start's."""
    if _state_path:
        write_state(_state_path, "starting")
    server = _current
    if server is not None:
        server.should_exit = True
    _restart.set()


def main():
    data_dir = os.environ["GAMMA_DATA_DIR"]
    port = int(os.environ["GAMMA_PORT"])
    log = _redirect_output(data_dir)
    log.write(f"\n--- {_now()} start port {port} ---\n")

    # The built web app sits beside this file's folder in the bundle
    # (python/frontend_dist next to python/app). Swift sets the path; this
    # only covers a runtime started without it, as the desktop entry does.
    static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend_dist")
    if os.path.isdir(static):
        os.environ.setdefault("GAMMA_STATIC_DIR", static)

    # The cryptography wheel for iOS links OpenSSL 3 without its legacy
    # provider and refuses to import until told that is expected; the server
    # needs none of the legacy algorithms (it uses Fernet: AES and HMAC).
    os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")

    # gamma/config.py reads the environment at import time, so the import
    # comes after the environment is final, and after the redirect so an
    # import error lands in the log.
    import uvicorn
    from gamma.app import app

    # Loopback only: the web view in this app is the one client. uvicorn
    # installs SIGINT/SIGTERM handlers because this is Python's main thread
    # (the one that initialized it); iOS ends apps with SIGKILL, so they
    # change nothing there. The app's lifespan (gamma/app.py: the sweeps,
    # the backup schedule) runs with each server and ends with it.
    def make_server():
        return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))

    supervise(make_server, os.path.join(data_dir, "logs", STATE_FILE), log)


if __name__ == "__main__":
    main()
