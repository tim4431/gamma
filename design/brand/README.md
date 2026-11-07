# Gamma brand sources

Edit brand artwork here, then run from the repository root:

```sh
node tools/branding/build.mjs
node tools/branding/build.mjs --check
```

Generation's requirements are in [tools/branding](../../tools/branding/README.md);
the check needs only Node. Commit the updated sources, consumer outputs and
`generated.json` together. Normal builds and unpacked extension installs use
committed outputs; they need no generator.

## Source ownership

| Source | Purpose |
|---|---|
| `marks/favicon.svg` | The only editable Gamma mark, used for every icon and composition |
| `compositions/` | Hero layouts and the shared Gamma PDF logo layout |
| `tokens.json` | Brand palette and typography reference; the Store layouts read its colors directly |
| `android/` | The Android adaptive-icon XML, and the colour resource the generator fills with the mark's plate colour |

Open `marks/favicon.svg` in a browser or SVG editor to edit the mark. Keep the
32 by 32 viewBox and the `gamma-background` ID on its background rectangle:
the generators use that ID to omit the plate for monochrome masks, the
transparent Windows tiles (`Square150x150Logo` and friends, which the MSIX
manifest's `backgroundColor` paints instead) and the Android launcher icon's
layers, and read the plate colour from it. Geometry, stroke weights and icon colors come from this file.

The generator changes only size, positioning and presentation. Desktop, MCP,
plugin and extension icons share the same amber design. Disabled extension
icons are generated in grayscale. The Gamma PDF logo and README illustrations embed the
same mark; Store layouts control padding and transparent/full-bleed backgrounds.
There are no separate vector or raster icon masters to synchronize.

The main logo matches the hero: bold Gamma plus amber PDF, using the hero's
Inter/Segoe UI font stack. Store posters and all README headers reuse this logo. The amber accent is
`#e8a020`, the dark plate `#1e1e1c`, and the warm foreground `#eeebe4`.
SVG artwork retains its explicit colors for portability; when changing the
palette, update the reference tokens and relevant vector sources together.
These brand tokens do not replace the app's independent theme tokens.

Use `docs/assets/branding/gamma-logo.svg` (or the transparent 2040 × 336
`gamma-logo.png`) for the standalone one-line logo on light backgrounds. Its
layout is shared with all README headers and Store posters; edit
`compositions/logo.svg` to change it. `compositions/banner-light.svg` is a
2560 × 1440 banner on the hero's warm paper (the logo centred over a standing
wave, the promise below), published as `gamma-banner-light.svg` and `.png`,
and cropped close around the text as `gamma-banner-crop-light` (1420 × 700,
its PNG at 2×). The unused Gamma/PDF Annotator wordmarks
and their templates have been removed.

## Published copies and provenance

`outputs.json` maps direct copies and SVG templates to existing consumer paths.
Store sizes/layouts live in `tools/branding/store-layouts.mjs`; README scenes
are the `tools/branding/build-*.py` generators (connections, workspaces,
library, anywhere, and the demo scenes), each writing its light SVG to
`docs/assets/branding/`. `generated.json` records
every generated destination, recipe, dimensions and SHA-256, plus hashes of
the sources and rendering toolchain. Do not edit this ledger manually.

`--check` detects missing/modified outputs, changed inputs, missing manifest
destinations and unregistered assets in managed directories without rendering.
It normalizes text line endings for Windows/Linux checkouts. It verifies the
recorded generation state, not pixel reproducibility across operating systems;
review regenerated images before committing renderer or font updates.

Published paths stay unchanged: `docs/assets/branding/`, the frontend favicon,
mask and home-screen icons (full-bleed plates through the `bleed` option,
[docs/dev/ipad.md](../../docs/dev/ipad.md)), desktop resources, extension icons, backend MCP icon, plugin icon,
and the Android app's launcher icon and Play Store art (below).
They are generated copies because each package must remain self-contained.
The website (`sites/`) keeps no copies: `sites/build.mjs` takes its favicon,
logo and hero from these paths at each build.

The Android app's launcher icon is adaptive, three layers the launcher masks
to its own shape (`android/app/src/main/res/`): the plate colour
(`values/ic_launcher_background.xml`), the bare mark on a transparent 108 dp
square (`mipmap-*/ic_launcher_foreground.png`, mdpi to xxxhdpi) and the same
mark in white for themed icons (`ic_launcher_monochrome.png`, which Android 13
and later tint by its alpha). `bleed: 12.5` with `bare` sets the 32-unit mark
in a 57-unit square, so its farthest point, the outer arcs' end caps, lies
31.3 dp from the centre, inside the 66 dp circle every mask keeps.
`mipmap-anydpi/ic_launcher.xml` and `ic_launcher_round.xml` are copies of
`android/ic_launcher.xml`. The Play Store icon `android/store/icon-512.png`
(`bleed: 3`) is that icon's visible 72 dp square as a full-bleed plate, and
`android/store/feature-graphic.png` (1024 × 500) is the Chrome marquee's layout
with the logo's name. Both are opaque; the icon keeps an alpha channel all the same (`alpha: true`), as Play asks for a 32-bit icon, and the feature graphic has none, as Play asks. The check treats every PNG or SVG under
`android/app/src/main/res/` and `android/store/` as brand output, so a
hand-made picture there fails it; Play screenshots belong elsewhere.

Screenshots and recordings remain in `docs/assets/`; third-party logos and
licenses remain with the frontend; UI glyphs and React illustrations remain
code. Preview renders belong in ignored `artifacts/branding/`.

Removed during consolidation: unused disabled connector icons at 48/128px,
the README's superseded connector animation, and old local inspection PNGs.
The 16/32px disabled icons are still required by the extension toolbar.
