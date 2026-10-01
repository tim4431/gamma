# Shot recipes

Start with [README.md](README.md) for setup, recording and WebP delivery. These
recipes describe the real interactions; selectors and timing marks live in the
corresponding recorder. The source workspace must be the user-curated demo
library. Use its exported copy for recording, not synthetic replacement papers.

| Demo | Story | Recording details |
|---|---|---|
| Highlight, annotate & draw | Highlight a phrase and note why it matters, circle the claim it makes possible, draw an arrow from one to the other, lasso the arrow and recolor it red | `record-ink.mjs --annotate`; isolated server; gestures placed from the text layer; note, three strokes and two recolored checked after reload; `render_feature_demos.py annotate-and-ink` |
| Native agentic | One PDF Chat on Figure 1: Ctrl+drag panels c,d and ask about them, hover a passage citation for its quote and follow it, then ask it to find the two most-cited follow-ups and save them; allow the save in this chat | `record-native-agentic.mjs`; fresh workspace; real model responses and web search; figure attachment, exact citation, PDF context, two saved papers verified; `render_feature_demos.py native-agentic` trims model waits and closes in on the agent's steps |
| AI in the notes | A notes page: cursor on a block, paste a slide's equation into the chat, ask for it under that block, allow the edit; the KaTeX block appears | `record-agentic-notes.mjs`; `rabi-slide.png` in the shot's directory; 130% interface size; block under the cursor holds the LaTeX and the request the picture, verified |
| Notes | Type markdown, a page reference, nested display math with autocomplete, then `/page` makes a sheet and a stylus sketches on it | `run-case.mjs notes`; isolated copy of the curated export; 130% interface size; CDP pen input; text and sheet strokes checked after reload |
| Search | In the atom-arrays paper, Ctrl+F finds "rydberg" and Enter steps through its matches; Ctrl+P finds the "quantum computing" label and opens its two papers | `run-case.mjs search`; topic labels prepared, the account's paper-view search reset to compact |
| Library (website) | Home search, title and PDF results, folder filter, open a highlighted match | `run-case.mjs library`; Quantum subfolders prepared, recents populated by navigation |
| Agent (scratch preview) | Ask to organize papers, show real tool calls and the resulting folders | `run-case.mjs agent`; requires configured AI |
| Metadata | Fetch a paper, watch fields fill, copy BibTeX and slide citation | `run-case.mjs metadata`; arXiv 2312.03982 removed first; never fill metadata by hand |
| Q&A (scratch preview) | Paste Attention paper URL, select and highlight a sentence, ask for a short explanation | `run-case.mjs download-and-chat`; requires configured AI |
| Reference links | Click citation 36, jump to its reference, fetch the linked paper | `run-case.mjs reference-links`; arXiv 0904.2557 removed so Fetch appears; fixed detail crop includes the citation and modal |
| Collaboration | A shared workspace's page seen by its owner: the owner and Maya type two questions into one block at once, Sam captions another block and pastes a figure under it | `record-collab.mjs`; isolated server; the others are real sessions in a second browser; typing pauses every two words so edits arrive live; both lines, caption and picture checked on the server |
| Connector | A PRL paper's APS page, the toolbar badge, the real popup: folder, label, Save, open in Gamma | `run-case.mjs connector`; full Chromium at 2× with the unpacked extension; APS requests fetched with curl past its bot check, institution notices removed; popup opened through its `?tab=` hook and hung under a composited toolbar; PDF, folder and label checked |

## Details that matter

- Log in through `/api/login` and reuse its session cookie. Never mint sessions
  or inspect `users.db`. Source account credentials may be in project memory;
  otherwise ask for the demo login.
- AI configuration belongs to the account, not the exported workspace. AI cases
  use a disposable personal workspace on the existing demo account. Ink and
  connector use isolated accounts because they need no AI; so do notes and
  collaboration.
- Capture with `launchRetina()` and `startCapture()`, not `recordVideo`: Chrome's
  screencast is capped at the window's CSS size, so device-scale emulation still
  records 1×, and CSS zoom on `<html>` misplaces the app's popups (the rects are
  zoomed, the styles are not). A 2× screen with a 1440 × 900 window has neither
  problem. For bigger UI use the app's Interface size (`gamma-ui-scale`).
- Pin `locale: 'en-US'` (`RETINA` does): headless Chromium takes the system
  locale, and the recorders find buttons by their English names. Tour offers are
  switched off the way the e2e harness does it.
- Move the pointer with timed samples. Playwright `steps` alone does not specify
  motion duration; `runtime.mjs` paces every `steps > 1` move.
- Use real mouse drags for sentence highlights. PDF text spans provide start/end
  character coordinates; commit with `.plainTip .colorBtn` before clearing the
  selection. A tiny reference number can use an exact DOM Range when dragging
  would select surrounding text. Dismiss its color popup before clicking Fetch.
- Native citation overlays are `.pdfLinkBox`, with titles such as `Jump to
  reference` or the target URL. Scroll the citation and its destination into a
  consistent part of the frame before choosing a detail crop.
- PDF pages are virtualized. After fetching, check the new URL's `page` or `block`
  ID, then wait for text to paint. An old page-1 selector can remain absent when
  the reader restores a later page.
- Zoom the recorded frame if needed, not the app's PDF zoom: app zoom changes
  wrapping and click coordinates. The camera's rects are recorded `framing`
  boxes, and a box must hug its content: KaTeX's display wrappers and a block row
  span the whole width.
- Metadata must resolve on camera. Cached papers cannot reproduce the fetching
  state; prepare a fresh fetch in the disposable workspace.
- The notes editor uses actual keyboard input for autocomplete and argument hops.
  Backslashes must reach CodeMirror literally; Enter/Tab nesting and blur produce
  the rendered result.
- Timing JSON and source-path files are scratch artifacts. `render-suite.py`
  trims those marks and keeps a short hold at the end of each visible outcome.

## Stills

`shoot-stills.mjs` navigates the curated demo to capture the annotated PDF, home,
and library search for documentation. Read each image back and check that the
paper and highlights have painted, panels are useful, and no loading state remains.

`shoot-hero.mjs` makes the website's hero, `docs/assets/screenshots/hero-app.webp`:
an isolated server with the curated export, the atom-arrays paper on Fig. 1 in a
2× window, a sentence highlighted with a comment in its notes, and the paper's
chat under them. The chat is the question and the model's answer from the earlier
hero (`01-annotated-pdf.png`), saved as the paper's conversation; nothing is sent
to an AI. The model list is answered with the demo account's model (as the e2e
harness's `fakeAiModels` does), so the chat shows its composer instead of the
"connect an AI service" card. The script checks the saved highlight; the PNG
master stays in `artifacts/readme-media/hero/`; `--inspect` saves the framing.
