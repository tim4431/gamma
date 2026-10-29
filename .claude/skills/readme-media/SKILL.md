---
name: readme-media
description: Record and regenerate README screenshots and demos from the curated demo workspace using the tooling in tools/readme-media.
---

# README media

Read [the media tooling guide](../../../tools/readme-media/README.md) before
recording: setup, the recipe per README slot, the delivery rules and the
review steps live there, and the per-shot details in
[WORKFLOW.md](../../../tools/readme-media/WORKFLOW.md). This skill holds no
scripts.

- Use the real curated demo workspace (exported through the API). Do not invent
  a replacement library or read account databases for credentials. Credentials
  may be in project memory (`gamma-demo-account.md`); otherwise ask for the demo
  login. Never commit credentials, cookies, or workspace exports.
- The README stories record on `take.mjs` (isolated server, the scripted demo
  model for AI): a take is kept only when its checks pass. Keep to the house
  style in the media guide's delivery rules: one story per clip, waits cut,
  pointer and streams sped up (typing at 1×), results held, at most two camera
  moves.
- After a capture, render, then look at the frames of `<name>-preview.mp4` and
  `check-media.mjs`'s contact sheet, verify the demonstrated action saved,
  update the matching README link (and the site's, `sites/build.mjs` and
  `sites/site/index.html`), and stop any isolated server you started.
- Leave changes in the working tree. Do not commit unless the user asks.

The research behind the style lives in
[demo-production.md](../../../docs/research/demo-production.md) and
[demo-film-pipeline.md](../../../docs/research/demo-film-pipeline.md).
