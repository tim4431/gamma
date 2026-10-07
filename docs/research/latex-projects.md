# LaTeX projects: an Overleaf-like editor and compiler inside Gamma

Surveyed October 2026, before any code. The question: can Gamma hold a
LaTeX project — a file tree, a source editor with the note editor's LaTeX
aids, a compiled PDF beside it, SyncTeX both ways, and an AI chat that reads
and edits the sources — the way Overleaf does, within the page and block
model it already has? Status: **a plan; nothing is built.** The first part
records what Overleaf and its open-source relatives do and what the compile
runtimes weigh; the second part is the design proposed for Gamma, with the
decisions still open at the end.

## Part 1: what exists elsewhere

### Overleaf's editor (2025–2026)

The redesigned editor is two panes, source left and PDF right, with a
vertical rail on the far left (file tree, search, integrations, review,
chat) and a top bar of menus. The source pane has a Code / Visual toggle;
the PDF pane has **Recompile** (auto-compile on change, or manual) with a
dropdown for *Stop on first error* and *Fast (draft)*; the **logs and
errors** panel opens beside it; the **SyncTeX arrows** sit on the divider
between editor and PDF. Below the file tree is a **file outline** that jumps
section to section. Settings has Editor, Compiler and Appearance tabs:
compiler (pdfLaTeX, LaTeX, XeLaTeX, LuaLaTeX), TeX Live year per project,
main document, spell-check language, Code Check on/off, autocomplete on/off,
keybindings (none, Vim, Emacs), theme, font, line height.

Editing aids: completion of commands (a static list plus the project's own
`\newcommand`s), environments with auto-close, `\ref` from a continuous
scan of `\label`s, `\cite` from the project's `.bib` files, file names for
`\input` and `\includegraphics`, packages and classes, snippets; **Code
Check**, a live linter for mismatched `\begin`/`\end`, braces and math
delimiters; a math preview strip; Hunspell spell-check in a web worker;
word count through `texcount`. Shortcuts worth copying: Ctrl+Enter or
Ctrl+S compile, Ctrl+/ comment, Ctrl+Space open completion, Ctrl+Shift+L
go to line.

The editor is CodeMirror 6 (since late 2022; Ace retired June 2023) with
Overleaf's own Lezer grammar, published as `@overleaf/lezer-latex`
(AGPL-3.0). A third-party package built on it, `codemirror-lang-latex`
(TeXlyre, AGPL), adds highlighting, folding, bracket matching, completion,
environment closing, lint and hover. The PDF viewer is pdf.js, with zoom
presets, fit modes, presentation mode, and it keeps its scroll position
across recompiles.

Collaboration: link sharing with editor/reviewer/viewer roles, comments,
track changes (paid), chat, history with labels (24 hours free, full paid),
Git/GitHub/Dropbox sync (paid), Zotero/Mendeley bibliography import (paid),
templates. Limits (docs, October 2026): compile timeout **10 s free / 240 s
paid**, self-hosted default 180 s; 2000 files, 7 MB of editable text, 2 MB
per text file, 50 MB per upload.

AI (included in every plan since July 2026, OpenAI behind it): **Error
Assist** in the log panel — the request carries the full error, the
relevant source lines and the project's file names; the reply is an
explanation and a red/green diff; *Apply* edits the document and
recompiles. An **AI Assistant** chat sees the current document, caret and
selection and can search the project; it advises rather than edits. Plus
Writefull's rewrite tools, an equation and table generator from text or an
image, title/abstract generation, and natural language to LaTeX. CoCalc has
the same "Help me fix this" button on an error; OpenAI's Prism (the former
Crixet) and Octree edit the whole project with approval. The tool loop
these converge on: `list_files`, `read_file` with line numbers, a line-range
or search/replace patch applied atomically, `compile` returning the parsed
log as file:line:severity, a page picture, and a project search.

### How Overleaf compiles

CLSI (Common LaTeX Service Interface) is a plain REST service: `POST
/project/:id/compile` with `{options: {compiler, timeout, imageName, draft,
stopOnFirstError, syncType, syncState, …}, rootResourcePath, resources:
[{path, content} | {path, url, modified}]}`; it answers the output files
(`output.pdf`, `output.log`, `output.synctex.gz`, …) under
`/project/:id/build/:buildId/output/`. It runs

```sh
latexmk -cd -jobname=output -auxdir=$DIR -outdir=$DIR -synctex=1 \
        -interaction=batchmode -time -pdf|-xelatex|-lualatex|-pdfdvi \
        -f|-halt-on-error   # never -shell-escape
```

in a **per-project directory kept between compiles**, so latexmk reruns
only what changed; the client sends only changed documents when its hash
of the file list matches the server's (`syncType: incremental`), else a 409
makes it send everything. Outputs are copied under a build id (two builds
per project, 90 minutes), and the PDF is optionally cut into hashed object
ranges so pdf.js refetches only the bytes that changed. SyncTeX shells out
to the TeX Live `synctex` binary (`synctex view -i line:col:file -o pdf`,
`synctex edit -o page:x:y:pdf`) and parses its `Page:`/`h:`/`v:` and
`Input:`/`Line:` lines. Word count runs `texcount`.

Server Pro runs each compile in a **sibling Docker container** from a
`texlive-full:<year>` image: the compile directory mounted at `/compile`,
network disabled, all capabilities dropped, no new privileges, an optional
seccomp profile, a CPU ulimit of timeout + 5 s, killed at the timeout.
Community Edition has no sandbox: compiles run with the container's
privileges, which is why its docs call it fit for trusted users only. The
whole product is eleven Node services on MongoDB and Redis; the CE app image
is 0.9 GB compressed and ships only TeX Live *scheme-basic*; a full TeX Live
makes the community images about 5 GB compressed and 8.5 GB on disk. The
documented sizing is two cores and 3 GB for five concurrent users, LaTeX
being single-threaded; a 63-page XeLaTeX thesis compiles in about 9 s warm
and 28 s cold.

### Open-source relatives

| Project | What it is | State (Oct 2026) |
|---|---|---|
| Overleaf CE / Toolkit | the real thing minus comments, track changes, Git, sandboxed compiles, SSO | AGPL; Mongo + Redis + multi-GB TeX images; moderate to self-host |
| CoCalc | latexmk, forward/inverse SyncTeX, a Problems panel, AI "fix this" | not open source (MS-RSL) |
| TeXlyre | local-first CodeMirror 6 editor, Yjs over WebRTC, compiles **in the browser** (BusyTeX TL2026 with Biber in WASM, or SwiftLaTeX), SyncTeX, Typst | AGPL; new (2025), active |
| latex-on-http | `POST /builds/sync` with resources → PDF; pdflatex/xelatex/lualatex | AGPL; active; 3 GB image |
| latex-online | `GET /compile?…`, tarball POST, cached by hash | MIT; last push 2024; 2.9 GB image |
| Typst / typst.ts | a different language with a 45 MB compiler and a 21 MB WASM build | not LaTeX |

### Compile runtimes

| Runtime | Size | Engines, bibliography | Offline | Notes |
|---|---|---|---|---|
| `texlive/texlive` images (Island of TeX, weekly, amd64 + arm64) | minimal 355 MB, basic 389 MB, small 629 MB, medium 951 MB, **full 2.61 GB** compressed | all engines, latexmk, biber, synctex, texcount, fonts | yes | the straightforward sidecar base |
| TeX Live / MiKTeX / MacTeX already on the machine | 0 | whatever is installed; MiKTeX installs packages on the fly; MiKTeX's latexmk needs a separate Perl | yes | detected on `PATH`; this checkout has TeX Live 2024 with latexmk, synctex and biber |
| **tectonic** (Rust, MIT; 0.17 July 2026) | 10–22 MB binary | XeTeX only (no pdfTeX, no LuaTeX); BibTeX built in, **biber external**; `--synctex`; shell escape off, `--untrusted` mode | after the first compile (packages come from its TeX Live 2024 bundle on demand, cached; `--only-cached` afterwards) | not in Debian; download the release binary; no `synctex` CLI, so SyncTeX needs an in-house parser |
| TinyTeX (TeX Live subset with `tlmgr`) | 150–210 MB; 1.8 GB for scheme-full | all engines, latexmk, synctex; no biber/biblatex/tikz by default, installed on demand from CTAN | once installed | a runtime download, not something to ship in an installer |
| Browser WASM: texlyre-busytex (TL2026), SwiftLaTeX (TL2020, dormant since 2024), LibrePaper wasm-latex (Sept 2026) | 32 MB WASM + 90–400 MB of packages | pdf/xe/lualatex, bibtex, makeindex, Biber in WASM (texlyre) | after caching | AGPL on the maintained one; fonts and packages need their file server (or our own); no server CPU at all, so it is the only option a hosted container could offer without a compile service |
| External API (latex.ytotech.com, latexonline.cc, FormaTeX) | 0 | varies | no | sources leave the machine; rejected except as an explicit opt-in |

SyncTeX itself: `.synctex.gz` is a gzipped line-oriented record file (pages
→ boxes with position and size in scaled points, each tagged with an input
file and line). Parsers: the `synctex` CLI (TeX Live, TinyTeX; not
tectonic), LaTeX Workshop's built-in JS parser derived from `synctex-js`
(MIT, a few hundred lines, last touched 2021), a new Go port; no maintained
Python package. Porting synctex-js to Python, or running it in the browser
over the gunzipped file, is a day's work and frees the design from the CLI.

Language intelligence beyond completion: **texlab** (Rust LSP, 10 MB,
active) and **digestif** (Lua) give package-aware completion, ChkTeX
diagnostics and label/cite navigation over stdio; either could sit behind
the backend per project directory later. Not needed for the first version:
a regex scan of the project for labels, cite keys, `\newcommand`s and file
names covers what Overleaf's completion does.

## Part 2: what Gamma has, and the design proposed

### What is there already

- **The container.** A page is a root block; folders are nesting labels
  (a page may be filed in several, folders hold only folders, pages have no
  order within one), and a page carries at most one attachment, a PDF
  (`blocks_store.page_attachment`; `POST /pages/{id}/attachment` is 409 on
  a second). So Gamma's folders cannot be a project's file tree — which is
  Overleaf's own split: the project list is one thing, the file tree inside
  a project another.
- **Block text.** A block holds up to 200,000 characters (`ops.MAX_CONTENT`),
  is edited through `set` ops with `base` and merged three-way at apply
  time (`gamma/textmerge.py`, diff-match-patch), shows peers' carets, is
  indexed for search (first 20,000 characters), reaches the AI as notes,
  can be patched by `edit_block` and reverted (`ai_revert.py`), and travels
  with exports, backups, the mirror and the iPad replica. The `ink_url` +
  `base_props` merge is the precedent for a block whose content lives in a
  file.
- **Uploads.** `POST /upload-file` stores any non-executable file under its
  content hash (`storage.store_file`), already typed for `.tex` and `.bib`,
  quota-checked, served immutable from `/api/uploads/<hash>.<ext>`, and
  kept alive by one reference grammar (`storage.upload_refs`: a URL in a
  block's content *or properties*, or a page's `doc_id`), with a 30-day
  orphan sweep.
- **The viewer.** `PdfViewer` takes any URL; an upload URL gets the manifest,
  range transport and the IndexedDB cache, all keyed by URL. It needs a
  `PageToolsContext`, is mounted once by App, and nothing re-anchors
  highlights when a page's PDF changes.
- **Jobs.** `jobs.start(kind, …, key=, run=)` gives a worker thread,
  progress phases (`typesetting` already exists in `taskKinds.js`), Stop, a
  file artifact, two jobs per account and four per server, and the tray.
  The client learns of changes by polling every 1.5 s; there is no push
  for jobs — but every op applied to a page is broadcast on the page's
  socket, so a result written as an op reaches every open tab at once.
- **The editor.** CodeMirror 6 is mounted per block (`BlockCmEditor.jsx`)
  with only `@codemirror/state`, `view` and `commands` installed: no
  language mode, history, search or autocomplete package. The LaTeX pieces
  are pure and reusable — `latexInput.js` (dollar and bracket pairing),
  `latexCompletion.js` (catalog, ranking, snippets, Tab navigation),
  `latexLint.js` (KaTeX errors), and `LatexEditor.jsx`'s `MathLivePreview`
  and `LatexAcPopup` — except `findMathAtCursor`, which knows `$` spans
  only.
- **Runtimes.** No subprocess runs anywhere in `backend/gamma`. The Docker
  image is `python:3.12-slim` plus one font package, built for amd64 and
  arm64; the desktop app is a 50 MB PyInstaller freeze behind Electron; a
  hosted Pro container has 1.5 GB and two CPUs. Any TeX engine is new.

### 1. A project is a page; its files are blocks

A LaTeX project is a page with `properties.latex = {main: "main.tex",
engine: "pdflatex", build: {…}}`. Each file is a child block of a new kind
**`file`**: `properties.file = {path: "sections/intro.tex"}` with the text
in `content`, or `{path: "fig/plot.pdf", url: "/api/uploads/<hash>.pdf",
size}` for a binary. The tree the UI shows is the paths; `position` keeps
them sorted. The page's own notes stay what they are — the outliner skips
`file` blocks, the project view shows only them — so a paper's draft and
the notes about it share one page, one chat and one share link.

Why blocks and not uploads with a manifest: a text file as a block gets the
three-way merge, presence, undo, the search index, the AI's `edit_block`
and revert, exports, backups, the mirror and the replica without new code.
An upload-only project would need a second sync, history and permission
path for exactly the data people edit most. Why not a new table: the same
reason, plus the op log, the share reach and the trash all speak blocks.

What it costs: `kind` is a generated SQL column (`db.BLOCK_HOT_COLUMNS`)
mirrored by `blocks_store.block_kind`, so a new kind is a numbered
migration (drop the index, replace the virtual column, recreate it) and a
line in each; `SUMMARY_PROPS` and `pageKindLabel` learn the `latex`
property so the library card can say "LaTeX"; the outliner, the Markdown
and Obsidian exports, the replica's tree rules and the search snippets each
decide what a `file` block is to them (hidden, a file on disk, carried,
indexed). The 200k-character limit is fine for `.tex` (3,000–4,000 dense
lines) and wrong for a Zotero-sized `.bib`: a text file past the limit is
stored as an upload and shown read-only, or the limit is raised for `file`
blocks — a decision below.

### 2. The view

A `frontend/src/latex/` surface, loaded lazily when a page has
`properties.latex`, replaces the notes column and the viewer with three
resizable panels (`react-resizable-panels`, as the docks already use):

- **Files** (narrow, collapsible): the tree, new file/folder, upload (drag
  files onto it), rename, move, delete to the page's trash, "set as main";
  beneath it the **outline** of the main document (sections, scanned).
- **Source**: a whole-document CodeMirror with a file tab row; the
  **Notes** tab beside it opens the page's ordinary outliner, so notes
  about the paper are one click away.
- **PDF**: the existing `PdfViewer` on the last build, read-only (a stub
  `PageToolsContext`, no highlight tools), with **Compile** (Ctrl+Enter /
  Ctrl+S), auto-compile on save as a preference, an engine menu, and the
  **log panel**: parsed errors and warnings as file:line rows that jump to
  the source, the raw log below. A SyncTeX button on each side, as
  Overleaf's divider arrows.

The chat dock stays as it is on the right. On a phone the three panels are
tabs. Keyboard: a `latex.*` catalog in `appCommands.js` (compile, toggle
comment, go to line, show log, sync to PDF, sync to source) — the block
catalog's outliner keys do not apply inside a file. Vim and Emacs modes are
left out, as everywhere else in Gamma.

### 3. The editor

`latex/LatexSourceEditor.jsx` is one CodeMirror per open file, editing the
file block's `content` through the page's usual op path: a `set` with
`base`, coalesced 350 ms, merged on the server — exactly what a note block
does, so two people typing in different paragraphs of `intro.tex` already
merge, and the documented upgrade (CodeMirror's collab rebase on the open
block, [dev/collab.md](../dev/collab.md)) applies here first if it is ever
needed. `remoteCursorField` and `externalSync` from `BlockCmEditor.jsx`
carry over.

Packages to add: `@codemirror/language`, `@codemirror/autocomplete`,
`@codemirror/search`, `@codemirror/lint`, and `history()` from `commands`
(a file gets CodeMirror's own undo; the page's block history does not
model a 200k-character document well). Grammar: `codemirror-lang-latex`
(Overleaf-derived Lezer grammar; AGPL, which Gamma is too) gives folding,
environment closing and the structure the outline and Code Check-style
lint need; `@codemirror/legacy-modes/mode/stex` (MIT) is highlighting only.
The grammar's bundle size decides whether it rides in the lazy chunk.

What is reused as is: dollar and bracket pairing, `latexCompletionEdit`,
`mathTabJump`, `latexErrors` (KaTeX) for the math under the caret,
`MathLivePreview` (Overleaf's strip; ours is already docked the same way),
`LatexAcPopup`. What is new: a math-span finder that also knows `\[…\]`,
`\(…\)` and the `equation`/`align` family; a pure `latexProject.js` index
over all text files — `\label`s, cite keys from the project's `.bib`
blocks, `\newcommand`s, `\usepackage`s, file paths — recomputed debounced
on change and feeding completion sources for `\ref{`, `\cite{`, `\begin{`,
`\input{`, `\includegraphics{` and `\usepackage{`; a command catalog beyond
math (LaTeX Workshop's list is the usual source); and the outline. A
library tie-in that Overleaf cannot offer: `\cite{` also lists the
workspace's papers, and accepting one inserts its pinned `cite_key` and
appends the BibTeX record (`cite` / `bibtex.py`) to the project's `.bib`.

### 4. Compiling

`gamma/latex_build.py` owns one abstraction, `compile(project) → Build`,
with pluggable engines behind it:

1. **Materialise** the project into `<data>/workspaces/<ws>/latex/<page>/`
   — text files from blocks, binaries hard-linked or copied from uploads —
   writing only files whose hash changed, and keep the directory between
   builds so latexmk's incremental rebuild and the `.aux`/`.bbl` files
   survive (CLSI's biggest speed lever). Cap the directory and sweep idle
   ones like job files.
2. **Run** the engine with a hard timeout (default 180 s, a server setting),
   `-interaction=nonstopmode -file-line-error -synctex=1`, no shell
   escape, `openin_any=p openout_any=p` so TeX reads and writes nothing
   outside the directory, the process group killed at the timeout.
3. **Collect** `output.pdf`, `output.log`, `output.synctex.gz`; parse the
   log into `{file, line, severity, message, context}` (a port of LaTeX
   Workshop's or Overleaf's log parser, both small); store the PDF with
   `store_pdf` (so it has a manifest, range transport and an immutable,
   content-unique URL — the per-build URL the viewer's caches need), the
   log and the SyncTeX file with `store_file`.
4. **Record** the build as a `set` op on the page root:
   `properties.latex.build = {pdf_url, log_url, synctex_url, at, ok, errors,
   warnings, engine}`. Because it is an op, every open tab learns of it over
   the page socket at once, the mirror and backups carry it, and the
   reference grammar keeps the three files out of the orphan sweep.

The run is a job (`latex-build`, `key` = page id, phases `preparing`,
`typesetting`, `saving`), so it has Stop, the tray, the account and server
concurrency limits, and recovery after a restart. A compile asked for
while one runs queues one more, not one per keystroke.

Engines, in the order worth building:

| Engine | Detected by | Fits | Does not fit |
|---|---|---|---|
| **Local TeX** (`latexmk` + pdflatex/xelatex/lualatex, or the bare engines with a built-in three-pass fallback when latexmk's Perl is missing) | `PATH`, or `GAMMA_LATEX_BIN` / a server setting | bare-metal servers, the desktop app where TeX Live, MacTeX or MiKTeX is installed, this checkout today | a multi-user server with untrusted members (TeX is a program; the file-access settings above are a fence, not a sandbox) |
| **Sidecar** (`ghcr.io/…/gamma-tex`: `texlive/texlive:latest-medium` or `full` + a 100-line service with `POST /compile` taking a tarball and returning the three files, and `POST /synctex`) | `GAMMA_LATEX_URL` | Docker installs, the NAS; opt-in in `docker-compose.yml.example`; network off, read-only root, memory and pid limits in compose | the desktop app, hosted containers (another service to run per customer — a shared compile service is a Gamma Cloud question) |
| **tectonic** | a binary Gamma downloaded into the data directory on request ("Install a LaTeX engine", 20 MB) | the desktop app with no TeX installed; a Docker image that may reach the network at first compile | pdfTeX-only behaviour (microtype), LuaLaTeX, biblatex + biber, packages newer than the TL 2024 bundle, a strictly offline first run |
| Browser WASM | a lazy chunk | hosted containers without a compile service, a demo | AGPL isolation, 100–400 MB of downloads, fonts, speed on a thesis |

SyncTeX: with a local TeX or the sidecar, shell out to `synctex view` /
`synctex edit` as CLSI does; with tectonic, parse `output.synctex.gz` in
Python (the synctex-js port). Forward: the editor's file and line →
`{page, x, y, w, h}` → the viewer scrolls there and flashes a box drawn in
the page frame it already uses for highlights (`page_frame` /
`pdf_position`). Inverse: a double-click on the PDF page → `{file, line}`
→ the editor opens the file at the line. Both are
`POST /api/pages/{id}/latex/sync/{code,pdf}`.

### 5. The AI chat

A LaTeX page's *document part* (`page_report_section`) is the main file
with its `\input`/`\include`d files spliced in order, up to the usual
budget, instead of PDF text — the source is the better representation of
the paper — plus the file list and the last build's error count. The
composer's focus chip and `note_selections` already describe a block and a
character range, so "the selected lines of `intro.tex`" works without a new
request field.

Tools, following the shape every competitor converged on and Gamma's own
`edit_block`:

- `list_files(page_id)`; `read_file(page_id, path, from_line, to_line)` with
  line numbers; `create_file(page_id, path, content)`.
- `edit_file(page_id, path, find, replace)` and a line-range form — thin
  wrappers over `edit_block` on the file block, so the existing Allow / Ask
  / Off states, the approval card, `AgentChanges` and revert apply
  unchanged.
- `compile_latex(page_id)`: runs the build job and waits up to its timeout,
  returning the parsed errors with their context lines; `view_pdf_page`
  resolves the build's PDF, so the model can look at a typeset page.
- The `cite` tool gains "add to this project's bibliography".

Deleting files is never offered, like deleting anything else. The compile
tool wants its own permission state (it spends CPU and seconds), default
*Allow* in a page scope since the person asked for the fix — a decision
below. Over MCP, `list_files` and `read_file` join `READ_TOOLS`, so an
outside assistant (Claude Code, Cursor) can read a project through the
connector; editing stays inside Gamma, where it is reviewable.

The one piece of UI beyond the chat: **Fix with AI** on a log error row,
Overleaf's Error Assist shape — the error, the surrounding lines and the
file list go into the chat, the reply's edit arrives as a change card, and
*Apply* recompiles.

### 6. Import, export, the library

- Import a project zip (Overleaf's *Download source*, an arXiv e-print) into
  a LaTeX page, through the transfers dialog; drop files onto the tree.
- Export the sources as a zip, with or without the last PDF. The Gamma
  export, backups and the mirror carry file blocks because they are blocks.
- **Snapshot as PDF page**: turn a build into an ordinary PDF page beside
  the project, with a link back — the way to annotate a submitted version
  with highlights, since highlights on a changing build would drift.
- Later: a saved arXiv paper fetches its source into a sibling LaTeX page,
  so the AI reads the real equations rather than extracted text.

### 7. Deliberately not Overleaf

No Visual editor, track changes, Git bridge, templates gallery, Vim/Emacs,
chunked PDF deltas, or spell-check beyond the browser's. History is the
one gap that matters: the op log keeps 300 rows and 24 hours, so project
history needs its own design (each build could pin the hashes of the
sources it was made from, which is a version list for free; named
versions and diffs come after).

### 8. Phases

| Phase | Builds | Gate |
|---|---|---|
| 0 — spike (days) | the local engine behind a bare `POST .../latex/compile`, one `main.tex` block, a plain CodeMirror, the build in `PdfViewer`; measure a real paper's compile and the viewer's refresh | does the loop feel like Overleaf? |
| 1 — the project (weeks) | the `file` kind and migration, `properties.latex`, the view (tree, tabs, outline, log panel), the editor with grammar, completion sources and the reused math aids, the build job with the local engine and the sidecar image, SyncTeX both ways, settings (engine, main, auto-compile, timeout), i18n, tests, the dev doc | a two-person session on a real paper with figures and a `.bib` |
| 2 — AI and interchange (weeks) | the context section, the five tools, Fix with AI, zip import/export, Snapshot as PDF page, the library `\cite{`, tectonic download for the desktop | an error fixed from the chat end to end; an Overleaf zip round-trips |
| 3 — later | arXiv source fetch, versions, browser WASM for hosted containers, texlab, word count, templates | — |

### 9. Risks and open decisions

Risks: TeX on a shared server is code execution unless sandboxed (the
sidecar is the answer for untrusted members; the local engine should be an
admin's opt-in with the fence settings on); hosted containers have no TeX
and little room, so Gamma Cloud needs a compile service or the WASM path
before the feature means anything there; the per-block limit versus large
`.bib` files; the replica and the mirror meeting a kind they have not seen
(their tree rules must carry it through untouched); MiKTeX on Windows
installing packages at compile time with a prompt (set its auto-install
to yes for the child process); materialising a big project per compile
(solved by writing only changed hashes).

Decisions to take before Phase 1:

1. **Files as blocks with a new `file` kind** (recommended) or uploads plus
   a manifest.
2. **Oversized text files**: upload and read-only, or a higher
   `MAX_CONTENT` for `file` blocks.
3. **The preview is read-only** with Snapshot as PDF page for annotation
   (recommended), or the build becomes the page's attachment and
   highlights drift.
4. **Engines first**: local `PATH` + sidecar (recommended); tectonic in
   Phase 2 for the desktop; WASM only when hosted needs it.
5. **Grammar**: `codemirror-lang-latex` (AGPL, rich, recommended pending a
   bundle-size check) or the MIT `stex` mode.
6. **The compile tool's default permission** in the chat: Allow or Ask.
