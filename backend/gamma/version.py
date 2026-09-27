"""Which build this server is, and whether a newer one exists.

The build is stamped through the environment: ``GAMMA_VERSION`` (the release
version, set by the Docker build from the release tag and by the desktop shell
from its own package version), ``GAMMA_COMMIT`` (the git sha the Docker image
was built from) and ``GAMMA_BRANCH``. A Docker image built from a push or a
branch dispatch rather than a release is stamped ``<newest v* tag>-dev.<n>``
(``n`` commits since that tag) with the branch it was built from. A checkout
run by hand has none of them and reports itself as a development build.

Two checks answer "is there something newer", both fetched on demand by the
admin dashboard through the SSRF guard and cached in memory:

- the latest release, from the GitHub Releases API (the desktop workflow
  publishes ``v<version>`` and dispatches the matching Docker tag — see
  docs/dev/github_actions.md);
- for a ``-dev`` build, how far its branch has moved past this commit, from
  GitHub's compare API. The branch's newest build is then
  ``<tag>-dev.<n - behind + ahead>``, the same count the Docker build takes.

A Docker container cannot update itself: the dashboard only says that a newer
image exists and how to pull it.
"""

import json
import os
import platform
import re
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request

from . import db
from .logbuf import counts as log_counts
from .net_guard import guarded_urlopen

VERSION = os.environ.get("GAMMA_VERSION", "").strip().lstrip("v")
UPDATE_CHECK = os.environ.get("GAMMA_UPDATE_CHECK", "").strip().lower() not in ("0", "off", "false", "no")
COMMIT = os.environ.get("GAMMA_COMMIT", "").strip()[:12]
BRANCH = os.environ.get("GAMMA_BRANCH", "").strip()
STARTED_AT = datetime.now(timezone.utc)

REPO = "tim4431/Gamma"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
COMPARE_API = f"https://api.github.com/repos/{REPO}/compare"
IMAGE = "ghcr.io/tim4431/gamma"

_CACHE_OK_SECONDS = 6 * 3600
_CACHE_FAIL_SECONDS = 10 * 60
_cache = {}  # check name → {"at", "ttl", "value", "error"}
_lock = threading.Lock()


def label() -> str:
    """One line for the startup log and the dashboard tile."""
    if VERSION:
        return f"v{VERSION}" + (f" ({COMMIT})" if COMMIT else "")
    return "development build" + (f" ({COMMIT})" if COMMIT else "")


def build_info() -> dict:
    """The build as every signed-in client may know it (``/api/session``):
    what a problem report names, nothing an admin dashboard adds."""
    return {"version": VERSION, "commit": COMMIT, "label": label(),
            "frozen": bool(getattr(sys, "frozen", False))}


def parse_version(text) -> tuple | None:
    """``v1.2.3`` / ``1.2.3`` → ``(1, 2, 3, 0)``; ``1.2.3-dev.7`` →
    ``(1, 2, 3, 7)``, seven commits after v1.2.3, so newer than it and older
    than v1.2.4; None for anything else."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:-dev\.(\d+))?", str(text or "").strip())
    return (int(m[1]), int(m[2]), int(m[3] or 0), int(m[4] or 0)) if m else None


def _get_json(url: str) -> dict:
    req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "gamma-server"})
    with guarded_urlopen(req, timeout=8) as resp:
        return json.loads(resp.read(200_000).decode("utf-8"))


def _fetch_latest() -> dict:
    """The newest release: ``{version, url, published_at}``. Raises on any
    trouble (network, a tag that is not a version)."""
    data = _get_json(RELEASES_API)
    tag = str(data.get("tag_name") or "")
    if not parse_version(tag):
        raise ValueError(f"unexpected release tag {tag!r}")
    return {"version": tag.lstrip("v"), "url": data.get("html_url") or RELEASES_PAGE,
            "published_at": data.get("published_at") or ""}


def _fetch_build() -> dict | None:
    """The newest build of this ``-dev`` build's branch: ``{version, branch,
    ahead_by, url}`` (``ahead_by`` commits there that this build lacks; ``url``
    is GitHub's comparison), or None for a build that tracks no branch. Page 2
    at one commit per page leaves out the file list and its patches."""
    mine = parse_version(VERSION)
    if not (mine and "-dev." in VERSION and COMMIT and BRANCH):
        return None
    data = _get_json(f"{COMPARE_API}/{COMMIT}...{quote(BRANCH, safe='')}?per_page=1&page=2")
    ahead, behind = int(data.get("ahead_by") or 0), int(data.get("behind_by") or 0)
    count = max(mine[3] - behind + ahead, 0)
    return {"version": f"{mine[0]}.{mine[1]}.{mine[2]}-dev.{count}", "branch": BRANCH,
            "ahead_by": ahead, "url": data.get("html_url") or ""}


def _cached(name: str, fetch, refresh: bool) -> tuple:
    """``(value, error)`` of one check, fetched when stale or on ``refresh``.
    A failed fetch is remembered briefly so a dashboard left open does not
    hammer GitHub."""
    with _lock:
        entry = _cache.get(name) or {"at": 0.0, "ttl": 0.0, "value": None, "error": ""}
        if refresh or time.monotonic() - entry["at"] >= entry["ttl"]:
            try:
                value, error, ttl = fetch(), "", _CACHE_OK_SECONDS
            except Exception as exc:  # noqa: BLE001 — any failure is just "could not check"
                value, error, ttl = entry["value"], f"{type(exc).__name__}: {exc}"[:200], _CACHE_FAIL_SECONDS
            entry = _cache[name] = {"at": time.monotonic(), "ttl": ttl, "value": value, "error": error}
        return entry["value"], entry["error"]


def check(refresh: bool = False) -> dict:
    """Is something newer than this build out? ``latest`` (the newest
    release), ``latest_build`` (the newest build of a ``-dev`` build's
    branch), ``update`` (``{kind: release|build, version, url}``, the one to
    move to, or None) and ``update_available``, None when this build carries
    no version or nothing could be asked. A newer release wins over the
    branch: once a new tag exists, the branch's count no longer applies."""
    if not UPDATE_CHECK:
        return {"latest": None, "latest_build": None, "update": None, "update_available": None,
                "latest_error": "the update check is switched off (GAMMA_UPDATE_CHECK)"}
    release, release_error = _cached("release", _fetch_latest, refresh)
    build, build_error = _cached("build", _fetch_build, refresh)
    mine = parse_version(VERSION)
    theirs = parse_version(release["version"]) if release else None
    update = None
    if mine and theirs and theirs > mine:
        update = {"kind": "release", "version": release["version"], "url": release["url"]}
    elif mine and build and build["ahead_by"] > 0:
        update = {"kind": "build", "version": build["version"], "url": build["url"]}
    answered = bool(mine and (theirs or build))
    return {"latest": release, "latest_build": build, "update": update,
            "update_available": bool(update) if answered else None,
            "latest_error": " · ".join(e for e in (release_error, build_error) if e)}


def server_info(refresh: bool = False) -> dict:
    """Everything the admin dashboard shows: the build, uptime, log counts
    and the update check (``check``)."""
    return {
        **build_info(), "branch": BRANCH,
        "started_at": STARTED_AT.isoformat().replace("+00:00", "Z"),
        "uptime_seconds": int((datetime.now(timezone.utc) - STARTED_AT).total_seconds()),
        "python": platform.python_version(), "platform": f"{platform.system()} {platform.machine()}".strip(),
        "schema_version": db.SCHEMA_VERSION,
        "log_counts": log_counts(),
        **check(refresh=refresh),
        "image": IMAGE, "releases_url": RELEASES_PAGE,
    }
