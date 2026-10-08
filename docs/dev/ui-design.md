# UI design conventions

The rules that keep the frontend looking like one product. New UI follows
them instead of inventing new patterns.

## Tokens

[`shared/styles/tokens.css`](../../frontend/src/shared/styles/tokens.css)
is the one place a design value is defined: every theme's colours and
`color-scheme`, the type stacks and sizes, the control sizes, and the
radius, shadow, spacing, motion and stacking scales. It holds custom properties only, no component
rules. `index.html` links it as the first stylesheet, and Vite bundles it
into the render-blocking entry CSS. So the first frame, splash included,
paints from the same values as the app; the pre-paint script only sets the
theme attributes. Component rules read tokens and never declare a colour
token (`tests/themes.test.mjs`). A component-local custom property
(`--co-color`, `--peer`, `--notice-tone`) stays with its component.

### Colour

A theme's block names a handful of seeds: `--ground` (the content surface),
`--ink` (body text), `--accent`, `--danger`, `--ok`, and optionally
`--accent-fill` (the primary button), `--on-accent` and the callout colours.
Every other colour comes from one of two recipes, `color-mix()` in oklab of
the seeds. `--warn`, the amber of warnings and conflicts, is one value in
every theme, with its tint `--warn-bg`. The light recipe steps each surface, border and quieter text from
the ground toward the ink. The dark one takes chrome and wells toward black,
and raised surfaces toward the ink.

The root carries `data-scheme="light|dark"` beside `data-theme`, and the
scheme picks the recipe (`DARK_THEMES` / `themeScheme` in `app/prefDefs.js`).
Three places set both attributes: `index.html`'s pre-paint, App.jsx's theme
effect and the desktop shell. A rule that differs by scheme keys on
`[data-scheme="light"]`, never on a list of themes. The recipes match any
element carrying the attributes, not only the root, so a subtree can show
another theme in its own colours: the Appearance pane's swatches do.

The recipes are `:where()` rules, so a value a theme names always wins. Their
percentages are fitted to Light and Dark. Under its seeds, a theme lists the
hand-tuned values the recipe would change visibly (more than 0.006 in oklab,
about two steps of 255). A new theme is its seeds and nothing else.

Most tokens are therefore `color-mix()` expressions. Read in JS, a token
gives that expression (or, through `getComputedStyle`, an `oklab()` string).
Code that needs a plain colour resolves it on a probe element and reads one
canvas pixel back (`tokenHex` in App.jsx, for the theme-color meta). The
pre-paint script runs before tokens.css loads, so for the first theme-color
it carries each theme's `--bg-page` as a hex, which `tests/themes.test.mjs`
holds to the tokens (as it does the desktop title bar's colours).

### Surfaces

Each region paints one surface role, so notes, chat and the library read as
one app inside one frame:

| Role | Token | Where |
|---|---|---|
| Chrome | `--bg-page` | the topbar and its tab strip, the Settings rail, collapsed dock bars, the phone's bottom bar, the desktop shell's bar |
| Content | `--bg-surface` | the library, the notes page, every dock (notes and chat alike), dialogs — in a light theme the lightest thing on screen but the chat composer's field |
| Quiet fill | `--bg-block` | row hover, code, table heads, a chat bubble |
| Selected | `--bg-selected` | the focused block: the accent at 6 % over the content (11 % in the dark scheme) |
| Sunken | `--bg-deeper` | wells; the PDF surround in the tinted themes |
| Floating | `--bg-elevated` + `--shadow-2` | popovers, menus, tips, the guide card |
| Field | `--bg-field` + `--border-light` | a text box that must stand out on the content — the chat composer: toward white in a light theme, toward the ink in a dark one |

The active tab is the content surface cut out of the chrome: no shadow, and
a band under it (`.tab.active::after`, over the topbar's padding and bottom
border) joins it to the page below. `--bg-elevated` is "what floats", not
"lighter than the page": in a light theme it equals the content. So a
control that has to stand out on content (a card, a field, a secondary
button) carries a border, and a state that has to show on it (the focused
block) uses `--bg-selected`.

### Type

- **One interface face**: Inter, bundled (`@fontsource-variable/inter`,
  imported in `main.jsx`). It is one variable woff2 per unicode subset,
  upright and italic, served from `/assets/` with the bundle and fetched only
  when text in its range shows. No third-party request, so it works offline
  and in the desktop sidecar. `--font-sans` names it with system and CJK
  fallbacks (Chinese text keeps the OS face). `body` uses it, and form
  controls inherit the family from a zero-specificity rule.
- **One monospace stack**, `--font-mono`: code, raw math, key caps, cron
  fields, workspace folders. Bare `code`/`pre`/`kbd`/`samp` get it from a
  zero-specificity rule.
- A component rule never names a font family; it uses one of the two tokens.
- Counts, times and page numbers use `font-variant-numeric: tabular-nums`
  (the grouped rule under `body` in app.css), so they keep their width as
  the digits change.
- **Eight sizes, each a job** (`--fs-*`). A font size is one of them, never a
  pixel value. Relative sizes (`em`, headings inside rendered notes) and
  `inherit` are fine:

  | Token | px | Job |
  |---|---|---|
  | `--fs-2xs` | 11 | captions, badges, kind labels, key caps — nothing smaller |
  | `--fs-xs` | 12 | meta, hints, section labels, tabs, compact (`sm`) buttons |
  | `--fs-sm` | 13 | the default: buttons, menus, dialog fields |
  | `--fs-md` | 14 | list rows, the settings rail, text fields, chat messages (times `--text-scale`) |
  | `--fs-body` | 15 | the notes' reading text (times `--text-scale`) |
  | `--fs-lg` | 17 | pane and section titles |
  | `--fs-xl` | 20 | the page title |
  | `--fs-2xl` | 24 | the largest headings (sign-in, the phone library's title) |

  Text that follows the panel's Ctrl+scroll size multiplies in place:
  `calc(var(--fs-body) * var(--text-scale, 1))`.
- Weights: 400 for text, 500 for controls, 600 for headings, labels and
  active states; 700 only for **bold** in notes. Line heights: `--lh-tight`
  1.25 (headings, one-line controls), `--lh-ui` 1.4, `--lh-read` 1.55
  (reading text).

### Scales

A component takes its corner, depth, spacing, control size, timing and
layer from these scales instead of a number of its own:

| Scale | Tokens | Use |
|---|---|---|
| Radius | `--radius-xs` 4 · `-sm` 6 · `-md` 8 · `-lg` 12 · `-full` | xs: tags, inline code, key caps, small marks; sm: icon buttons, menu and list rows, chips; md: buttons, fields, popovers, menus, floating toolbars; lg: cards, panels, dialogs, the settings modal; full: pills. A circle stays `50%`, a hairline bar 1–2 px |
| Shadow | `--shadow-1` · `-2` · `-3` | 1: raised controls (buttons, chips, the find box, the composer); 2: what floats (popovers, menus, tips, the guide card, floating toolbars and pills, a hovered card); 3: dialogs and the lightbox. Each scheme defines its own: the dark one shades with black, the light one with a little of the theme's ink. Rings (`0 0 0 Npx`) and inset accent bars are borders, not depth, and stay with their component |
| Spacing | `--space-1`…`-8` = 2 · 4 · 6 · 8 · 12 · 16 · 24 · 32 | the shared controls and new rules. The ratchet below doesn't count spacing, so layout rules may keep their pixels |
| Controls | `--ctl-btn` 35 · `-btn-sm` 31 · `-icon-btn` 29 · `-icon-btn-xs` 24 · `-close-sm` 22 · `-close` 26 · `-close-lg` 31 · `-tab` 33 | a `uiBtn` and a `uiBtn sm` (an `iconSq` is that square, a topbar icon button a button tall); the frameless icon button (`ctlBtn`, the viewer's control boxes, search toggles); a compact button inside a line of text (find arrows, metadata rows, the chat's change list); the round × in its three sizes; a tab. One set for a mouse and a finger alike: no pointer media query changes them ([below](#one-behaviour-for-mouse-and-finger)). What sits around a control derives from them with `calc()` (a control box's width, a field's room for its eye button, the gap kept clear of the viewer's control boxes), so a size changes in one place |
| Motion | `--dur-fast` 100 ms · `--dur-base` 160 ms · `--dur-slow` 240 ms | fast: hover and press; base: enter, fade, small moves; slow: bars and larger moves. Loops and attention effects of half a second or more (spinners, pulses, the landed flash) keep their own period |
| Layers | `--z-dock` 20 · `--z-float` 30 · `--z-phone-overlay` 80 · `--z-editbar` 90 · `--z-dropdown` 100 · `--z-overlay` 1000 · `--z-popover` 1100 · `--z-modal` 1200 · `--z-subdialog` 1290 · `--z-confirm` 1300 · `--z-lightbox` 1500 · `--z-ctxmenu` 1600 · `--z-refpopup` 2000 · `--z-drop` 9999 · `--z-tip` 30000 | every z-index above 9 is a layer token (one step above a layer is `calc(var(--z-dock) + 1)`); 1–9 only orders siblings inside one component |

**Reduced motion.** One rule in app.css's primitives layer honours the OS
setting. Under `prefers-reduced-motion: reduce` every transition and
animation ends at once (1 ms, one iteration). The spinners keep turning
slowly (2.4 s), so a wait never looks like a hang. A component needs no
reduced-motion rule of its own unless it wants a different still state.

### Ratchets

Two node tests hold the tokens in place. Each may only get better.

- **Contrast.** `node tools/themes.mjs` resolves tokens.css per theme the
  way a browser does. It prints text contrast for the pairs that matter:
  text on surface, page and hover; faint text; the accent; the primary
  button's label. `tests/theme-contrast-baseline.json` records the pairs
  below WCAG AA. `tests/themes.test.mjs` fails when a change lowers any
  ratio or makes a passing pair fail. After a deliberate improvement,
  `node tools/themes.mjs --baseline` records the new floor.
- **Raw values.** `tests/designTokens.test.mjs` scans every stylesheet
  under `src/` (tokens.css aside) for values that should be tokens: a
  colour literal, a pixel font size, a corner radius over 2 px, an
  elevation shadow, a z-index above 9, a transition under half a second. It
  fails when a file holds more of one kind than `tests/design-baseline.json`
  records, and lists the new offenders. What is left is mostly the PDF
  page's own colours. After a cleanup,
  `node tools/designTokens.mjs --baseline` records the lower floor.

A value that is raw on purpose says why in a comment the scanner reads:
`/* ds-allow: why */` on its line, or `ds-allow-start: why` …
`ds-allow-end` around a block. Examples: the syntax and highlight palettes,
the collaborator colours, the transfer illustration, a transition that
follows the pointer.

### The desktop shell and the extension

The desktop shell's chrome and the browser extension's popup and options
page load their pages from disk and can't reach the frontend bundle. Each
keeps a committed copy of tokens.css and the Latin subset of Inter
(`desktop/ui/tokens.css` + `desktop/ui/fonts/`, `extension/tokens.css` +
`extension/fonts/`). `npm run copy-tokens` in `frontend/` refreshes them,
and `tests/themes.test.mjs` fails while a copy differs from its source.
`desktop/ui/theme.css` and `extension/popup.css` repeat this page's control
recipes (buttons, icon buttons, fields, the switch, the focus ring), and
their pages follow the same type, radius, icon and label rules. The
extension can't know the app's pinned theme, so `extension/theme.js` sets
Light or Dark from the OS before the stylesheets paint.

## One control set, everywhere

Reuse the unified classes; never invent a bespoke style for a control that
already exists. Bespoke CSS classes are for **layout only**.

| Class / component | Use for |
|---|---|
| `uiBtn` (+ `primary`, `ghost`, `danger`, `sm`, `iconSq`, `on`) | every button with a label, in the topbar too (there is no generic topbar button style); the hierarchy and sizes are under Buttons below |
| `ctlBtn` / `ctlBtnRow` / `pdfCtlBox` | the flat `--ctl-icon-btn` (29 px) icon buttons (a 16 px icon) of the PDF zoom column: `pdfCtlBox` = the elevated vertical box, `ctlBtnRow` = the same buttons laid flat with no box (chat header), `modeActive` = on; `pdfCtlGroup` keeps a run of a box's buttons together (a guide anchor) without changing the layout. The zoom column of the PDF and the notebook viewer starts with the same buttons in the same places (zoom out, zoom in, fit to width, the pen); what only one viewer has goes after them. **`ctlBtn` (frameless) is the DEFAULT style for any icon button** — new icon toolbars (e.g. the image tools) use it, not bespoke button styles |
| `uiClose` (+ `uiCloseSm`/`uiCloseLg`) | every × close button: round, `--ctl-close` 26 px (Sm 22 for chips and rows, Lg 31 for dialogs and panes) |
| `MenuButton` ([Menus.jsx](../../frontend/src/shared/ui/Menus.jsx)) | a row's or a card's "⋯": the visible way into the menu its right-click or held finger opens ([below](#one-behaviour-for-mouse-and-finger)) |
| `aiKeyInput` | every text/number/password input in dialogs and settings |
| `switch` / `switchTrack` | every on/off toggle |
| `MenuSelect` / `ActionMenu` ([Menus.jsx](../../frontend/src/shared/ui/Menus.jsx)) | every dropdown: Codex-style pill trigger + checkmarked `ContextMenu`. No native `<select>` anywhere |
| `MenuItem` / `MenuLabel` / `MenuDivider` / `SubMenuItem` ([Menus.jsx](../../frontend/src/shared/ui/Menus.jsx)) | every row inside a menu, context or popover: icon column + ellipsizing label (+ `danger`, `trailing`, `keys`); `MenuDivider` between groups. `SubMenuItem` is the nested flyout — hover-opened, safe-triangle guarded; a popover menu hosts one through `MenuScope` |
| `categoryTag`, `uiTag` | chips and small badges |
| `KeyCaps` / `KeyBinding` ([SettingsKit.jsx](../../frontend/src/settings/SettingsKit.jsx); `KeyCaps` lives in [shared/ui/KeyCaps.jsx](../../frontend/src/shared/ui/KeyCaps.jsx), shared with the guide) | a keyboard chord as `.keyCap` key caps; `KeyBinding` is the rebindable version (click, press the new keys; reset button when changed) — [hotkeys.md](hotkeys.md) |
| `popoverAnchor` | the `position: relative; inline-flex` wrapper every popover trigger sits in (`data-popover="…"` on the same element) — never inline that style |
| `Section`'s `scope` tag (`.setScope`, [settings.md](settings.md)) | where a settings section's values live: a 14 px icon and one muted word in the small caption size, "account" or "browser". An account tag's icon is the sync state of that section's own settings (check, cloud-check, spinning refresh, warning — the only colour); the sentence is the hover `title` and `aria-label`, never text |

Mermaid diagram previews in notes and chat use the shared component and
toolbar described in [mermaid.md](mermaid.md). The toolbar is the note
image's strip of `ctlBtn` icon buttons, shown on hover and while the
object is selected, and the diagram resizes with the same two-sided grips
as an image (`shared/ui/ResizeGrip.jsx`).

### Buttons

One hierarchy, the same on every surface:

| Class | Look | Use |
|---|---|---|
| `uiBtn primary` | solid accent fill | the one main action of a surface (Save, Copy link, Invite, Log in) |
| `uiBtn` | the secondary button: a bordered rect on the elevated surface with `--shadow-1` | every other action |
| `uiBtn ghost` | no frame or fill until hover | tertiary actions: a toolbar's text button, "Set up AI…" |
| `uiBtn danger` | outlined, red at rest | a destructive action beside others (Delete…, Leave…, Stop sharing, Remove) |
| `uiBtn primary danger` | solid red | the confirming click of a destructive action |

Sizes are the control tokens ([Scales](#scales)): the default is
`--ctl-btn` (35 px) tall, `sm` `--ctl-btn-sm` (31; settings rows, popovers,
toolbars); `iconSq` is a square at the same heights. `ctlBtn` is the
frameless `--ctl-icon-btn` (29 px) icon button, `uiClose` the round × (22 /
26 / 31). Buttons and fields
take `--radius-md`, icon buttons and chips `--radius-sm`. States: hover
`--bg-hover`, pressed `--bg-active`, `on` (a chosen option) `--bg-selected`
with an accent border, disabled the faint text colour with no lift (a filled
button fades as a whole). Native checkboxes, radios and ranges take the
accent from one `accent-color` on the root.

### Icons

Icons are the one stroke set in `shared/ui/Icons.jsx` (lucide-style, a 24
grid). Three sizes: **14** inline with text (chips, menu rows, a label's
glyph), **16** in buttons and toolbars (the default; an `iconSq` or `ctlBtn`
button's icon), **20** in pane headers, empty states and card tiles; 48 is
for illustrations. A few 9–10 px glyphs stay where they sit inside a badge
or a chip smaller than the text (a token count's arrows, a chip's folder).
The stroke is drawn at 1.6 px whatever the size: `Icon` sets `strokeWidth`
to `1.6 × 24 / size` (`iconStroke`), so a 14 and a 20 beside each other look
equally heavy; a check mark is a touch heavier (`weight` 2). Pass
`strokeWidth` only for an icon sized by CSS (`size={null}`). A close button
is `uiClose` with an `XIcon` inside (14, inline with the title it closes;
16 in `uiCloseLg`), never a text ×.

### Section labels

Panel and section names (dock titles, Library, Recently viewed, Pinned, the
popover, search and menu section captions, the Keyboard pane's groups) share
one rule in app.css: sentence case, `--fs-xs`, weight 600, `--text-secondary`
(dock titles a step up, `--fs-sm`), the Settings rail's look.
Labels get no `text-transform: uppercase` or letter-spacing: the text keeps
its own case, so only acronyms ("PDF") are capitals. A new label class joins
that list and keeps only its layout in its own rule. The kind label on a card
("Page", "PDF") is the small muted version: `--fs-2xs`, weight 500,
`--text-muted`; in the list it is a plain muted column.

### Keyboard focus

- **One ring**: every button, link, summary, `role="button"`, checkbox,
  radio, range and tabbable element shows `outline: 2px solid
  var(--focus-ring)` (the theme's accent) at a 2 px offset on
  `:focus-visible`, so mouse clicks never show it. The rule sits in the
  primitives layer of app.css with only `:focus-visible`'s specificity:
  a widget that draws its own focus overrides it (the switch's track, the
  key chip, rows that highlight like `.chatHistRow`). Rows that fill a list
  (menu items, popover items, search results, the Settings rail) draw it
  inside (`outline-offset: -2px`), where the list can't clip it.
- **Fields** show focus as the accent border plus a 3 px `--accent-bg` halo,
  on click and keyboard alike. The field list is next to the ring rule. A
  field inside a framing box (`.searchInputWrap`, `.homeFindBox`,
  `.pdfPageWidget`, `.chatComposer`) leaves the halo to the box
  (`:focus-within`). A new field joins that list instead of writing its own
  `:focus` border.
- **Hover-revealed controls reveal on focus too.** Every rule that shows a
  control on hover (`opacity`/`visibility`) has a twin for the control's own
  `:focus-visible` (or the row's `:focus-within` when the row holds the
  focus), so the ring never lands on something invisible. Where nothing
  hovers the control is shown at rest instead
  ([below](#one-behaviour-for-mouse-and-finger)).
- `outline: none` only on an element that has its own focus style, or on a
  borderless inline editor where the caret is the focus (the block editor,
  the chat bubble edit, the label input, table cells, which are outlined
  while edited).

### Interface size and text size

**Interface size** (Settings → Appearance, `gamma-ui-scale`, a `Stepper`
over the `UI_SCALE` range in `app/prefDefs.js`, 70–160 % in 10 % steps)
scales both interface text and controls. `index.html` applies the stored
value before first paint and App.jsx keeps it in sync. PDF page geometry and
its text layer keep their own zoom.

- The `--fs-*` sizes multiply by `--ui-font-scale`, inherited from
  `--ui-scale`.
- Controls (buttons, summaries, button roles and the shared control classes)
  use CSS `zoom` for their box, text and icon. They reset `--ui-font-scale`
  to 1, so the text scales once. Nested controls reset `zoom` to 1 as well.
- A custom property resolves its `var()`s where it is declared, so tokens.css
  declares the `--fs-*` sizes a second time on the same control selector. A
  control's `var(--fs-sm)` is then 13 px before the zoom.
- A control that takes its parent's type writes `font-family: inherit` (and
  weight, line height), not `font: inherit`. The shorthand would copy the
  parent's already scaled size and zoom it a second time.
- A box that holds controls (a popover, a menu) keeps its pixel widths
  while its rows zoom. Where the rows have to fit, the width multiplies by
  `--ui-scale` (the account menu's `min-width`), and a row gives up its
  least important text first: the workspace row ellipsizes its meta, never
  the name.
- PDF-coordinate positioning stays on an unscaled wrapper with the control
  inside it (`pdfNoteAnchor` / `pdfNoteBadge`). Zooming the positioned
  element would scale its offsets too, and it would drift off the highlight.

**Text size** is per panel and per session: Ctrl/⌘+scroll over the notes
list or the chat transcript. `useTextScale` (`shared/ui/Widgets.jsx`) owns
it, as a native non-passive wheel listener: React's `onWheel` can't
`preventDefault`, and the browser would zoom the page. Each ~40 px of
accumulated delta is one ×1.1 step, so mouse notches and trackpad pinches
both land on whole steps. It is clamped to 0.6–2.5 and snaps back onto
100 %. The scale goes on the panel as the `--text-scale` custom property,
which the base font sizes multiply in: `.blockRendered`, `.blockEditor`,
`.blockEditorCm .cm-scroller`, `.chatBubble`. A transient `.textScaleBadge`
pill (the panel's first child, sticky, zero height) reads out the
percentage. Nothing is stored: reload resets it. On the home library the
gesture is left to the browser.

### Zoom gestures in a viewer

The viewport meta turns the browser's own zoom off, so a viewer that zooms
reads the gestures itself. Both gestures come from one place,
`shared/lib/viewerZoom.js`, so the PDF's pages and the notebook's sheets
zoom the same way. The `wheel` listener is native and non-passive:
React's root listener is passive, so the `preventDefault` that stops the
browser zooming the page would not work from an `onWheel` prop.

**Ctrl/⌘ + wheel** (also what a trackpad pinch reports) zooms by
`e^(-deltaY · 0.0015)` per event, so a notch is a fixed ratio whatever the
device reports in pixels. Firefox's line-mode deltas are converted first.
The scale compounds on the module's own `live` value, not on the committed
scale, which is a frame or two behind. The dispatch is coalesced to one per
frame, because every commit re-renders every page. A viewer pushes its
committed scale back in with `sync()`. That is ignored while a dispatch is
in flight: the events that arrived since are already in `live`, and
overwriting it dropped them (a 6-notch train zoomed about 3 notches' worth).

**Two fingers** pinch to zoom and drag to pan. The pinch does not commit
while they are down: it previews as a CSS transform on the content layer,
compositing only, so it is blurry until they lift, like every native PDF
app. A commit per move event is more re-layout than a tablet keeps up with.
The layer must be the scroller's first in-flow child with
`transform-origin: 0 0`, which is what the math assumes. `preventDefault`
on the two-finger move blocks native scrolling along with the browser's
zoom, so panning is the caller's job too: fingers held the same distance
apart are a drag, and the commit gets the midpoint's travel to move the
view by.

Each viewer supplies only the two commits, since anchoring depends on its
layout. The PDF's re-bases scroll and lets its zoom-anchor effect re-place
the point (`pdf/PdfViewer.jsx`). The notebook's names the sheet and the
fraction of it to hold ([notebooks.md](notebooks.md)).

### Fullscreen on touch devices

The fullscreen button asks for native fullscreen first. App fullscreen
(`.app.pseudoFullscreen` + `html.appFocusFullscreen`) is the fallback when
the Fullscreen API is missing (iOS Safari) or rejects the request. It hides
the app bars, confines overscroll, and exits through the same button or
Escape; the browser's own bars may stay visible. The button handles a
stationary touch release itself, because a mobile browser may omit the
compatibility click after a scroll. The click that does follow is consumed,
so one tap cannot toggle twice. Mouse and keyboard keep the plain click path.

### Menus and submenus

Every cursor-anchored menu is a `ContextMenu`; every row inside one is a
`MenuItem` (icon column, ellipsizing label, optional `trailing` node or
`keys` — the row's shortcut, e.g. the page menu's Enter / F2 / Del —
`danger` for destructive actions). Groups of rows are separated by
`MenuDivider`; `menuGroups(...groups)` drops empty groups and puts one
divider between the rest, so a menu a viewer sees half of has no doubled
rule. Popover menus use the same rows: the topbar's account, View and Add
menus and the page header's Document popover. A popover that hosts a flyout
renders its panel as a `MenuScope` (the flyout state a `ContextMenu`
carries), so the account menu's **Tours** is a `SubMenuItem` like any other
(`data-guide="account.tour"` on its trigger, `data-tour` on its rows). A
click on a flyout's trigger opens it and never toggles it shut. A row that opens a nested list is a
`SubMenuItem` — it renders its panel *inside* the parent menu's DOM (a
portalled panel would sit outside the parent's outside-pointerdown test, and
the parent would dismiss itself before a click on a flyout row could land),
flips to the other side and clamps vertically when the viewport is tight.
In the compact shell's More sheet a flyout opens inline under its row
instead (`.phoneBottomBar .popover .ctxSubMenu`): beside it, the sheet's
own scroll box would clip it. An open menu takes Escape first, on the
capture phase. So Escape in a dropdown inside a dialog or a popover closes
only the dropdown.

A menu that lists commands lists their ids: `CommandMenuItem`
(`app/CommandMenuItem.jsx`) draws a catalog command ([hotkeys.md](hotkeys.md))
as a `MenuItem` with its label, its glyph (`app/commandIcons.jsx`) and the
key this account gave it, greyed while its `when` fails, and runs it with
the context its key runs it with. The View menu's Undo, Redo, Go to page and
Command palette are such rows: the way to them without a keyboard.

Submenus open on hover, and the hover-switching is guarded by
[menuAim.js](../../frontend/src/shared/ui/menuAim.js): while a flyout is open, a
pointer move that stays inside the triangle from the cursor's recent position
to the flyout's near edge counts as "aiming at the flyout", and the hover
change it would cause is held until the aim breaks or the cursor stops. That
is what lets a diagonal move into the flyout pass over the rows below the
trigger without closing it. The module is plain geometry plus a `useMenuAim`
hook (`setTarget` / `guard` / `keep`) — any other menu surface can adopt it
without going through `shared/ui/Menus.jsx`.

### Dialogs

`.reportOverlay` › `.reportModal` is the one dialog surface (settingsKit's
`SubDialog` wraps it for the settings editors). Confirm-style dialogs — the
shared `confirmBox`, the external-link prompt — add a `.confirmHead`: an icon
chip (`.confirmIcon`, `.danger` for destructive) leading a title plus one
line of explanation, the same shape as a settings `PaneHead`, over the
right-aligned `.reportModalBtns` row. Escape closes them.

Destructive affordances all read from one set of tokens — `--danger`,
`--danger-bg`, `--danger-border` — so the solid confirm button
(`.uiBtn.primary.danger`), the outlined one (`.uiBtn.danger`) and a menu's
`danger` row are the same red in both themes. Never hardcode a red.

An irreversible action in a settings pane or editor dialog lives in its
**Danger zone**: settingsKit's `Section tone="danger"` (red label and rule,
the rows in a `--danger-border` box). Its buttons are `uiBtn sm danger`,
red at rest, and each row's hint says what is lost in one line ("Deletes
every page, PDF, chat and backup in it, for all 2 members; the server keeps
one final copy."). The workspace manager (Leave, Delete workspace) and the Users
account editor (Delete account) use it; a menu row that replaces data (a
backup restore) is a `danger` row.

### The status pill

App's `setStatus(msg)` logs a message (Settings → Diagnostics) and posts
it to the floating pill: one ending in "…" is in progress and spins,
anything else is final and fades after a second. `setStatus(msg, action)`
with an `action` `{label, run}` puts its button on the pill
(`.statusPill.interactive`), which then stays 8 seconds to be pressed.
`undoableStatus(msg)` is that with Undo, which runs the `app.undo`
command: a deleted note says "Block deleted." with Undo beside it, in
reach without a keyboard. A finger that scrolls a page while a tool is
armed and only a pen draws gets "Fingers scroll while a pen draws" with
**Draw with finger**, once a session and never after a pen has drawn
([handwriting.md](handwriting.md)).

### The share popover

`sharing/SharePopover.jsx` (the topbar link button) is the one place a page
is published. An open folder's link button opens the same popover with a
folder `target`: folder wording, no Citation or Gamma Cloud section. It is a popover under its button, like the account menu (App
wraps it in a `data-popover="share"` anchor, so the outside-click and Escape
rules close it; on phones the bottom bar's popover rule spans it across the
screen), built from the settings kit like the workspace Manage dialog.
The title names what is shared (Share "<page title>" / Share folder
"<name>"). Before a share exists the popover holds only the Link row — one
wrapping line and a primary **Share** button — then Gamma Cloud and the
citation: nothing is shared by opening it, and Share creates the share as
Signed in · View, the access controls appearing with it. `Section`s once
shared, top to bottom — Link (the address as the row hint, Copy link as the
primary button and Stop sharing beside it as a `uiBtn sm iconSq danger` —
the cloud link's Copy / Unpublish pair — opening the same inline
`mirrorConfirm`, naming how many invited people lose access), Who has access
(the invite box on top — a compact `AccountPicker` beside a primary Invite,
no modal in a popover; then `aiProvRow` rows: you, a shared workspace's
members as one row tagged *workspace*, each invited account with its own
View / Edit `Segmented` and a `uiBtn sm iconSq` remove), General access
(pictured, not described: three `IconChoices` tiles — Anyone / Signed in /
Invited only — and, as the section's action, one View / Edit `Segmented`,
disabled under Invited only; one summary sentence under the tiles is the
only prose, amber `.shareWarn` when a link is editable without sign-in).
Below them the page's Citation section (App.jsx owns it: a `citeHead`
label line, then a `CopyBox` — text with the copy button pinned top-right —
for the slide citation and for BibTeX; the section's action regenerates).
On a server with cloud sign-in a *Gamma Cloud* section sits above the
citation (`PublishSection`): Publish, or the published link with Copy and
an inline-confirmed Unpublish, the sync state line with Sync now, and the
cloud share's access as the same tiles and View / Edit toggle
([mirror.md](mirror.md) "What the person sees").
Every change saves at once; nothing is a bespoke control. There is no
"reset link": stopping and sharing again mints a new address. The read-only
view's top bar is the visitor's side of it: the brand mark (the way to the
server's front door; a folder share keeps its Back to the shared folder
button), the title, the access pill (`sharing/ShareAccess.jsx`: an eye or a
pencil with "View only" / "Can edit", then "· shared by" with the owner's
avatar initial and name, the sentence behind it as its hover title; phones
keep the icon and the role), "as <name>" for an editing visitor without an
account, and one action: Sign in for a visitor with no account, "Add to my
library" for a signed-in one, "Open in my library" for the owner.

### Sign-in and status pages

`AuthShell` in `auth/LoginPage.jsx` frames every page shown before or
instead of the app: the login (also a share link's sign-in gate), loading,
an unavailable workspace, a session conflict and a blocked share link. It
shows the brand mark (`shared/ui/BrandMark.jsx`, the favicon artwork) over the name, or over the
situation's headline on a status page. The card hangs at a fixed height near
the optical centre instead of being centred, so an error that appears
mid-form grows it downward and the fields never move. Log in is the one
primary button; Gamma Cloud is an outlined secondary above an "or use your
account on this server" divider. On a Gamma Cloud server (`cloud.lead` in
`GET /api/server-config`: a hosted container or the share host, where every
account signs in through Gamma Cloud) the cloud button is the primary
(`.loginCloudBtn.lead`) and the password form folds behind Admin sign-in,
as a demo server folds it behind Try the demo. A failed sign-in says why (wrong password,
too many attempts, server unreachable) in a `--danger`-tinted `.loginError`
box, and a refused password also takes the focus and a red border.

## One behaviour for mouse and finger

Every interaction a mouse has works under a finger through the same code:
no touch branch in feature code, and the same controls at the same sizes
on a desktop, a touch laptop and a tablet. A gesture goes through
`shared/ui/press.js`, a size or a reveal through CSS, and the few questions
neither can answer through `shared/lib/pointer.js`.

### Gestures: `shared/ui/press.js`

A right-click and a long press both ask for an item's menu; a double-click
and a double tap both ask for its second action. The browser cannot be left
to translate: iPadOS Safari sends no `contextmenu` for a long press, and
its `dblclick` for a double tap is not dependable. A call site spreads one
helper where it had `onContextMenu` or `onDoubleClick`, and its handler
never learns which gesture it was.

- **`menuPress(open)`**: a right-click, or a finger or pen held still for
  500 ms (`HOLD_MS`; it may wander 10 px). `open` is the handler
  `onContextMenu` had. For a hold it gets `pressAt(x, y, el)`, a stand-in
  carrying what a menu opener reads: the point, `currentTarget` and
  `target`, and no-op `preventDefault` / `stopPropagation`.
  - The click the lifting finger makes goes nowhere, so it never opens
    the item whose menu just appeared.
  - A draggable ancestor is not draggable for the length of the press:
    iPadOS would lift it at the moment the hold completes.
  - Android's own `contextmenu` for a hold already answered is ignored.
  - One press reaches every element around the finger, and the innermost
    one wins: a chip in a row keeps its own menu.
  - The element carries `data-press="menu"` (below).
- **`doublePress(run)`**: a mouse keeps the browser's `dblclick` and the
  system's double-click speed. A finger's or pen's two releases on the
  same element, within 400 ms and 30 px, are counted here, each once, by
  the innermost element it reaches. The browser's `dblclick` after a
  counted double tap is ignored.
- **Where they are.** `menuPress`: the library's rows, cards and chips,
  tabs, the Back button, highlights, their note badges and PDF links, the
  translate button, the note object frame, file chips, chat history rows.
  `doublePress`: the dock grip's fold, a diagram's source, a picture's
  zoom, a resize grip's natural size. A widget built outside React (the
  editor's picture and table widgets, `BlockCmEditor.jsx`) attaches the
  same handlers with `addEventListener`. `tests/press.test.mjs` covers the
  counting.

### Device questions: `shared/lib/pointer.js`

What press.js and CSS cannot decide, feature code asks here, rather than
reading `pointerType`, `matchMedia` or `navigator` itself:

- **`lastPointer()`**: the pointer of the last press, `"mouse"`,
  `"touch"` or `"pen"`, from one capture-phase `pointerdown` listener. A
  `click` does not say which everywhere. The library reads it (a tap
  opens where a click selects), so does a highlight's note badge (a tap
  opens its tip where a click jumps to the note), and so does the
  `selectionchange` path that gives a touch selection its highlight tip.
- **`touchTyping()`**: text is typed on an on-screen keyboard, because
  the primary pointer is coarse or the last press was a finger or a pen.
  Return is then a line break's only key, and Tab and the Ctrl chords are
  missing. The editing bar ([ipad.md](ipad.md#the-editing-bar)), the
  chat's Return key ([ai.md](ai.md#the-composer)), and a field whose `autoFocus` would
  raise a keyboard read it.
- **`touchScreen()`**: the device has a touch screen at all
  (`maxTouchPoints`), whatever is in use right now. The area-note toggle
  in the viewer's zoom column and the refusal of Safari's gesture events
  follow it.

Where a pointer's kind is the point, the code reads it where it is: the ink
layer, where a pen draws and a finger scrolls ([handwriting.md](handwriting.md));
the layout, which follows the screen (`PHONE_MQ`,
[ipad.md](ipad.md#layout-by-orientation)).

`shared/lib/pickFiles.js` opens the system file picker from code. iOS
Safari may not open it for an `<input>` outside the document, or may drop
that input before its `change`, so the input joins the page, hidden, for
the pick and leaves after it. The backup restore and the imports pick
through it.

### Sizes and reveals: CSS asks capability

- **One size set.** Controls take the `--ctl-*` tokens ([Scales](#scales)),
  the same for every device: no `(pointer: coarse)` rule changes them. A
  thin target gets a larger hit area rather than a larger look (a resize
  grip's `::before`, a splitter's `hitAreaMargins`). Two sizes stay apart:
  the editing bar's 36 px buttons, which only a touch screen shows, and
  the text box's handles under `(pointer: coarse)` ([text_boxes.md](text_boxes.md)).
- **Hover is a capability.** A rule that reveals a control on hover sits
  under `@media (hover: hover)`, or pairs its `opacity: 0` with
  `pointer-events: none`: a hidden control takes no press, so a stray tap
  never lands on something invisible. Under `(hover: none)` the control
  shows at rest, or by state (the focused row, a selected object's
  `.mdObjectSelected`, `:focus-within`):
  - A row's or a card's "⋯" is always there. A list row's or a card's pin
    shows only when pinned (Pin is in the menu), and a recents card's ×
    at 70 %.
  - Every note row's ⋮⋮ handle stays at 40 %, so its menu and drag are in
    reach; its "+" comes with the row being edited, and its × and the
    attach-mode button with the focused row.
  - A code block's Copy sits beside its language badge. A chat message's
    Copy and Edit, and a chat code block's Copy, are always there.
  - A selected picture or diagram shows its toolbar, its grips and its
    "⋯". A table's "+" strips and handles show while a cell is edited or
    the table is selected, and a touch places the handles as a hover does
    (`pointerover`).
- **A held finger opens Gamma's menu only.** `[data-press]` turns off the
  system's link and image callout everywhere, and its text selection where
  nothing hovers; fields inside keep theirs.

### `MenuButton`

`MenuButton` ([Menus.jsx](../../frontend/src/shared/ui/Menus.jsx)) is a
row's "⋯" (`ctlBtn rowMenuBtn`, a `MoreIcon`). It calls the same opener
as the row's `menuPress`, with a press under the button so the menu drops
from it, and keeps its click and double-click from the row. Inside a
`.rowMenuHost` it shows with its row (hover, `:focus-within`) where a
pointer hovers, and always where none does (app.css). The library's list
rows and cards, Select mode's selection, the chat history rows, and a
picture's and a diagram's toolbar (`ObjectMenuButton` in
`editor/MdObject.jsx`) carry one.

### Dock windows

`DockWindow` (`shared/ui/Widgets.jsx`) is the header the Notes and Chat
windows share: the grip ("⠿ Chat"), the close button, then the window's
own controls.

- **The grip** spans the header's full height and is padded past its
  text, with a clear gap before the close button.
- **Drag.** A press that travels 8 px drags the window (App's
  `startWindowDock`); the drop preview re-renders only when the zone
  changes. A `pointercancel` (the system took the touch) moves nothing,
  and the drop's click is swallowed.
- **Fold.** A double press on the grip folds the window to its header
  (`onFold` through `doublePress`). Folded, one press anywhere on the
  header but a button opens it. When that press was the first of a
  double-click, the second, landing on the opened grip within 800 ms,
  does not fold it again.
- **Close.** A closed window forgets its fold: shown again, from the View
  menu or anywhere, it comes back whole.
- **Splitters.** Each `PanelResizeHandle` between docks takes a press
  6 px past its 5 px on each side, for a mouse and a finger alike
  (`SASH_MARGINS` in App.jsx). react-resizable-panels' own 15 px for a
  coarse pointer reached over half a window header, grip and close button
  included.
- **The compact shell** neither drags nor folds a window: without
  `onGrip` the title is a plain label, with no "⠿".

### The PDF viewer

- **What a drag does.** A mouse draws an area note with Ctrl held. A
  finger has no Ctrl, so on any touch screen (`touchScreen()`, in either
  layout) the zoom column carries a sticky text / rectangle toggle
  (`viewer.selectMode`; the viewer tour's step for it requires the guide
  fact `touch`, [onboarding.md](onboarding.md)).
- **Menus.** A highlight, its note badge, a link and the translate button
  open their menus through `menuPress`.
- **A second finger** turns a one-finger gesture into a pinch: it cancels
  an area drag (`beginAreaDrag` takes the primary pointer only) as it
  cancels a finger's ink stroke ([handwriting.md](handwriting.md)).
- **The highlight tip.** The selection listeners subscribe once while
  annotating is allowed, not on every App render, so the pending
  `selectionchange` timer (350 ms) of a touch selection survives. The tip
  is placed on the screen, so a scroll of the pages closes it; a touch
  scroll sends no `mousedown`.

### Adding a control

1. A menu on an item: spread `menuPress(open)` where `onContextMenu`
   would go, and give the menu a visible way in, a `MenuButton` in a
   `.rowMenuHost` row or a button of its own.
2. A second action on an item: spread `doublePress(run)`, never
   `onDoubleClick` alone.
3. No `pointerType`, `(pointer: coarse)` or `maxTouchPoints` in feature
   code: ask `pointer.js`.
4. A control shown on hover: hide it under `@media (hover: hover)` or
   with `pointer-events: none` beside its `opacity: 0`, add the
   `:focus-visible` twin, and decide what shows it where nothing hovers.
5. Sizes from `--ctl-*`, and what sits around them by `calc()` of them.
6. Drag alone is not enough: what a drag files or orders has a menu row
   too (the folder menu's Move to, Move up, Move down).
7. A text field's Enter handler returns early while an input method
   composes (`composing` in `chat/enterKey.js`), and an `autoFocus` that
   would raise an on-screen keyboard is `!touchTyping()`. A username or
   account field turns capitalization and correction off.
8. A file pick goes through `pickFiles`.

## Block editor interactions

`editor/BlockTree.jsx` owns row interactions and the editor's popups;
`editor/BlockCmEditor.jsx` supplies the CodeMirror facade. Opening rendered
text maps the clicked character through `clickToSource.js` and keeps that
line under the pointer. A drag that began on rendered text continues as a
selection in the editor. A blur while the window is unfocused (Alt+Tab or
a file dialog) must not close the editor: `document.hasFocus()` gates the
blur callback, and the browser restores the active element on return.

The row gutter (`.rowHandles`) holds the drag/menu handle and an out-of-flow
add button so one-line rows stay one line tall. Add uses the same sibling
creation path as Enter: below by default, above with Alt+click; a home-library
row creates a page. The handle menu copies a link or the subtree as Markdown,
attaches the block to chat (also Ctrl+click), duplicates with fresh IDs and
without highlight anchors, moves to another page, or deletes.

Clipboard handling preserves Markdown storage:

- A URL offers the existing link/mention/embed choices; Gamma page/citation
  links are stored without host or workspace ([PDF citations](pdf_citations.md)).
- Strict TSV offers Table / Text / Blocks; other multiline text offers Text /
  Blocks. Blocks uses `POST /api/markdown-blocks`, the Markdown import parser,
  and inserts siblings, replacing an empty block without children.
- A clipboard containing one HTML table becomes a formatted Markdown table.
  Files upload and insert at the caret ([files and documents](block_centric.md#stage-4--files-and-documents-done-2026-09-13)).

An editable `![[embed]]` card writes to its source block, including checkbox,
image, table, and raw-text edits. Same-page sources use `onChangeText` and the
page's normal save path; cross-page sources use `PUT /blocks/{id}` with `base`
and reconcile the reference cache with the response ([collaboration](collab.md)).
Its editor shares `useMathUi`, completion, preview, and image/table paste with
the ordinary row editor. The card footer navigates to the source.

## File map (frontend/src)

| File | Owns |
|---|---|
| `app/App.jsx` | routing, block-tree editor state, docks, the page's live session glue, AI chat glue (decomposition in progress) |
| `collaboration/usePageCollab.js`, `shared/model/blockOps.js`, `collaboration/Presence.jsx` | the live session (ops out, ops + presence in), the pure tree diff/apply, the avatar stack / row chips ([collab.md](collab.md)) |
| `collaboration/MirrorPopover.jsx`, `collaboration/MergeResolver.jsx`, `settings/SettingsMirrors.jsx` | a clone's sync pill with its settings and conflicts views, the conflict card + the row chip, Settings → Workspaces → Clones ([mirror.md](mirror.md)) |
| `app/prefDefs.js`, `app/prefs.js` | every preference's key, default, codec and scope (`PREFS`); the hooks that make them state (`useAppPrefs`) and sync the account-scoped ones (`useProfileSync`) |
| `settings/SettingsDialog.jsx` + the `settings/Settings*.jsx` panes | the Settings dialog (`SettingsKit.jsx` holds the shared primitives incl. `AccountPicker`, the search-box-over-account-rows people picker, and `LogBox`) |
| `settings/sectionPrefs.js`, `settings/syncState.js` | which account preferences each settings section holds (`SECTION_PREFS`), and how their sync reads: a section's scope tag (`profileSyncState`) and the Settings sync row's hint (`cloudSyncHint`) ([settings.md](settings.md)) |
| `settings/SettingsAppearance.jsx`, `settings/SettingsLibraryDisplay.jsx` | the Appearance pane (theme cards + PDF sample) and its Library section (a live `PageCard` beside its switches) ([settings.md](settings.md)) |
| `settings/BackupTasks.jsx` | Settings → Backups' task table and its editor `SubDialog` (scope `Segmented`, workspace and weekday `ToggleGroup`s, frequency and retention `MenuSelect`s, the cron preview); each row's `ActionMenu` ([settings.md](settings.md)) |
| `settings/SettingsCloudSignIn.jsx` | Settings → Server › Sign-in (the account server, the server client, the unknown-accounts policy) and the Account pane's Gamma Cloud row ([cloud_accounts.md](cloud_accounts.md)) |
| `transfers/ImportExport.jsx`, `transfers/transferFormats.js`, `shared/illustrations/` | the Import/Export dialogs, their format/source rules (`resolveExport` / `resolveImport`) and the decorative previews ([import_export.md](import_export.md)) |
| `transfers/ImportReviewDialog.jsx`, `transfers/ImportTree.jsx`, `transfers/importApi.js`, `transfers/importReview.js` | the import review: upload → review → import → summary in one `SubDialog` (filter `Segmented`, source and destination trees with checkboxes), the `/api/import/review` calls, and the pure selection/filter/tree helpers ([import_export.md](import_export.md)) |
| `shared/lib/xhrUpload.js` | the one multipart upload: progress, a processing callback, abort by function or `AbortSignal`, the workspace and tab-identity headers; behind `FileChip.postFile`, PDF uploads, backup restores and the import review |
| `pdf/noteAnchor.js` | where a highlight's note badge sits: the geometric last line of its rects, not the last stored rect |
| `shared/model/gammaLinks.js`, `GammaLinkCard` / `CitationPill` in `shared/ui/Widgets.jsx` | links into this library (page / block / citation) classified once and drawn the same in the chat and in notes: a page or block link as a card, a citation as a compact pill with a hover preview ([pdf_citations.md](pdf_citations.md)) |
| `pdf/pdfCitation.js`, `pdf/PdfCitationOverlay.jsx` | a citation link → the quoted passage highlighted on the cited PDF page ([pdf_citations.md](pdf_citations.md)) |
| `shared/lib/canvasSize.js`, `pdf/verticalScrollSnap.js` | the canvas backing-store cap and the one-finger vertical scroll alignment ([pdf_loading.md](pdf_loading.md)) |
| `shared/lib/viewerZoom.js`, `shared/model/zoom.js` | the Ctrl+wheel and two-finger pinch/pan gestures both zooming viewers read, and the zoom limits every entry point clamps to (above) |
| `shared/lib/stableActions.js`, `shared/lib/keepIfSame.js` | what a memoized row or page relies on: one stable wrapper per handler name calling the latest handler (`useStableActions` for the page tools, `makeStableActions` for the outliner's `rowProps`), and a derived map or object kept by identity while it reads the same (`keepIfSame` with `sameMap` / `sameObject`) ([frontend-refactor.md](frontend-refactor.md)) |
| `chat/ChatDock.jsx` | the AI chat panel (incl. agent wiring); header = a `.ctlBtnRow` of `.ctlBtn` icon buttons (the PDF zoom column's buttons laid flat) with the ⚙ settings popover |
| `pdf/PdfViewer.jsx` | the custom pdf.js viewer. A highlight's colour rides in `--hl`; a palette colour is also tagged with its `COLORS` index (`paletteIndex` in `shared/model/highlightColors.js`, `data-hl-color`), so on a dark page — Flip page colors or Gamma Dark — app.css draws a dark-tuned set with normal blending in place of the screened pastels, while colours from other apps keep the screen blend. A jump to a highlight (its note's dot, a deep link, a search hit) pulses it once in its own hue: App's `triggerFlash` → `flashHighlightId` → `.pdfHlFlash` on its rects, a still ring under reduced motion |
| `ink/ink.js`, `ink/inkStore.js`, `ink/inkInput.js`, `ink/InkLayer.jsx` | handwriting ([handwriting.md](handwriting.md)): the stroke codec + geometry (pure), the files/drafts store, pointer sampling, and the page layer + selection menu (`.inkEditMenu`, placed by `useSelectionMenuAnchor`) + notes card + `ColorChoices`, the swatches and custom colour that a pen preset's row and a text box's style share |
| `markup/MarkupToolbar.jsx`, `markup/MarkupLayers.jsx`, `markup/PageTools.jsx` | the tool strip (`.pdfInkBar`: `ctlBtn`s and `colorBtn` swatches; the Text tool's `TypeIcon`), the one mount of a page surface's layers (text boxes, then ink), and the tools context they read, with `armedClasses` for a surface's armed-tool classes ([handwriting.md](handwriting.md)) |
| `markup/TextBoxLayer.jsx`, `markup/useTextBoxes.js`, `markup/textBox.js`, `markup/markup.css` | text boxes ([text_boxes.md](text_boxes.md)): the boxes of a page (frame, width handle and band in screen pixels over a box sized in the page's points), the box menu (the handwriting menu's `inkEditMenu` rows: `TextSizeIcon`, `PaletteIcon`, `PaintBucketIcon`, then Duplicate, Show note, Delete), and `TextStyleChoices`, the size numbers, the pen palette through `ColorChoices`, and the four backgrounds ("none" a struck-out swatch, `.textNoBackground`) that the menu and the strip's Text row share; the notes row's `TypeIcon` marker (`.textBoxMarker`) |
| `search/SearchPanel.jsx`, `search/snippets.js`, `library/librarySearch.js` | workspace search (Ctrl+F); `MarkedText`, the query's matches as `mark.searchMark` (quick open's rows and the `[[` picker's too), over the pure result text of `snippets.js`; the title scorer it shares with chat, and the library matcher (title + folder/label chips) shared by the home filter box and quick open |
| `library/QuickOpen.jsx` | quick open (Ctrl+P): a `.reportOverlay` palette over the library ([home_library.md](home_library.md)) — no query: recents, open tabs, the rest; a query: pages, folders and labels in sections, then the search-everywhere and create-page actions; rows are the chat mention picker's `chatMentionOption`; a query starting with `>` (Ctrl+Shift+P) is the command palette over App's `paletteCommands()` ([hotkeys.md](hotkeys.md)) |
| `library/PageOption.jsx` | a library page as a picker option, for quick open and the chat mention picker: kind glyph, marked title, authors and year, folder/label chips, then a check, "Current" or the time; also the Recent / Open tabs / Everything else headings (`recencySections`) |
| `chat/PaperMentionInput.jsx`, `chat/paperMentions.js` | chat mention picker (quick open's page list over the composer), mention text edits and `MAX_CHAT_REFERENCES` (six attached pages plus the current page) |
| `chat/ApprovalCard.jsx`, `chat/approvals.js` | the card a tool call waits on while the user decides ([ai.md](ai.md#asking-before-a-call-approvals)): the fetch handoff card's look (accent border and tint), a caption naming the permission, the change as a word diff (`del` on `--danger-bg`, `ins` on an `--ok` tint), **Allow once** as the one primary button, **Don't allow** ghost; the buttons never take the focus themselves. A permission's state (Allow / Ask / Off) is a `MenuSelect` whose icon is tinted by state (`.agentPermState` in settings.css) |
| `chat/AgentChanges.jsx`, `chat/aiRevert.js` | what an agent reply changed, under its steps pill ([ai_tools.md](ai_tools.md#reverting-a-note-change)): `.chatChanges` rows behind an accent rule, a note row ending in a frameless `ctlBtn` undo icon, "Revert all" a `uiBtn sm ghost` in the head, a reverted row muted with its "Reverted" tag and a redo icon in the same place; a row that stops shows its reason, the approval card's `WordDiff`, and **Revert anyway** (`uiBtn sm`) beside **Keep it** (ghost) |
| `chat/chatErrors.js`, `chat/agentSteps.js`, `chat/chipText.js` | pure helpers of the chat panel: the error card's copy per failure kind (also the login check's strip), the agent's steps pill and change list, a context chip's plain-text preview ([ai.md](ai.md); its words through `search/snippets.js` `plainSnippet`, the one markdown-to-words rule) |
| `editor/BlockTree.jsx`, `editor/refLabels.js`, `shared/model/blockModel.js` | outliner rendering (its rows are memoized), every row's `[[ref]]` and Gamma-link labels resolved once per tree change by the pure `refLabels.js`, and the pure tree ops (`shared/model/highlightColors.js` is the highlight palette both share with the viewer). Line breaks in a rendered note: one Enter is a hard line break (`remark-breaks`), one blank line the paragraph break, and every further blank line a visible empty line (`expandBlankLines` in `editor/mdMarks.js`, applied by `mdPreprocess` outside math and code) — what the editor shows is what the note renders |
| `transfers/FileChip.jsx` | the file chip an upload link renders as — a small card (kind icon in a tinted square, name, download arrow), inline so it sits in a sentence, identical for every type; a PDF or markdown chip whose page exists gets an accent "open page" button before the arrow; a `ContextMenu` on a right-click or a held finger (`menuPress`) with "Open page" / "Add to library" (fed by `FileChipContext` from App and one batched `POST /pages/by-docs` per render) and download; also the shared `postFile` / `uploadFilesAsLines` upload helpers |
| `editor/MdTools.jsx` | in-place tools on rendered notes: `MdImage` (a toolbar of `ctlBtn` icons, shown on hover and while the picture is selected — zoom lightbox, caption via alt text, download, delete, the object menu's "⋯" — plus a drag grip on each side of the centred picture writing the Obsidian `![alt|300]` size; legacy Logseq `{:width N}` reads and normalizes on edit) and `MdTableWrap` ("+" strips shown on hover, while a cell is edited and while the table is selected, column/row handles placed by the hovered or touched cell (`pointerover`) opening menus — insert, align, delete — and click-a-cell in-place editing: an input over the cell, Tab/Shift-Tab hop cells across the commit remount via a module-level session map, Enter commits, Esc cancels; tables are never edited as raw markdown — a cell mousedown stops the block row's edit-on-mousedown; selecting, moving and deleting a whole table is the object frame, `MdObject.jsx`), backed by pure source transforms (`scanImages`/`scanTables` locate the nth rendered construct; `applyImageEdit`/`applyTableEdit` rewrite it, tables re-serialized pretty-printed; `formatTables` also runs when a block's raw editor closes) and `htmlTableToMarkdown` for the spreadsheet-paste path |
| `editor/MdObject.jsx` | the object frame around every rendered image, table and Mermaid diagram (`MdObject`, wrapped by `BlockMarkdown`). A press on the object's body (`.mdImgFrame`, `.mdTableWrap`, `.mermaidDiagram`) selects it (`mdObjectSelected` ring, and a selected picture or diagram shows its toolbar and grips; an editable picture zooms on double-click or double tap, a read-only one on click) and never reaches the block row; a press on the frame's margin does reach it, and click-to-source puts the caret at the object's near end. One menu for all kinds, on a right-click or a held finger (`menuPress`), or from the "⋯" (`ObjectMenuButton`) ending a picture's or a diagram's toolbar: Edit markdown source (click-to-source with the object's offset), Move to ▸ new block above / below / another page (the block move's page picker), Copy as markdown, Delete (Delete/Backspace on a selected object does the same); the table's corner handle opens it through `useObjectMenu`. The frame is the HTML5 drag source: `dragStart` publishes `{blockId, kind, idx}` as `_dragState.fragment` plus the `application/x-gamma-object` data type, and App's `onBlockDragOver`/`onBlockDrop` route it. The row's outer 30 % (6–14px) means a new sibling block (the block drop indicator); the middle means inside the block at the gap nearest the pointer (`dropGapAtPoint`: `renderedGaps` plus a gap above the first and below the last construct, mapped to a source line start by the object scanners or `blockStartInSource` + `gapInSource`; drawn by `BlockDropIndicator` as `dropIndicatorInside`). `scanObjects` merges `scanImages` / `scanTables` / `scanMermaidFences` into one source-ordered list; quoted tables and prefixed or unfinished fences are not `editable` (menu without move/delete, no drag) |
| `editor/mdScan.js` | the pure scanners the rendered view (mdPreprocess, MdTools, the object frame) and the editor's widgets share, so all agree on which construct is the nth one: `scanMathSpans`, `protectedSpans` (math, fences, inline code — where markdown syntax is not read), `scanImages`, `scanTables`, `parseTable`, `serializeTable`, and `blockSpans` (the fences and display math a line boundary must not split) |
| `editor/mdObjects.js` | the pure source-range algebra behind every object move (`tests/mdObjects.test.mjs`). `cutObject`: the object's markdown and the content without it; a whole-line object takes its lines and the blank lines around it close up to the wider gap, an image inside a text line leaves the text; `mapOffset` carries a drop offset across the cut and returns null inside the removed zone, so a drop onto itself is a no-op. `insertObject`: a paragraph of its own at a line start or the end, the next line's indentation kept. `moveObject` (same block) and `moveObjectInTree` (the one tree edit for the drop and the menu: `inside` splices source, `sibling` / `child` make a new block) — one `setBlocks`, so a cross-block move is one undo step and one op batch. Nothing new is stored; the object's markdown changes place |
| `editor/BlockCmEditor.jsx` | the CodeMirror 6 block editor (textarea-compatible facade) with live in-place rendering of closed `$…$`/`$$…$$` spans, ``` ``` ``` fences (highlight.js cards), `[[ref]]`/`![[embed]]` chips, `![alt](url)` images (the picture, sized like the rendered view, alt as caption; `scanImageSyntax` in `mdMarks.js` is the one image scanner, shared with `MdTools`) and GFM tables (`TableWidget`, a read-only table of the raw cells). Pictures and tables are objects, not text: they stay rendered while the caret rests at either end or steps through their lines, and only a selection reaching strictly inside shows the source. A click puts the caret after them; a right-click or a held finger (`menuPress`) drops it inside (the editor's "Edit markdown source"). The widget is a drag source through `onObjectDrag` (BlockRow's object action → `_dragState.fragment`), so a table can be dragged out of a block while it is being edited. A drop INTO an open editor is the editor's own: capture-phase `dragover`/`drop` on its host, recognized by the `application/x-gamma-object` data type, land at the line boundary nearest the pointer (the inside drop line, then `onMoveObject` `inside`); CodeMirror's default drop, which would paste the markdown at the caret as a copy, never runs. The object index comes from `mdScan.js`, so it matches the rendered view's frame. Markdown renders live too (headings, `**`/`*`/`` ` ``/`~~`/`==`, links + bare URLs, clickable `- [ ]` checkboxes, `- ` bullets, `---` rules, quote lines and full `> [!type]` callout boxes with their fold flag, colored runs — `<span style="color:…">` / `background:…` inline HTML, tags hidden, `scanColorSpans` in `mdMarks.js`) — the construct the caret touches stays raw source (line-level touch for heading/quote prefixes, marker-only touch for list markers so a todo's checkbox survives editing its text). Raw math gets VSCode-style bracket-pair colorization (depth-cycled `--bracket-*` colors, enclosing pair boxed). Opening a block's editor keeps its height (checked by notes.mjs): the rendered view's outer margins stop at the block's edges, a list, quote, callout or table hugs the text line above it as its source lines do, and the editor's widgets take the rendered metrics — the `$$` math and code cards their margins, a callout's first and last lines the box's 6px padding (the title 2px under it, 4px when foldable), the table widget the object frame's 3px halo, 6px under the line above and the 14px the rendered table keeps under it for its + row. Blank lines still differ (the rendered view folds one into a paragraph break), as does a heading directly under text. Decorations come from a `StateField`, not a ViewPlugin — plugin decorations may not replace line breaks (multi-line fences/`$$` would throw). Formatting hotkeys — Ctrl/Cmd+B/I/E, Ctrl+Shift+X/H toggle `**`/`*`/`` ` ``/`~~`/`==` Obsidian-style, Ctrl+K inserts `[sel](url)` (clipboard URL fills the slot); swallowed inside math/fences/inline code — are block commands of `editor/blockCommands.js`, planned by `markCommands.js` and dispatched to the editor; a system keyboard's or menu's `beforeinput` `formatBold` / `formatItalic` / `formatStrikeThrough` runs the same commands. The system's typing aids (autocorrect, capitals, spelling, suggestions) are on in prose and off in a code fence or math, decided as the caret moves (`typingAids`, [ipad.md](ipad.md#the-editing-bar)) |
| `editor/EditBar.jsx` | the touch editing bar: the markup strip's surface (`.pdfInkBar`'s elevated box, border, radius and light-scheme shadow) with its `.pdfInkSep` separators, holding `ctlBtn`s with 16 px icons at the ink menu's finger size (36 px), then a Done ✓. It floats centred 6 px above the on-screen keyboard, never lower than the compact shell's bottom bar, else at the screen's edge, at `--z-editbar` while a note's editor is open on a touch screen. Every button but Undo / Redo (the page's block history) is a block command, disabled when its `when` fails. Presses never take focus ([ipad.md](ipad.md) "The editing bar") |
| `editor/clickToSource.js` | maps the rendered view back to the raw source by text (the layouts differ too much for coordinates): the clicked character a new editor opens on (`sourceOffsetAtPoint`), the gap line between two rendered blocks (`renderedBlocks`, `renderedGaps`, `blockStartInSource`, `gapInSource`), a Ctrl-selection's source range for the chat (`sourceRangeOfSelection`), and other people's carets placed on the rendered view (`renderedCaretRect`, via `locateInRendered`); `locateInSource` / `locateInRendered` / `gapInSource` are pure and unit-tested |
| `editor/mdMarks.js` | the inline-mark table (regex + class per marker) shared by the live renderer and the hotkeys, plus the pure `toggleMark`/`insertLink` transforms (wrap / unwrap / empty pair / per-line for multi-line selections). `scanMarks` allows proper nesting (`**a *b* c**`, `*a **b** c*`; nothing inside inline code) and treats `***x***` as one bold+italic span with two `layers`, so Ctrl+B and Ctrl+I each peel off their own delimiters |
| `editor/slashCommands.js`, `editor/SlashMenu.jsx` (their text-only insertions in `editor/slashInserts.js`, which the iPad app runs too) | the "/" command catalog and its matching (pure, `tests/slashCommands.test.mjs`): headings, to-do, lists, quote, callout, divider, equations, `/page` (a new library page, linked) and `/note` (a sheet of paper), table, code, mermaid, image, date, link, embed, highlight, and the `hidden` text/background color commands from `mdMarks.TEXT_COLORS` that show only when the query matches. The bare "/" list is the catalog in order under the `SLASH_GROUPS` titles (Text, Math, Insert, Link, Style); a typed query is one ranked list, by tier: the command's own name, a name it begins, a word of its label it begins, a keyword it begins, then a VS Code-style fuzzy match of the name or label (anchored on the first letter, jumping to word starts: `/cb` → Code block), like the `\command` completion's ([latex_editing.md](latex_editing.md)). A command that `needs` an ability (`newPage`, `sheet`) is left out where the editor lacks it (`filterSlashCommands`' `can`: a share's editor makes no library page). `SlashMenuPopup` draws a row as an icon tile (`MENU_ICONS` by the item's `icon` key; the colors keep a tinted "A" `glyph`), the label over a one-line description, and the `/name` to type at the right, the matched letters marked with search's `mark.searchMark` in the name or the label, wherever they matched; a key-hint footer (Tab or Enter inserts). The same popup is the "Paste as" chooser shown after a URL paste (gamma block link → mention/synced block/URL, other URLs → URL/titled link), without names; blockTree owns trigger detection and key handling |
| `editor/RefPicker.jsx`, `editor/refLists.js` | the `[[` link picker: a caret-anchored popup (the "/" menu's placement) with pages matched by title (`rankRefPages`, the library matcher) above note blocks from `/api/block-search`, each block one plain line under its page path (`refBlockText` / `refBlockPath`, search's `plainSnippet`); when that search stopped at the server's time budget (`partial`) the key-hint footer leads with a "stopped early" line; the typed text is marked with search's `MarkedText` and a page row says `pageKindLabel()` ("Page" / "PDF"), as Search, Quick Open and the library do. `pageByTitle` is the hand-typed rule: `[[title]]` closed by hand becomes `[[id]]` when exactly one page has that title, else it renders as a dashed `.unlinkedRef` chip. blockTree owns the trigger, the keys and the insertion |
| `editor/BacklinksPanel.jsx` | "Linked from N pages" under a page's notes: App's `/blocks/{id}/backlinks` list grouped by page, each linking block a three-line rendered snippet; a click opens its page at the block (`openBlockLink`, a link jump); the section is off until the account turns it on (`backlinksVisible`, Settings → Reading & editing › Notes — off, no backlinks are fetched), and its fold is the browser pref `backlinksCollapsed` |
| `editor/callouts.js` | remark plugin for `> [!note] Title` callouts (type aliases → note/tip/warning/danger/important/quote; each type's colour is a `--callout-*` token); Obsidian's `[!note]-` / `+` fold flag makes a native `<details>` with the title as `<summary>` (chevron in app.css) |
| `editor/codeHighlight.js` | the highlight.js (`lib/common`) wrapper and the code card's copy button, shared by editor + renderer; token colors are theme-aware `.hljs-*` rules in app.css. The fence scanner is `editor/fences.js`: `scanFences` (used by mdPreprocess's exclusions and BlockTree's Enter/Tab-in-fence handling) and `fenceInnerAt` |
| `editor/LatexEditor.jsx` | LaTeX aids while editing: the live preview docked to the editor column with a caret marker, the `\command` popup, `renderKatex`/`useCaretAnchored` shared helpers; `editor/latexCompletion.js` is the pure catalog (prefix/abbreviation/fuzzy tiers, snippets, Tab-out navigation) it re-exports; `editor/latexInput.js` supplies scalable delimiter pairing. See [LaTeX editing](latex_editing.md) for shortcuts and browser checks |
| `library/RecentlyDeleted.jsx` | the Recently deleted dialog: a `SubDialog` of `aiProvRow` rows (title, who deleted it and when, days left, folder) with Restore and a delete-for-good icon button behind App's confirm box, Empty in its `DialogButtons` ([home_library.md](home_library.md) "Recently deleted") |
| `library/libraryUtils.js` | the library's folder and label helpers over the folder and label trees ([home_library.md](home_library.md) "Folders and labels"), and `pageAttachment`, what a page carries |
| `shared/ui/Widgets.jsx`, `shared/ui/Menus.jsx`, `shared/ui/Icons.jsx` | shared components; `OpenTabs` in Widgets is the topbar's tab strip (a kind icon per tab, pinned tabs first, full width with a pin in place of the close button, the active tab kept in view clear of the right-edge fade, and on overflow a "⌄ n" popover listing every tab with a filter) |
| `shared/ui/wheelPan.js` | `useWheelPan`: a plain mouse wheel pans a sideways strip (the card strips, the tab strip) |
| `shared/ui/listKeys.js` | `stepList`: ↑/↓ between a search box and the results listed under it (Settings search, the move-to-page filter, the chat's page picker) |
| `shared/ui/MermaidDiagram.jsx`, `shared/ui/ResizeGrip.jsx` | the Mermaid figure with its toolbar of `ctlBtn`s, ended by a note's object "⋯" (`tools`) ([mermaid.md](mermaid.md)); the two-sided drag grips (`ResizeGrips`) + `useDragResize` hook that size centred note images and diagrams alike, a double-click or double tap on a grip restoring the natural size |
| `shared/ui/press.js`, `shared/lib/pointer.js`, `shared/lib/pickFiles.js` | one behaviour for mouse and finger (above): `menuPress` / `doublePress` / `pressAt`, the three device questions, and the system file picker opened from code |
| `app/CommandMenuItem.jsx` | a catalog command as a menu row (the View menu's Undo, Redo, Go to page, Command palette; [Menus and submenus](#menus-and-submenus)) |
| `shared/ui/menuAim.js` | pointer-trajectory ("safe triangle") hover intent for hierarchical menus — UI-agnostic, consumed by `shared/ui/Menus.jsx` |
