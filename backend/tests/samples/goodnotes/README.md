# GoodNotes samples

Real notebooks for the GoodNotes import's tests
(`backend/tests/test_goodnotes_samples.py`, docs/dev/import_export.md
"GoodNotes notebooks").

| File | What it is |
|---|---|
| `gn-mac-mixed-pens.goodnotes` | One page of GoodNotes 6 for Mac (App Store, 2026-07-09), schema 25, on Standard yellow squared paper: fountain and brush strokes, a ball pen line, a pencil scribble, a highlighter stroke, a strip of tape and a filled ellipse from the shape recogniser |

It comes unchanged from
[cable729/inkterop](https://github.com/cable729/inkterop)
(`core/tests/fixtures/goodnotes/`, commit `f16eb8d`, 2026-07-11), whose
NOTICE dedicates its self-made fixtures to the public domain (CC0).

SHA-256:

- `b2b5612d4a1564335203a64fdaee675ec44cca17ba05f105ebef2b1527531ece` `gn-mac-mixed-pens.goodnotes`

More notebooks can be tested without adding them here:
GAMMA_GOODNOTES_SAMPLES names a folder of `.goodnotes` files the same
tests import too, and a `.pdf` beside one, named as it is, is taken as
GoodNotes' own Editable PDF export of it, which its strokes are held to.
The public notebooks that carry such an export (goodparse's `samples/`,
GPL-3.0, and parser-for-goodnotes' `assets/`, whose notice asks that
third-party content not be republished) are fit for that and not for this
folder.
