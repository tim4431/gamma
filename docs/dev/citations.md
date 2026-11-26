# Citations

How a page's paper record becomes a citation: a formatted reference in a
CSL style, a citation in the text, a BibLaTeX, RIS or CSL JSON entry beside
the BibTeX one, and the copies and files the app makes of them. The server
keeps the data side (`gamma/citations.py`, `gamma/meta_fields.py`,
`gamma/names.py`, `gamma/bibtex_format.py`, the bibliography builders in
`gamma/routers/export.py`, the style store in `gamma/routers/citations.py`);
the browser renders every
style (`frontend/src/citations/`). What Zotero does and why Gamma took what
it took: [research/citations.md](../research/citations.md). The record
itself and how it is looked up: [paper_metadata.md](paper_metadata.md);
the BibTeX entry and the export dialog: [import_export.md](import_export.md#bibliography).

## The record a citation reads

A page's record is `properties.meta` ([paper_metadata.md](paper_metadata.md)).
Beside the fields every record has (title, authors, year, venue, volume,
pages, publisher, ISBN, DOI, arXiv id, `kind`), four keys hold what a style
prints and the record lacked before migration step 72
(`meta_fields.NEW_FIELDS`):

- `issue`, at most 40 characters;
- `journal_abbr`, the journal's abbreviation, at most 300 (both limits
  `meta_fields.CITED_MAX`, applied by `clean_cited` wherever a record gains
  them);
- `date`, the full date, `YYYY-MM` or `YYYY-MM-DD`, only when it says more
  than the year;
- `url`, the record's own address, an http(s) URL with a host and no
  whitespace, at most 2,000 characters (`meta_fields.clean_url`; a
  `javascript:` address, a relative path or a bare word is "").

A record holds each key only when it has a value. A record without them
is the record from before them, and its BibTeX entry is byte for byte the
same. The writers:

- the lookups: doi.org and Crossref give `issue`, `container-title-short`
  as `journal_abbr` (unless it is the journal's own title), and `date`
  from `issued` when it names a month (`_csl_cited`). arXiv dates a
  preprint by the day its first version went up, and a paper it marks as
  published (`journal_ref`) not at all;
- a Connector clip's page record. The Connector sends the date tag as the
  page wrote it, which `web_record` reads through `web_page.normalize_date`
  and `meta_fields.parse_date`; it keeps `issue`, `journal_abbr` and a
  `date` that agrees with the year ([extension.md](extension.md#detection));
- a hand edit (`metadata_update`, [paper_metadata.md](paper_metadata.md#metadata-fetch--edit--cite));
- the Zotero link's pull, which drops a date its pulled year disagrees
  with ([zotero_sync.md](zotero_sync.md));
- migration step 72, which gave every record the issue and month its
  cached BibTeX entry already held ([migrations.md](migrations.md)).

**Date and year agree.** The year stays the field every reader has: a
BibTeX key, a Zotero item and the popover's Year were built from it. So
`date` counts only while it is a month or a day of that year
(`meta_fields.date_agrees`); a date of another year is stale (a year
pulled from Zotero, an edit of the year alone) and every reader takes the
year alone. `meta_fields.parse_date` reads what the Date row or a source
wrote: `2020`, `2020-03`, `2020-03-05` and `2020/3/5` set the year and,
when more precise, the ISO date, and so does a date in English words as
Zotero's `strToDate` reads one (`August 1, 2020`, `1 Aug 2020`,
`March 2020`, PubMed's `2020 Aug 1`); anything else (`in press`, `n.d.`)
is the year as written, 20 characters at most, with no date. Both rules, and the
popover's Date row that shows the agreeing date (`shared/lib/itemTypes.js`
`metaDate`), are pinned by one set of cases,
`tests/shared/citationdates.json`.

**`citations.record(props)`** is what every format reads: `meta` with
`date` only while it agrees, and as `url` the record's own address, else
the page's `web_url` (the tab a clip came from), both through `clean_url`.
None for a page without a record. The AI `cite` tool prints it
([ai_tools.md](ai_tools.md#cite-both-scopes)).

## The formats

`gamma/citations.py` maps a record to CSL JSON, RIS and BibLaTeX as
Zotero's translators do, so a file Gamma writes reads in Zotero as
Zotero's own would. The code is the reference; in outline:

- **One reading of the record** (`_work`): the item type as cited (a
  journal article whose only venue is arXiv is a preprint, as
  `bibtex.build_entry` treats it), the venue by what it is for that type (a
  journal, proceedings or the book a chapter is in; a presentation's
  meeting; a report's series when the report has a publisher of its own),
  the publisher (a thesis's university and a report's institution through
  `item_types.publisher`), the agreeing date and the identifiers. An arXiv
  preprint is cited as Zotero's arXiv translator saves one: arXiv its
  publisher, `arXiv:<id>` its number, its abstract page its URL and
  `10.48550/arXiv.<id>` its DOI when it has none of its own. Another
  preprint's venue (bioRxiv, SSRN) is its publisher.
- **Names** are split once for every format (`names.name_parts`,
  [Names](#names) below).
- **CSL JSON** (`csl_item`, CSL data schema 1.0.2): `id` and
  `citation-key` are the citation key; types `article-journal`,
  `paper-conference`, `article` (a preprint), `chapter`, `book`, `thesis`,
  `report`, `speech` (a presentation), `document`; names as
  `{family, given, suffix}` or `{literal}`, with the particles split off
  as Zotero's `itemToCSLJSON` splits them (citeproc-js's `parseParticles`:
  the lowercase words before a surname are its `non-dropping-particle`,
  "van der" of "Jan van der Berg", which a style may demote and sorts
  under "Berg"; those ending a given name its `dropping-particle`; a
  capitalized "Den" or "Van" stays in the surname); `issued` as `date-parts`, a
  year that is no number as a `literal`; a meeting as both `event-title`
  and `event` (older styles, APSA and ASA among the bundled ones, read only
  `event`, and citeproc-js 2.4 maps neither to the other). Every field the
  record holds is kept, its URL too: whether a style prints it is the
  renderer's choice. `csl_json` writes an array, two-space indented, the
  characters as they are.
- **RIS** (`ris_entry`), in Zotero's tag order:
  - `TY` by Zotero's `exportTypeMap`: JOUR, CONF, CHAP, BOOK, THES, RPRT,
    SLIDE, and GEN for a preprint and a document;
  - the venue as `T2`, a conference paper's as `C3`;
  - `PY` the year, and `DA` the date as `YYYY/MM/DD/` (`2021///` for a
    year alone, `///in press` for a year that is no number);
  - pages as `SP`/`EP`, a range split at `-`, `--` or any other dash
    (`bibtex_format.PAGE_RANGE_RE`, which the CSL `page` and BibTeX's `--` read
    too);
  - `J2` the abbreviation, `ID` the citation key.

  A preprint's `arXiv:<id>` number has no tag, as in Zotero's export:
  Zotero reads an `M1` back as a journal article's issue. Its `UR` and
  `DO` name it. Lines end in CR LF and a record in `ER  - `; a file is the
  records a blank line apart.
- **BibLaTeX** (`biblatex_entry`, written by `bibtex_format.format_entry`
  with the `bibtex_format.BIBLATEX` dialect): `@article` with `journaltitle`,
  `shortjournal` and the issue as `number`; an arXiv preprint `@online`
  with `eprinttype = arxiv`; `@thesis` and `@report` with their `type`
  and `institution`; a conference paper, a chapter and a book as in BibTeX,
  with their volume and ISBN; a presentation `@unpublished` (Zotero's
  type) with its meeting as `howpublished`; documents and other preprints
  `@misc` with `howpublished`, the venue or else the publisher. Names
  are written `Last, First`, `Last, Jr., First` and `{Organisation}`, so
  biber reads them unambiguously. The date is ISO `date`; a year that is no number is `year`.
  `@online` carries only the record's own DOI and URL, not the derived ones.
- **BibTeX** stays `bibtex.page_entry`, the cached entry
  ([import_export.md](import_export.md#bibliography)). An `@article` gains
  `number` (the issue), every entry `month` (from an agreeing date);
  `@misc`, `@phdthesis` and `@techreport` gain `url` from the record's own
  address (not `web_url`). The authors are the record's strings with an
  organisation braced whole, `{DES Collaboration}` (Zotero's BibTeX
  export braces a single-field name; bare, BibTeX takes "Collaboration"
  for a surname and splits a name at an `and` of its own); a cached entry
  gets the braces when it is printed.
- **Case** (both dialects, `bibtex_case.protect_case`): a word of a `title`,
  `booktitle`, `shorttitle` or `series` with a capital after its first
  letter is braced, `{GI}-type`, `{KiDS}`, `{eBOSS}`, `$\Lambda$ {CDM}`,
  Zotero's BibLaTeX rule, so plainnat or biblatex's sentence case keeps
  it; a word counts its digits, so `{Y3}`, `{3D}` and `{GNN2R}` are braced
  too (the BibLaTeX rule leaves "DES Y3" to become "DES y3"). What is
  braced already, math and command names stay as they are. (Zotero's
  BibTeX export braces every capitalized word, which leaves no sentence
  case at all; Gamma takes the BibLaTeX rule for both.)

Every format is keyed by the BibTeX entry's key, so a document cites one
key from any of the files: a `.bib` key is a BibLaTeX key, an RIS `ID` and
a CSL JSON `id`.

### Names

`names.name_parts` reads a name as last name, given name and suffix:

- The suffix is kept apart, as Zotero keeps it. It is also read in
  Zotero's place for it, the first name ("Martin Luther, Jr. King"), and
  in BibTeX's ("King, Jr., Martin Luther").
- A name of one word stays whole, and so does an organisation's: one with
  a word such as Collaboration, Office, Bureau, Survey or Ministry in it
  (`names._ORG_RE`).
- Six of those words are surnames too (Bureau, Office, Panel, Fund,
  Center, Centre: `names._SURNAME_ORG`). One of them alone before the
  comma of "Last, First" is a person's surname ("Bureau, Jean").
- Bureau, Office, Panel and Fund made no organisation before release 72,
  so after one given name they are a person's surname too ("Jean Bureau",
  as every name stored then reads). "U.S. Census Bureau" stays an
  organisation.

A source that keeps a name in two fields (a Zotero item, the RDF import,
doi.org and Crossref, a page's Highwire `citation_author` "Last, First")
is stored as `names.join_name` joins it: "First Last" while that splits
back to its surname, else "Last, First" ("Pereira Campos, Jorge
Fernando", "Den Hengst, Floris"; "Bureau, Anna Maria", which "Anna Maria
Bureau" would read as an organisation's). A suffix in the first name goes
where BibTeX reads it, "King, Jr., Martin Luther". So a surname of two
words, or one with a capitalized particle, cites as Zotero cites it. A
record keeps one string per author, so Zotero's single-field flag is not
kept: an organisation the word list misses reads as a person.

The popover's Authors row joins the names with "; ". Its save sends the
row split at each semicolon, and a row of one comma and no semicolon as
one "Last, First" name (`libraryUtils.js` `authorsFromRow`). Any other row
goes as typed, and the server splits it at commas and " and ", keeping a
stored "Last, First" name whole (`routers/metadata.py` `_split_authors`).

Text that lists names puts "; " between them (`names.author_list`,
`libraryUtils.js` `authorList`): the cite tool's and the AI context's
records, an exported PDF's and a Notion page's line, the page picker and
the Properties panel. A Markdown export's front matter lists them as a
block list. A citation pill's label and the AI's page lines show the
surname a reference prints, "Pereira Campos" or "van der Berg"
(`names.family_name`, `libraryUtils.js` `familyName`). The link dialog's
match reads the last word before any comma (`authorSurname`).

## The bibliography answer

One builder serves the downloads and every copy: `_CitationBuilder` in
`routers/export.py`, with a subclass per format registered as the export
modes `bibtex`, `biblatex` (`.bib`), `ris` (`.ris`,
`application/x-research-info-systems`) and `csljson` (`.json`,
`application/vnd.citationstyles.csl+json`). The keys are made unique over
the pages' key-sorted list as a `.bib`'s are (`keyed_records`), and a page
with a cached entry but no record is cited by its title in the other
formats.

`GET /api/bibliography?page_id=|folder=&format=` (with `sub_pages=1`, a
page and the pages inside it) answers in one format, `format` (`bibtex`,
the default, `biblatex`, `ris` or `csljson`; 400 for another):

- `format`, the one asked;
- `name`, the export's file name without its suffix (the folder's or the
  page's), which a reference list downloaded from the Export dialog is saved as;
- `entries`, one per citable page in the file's order: `page_id`, `title`,
  `key`, `pinned`, `text` (the entry in that format; for CSL JSON a
  one-item array, as Zotero's copy gives it) and `csl`, the CSL JSON item
  the browser renders every style from, whatever the format;
- `skipped`, the pages left out with the reason;
- `text`, the file, byte-identical to that mode's download (a `.bib` with
  its head comment).

`POST /api/bibliography` takes `{page_ids, key_folder?, format?}`, a
selection whose ids do not fit a URL, and gives the same answer. Repeats
are dropped, then 400 for none or an unknown format and 413 past 1,000
(`BIBLIOGRAPHY_PAGES_MAX`). Each id is checked as a page export's is
(`_checked_page`: 403 outside a share, 404 for a note, a page in Recently
deleted or an unknown id). Entries and skipped pages come back **in the
order asked**, which a numeric style numbers by, and `text` is those
entries in that order laid out as the format's file (a `.bib` without its
head comment, an `.ris`'s records, one CSL JSON array). The keys are made
unique over the pages' key-sorted list; with `key_folder`, over that
folder's papers first (`bibtex_keys.unique_keys`), so a copy made inside a
folder carries the keys the folder's `.bib` has, and an asked page outside
the folder is keyed after them and around them: a key pinned on it that
one of the folder's papers holds takes the suffix.

On a hosted server the POST counts as a read (`hosted.POST_READS`), so a
read-only server whose plan has lapsed still copies citations
([cloud_accounts.md](cloud_accounts.md) "Hosted containers"). The web app
reads both through `transfers/exportApi.js` (`bibliographyPreview`,
`bibliographyFor`, each with its `format`). `useCitations` keeps an answer
30 s by what it covers and its format, so a menu's prefetch and the click
that follows take one request; a change of the open page's stored entry
or record drops every answer kept. A copy in a file format copies the
answer's `text`: a POST's for pages, the GET's for a folder, so a folder's
BibTeX or BibLaTeX copy is the folder's `.bib`, head comment and all. A
render or a key command reads only the `csl` items and keys, which every
answer has.

## Rendering

### The engine behind a lazy boundary

Every style is rendered in the browser by citeproc-js (npm `citeproc`,
pinned at 2.4.63, AGPL-3.0-or-later per its licence), the engine Zotero
uses, so the output is Zotero's. A Python renderer would disagree with it
on author-date lists, and a Node one cannot run on the iPad and Android
servers ([research/citations.md](../research/citations.md#rendering-engines)).

`citations/render.js` is the front door. Its one literal
`import("./cslCore.js")` is the boundary: `cslCore.js` imports citeproc
and `cslAssets.js`, and nothing else may import either one (DOMPurify it
shares with `shared/lib/svgSanitize.js`), or the engine lands in the startup bundle and `npm run budget` fails
([frontend-refactor.md](frontend-refactor.md#lazy-boundaries)).
`preloadCitations()` fetches the chunk and `preloadStyle(id)` a style's XML
too; both never reject, and the surfaces that render call them on
pointer-enter or focus, or as they open (the menus, the share popover,
the Reference line, the Export dialog's Bibliography card).

`render({items, style, locale, mode, output, locators})` resolves to
`{output, entries, styleClass}`. `items` are CSL items in the order
wanted; `mode` is `bibliography` or `citation` (every item as one cluster,
a note style's note); `output` is `html`, `text` or `rtf` (a whole RTF
document with Zotero's 720-twip hanging indent); `entries` has each item's
entry as a standalone piece in input order (`""` for an item the style
leaves out, as Chicago's notes style does a talk), computed only when read.
It rejects with `status: 404` when an added style is no longer on this
server. `renderPair` gives the HTML and the text a copy puts on the
clipboard, and `entries` as such pairs, made when first read (the Export
dialog's copy of the highlighted reference).

`cslCore.js` keeps an engine per style revision, locale and output, at
most 24, the oldest dropped first, and drops one that throws, since
citeproc's state after a failure cannot be trusted.
A render is one synchronous pass with the registry emptied first, so
numbering, disambiguation and 2020a/2020b hold across one list and never
leak into the next. Names are taken as the server split them, particles
included (`parse_names` off, as in Zotero, whose `itemToCSLJSON` splits
them), URLs and DOIs are wrapped as links, APA-like styles
capitalize subtitles, and a style without a bibliography gives its
citations as a list. HTML gets Zotero's inline presentation from
`makeBibliography`'s parameters (line height, hanging indent, label
columns, entry spacing), so a word processor that drops stylesheets keeps
the layout.

**The URL rule.** The renderer's copy of an item keeps `URL` only when it
is http(s), with a `"` written `%22`, and drops it, with the accessed
date, from a journal, magazine or newspaper article that has pages:
Zotero's "Include URLs of paper articles in references", off by default.
The CSL JSON file keeps it.

**Sanitizing.** citeproc's escaper leaves quotes in attribute values
alone, so a link's address (an item's URL or DOI) is the way an item could
reach the markup. The renderer's copy writes their `"` as `%22`. Every
HTML render goes through DOMPurify before anything shows, copies or saves
it:

- the tags `div span i b sup sub br a` and the attributes `href class style`;
- an `a` keeps its `href` alone, and only an http(s) one;
- a `class` stays only when it is citeproc's own (`csl-…`, `nocase`);
- a `style` is dropped when it could load something: `url(`, `@import`,
  `image-set(`, `expression(`, `element(`, `src(` or a backslash
  (`svgSanitize.js` `CSS_LOADER`, the one definition of that rule).

**On screen.** `citations/CitationHtml.jsx` shows the render in the share
popover, the metadata popover's Reference, the Settings preview and the
Export dialog's reference list. It shows a screen variant (`screenHtml`),
never what is copied:

- the style's print spacing (APA's `line-height: 2`, Chicago's blank line
  between entries) gives way to the app's line height (`--lh-ui` in the
  popovers, `--lh-read` elsewhere) and an entry gap;
- the hanging indent moves from the list onto each entry, so a marked
  entry's tint holds its first line;
- a link's text may break after `//` and before a single `/`, as the
  Chicago Manual breaks an address.

One rule styles it in both themes (`.citeHtml` in app.css): links in the
accent, and a label column such as IEEE's `[1]` never broken.

### Styles and locales

Gamma carries Zotero's fifteen bundled styles and American Physical
Society's, sixteen CSL files in `citations/styles/`, unchanged from the CSL
styles repository at the commit their `README.md` names, with a size and
SHA-256 per file; `bundledStyles.js` lists their ids, titles and classes,
held to `tests/shared/citationstyles.json` and to the XML by
`tests/citations.test.mjs`, and the server holds the same ids and classes
(`routers/citations.py` `BUNDLED`). The 63 CSL locales and `locales.json`
are in `citations/locales/`. Both are CC BY-SA 3.0 (`LICENSE` in each
folder); the licence asks software that uses them to credit the CSL
project with a link to citationstyles.org, which Settings → Citations
does under Your styles. Each style and each locale is a lazy raw chunk of
its own (`import.meta.glob` with `?raw`), fetched the first time a render
needs it. The default style is APA (`DEFAULT_STYLE`).

citeproc asks for a locale synchronously, so `cslAssets.loadLocales`
loads ahead every locale an engine may ask for: the chosen language's base
and dialect, the style's `default-locale`, each `locale=` the style tests,
and en-US. A tag with no file of its own answers with the closest file
(`localeFileFor`): the same tag in another case, a script form's file
(`sr-Cyrl` → `sr-Cyrl-RS`), the language's primary dialect from
`locales.json` (`de-LU` → `de-DE`), else en-US. A style's own
`default-locale` wins over the chosen language.

**A dependent style** has no layout of its own: it names an independent
parent in `<link rel="independent-parent">` and renders with that one's
layout and its own default locale, which citeproc only learns of by force
(`CSL.Engine`'s `forceLang`). The server resolves it, so the browser
derives nothing of it: `cslAssets.styleSource` takes the parent the `.csl`
route names in `X-Csl-Parent` from the bundle (a 404 when this build does
not carry it), or the parent's XML the route sends in the dependent's
place, and the locale from `X-Csl-Locale` ([below](#settings--citations-and-the-style-store)).

## Quick Copy and the copy surfaces

Three account preferences (`app/prefDefs.js`, Settings → Citations):
`citationStyle` (`gamma-citation-style`, default `apa`), `citationLocale`
(`gamma-citation-locale`, default `en-US`) and `quickCopyFormat`
(`gamma-quick-copy`): what Copy bibliography and its shortcut give, `style`
(the default style's bibliography), a file format (`bibtex`, `biblatex`,
`ris`, `csljson`) or a key command (`latex` `\cite{a,b}`, `pandoc`
`[@a; @b]`, `typst` `@a @b`, as Better BibTeX's Quick Copy gives them;
`formats.keyCommand` writes a key a command would break on in its braced
form or drops the characters that would end it).

`citations/useCitations.js` is the owner hook App calls once
([frontend-refactor.md](frontend-refactor.md#new-state)); its rules are
the pure `citeRules.js`. It holds the style in use (`citeRules.styleInUse`:
the preference, or APA when the account's style is not on this server),
the answers, and the copies; the account's added styles are
`citations/citationApi.js`'s list, held once for the tab and read through
`useSyncExternalStore` (asked for when a menu opens, the share popover
opens, or the default is not a bundled one; a viewer without an account
of this server has none). A copy runs `citeRules.copyPlan` (what `as` and
the Quick Copy format make) and reads the answer of that plan's format:

### The menus and the commands

The page menu (a page, the library's selection, a Pages inside row, a
Files window row) has a citation group of its own where an acted-on page
has a record (`CitationMenuRows`, [ui-design.md](ui-design.md#menus-and-submenus)):

- **Copy citation**, the citation in the text, only while Quick Copy is
  the style, as Zotero shows it;
- **Copy bibliography** under the Quick Copy format's label
  (`formats.quickCopyLabel`: "Copy as RIS", "Copy \cite{…}");
- **Copy as ▸** with every other format and style in labelled groups:
  Formats (BibTeX, BibLaTeX, RIS, CSL JSON) and Cite keys (`cite{…}`,
  Pandoc, Typst), `formats.copyAsGroups`; Your styles (the default, marked
  *default*, and the added ones) and Other styles, the other bundled ones,
  `citeRules.copyAsRows`. A screen reader names each row's group
  (`MenuGroup`).

A folder's menu has the same group, without Copy citation, for every
paper filed in it or below it, where one of them has a record
(`useCitations` `hasRecordIn`). Opening the page menu prefetches the
engine, the added styles and the answer its rows copy from; a folder's
waits for the click. A selection is copied in the order the list shows
it, not the order it was clicked in (`citeRules.inListOrder`), with the
folder the library shows as `key_folder`.

The status pill says what was copied (`saidOfRender`): the citation itself
while it is 60 characters at most ("Copied (Smith & Doe, 2023)."), else
"Citation copied (Chicago 18th (notes)).", "Bibliography of 3 papers
copied (APA 7th)." or "Copied as BibTeX.". It adds how many pages had no
record ("2 of 5 pages have no paper metadata.", `skippedNote`).

Two commands do the same from the keyboard (`app/appCommands.js`, the
Library group, shown while `citePicks > 0`: a selection of which one page
has a record, or an open page with a record or a stored entry):
`app.copyCitation` and `app.copyBibliography`, on the library's selection,
else the open page. Like the handwriting keys they are declined in a text
field or a note's editor (`outsideFields`).
Their default chords are Zotero's, Ctrl+Shift+A and Ctrl+Shift+C (⌘ on a
Mac), in the desktop app only: in a browser both are the browser's own
shortcuts, so there they have no default and take one in Settings →
Keyboard ([hotkeys.md](hotkeys.md)). With a Quick Copy format other than
the style, Copy citation copies in that format, as Zotero's does. The page
menu shows the chords beside its rows where the rows act on what the
commands act on (the library's selection).

### The share popover

The page's Citation section (`CitationShareSection`,
[ui-design.md](ui-design.md#the-share-popover)), from the top:

- the **Reference** in the default style, copied rich through `copyPage`
  as every copy is, with the record's provenance and a style picker;
- **Copy citation**, with the in-text citation beside it on two lines at
  most, except for a numeric style, whose `[1]` means nothing alone;
- the slide citation; with no AI service connected, a line saying one
  writes it, and Set up AI…;
- the BibTeX entry the `.bib` export prints.

The style picker is a `MenuSelect` with `CitationChoices.styleMenuProps`:
the account's added styles first under Your styles, then the bundled ones
in sections by class. For an account it ends in More styles…, which opens
Settings → Citations. Its trigger names the style short (`render.js`
`styleLabel`): "APA 7th", "Chicago 18th (notes)", an added style's title
or, past 28 characters, its title-short. A style picked there becomes the
account's default, and the status pill says "Citations now use IEEE."
with Undo (`useCitations.setStyle`).

The section's action, **Copy as ▾**, copies the page in BibLaTeX, RIS,
CSL JSON (Formats) and the key commands (Cite keys). The popover asks for
the page's answer when it opens, and again when the stored entry or the
record changes; a page with a stored entry and no record is asked for
too. An answer with no entry (a stored entry that is no valid BibTeX, and
no record) shows the no-metadata hint in place of the Reference, without
Copy as or the in-text row (`shareLoaded`).

### The metadata popover's Reference

Under the table of fields, full width, a Reference block shows the record
as the default style cites it, the style's short name on its label line,
in a `CopyBox` as the share popover's (`ReferenceLine`). It renders the server's own
CSL item of the saved record (the answer's `csl`), the item every copy is
made from, so it shows what a copy gives: an edit shows once it is saved,
when the stored entry changes and the page's answer is read again. A page
with a stored entry and no record shows its title as the style cites it
(the server cites it by title alone); a page with neither shows none.
Its copy button copies through `copyPage`, so it gets the status pill and
its retry. There is no second copy of the server's mapping in the browser.

### Copy with citation

The PDF selection tip (after Comment and Link, which stay its first
tools: [comments.md](comments.md)) and a highlight's menu offer **Copy
with citation**: the passage in quotes with its citation,
`“…” (Smith & Doe, 2023, p. 678)` (`citeRules.quotePayload`;
`useCitations.copyQuote`). It is cited in the default style when that
cites by name in the text (class `author-date`, or `author` as MLA:
`(Smith and Doe 678)`), else in APA, since a number or a note means
nothing beside a pasted quote; the row's title says which
(`quoteHint`). The pill repeats the citation ("Quote copied with (Smith
& Doe, 2023, p. 678)."). Not in a share view. The selection copies the
original words, even over a translation.

**The locator** (`formats.pageLocator`) is the page the quote is on: the
PDF's page label when it has labels (`iv`, `347`); else, when the record's
pages are a numeric range exactly as long as the PDF, the printed page (a
journal article starting at 676 cites its third page as 678); else the PDF
page number. The viewer reads the labels and page count once per document
(`labelsRef`). A highlight that lost its place cites no page; a page with
nothing to cite (no record, no stored entry) copies the quote alone and
says why, and one with a stored entry alone is cited by its title.

### The clipboard

Every copy goes through `copyPayload(promise, {html})` (`shared/lib/utils.js`),
called in the click itself before anything is awaited: with the async
clipboard the `ClipboardItem` is made at once from promised blobs, so
Safari and the iPad's web view keep the gesture while the answer is fetched
and the render made. It puts `text/plain` and `text/html` (the render's
HTML, or the text escaped with `<br>` when it has none); with
`html: false` text alone (a file format, a key command). No RTF: the async
clipboard takes none. Without the async clipboard (plain HTTP on a LAN) it
awaits the payload and runs the legacy copy, which works while the browser
still counts the click. It resolves `false` when the clipboard refused,
and the status pill then offers **Copy**, a fresh gesture over the same
render; it rejects with the payload's own failure.

## Settings → Citations and the style store

The pane (`settings/SettingsCitations.jsx`, [settings.md](settings.md)):

- **Citations**, the account's tag: Style, with a preview of a fixed
  sample (a journal article by one author with a German title, one by
  two, a book); Language, disabled with "Set by the style" when the style
  has its own locale; and Quick Copy, its menu as Zotero's Item Format
  ("Citation style (APA 7th)", then Formats and Cite keys). The Language
  menu leads with the likely languages (the one chosen, English as the US
  and Britain write it, the interface's), then All languages.
- **Your styles**, the styles the account added, each with its class, Use
  (or the In use tag) and remove; **Add style…** and the CSL credit.

A long menu opens scrolled to its checked row (`MenuSelect`).
`citations/CitationChoices.jsx` holds what the pane, the share popover and
the Export dialog's reference list share: `useAddedStyles`, `styleMenuProps`
(a Style `MenuSelect`'s props) and `CitationLanguageRow`. `useAddedStyles`
reads citationApi's list; the pane reads it again on each visit with the
held list on show, so only a first visit says Loading….

`settings/CitationStyles.jsx` is the Add style dialog. It searches the
repository 250 ms after typing pauses and lists each result with its
class (`citeRules.styleKindLabel`) and Add (or Included, Added). The
results sit in a box of one height, so the dialog stays put while typing.
**Add from file…** is in its footer, the main button while downloads are
off; the other button is Close, Done once something was added. An add or
a removal changes citationApi's list in place, so the app's copies and the
Export dialog see it at once.

**The store.** An account adds styles on its server, from the CSL
repository Zotero serves (`https://www.zotero.org/styles`, about 10,900
styles) or from a `.csl` file. They are rows of users.db
`citation_styles` ([user_db.md](user_db.md)), at most 50 an account
(`STYLES_MAX`; the count and the insert run in one `BEGIN IMMEDIATE`
transaction, so two adds at the same moment cannot pass it). They go with the account
(`workspaces.delete_account_workspaces`). The profile carries only the
default style's id, so a style it names may be missing on another server.

| Method | Path | What it does |
|---|---|---|
| GET | `/api/citations/styles` | the account's added styles by title (no XML), `limit`, and `downloads` (whether the repository is open here) |
| GET | `/api/citations/repository?q=` | the repository's styles holding every word of `q`, the closest first (an id or short title that is the query, then a title starting with it; independent styles before dependent ones), at most 50; `[]` for a blank query |
| POST | `/api/citations/styles` | `{id}`: add the repository's style (a renamed one under the id it has now) |
| POST | `/api/citations/styles/upload` | multipart `file`: add a `.csl` file |
| DELETE | `/api/citations/styles/{id}` | remove one; 404 when the account has none of that id |
| GET | `/api/citations/styles/{id}.csl` | the XML the browser renders with (`Cache-Control: no-cache`), as a sandboxed download (`Content-Disposition: attachment`, `Content-Security-Policy: default-src 'none'; sandbox`, as an uploaded SVG is served): an uploaded file is any XML with a CSL root, which a browser opening the address would otherwise run in this origin; the app's `fetch` reads it as it is |

Every route is the signed-in account's own (`require_personal_user_id`):
a guest and an integration token get 403 on each, a visitor with no
account (an anonymous share view) 401, the list too, which the client
reads as "no added styles"; an account reading through a share gets its
own styles.

**Validation** (`read_style`, before anything is kept): at most 512 KB,
UTF-8, no DOCTYPE or entity declaration (refused before the parser sees
it), a `style` root in the CSL namespace with its `info`, a citation
layout unless it is dependent, an id (the last segment of its `<id>`) and
a title. A style with no class gets one from its layout (`class="note"`
a note, a `citation-number` numeric, else author-date); a dependent one
with none takes its parent's, a bundled parent's from `BUNDLED`, which
maps each bundled id to its class. A style whose id is a bundled one is refused
(400): a copy of one's own needs an id of its own. Errors: 400 a bad id or
bad CSL, 403 downloads off, 404 an unknown repository style or parent, 409
the limit, 502 the repository out of reach.

**The parent rule.** A dependent style's parent must be a style of the
repository (its `independent-parent` link `https?://www.zotero.org/styles/<id>`).
A bundled parent is named in the row's `parent` and the `.csl` route
answers the dependent with `X-Csl-Parent`; any other is fetched with it
into `parent_xml`, and the route answers the parent's XML with the
dependent's default locale in `X-Csl-Locale`. A parent that is itself
dependent is refused (400), and nothing is kept.

**The server option** `citation_styles` (on by default; Settings → Server
› Online services › Citation style repository, `gamma/server_options.py`)
opens the repository: the index (`styles.json`, 3 MB, read through
`net_guard.guarded_urlopen`, so the admin's proxy and the address checks
apply, held in memory for a day; a failed read is the answer, a 502, for
a minute, `INDEX_RETRY_S`, so the searches queued behind it fail at once
rather than each waiting out a fetch of its own) and each style. Off, search and add
answer 403 and a file can still be added, unless its parent would have to
be fetched. The browser suite turns it off with the other online services
(`tests/e2e/harness.mjs` `OFFLINE`).

**A missing style.** A style the profile names that this server lacks (a
profile synced from another server, a style removed) renders in APA. One
rule says so for every surface, `citeRules.styleInUse` over citationApi's
list: a bundled style is always here; an added one unless the list, once
read, lacks it, or a render found its `.csl` gone (a 404, which citationApi
keeps in the list's `missing` until a read of the list or an add of it).
A list that could not be read counts as unknown, not empty, so the style
stands. `useCitations`, Settings' Style row (which says so, with **Add it
again** where the repository can be asked) and the Export dialog's
reference list all read it. A copy whose style meets a 404 is made in APA
and its pill says so ("… is not on this server, so it was copied in
APA.").

## What released clients and mirrors meet

Step 72 moves the workspace format from 55 to 72
([migrations.md](migrations.md), [mirror.md](mirror.md#the-data-format)):
an older copy's popover edit rebuilds the record from the fields it knows
and would erase the new ones. So a clone and its origin, a publication and
the share host, and a desktop vault of another server pause until both
sides have run it. An older tab of an upgraded server sends `year` and no
new keys: `metadata_update` keeps the stored new keys it did not send. An
older Connector sends no date, issue or abbreviation, and its records are
as before. The new fields are not synced with a Zotero link
([zotero_sync.md](zotero_sync.md); why: [research/citations.md](../research/citations.md#what-was-left-and-why)).

## Tests

- `backend/tests/test_citations.py`: the record and its date and address,
  CSL JSON, RIS and BibLaTeX for an article, an arXiv preprint, a published
  paper read from arXiv, a thesis, a report, a conference paper, a
  chapter, a book and a presentation, and page ranges written with `--` or
  another dash.
- `backend/tests/test_bibtex_export.py`, `test_bibtex_entries.py`: each
  mode's download, one key across every format, a selection in the order
  asked, keyed around its `key_folder`'s keys (pages outside it, a pinned
  one too) and checked page by page; the new BibTeX fields only when the
  record has them, and `record_from_entry`.
- `backend/tests/test_metadata_fields.py`: what doi.org, Crossref, arXiv
  and a web page's record give. `test_metadata_update.py`: the Date row, an
  edit from an older tab, a clear, a typed DOI, the merge inside the write.
- `backend/tests/test_citation_styles.py`: the store's routes, validation,
  parents (a bundled parent's class, a chain of dependents refused), the
  `.csl` route's headers, limits, the option, an unreachable repository
  (one index fetch for two 502s) and who may call them (the repository
  monkeypatched at `_fetch`).
- `backend/tests/test_names.py`: a suffix where Zotero and BibTeX keep it,
  "Bureau, Jean" and "Jean Bureau" a person, `join_name`'s forms reading
  back alike, the compared forms, the key's surname, the lists and labels.
  `test_zotero_fields.py`, `test_zotero_sync_names.py`: a name compared as
  its written creator reads back, a push keeping Zotero's split, a split
  made anew on either side, a link stored by release 71 after step 72, a
  restore, a force and a first link with a typed "Last, First" name and an
  arXiv line with its class, Zotero's names in a conflict's "theirs".
- `backend/tests/test_migrations.py` (`test_v72_*`), `test_zotero_local.py`
  (a disagreeing date dropped), `test_shared_fixtures.py` (both suites read
  `citationstyles.json` and `citationdates.json`).
- `frontend/tests/citations.test.mjs`: the engine over the bundled styles
  and locales from disk under jsdom (every style renders, numbering and
  order, a note style, the URL rule, the HTML kept to its markup and
  http(s) links, RTF, a dependent with its parent, the locale fallback, the
  files against their READMEs, the bundled list against the fixture), key
  commands, copies in a file format, the locator, the preferences;
  `copyPayload.test.mjs`; `citeRules.test.mjs` (the style in use, a copy's
  plan, the menus' styles); `transferFormats.test.mjs` (the Bibliography
  card); `itemTypes.test.mjs` (the Date row, over `citationdates.json`);
  `extension/tests/pageRecord.test.mjs`.
- The browser suite's `citations` group (`frontend/tests/e2e/scenarios/citations.mjs`):
  the Bibliography card's formats, its RIS and CSL JSON downloads against
  the answer the review read, a reference list in APA then IEEE (the whole
  list in its pane, the picked paper's entry marked) with its `.html`
  and rich Copy all; the share popover's Reference, its style picker and
  Copy as ▾ BibLaTeX; the library's Copy bibliography of a selection in the
  list's order with a skipped page; a highlight's and a selection's Copy
  with citation at the printed page; Settings → Citations' Add from file
  and its preview, with the repository off. Under WebKit the copy steps
  (`--only "citations: copy"`) read a stand-in clipboard.
