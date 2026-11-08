"""The mobile apps' server dependencies (backend/requirements-mobile.txt,
the iPad's and Android's) keep up with the server's own
(backend/requirements.txt).

The apps install wheels built for iOS and Android, and some packages have
none at the server's pin or at all, so the mobile file is kept by hand. This
test makes a new server dependency force that decision: each package the
server names is either in the mobile file (at whatever pin has mobile
wheels) or left out here with the reason. tools/check_mobile_pins.sh checks
that every pin resolves for the apps' platforms.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "backend" / "requirements.txt"
MOBILE = REPO / "backend" / "requirements-mobile.txt"

# Package → why the apps go without it, for one with no mobile wheel; the
# server in the apps must then run without it. Empty: every package the
# server names has iOS and Android builds.
EXCLUDED = {}


def _names(path):
    """The canonical names (PEP 503) a requirements file asks for."""
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line).group(0)
        names.add(re.sub(r"[-_.]+", "-", name).lower())
    return names


def test_every_server_dependency_is_decided_for_the_apps():
    undecided = _names(SERVER) - _names(MOBILE) - set(EXCLUDED)
    assert not undecided, (
        f"backend/requirements.txt names {sorted(undecided)}, which "
        "backend/requirements-mobile.txt neither lists nor excludes: add a "
        "mobile pin (see the file's header) or an EXCLUDED entry with the reason")


def test_excluded_packages_are_really_left_out():
    both = _names(MOBILE) & set(EXCLUDED)
    assert not both, f"{sorted(both)} is excluded here but listed in requirements-mobile.txt"
    stale = set(EXCLUDED) - _names(SERVER)
    assert not stale, f"{sorted(stale)} is excluded but the server no longer needs it"
