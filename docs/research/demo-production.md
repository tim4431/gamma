# Polished repository demos

Surveyed 2026-09-15. Question: why do polished project demos feel smoother than
Gamma's existing README GIFs, and what can we reproduce locally?

## What the sources actually show

| Example | Documented technique | Relevance to Gamma |
|---|---|---|
| [Charm's VHS](https://github.com/charmbracelet/vhs) and its [example tapes](https://github.com/charmbracelet/vhs/tree/main/examples) | The README explicitly links its demo's source. Text scripts describe actions, pauses, dimensions and output; examples include Gum, Glow and GitHub CLI. | Keep the storyboard as source. It is easy to rerun a short deliberate sequence when the UI changes. VHS itself records terminals, so Gamma needs browser automation. |
| [Screen Studio](https://screen.studio/) | Documents cursor smoothing, click-focused automatic/manual zoom, framing/shadows, cutting idle time, cursor loop positioning, and video/GIF exports. | The polished appearance comes from motion direction and editing as well as capture quality. These techniques can be reproduced without changing Gamma's UI. Screen Studio itself targets macOS. |
| [Cap](https://github.com/CapSoftware/Cap) and [its documentation](https://cap.so/docs) | The open source project separates quick sharing from Studio Mode with local capture, backgrounds, zooms, trimming and export controls; the desktop app supports Windows/macOS. | A GUI editing option for this Windows workspace. Its published source also separates recording, rendering, and export concerns. |

These are documented production approaches, not evidence that an arbitrary
repository used a particular editor. A finished GIF rarely identifies the tool
that made it; vendor customer logos do not establish how those teams made their
GitHub README assets.

## What made the earlier GIFs look jerky

Low frame rates (10 to 12 fps), blanket 1.35 to 1.7× speed-ups of the whole
recording, and pointer moves split into Playwright steps without a time budget.
A modern-looking demo needs readable composition, consistent motion and a
concise story before decoration. Enlarging a low-rate GIF or converting it to
60 fps cannot recover missing interaction frames.

## Chosen direction and measured result

Keep Playwright because it can reproduce the real app, and borrow the editing
conventions above. Time-paced strokes and eased pointer travel preserve the
meaning of the drawing. Keep the paper, toolbar and note preview visible together.
Capture on an isolated copy of the curated workspace, and validate persistence
after the recorded segment. Do not mock the ink layer or paint a simulated app.

Deliver a single animated WebP per README slot. [Playwright's video documentation](https://playwright.dev/docs/videos)
explains that video dimensions must be configured explicitly and files finalize
when the context closes. The WebP encoder coalesces identical frames without
shortening holds; no frame interpolation is used.

[Google's WebP documentation](https://developers.google.com/speed/webp/faq)
documents animation support in modern Chrome, Edge, Firefox and Safari, and the
format's lossy/lossless choices. Measured on one 9.6-second ink clip at the same
dimensions (2026-09): lossy WebP at quality 85 **1.13 MiB**, GIF **3.33 MiB**,
lossless WebP **3.53 MiB**. A fixed camera keeps the toolbar, paper and note
preview in frame without adding motion to every text pixel.

The delivery rules, the recipe per slot and the published inventory live in
[tools/readme-media/README.md](../../tools/readme-media/README.md) and
[the asset directory](../assets/demos/README.md).

## Retina capture, a camera and a tile encoder (2026-09-30)

Why the earlier WebPs looked soft: they were captured at 1×, since Playwright's
`recordVideo` and Chrome's screencast deliver the window's CSS size even under
`deviceScaleFactor: 2`. Playwright then compresses to VP8 at 1 Mbps, and the
render scaled that to 60% for a 1040–1120 px image, so zoomed shots were 2×
upscales.

What works is a headless shell whose screen is 2× (`--force-device-scale-factor=2`
and a 1440 × 900 window, with no viewport emulation). The page gets its usual
viewport at devicePixelRatio 2, and the screencast delivers 2880 × 1800 frames.
CSS zoom on `<html>` was the other candidate. Chrome's standardized zoom returns
zoomed rects, which the app then applies as unzoomed px, so popups land at twice
their offset. GPU compositing is required: software readback held capture to
about 15 fps and slowed scripted input 2.5×; with the GPU it is about 30 fps at
normal timing. Rendered to 1600 × 900, the app is supersampled, and GitHub's
README column shows about 1.8 image px per CSS px, which is sharp on a retina
screen.

Animated WebP has no motion compensation. A frame in which everything moves
costs 50–130 KB at 1600 px, while a clean capture's unchanged pixels are
identical, so typing costs about 5 KB/s. Measured on the ink clip, a 0.8 s zoom
cost 1.8 MB at quality 70. Heavy motion blur (a 3 px Gaussian on a 180° shutter)
brought it to 0.6 MB but read as blur and was rejected. A light blur (15% of a
frame, at most 0.6 px) on 0.5 s moves costs 0.5–0.9 MB. Hence at most two moves
per clip, a still framing wherever the action allows, and no reframing inside a
dissolve: blending two framings costs as much as a move.

libwebp's animation encoder (behind FFmpeg's `libwebp_anim` and Pillow) judges a
pixel unchanged against the previous source frame, within a quality-dependent
tolerance (about 5 levels at quality 75, 12 at 40). A slow change never crosses
it, so what is on screen drifts from the source: blocks left behind after a
camera settles, a closed popup's shadow. `encode_master` keeps, for each 16 px
tile, the source it was last encoded from and at what quality. It re-encodes a
tile that drifted by more than one level, or that was encoded lower (an
isolated patch's flat colour lands a few levels off its neighbours), behind an
alpha mask, and writes the RIFF container itself. At equal quality it is the
same size without the ghosts.

Result: five README animations at 1600 × 900, 8.62 MiB together, against 10.34 MiB
for the previous four at 960–1120 px. Each file is smaller than the one it replaced.

## Abstract SVG scenes next to the recordings (2026-09)

Tried: the same three interactions (annotate + ink, notes with live math,
library search) as animated SVG illustrations in the branding style
(`tools/branding/build-demos.py`), to see whether "showing the idea" can
stand in for a recording.

What works: a scene is ~10 KB against 1–5 MiB per WebP, renders crisp at
any size, needs no server, no demo workspace and no re-recording when the
UI's pixels change — only when the interaction itself changes. SMIL keeps
it a plain `<img>` (GitHub strips scripts and ignores CSS animation inside
an embedded SVG; SMIL plays). Typing is one `<tspan>` per character switched
on in turn (`branding.typewriter()`, a wipe looked like a curtain, not a
keyboard); drawing is `stroke-dashoffset`; a cursor is an `animateTransform`.

What it cannot do: prove the feature exists. A recording shows the real
toolbar, the real latency, the real result; the abstraction shows a claim.
It also cannot show density — a real notes panel is busier than the scene.
Rules from the attempt: every `keyTimes` must end at 1 (a list ending early
silently disables that animation); glyph widths are unknowable (the viewer's
system font draws the text), so the caret hops along estimated advances and
`textLength` squeezes the line to the same estimate; and the less a scene
shows, the better it reads — the annotate scene ended up as one highlighted
line, one note and one stroke.

Decision: keep the recordings in the README, where a visitor decides
whether the product is real, and use the abstract scenes, light only, in
the user guide, where the reader already has the app and wants the idea.
