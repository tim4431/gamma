# What Zotero has that Gamma does not

Surveyed October 2026 against Zotero 7 (August 2024), Zotero 8 (January
2026) and Zotero 9 (April 2026), plus the Better BibTeX plugin, which most
LaTeX users treat as part of Zotero. Gamma's side comes from the topic docs
and the code at the time of the survey, with pointers so a reader can check
whether a gap has since closed. The current mechanics live in
[paper metadata](../dev/paper_metadata.md),
[import and export](../dev/import_export.md),
[home library](../dev/home_library.md) and
[extension](../dev/extension.md); this note does not repeat them.

Gamma is not a reference manager, so the question is not "match Zotero"
but which pieces of Zotero a Gamma user misses when their library of
annotated papers also has to feed a paper they are writing. The gaps are
grouped by how often that happens, and each says what Gamma already has
nearby, since most are extensions of existing code rather than new systems.

## What Gamma already covers

- Metadata by arXiv id, DOI, ISBN, Crossref title search and verified AI
  extraction, cached on the page as `properties.meta` with a per-page
  BibTeX entry and a "Copy BibTeX" button in the Share popover
  (`backend/gamma/bibtex.py`, `build_entry`).
- A Zotero-Connector-style browser extension for arXiv, DOI, publisher
  pages and PDF tabs; PDF retrieval through `citation_pdf_url`, Unpaywall,
  publisher cookie sessions and a browser handoff.
- Nested folders (a page may be in several), flat labels, pins, full-text
  search over every PDF and note, a 30-day trash, storage quotas.
- Zotero RDF import (collections, tags, notes, embedded annotations) and
  Zotero RDF export; Obsidian, Logseq, Notion and Markdown imports.
- Shared workspaces with roles and live co-editing, share links, publishing,
  offline mirrors: more than Zotero group libraries offer.
- Highlights, area highlights, ink, text boxes, in-PDF citation links,
  page outline, inverted page colours, tabs.
- An AI `cite` tool that returns the stored records and BibTeX of up to 50
  pages and an MCP server over the library.

## Gaps, ranked

### 1. Bibliography output: batch BibTeX and a `.bib` file

**Built since this survey** — a `bibtex` export mode for a page or a folder,
unique keys, a pinned `cite_key`, and a share-link URL that always serves the
current `.bib`. See
[import_export.md](../dev/import_export.md#bibtex-bibliography). The "Copy
BibTeX" action on a multi-selection proposed below (and in the suggested
order) is still unbuilt: the library view has no such command. The rest of
this entry is the survey as written.

**Zotero.** Select any items, a collection or the whole library and export
to BibTeX, BibLaTeX, RIS, CSL JSON, EndNote XML, RDF, CSV, or drag items
into a text editor to paste formatted entries. Better BibTeX adds stable
citation keys generated from a pattern (default `auth.lower + shorttitle(3,3)
+ year`, with `a`/`b` suffixes on clashes), pinned keys, and *auto-export*: a
collection is linked to a `.bib` path and rewritten whenever its items
change, which is how Zotero libraries feed Overleaf and local LaTeX builds.

**Gamma.** BibTeX exists only per page: the Share popover's copy button, a
```` ```bibtex ```` fence inside each page of the Markdown and Obsidian
exports, and the AI `cite` tool, which is read by the model, not saved to a
file. Export modes (`routers/export.py`) are annotated PDF, notes PDF,
readable Markdown, Obsidian, Logseq, Zotero RDF and the Gamma zip; none is a
bibliography. Citation keys are first-author surname plus year with no
clash handling and no way to pin one, so two papers by the same author in
one year collide silently.

**Nearest fit.** A `bibtex` export mode over the existing page-or-folder
`ExportJob` that concatenates `properties.bibtex`, with keys made unique at
export time (append `a`, `b`, …) and a per-page override field for a
pinned key. A "Copy BibTeX" action on a multi-selection in the library view
covers the quick case without a file. Auto-export maps naturally onto a
share-link-like URL that always serves the folder's current `.bib`, which is
what Overleaf's "upload from URL" refresh consumes; that avoids running a
file watcher at all.

### 2. Formatted citations in a style (CSL)

**Zotero.** Any of ~10,000 CSL styles renders a bibliography or an in-text
citation; "Create Bibliography from Items" copies HTML or RTF; Quick Copy
puts a formatted citation on the clipboard on drag or Ctrl+Shift+C; and the
Word, LibreOffice and Google Docs plugins keep live citations in a document
(Zotero 8 unified the citation dialog; Zotero 9 inserts annotations with an
attached citation straight into the document).

**Gamma.** One AI-made "slide citation" per page, a prompt the user may edit,
and the `cite` tool's instruction to the model to "format other styles from
the fields". There is no deterministic renderer, so an APA reference list
for a folder is a chat request whose output is not reproducible.

**Nearest fit.** `citeproc-js` in the browser with a handful of bundled
styles (APA, IEEE, Nature, Chicago, Vancouver) over `properties.meta` would
give a "Copy citation as…" menu and a folder-level reference list without
an AI call. Gamma already consumes CSL JSON from doi.org, so the data shape
is close; the stored fields lack `issue`, `URL`, `abstract` and a full date,
which the metadata fetch would need to start keeping. Word-processor
plugins are a separate product and not worth building; a copy-as-RTF path
covers most of the value.

### 3. Add an item by identifier without a PDF

**Zotero.** The magic-wand button takes a DOI, ISBN, PMID, arXiv id or ADS
bibcode, one or many per line, and creates full items; the PDF is a
separate "Find Available PDF" step that can run later over any selection.

**Gamma.** The "+" box resolves a URL or identifier *to a PDF* and creates
nothing when no PDF is reachable. Metadata-only pages exist (Zotero import,
the extension's `web_url` fallback, the agent's `save_paper`), so the data
model allows them; only the entry path refuses. PMID is not among the
lookups, and there is no later "find the PDF" action on existing pages.

**Nearest fit.** Let the "+" box fall back to a metadata-only page with a
visible "PDF not found" state, and add a "Find PDF" action (single page and
batch from the Maintenance health table) that reruns the existing resolver.
PubMed's E-utilities would add PMID lookup to the registry chain.

### 4. Finding duplicates in an existing library

**Zotero.** A "Duplicate Items" view groups candidates by DOI, ISBN, title
plus creator plus year, and a merge keeps one master record while moving
notes, attachments, tags and collection membership onto it.

**Gamma.** Duplicates are prevented on ingest (DOI, arXiv id, URL hash, file
content hash in `routers/clip.py` `find_page`) but nothing finds them
afterwards: two uploads of different scans of the same paper, or a Zotero
import beside an earlier manual upload, remain two pages. There is no merge,
and a merge is harder here than in Zotero because each page carries a block
tree of notes and highlights.

**Nearest fit.** A Maintenance tab list keyed on the same identifiers plus a
normalized title match, offering "keep this one, move the other to trash";
moving the loser's notes under the winner is an `apply_ops` reparent and is
doable, merging highlights that point at a different PDF is not.

### 5. Saved searches and richer item types

**Zotero.** Saved searches are live collections built from an advanced-search
form with nested AND/OR groups over any field (Zotero 8 moved that form
above the item list and lets it filter any view). Thirty-odd item types
(journal article, book section, thesis, preprint, webpage, video, dataset,
software…) each with their own fields. The items list has user-chosen sort
columns (creator, year, publication, date added, Zotero 9 adds "added by").

**Gamma.** Search chips for folder and label are not saveable. `kind` is one
of seven values and only `book` changes anything. The list view has fixed
columns (kind, date, pin) and sorts by modified, added, viewed or title, not
by author, year or venue. There is no read/unread state.

**Nearest fit.** Saved searches are a stored query plus a pseudo-folder in
the sidebar, which the label view already demonstrates. Sort by author and
year needs nothing but reading `properties.meta` in the list view. More
item types are only worth it once a CSL renderer exists to show the
difference.

### 6. Web page snapshots and non-PDF reading

**Zotero.** The connector saves a full HTML snapshot (SingleFile) of any web
page; Zotero 7 made snapshots and EPUBs first-class in the reader with
highlights, underlines and notes; Zotero 8 added a reading mode that strips
page chrome and reader themes (Dark, Snow, Sepia); Zotero 9 added read-aloud.

**Gamma.** A clipped page without a PDF stores only its URL; a selection can
be clipped as a quote block. There is no snapshot, no HTML or EPUB reader,
and the only PDF theme is inverted colours. The upstream fork's "readable"
pipeline turns a PDF into Markdown, which is the opposite direction.

**Nearest fit.** The smallest useful step is Readability-extracted article
text stored as a note page at clip time, so that the content is searchable
and survives link rot; a true annotatable snapshot reader is a large
project and arguably outside Gamma's PDF focus.

### 7. Retraction notices

**Zotero.** Items with a DOI or PMID are checked against the Retraction Watch
database locally; retracted items get a red flag in the list and a warning
in the item pane and in the Word plugin.

**Gamma.** Nothing. Most pages carry a DOI, so the data to check is there.

**Nearest fit.** Crossref exposes retraction relations in its DOI metadata
and Retraction Watch's dataset is public; a nightly background task
(`docs/dev/tasks.md`) marking `properties.meta.retracted` and a notice on
the page card would cover it with no new UI surface.

### 8. Attachment and file conventions

**Zotero.** Several attachments per item (PDF, supplement, snapshot, link);
configurable filename templates applied continuously as metadata changes
(Zotero 8), with per-group rules (Zotero 9); a "Linked files" mode that
leaves PDFs where they are on disk.

**Gamma.** One PDF per page, by design (`docs/dev/block_centric.md` states a
multi-PDF page is not planned); supplementary files become file chips in
notes or their own page; stored files are content-hashed and only exports
take the title as filename. Page *titles* already follow metadata while
automatic.

**Nearest fit.** A filename template for exports only (`{author}{year} -
{title}.pdf`) is cheap and is what people miss when they pull PDFs out for
a colleague. The one-PDF rule is a settled decision and is not a gap to
revisit here.

### 9. Smaller items

- **Feeds.** Zotero subscribes to RSS and Atom feeds (journal tables of
  contents, arXiv listings) and lets you add an entry to the library in one
  click. Gamma has nothing; the agent's `search_papers` tool is pull-only.
- **Annotation tags and colours.** Zotero tags individual annotations and
  offers eight highlight colours plus underline and strikethrough as native
  tools. Gamma has four colours, page-level labels only, and underline or
  strikeout arrive only through PDF import.
- **Related items.** Zotero's manual "Related" field pairs items in both
  directions. Gamma's `[[links]]` and backlinks already serve this; only a
  dedicated field is missing.
- **Unfiled and Recently Read.** Zotero shows pages in no collection, and
  Zotero 9 added a synced Recently Read collection. Gamma's root lists
  everything and its Recently viewed strip is the equivalent of the latter;
  a "no folder" view is the missing half.
- **Timeline.** A date histogram of the library; rarely used, not worth
  copying.
- **Plugin system.** Zotero's add-on ecosystem (Better BibTeX, Zotfile
  successors, Zotero-GPT) is the source of much of its value. Gamma's
  equivalent extension points are the HTTP API, MCP and the agent tools;
  a plugin API for the UI is a different scale of commitment.

## Suggested order

1. Batch BibTeX: a `.bib` export mode over folders and multi-selections,
   unique keys, a pinned-key field, a stable URL for Overleaf refresh.
2. Add by identifier without a PDF, plus "Find PDF" on existing pages and
   PMID lookup.
3. CSL rendering for copy-as-citation and a folder reference list, which
   also motivates keeping `issue`, `URL`, `abstract` and full dates.
4. Duplicate finder in Maintenance.
5. Retraction check as a background task.
6. Saved searches and author/year sorting in the library view.

Items 1 to 3 are the ones that stop a Gamma user from leaving Zotero
installed beside it; the rest are quality-of-library work with no deadline.

## Sources

- [Zotero 7: Zotero, redesigned](https://www.zotero.org/blog/zotero-7/)
- [Zotero 8](https://www.zotero.org/blog/zotero-8/)
- [Zotero 9](https://www.zotero.org/blog/zotero-9/)
- [Zotero version history](https://www.zotero.org/support/changelog)
- [Collections and tags](https://www.zotero.org/support/collections_and_tags)
- [Retracted item notifications with Retraction Watch integration](https://www.zotero.org/blog/retracted-item-notifications/)
- [Better BibTeX: citation keys](https://retorque.re/zotero-better-bibtex/citing/)
- [Better BibTeX: automatic export](https://retorque.re/zotero-better-bibtex/exporting/auto/)
- [Updated reader with EPUB and snapshot support](https://forums.zotero.org/discussion/106716/available-for-beta-testing-updated-reader-with-epub-snapshot-support-and-new-annotation-types)
