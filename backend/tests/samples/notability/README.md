# Notability samples

Real notes for the Notability import's tests
(`backend/tests/test_notability_samples.py`, docs/dev/import_export.md
"Notability notes").

| File | What it is |
|---|---|
| `NOTE Note Apr 26, 2026.note` | Notability's "Note" export of one note (its `Session.plist` names 16.1.5): pen, pencil and highlighter strokes, dashes, strokes cut by the eraser and the shape eraser, every kind of shape, partly erased ones, four ink groups (two of them turned copies), photos cropped and turned, stickers, an animated GIF, two sticky notes and typed text with lists |
| `NTB Note Apr 26, 2026.ntb` | The same note's "NTB" export (its `manifest.json` names 16.10) |

Both come unchanged from the `input/` folder of
[nokcha0/Notability-notes-converter](https://github.com/nokcha0/Notability-notes-converter)
(commit `bb87583`, 2026-09-27), whose example note this is; its thumbnail,
`thumb12x.png` inside the `.note`, is Notability's own drawing of page 1.
That repository states no licence.

SHA-256:

- `c489cbdfe7e008195508ca980df8c66d3d386bf0bcbc6755c5f3af1eec44203f` `NOTE Note Apr 26, 2026.note`
- `f54217cc13689872ac27d88bb5b0444736a1ac601544e3fd3089c247cfdb0bb5` `NTB Note Apr 26, 2026.ntb`

More notes can be tested without adding them here: GAMMA_NOTABILITY_SAMPLES
names a folder of `.note` and `.ntb` files the same tests import too.
