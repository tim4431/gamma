# Local artifacts

This directory is ignored except for this guide. Keep executable source in
`tools/`, published media in `docs/assets/`, and disposable test output in `tmp/`.

- `readme-media/`: original WebM recordings, edit/timing JSON, required composite
  backgrounds and the private `demo.zip` workspace export. Regenerate published
  animations with [the media tooling](../tools/readme-media/README.md).
- `readme-media/browsers/`: local Playwright browser installation; set
  `PLAYWRIGHT_BROWSERS_PATH` to this absolute directory when using it.
- `branding/`: generated PNGs for inspecting the SVG illustrations.
- `debug/citation-regression/`: original PDF and extracted text for the citation
  regression already represented in `frontend/tests/fixtures/`.

No paper-mentions patch is kept here: the feature is in the code
(`frontend/src/chat/paperMentions.js`, `backend/tests/test_ai_mentions.py`).

Recordings and exports can contain private workspace content. Do not commit
them. Builds, browser profiles, logs, sessions, QA frames and lossless render
intermediates are reproducible and can be discarded after verification.
