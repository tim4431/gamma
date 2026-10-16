"""The iPad's server dependencies (ipad/python/requirements-ios.txt) keep up
with the server's own (backend/requirements.txt).

The iPad installs iOS wheels, and some packages have none at the server's
pin or at all, so the iOS file is kept by hand. This test makes a new
server dependency force that decision: each package the server names is
either in the iOS file (at whatever pin has an iOS wheel) or left out here
with the reason. ipad/README.md "Python on the device" has the build step.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "backend" / "requirements.txt"
IOS = REPO / "ipad" / "python" / "requirements-ios.txt"

# Package → why the iPad goes without it. The server must still run
# without each (its imports are guarded).
EXCLUDED = {
    "pypdfium2": ("no iOS wheel; gamma/pdf_text.py falls back to PyPDF2 for text, "
                  "and page images for the AI and MCP (pdf_text.render_page) are unavailable"),
}


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


def test_every_server_dependency_is_decided_for_the_ipad():
    undecided = _names(SERVER) - _names(IOS) - set(EXCLUDED)
    assert not undecided, (
        f"backend/requirements.txt names {sorted(undecided)}, which "
        "ipad/python/requirements-ios.txt neither lists nor excludes: add an iOS "
        "pin (see the file's header) or an EXCLUDED entry with the reason")


def test_excluded_packages_are_really_left_out():
    both = _names(IOS) & set(EXCLUDED)
    assert not both, f"{sorted(both)} is excluded here but listed in requirements-ios.txt"
    stale = set(EXCLUDED) - _names(SERVER)
    assert not stale, f"{sorted(stale)} is excluded but the server no longer needs it"
