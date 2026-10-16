"""Entry module of the Gamma server inside the iPad app.

The Swift side (GammaIPad/Server/PythonRuntime.swift) sets the environment
before the interpreter starts (GAMMA_DATA_DIR, GAMMA_STATIC_DIR, GAMMA_PORT,
GAMMA_VERSION, GAMMA_CLOUD_DEFAULT_ISSUER, and GAMMA_ADMIN_* on a first
start), then runs ``import gamma_ios; gamma_ios.main()`` on a thread of its
own, where it blocks for the app's life. This is desktop/backend_entry.py
for the iPad: there the configuration comes from argv and the shell keeps
the output; here it is already in the environment and the output goes to
a file.

ipad/scripts/build-python.sh copies this file into the app bundle's
python/app/, next to app.py and the gamma package.
"""

import faulthandler
import os
import sys
from datetime import datetime, timezone


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


def main():
    data_dir = os.environ["GAMMA_DATA_DIR"]
    port = int(os.environ["GAMMA_PORT"])
    log = _redirect_output(data_dir)
    log.write(f"\n--- {datetime.now(timezone.utc).isoformat()} start port {port} ---\n")

    # The built web app sits beside this file's folder in the bundle
    # (python/frontend_dist next to python/app). Swift sets the path; this
    # only covers a runtime started without it, as the desktop entry does.
    static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend_dist")
    if os.path.isdir(static):
        os.environ.setdefault("GAMMA_STATIC_DIR", static)

    # gamma/config.py reads the environment at import time, so the import
    # comes after the environment is final, and after the redirect so an
    # import error lands in the log.
    import uvicorn
    from gamma.app import app

    # Loopback only: the web view in this app is the one client. uvicorn
    # installs SIGINT/SIGTERM handlers because this is Python's main thread
    # (the one that initialized it); iOS ends apps with SIGKILL, so they
    # change nothing there.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
