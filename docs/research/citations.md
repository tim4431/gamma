# Citations: what Zotero does and what Gamma took

Surveyed in November 2026 against Zotero 7 to 10 (10.0 shipped on 17
August 2026), Better BibTeX 9.0.77, citeproc-js, citation-js, citeproc-py
and the CSL style and locale repositories. Zotero's behaviour comes from its
source on GitHub (`defaults/preferences/zotero.js`, the `.ftl` locale files,
`xpcom/cite.js`, `xpcom/quickCopy.js`, `xpcom/style.js`, the translators
repository) as well as its documentation, which in places describes the
dialogs Zotero 8 replaced. The mechanics as built live in
[dev/citations.md](../dev/citations.md); the earlier backlog is
[zotero-features.md](zotero-features.md) §2.

Gamma had, before this: a BibTeX entry per page, a `.bib` export of a page
or a folder with unique keys and pinned keys, a share link that serves the
current `.bib`, and an AI-made slide citation. Nothing rendered a style.

## What a Zotero user does with citations

Ranked by how often a researcher writing papers meets each.

1. **The citation dialog in a word processor.** Zotero 8 (January 2026)
   replaced the red Quick Format bar and the classic dialog with one dialog
   in List and Library modes, searching by title, creator, year, DOI or
   ISBN, and since 9.0 by citation key. Each item in a citation takes a
   locator (27 types, page by default, typed inline as `p. 34` or `:45-48`),
   a prefix, a suffix and Omit Author; several items form one citation,
   kept sorted when the style sorts. Document preferences hold the style,
   the language, footnotes or endnotes for a note style, and the MEDLINE
   journal abbreviations, which apply in documents only. Zotero 9 added Add
   Annotation, which inserts an annotation with its citation; Zotero 10 a
   citation preview pane.
2. **Quick Copy.** Copy Bibliography (Ctrl/Cmd+Shift+C) and Copy Citation
   (Ctrl/Cmd+Shift+A) from any selection, and drag-and-drop into any text
   field (Shift at the start of a drag drops citations). The item format is
   any installed style or any export translator (BibTeX, RIS, CSL JSON…),
   with its own language and per-site overrides. The clipboard always gets
   `text/html` and `text/plain`, never RTF; the HTML carries inline CSS for
   the hanging indent, line spacing and second-field alignment so Word and
   Google Docs keep it. A numeric style numbers the items in the order the
   items list gives them. The default item format is Chicago shortened
   notes.
3. **The Bibliography Entry header** (Zotero 7): the selected item's
   reference in a style (APA by default) above its fields.
4. **Citations from annotations.** Notes made from annotations carry a
   citation per annotation (note templates `{{highlight}} {{citation}}
   {{comment}}`); selecting an annotation and pressing Ctrl/Cmd+C copies its
   text with a basic author-date citation, whatever the Quick Copy style.
5. **Create Bibliography** from items or from a collection: style,
   language, Citations or Bibliography, and Save as RTF, Save as HTML, Copy
   to Clipboard or Print. The HTML file embeds COinS; the RTF uses a
   720-twip hanging indent.
6. **Settings → Cite**: the styles installed (15 bundled), Get Additional
   Styles (the repository at zotero.org/styles, "over 10,000"), Add from
   File, the style editor and preview, and "Include URLs of paper articles
   in references", off by default: a journal, magazine or newspaper article
   with a page range then loses its URL and access date.

The 15 bundled styles (`zotero/bundled-styles`, October 2026): American
Chemical Society, American Medical Association, American Political Science
Association, American Sociological Association, APA 7th, Chicago
author-date, Chicago notes-bibliography, Chicago shortened notes, Elsevier
Harvard, Harvard Cite Them Right, IEEE, MHRA notes, MLA 9th, Nature, NLM
citation-sequence (which replaced "Vancouver" in Zotero 8.0.1).

## Formats

- **Export translators**: BibTeX, BibLaTeX and RIS are the common ones, CSL
  JSON for Pandoc and Quarto, then Zotero RDF, EndNote XML, MODS, CSV,
  Refer/BibIX, TEI, Wikipedia templates, COinS, CFF and others. Imports
  take BibTeX, BibLaTeX, RIS, CSL JSON, MODS, EndNote XML, Citavi, MARC,
  PubMed and more.
- **RIS** (`RIS.js`): journal article JOUR, book BOOK, chapter CHAP,
  conference paper CONF, thesis THES, report RPRT, presentation SLIDE,
  document GEN; a preprint has no mapping and falls to GEN. Authors are
  `AU  - Last, First`; `PY` the year, `DA` the date as `YYYY/MM/DD/`; `T2`
  the container, `J2` the journal abbreviation; `SN` the ISSN or ISBN.
- **BibLaTeX against BibTeX**: `@thesis` with `type`, `@report`, `@online`;
  `date=` in ISO, `journaltitle`, `eprinttype`/`eprint`. Zotero's own BibTeX
  and BibLaTeX translators write a preprint as `@misc` with no eprint
  fields; Better BibTeX does better.
- **CSL JSON**: `id` is the citation key when there is one; types
  article-journal, paper-conference, chapter, book, thesis, report, speech,
  document, and `article` for a preprint; names as `{family, given}` or
  `{literal}`; dates as `date-parts`.
- **Better BibTeX**: keys by formula (`auth.lower + shorttitle(3,3) +
  year`, clashes suffixed a, b, …), and since Zotero 7.0.31 kept in
  Zotero's own Citation Key field, always pinned; Quick Copy modes for
  `\cite{}`, Pandoc `[@key]`, org-cite and others; automatic export that
  keeps a `.bib` rewritten; a pull export URL and a cite-as-you-write
  endpoint with locators, prefixes and suffixes. It advises CSL JSON over
  BibTeX for Pandoc, since the conversion recases titles.

## Rendering engines

- **citeproc-js** (npm `citeproc` 2.4.63, published April 2023; upstream
  commits continue, the latest in November 2026). CPAL 1.0 or AGPL 3.0 or
  later, so it fits Gamma's AGPL. One CommonJS file, 975 KB unpacked, 381
  KB minified, 97 KB gzipped. `CSL.Engine(sys, style, lang, forceLang)`
  with a synchronous `retrieveLocale`; `updateItems`, `makeBibliography`
  (entries plus the layout parameters the caller turns into HTML or RTF),
  `previewCitationCluster` for a citation that leaves no trace. Zotero
  vendors its own copy and builds the presentation in `cite.js`; its
  `web-common/cite` module (AGPL) is the same layer as an ES module, used
  by ZoteroBib.
- **citation-js** (MIT, 0.9.0): wraps citeproc-js and adds BibTeX and RIS
  parsing, 174 KB gzipped with those plugins. Worth it only for parsing
  files in the browser.
- **citeproc-py** (BSD, 0.12.3, needs lxml): about 60% of the CSL test
  suite; no disambiguation, year suffixes or collapsing, so author-date
  lists come out wrong. **citeproc-js-server** is a Node service; Gamma's
  iPad and Android servers have no Node. **citeproc-rs** is archived.
- **Styles**: 2,865 independent and 8,004 dependent styles, CC BY-SA 3.0;
  software that uses them must credit the CSL project with a link to
  citationstyles.org. A dependent style has no layout and names its parent
  in `<link rel="independent-parent" href="http://www.zotero.org/styles/…">`.
  `https://www.zotero.org/styles/<id>` serves a style (and redirects a
  renamed one: `vancouver` → `nlm-citation-sequence`);
  `https://www.zotero.org/styles-files/styles.json` lists all of them
  (3 MB, title, id, class, dependent). Both answer CORS with `*`.
- **Sizes**: APA 86 KB, Chicago notes-bibliography 243 KB, IEEE 18 KB,
  Nature 6 KB; the 15 bundled together 959 KB; the 63 locales 1.8 MB, a
  locale 18–33 KB.
- **Clipboard**: the async clipboard takes `text/plain` and `text/html`
  only (no RTF), in a secure context; Safari and WKWebView need the write
  started inside the gesture, which a `ClipboardItem` of promised blobs
  allows. Electron could add RTF from the main process.

## What Gamma took, and why

- **One renderer, citeproc-js in the browser**, lazy-loaded. Its output is
  Zotero's; a Python renderer would disagree with it on every author-date
  list, and a Node one cannot run on the mobile servers. The server keeps
  the data side: one mapping from a page's record to CSL JSON, RIS and
  BibLaTeX files beside BibTeX, all through the same unique keys, so share
  links and `curl` keep working.
- **Zotero's 15 bundled styles plus American Physical Society** (Gamma's
  library is heavy in physics), and every CSL locale as its own lazy chunk:
  a style added from the repository may name any locale, and citeproc asks
  for it synchronously.
- **Styles added per account** (repository or file), kept in users.db with
  a cap, not in a shared directory: a shared store had no owner, no quota
  and no backup, and a style list in the synced profile could be lost to
  two tabs writing it at once.
- **Quick Copy** as Zotero has it: a default format (a style, BibTeX,
  BibLaTeX, RIS, CSL JSON or a key command), Copy citation and Copy
  bibliography on any selection, a Copy as menu, and Zotero's shortcuts on
  the desktop app only (both are browser shortcuts in Chrome).
- **Copy with citation** on a highlight and a selection, with the page
  label as locator, in the default style when it is author-date and APA
  otherwise, as Zotero's annotation copy uses author-date.
- **Record fields** a style needs that the record lacked: issue, journal
  abbreviation, a full date and a URL. doi.org's own BibTeX already held
  the issue and month for most pages; a migration moved them into the
  record, since a hand edit rebuilds the entry from the record.

## What was left, and why

- **Zotero sync of the new fields.** Adding `issue` and `journal_abbr` to
  the compared fields looked like two names in a tuple. The plan review
  found otherwise: whole-projection comparisons decide trash, restore and
  permanent deletion (`zotero_sync_items.py` `_zotero_changed`,
  `_page_trashed`, `_zotero_trashed`, `zotero_sync_gone.py`), and a base
  stored before the upgrade would read as changed in each, bringing deleted
  items back; a backup restore blanks the Gamma base, so a conflict would
  push empty values over Zotero's; rounds visit only what changed, so most
  linked items would never pick the fields up; and every write Zotero once
  refused would be retried. A design has to version the stored projections,
  fill old bases from Zotero's side, requeue every live item once and keep
  the refused marks. The RDF import and export should then carry the same
  fields, keeping the pass-through element while it agrees with the record
  (`zotero_export._kept_element`).
- **Names on pages linked before release 72.** A sync of 71 read Zotero's
  "Jorge Fernando" / "Pereira Campos" as "Jorge Fernando Pereira Campos",
  which cites the surname "Campos". Such a page takes the record's
  "Pereira Campos, Jorge Fernando" only once a round reads its item (a
  change on either side, a force pull): rounds visit only what changed,
  and requeueing every linked item at the upgrade would read the whole
  library from Zotero once for the few names it concerns
  ([zotero_sync.md](../dev/zotero_sync.md#names)).
- **A split merged name by name.** The link merges how names split as one
  list: two names split anew, one on each side, or a split made here while
  Zotero adds a co-author, are both sides changing the authors (Gamma's
  go, Zotero's stay in a conflict row), where a merge by name could take
  both.
- **Word-processor plugins**, live citations and RTF Scan: a separate
  product.
- **Citations inside Gamma notes** (a `/cite` command, a bibliography
  block): needs a stored citation node that re-renders when the style
  changes.
- **Importing BibTeX, RIS and CSL JSON files** as pages: belongs with
  adding a paper by identifier without a PDF ([zotero-features.md](zotero-features.md) §3).
- **A key formula**, editors, place, edition, journal abbreviation lists,
  server-side rendering for the AI `cite` tool and MCP, retraction
  warnings at cite time.

## Sources

- [Zotero: Creating bibliographies](https://www.zotero.org/support/creating_bibliographies)
- [Zotero: Word processor plugin usage](https://www.zotero.org/support/word_processor_plugin_usage)
- [Zotero: Export preferences](https://www.zotero.org/support/preferences/export),
  [Cite preferences](https://www.zotero.org/support/preferences/cite),
  [Styles](https://www.zotero.org/support/styles)
- [Zotero: Exporting](https://www.zotero.org/support/kb/exporting),
  [Importing standardized formats](https://www.zotero.org/support/kb/importing_standardized_formats),
  [Item types and fields](https://www.zotero.org/support/kb/item_types_and_fields)
- [Zotero 8 changelog](https://www.zotero.org/support/8.0_changelog),
  [Zotero 9 changelog](https://www.zotero.org/support/9.0_changelog),
  [Zotero changelog](https://www.zotero.org/support/changelog)
- [zotero/zotero](https://github.com/zotero/zotero) (`chrome/content/zotero/xpcom/cite.js`, `style.js`, `quickCopy.js`),
  [zotero/translators](https://github.com/zotero/translators) (`RIS.js`, `BibTeX.js`, `BibLaTeX.js`, `CSL JSON.js`),
  [zotero/bundled-styles](https://github.com/zotero/bundled-styles),
  [zotero/web-common](https://github.com/zotero/web-common)
- [Better BibTeX: citing](https://retorque.re/zotero-better-bibtex/citing/),
  [automatic export](https://retorque.re/zotero-better-bibtex/exporting/auto/),
  [Pandoc](https://retorque.re/zotero-better-bibtex/exporting/pandoc/)
- [citeproc-js: running the processor](https://citeproc-js.readthedocs.io/en/latest/running.html)
- [CSL specification](https://docs.citationstyles.org/en/stable/specification.html),
  [CSL styles](https://github.com/citation-style-language/styles),
  [CSL locales](https://github.com/citation-style-language/locales),
  [CSL data schema](https://github.com/citation-style-language/schema)
- [citeproc-py](https://github.com/citeproc-py/citeproc-py), [citation-js](https://github.com/citation-js/citation-js)
- [Clipboard API](https://w3c.github.io/clipboard-apis/), [WebKit: async clipboard](https://webkit.org/blog/10855/async-clipboard-api/)
