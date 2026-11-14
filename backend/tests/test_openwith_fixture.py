"""tests/shared/openwith.json, what the desktop app registers with the
operating system, held to the format registry (gamma/documents/formats.py
EXTENSIONS): for each kind, the registry's extensions of its format (and,
for text, of one of its languages) are its ``exts`` plus its ``left_out``,
the two disjoint, and none is blocked or sandboxed. A row added to the
registry in a registered language fails here until the fixture places it;
desktop/scripts/openwith.cjs then writes the installers' files from it
(desktop/test/openWith.test.js holds those to the fixture)."""

import json
from pathlib import Path

import pytest

from gamma.documents import formats

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "shared" / "openwith.json"
TABLE = json.loads(FIXTURE.read_text(encoding="utf-8"))
KINDS = TABLE["kinds"]


def _registry(kind: dict) -> set[str]:
    return {
        ext for ext, row in formats.EXTENSIONS.items()
        if row.format == kind["format"] and (kind["format"] != "text" or row.language in kind["languages"])
    }


@pytest.mark.parametrize("kind", KINDS, ids=[k["kind"] for k in KINDS])
def test_each_kind_is_the_registrys_rows_less_its_left_out(kind):
    exts, left_out = set(kind["exts"]), set(kind["left_out"])
    assert len(exts) == len(kind["exts"]) and len(left_out) == len(kind["left_out"])
    assert not exts & left_out, "an extension both registered and left out"
    assert _registry(kind) == exts | left_out
    assert kind["format"] in formats.FORMATS
    assert bool(kind["languages"]) == (kind["format"] == "text")


def test_nothing_registered_is_blocked_or_sandboxed():
    for kind in KINDS:
        for ext in kind["exts"] + kind["left_out"]:
            assert ext not in formats.BLOCKED_EXTENSIONS, ext
            assert ext not in formats.SANDBOXED_EXTENSIONS, ext


def test_kinds_and_progids_are_unique_and_named():
    names = [k["kind"] for k in KINDS]
    assert len(set(names)) == len(names)
    assert all(n == n.lower() and n.isidentifier() for n in names)
    progids = [k["progid"] for k in KINDS]
    assert len(set(progids)) == len(progids)
    assert all(p.startswith("Gamma.") for p in progids)
    assert next(k for k in KINDS if k["kind"] == "pdf")["progid"] == "Gamma.pdf", "an update keeps the PDF default a user gave"
    every = [e for k in KINDS for e in k["exts"]]
    assert len(set(every)) == len(every), "an extension under two kinds"


def test_no_mime_type_is_kept():
    """Windows and the Store type a file by its extension; a copied MIME
    type had drifted from the registry's."""
    for kind in KINDS:
        assert "mime" not in kind
        assert set(kind) == {"kind", "format", "languages", "exts", "left_out", "progid", "name"}
    assert TABLE["folder_verb"].replace("&", "") == "Open with Gamma"
