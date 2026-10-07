# Research notes

Findings, not tasks: how other products solve a problem, what was learned
from Gamma's own code before a redesign, and the reasoning behind the
design that was picked. Read these when a similar decision comes up again;
the current mechanics live in [docs/dev/](../dev/), which these notes never
duplicate.

The last column says what a note still leaves open, checked against the code
in October 2026. "Nothing" means the note records a decision that is built;
the dev doc named there describes how it works now.

## Reading, PDFs and handwriting

| Note | Question it answers | Still open |
|---|---|---|
| [pdf-loading.md](pdf-loading.md) | Where a cold PDF open spends its time, the five moves the upstream fork made (range transport, server page sizes, server previews, keeping parsed documents, MRC flattening) with their measurements, and which of them fit Gamma. | Nothing; server previews and MRC flattening were declined. Built: [dev/pdf_loading.md](../dev/pdf_loading.md). |
| [pdf-zoom-rendering.md](pdf-zoom-rendering.md) | Why a zoomed page goes soft, what pdf.js's own viewer does about it, what a raised cap, tiles, a detail canvas and a pdf.js upgrade each cost as measured, and the design recommended. | The design is built, with differences listed in its "As built" section: [dev/pdf_loading.md](../dev/pdf_loading.md#high-zoom-and-touch-scrolling). Open: iPad and WebKit timings, a zoom commit across a long document, a render queue, very long documents and live ink at the 800% limit, pdf.js 6.x. |
| [handwriting.md](handwriting.md) | Which ink formats exist (InkML, Xournal++, PDF `/Ink`, tldraw, Excalidraw, reMarkable, PencilKit) and which are worth speaking, what stylus input each platform gives the browser, and how Notability's pen, highlighter, eraser and lasso are set up. | Dashed and dotted pens, the highlighter drawn behind ink, the eraser returning to the last tool, draw-and-hold shapes, preset reorder and sync, a `.xopp` importer. |
| [handwriting-interactions.md](handwriting-interactions.md) | How Goodnotes and Notability select, edit, transform and reuse ink; finger taps versus holds; contextual menus; the wider writing and study feature inventory. | Priorities 1, 2 and 4 are built ([dev/handwriting.md](../dev/handwriting.md)). Open: priority 3 and the long tail of the inventory (copy and paste of ink, shapes, ruler, layers, locks, recognition, audio). |
| [touch.md](touch.md) | What the web app can and cannot do on an iPad with fingers and a Pencil and no keyboard, and where a mouse interaction is unreliable or loses state under a finger: what was measured in touch emulation and the platform facts behind it. | The editing bar closed the typing gaps ([dev/ipad.md](../dev/ipad.md)); one code path for mouse and finger closed the menus, hover-only tools, sizes (one set for every device, the owner's choice over coarse-pointer sizes), typing aids, the dock splitters and the notebook view's place ([dev/ui-design.md](../dev/ui-design.md#one-behaviour-for-mouse-and-finger)). Open: one host per window (rotation still remounts Notes and the topbar popovers), the visible viewport for the chat composer and dialogs, the Text tool and finger-draw swipe trap, a second passage without Ctrl, the `[[` picker, undo reach for other deletes, ink drafts across a killed tab, every device check. |
| [ipad.md](ipad.md) | Native app or installed web app for the iPad, and why PencilKit's drawing format was rejected. | Nothing; the installed web app came first and the native app followed. Built: [dev/ipad.md](../dev/ipad.md). |

## Notes and the editor

| Note | Question it answers | Still open |
|---|---|---|
| [collaboration.md](collaboration.md) | How real-time collaborative editing is built elsewhere (OT, record-level last-writer-wins, CRDTs), why a snapshot autosave cannot collaborate, and why Gamma took the Notion / Linear / Figma shape. | Nothing. Built, and since extended with a three-way text merge: [dev/collab.md](../dev/collab.md). |
| [latex-editing.md](latex-editing.md) | How LaTeX Workshop, Obsidian's LaTeX Suite and Overleaf complete `\commands` and place their equation previews, and which of those Gamma took. | Nothing. Built: [dev/latex_editing.md](../dev/latex_editing.md). |
| [latex-projects.md](latex-projects.md) | What an Overleaf-like LaTeX project inside Gamma would take — a page whose files are blocks, a whole-document editor reusing the math aids, a build job with pluggable engines, SyncTeX, and the AI's file tools — beside what Overleaf and its open-source relatives do and what each compile runtime weighs. | Everything: a plan, nothing is built; the decisions it leaves to take are listed at its end. |
| [keyboard-shortcuts.md](keyboard-shortcuts.md) | What VS Code, Obsidian, Logseq and Notion bind for line and block operations, which chords a browser keeps for itself, and why Gamma treats the block as the line and reads physical keys. | Nothing; multi-key chords, multi-cursor and palette prefixes were left out on purpose. Built: [dev/hotkeys.md](../dev/hotkeys.md). |

## AI

| Note | Question it answers | Still open |
|---|---|---|
| [ai-context.md](ai-context.md) | What one chat request carries and why it cost what it did, and the ordered list of changes: caching breakpoints, a stable document turn, in-turn elision, pre-flight trimming, compaction. | Steps 7 and 9, most of 11, and part of 12; compaction is not built. Built steps: [dev/ai.md](../dev/ai.md), [dev/ai_context.md](../dev/ai_context.md). |
| [open-weight-providers.md](open-weight-providers.md) | How Chinese and open-weight models (DeepSeek, Kimi, Qwen, GLM, MiniMax, Doubao, local servers) behave on the `openai` and `anthropic` wires, and the ranked gaps found. | Reasoning passthrough, the base-URL path and picture gating are closed. Open: inline `<think>`, Qwen3's non-streaming refusal, thinking on the Anthropic wire, vendor thinking switches. |
| [ai-permissions.md](ai-permissions.md) | How Claude Code and Codex ask before an agent acts, and which of those Gamma's Allow / Ask / Off permissions and approval card took. | Nothing. Built: [dev/ai.md](../dev/ai.md#asking-before-a-call-approvals). |
| [paper-fetching.md](paper-fetching.md) | What real paper questions revealed about search, reference following, publisher downloads and blocked access; the browser handoff that followed; where a real reply's tokens went. | The live-service checks listed under "Still unverified". Built: [dev/ai_tools.md](../dev/ai_tools.md). |

## Library, import and export

| Note | Question it answers | Still open |
|---|---|---|
| [obsidian.md](obsidian.md) | What an Obsidian vault is, how the official Importer maps Notion and Logseq into it, and the mapping Gamma's vault import and export chose. | Nothing. Built: [dev/import_export.md](../dev/import_export.md). |
| [zotero-features.md](zotero-features.md) | What Zotero 7 to 9 and Better BibTeX do that Gamma does not, what Gamma has beside each, and the order worth building them in. | The BibTeX bibliography is built. Open backlog: multi-select copy, CSL styles, add by identifier without a PDF, a duplicate finder, saved searches, snapshots, retraction flags, feeds. |
| [upstream-features.md](upstream-features.md) | What the upstream fork (`amogadget/Gamma`) built that this one had not as of September 2026, ranked. | The iPad app and offline use were built differently here. Open: audio-synced replay and recording blocks, model-catalog refresh, server previews, MRC flattening. |

## Data model, scaling and hosting

| Note | Question it answers | Still open |
|---|---|---|
| [workspaces.md](workspaces.md) | What happens when identity and data location are one string, what a workspace model needs, and how to version a data directory so upgrades stay safe. | Nothing. Built: [dev/workspaces.md](../dev/workspaces.md), [dev/migrations.md](../dev/migrations.md). |
| [data-model.md](data-model.md) | What a clean-slate redesign of the stored shapes would change, ranked, and what was considered and left alone. | Nothing; schema steps 25 to 34 made the changes, with the differences listed in the note. Built: [dev/user_db.md](../dev/user_db.md). |
| [scaling.md](scaling.md) | Where the design stopped scaling, the Postgres shape and the SQLite-with-workspace-affinity shape that was chosen, and the ranked work list. | Items 1 to 12 are done (item 8, the blob seam, was built and then taken out again). Parked until a second node: LiteFS, placement, a router and worker processes. |
| [bundle.md](bundle.md) | What the frontend's one startup chunk held, what each first screen downloaded, and what splitting by surface reached. | A dated measurement. The rest of the saving needs the page surface out of `App.jsx` ([dev/frontend-refactor.md](../dev/frontend-refactor.md)). |
| [performance.md](performance.md) | Where speed and memory still went after the PDF-loading work: a survey of the client (the editor's keystroke, a long reading session, zoom and scroll, search, the disk cache) and the server (the chat's extraction, the subtree read, the missing index, the event loop, whole files in memory, the walks over every workspace), ranked, with what was built from it. | App's own render per keystroke (the state owners of [dev/frontend-refactor.md](../dev/frontend-refactor.md)), zoom as a CSS variable, the annotated-PDF export as a job, the startup walk and the upload GC over every workspace, Pillow in the image. |
| [hosting.md](hosting.md) | What running Gamma as a multi-tenant service with open registration would take, and where the code assumes one machine. | The sharded shape was not built; Gamma Cloud's hosted containers took its place. Open on a Gamma server: a real default quota, a report path for public shares, terms of service, self-service deletion, shipping logs out. |
| [cloud-plans.md](cloud-plans.md) | What the Free, Lite, Plus and Pro plans contain and cost beside Zotero's and Obsidian Sync's price lists, and how a paid container learns and enforces its owner's limits. | The design is built, with differences listed in its status paragraph. Not built: snapshot jobs, the `sync` fleet job, a notice to every hosted admin. Built: [dev/billing.md](../dev/billing.md), [dev/hosted.md](../dev/hosted.md). |
| [cloud-operations.md](cloud-operations.md) | What the operator of Gamma Cloud can and cannot do from the Admin page: holding a plan back, invites, what a payment's start and end set off, running and updating the fleet, configuring a customer's container, and what a running server reports. | Work items 1 to 8 are built ([dev/hosted.md](../dev/hosted.md), [dev/billing.md](../dev/billing.md), [dev/cloud_accounts.md](../dev/cloud_accounts.md)). Open: a `move` job between hosts and the shared server reporting usage per account. |

## Onboarding, website and demos

| Note | Question it answers | Still open |
|---|---|---|
| [onboarding.md](onboarding.md) | How VS Code walkthroughs, Notion, Linear, Figma, Logseq, Readwise Reader and the tour libraries onboard a first user, why tours rot, and which pieces Gamma's guide took. | An anchor inspector and a synced onboarding preference; the checklist was declined. Built: [dev/onboarding.md](../dev/onboarding.md). |
| [website.md](website.md) | What thirteen note and reading apps put on their front page, which patterns recur, and the section order and tone gammapdf.com took from them. | Nothing. Mechanics: [sites/README.md](../../sites/README.md). |
| [demo-production.md](demo-production.md) | How scripted demos and screen-recording editors achieve smooth motion, readable framing and repeatable exports; animated WebP delivery measured on Gamma's demos. | Nothing. Rules: [tools/readme-media/README.md](../../tools/readme-media/README.md). |

Conventions: one file per topic in kebab-case, dated where the survey has a
shelf life, written after the fact from what was actually found. When a gap
a note lists is closed, mark it there with one clause and describe the
mechanics in the dev doc. Add a row here for every new note, and update its
last column when the status changes.
