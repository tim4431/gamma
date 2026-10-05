# Branding artwork

Authoritative artwork and variant guidance live in
[`design/brand/`](../../design/brand/README.md).

From the repository root:

```sh
node tools/branding/build.mjs          # regenerate all published copies and ledger
node tools/branding/build.mjs --check  # check sources, coverage, hashes, dimensions
node --test tools/branding/check.test.mjs  # the check's own test (CI runs both)
node tools/branding/render.mjs         # optional inspection PNGs in artifacts/
```

Generation uses Python 3 (standard library), the frontend's locked Playwright
dependency and its Chromium headless shell. Run `npm ci --prefix frontend`,
then `npx playwright install chromium-headless-shell` from `frontend/`.
Set `PYTHON` if the interpreter has a different name. Rendering is offline;
the logo uses the same Inter/Segoe UI system font stack as the hero.

`build.mjs` distributes canonical artwork, generates README scenes, renders
Store art and hero PNGs, and records provenance. `store-layouts.mjs` owns
Store dimensions and layouts while loading geometry from the canonical
sources: the Microsoft Store art in `desktop/assets/` and the Chrome Web
Store promo tile and marquee in `extension/store/` (opaque, as that store
requires). The desktop's `npm run store-art` remains an alias for the unified
generation.

An `outputs.json` entry of kind `crop` publishes a window onto a
composition. Its `box` (x, y, width, height in the composition's units)
becomes the root's size and viewBox, and the element ids in `hide` are left
out. The demo server's landing picture
(`frontend/public/media/gamma-scene-light.svg`) is the hero's paper and notes
cropped this way, without the hero's left-hand promise (`#hero-promise`).

Edit hero templates in `design/brand/compositions/`. The other scenes are
the `build-*.py` scripts, one per README illustration: `build-connections.py`,
`build-workspaces.py`, `build-library.py`, `build-anywhere.py` (one library on
the server, every device, an edit reaching all of them) and `build-demos.py`
(the abstract, animated counterparts of three recorded demos — annotate,
notes, search — light only, shown in the user guide next to the WebP
recordings, not in their place). `build.mjs` runs every `build-*.py` it finds and records the
paths the script prints, so a new scene is a new script and nothing else.
Their shared `branding.py` reads `design/brand/marks/favicon.svg` directly,
independent of the hero layout. Scenes animate with SMIL (`<animate>`,
`<animateTransform>`): it plays inside a plain `<img>` on GitHub and on the
site, where scripts and CSS animations in an SVG do not. Every `keyTimes`
list must end at 1 or the browser drops that animation. **Typed text always
goes through `branding.typewriter()`** — one `<tspan>` per character switched
on at its own moment (a jittered rhythm, slower after spaces and
punctuation), a caret and an optional name tag that hop from letter to
letter, and `textLength` pinning the line to the estimated width so the
caret ends on the last letter in any system font; `test_branding.py` pins
the SMIL shape (`python -m pytest tools/branding/test_branding.py`). `node tools/branding/render.mjs [name…]`
renders inspection PNGs of the first frame. All main branding headers and Store posters
reuse `design/brand/compositions/logo.svg`. Run the full build after modifying any source.

Consumer copies stay committed at existing paths. The generator does not alter
recordings, screenshots, third-party logos, or frontend theme CSS. The Gamma
Light/Dark app themes live in `frontend/src/shared/styles/tokens.css`.

README feature illustrations share the logo placement, warm paper background, amber
curves and card shadow defined in `branding.py`, with 72 px headings and 28 px
introductory copy on a 1920 × 1080 canvas. The real recordings use the matching
16:9 paper frame, `Frame` in `tools/readme-media/compose.py`.
