# Multi-line quotes and callouts (October 2026)

How Obsidian, Notion and GitHub handle a quote or callout that spans lines
(typing, pasting, exporting), what standard markdown allows inside one, and
why Gamma's editor took the line model in
[docs/dev/ui-design.md](../dev/ui-design.md) (`editor/mdLines.js`).

## What was found in Gamma's own code

- A quote is markdown with `> ` on every line, and the editor helped in one
  place only: the line-break Enter continued `> `. A multi-line paste onto a
  quoted line quoted its first line only, `/quote` and `/callout` marked only
  the caret's line, and Enter on `> - item` continued `> `, not `> - `.
- The `/` line commands read one prefix: `/todo` on `> - item` gave
  `- [ ] - item`, `/h2` on `> text` dropped the quote, and `/callout` on an
  empty `> > ` gave `> [!note] > `.
- Copying out of a quote always carries the marks: the rendered view writes
  `> ` on every line (`fragmentToMarkdown` in `shared/ui/Widgets.jsx`) and
  the editor copies its source. Pasted onto a quoted line that adds a
  level, so a quote's own lines came back as a quote inside it.

## What the other products do

- **Obsidian** continues `>` on Enter, but its paste adds nothing: only the
  first pasted line stays quoted. Long-standing forum requests ask for it,
  and plugins fill the gap: the Linter's paste rule quotes every line after
  the first on a quote line, Paste Mode pastes at the caret's indentation or
  as a blockquote, obsidian-block-paste and Easy Typing do the same. Its
  callouts (`> [!type] Title`, `-` / `+` to fold) can nest.
- **Notion**'s Markdown & CSV export has no markdown for a callout: it
  writes an `<aside>` with the icon as a leading emoji and drops the colour.
  Its API's enhanced markdown writes `<callout icon="…" color="…">` with the
  children indented inside. Gamma's zip import turns each `<aside>` into a
  `> [!info]` callout ([dev/import_export.md](../dev/import_export.md)).
- **GitHub** alerts are a stricter subset: five types (note, tip, important,
  warning, caution, any case), the marker alone on its line, no custom
  titles, no nesting. What GitHub shows for a title after the marker was not
  confirmed.

## What standard markdown allows

Checked with remark (CommonMark + GFM), the parser Gamma renders with:

- A quote inside a quote, a list inside a quote, a heading inside a quote
  and lists nested by indentation are all CommonMark.
- A sub-item lines up with its parent's text: 2 spaces under `- `, 3 under
  `1. `, 4 under `10. `. Two spaces under `1. ` make a second, separate list.
- `- [ ]` is GFM. The `[!type]` title is not standard at all: elsewhere a
  callout is an ordinary quote whose first line reads `[!note] Title`.
- A single line break is a space in CommonMark (GitHub joins the lines);
  Gamma (`remark-breaks`) and Obsidian keep it.
- Lazy continuation lets a line drop its `>`; writing every mark is the
  stricter form and reads the same everywhere.

## Decisions

- Every line of a quote carries its marks, a blank one a bare `>`, rather
  than an HTML wrapper like Notion's: the notes stay Obsidian and GitHub
  markdown.
- One model for a line: where it sits (quote marks, indentation) and what
  it is (bullet, number, to-do, heading, callout title). Enter continues
  both; a paste moves its lines to the level of the line it lands on, in
  its quote or beside or under its list item, keeping their own shape; the
  `/` commands swap what a line is and keep where it sits.
- Lines copied out of a quote merge into the quote they land in; a copied
  callout keeps its level and stays a box. The paste menu offers the other
  placement and the text as copied, so neither guess traps anyone.

Sources: [Obsidian forum: paste into a blockquote](https://forum.obsidian.md/t/insert-text-in-a-blockquote-callout-via-pasting/53838),
[Linter paste rules](https://platers.github.io/obsidian-linter/settings/paste-rules/),
[Paste Mode](https://github.com/jglev/obsidian-paste-mode),
[Notion enhanced markdown](https://developers.notion.com/guides/data-apis/enhanced-markdown),
[GitHub alerts are case-insensitive](https://github.com/gohugoio/hugo/issues/12767),
[custom alert titles request](https://github.com/orgs/community/discussions/103219).
