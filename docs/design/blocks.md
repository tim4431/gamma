# Blocks and pages

What Gamma stores when you write, and why a page is the only container.
Everything you make in a workspace is a block: a note, a highlight, a
drawing, a sheet of paper, a page. The rest of Gamma (sharing, search, the
AI, sync, export) reads that one shape.

## Everything is a block

A block has an id, a parent, a place among its siblings, its text (in
Markdown) and a set of properties. Blocks nest, so a workspace is one tree:

```text
workspace
├── library                      the pages at the top
│   ├── Attention is all you need          ← a page
│   │   ├── highlight  "scaled dot-product attention"
│   │   │   └── note   "compare with additive attention"
│   │   ├── drawing    a circled figure on p. 4
│   │   └── note       "## Questions"
│   │       └── note   "why divide by √d?"
│   └── Group meeting 3                    ← a page with no file
│       └── …
├── folders                      the folder tree, nested
├── labels                       the labels, a flat list
└── Recently deleted             pages kept for 30 days
```

**What a block is follows from what it carries.** No block stores a type.
One that carries strokes is a drawing; one with a box on the page is a
text box; then a picture, a sheet of paper, a recording, a link region, a
highlight. A block that carries none of these is a note. So a highlight
with a note under it is a highlight block with a note block as its child,
and moving, folding, linking or commenting works the same on both.

This is the shape Notion, Logseq and Craft settled on. Properties are left
untyped on purpose, so two features never fight over a column.

## A page

A page is a block at the top of the library. Its title is the block's text.
Every feature works on the page and keys on its id: opening, sharing,
exporting, search, the AI's context, links, tabs, recents and chats. All of
them work on a page that holds nothing but text.

**There are no page types.** A page is not "a note" or "a paper". A card
describes it by what it carries: *PDF*, *Page*, or the kind of file it shows
(*LaTeX*, *Picture*). A page-type field was considered twice and dropped
both times: nothing needed it, and what a page carries already says what it
is.

## The document, and files in notes

A page holds at most **one document**: the file it shows beside its notes.
Any number of other files can sit in its notes as **file chips**. The two
are kept apart on purpose.

| | A file chip | The document |
|---|---|---|
| How many | any number, in any note | one per page |
| What it is | content: download it, open it in a tab | what the page shows beside the notes |
| Comes from | a file dropped or pasted into the notes | the paperclip in the page header, or a file added to the library |
| Kinds | anything but a program | PDF, text, picture, audio, video, any other file (Markdown becomes notes) |

What the document is decides which viewer opens beside the notes:

| Document | Viewer |
|---|---|
| PDF | the PDF reader, with highlights, ink, translation |
| Text (code, LaTeX, data) | a read-only code view with line ranges you can mark |
| Picture | the picture view, with regions you can mark |
| Audio or video | a player |
| Any other file | a card with its name, size and Download |
| None | the notes alone, centred |

The page layout is the same for every page; only whether a viewer appears
differs. You can fold the viewer away, and that is a view, not a mode.

The rules that follow from this:

- **A document's kind is settled once**, when it is attached: from its
  name, then from its first bytes. Replacing it keeps the kind, so turning
  a picture page into a text page means detaching first.
- **One page per stored file.** Adding the same file to the library again
  opens the page it already has. "Same" is the same bytes under the same
  extension, so one text saved as `a.txt` and as `a.log` is two pages.
- **Dropping a file on an open page adds a chip.** It never replaces the
  page's document; only the paperclip does that.
- **A Markdown file becomes notes**, never a stored document: its headings
  and lists become blocks you edit.
- **Add to library** on a chip makes the page for that file, filed in the
  folder of the page it was dropped on. It does not make a page inside that
  page, because the same paper turns up under several projects and needs
  one identity with one set of highlights. Doing it twice opens the same
  page. A Markdown chip becomes a note page that is a copy: editing one
  never changes the other.

A list of attachments with a switcher was considered and rejected. A
supplement gets a page of its own, and the project page links both.

**The bibliographic record** (title, authors, year, DOI) belongs to the
page, not to its PDF. In Zotero's terms the page is the item and the
document its attached file. So a page can carry a paper's record with no
PDF at all, for a paper you only cite.

## Pages inside pages

A page can sit inside another page, as in Notion. A **sub-page** is an
ordinary page that names the page it is inside. It has its own notes,
document, chat, share link and place in Recently deleted. Only where it is
listed changes: it is reached through the page it is in, not listed in
the library's folders (a label's list, recents and pins still show it).

```text
Thesis                       folders: Writing          labels: draft
└── Chapter 2                (filed with Thesis)       labels: review
    └── Figures              (filed with Thesis)       labels: —
```

- **Any depth**, up to 64 pages in a chain. A move that would put a page
  inside itself is refused.
- **Filed where its top page is.** A sub-page has no folders of its own;
  it shows up in Thesis's folders through Thesis. Labels stay its own,
  since a label is a tag, not a place.
- **Deleting a page takes the pages inside it along** to Recently
  deleted, as one entry. If the page above is gone some other way (deleted
  for good on another copy, or not yet arrived on an offline copy), the
  sub-page reads as a library page until it comes back.
- **Reached** by its `[[link]]`, by the "N pages inside" list under its
  parent's notes, by the path above its title, and by Quick open, where it
  reads "Thesis › Chapter 2".
- **`/page` makes one** and links it where you typed. Deleting that link
  leaves the page in place: links are typed, copied and pasted by hand, and
  undoing a deleted line would otherwise have to bring a deleted page back.

Three shapes were weighed:

| Shape | Why not, or why |
|---|---|
| Zoom into a block's children, as Logseq does | Not a page: no document, chat, share or trash of its own, and the parent's tree would carry every sub-page's notes |
| A page stored inside another page's tree | Every reader of "is this page live?" would have to walk up a chain |
| A top-level page that names its parent | **Chosen.** Everything keyed on a page keeps working; only listing and reaching change |

## Notebooks

A notebook is a page with sheets of paper. A sheet is a block with a paper:
its size, orientation, pattern and background. Drawings, text boxes and
pictures belong to the nearest sheet above them, so moving a block under
another sheet moves it onto that sheet. Deleting a sheet deletes what is on
it.

Sheets are blocks, not a page count, so two devices that each add a page
while offline both keep their page. Whether a page shows as a notebook or
as notes is each reader's own choice, kept in their browser.

## Notes are Markdown

A note's text is plain Markdown, so it stays readable in Obsidian, on
GitHub and in any export.

- Plain text is a paragraph; `- ` makes a bullet. A paragraph can have
  children, and a folded one is a toggle.
- A quote is `>` on every line; a callout is `> [!note] Title`.
- Math is `$…$` and `$$…$$`; a pasted `\( \)` or `\[ \]` is converted.
- A diagram is a fenced `mermaid` or `svg` block.
- Dates, people and page links are readable text: `@2026-10-09`, `@alice`,
  `(remind @alice)`, `[[page]]`, and `[[page|words]]` for a link that shows
  words of its own. The app draws them as chips, and every other reader
  still shows something that makes sense.

## Who made a block

A block made by Gamma's AI, by an assistant connected over MCP or by an
agent on your computer says so: *Highlighted by Codex on TIM-PC, for tim*,
and under *Made by* in its properties. It still belongs to the account it
acted for, so permissions and notifications read the account, not the
agent. No app can forge the mark. Undo keeps it, and Duplicate drops it,
because the copy is yours.

## Properties

Every block's properties can be read in its **Properties** popover (the
block's handle menu, the command palette, a tab or a library card). You
change a property through the thing it belongs to: a highlight's menu, a
picture's menu, the record's ⓘ popover, the paperclip, or the library's
menu for folders, labels and pins.

## Behind this page

- [Block-centric design](../dev/block_centric.md): the target model and
  how the code got there.
- [Documents](../dev/documents.md): the formats, the stored shape and the
  viewers.
- [Home library](../dev/home_library.md#sub-pages) and
  [Sub-pages](../research/sub-pages.md): the sub-page rules and the shapes
  weighed.
- [Notebooks](../dev/notebooks.md), [Mentions and reminders](../dev/mentions.md).
- [Data model survey](../research/data-model.md) and
  [Block properties](../research/block-properties.md).
