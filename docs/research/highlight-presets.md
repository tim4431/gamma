# Highlight presets: how readers keep a style and a colour at hand

November 2026, after #154 shipped styles (wash, underline, wavy underline,
strikethrough) with presets as named chips beside free style and colour
pickers, and the developer found the presets "not implemented quite well"
and asked for one strip button per preset. What follows is a survey of how
other PDF and reading apps let people keep and reach a style-and-colour
look, and what Gamma took from it. Built:
[dev/settings.md](../dev/settings.md#text-highlighters).

## What the apps do

Each row is what the app's documentation, support pages or a recent review
said in November 2026. Where only a search snippet could be read (a 403 on
the page), or nothing confirmed a detail, the row says so.

| App | Presets | Toolbar | Creating and editing |
|---|---|---|---|
| Zotero 7 reader | None. Highlight and Underline are separate tools, each with its own colour from a fixed eight. | One button per tool and a colour button above the document; a popup over a selection. | Nothing to create. An annotation's ⋯ changes its colour; switching highlight and underline was planned in 2024. |
| PDF Expert | Highlight, Underline and Strikeout tools, each with its colour from five slots; the Mac's Underline switches to a squiggle. | Toolsets on iPad (at most three in portrait), a Favorites toolset. | Favorites: + → Add tool, set its colour and opacity; drag to reorder, minus to remove. |
| Acrobat | None. One default look per markup type. | A floating Quick action toolbar; the markups overlapped there (a 2023 guide). | Change the default from the tool, or from a mark's Properties ("make current properties default"). No second copy of a tool found (unconfirmed). |
| GoodNotes 6 | Colour presets per tool, three to twelve, beside thickness presets; text highlighting shares the highlighter's. No named favourites. | The armed tool's slots inline in the toolbar; tap a tool twice for its settings. | Tap a slot to use it, tap the selected one again to edit it; add, reorder, remove per tool (from search snippets). |
| Notability | Version 10's Favorites toolbar held 8 pen, highlighter and eraser styles; version 14 made duplicated tools buttons of the Toolbox. | A floating Toolbox, each duplicate its own button. | Duplicate a tool, drag to reorder, hide it to a Hidden Tools list that brings it back. |
| MarginNote 4 | Excerpt tools are named presets: a name in the toolbar, a colour on the icon's corner, a style, tags it applies. | Each tool a button in the document toolbar, its settings under it. | Clone a tool to make one; 12 on the Max tier, 8 on Pro. Reordering not described (unconfirmed). |
| LiquidText | A fixed set of colours per tool, no custom colours, no underline (a 2024 review). | A fixed top toolbar. | Nothing beyond picking a colour. |
| Skim | Note types (Highlight, Underline, Strike Out and others), one default colour each; a Favorite Colors toolbar item recolours a note. | The macOS toolbar; keys switch the note type. | Customize Toolbar; drag colours onto Favorite Colors. |
| Xodo | Style presets per annotation tool, in a preset bar beside the toolbar. | The annotation toolbar and its preset bar. | Edit a preset in the style picker. How many per tool, and whether a tool can sit twice on Favorites: unconfirmed. |
| Foxit PDF Editor | Separate Highlight, Underline, Squiggly, Strikeout buttons; a Format panel with colours and opacity. | The ribbon's Comment tab, Keep Tool Selected. | "Set current properties as default" per type. |
| Readwise Reader | One highlight colour; tags instead. Selection highlights at once; H, T, N on the keyboard. | A bar on a highlight. | Tags made as typed. |
| Hypothesis | One colour (asked for since 2017, not built as of 2024); tags instead. | A two-button adder over a selection. | None. |
| Apple Books | Five colours and Underline in the selection callout. | The system callout. | None; switching to underline is on a highlight's menu. |
| Kindle | Four highlight colours (a fifth reported in 2026, unconfirmed); the next highlight takes the last colour. | The selection menu. | None. |
| Drawboard PDF | Swatch and thickness presets per tool; Text highlight, Underline, Squiggle, Strikethrough separate; Add to Favourites saves a set-up tool. | The Markup toolbar and a Favourites toolbar (placement unconfirmed: 403). | Add to Favourites from the tool. |
| GuruMind (the issue's reference) | A tool per style (Highlight H, Underline U, Wavy W, Strikethrough S), ten colours and a custom one each, its key changeable; *Copy tool* makes a new instance with the same settings. | A top toolbar, each instance its own button, ⋯ at the end. | Long-press or right-click a tool: its colour, key, Copy tool, Delete tool; at least one tool stays. |
| 知云文献翻译 | Highlight, strikethrough, underline and wavy line among the annotation tools; the quick bar on a selection offers highlight and underline only. | An annotation toolbar in groups, a quick bar on a selection. | Change a mark type's default. |
| WPS PDF | Underline, strikethrough and wavy line icons under 批注 (some versions group them as 线型标注), each with its own default colour. | The ribbon tab and a properties panel. | Change a tool's default; only new marks follow. |

Sources: the Zotero reader's support page and forum threads 112012 and
others; Readdle's support articles on highlighting and Favorites; Adobe
Community threads on keeping the highlighter selected; GoodNotes article
7353743265039 (snippet); Ginger Labs' "Customize your Toolbox"; the
MarginNote 4 manual's excerpt tools; beingpaperless.com's LiquidText and
Notability 14 reviews; the Skim wiki and manual; Xodo's getting-started
article; Foxit PDF Editor 13's Comment manual; Readwise's highlights FAQ;
hypothesis/product-backlog#198; Apple's iPad guide (snippet); clippings.io
on Kindle colours; Drawboard's Favourites article (snippet); GuruMind's PDF
annotation guide; a sohu.com and a cnblogs.com walk-through of 知云; wps.cn's
article on line annotations.

## Patterns

- **Two ways in, and both matter.** Selection first (a popup on the text:
  Zotero, Acrobat, Apple Books, Kindle, Hypothesis, 知云, GuruMind) and tool
  first (arm a tool, then select: every PDF editor and GuruMind).
- **The style is the tool and the colour its property.** Separate Highlight,
  Underline, Squiggly and Strikeout tools, each remembering its colour, are
  the norm in PDF apps.
- **Named style-and-colour presets are rare, and where they exist they are
  tools**: each a toolbar button (MarginNote's excerpt tools, GuruMind's
  copied tools, Notability's and Drawboard's favourites), never a third
  layer of chips beside free style and colour pickers, which is what Gamma
  had shipped.
- **Selection popups stay one short row**: Zotero eight colours and a
  toggle, Apple Books five and Underline, Kindle four, Hypothesis two
  buttons, 知云 highlight and underline. Anything richer is on the tool.
- **Configuration happens on the button**: tap the armed one again
  (GoodNotes, Xodo, Gamma's own pen strip), or long-press / right-click it
  for its panel with Copy and Delete (GuruMind); duplicate to make another.
- **Presets are saved from the state at hand, not built in a form**:
  Drawboard's Add to Favourites, PDF Expert's star, "make current
  properties default".
- **Two memory models**: last-used (Kindle, Zotero, Acrobat, and Gamma's
  released highlighter) and fixed favourites that do not drift when used
  (Drawboard, PDF Expert, Xodo, GuruMind's instances).
- **Toolbars cap the favourites** and overflow the rest: Notability 8,
  Drawboard Basic 7, Xodo's desktop Favorites 9, MarginNote 12 or 8, PDF
  Expert three toolsets in portrait.
- **Keys per preset** are expected by power users: GuruMind's per tool,
  Zotero's planned number keys, Readwise's letters, Gamma's pen strip 1–9.
- **Restyling a mark after the fact is uncommon** and arrived late (Zotero
  planned it in 2024, Apple Books switches to underline); Gamma's in-place
  restyle that keeps the note is worth keeping.

## What Gamma took

- A **text highlighter** is a style and a colour, named if you like, and
  the presets *are* the highlighters: one list, its first four buttons on
  the viewer's strip (GuruMind's instances, Notability's duplicated tools),
  the same ones as glyph buttons in the selection popup, keys 1–9 in list
  order.
- **Fixed, not last-used**: a swatch or a style picked in the popup, or the
  bar over a highlight, changes that highlight and never a highlighter.
- **Configuration on the button**: a strip highlighter's right-click or
  held finger opens its menu (its name, style and colour, move, Duplicate,
  hide, Hidden highlighters, Remove), as the pen strip's Tool menu reads.
- **Saved from what is at hand**: *Save look as highlighter* on a highlight
  and on the bar over a new one; *Duplicate* on a strip button; the issue's
  three meanings (Definition, Question, Disputed) as one press.
- **A one-row popup** again: the colours, the highlighters, a fold with a
  one-shot style, the colour group and the way to Settings.
- **A cap of four on the strip**, the rest hidden and a menu away, so the
  vertical strip fits a short viewer, which scrolls past that.

## Left open

- A colour remembered per style for a one-off line (Zotero, Acrobat, Foxit
  and WPS keep one per tool).
- Recording which highlighter made a highlight (`properties.preset`), so a
  later edit of the highlighter does not restyle what it made and the notes
  can group by meaning, as MarginNote's tool tags and the tags of Readwise
  and Hypothesis do.
- Whether a highlighter armed should commit without opening the note, which
  would let its keys act straight after a mark.
- A screen-reader check of the name field inside a strip highlighter's menu.
