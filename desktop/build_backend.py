"""Freeze the Gamma backend into a self-contained folder the desktop shell
bundles as its local-workspace server.

Run with a Python that has backend/requirements.txt installed (the repo venv
locally, the CI interpreter on runners); it installs the pinned PyInstaller
and its hooks (PYINSTALLER) into that Python first when they differ:

    python desktop/build_backend.py

Output: desktop/dist-backend/gamma-server/  (onedir bundle with the built
frontend/dist inside; electron-builder copies it into resources/gamma-server).
"""

import os
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DESKTOP = REPO / "desktop"
DIST = DESKTOP / "dist-backend"
# The freeze's own pins, as backend/requirements.txt pins what it freezes:
# the hooks decide what goes into the bundle as much as PyInstaller does
# (certifi's CA bundle comes through its hook, for one).
PYINSTALLER = {"pyinstaller": "6.22.3", "pyinstaller-hooks-contrib": "2026.9"}


def ensure_pyinstaller():
    def installed(name):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None

    if any(installed(name) != pin for name, pin in PYINSTALLER.items()):
        pins = [f"{name}=={pin}" for name, pin in PYINSTALLER.items()]
        print(f"[build] installing {' '.join(pins)} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *pins])


def main():
    frontend_dist = REPO / "frontend" / "dist"
    if not (frontend_dist / "index.html").exists():
        sys.exit("frontend/dist missing - run `npm run build` in frontend/ first")

    ensure_pyinstaller()
    if DIST.exists():
        shutil.rmtree(DIST)

    sep = os.pathsep  # --add-data uses ; on Windows, : elsewhere
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "gamma-server",
        "--distpath", str(DIST),
        "--workpath", str(DESKTOP / "build" / "pyi-work"),
        "--specpath", str(DESKTOP / "build"),
        "--paths", str(REPO / "backend"),
        # The SPA the server serves; backend_entry points GAMMA_STATIC_DIR here.
        "--add-data", f"{frontend_dist}{sep}frontend_dist",
        "--add-data", f"{REPO / 'backend' / 'gamma' / 'mcp_icon.png'}{sep}gamma",
        # The seeded Welcome page (gamma/seed.py reads it next to the package).
        "--add-data", f"{REPO / 'backend' / 'gamma' / 'onboarding' / 'welcome.md'}{sep}gamma/onboarding",
        # Zotero's item types and fields (gamma/zotero_fields.py reads it next to the module).
        "--add-data", f"{REPO / 'backend' / 'gamma' / 'zotero_schema.json'}{sep}gamma",
        # uvicorn resolves loop/protocol classes from strings at runtime.
        "--collect-all", "uvicorn",
        "--collect-all", "websockets",  # uvicorn loads its websocket protocol lazily
        # PyMuPDF's native libraries (MuPDF and its C++ wrapper) live in its
        # package beside the extension module; collect-binaries takes them
        # alone. collect-all would also copy the package's C headers, import
        # libraries and .py sources, about 11 MB nothing reads at runtime.
        "--collect-binaries", "pymupdf",
        # Math typesetting fonts (STIXTwoMath / DejaVuSans live in package data).
        "--collect-data", "ziamath",
        "--collect-data", "ziafont",
        "--collect-data", "latex2mathml",
        # MCP validates tool schemas against the bundled JSON Schema metaschemas.
        "--collect-data", "jsonschema_specifications",
        str(DESKTOP / "backend_entry.py"),
    ]
    env = os.environ.copy()
    # Conda-based interpreters keep the DLLs their extension modules link
    # against (ffi.dll, sqlite3.dll, liblzma.dll, ...) in <env>\Library\bin,
    # which is not on PyInstaller's default DLL search path. Harmless no-op on
    # standard python.org / CI interpreters.
    lib_bin = Path(sys.base_prefix) / "Library" / "bin"
    if lib_bin.is_dir():
        env["PATH"] = str(lib_bin) + os.pathsep + env.get("PATH", "")
        print(f"[build] added DLL search path: {lib_bin}")

    print("[build]", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(REPO), env=env)
    exe = "gamma-server.exe" if os.name == "nt" else "gamma-server"
    print(f"[build] done: {DIST / 'gamma-server' / exe}")


if __name__ == "__main__":
    main()
