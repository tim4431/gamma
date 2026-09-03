# API reference

All endpoints are same-origin under `/api`. The frontend never talks anywhere
else; in dev, Vite proxies `/api` → `127.0.0.1:9001`.

## Auth model

- A `session` cookie identifies the user (middleware sets
  `request.state.user`). Write endpoints require it (`require_user`).
- Share tokens (`?share=<token>`) are the ONLY unauthenticated **read** path.
  `resolve_user` returns the session user, or the owner named by a valid
  `?share=` token — there is no `?user=` fallback (it used to trust any
  username and leaked whole accounts). A share is keyed by PAGE (the root
  block — so note pages without a PDF share exactly like papers; the PDF is
  just the page's `doc_id`/`source_url`) and scoped to it: read endpoints that
  can serve a share view also call `share_scope_page()` and
  `blocks_store.assert_block_in_page()`, so a token can only reach its own
  page's subtree and assets (its PDF, uploads its blocks reference, its own
  `source_url` through the proxy) — root listing, backlinks, other pages, and
  folder export are refused (403). Rows minted by the old doc-keyed model
  (`page_id` NULL) were backfilled once by `gamma/migrate.py` (see
  [user_db.md](user_db.md)); auth treats a row without `page_id` as dead.
- **Share permissions** (`shares.audience` / `role` / `allowed_users`,
  `auth.share_access`) are Notion-shaped and additive: the owner INVITES
  people (`users: [{name, role}]`, stored as `carol:edit,dave:view`) who get
  in with their own `view`/`edit` whatever general access says; everyone else
  goes through general access — `audience` `anyone` (no session, always view),
  `users` (any signed-in non-guest account, with the share's `role`), `list`
  (nobody beyond the invited). When a request carries `?share=`, the token decides
  WHOSE data is read (the owner's — a signed-in visitor sees the owner's page,
  not their own library) while the session decides whether the audience gate
  admits them; a refused token is 401 when signing in could help, else 403.
  `edit` shares (never valid with `anyone`) let `require_writer` resolve the
  owner for the block writers — `POST /blocks`, `PUT /blocks/{id}`,
  `DELETE /blocks/{id}`, `PUT /blocks/{id}/children`, `POST /blocks/{id}/reorder`,
  `POST /upload-image`, `POST /upload-file` — each of which confines the touched blocks to the
  shared page (no new pages, no deleting/moving the page itself, no changes to
  the page root's properties). Everything else stays session-only.
  Keep that read/write + scope distinction when adding endpoints.
- Outbound fetches of user-supplied URLs (PDF proxy/resolver, AI PDF
  re-download) go through `gamma.net_guard.guarded_urlopen`, which blocks
  non-http(s) schemes (`file:`, `ftp:`, …) and hosts that resolve to
  loopback/private/link-local/metadata addresses (SSRF), re-checking on every
  redirect.
- Usernames and doc ids are validated (`db.safe_username` / `db.safe_doc_id`,
  used by `user_db_path` / `user_uploads_dir` / `pdf_upload_path`) before they
  become filesystem paths — no traversal.
- The session cookie is `HttpOnly; SameSite=Lax`, and `Secure` when the request
  is HTTPS (auto via scheme / `X-Forwarded-Proto` — off on plain-HTTP LAN so
  login still works there). Sessions are enforced server-side against
  `SESSION_MAX_AGE` (expired rows are deleted in the middleware) and are revoked
  when the account's password is changed.
- `/api/login` and `/api/login-guest` are rate-limited per IP/username
  (`gamma/ratelimit.py`, in-process fixed windows → 429). Not an edge WAF; add
  one for large public deployments.
- Every response carries baseline hardening headers (`X-Content-Type-Options`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`, `Content-Security-Policy:
  frame-ancestors 'self'`, and HSTS on HTTPS). SVG uploads are served
  `Content-Disposition: attachment` + `CSP: sandbox` so they can't run inline as
  stored XSS.
- `/api/admin/*` additionally requires the `is_admin` flag.

## Endpoints

### Session & account (`auth.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/login`, `/login-guest`, `/logout` | session management |
| GET | `/session` | who am I (identity only — quota lives in `/quota`) |
| GET | `/export` (+ `/export-progress`) | backup zip (everything or DB-only); admins may target `?user=` |
| POST | `/import-data` | restore/merge a backup zip |

### Blocks (`blocks.py`) — the core data model
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/blocks/by-doc/{doc_id}` | lookup / create the page BY ATTACHMENT — the page whose PDF is `doc_id` (POST creates it: `{default_title, source_url?, original_filename?}`); the PDF-ingest + extension-dedup path. Text-only pages come from `POST /pages` |
| GET | `/blocks/{id}/children`, `/{id}/subtree`, `/{id}/backlinks` | tree reads; the root listing (`/blocks/root/children`) additionally gives every page a `preview` — the first ~240 chars of its first non-highlight child blocks joined with ` · ` (one window query, `""` when empty) |
| POST/PUT/DELETE | `/blocks`, `/blocks/{id}` | CRUD |
| PUT | `/blocks/{id}/children` | replace the whole subtree (delete + reinsert; triggers orphan-upload cleanup) |
| POST | `/blocks/{id}/reorder` | sibling reorder |
| GET | `/block-search` | fuzzy note/page/highlight search; empty `q` returns recently edited blocks (feeds the `[[ref]]` popup's initial suggestions) |
| POST | `/blocks-replace` | bulk replace (no frontend UI currently) |

Route order matters: the static-prefix routes (`by-doc`, `children`,
`subtree`) must stay registered before `/blocks/{block_id}`.

### Pages (`pages.py`) — page first, PDF as an action on it
| Method | Path | Purpose |
|---|---|---|
| POST | `/pages` | create a text-only root page: body `{title?, folder?}` (title defaults to `Untitled`, `folder` → `properties.folder`) → the block dict |
| POST | `/pages/{page_id}/attachment` | attach a PDF to a page that has none: body `{doc_id?, source_url?, original_filename?}` (at least one of `doc_id`/`source_url`; `doc_id` is shape-validated only — a URL-opened PDF's id is the URL hash and the proxy fetches it lazily, like `by-doc`; `source_url` defaults to `/api/uploads/<doc_id>.pdf`). While the title is still automatic (`Untitled`/empty) it becomes the file name / URL tail and is marked `auto_title`. → the updated block. 400 bad input / not a root page, 404 unknown page, 409 `{"detail": "page already has an attachment"}`, 409 `{"detail": "attachment belongs to another page", "page_id"}` |
| DELETE | `/pages/{page_id}/attachment` | drop `doc_id`/`source_url`/`original_filename` (highlights keep their `pdf_position`; the orphan sweep deletes the file unless another page references it) → `{ok, block, removed_uploads}`; 404 when the page has no attachment |

All session-only (`require_user`): a share token never creates pages or
touches a page's attachment. `GET /pages/{id}/export*` live in `export.py`.

### PDFs & uploads (`pdf.py`, `uploads.py`, `shares.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/resolve-pdf` | URL/arXiv/DOI → fetchable PDF (citation_pdf_url sniffing, Unpaywall OA fallback) |
| GET | `/pdf` | proxy/download a PDF (`save=1` caches it server-side) |
| POST | `/uploads`, `/upload-image` | store a PDF / an image (content-hash names, dedup'd; quota-gated) |
| POST | `/upload-file` | store any allowed file for a block to reference as `[name](/api/uploads/<hash>.<ext>)`: md, txt, csv, json, tex, bib, py, ipynb, html, docx, xlsx, pptx, zip, plus images (routed like `/upload-image`) and PDFs; extension from the uploaded name; same hashing + limits → `{url, name, size, already_existed}`; 400 for anything else |
| GET | `/uploads/{filename}` | serve stored files with their media type; pdf / images / txt / md render inline, everything else is `Content-Disposition: attachment` (html additionally sandboxed like svg) |
| GET | `/quota` | effective limits + usage for the session user |
| POST | `/share/{page_id}` | create the page's share link (defaults `anyone`/`view`; optional body `{audience, role, users}` applies to a NEW link) or return the existing one unchanged — root blocks only (400 otherwise) |
| GET/PUT/DELETE | `/share-settings/{page_id}` | owner: read settings (`{token: null}` when unshared) / change `audience`, `role`, `users` (`["carol"]` or `[{name, role}]`; validated: `edit`+`anyone` → 400, unknown usernames or roles → 400; the token stays) / stop sharing (the token dies) |
| GET | `/share/{token}` | resolve a link for this viewer → `{page_id, doc_id, username, audience, role, can_edit, viewer}` (`doc_id` = the page's PDF attachment id via `page_attachment`, `""` without one — the vestigial `shares.doc_id` column is never read); 404 unknown, 401 sign in first, 403 signed in but not allowed |

### Search (`search.py`, `gamma/block_index.py`, `gamma/pdf_index.py`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/search?q=&limit=&scope=` | one search over the knowledge base: notes (`block_fts`) + PDF text (`pdf_fts`). `scope` = `""` (library) or a folder path (that folder and its subfolders). → `{"results": [...], "indexing": n}`; results are notes hits first (bm25 order) then PDF hits, each capped at `limit` (default 20, max 100). A notes hit is `{"source": "notes", "block_id", "page_id", "title", "snippet"}` (`block_id` = the matched block, `page_id` its page root, `title` the page's); a PDF hit is `{"source": "pdf", "block_id", "page_id", "doc_id", "title", "page", "snippet"}` (`block_id` = `page_id` = the page carrying the PDF, `page` the 1-based PDF page). `indexing` = note pages still waiting for a rebuild batch + PDFs the background extractor hasn't reached. Owner-only |
| GET | `/pdf-search` | the PDF-only predecessor (same `pdf_fts` index; hits `{block_id, doc_id, title, page, snippet}`) — the Ctrl+F panel's library group still uses it (with `/block-search` for notes: fuzzy/regex + flags that FTS does not offer) |
| POST | `/search-reindex` | full rebuild (PDF text re-extracted in the background, every note page stamped stale for the next search), or just `doc_ids` from the body |
| GET | `/tasks` | background task progress (indexing, downloads) |

The notes index is rebuilt lazily per page: a search first refreshes every
page whose `block_fts_meta` row is missing, older than `textnorm.INDEX_VERSION`,
or no longer matches the page root's `updated_at`; the block writers that
change a child without touching the root (`POST /blocks`, `PUT /blocks/{id}`,
`DELETE /blocks/{id}`, `PUT /blocks/{id}/children` on a nested block, a
re-parenting `reorder`, `blocks-replace`) call `block_index.mark_page_dirty`.
Deleting a page or detaching its PDF prunes its rows (`block_index.purge_page_data`,
which also drops the `pdf_fts` rows of papers no page carries and the deleted
blocks' chats). The `pdf_fts` schema and its shared queries (`pdf_missing`,
`search_pdf`) live in `gamma/pdf_index.py`; extraction and the background
indexer in `search.py`.

### Link previews (`links.py`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/link-preview?url=` | webpage title for the frontend's link chips (`{url, host, title}`); fetch goes through the SSRF guard, results cached in-process (TTL 24 h) |

### Browser extension (`clip.py`) — see [extension.md](extension.md)
| Method | Path | Purpose |
|---|---|---|
| POST | `/clip` | one-shot "save this page": dedup by DOI/arXiv/URL → resolve → fetch + store (`save_copy`) → page (`get_or_create_doc_page`) → folder/labels → metadata in a background thread. Body: `source_url, pdf_url, doi, arxiv_id, doc_id (pre-uploaded bytes), title, selection, folder, labels, allow_oa, save_copy`. Returns `{block_id, doc_id, title, existed, open_url, folder, labels, note?}`. **No PDF resolvable** (a plain web page, or a dead/HTML link) → a page titled from `title` (else the URL tail) with `properties.web_url = source_url` and the `selection` (if any) as its first `> quote — [title](url)` block; `doc_id` is `""` and `note` says so. Re-clipping that URL finds the page (`find_web_page`, by `web_url` on attachment-less pages), files it and appends the new selection. Only a request with nothing at all (no URL, title or selection) is a 400 |
| GET | `/library/lookup?doi=&arxiv_id=&url=` | is this page in the library (`properties.meta`, `source_url`, `web_url`, URL hash — web-clip pages by `web_url`)? 404 when not |
| GET | `/library/folders` | `{folders, labels}` in use (folder paths include their ancestors) — the popup's pickers |
| POST | `/clip/note` | the explicit "clip into page" append: `> quote — [title](url)` as the last block of `page_id`, or of the "Web clips" page (created on first use) |

All four are session-only (`require_user`), never share-token readable.

### Metadata (`metadata.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/metadata/fetch` | resolve a paper (arXiv → DOI → AI extraction), cache meta + BibTeX on the page |
| POST | `/metadata/update` | save hand-edited fields (rebuilds BibTeX) |
| POST | `/metadata/cite` | BibTeX → PPT-style citation via AI |
| GET | `/metadata/status` | library-wide health table (feeds Settings → Library): every page with a PDF attachment plus pages carrying `properties.meta` without one (`has_file: false`) |

### AI (`ai.py`) — all config is per-user GUI entries, no env API keys
| Method | Path | Purpose |
|---|---|---|
| POST | `/ai/chat` | chat; NDJSON stream of `{context}` (first line: per-page coverage — native/text, pages shown of total; `doc_id` `""` for a page without a PDF) then `{delta}`/`{action}`/`{progress}`/`{error}`; `progress` previews an edit_block/create_block call still being written (target id + markdown so far). Context is `pages` (several) or `page_id` (one; its PDF attachment derived server-side; `doc_id` is accepted as a compatibility input and resolves to its page), plus model id, effort, images, files, the agent scope, and the notes pointers `focus_block_id` (cursor block), `context_blocks` (attached block ids), `note_passages` (Ctrl-selected note text). See [ai.md](ai.md) |
| GET | `/ai/models` | model registry (each model carries `native_pdf`: whether its provider accepts the PDF file itself) + default prompts (feeds the model switchers and prompt editor) |
| GET | `/ai/settings` | masked provider list (key hints only) |
| POST/PUT/DELETE | `/ai/providers[/{id}]` | manage provider entries |
| POST | `/ai/providers/{id}/test` | live probe of one credential (model: the entry's `test_model`, else the request's `model` — the client sends its metadata model — else the first model); failures carry an `auth` flag for expired/rejected credentials |
| POST | `/ai/providers/{id}/usage` | ChatGPT subscription allowance windows; explicitly unavailable for generic API-key providers; an expired sign-in returns `{available: false, auth: true}` in-body |
| POST | `/ai/health` | login connection check of one entry (`{provider_id, mode}`; `""` = first entry): `mode: "ping"` is the free credential check (OAuth → usage endpoint, API key → `/v1/models`), `"test"` the tiny live completion; always answers in-body `{configured, ok, auth?, error?}` |
| POST | `/ai/model-catalog` | list models available to a credential |
| POST | `/ai/oauth/chatgpt/start`, `/complete` | ChatGPT OAuth (PKCE, pasted callback URL) |
| POST | `/ai/transcribe` | voice dictation |
| POST | `/ai/translate` | translate paragraph texts for the viewer's translated view (`{texts, lang, model, effort, stream}` → `{translations}`; with `stream: true` an NDJSON stream of `{i: [indices], text}` partials as each paragraph is written, then the same final object; in-memory per-paragraph cache) |
| GET | `/pdf-text-status` | whether a doc has extractable text |

### Chats (`chats.py`, prefix `/api/chats`)
| Method | Path | Purpose |
|---|---|---|
| GET/PUT/DELETE | `/chats/{key:path}` | chat history per bucket: page id, `home`, or `home:<folder>` (hence `:path`) |
| POST | `/chats/folder-rename` | migrate folder buckets on rename/move/delete (`{src, dst}`, dst `""` deletes) |

### Import & export (`imports.py`, `export.py`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/import/logseq` | Logseq .pdf + .edn import |
| POST | `/import/markdown` | UTF-8 `.md`/`.markdown` file → note page and nested blocks (optional `folder`; a front-matter `folder:` files it below that) |
| POST | `/import/markdown-zip` | zip of Markdown notes → one page per `.md` (multipart `file`, optional `folder` prefix): Notion "Markdown & CSV" exports (subpage folders → folder labels, databases → table pages, links → mentions, images uploaded), Gamma Markdown exports (folder/source/meta/bibtex restored) or any zipped notes. Idempotent by file digest / `notion_id` |
| POST | `/markdown-blocks` | parse markdown text into a `{content, children}` tree without storing anything (the editor's paste-as-blocks helper; same parser as `/import/markdown`, 5 MB cap) |
| POST | `/import/pdf-annotations` | import annotations embedded in the PDF (idempotent; optional `strip`) |
| POST | `/import/zotero` | Zotero library import: zip of a "Zotero RDF" export (multipart `file`; `strip`, optional `folder` prefix). Items→pages+metadata, collections→folders, tags→labels, notes→blocks; embedded annotations via the same importer. Idempotent by file hash / `zotero_key` |
| GET | `/pages/{id}/export` | page export (`?mode=readable|notes-pdf|logseq-graph|zotero-rdf|gamma` + `highlights=&notes=&pdf=`); `notes-pdf` = the notes typeset as their own PDF (works without a paper); `gamma` = scoped backup for `/import-data?mode=merge` |
| GET | `/pages/{id}/export-pdf` | the page's own PDF with annotations written back (`?highlights=&notes=`) |
| GET | `/folders/export` | whole-folder export, same modes/flags (`?name=` + `mode=`); subfolders become Zotero collections, `notes-pdf` one PDF for the whole folder |
| GET | `/folders/export-progress` | per-page progress of a running folder export (`{active, total, done, title}`) |

### Prefs (`prefs.py`)
| Method | Path | Purpose |
|---|---|---|
| GET/PUT | `/prefs/{key}` | small synced JSON KV (`open-tabs`, `recent-views`, `ai-provider`, …); refuses the reserved `ai-settings` key |
| GET | `/page-snaps` | all recents-card cover thumbnails `{snaps: {pageId: {img, at}}}`; `?after=<iso>` returns only newer ones (the focus-pull delta) |
| PUT | `/page-snaps/{page_id}` | store a cover (JPEG data URL body `{img, at}`; per-page newest-`at` wins, count-capped server-side) |
| DELETE | `/page-snaps/{page_id}` | drop a cover (the recents card's ×) |

### Admin (`admin.py`, prefix `/api/admin`)
| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/admin/users` | list (with usage) / create accounts |
| PUT/DELETE | `/admin/users/{name}` | password, admin flag, storage overrides / delete |
| POST | `/admin/users/{name}/rename` | rename (moves the data dir first; sessions survive) |
| GET/PUT | `/admin/settings` | server-wide storage defaults |
| GET | `/admin/logs?after=<seq>` | scrubbed in-memory server log |

Rails: the guest account is untouchable, no self-delete, the last admin
can't be demoted or deleted.
