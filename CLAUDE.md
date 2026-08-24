# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Gamma is a self-hosted, Logseq-inspired PDF annotation server: highlight PDFs in the browser, organize notes as nested outliner blocks, share read-only annotated copies via link. Multi-user with per-user isolated SQLite databases; app-level session auth (no external provider).

## Docs

Topic docs live in `docs/dev/` — **read the relevant one before working in that area**, and keep them in sync with code changes:

- [docs/dev/api.md](docs/dev/api.md) — every `/api/*` endpoint, grouped, plus the auth model.
- [docs/dev/user_db.md](docs/dev/user_db.md) — the data directory and per-user DBs, auth middleware, seeding/first-run admin, `manage.py` CLI, user-management GUI, storage limits, the server log.
- [docs/dev/ai.md](docs/dev/ai.md) — the AI stack: provider entries and protocols (incl. ChatGPT OAuth), the `/api/ai/chat` request/stream shape, the library agent (scopes, permissions, tool loop, replay, privacy), chat-history buckets.
- [docs/dev/ai_tools.md](docs/dev/ai_tools.md) — the agent's tools: what each does, arguments and caps, guardrails.
- [docs/dev/ai_context.md](docs/dev/ai_context.md) — how the AI chat reads long papers: excerpt labelling, document map, search relaxation, page cap, the grounding prompt, and the eval results behind them.
- [docs/dev/paper_metadata.md](docs/dev/paper_metadata.md) — metadata fetch/edit/cite/status and `/api/resolve-pdf` resolution chain.
- [docs/dev/import_export.md](docs/dev/import_export.md) — the Import/Export dialogs and pipelines: embedded PDF annotations, Zotero and Logseq imports, Markdown export, the annotated-PDF writer (fonts, vector math/CJK, images).
- [docs/dev/home_library.md](docs/dev/home_library.md) — folder labels, the merged listing and sorts, the shared page card, recents strip + cover snapshots, the home context menu.
- [docs/dev/settings.md](docs/dev/settings.md) — where every setting is stored (localStorage / synced prefs / server), the Settings dialog's pane and file layout, storage limits.
- [docs/dev/ui-design.md](docs/dev/ui-design.md) — the unified control classes, settings primitives, theme system, layout rules, frontend file map.
- [docs/dev/debugging.md](docs/dev/debugging.md) — run/test/debug: commands, test suite, log surfaces, common gotchas.

## Commands

```bash
# Backend (FastAPI, Python 3.11+) — from backend/, venv active
pip install -r requirements.txt
python manage.py setup                  # idempotent: guest account + missing per-user DBs
uvicorn app:app --host 127.0.0.1 --port 9001 --reload

# Frontend (React + Vite) — from frontend/
npm run dev      # :5173, proxies /api → 127.0.0.1:9001
npm run build

# Tests — from backend/ (pip install -r requirements-dev.txt once)
python -m pytest tests -q               # in-process TestClient, throwaway data dir
```

Frontend has no test suite or linter; verify UI changes by running the app. Docker image bundles both (multi-stage build, FastAPI serves `dist/`). More in [docs/dev/debugging.md](docs/dev/debugging.md).

## Architecture

### Backend (`backend/`)

- All state is SQLite + files under a data directory (env `GAMMA_DATA_DIR`, defaults to `backend/`): global `users.db` (accounts, sessions, shares, server settings) plus per-user `users/<username>/pages.db` (the `unified_blocks` tree — everything is a block; root-level blocks are pages, highlights are blocks with `highlight_id`/`pdf_position` in `properties`), `data.db` (chats, synced prefs KV, page snapshots, AI provider entries) and `uploads/` (content-hash filenames, dedup). Layout details: [docs/dev/user_db.md](docs/dev/user_db.md).
- Auth: `session` cookie → middleware resolves `request.state.user`; guest data is wiped daily. Share tokens allow unauthenticated reads — shared-view endpoints resolve the user via `_resolve_user` fallback, write endpoints use `_require_user`. Keep that distinction when touching endpoints.
- Route order matters for `/api/blocks/*`: static-prefix routes (`by-doc`, `children`, `subtree`) must be registered before `/{block_id}`.
- AI: providers are per-user GUI entries — there are NO env API keys; AI endpoints must build config through `ai_runtime(user)` (`gamma/ai_settings.py`), never module-level constants. Chat speaks Anthropic Messages, OpenAI Chat Completions, and the ChatGPT-OAuth Responses wire; the library agent's tool registry lives in `gamma/ai_tools.py`. All wiring: [docs/dev/ai.md](docs/dev/ai.md); long-paper context: [docs/dev/ai_context.md](docs/dev/ai_context.md).
- Paper metadata + PDF resolution (arXiv/DOI/Unpaywall chains, AI extraction fallback, BibTeX/citation caching): [docs/dev/paper_metadata.md](docs/dev/paper_metadata.md).
- Import/export (embedded-annotation import + strip, Zotero/Logseq imports, Markdown export, the annotated-PDF writer with its font/vector-text/image machinery): [docs/dev/import_export.md](docs/dev/import_export.md).
- Endpoints doing slow work (downloads, AI calls, PyPDF2) are deliberately sync `def` — FastAPI runs them in its threadpool so they don't block the event loop. Don't convert them back to `async def` while they hold blocking calls.
- Search: `/api/pdf-search` is a lazily built per-user FTS5 index over pypdfium2-extracted text; `/api/block-search` shares the same normalization (`gamma/textnorm.py` — ligatures, hyphenated line breaks, digit separators; bump `textnorm.INDEX_VERSION` when extraction/normalization changes). The index stores no positions — the frontend re-finds matches with pdf.js on open. The normalization rules are mirrored in `frontend/src/search.jsx` and `pdfViewer.jsx`; keep all three in sync.
- Accounts and admin: first-run admin seeding, `manage.py` CLI, the Settings → Users GUI, storage limits (`check_upload_allowed`/`can_store`), and the scrubbed in-memory server log (`logbuf.log` — use it, not `print()`): [docs/dev/user_db.md](docs/dev/user_db.md).
- Package layout: `gamma/config.py` (env config), `gamma/db.py` (schemas/paths), `gamma/auth.py` (middleware), `gamma/seed.py` (user DB creation), `gamma/blocks_store.py` (tree CTE helpers), `gamma/storage.py` (uploads + content-hash PDF store), `gamma/foldertags.py` (folder-label path rules, mirrored in `frontend/src/libraryUtils.js`), `gamma/textnorm.py` (search normalization + fuzzy matching), `gamma/logseq_import.py` + `gamma/zotero_import.py` (import parsers), `gamma/ai_client.py` (provider wire protocols), `gamma/ai_context.py` (PDF/chat context assembly), `gamma/ai_tools.py` (agent tool registry), `gamma/routers/*` (one module per API area), `gamma/app.py` (assembly + SPA serving).

### Frontend (`frontend/`)

- `src/App.jsx` — still the main component (decomposition in progress): routing (URL query params, no router lib), block tree editor, dockable windows (react-resizable-panels v2 — v4 has an incompatible API), autosave (500 ms debounce), login, ChatGPT-style AI chat, background-tasks popover.
- `src/prefs.js` — every localStorage-backed user preference, one `useAppPrefs()` hook (keys, defaults, codecs). Add new browser preferences here, not as loose `usePersistedState` calls in App.jsx. Open tabs, the recents queue and the active AI key additionally sync through `/api/prefs/*` (server wins, localStorage is the instant-paint cache). Storage-layer overview: [docs/dev/settings.md](docs/dev/settings.md).
- `src/search.jsx` — the workspace search (Ctrl+F): `SearchPanel` popover, results grouped titles → this paper's notes → this PDF → other notes → links → library-wide PDF content; collapsible into a compact find bar (default per place via Settings → Reading → Search). `buildSearchRegex` mirrors the backend's fuzzy rules. Opening a library hit "pins" the search: after the paper renders, the query is re-found via pdf.js and highlighted. No replace UI (the `/api/blocks-replace` endpoint still exists, unused by the frontend).
- `src/pdfViewer.jsx` — the custom pdf.js viewer (`PdfViewer`/`PdfPage`/`PlainTip`, exports `COLORS`): lazy memoized pages, capped DPR, cancelable render tasks, highlight/link overlays; its `searchRef` searches normalized per-page text with a char-level map back to exact rects.
- `src/logseqPdfModel.js` — pure block-tree operations (insert/indent/outdent/flatten/cycle-check).
- `src/blockCmEditor.jsx` — the block editor (CodeMirror 6 behind a textarea-compatible facade): live in-place rendering of closed `$…$`/`$$…$$` math, `[[ref]]` chips, and markdown (headings, quotes + `> [!type]` callout boxes, bold/italic/code/strike, links, clickable todos, bullets, `---` rules) — the construct the caret touches stays raw, Obsidian-style. `src/slashMenu.jsx` holds the "/" command catalog + popup; `src/callouts.js` is the callout remark plugin; `blockTree.jsx` owns trigger detection, key handling (incl. markdown list continuation on the line-break Enter) and the popups' state.
- Settings dialog: `src/settings.jsx` (nav + most panes) + `src/settingsKit.jsx` (pane primitives and shared controls) + `src/settingsAi.jsx` + `src/settingsUsers.jsx`. Panes are built ONLY from the settingsKit primitives, every choice control is a `MenuSelect`/`ActionMenu` dropdown (`menus.jsx`), new controls belong in the shared set — `settings.css` is layout only. Pane map and design rules: [docs/dev/settings.md](docs/dev/settings.md), [docs/dev/ui-design.md](docs/dev/ui-design.md).
- Theme: Settings → General — System/Light/Dark (`gamma-theme`; inline script in `index.html` applies a pinned theme before first paint) plus display-only "Flip page colors" (`gamma-pdf-dark`).
- View modes are derived from the URL: `/` home, `/?page=<id>` page (with PDF if it has `source_url`), `/?share=<token>` public read-only, `/?block=<id>` jump-to-block.
- Reference links: a highlight block with `properties.link_url` / `link_page_id` is a clickable link region on the PDF; `link_highlight_id` additionally targets an exact highlight in that paper. Document links resolve against the library by DOI/arXiv id before offering fetch-vs-browser.
- Home library (folder labels, merged listing, the shared `PageCard`, recents strip + snapshots, context menu): [docs/dev/home_library.md](docs/dev/home_library.md).
- Menus (`src/menus.jsx`): `ContextMenu` + row primitives (`MenuItem`/`MenuLabel`/`SubMenuItem`). A flyout renders INSIDE the parent menu's DOM (portalling would break the outside-pointerdown test) and opens on hover guarded by `src/menuAim.js` (the "safe triangle"; UI-agnostic, reuse for any hierarchical surface).
- Frontend always talks same-origin `/api/*`; in dev Vite proxies to :9001. Endpoint reference: [docs/dev/api.md](docs/dev/api.md).

### Data-model invariants

- Block positions are fractional-index strings; sibling order is lexicographic on `position`. Use `generate_key_between` — never invent position strings.
- `PUT /api/blocks/{id}/children` replaces the entire subtree (delete + reinsert); it and block deletion trigger orphan-upload cleanup (files no longer referenced by any block content/properties get deleted).
- Timestamps are UTC ISO strings with `Z` suffix (`page_now()`); clients parse them, keep the format.
