# The Notion round trip: what each route carries (November 2026)

Survey made on 5 November 2026 before any Gamma → Notion route existed. It
asks what Notion's exports really are, what Notion takes back in, what the API
offers instead, how other importers cope, and how much of a page survives the
trip each way. Three decisions came out of it and **all three are still
undecided**; they are listed [at the end](#the-three-open-decisions) with the
options and the recommendation, not as a plan. The mechanics of the import
as it stands are in
[dev/import_export.md](../dev/import_export.md#markdown-zips-obsidian-vaults-notion-exports-gamma-exports-zipped-notes),
the mapping for Obsidian in [obsidian.md](obsidian.md), sub-pages in
[sub-pages.md](sub-pages.md); this note does not repeat them.

## How it was found, and the words used

Read: Notion's help and developer pages (fetched 2026-11-05, changelog to
2026-11-04); the source of obsidian-importer, AFFiNE/BlockSuite, AppFlowy-Collab,
Docmost and TriliumNext; real exports that projects and users have published,
read byte by byte; and two exports of the developer's synthetic test page, made
on 2026-11-05, one with folders and one flat. The importer was then run over a
set of 27 zips and fixed, which taught more; see
[what real exports taught](#what-real-exports-taught).

No Notion workspace was written to, and no probe has come back from Notion.

- **Verified**: seen in a real export or stated in Notion's own docs, with its
  date. A doc states what Notion promises, not what was run.
- **Reported**: claimed by a blog, a vendor or another project's code comment.
  None of the blogs on the importer says it tested anything.
- **Untested**: nobody has looked. An inference is called one where it appears.

## What Notion's exports are

### Two formats and what each loses

Notion's help says little: Markdown & CSV writes pages as `.md`, full-page
databases as CSV, and "Callout blocks will be exported as HTML, as there is no
Markdown equivalent". Everything else below comes from the files.

| | Markdown & CSV | HTML |
|---|---|---|
| Both keep | Text, headings 1 to 3, lists, to-dos, code with its language, TeX, the full title (`# H1`; `<title>`), the database CSVs | same |
| Ids | Page ids in file names, no block ids | Page and block ids (`id=`), but none on a toggle heading |
| Block types | A toggle is a bullet with indented children; a toggle heading a plain heading (both verified 2026-11-05) | `ul.toggle` or `details.toggle` with its open state; toggle headings by font size |
| Colour | Dropped, callouts' too | Block and text colours, callout colour, select colours |
| Underline | Written as `<u>` (verified 2026-11-05) | A styled span |
| Page | No icon, no cover | Icon, cover and its offset, font |
| Layout | Columns one after another (inferred from one template) | Column widths (a 2024 sample) |
| Tables | The first row always becomes the header | Header flags, widths, cell colours |
| Images | Alt text, the caption again as a paragraph, no width | Caption, width, alignment |
| Bookmarks | A bare link | Title, description, preview image |
| Database values | Text only; dates in the exporter's locale | Property types; since 2026 ISO dates in `<time datetime>` |
| Comments | Never | Optional; the markup has not been seen |

Lost in both: other database views, filters and sorts (the Markdown stub keeps
a view's filter and sort as text), formula and rollup definitions, synced-block
identity (reported: the content is copied in place), mentions of a block, page
history. Other small facts that cost an importer time: `- [ ]  text` with two
spaces, every quote ending in an empty `> ` line, `‣` where a mention cannot be
rendered, nothing escaped (`[Name]` and `<tag>` come through literally), `<aside>`
callouts whose first line can be an emoji or an `<img>` alone (2026) or the icon
glued to the text (2024), and the icon host moving from `www.notion.so/icons`
to `app.notion.com/icons`. HTML changes faster: per-block `display:contents`
wrappers in December 2025, bare `details.toggle` and `input.checkbox` by July
2026, with a Trilium test still showing the wrappers in 2026. Dates in Markdown
carry no ISO form, and obsidian-importer needed day-order inference across the
export to read them (issue #672); in Gamma they stay text.

### The folder change of about November 2025

Until about October 2025 every file and directory ended in the page's 32-hex
id (verified: 2024 samples, August 2025). Since about November 2025 **files keep
the id and directories do not**: `Title <id>.md` sits beside `Title/`. When two
pages in one directory share a title, the extra one's directory carries the
id's first and last four digits, `Title abcd-wxyz/`. Notion's help says nothing
about file names. The date comes from obsidian-importer issue #450 (2025-11-22 to
25: "every file has the same single parent ID of the export itself") and a
Docmost code comment (commit 7520c329, 2026-03-15), and an autumn 2025 HTML page
fits it. Verified since in a public 2026 Markdown export, a public 2026 HTML
export (August) and the developer's folder export. A name does not always settle
it: when two pages of a title are left for one plain `Title/`, only which body
links into it tells them apart (inferred from that HTML export).

### Flat exports

"Create folders for subpages" off puts every page, CSV and attachment in one
directory, attachments numbered on clashes (`image 4.png`). Notion's help offers
this as the cure for Windows' 260-character paths, so a Windows user may well
arrive with one. Nothing in the export states the tree. Three public 2026
exports of this shape appear to be workspace-wide, from their several unrelated
top pages (an inference; the survey's two reports differ on it).

### Names, cells and stubs

- **Names are cut.** A title is cut to 50 characters before ` <id>`, sometimes
  inside a parenthesis, and a cut ending on a space leaves two spaces (2020 to
  2026). Directories too. Only the H1 or `<title>` has the whole title, and a
  CSV has neither.
- **Names are altered.** `:` goes, `.` becomes a space (`Release 2.0` is
  `Release 2 0 <id>.md`), runs of spaces collapse, while the link text keeps
  the title as typed. So a name and a label compare only by letters and digits.
- **Links** encode spaces and non-ASCII and leave `& ( )` raw, a target can
  hold unbalanced parentheses after a cut, and Docmost issue #1732 reports a
  bare `%`. A link to a database can name a `.md` or `.csv` that is not in the
  zip, which is normal for system databases and linked views.
- **Relation cells** are `Title (path.md)`, joined by `, ` in one quoted cell,
  and titles hold commas. The path is relative to the page that holds the
  database in a CSV and to the row page in a row's property line, so one
  relation has two spellings. Only the id in the path is safe.
- **`.csv` and `_all.csv`.** The plain CSV is the exported view in the view's
  column order; `_all.csv` has every property, title first and the rest sorted
  (verified 2026-11-05). Rows were equal in every sample, though one guide
  says `_all` ignores the view's filter (reported). A linked view has a `.csv`
  alone, filtered, no row directory: its rows live under the source database,
  so one row can appear in several CSVs. Three 2026 flat exports hold only
  `_all.csv`; the developer's 2026 flat export holds both. Why is unknown; the
  view-export setting is a guess.
- **Database stubs.** In 2024 a full-page database had no `.md`. In 2025 and
  2026 it can also have a stub: `# Name`, a link to its CSV, then `filters:` and
  `sort:` lines ending in a space. The stub's id is the CSV's. System databases
  leave bare pages such as `# Home views`, and the CSV they link may be absent.
- **Row pages** start with `Key: value` lines: no blank lines between them,
  the title left out, empty properties left out, not in CSV column order. HTML
  lists the empty ones. The body may open with another `# H1`, so only the first
  is the title.

### `index.html`

The zip root can hold a manifest with the whole tree, dashed ids and unencoded
hrefs: a database is an `id::<uuid>.csv` node then an `id::<uuid>` node holding
its rows, ` (Inline database)` marked. It is the only place an export states a
parent. It appears in both formats, in samples from September 2024 to August
2025, and **every one of those has folders**. It is absent from the 2020 export,
from the three 2026 flat exports, and from the developer's two page exports of
2026-11-05. Whether a workspace export written today carries one, and above all
whether a flat one does, is untested.

## What Notion's importer takes

Nobody has tested Notion's file importer against Gamma's probe zips yet. Two
zips are built, with every construct labelled, and none has been imported. So
nothing below about Gamma's output is measured.

**Documented** (help page, fetched 2026-11-05). Markdown imports "Plain text and
standard Markdown / Headings, lists, and code blocks", not "Anchor links /
Advanced or nonstandard Markdown extensions". HTML imports "Headings, paragraphs,
lists, links, code blocks, simple tables", bundled images when reachable, H4 and
below as H3; not "Complex styling and layouts, scripts, embeds, comments, edit
history". A ZIP (since 2023-09-22) is how to "preserve folder structure", with no
word on what a directory becomes; it takes up to 5 GB, with 10,000+ files "likely
to fail", and leaves out PDFs and files it cannot convert. Files take 5 MB on Free, 50 MB on paid; about
120 HTML or Markdown imports per 12 hours. A CSV makes a database and cannot make
relations, rollups or formulas; "Imports add rows. They don't update existing
rows." And: "You can't instantly recreate your workspace by reuploading your
exported workspace content."

**Tested once, three years ago.** Classmethod DevelopersIO (2023-10-03) re-imported
Notion's own exports. Callouts became ordinary blocks, background colours reset
and the table of contents went, in both formats. A cover became a body image
(HTML) or vanished (Markdown). Databases came back with their properties, but
row bodies landed as separate pages outside them: linked by a mention from HTML,
not linked from Markdown. Toggles, columns, equations, nesting and links were
not tested. The importer may have improved since; the only release note found
is on CSV column mapping (2025-07-10).

**Reported** (mostly vendor blogs of June 2026, none saying it tested anything):
to-dos and quotes come through, `==x==` stays literal, front matter shows as
text or a divider, footnotes are unsupported, a pipe table is a simple table or
a database (the blogs disagree), `<details>` vanishes, code keeps its language
(a README of about 2020 said it was lost).

**Untested, and these decide the file route**: whether `Title.md` beside `Title/`
nests as a sub-page, with or without ids; whether ` <id>` is stripped from a
title; whether relative links become page links; whether bundled images upload
from Markdown; `$` and `$$`; `<u>`; H4; a paragraph's nested children; `<aside>`;
and Notion's own `<callout>` and `<toggle>` in a file. That last one matters most.
Since July 2026 the desktop app opens `.md` files with an "Import to Notion"
button, announced beside the Markdown API. If that path shares the API's parser,
a zip carries nearly what the API carries, minus updates and ids. That is a
guess worth one test.

Nothing documented updates an imported page, and Gamma never learns the ids
Notion assigns. That a second send duplicates is an inference from this, the
quoted sentence being about CSV rows.

## The API route

Everything here is from developers.notion.com (fetched 2026-11-05) and untested
live: Gamma has never called it.

- **Markdown endpoints.** Since 2026-02-26 `POST /v1/pages` takes `markdown`,
  and `GET` and `PATCH /v1/pages/{id}/markdown` read and edit a page.
  `markdown_version: "v2"`, Notion-flavored Markdown, arrived in preview on
  2026-09-30. On 2026-10-30 Notion said v2 becomes the default in the next
  `Notion-Version`, v1 ("enhanced markdown") is deprecated and retires about three
  months later. So v2 is the only dialect worth writing.
- **What v2 carries**: `<callout icon color>`, `<toggle heading="h2">`, block and
  inline colours (nine and their backgrounds), `<u>`, equations, column ratios,
  `<table>` header flags, `<page>`, `<database>`, `<mention>`, `<date>`,
  `<synced-block>`, media with captions. Everything the Markdown export drops,
  except block ids: the read returns none, so a `#blockid` link needs the block
  API as well.
- **Limits that shape a writer.** A plain heading cannot hold children; only a
  toggle heading can, and in Gamma any block nests. A read loads about 20,000
  records and past that returns `truncated` with `<unknown>` markers. A create
  over 5,000 blocks is refused. An equation holds 1,000 characters in the block
API. File URLs in
  a read expire. Link previews, breadcrumbs and template buttons do not render.
  Sub-pages and each database row cost a request each. v2 refuses a parse failure
  with a 400 where v1 saved text. Code languages come from a fixed list, without
  `svg` or `bibtex`.
- **Tokens.** Personal access tokens exist since 2026-05-12: one workspace, acts
  as you with no sharing step, pasted once, 7 days to a year. On Free only owners
  may make one, on Plus all members, on Business owners by default; guests never.
  They are exempt from the Free block limit the API enforces since 2026-09-08 on
  multi-member workspaces, which binds internal connections. Public OAuth needs a
  client secret and a fixed redirect a self-hosted server lacks, so it would need
  Gamma Cloud as a broker (inferred; never tried). MCP's OAuth has neither
  problem, but its tool shapes are unversioned and change monthly: fine for an
  agent, fragile for a converter.
- **Rate limits.** 180 requests a minute per connection on Free and Plus, 600 on
  Business and Enterprise; a workspace-wide budget above that; about 3 a second
  for data-source queries; `Retry-After` on a 429. Reading is about two requests
  a page, so about 90 pages a minute on Free (an estimate).
- **File Upload API** (2025-05-20, per Notion's developer account): one part up to
  20 MB, several parts up to 5 GiB; 5 MiB a file on Free; an upload must be
  attached within an hour. Notion cannot reach a private Gamma URL, so images have
  to go up. **No documented way names a `file_upload` from REST Markdown**, though
  MCP has one. The workaround is a placeholder external image swapped by
  `PATCH /v1/blocks/{id}`, which the docs describe for an image block but nobody has
  tried on one made from Markdown. Also untested: `<page>` with no url creating a
  sub-page in place, and how a text run past 2,000 characters is split.

Only the API returns ids, so only it can update what it made and match pages on
the way back. It costs a stored secret, outbound HTTPS from a self-hosted server,
a job with a rate limiter, and has no sandbox.

## How other importers solve it

Every importer that cares about fidelity reads HTML or the API. AppFlowy reads
Markdown because it wants the CSVs; Gamma's began as a reader of Markdown zips.

| Project | Reads | What it does | Gamma |
|---|---|---|---|
| [obsidian-importer](https://github.com/obsidianmd/obsidian-importer) (@143eaea) | HTML; refuses a Markdown export by name (PR #364); API since v1.8.0, which its maintainer pointed to on #450 | Title from `<title>`; directory fallback for the id-less layout; merged per-item lists; locale dates (#672) | Same answer to cut names: the H1. Rejected refusing Markdown: a user with that zip gets a tree, not an error. Date inference not taken |
| [AFFiNE](https://github.com/toeverything/AFFiNE) (@8bbbfcf) | HTML; warns on a zip that starts with a CSV | Maps every path suffix to a page; skips empty page bodies; types columns from markup | Same need met by Gamma's path-then-basename rule. The rest waits for an HTML reader |
| [Docmost](https://github.com/docmost/docmost) (@7520c329) | `.md` and `.html` | Handles `Title abcd-wxyz/`, partial-id folders claiming their files before plain ones; colours from the export's `<style>` (#1795); a link whose text is the title becomes a mention | Took the pairing, and added that a name which only looks like it (`Budget 2024-2025/`) stays a title. Its placeholder page for a bare folder not taken: such a directory stays a folder |
| [TriliumNext](https://github.com/TriliumNext/Trilium) | HTML; refuses Markdown | Strips ids from both sides to pair `Title/`; detects flattened exports by CSV titles matching row pages; keeps `details[open]` | Took the idea of row names, and went further: flat trees need link lines as well, and rows ranked by value fit |
| [AppFlowy-Collab](https://github.com/AppFlowy-IO/AppFlowy-Collab) (@be5aa89) | Markdown & CSV | Keeps `_all.csv`, resolves relations across CSVs into typed grids | Keeps `_all.csv` too, in the view's column order; relations become mentions. No typed grid: Gamma has none |

Outline uses both the API (OAuth, v0.83.0) and the file import. Two more warnings
came from the sources: Obsidian's help tells users not to use the Markdown export
("omits important data"), and blogs were unreliable (one claims callouts export
as blockquotes, which every real sample contradicts).

For fixtures, obsidian-importer's `notion-testspace.zip` (MIT, HTML, December
2024) and 2anki's pages (MIT, HTML, 2020 to 2026) can be copied with their notice;
AppFlowy's carry third-party articles and photos and are for modelling on; the
personal and company exports have no licence and were used for facts only.
Gamma's tests are hand-written, which is why they missed so much until they were
compared with the real bytes.

## What survives each route

What a route can carry once its reader or writer exists; the first column is the
only one running today. PDF highlights lose their positions on every route and
keep their quote and page number; Gamma's folders and labels have no Notion home,
a parent page per folder being the nearest. No route keeps a colour outside
Notion's palette.

| Construct | Notion → Gamma, Markdown | Notion → Gamma, HTML (no reader) | Gamma → Notion, file (no writer) | Either way, API (nothing built) |
|---|---|---|---|---|
| Paragraph vs bullet | In the export; Gamma's import makes both one block | Kept | Documented; nested children untested | Kept |
| Toggle | A bullet | Kept, with open state | `<details>` reported lost; `<toggle>` untested | Kept |
| Toggle heading | A plain heading | Kept, no block id | Expected a plain heading, untested | Kept |
| Callout | Emoji kept, colour lost | Icon and colour | Plain block (2023 test) | Kept |
| Colour | Lost | Kept | Reset (2023 test) | Nine named, others nearest |
| Underline | `<u>` | Kept | Untested | Kept |
| Equation | `$`, `$$` | Exact TeX | Undocumented | Kept, block 1,000 characters |
| Image | File, caption twice, no width | Caption, width | Reported for Markdown, documented for HTML | Uploaded; Free 5 MiB; width believed not settable |
| Sub-pages | Kept, flat ones inferred | Kept, ids known | Untested | Kept |
| Links between pages | Become mentions, label lost | Carry ids | Untested | Mentions, once the target exists |
| Database | Table page and row pages, types lost | Types known, still a table | New database, row bodies apart (2023 test) | Possible; not in a first version |
| Comments | None | Option, markup unseen | Not imported | Posted as the token's user |
| Ids, sending again | Page ids; re-import adds nothing | Page and block ids | No ids; expected to duplicate | Ids come back; updates in place |

## What real exports taught

The reports were written before the importer met the zips. Fixing it over 27 zips
(some are HTML or AppFlowy's own format and are refused, two are copies; older
exports kept their tree, and the three 2026 flat ones went from 0 nested pages to
15, 26 and 18) turned up what they had not expected. Only the developer's pair
has a known answer: the flat tree matches the folder export, 9 of 10 nested.

- **A row page can show a property its CSV lacks.** The reports treated the CSV
  header as the row's keys. In the Markdown samples 425 note files with property
  lines are titled as a CSV row (copies between zips counted, matched by title), and
  3 show a key the CSV lacks, an `Email` in a people database, one in each 2026 flat
  export. Few, but a rule asking that all of a
  note's keys be columns would drop those rows from their database.
- **So a body line with a colon reads as a property.** Notion leaves empty
  properties out, so a row of a name-only database can open with `Attendees: Ada`
  in its body, and nothing marks that as body. A rule asking for one shared column
  would refuse it. This came from reasoning and a code review, not a sample: none
  of the 425 did this. The two cases pull opposite ways, which is why a name-first
  match needs a last pass that asks nothing.
- **A flat export's tree is an inference from two signals**: lines that are one
  link, and CSV row names. Neither says "parent". A line can be a link to a page
  (that Markdown cannot tell from a sub-page), and rows of one name can sit in two
  databases. Back-links from a sub-page to its top, untitled pages, and navigation
  boxes in callouts each produced a wrong tree before they were handled, so the
  rules grew from cases.
- **A workspace's top pages that link to each other are ambiguous.** Nothing
  separates "A links to B" from "B is inside A", so they nest under the first.
  The result looks right and cannot be checked. On the three 2026 flat exports
  there is no truth to compare with: "nested" is a count, not a score.
- **`index.html` is the remedy, as far as it goes.** It would replace this
  inference where present. But no flat export seen has one (see
  [above](#indexhtml)), so it fixes the case it was proposed for only if a current
  flat workspace export writes it. The inference may stay the only path.
- **What the reports had as gaps or wrong.** Now verified on 2026-11-05: Markdown
  bare-title folders, `$$` on their own lines, a toggle heading as a plain heading,
  an emoji alone on a callout's first line. A page export's files can sit under a
  `Private & Shared/` directory. And underline is not dropped: `<u>` is written.

## The three open decisions

Undecided as of 2026-11-05. Each recommendation is the survey's, not a choice made.

### 1. How pages reach Notion and come back

- **a. Files now, API-ready core** (recommended). One Notion block model with a
  v2 writer and reader as pure code; a reader for the HTML export; a Notion zip in
  Notion's own layout; the probe. Token, rate limiter, uploads and id map follow.
  Notion → Gamma improves at once and everything shipped is tested, but Gamma →
  Notion is only as good as the importer, and a second send duplicates.
- **b. Files and API now.** A faithful send with updates in place, but it ships
  untested until someone tries a token, with two documented behaviours unconfirmed
  (the image swap, `<page>` in place), a secret on the server and outbound HTTPS.
- **c. Files only.** Smallest and needs no network. Gamma → Notion stays lossy, and
  an API later means building the model then.

### 2. How far to restructure

`_import_zip` is one function of about 650 lines of closures on shared state, and
some Notion rules run on every source: the id strip gives a vault note named with
32 hex digits a `notion_id`, and any `.csv` becomes a database page. Five walkers
(readable, vault and folder sync, Logseq, notes PDF, Zotero notes) each decide
block kinds and the switches again, and have drifted: the vault export drops ink
images, and the readable front matter is invalid YAML for a title holding `:`.

- **a. Rebuild the importer in stages; build only the Notion exporter on a new
  shared walker** (recommended). A class with phases like
  `note_app_import.AppImport`, a dialect per source, Notion's layout rules in their
  own module. No existing export changes by a byte. Two export styles live side by
  side for a while, and the walker is shaped by one consumer.
- **b. Also move the readable and Obsidian exports now.** Proves the walker on
  three formats and fixes that drift, at the price of a bigger review. Folder sync
  writes vault output into users' folders, so it must stay byte-identical, and each
  deliberate fix rewrites their files on the next sync.
- **c. Minimal.** Fastest, but a third renderer of about 250 lines inherits every
  divergence, and the function grows.

### 3. How imported paragraphs look

Gamma draws a dot on every block, and an outline has no paragraph.

- **A. Keep Gamma's look and store the Notion block type invisibly** (recommended).
  Imports write a `notion` property on each block, the type and colour and icon,
  and the export reads it back. Nothing in the editor changes, and B's data is
  there if B comes later. Imported pages read as outlines, and the stored type can
  go stale under edits. A Gamma-made page has no such key and exports by defaults.
- **B. Paragraphs without a dot.** Pages read as documents, but it is a change to
  the core editor reaching well past Notion: does a Gamma page get the style, does
  Enter continue it, per block or per page, what of a paragraph with children?

What would settle most of it: the probe zips imported into a throwaway workspace
and exported back, and the HTML export of the developer's test page.

## Sources

Notion: [Export your content](https://www.notion.com/help/export-your-content),
[Import data](https://www.notion.com/help/import-data-into-notion),
[Back up your data](https://www.notion.com/help/back-up-your-data),
releases [2023-09-22](https://www.notion.com/releases/2023-09-22) and
[2025-07-10](https://www.notion.com/releases/2025-07-10) (fetched 2026-11-05);
[Notion-flavored Markdown](https://developers.notion.com/guides/data-apis/notion-flavored-markdown),
[Working with markdown content](https://developers.notion.com/guides/data-apis/working-with-markdown-content),
[Request limits](https://developers.notion.com/reference/request-limits),
[Workspace block limits](https://developers.notion.com/reference/workspace-block-limits),
[Personal access tokens](https://developers.notion.com/guides/connections-and-tokens/personal-access-tokens),
[Changelog](https://developers.notion.com/page/changelog).

The 2023 test: [Classmethod DevelopersIO](https://dev.classmethod.jp/articles/check-notion-recover-option-by-zipfile/).
Page bodies and CSV: [CodeJam, 2022-05-11](https://www.codejam.info/2022/05/notion-csv-markdown-page-content.html).
Importer claims, untested: [bulkmd](https://bulkmd.app/blog/importing-markdown-into-notion),
[markdowntools](https://www.markdowntools.com/blog/markdown-for-notion-what-actually-works).
Obsidian's advice: [obsidian.md/help/import/notion](https://obsidian.md/help/import/notion).
Stub pages: [inkeep/open-knowledge](https://github.com/inkeep/open-knowledge) `docs/content/migrate/notion.mdx`.
Sample exports: obsidian-importer `tests/notion/`, AppFlowy-Collab
`collab/tests/importer/asset/`, [2anki](https://github.com/2anki/2anki.net) fixtures,
and public 2024 to 2026 exports in personal and company repositories, not named here.
