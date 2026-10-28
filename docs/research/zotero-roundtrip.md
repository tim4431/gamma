# What the Zotero round trip loses, and what it costs (October 2026)

Gamma reads a Zotero RDF export and writes one, and the two are meant to be
each other's inverse. This note asks what a library loses on the way
Zotero → Gamma → Zotero and Gamma → Zotero → Gamma, where the formats stop
Zotero from reading what Gamma writes, and where the time goes. The
mechanics are in [dev/import_export.md](../dev/import_export.md#zotero-library-import)
and the annotation rules in [zotero-annotations.md](zotero-annotations.md);
this note does not repeat them. What was built from it, and how that
differs from what it suggests, is under [Outcome](#outcome-october-2026).

## How it was found

The code was read: `gamma/zotero_import.py`, `gamma/zotero_export.py`, the
Zotero sections of `routers/imports.py` and `routers/export.py`, the
annotation writer in `pdf_export.py` and reader in `routers/imports.py`,
`item_types.py`, `bibtex.py`'s name handling, `transfers/folderZip.js`, and
the tests. Four costs were timed with the backend's own Python on three
library PDFs and a synthetic 5,000-page database (the script is not kept;
the numbers are under "Speed"). The Zotero local API's documentation page
was read, and Zotero's own translators in the local Zotero data directory
(`Zotero RDF.js`, the export; `RDF.js`, the import) for what Zotero writes
and accepts; no running Zotero was driven. A second review of the same code
was checked claim by claim against it; what held is folded in below.

## What survives

Item keys (`rdf:about` kept as `properties.zotero_key`, Gamma's own as
`#gamma_page_<id>`), the collection tree, tags, the Better BibTeX citation
key, the nine item types, title, authors, year, venue, volume, pages, DOI,
ISBN, publisher, the arXiv id, the PDF and the other attachments, each
note's text, and every annotation kind Zotero has (through the PDF copies).
A Gamma export brought back into its own library adds nothing. The tests
cover each of these.

## What is lost

### Metadata

Both directions keep only what `properties.meta` has a field for; the
rest is read and dropped, or never written.

| Field | Zotero → Gamma | Gamma → Zotero |
|---|---|---|
| URL | read only to find an arXiv id, then dropped | never written; a clipped web page's `properties.web_url` stays home, so a `webpage` item arrives with no link |
| Date | the first four digits of `dc:date`; month and day go | the year alone |
| Creators | `bib:authors` only; editors, translators and contributors go, so an edited volume arrives with no names | one `foaf:Person` per author, split on the last space: "Jan van der Berg" becomes surname "Berg", "ATLAS Collaboration" surname "Collaboration". `bibtex.py` splits names the same way, so this is the stored name model, visible here |
| Abstract, issue, ISSN, journal abbreviation, language, short title, series, edition, place, number of pages, rights, access date, date added | dropped | not written (`meta` has no field; [zotero-features.md](zotero-features.md) already lists abstract, issue, URL and the full date as what a CSL renderer would need) |
| Extra | only the `Citation Key:` line | only the `Citation Key:` line; Zotero's own `arXiv:` and `PMID:` lines go |
| A preprint's registry identity | the DataCite DOI `10.48550/arXiv.<id>` is read back into `arxiv_id` | not written: no `dc:publisher` "arXiv", no DataCite DOI, no `z:archiveID` (the export translator writes every field it has no term for under the `z:` namespace), only the abstract URL, so Zotero's duplicate check and Better BibTeX see less than Zotero's own arXiv records carry |
| Attachment title | read, not stored | every PDF is titled "PDF" |
| Attachment path | `z:path` only; Zotero's reader also takes an `rdf:resource` element (`RDF.js`), so an export in that older shape loses every file here | `z:path`, as Zotero writes today |
| Automatic tags | kept as plain tags | written as plain tags |
| Date added | not in the export: the RDF translator writes no `dateAdded` (its `dcterms:dateSubmitted` is the access date, in the row above) | not written; Zotero stamps an imported item itself |

The cheapest way to make the metadata leg lossless without widening `meta`
is a pass-through: keep every simple predicate the import does not map, as
predicate → text, on the page (`properties.zotero_fields`), and write them
back on export. Zotero's importer reads what its exporter wrote, and ignores
nothing of its own. URL, full date and editors are worth a real field each.

### Notes

**A standalone note is dropped without a word.** `parse_zotero_rdf`
collects every `bib:Memo` but reads only the ones an item references
through `dcterms:isReferencedBy`; a note that stands in a collection on its
own (Zotero's reader adds those as standalone notes) makes nothing, and no
warning says so. Checked: a Memo listed by a collection and referenced by
no item parses to no item. It could become a note page filed in its
collections.

The import flattens a Zotero note into one block: `html_note_text` turns
`<p>` and `<li>` into lines of one content string, so a note with headings
and lists is a single block with `- ` lines. The export does the inverse
from a block tree, a `<p>` for the root and a nested `<ul>` for the
children. So Gamma → Zotero → Gamma turns a subtree of blocks into one block
holding list markup, and Zotero → Gamma → Zotero turns paragraphs into one
`<p>` with `<br/>`. A structural parse of the note HTML (block elements as
sibling blocks, nested lists as children) makes both legs exact and gives a
Zotero user real blocks.

Inside a note, `html_note_text` drops a link's target (its text stays),
drops `<img>` without a placeholder, loses heading levels, numbers an
ordered list as bullets, loses `<pre>` and `<code>`, runs a table's
cells together (`</tr>` ends a line, `<td>` adds nothing), and mangles a
nested list: `<li>outer<ul><li>inner</li></ul></li>` comes out as
`- outer- inner`, since only `</li>` ends a line. Gamma blocks already
hold `[text](url)`, so links are free to keep. The other way,
`_inline_html` knows `**bold**` and `*italic*` only: a block's `[text](url)`,
`## heading`, `- [ ] task`, `[[page ref]]`, inline code and `$math$` reach
Zotero as literal markdown. Gamma's own note images go out as `<img
src="data:…">` and as attachments, and come back into another library as a
chip under "Attachments", not in the note. Comment threads are in no export
to Zotero at all.

### Annotations

Beyond what [zotero-annotations.md](zotero-annotations.md) left open
(colours, annotation tags, underline, the turned text annotation):

- **Colour, round trip.** Zotero's `#ffd400` arrives at the importer's
  0.45 alpha (Zotero writes no `/CA`), goes out as `/CA 0.45`, and Zotero
  blends it 45% toward white. A Zotero user's eight colours come back washed
  out. The research note's first option fits: in the Zotero copies write no
  `/CA` on markup, as Zotero does, and keep Gamma's alpha in a private key
  that Gamma's own importer honours.
- **Identity.** `imported_annot` is a geometric key (page, subtype, rounded
  rectangle), so Zotero's `/Zotero:Key` and `/NM` are not stored, and the
  export mints a new key from the block id. Zotero receiving Gamma's copy of
  its own annotations sees them as external ones under new keys. Keeping the
  original key on the block and writing it back would let Zotero match
  them; whether its worker dedups by key is for a run against the worker,
  as that note did.
- **Author and date.** `/Zotero:AuthorName` and `/M` are not read; the
  export writes the exporting account as `/T` only, which Zotero's worker
  never reads, so every annotation from Gamma shows no author there.
  Writing `/Zotero:AuthorName` beside `/T` costs nothing elsewhere.

## Correctness

- **Two libraries' keys collide.** Zotero names an item by its URL, by
  `urn:isbn:<isbn>`, or else `#item_<itemID>`, a number local to one Zotero
  database (`Zotero RDF.js`). Two people's libraries imported into one
  workspace both hold `#item_42`; when the PDFs differ or one is missing,
  `_zotero_existing_page` falls through to the key and merges the second
  item into the first's page, carrying its notes, tags and folders onto the
  wrong paper. A numeric key should merge only when the title or the DOI
  agrees too.
- **Snapshots flood the review.** Zotero exports a web snapshot with its
  whole folder (`<title>_files/`, `.zotero-ft-cache`), and every file in it
  that no attachment names gets its own "File is not imported" warning.
  Files beside an attachment's own target want one line per folder, or
  none.
- **A stale hint.** The Zotero card still says "Additional PDFs become
  separate pages" (`transfers/importApi.js`, and the Chinese locale),
  shown under the card and in the review dialog; they have been chips under
  the page's Attachments block since the attachments change.

## Compatibility

- **The zip.** Zotero's wizard reads no zip, which the README and the step
  guide handle. A browser cannot download a folder, so a zip stays the only
  shape for the web app.
- **The connector server** (`/connector/import`, what the pipeline was
  verified against) takes an RDF body but not the files, which would go
  item by item through `saveAttachment`; and Zotero's developers say the
  connector server does not do CORS, so only the Gamma Connector extension
  or the desktop app could call it. Not worth it before the local API.
- **The local API** (Zotero 7 and later, `http://localhost:23119/api/`,
  user `0`, on under Settings → Advanced → "Allow other applications on
  this computer to communicate with Zotero"; 403 while off) is a local
  implementation of the Web API over the user's own database: items,
  collections, saved searches, an attachment's file as a `302` to a
  `file://` URL or `/file/view/url` as text, full text. The Web API lists
  notes and annotations among an attachment's children, as items with the
  annotation's type, colour, comment, tags, page index, rectangles, author
  and date; the local API's page says the Web API's endpoints work under
  the same prefix, which was not verified against a running Zotero here.
  An import from it would skip the export step (no PDF-worker wait, so the
  "export not finished" case disappears, no zipping and uploading of
  gigabytes) and read each annotation as data rather than back out of a
  PDF: exact colours, underline as itself, a note's colour, tags, keys. The
  server refuses a `Mozilla/` user agent without a `zotero-allowed-request:
  1` header, and whether a web page may call it (CORS) is an open question
  on the developers' list, so the natural first client is the desktop app
  or a tool run on the machine, with the browser keeping the zip. Reads are
  unauthenticated, so Gamma would never ask for the port to be exposed.
- **Writes.** The page says `POST`, `PUT`, `PATCH`, `DELETE` and file
  uploads work in Zotero 10 and later, behind a local API key the user
  grants in a dialog. That is a direct "send to Zotero": items, notes,
  collections, files, and annotations as items, with nothing embedded in a
  PDF and no README. Whether Zotero 10 has shipped was not checked.

## Speed

| Measured | Result |
|---|---|
| `zlib` level 6 on three library PDFs (26, 20, 19 MB) | 50 to 52 MB/s; the files shrink to 93.7%, 93.7% and 96.8% |
| `sha256` and `crc32` of the 26 MB file | 12 ms and 4 ms |
| PyPDF2 open and walk `/Annots`, 484 pages, 26 MB | 202 ms (2,939 entries, links among them); PyMuPDF's `annots()`, which skips links, 39 ms |
| `json_extract` lookup of `zotero_key` over 5,000 root pages with 50,000 blocks, parent index present | 3.5 ms each; 3 µs with an expression index on `json_extract(properties,'$.zotero_key')` |
| `build_rdf`, 3,000 items in 200 folders | 0.4 s |

What follows from it:

- **The export deflates PDFs.** `_write_zip` uses `ZIP_DEFLATED` for every
  entry, so a 2 GB library spends about 40 s of one core to save about 6%.
  Storing `.pdf` and image entries (`compress_type` per entry; the client's
  `folderZip.js` already stores) is a small change, and `_write_zip` is
  shared by every zip export.
- **The import reads each PDF three times.** `plan_zotero_archive` inflates
  every PDF for its digest in the preview, whole into memory
  (`zf.read`), again in the commit (the staged review replans the same
  file), and `store_pdf_stream` hashes it a third time while copying.
  Keeping the plan with the staged upload (it is JSON) removes a pass, and
  hashing in chunks keeps a 500 MB scan out of memory.
- **An unannotated PDF is copied twice on export.** `add_page` reads the
  stored file, `_annotated_copy` hands the bytes back unchanged when there
  is nothing to burn in, and `spool` writes them to a temporary file that
  `_write_zip` reads again. A page with nothing to draw could name the
  stored file in `files` directly, as the image attachments do.
- **The key lookup is a scan.** `_zotero_existing_page` runs the
  `zotero_key` query for every item and again under the write lock for every
  new one: about 35 s of a 5,000-item import into a 5,000-page library. The
  digest lookup is indexed (`idx_ub_doc`); the key wants the same.
- Checked and fine: `build_rdf`'s collection loop, and PyPDF2's scan of a
  PDF for annotations.
- Zotero's own export, which re-embeds annotations through its PDF worker
  one file at a time, is the slow half of the whole import and outside
  Gamma; the local API avoids it.

## Suggested order

1. What loses or misplaces data without a word: the numeric-key merge
   guard, standalone notes as pages, the `rdf:resource` path shape.
2. The speed items: stored PDFs in the zip, the plan kept with the staged
   review, the unannotated PDF named rather than spooled, `zotero_key` as
   a generated column with an index as `doc_id` has.
3. The metadata leg: URL, full date and editors as fields, the
   pass-through for the rest, the preprint's DataCite DOI, repository and
   archive id, links kept in notes both ways, a placeholder for a note
   image, `/Zotero:AuthorName`.
4. Notes as block trees on import, the nested-list fix with them.
5. The Zotero copies without `/CA` on markup, the alpha in a private key;
   Zotero's annotation key kept and written back, checked against the
   worker.
6. The snapshot warning fold and the stale hint.
7. An import from the local API in the desktop app, and the write path
   once Zotero 10 is common.

## Outcome (October 2026)

Items 1 to 6 were built on the `zotero-import-export-upgrade` branch;
[import_export.md](../dev/import_export.md) ("Zotero library import",
"Zotero RDF export") describes the result. Where it differs from the order
above:

- **The metadata leg** went into one pass-through, `properties.zotero_rdf`,
  instead of new `meta` fields. The full date, editors, URL, abstract, a
  publisher's place and the rest travel with the page, and go back to
  Zotero while the page's record is still the same work. `meta`, BibTeX
  and the citation formats read nothing new.
- **The key column** is an expression index (`idx_ub_zotero_key`), not a
  generated column: one reader, and no migration step.
- **The key guard** compares an item with the page's record and with the
  Zotero record the page was imported with, so a record edited in Gamma
  still merges. An `#item_<n>` item with no DOI that was retitled in Zotero
  comes back as a second page once its PDF's bytes change.
- **Zotero's annotation key** is kept on import (`zotero_annotation`) but
  not written back. A copy carrying a key Zotero knows would let Zotero
  re-parent its own annotation to Gamma's copy.
- **Notes** became a converter of their own (`gamma/zotero_notes.py`).
  From Gamma it is exact: a tree goes to Zotero and comes back unchanged,
  checked over 50,000 generated notes. From Zotero, a note's first block
  stays its own, and each later paragraph or heading goes back to Zotero
  as an item of one list, as the page shows it. Note pictures are stored
  with their note instead of as a chip.
- **A preprint** goes to Zotero with its archive id and repository but no
  DataCite DOI. The import reads a DOI into the record, so one written for
  a preprint without one would come back as the record's DOI.
- **The round trip is a test:** `tests/test_zotero_roundtrip.py` takes one
  library through import, export and a second library's import, and gets
  the same records, pass-through, notes, filing and colours back.

Left out: item 7. The `zotero-cloud` branch builds the Web API import and
write path, which cover what the local API would. Still open: none of the
new shapes was checked in a running Zotero (`z:archiveID` and publisher
`arXiv` on a preprint, a standalone `bib:Memo` with tags and collections,
surname-only creators, a highlight without `/CA`, `/Zotero:AuthorName`); a
note page retitled in Gamma no longer matches its Zotero note and is made
again; a capitalised particle ("Augustus De Morgan") splits as surname
"Morgan".

## Sources

- [Zotero Local API](https://www.zotero.org/support/dev/web_api/v3/local_api)
- [403 on local API with "Allow other applications..." enabled](https://groups.google.com/g/zotero-dev/c/5KM1QVUOeck) (the user-agent rule)
- [CORS issue on zotero dev:client](https://groups.google.com/g/zotero-dev/c/iyQzPBrJTJM) (the connector server and CORS)
