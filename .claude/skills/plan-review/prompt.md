You are reviewing an implementation plan for Gamma, a self-hosted PDF
annotation and nested-notes app: a FastAPI/SQLite backend in backend/, a
React/Vite frontend in frontend/, Electron in desktop/, native iPad and
Android shells over the web app in ipad/ and android/, a browser extension
in extension/, and the Gamma Cloud account server in cloud/. The plan is in
the <stdin> block below. The repository is the working directory. Before
judging the plan, read docs/dev/README.md (architecture and shared rules),
then the topic docs and source files the plan names.

Find the holes: ways the plan, built as written, would lose or corrupt
data, break for some user, device or client, or fail under concurrency. Be
exhaustive. A hole left out costs more than a finding that is declined.
Look in particular at:

- stored data: migrations (backend/gamma/migrations.py), rows written
  before the change, data in mirrors, synced vaults and Zotero links;
- released clients: the desktop, iPad and Android apps and the extension
  run older code against a newer server, and a newer client meets an
  older server;
- authorization: workspace roles, share links, guests, integration tokens
  (backend/gamma/auth.py and the routers);
- concurrency: two people on one page, two tabs, a background job beside a
  request, a check followed by a write without a lock or a constraint;
- collaboration and undo: apply_ops / commit_ops, other people's edits
  arriving during an undo, a page socket that reconnects;
- every surface the change reaches: mouse, touch and pen; the iPad and
  Android shells; the themes; the i18n catalogs;
- existing helpers, widgets and modules that the plan re-implements
  instead of reusing.

Rules:

- Read only. Do not edit files, start the app, run the tests, or import
  backend code (importing it migrates the data directory).
- Each finding names a concrete failure: the inputs or state, the step of
  the plan, and the wrong result. Cite the code that makes it possible as
  path:line. A concern you cannot tie to the code goes under "Unverified".
- Prefer a change to the plan's design over a guard added around it.
- No praise and no summary of the plan.

Answer in Markdown, in this shape:

## Findings
1. **[high|medium|low] One-line title.** Failure: state or inputs → wrong
   result. Plan step: which. Evidence: path:line, … Change: the smallest
   change to the plan that removes it.

## Reuse
- What the plan builds → the existing path that already does it, and how.

## Unverified
- Concerns you could not tie to the code, each in one line.
