# One colour palette for highlights, pens and text boxes

What Gamma's code did with colours before the palette existed, and why the
palette stores concrete colours rather than slots. The mechanics as built
are in [dev/settings.md](../dev/settings.md) "Colors", [dev/handwriting.md](../dev/handwriting.md)
and [dev/api.md](../dev/api.md) "The highlight shape".

## What was there (October 2026)

Two colour systems that never met:

- **Text highlights.** Four `rgba(…, 0.65)` strings hard-coded in
  `shared/model/highlightColors.js`. The selection popup showed all four
  and clicking one both picked the colour and made the highlight; the
  string was stored verbatim in the block's `properties.color`. There was
  no "current colour" and nothing remembered the last one used. Dark
  pages worked by reverse lookup: the viewer squashed the stored string,
  matched it against the four, and tagged the element with the index so a
  CSS block could swap in a hand-tuned dark set. Any string that was not
  an exact match — every imported colour — got no dark treatment. The
  Logseq importer mapped colour names onto the exact four strings and the
  Logseq exporter mapped them back by exact string match, yellow for
  anything else. PDF export parsed the string into RGB plus alpha.
- **Ink.** Fourteen pen and eight highlighter hex colours in `ink/ink.js`,
  with the highlighter's opacity a separate constant rather than baked in,
  plus a browser colour picker for any hex. A stroke stored its own hex;
  a preset (up to 12, in localStorage) its own. Colours had names through
  a nearest-match table, so a swatch read "Pink highlighter".

So the two disagreed on hue (highlight yellow `rgb(255, 226, 143)` against
highlighter yellow `#fde047`), on encoding (rgba with baked alpha against
hex plus opacity), on dark pages (exact-string index against nothing) and
on names (ink had them, highlights did not).

## The decisions

- **One module, two roles.** A wash set of light colours for text
  highlights and highlighter strokes, an ink set of fourteen dark colours
  for pens and text boxes. Pens genuinely want dark tones and washes light
  ones, so "one palette" means one named source with two roles, not one
  flat list. The owner asked that the existing highlight colours not
  change, so the Highlight group is the four classic pastels, and the
  tool strip's highlighters keep their own Ink highlight group: those
  four and the four more they always had (pink, peach, cyan, light gray),
  at the stroke opacity. Text highlights and ink highlights are different
  things to the user — a mark on selected text, a freehand stroke — so
  they get different default groups rather than one shared list.
- **A stored colour is concrete, not a slot.** A highlight keeps
  `#rrggbbaa`; a stroke keeps hex plus its opacity. The alternative — a
  highlight naming a palette slot, resolved at render — was rejected: a
  shared workspace would show each member their own colours, every export
  and the MCP server would need the palette to resolve a colour, and a
  redefined swatch would silently recolour years of highlights. The cost
  is that changing a swatch affects only what is made from then on, which
  is also what the settings pane says.
- **Hex with alpha, not rgba.** One spelling for everything that stores a
  colour with transparency; the ink files already stored hex. The CSS
  readers, the PDF export and the Logseq export all read both spellings,
  since an older server, an old `.gamma` export or another app can still
  send the old one. A workspace migration step (37) respells stored
  colours so that data, exports and the MCP server carry one format; a
  `.gamma` re-import goes through the same step on its copies.
- **Dark pages derive the colour.** A function keeps the hue, raises the
  saturation, pulls the lightness to the middle and lowers the alpha. This
  replaces the hand-tuned four-entry CSS set, and gives imported colours
  the same treatment. The exact shades differ slightly from the old
  hand-tuned ones (which were the Tailwind 400 shades of the Tailwind 300
  highlighter hues); the contrast target, about 7:1 for light text over
  the wash, is the same.
- **Names come from a fixed reference table**, nearest-match, including
  the hues the old highlighter set had so existing presets keep their
  names. A user's own swatch is named after whatever it is closest to,
  rather than inheriting the name of the slot it replaced.
- **The palette is one account preference.** It started as `{wash, ink}`
  with fixed slot counts; the owner then asked to add and remove colours,
  name groups, choose which colours each picker shows, and set alpha. So
  it became `{groups, pickers}`: named groups of `{hex, alpha}` swatches,
  and per picker (text highlights, highlighter presets, pen presets, text
  boxes) the one group it shows, picked from a dropdown. Selection is at
  the group level rather than a checkbox per colour per picker: a user
  composes a group for a picker instead, and the settings pane stays four
  rows. A list of groups per picker was tried first; one group per picker
  is what the owner asked for and is simpler to read. The old shape still
  reads and becomes the default groups.
  Each swatch's alpha is what a text highlight stores and what a pen or
  highlighter preset takes as its opacity when the swatch is picked; a
  text box stays opaque, as the server validates its colour as plain hex
  and a translucent label reads badly over a page.

## Left out

- A remembered last-used highlight colour. Nothing consumes a default
  today: the popup's click both picks and creates.
- Per-colour selection inside a picker, and reordering groups or
  swatches by drag. Deleting and re-adding is the way to reorder.
- Alpha for text boxes.
