#!/usr/bin/env bash
# Prepares what the iPad app embeds to run the Gamma server on the device
# (ipad/README.md "Python on the device"):
#
#   ipad/Python.xcframework              CPython 3.13 for iOS (BeeWare's
#                                        Python-Apple-support), linked and embedded
#   ipad/GammaIPad/Resources/python/
#     app_packages.iphoneos/             the server's dependencies as iOS device wheels
#     app_packages.iphonesimulator/      ... and as simulator wheels; a build keeps
#                                        the one for its platform (project.yml)
#     app/                               backend/app.py, backend/gamma/, gamma_ios.py
#     frontend_dist/                     the built web app the server serves
#
# Everything it makes is generated and gitignored (ipad/.gitignore). It is
# idempotent and quick when nothing changed, because Xcode runs it before
# every build: the download and the wheels are cached and stamped, and the
# copies are rsyncs.
#
#   bash ipad/scripts/build-python.sh           prepare everything (macOS)
#   bash ipad/scripts/build-python.sh --check   only resolve the pinned wheels for both
#                                               platforms, installing nothing (no Xcode needed)
#
# (Through bash: the executable bit does not survive a commit from Windows.)
#
# Environment: GAMMA_IPAD_CACHE (default ~/Library/Caches/gamma-ipad) holds
# the tarball, the pip cache and the pip the script runs; GAMMA_IPAD_PYTHON
# (default python3.13 when on PATH, else python3) is the Python pip and the
# bytecode compiler run under. Only pip and compileall run on the Mac:
# nothing from the tarball or the wheels is executed here (compiling a
# file does not run it).

set -euo pipefail

# Python-Apple-support 3.13-b16: Python 3.13.16, iOS 13 or later. The hash
# was computed from the downloaded release asset. A new build means a new
# URL and hash here; the wheels below follow its Python version (3.13).
SUPPORT_URL="https://github.com/beeware/Python-Apple-support/releases/download/3.13-b16/Python-3.13-iOS-support.b16.tar.gz"
SUPPORT_SHA256="22b96f3d0c3663225f078556b036d2ee6ce3ff98c885706585240681335c71ea"

# How pip picks wheels for the device rather than for this Mac. Binary only
# (nothing can be compiled for iOS here); the interpreter is CPython 3.13
# (cp313 extensions, abi3 ones and pure "none" wheels all match). Flet's
# index carries iOS builds of the native packages PyPI has none of
# (pydantic-core, cryptography, orjson, rpds-py, cffi). Environment markers
# are evaluated for the Python running pip, not for the target: on a Mac
# (or Linux) that skips Windows-only ones like mcp's pywin32, as the device
# does, and on Windows a full resolve fails on it. pip's python_version is
# that Python's too, whatever --python-version says, hence python3.13 below.
PIP_IOS_FLAGS=(
  --only-binary=:all:
  --python-version 3.13
  --implementation cp
  --abi cp313
  --abi none
  --extra-index-url https://pypi.flet.dev
)
# One wheel platform per Xcode platform name ($PLATFORM_NAME); both are
# arm64 (the simulator slice on an Apple Silicon Mac).
PLATFORMS=(iphoneos iphonesimulator)

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ipad_dir=$(dirname "$script_dir")
repo_dir=$(dirname "$ipad_dir")
cache_dir=${GAMMA_IPAD_CACHE:-$HOME/Library/Caches/gamma-ipad}
requirements=$ipad_dir/python/requirements-ios.txt
out=$ipad_dir/GammaIPad/Resources/python

# Xcode runs build scripts with a minimal PATH, and Homebrew's node and npm
# live outside it. Appended, so a PATH that already has them wins.
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
export PIP_CACHE_DIR="$cache_dir/pip"
export PIP_DISABLE_PIP_VERSION_CHECK=1

say() { echo "build-python: $*"; }
# A line starting with "error:" or "warning:" is what Xcode lists as an issue.
die() { echo "error: build-python: $*" >&2; exit 1; }
warn() { echo "warning: build-python: $*" >&2; }

# The header comment above, without its "# ".
usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }

# pip runs from a venv of its own in the cache, upgraded once when made:
# the Mac's python3 may carry an old pip (the Command Line Tools' Python 3.9
# has one without --dry-run) or refuse installs (PEP 668), and the venv
# avoids both without touching that Python. It is made from a python3.13
# when there is one, so markers see the device's Python version.
pip_venv=$cache_dir/pip-venv
pip_python=${GAMMA_IPAD_PYTHON:-$(command -v python3.13 || echo python3)}
ensure_venv() {
  if ! "$pip_venv/bin/python" -m pip --version >/dev/null 2>&1; then
    # Missing, or broken by an upgrade of the Python it was made from.
    say "making the pip venv in $pip_venv"
    rm -rf "$pip_venv"
    mkdir -p "$cache_dir"
    "$pip_python" -m venv "$pip_venv"
    "$pip_venv/bin/python" -m pip install --quiet --upgrade pip
  fi
}
pip_ios() {
  ensure_venv
  "$pip_venv/bin/python" -m pip "$@"
}

# Bytecode for the device, made here because the device makes none: the
# signed bundle is read-only (write_bytecode off in PythonRuntime.swift),
# so without it every launch compiles the standard library, the wheels and
# the server from source, and the first start on an older iPad takes long.
# Unchecked hashes, because the bundle never changes after it is signed:
# the device loads each .pyc without comparing it with its source, so
# every caller compiles again whenever its sources change. The .pyc
# format is per minor version, so only a 3.13 compiles; another Python's
# files would only be ignored on the device. A file that does not compile
# is left to the device.
compile_tree() {
  local what=$1
  shift
  ensure_venv
  if [ "$("$pip_venv/bin/python" -c 'import sys; print(sys.version_info[:2] == (3, 13))')" != True ]; then
    warn "no bytecode for $what: the pip venv's Python is not 3.13 (set GAMMA_IPAD_PYTHON to a python3.13 and delete $pip_venv)"
    return
  fi
  say "compiling $what"
  "$pip_venv/bin/python" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$@" ||
    warn "some of $what did not compile; the device compiles those at each launch"
}

# --check: does every pin still resolve to wheels for both platforms? pip
# download has no dry run, so this is pip install --dry-run: it fetches into
# the pip cache and installs nothing. --target keeps the venv's own packages
# out of the resolution.
check_pins() {
  local scratch platform
  scratch=$(mktemp -d)
  # Expanded now: the local is gone by the time the trap runs.
  trap "rm -rf '$scratch'" EXIT
  for platform in "${PLATFORMS[@]}"; do
    say "resolving $requirements for ios_13_0_arm64_$platform"
    pip_ios install --dry-run --quiet --target "$scratch" \
      --platform "ios_13_0_arm64_$platform" "${PIP_IOS_FLAGS[@]}" -r "$requirements"
  done
  say "every pin resolves for ${PLATFORMS[*]}"
}

# The tarball is downloaded once into the cache and checked against the
# pinned hash whenever it is unpacked, so a corrupt or swapped file never
# reaches the app. The unpacked framework records the hash it came from,
# so a new pin replaces it and an unchanged one costs nothing.
install_support() {
  local tarball framework stamp got unpack
  tarball=$cache_dir/$(basename "$SUPPORT_URL")
  framework=$ipad_dir/Python.xcframework
  stamp=$framework/.gamma-support-sha256
  if [ "$(cat "$stamp" 2>/dev/null)" = "$SUPPORT_SHA256" ]; then
    say "Python.xcframework is current"
    return
  fi
  mkdir -p "$cache_dir"
  if [ ! -f "$tarball" ] || [ "$(sha256_of "$tarball")" != "$SUPPORT_SHA256" ]; then
    say "downloading $SUPPORT_URL"
    curl -fL --retry 3 -o "$tarball.part" "$SUPPORT_URL"
    got=$(sha256_of "$tarball.part")
    if [ "$got" != "$SUPPORT_SHA256" ]; then
      rm -f "$tarball.part"
      die "the Python support tarball's sha256 is $got, expected $SUPPORT_SHA256"
    fi
    mv "$tarball.part" "$tarball"
  fi
  # Unpack beside the target and move it in, so an interrupted run never
  # leaves a half framework behind. Only Python.xcframework is taken; the
  # tarball's testbed project is not needed.
  unpack=$(mktemp -d "$ipad_dir/.python-unpack.XXXXXX")
  tar -xzf "$tarball" -C "$unpack" Python.xcframework
  # The standard library's tests (lib/python3.13/test, some of them broken
  # on purpose) stay as they are: the server never imports them.
  compile_tree "the standard library" -x '/lib/python3\.13/test/' "$unpack/Python.xcframework/lib/python3.13"
  rm -rf "$framework"
  mv "$unpack/Python.xcframework" "$framework"
  rmdir "$unpack"
  echo "$SUPPORT_SHA256" > "$stamp"
  say "unpacked Python.xcframework"
}

# One pip install per platform, each into its own folder. The stamp is the
# hash of the requirements and the flags, so an edit to either reinstalls
# and nothing else does; pip's cache makes a reinstall a copy, not a
# download.
install_packages() {
  local want platform dest
  want=$( { cat "$requirements"; printf '%s\n' "${PIP_IOS_FLAGS[@]}"; } | shasum -a 256 | awk '{print $1}')
  for platform in "${PLATFORMS[@]}"; do
    dest=$out/app_packages.$platform
    if [ "$(cat "$dest/.gamma-stamp" 2>/dev/null)" = "$want" ]; then
      say "app_packages.$platform is current"
      continue
    fi
    say "installing the iOS wheels into app_packages.$platform"
    rm -rf "$dest.partial"
    # --no-compile: pip's .pyc would check timestamps, which nothing keeps
    # for the bundle; compile_tree makes the device's own below.
    pip_ios install --quiet --no-compile --target "$dest.partial" \
      --platform "ios_13_0_arm64_$platform" "${PIP_IOS_FLAGS[@]}" -r "$requirements"
    # Console scripts point at the Mac's pip venv and nothing on the device
    # runs them. The .dist-info folders stay: importlib.metadata reads
    # package versions from them.
    rm -rf "$dest.partial/bin"
    compile_tree "app_packages.$platform" "$dest.partial"
    echo "$want" > "$dest.partial/.gamma-stamp"
    rm -rf "$dest"
    mv "$dest.partial" "$dest"
  done
}

# The server's own code, laid out as the device imports it: python/app is
# on the module search path, so gamma_ios and the gamma package sit at its
# top. backend/tests and the venv stay behind (only gamma/ is copied), and
# so does the checkout's own bytecode. rsync rewrites only the files that
# changed, which gives them a new ctime: the copy's bytecode is made again
# when a file is newer than the last compile, and only then, so an
# unchanged build stays quick and no .pyc is older than its source. (A
# deleted module's .pyc would be harmless, since a .pyc in __pycache__
# without its source is never imported; the clear-out removes it anyway.)
install_app() {
  local stamp=$out/.app-bytecode
  mkdir -p "$out/app"
  rsync -a --delete --exclude __pycache__ --exclude '*.pyc' \
    "$repo_dir/backend/gamma/" "$out/app/gamma/"
  rsync -a "$repo_dir/backend/app.py" "$ipad_dir/python/gamma_ios.py" "$out/app/"
  if [ ! -f "$stamp" ] || [ ! -d "$out/app/__pycache__" ] ||
     [ -n "$(find "$out/app" -name __pycache__ -prune -o -type f -cnewer "$stamp" -print)" ]; then
    find "$out/app" -name __pycache__ -type d -prune -exec rm -rf {} +
    compile_tree "the server code" "$out/app"
    touch "$stamp"
  fi
  say "copied the server code into Resources/python/app"
}

# The web app the local server serves (GAMMA_STATIC_DIR). The repository's
# helper rebuilds frontend/dist only when its sources changed (it hashes
# them), which keeps an unchanged Xcode build quick. A Mac without Node
# keeps the copy it has, if any, with a warning.
install_frontend() {
  local frontend=$repo_dir/frontend
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    if [ -f "$out/frontend_dist/index.html" ]; then
      warn "Node.js not found; keeping the web app already in Resources/python/frontend_dist"
      return
    fi
    die "Node.js is needed to build the web app (frontend/): install it, or run this script once from a shell that has it"
  fi
  # A missing install, or one older than the lockfile, gets a clean one.
  if [ ! -f "$frontend/node_modules/.package-lock.json" ] ||
     [ "$frontend/package-lock.json" -nt "$frontend/node_modules/.package-lock.json" ]; then
    say "installing the frontend's packages (npm ci)"
    (cd "$frontend" && npm ci)
  fi
  node "$repo_dir/tools/build_frontend_if_needed.mjs"
  [ -f "$frontend/dist/index.html" ] || die "frontend/dist/index.html is missing after the build"
  mkdir -p "$out/frontend_dist"
  # The helper's stamp describes frontend/dist, not this copy.
  rsync -a --delete --exclude .gamma-build.json "$frontend/dist/" "$out/frontend_dist/"
  say "copied the web app into Resources/python/frontend_dist"
}

case "${1:-}" in
  --check)
    check_pins
    exit 0
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  "")
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

[ "$(uname -s)" = Darwin ] || die "this step runs on macOS (only --check runs elsewhere)"
install_support
install_packages
install_app
install_frontend
say "done"
