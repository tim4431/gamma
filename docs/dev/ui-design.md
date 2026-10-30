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
scheme picks the recipe (`DARK_THEMES` / `themeScheme` in `app/theme.js`).
Three places set both attributes: `index.html`'s pre-paint, `applyTheme`
(`app/theme.js`: App.jsx's theme effect, and `main.jsx` as the session
brings the account's theme, before the app's first screen) and the desktop
shell. A rule that differs by scheme keys on
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
| Motion | durations `--dur-fast` 100 ms · `-base` 160 ms · `-slow` 240 ms · `-long` 400 ms; easings `--ease-out` · `--ease-swap` · `--ease-pop`; travel `--move-sm` 4 · `-md` 8 · `-lg` 16; `--scale-in` 0.97 · `--scale-out` 0.99; `--blur-swap` 2 px | fast: hover, press, a tooltip going; base: a surface leaving, a swap in place, a tooltip coming; slow: a surface arriving, bars and larger moves; long: a toast or panel arriving, a reveal. `--ease-out` for whatever moves, scales or resizes, `--ease-swap` for a cross-fade in place, `--ease-pop` for a badge or a switch's thumb; a colour or opacity fade leaves its easing out. Loops and attention effects of half a second or more (spinners, pulses, the landed flash) keep their own period and easing. How each kind of thing moves is [Motion](#motion) |
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
  elevation shadow, a z-index above 9, a transition's time under half a
  second or its easing written out (a bezier or keyword: a fade leaves it
  out, anything that moves takes an `--ease-*`). It
  fails when a file holds more of one kind than `tests/design-baseline.json`
  records, and lists the new offenders. What is left is mostly the PDF
  page's own colours. After a cleanup,
  `node tools/designTokens.mjs --baseline` records the lower floor.

A value that is raw on purpose says why in a comment the scanner reads:
`/* ds-allow: why */` on its line, or `ds-allow-start: why` …
`ds-allow-end` around a block. Examples: the syntax palette, the
collaborator colours, the transfer illustration, the custom-colour
swatch's rainbow, a transition that follows the pointer. (The highlight and
ink colours are not in a stylesheet at all: they come from the account's
palette, `shared/model/palette.js`, as inline values.)

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
| `ctlBtn` / `ctlBtnRow` / `pdfCtlBox` | the flat `--ctl-icon-btn` (29 px) icon buttons (a 16 px icon) of the PDF zoom column: `pdfCtlBox` = the elevated vertical box, `ctlBtnRow` = the same buttons laid flat with no box (chat header), `modeActive` = on, `danger` = destructive (red under the pointer or the keyboard's focus); `pdfCtlGroup` keeps a run of a box's buttons together (a guide anchor) without changing the layout. The zoom column of the PDF and the notebook viewer stands in the account's one Viewer tools order, by default the same buttons first in both (zoom out, zoom in, fit to width, the pen) and what only one viewer has after them; a hidden tool is a row of the ⋯ button that ends the column ([settings.md](settings.md#bars-order-and-modes)). **`ctlBtn` (frameless) is the DEFAULT style for any icon button** — new icon toolbars (e.g. the image tools) use it, not bespoke button styles |
| `uiClose` (+ `uiCloseSm`/`uiCloseLg`) | every × close button: round, `--ctl-close` 26 px (Sm 22 for chips and rows, Lg 31 for dialogs and panes) |
| `MenuButton` ([Menus.jsx](../../frontend/src/shared/ui/Menus.jsx)) | a row's or a card's "⋯": the visible way into the menu its right-click or held finger opens ([below](#one-behaviour-for-mouse-and-finger)) |
| `aiKeyInput` | every text/number/password input in dialogs and settings |
| `switch` / `switchTrack` | every on/off toggle |
| `MenuSelect` / `ActionMenu` ([Menus.jsx](../../frontend/src/shared/ui/Menus.jsx)) | every dropdown: Codex-style pill trigger + checkmarked `ContextMenu`. No native `<select>` anywhere |
| `useMenuTrigger` ([Menus.jsx](../../frontend/src/shared/ui/Menus.jsx)) | the menus a toolbar's buttons open on the press, one at a time per toolbar: the recording player's mode and speed, the tool strip's More, Tool and Style, the selection menu's Style ([below](#menus-and-submenus)) |
| `MenuItem` / `MenuLabel` / `MenuDivider` / `SubMenuItem` ([Menus.jsx](../../frontend/src/shared/ui/Menus.jsx)) | every row inside a menu, context or popover: icon column + ellipsizing label (+ `danger`, `trailing`, `keys`); `MenuDivider` between groups. `SubMenuItem` is the nested flyout — hover-opened, safe-triangle guarded; a popover menu hosts one through `MenuScope` |
| `categoryTag`, `uiTag` | chips and small badges |
| `KeyCaps` / `KeyBinding` ([SettingsKit.jsx](../../frontend/src/settings/SettingsKit.jsx); `KeyCaps` lives in [shared/ui/KeyCaps.jsx](../../frontend/src/shared/ui/KeyCaps.jsx), shared with the guide) | a keyboard chord as `.keyCap` key caps; `KeyBinding` is the rebindable version (click, press the new keys; reset button when changed) — [hotkeys.md](hotkeys.md) |
| `ColorPicker` ([shared/ui/ColorPicker.jsx](../../frontend/src/shared/ui/ColorPicker.jsx)) | a colour with alpha, where the native `<input type="color">` (no alpha, a different dialog per browser) will not do: a saturation / value square, a hue bar and an alpha bar (any pointer drags, arrow keys step; `role="slider"`), a preview over a checkerboard, a hex field that takes `#rrggbbaa`, an opacity field in percent, and the eyedropper where `window.EyeDropper` exists. Controlled, `{hex, alpha}`; its gradients are inline, so the stylesheet keeps no raw colours. Appearance › Colors' swatch editor ([settings.md](settings.md)); the tool strip's custom-colour swatch keeps the native input |
| `popoverAnchor` | the `position: relative; inline-flex` wrapper every popover trigger sits in (`data-popover="…"` on the same element) — never inline that style |
| `Section`'s `scope` tag (`.setScope`, [settings.md](settings.md)) | where a settings section's values live: a 14 px icon and one muted word in the small caption size, "account" or "browser". An account tag's icon is the sync state of that section's own settings (check, cloud-check, spinning refresh, warning — the only colour); the sentence is the hover `title` and `aria-label`, never text |

Diagram previews (Mermaid and SVG) in notes and chat use the shared
component and toolbar described in [diagrams.md](diagrams.md). The toolbar
is the note image's strip of `ctlBtn` icon buttons, shown on hover and
while the object is selected, and the diagram resizes with the same
two-sided grips as an image (`shared/ui/ResizeGrip.jsx`). A Mermaid diagram
follows the color scheme; an SVG drawing keeps a white page in every theme,
like a picture.

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

### Alt variants

A control whose Alt+click does something else shows that variant while Alt
is held over it, so the outcome is visible before the click lands.
`useAltVariant()` ([altVariant.js](../../frontend/src/shared/ui/altVariant.js))
reports Alt held under the pointer or the keyboard's focus, and nowhere
else, so an Alt held over the PDF (the translation peek) restyles nothing.
The control swaps its icon for the variant's; a destructive variant adds
`danger` and takes its own name and `title`. The click still reads
`e.altKey`. The chat header's New chat, which becomes Discard chat, is the
model.

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
popover, search and menu section captions, the Keyboard pane's groups, the
handwriting strip's option-row captions) share
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
  key chip, a shape handle's dot, rows that highlight like `.chatHistRow`).
  A control hidden over another takes the ring there: the custom-colour
  swatch rings itself for its invisible native picker
  (`.inkCustomColor:has(input:focus-visible)`).
  Rows that fill a list (menu items, popover items, search results, the
  Settings rail) draw it inside (`outline-offset: -2px`), where the list
  can't clip it.
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

Every cursor-anchored menu is a `ContextMenu`. It measures itself and is
clamped inside the viewport; `anchorRight` / `anchorBottom` take the point
as its right or bottom edge, `flipY` is the other side of what it opens
from, taken when its own side has no room, and `bounds` a band it stays in
([below](#menus-and-popups-open-in-place)). Every row inside one
is a `MenuItem` (icon column, ellipsizing label, optional `trailing` node or
`keys` — the row's shortcut, e.g. the page menu's Enter / F2 / Del —
`danger` for destructive actions). Groups of rows are separated by
`MenuDivider`; `menuGroups(...groups)` drops empty groups and puts one
divider between the rest, so a menu a viewer sees half of has no doubled
rule. Popover menus use the same rows: the topbar's account, View and Add
menus and the page header's Document popover. The account, View and
Document menus render their panel as a `MenuScope` (the flyout state and
the menu role a `ContextMenu` carries), so the account menu's **Tours** is
a `SubMenuItem` like any other (`data-guide="account.tour"` on its trigger,
`data-tour` on its rows); the Add popover, whose rows sit under its URL
field, keeps a plain panel. A
click on a flyout's trigger opens it and never toggles it shut. A row that opens a nested list is a
`SubMenuItem` — it renders its panel *inside* the parent menu's DOM (a
portalled panel would sit outside the parent's outside-pointerdown test, and
the parent would dismiss itself before a click on a flyout row could land),
flips to the other side and clamps vertically when the viewport is tight.
In the compact shell's More sheet a flyout opens inline under its row
instead (`.phoneBottomBar .popover .ctxSubMenu`): beside it, the sheet's
own scroll box would clip it. An open menu takes Escape first, on the
capture phase. So Escape in a dropdown inside a dialog or a popover closes
only the dropdown. A `ContextMenu` rendered inside another one's rows
(the handwriting selection menu's Style picker opens one) is a part of
that menu: a press in it never dismisses the menu it came from, and
Escape closes it first (`NestCtx` in Menus.jsx).

A menu a toolbar's button opens comes from `useMenuTrigger({side})`
(Menus.jsx). It opens on the button's press, as a native menu does, not on
the click that follows (`triggerPress`, [below](#gestures-shareduipressjs)).
A primary press opens or closes it: a mouse's left button, a finger or a
pen. The click after the press does nothing. A click no press led to
(Enter or Space on the focused button) toggles the menu too. While the
recording player is open, Space plays and pauses wherever the focus is
outside a text field ([recording.md](recording.md)), so then only Enter
opens these menus from the keyboard. The press takes the button's tooltip
away (`InkTooltips`), and the menu comes in the same frame.

One hook holds the menus of one toolbar, one open at a time.
`trigger(kind)` is a button's props: its id, the press, the keyboard's
click, `aria-haspopup="menu"` and `aria-expanded`. `menu` is the open
menu's `ContextMenu` props, which name it by its button
(`aria-labelledby`). `close()` is what a row calls before it acts. The
pressed button is the open menu's `ignoreRef`, so a press on it closes the
menu rather than closing and reopening it. A press on another button of
the toolbar swaps the menus: the open menu's outside press queues its
close first, so the new kind opens. `side` (`"below"` or `"above"`)
places the menus from their buttons, each by its button's left edge
(`menuAnchor`, [below](#menus-and-popups-open-in-place)).

The menu is rendered outside any `InkTooltips` host. React bubbles a
portal's events up to the host, which would raise a tooltip for every row
the pointer crosses. Nothing moves the focus: the menu takes none, so
after Escape closes it the focus is where it was (on the button, when the
keyboard opened it). The recording player's mode and speed menus open
above the bar. The tool strip's More, Tool and Style menus and the
selection menu's Style menu open below their buttons. `MenuSelect` and
`ActionMenu` open on the click (`useDropdown`), and `MenuButton` is a
row's stand-in for its right-click menu ([below](#menubutton)).

To assistive technology a menu is WAI-ARIA's
[menu](https://www.w3.org/WAI/ARIA/apg/patterns/menubar/). The element
around its rows, the `MenuScope`, has `role="menu"`: every `ContextMenu`
and the account, View and Document menus. A menu is vertical, the role's
default, so nothing sets `aria-orientation`. Inside a menu a `MenuItem` is
a `menuitem` and a `MenuDivider` a `separator`. A row whose check the
row itself sets passes its own role with `aria-checked`. A toggle is a
`menuitemcheckbox` (the View menu's windows, a page's labels, the strip
More's Ruler, Shapes and Zoom window). One of a set is a `menuitemradio`
(`MenuSelect`'s choices, the account menu's workspaces, the player's mode
and speed, the Style menu's brushes, a `Segmented` inside a menu). A
`MenuSelect` with headings puts each set of radios in a `group` named by
its heading. In Move to folder (a page's) and Move to (a folder's) a check
marks the folder it is in. That is no state the row sets, so the row stays
a `menuitem`, and its title says so.

Every other control in a menu is a `menuitem` too: the account card, a
notice's link and the highlight menu's colours. No other role stands
between an item and its menu or group, so a notice is no `status` region
there, and its sentence describes its link. The one control that is no
item is the page menu's "New label…" field, a text box in the labels
flyout, which the menu role has no place for. A flyout is a menu of its
own inside its row's wrapper, as the pattern nests a submenu. So a
flyout's rows sit in two menus and every other row in exactly one. No
element adds a second `role="menu"` around rows that already have one.
Outside a menu (a dialog's list, the Add popover, a settings popover) a
`MenuItem` stays a plain button. A `ContextMenu` that holds something
else passes `role="none"`: the handwriting selection menu and the text
box menu are toolbars, and the model picker's list is a listbox. The
selection menu's Style picker opens a menu of its own, portalled outside
that toolbar.

A menu is named by what opens it. `MenuSelect`, `ActionMenu`, a flyout
and every `useMenuTrigger` menu (the player's mode and speed, the strip's
More and Tool) point `aria-labelledby` at their button or row. Another
menu with a button of its own takes that button's name as its
`aria-label` (Account & settings, View, Document, Export chat, Translate,
Comment actions, Recording actions, Table options). The Style menu is
named Pen style, not by its picker, whose name is the brush it shows. A
context menu opens at the pointer, on a right-click, a held finger or a
row's "⋯" standing in for them. No control of its own names it, so it
goes without a name, as a native context menu does. The dropdowns'
buttons, every `useMenuTrigger` button, a flyout's row, the account, View
and Document buttons and the chat's export button say
`aria-haspopup="menu"` and `aria-expanded`. A `MenuButton` says
`aria-haspopup` alone. The roles change neither the keyboard nor the
focus. The rows are Tab stops, and the pattern's arrow keys and its focus
moving into an opening menu are not built. The browser suite's
`assertMenuRoles` (harness.mjs) reads an open menu this way. The last
step of `touch.mjs` calls it, and so do the handwriting and replay steps
that open the strip's, the selection's and the player's menus.

A menu that lists commands lists their ids: `CommandMenuItem`
(`app/CommandMenuItem.jsx`) draws a catalog command ([hotkeys.md](hotkeys.md))
as a `MenuItem` with its label, its glyph (`app/commandIcons.jsx`) and the
key this account gave it. The row is greyed while the command's `when`
fails, with `disabledTitle` as its title then (the reason it does not
apply). It runs the command with the context its key runs it with and
then tells `onRun` (a menu that closes itself). Any other prop is the
row's, and an id the catalog does not know renders nothing. The View menu's Undo, Redo, Go to page and
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

### Menus and popups open in place

A floating surface is first seen where it stays: never on one side of its
anchor and then the other, never over its button and then above it, never
grown in place and then moved. A surface placed from its own size knows
that size only once it is laid out, so
[placement.js](../../frontend/src/shared/ui/placement.js) holds the one way
to place it:

- `placeFloating(size, {x, y, anchorRight, anchorBottom, flipY, bounds})`
  is the pure geometry (`tests/placement.test.mjs`): its own side of the
  point, the other side (`flipY`) when its own has no room and that one
  has, then inside the window and the `bounds` band.
- `menuAnchor(rect, {side})` is where a menu that opens from a button
  goes, as `ContextMenu`'s anchor, from the button's rect: its left edge
  on the button's, 4 px under it (`side: "below"`) or over it (`"above"`,
  with `anchorBottom`). Pure too (`tests/placement.test.mjs`);
  `useMenuTrigger` places its menus with it, and the browser suite's
  `assertMenuFrom` (harness.mjs) checks each of them there.
- `scrollBox(el)` is the box an element is seen through: its nearest
  ancestor that scrolls, else null for the window. A surface that follows
  the element (the selection toolbar, an ink layer's menus) keeps inside it.
- `useFloatingPosition(ref, place, deps, {follow})` measures and places it
  in a layout effect, before the browser paints it, and returns null until
  then, while the surface stands hidden. When its size changes later (a row
  of options opens, a translation streams in), the window's does, or, with
  `follow`, the page scrolls, it is placed again in that same frame
  (`flushSync`). A surface hidden this way (or while its anchor is out of
  sight) is hidden by `visibility`, which a child set `visibility: visible`
  overrides: nothing inside such a surface sets it, or that child shows
  alone where the surface stands. A child kept unseen for a while (a
  picture until it loads) is hidden by a rule that lets go of it after,
  so it inherits again (`.hlReviewShot:not(.loaded) img`). The browser
  also takes the focus from what it hides, dropping it to the page: a
  surface that holds the focus and hides while its anchor scrolls (the
  review card, `pdf/HighlightReview.jsx`) remembers the control the focus
  was on, and puts it back once shown when the focus is still on nothing.

The rule behind the second half: a state update from a ResizeObserver, a
scroll listener, a `requestAnimationFrame` or a passive effect renders
only after the browser has painted, so a position set there shows one
frame of the surface where it stood. The side is chosen by the surface
itself, from its measured size, never by a caller from a guessed one: the
handwriting and text box menus give `ContextMenu` the selection's edges
(`useSelectionMenuAnchor`) and it picks above or below. `ContextMenu`, the
PDF selection popup (`TipFrame`: from the point where the mouse was
released, when that is near the selection, else under the selection;
`pdf/selectionTip.js`) and the caret-anchored popups
(`useCaretAnchored`: "/", `[[`, `@`, the LaTeX completion and preview) and
a note's selection toolbar (`SelectionToolbar.jsx`: above the selection's
first line, else under its last, inside the box the note scrolls in,
`scrollBox`) and the review card of a highlight placed after its PDF was
replaced (`pdf/HighlightReview.jsx`: under the highlight, else over it,
following the page, [highlight_relocation.md](highlight_relocation.md#in-the-web-app))
are built on it; the guide card's anchor is measured the same way
(`GuideOverlay`). A surface placed in its event handler before it renders
(the tooltips, the citation preview) or by CSS under its button (the
popovers) has nothing to correct. The browser suite's
`assertOpensInPlace` (harness.mjs) checks a surface frame by frame.

A surface grows in from where it opens ([Motion](#motion)), so for a few
frames its rect is smaller than the box it lays out at. It is placed by
`surfaceSize(el)`, which takes that arrival scale back out, and never by
its rect. `growFrom(x, y, pos)` is the transform-origin that makes it
grow from the point it opens from, also when it took the other side or
was kept inside the window.

### Dialogs

`.reportOverlay` › `.reportModal` is the one dialog surface (settingsKit's
`SubDialog` wraps it for the settings editors). `DialogOverlay`
(`shared/ui/Surfaces.jsx`) renders the backdrop, so every dialog fades in
growing from its middle and leaves the same way ([Motion](#motion)). Confirm-style dialogs — the
shared `confirmBox`, the external-link prompt — add a `.confirmHead`: an icon
chip (`.confirmIcon`, `.danger` for destructive) leading a title plus one
line of explanation, the same shape as a settings `PaneHead`, over the
right-aligned `.reportModalBtns` row. Escape closes them.

A press on the backdrop closes a dialog only when it goes down and comes
up there (`backdropPress` in `shared/ui/press.js`). A click alone lands on
the nearest element both ends of the press share, so text selected in a
field with a drag let go past the dialog's edge would otherwise close it,
or raise the unsaved-edits question. Every `reportOverlay` dialog uses it
(Settings, its `SubDialog` editors, the confirm box, the link, label and
move-block dialogs, the chat's page picker); Quick Open closes on its own
`mousedown` check instead.

A dialog that holds work a close would lose asks first on the backdrop,
Escape and ×. An edited draft asks "Discard your unsaved edits?"; other
work names itself through `SubDialog`'s `guard` (`{question, keep,
leave}`). The import review before Import is the example: "Stop uploading
…?" while the file goes up, "Close without importing?" once the review is
in. The question goes away when the work ends on its own (the upload
fails). When it goes, by Keep or on its own, the dialog takes the focus
back from its button. The footer's Cancel stays direct: it is the labelled way
to stop. It asks only for a draft.

A dialog takes the focus as it opens, unless something inside it holds it
already, and gives it back on close to what had it as it opened. When
Settings opens with an editor dialog in it (the chat's AI tiles open it on
the connect dialog), the editor holds the focus, Escape closes the editor
alone and Settings has the focus back; a second Escape closes Settings and
the tile has it. Closing Settings closes its editor too, so the next visit
to the pane never shows the one left open.

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
anything else is final and fades after a second. It rises into place
when it comes, and a new message morphs out of the one it replaces
(`MorphText`, [Motion](#motion)); an error, which wraps to be read in
full, shows at once. `setStatus(msg, action)`
with an `action` `{label, run}` puts its button on the pill
(`.statusPill.interactive`), which then stays 8 seconds to be pressed.
Each source posts to a channel of its own (`postPill(channel, entry)`),
and the pill shows one entry: an error first, then a final message, then
any other, the newest first among equals. An entry with `question: true`
ranks with the final messages. So a question on its own channel stays
over a spinner or another button, and a newer final message shows over
it for its second and goes. The pens question is one
([handwriting.md](handwriting.md) "Presets follow the account").
`sayUndoable(msg, run)` is that with Undo, which clears the pill and
runs `run`. `undoableStatus(msg)` is the one whose Undo runs the
`app.undo` command: a deleted note says "Block deleted." with Undo
beside it, in reach without a keyboard. A stroke edit's pill (a snapped
shape, a scribble erase, a stamped line) comes from
`ink/useInkDocument.js`'s `undoableInkStatus(msg)`, whose Undo steps
the stroke history. A finger that scrolls a page while a tool is
armed and only a pen draws gets "Fingers scroll while a pen draws" with
**Draw with finger**, once a session and never after a pen has drawn
([handwriting.md](handwriting.md)).

A page opening says "Opening…" only once it has kept the reader waiting
300 ms (`OPENING_PILL_MS`, on a channel of its own), and takes it down as
the page lands. "Ready." answers it only for a page opened where none was
(the library, a link): a switch between pages is answered by the page
itself.

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
no modal in a popover; on a server that invites by e-mail
(`cloud.email_invites.members`) the section's action is a This server /
E-mail `Segmented`, and E-mail makes the box an e-mail field (`type=email`,
"name@example.org", invited to view); then `PersonRow`s (`aiProvRow`, from
the settings kit):
the requests to join that came through the link, tagged *asks to join*
with the e-mail address, an Approve `uiBtn sm` and a Decline
`uiBtn sm iconSq`; you, a shared workspace's members as one row tagged
*workspace*, each invited account with its own View / Edit `Segmented` and
a `uiBtn sm iconSq` remove (its sub line names its Gamma Cloud account
where that differs from its name here), each invitation by e-mail still
waiting (a manager's view only), named by its address with a mail icon,
tagged *invited*, "Invitation sent · joins once they accept" under it, its
View / Edit toggle, a resend `uiBtn sm iconSq` and a withdraw — the three
wait on one another, so a double click mails once — and each invitation
by cloud username still
waiting, tagged *pending*, with the same toggle and a withdraw), General access
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
Opened again on the same page or folder it shows the settings it had at
once and asks for them again in place; a first load, and the Gamma Cloud
section's state, come into focus where they will stand (`Reveal`).
Every change saves at once; nothing is a bespoke control. There is no
"reset link": stopping and sharing again mints a new address. The read-only
view's top bar is the visitor's side of it: the brand mark (the way to the
server's front door; a folder share keeps its Back to the shared folder
button), the title, the access pill (`sharing/ShareAccess.jsx`: an eye or a
pencil with "View only" / "Can edit", then "· shared by" with the owner's
avatar initial and name, the sentence behind it as its hover title; phones
keep the icon and the role), "as <name>" for an editing visitor without an
account, and one action: Sign in for a visitor with no account, "Add to my
library" for a signed-in one (on a page, and on a folder share's listing,
where it copies the folder browsed), "Open in my library" for the owner.

### Sign-in and status pages

Until the first real screen there is one splash: the one `index.html`
paints before the bundle, then the same one drawn by `Splash`
(`shared/ui/BrandMark.jsx`) while the server's config, the session and an
assistant's consent request are read. Its mark is drawn in place, not
loaded as a picture, and its spinner turns on from where it was, so no
hand-over shows; under reduced motion it turns slowly, as every spinner
does. The library's listing stays blank until it has come
back, so it never says it is empty before its rows arrive. A read that
fails ends the wait too: New page and New folder show under "Could not
load the library." and a Try again button.

`AuthShell` in `auth/LoginPage.jsx` frames every page shown before or
instead of the app: the login (also a share link's sign-in gate), an
unavailable workspace, a session conflict and a blocked share link. It
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
box, and a refused password also takes the focus and a red border. A
sign-in's outcome that is no failure — a request to join sent, or still
waiting — is the same box in the accent's tint (`.loginNotice`,
`role="status"`, a mail icon); a declined request is a `.loginError`. Where
a newcomer's Gamma Cloud sign-in asks to join (`cloud.join_requests`), the
`.loginFoot` says so, whom it asks (the person who shared the page, on a
share link's gate; else the admin) and that they see the username and
e-mail address.

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
- **`triggerPress(run)`**: a button whose menu opens on the press, as a
  native menu does, not on the click that follows. `useMenuTrigger`
  ([Menus and submenus](#menus-and-submenus)) is built on it.
  - `run` is called for a press with the primary button: a mouse's left
    button, or a finger's or a pen's contact, which is button 0 too.
  - It is called for a click no press led to as well (`detail` 0: Enter
    or Space on the focused button).
  - The click after a press belongs to the press, so it does nothing. The
    right and middle buttons do nothing either.
- **The timings** are exported (`HOLD_MS`, `HOLD_SLOP`, `DOUBLE_MS`,
  `DOUBLE_SLOP`) for gestures these helpers do not cover: the reorder
  drag (`shared/ui/useDragOrder.js`, below) and the finger-undo chords
  (`ink/fingerChords.js`) read them.
- **Where they are.**
  - `menuPress`: the library's rows, cards and chips, tabs, the Back
    button, highlights, their note badges and PDF links, the translate
    button, the note object frame, file chips, chat history rows.
  - `doublePress`: the dock grip's fold, a diagram's source, a picture's
    zoom, a resize grip's natural size.
  - `triggerPress`: the buttons `useMenuTrigger` drives (the recording
    player's mode and speed, the tool strip's More, Tool and Style, the
    selection menu's Style).
  - A widget built outside React (the editor's picture and table widgets,
    `BlockCmEditor.jsx`) attaches the same handlers with
    `addEventListener`.
  - `tests/press.test.mjs` covers the counting and which press or click
    `triggerPress` answers.

### Reorder by dragging: `shared/ui/useDragOrder.js`

A row or a list whose items the user puts in order drags them through one
hook, `useDragOrder({ containerRef, axis, start, onMove, onStatus, bounds })`:
the handwriting strip's presets along a row
([handwriting.md](handwriting.md), "Reorder by dragging") and Settings'
bar lists down a column ([settings.md](settings.md#bars-order-and-modes)).
The caller marks each slot with `data-order-id`, and their DOM order is the
order seen.

- **Where the drag starts.** `start: "hold"` is for a slot that is a button
  with a click of its own: a mouse drags it past 6 px (`DRAG_PX`), a finger
  or pen after holding it `HOLD_MS` and then moving, and a hold that never
  moved is its click. `start: "handle"` is for a ⋮⋮ grip with no click of
  its own (`touch-action: none`, `user-select: none`): any pointer drags it
  past 6 px.
- **What shows.** `drag` is `{ id, x, y, to, bar }` while a drag is live.
  The caller draws the 2 px `.dropBar` at `bar`, in the container's
  coordinates (so the container is `position: relative`), and a `.dragGhost`
  at `x, y`, and dims the source. "Drop to reorder." goes to `onStatus`.
- **Where it lands.** A release inside `bounds()` (the container by
  default) calls `onMove(id, to)`, `to` a visible slot, and eats the click
  that follows. A release outside, Escape, a window blur or a
  pointercancel leaves the order. Outside `bounds()` `bar` is null, so the
  drop bar shows only where a release lands. A list gives a looser
  `bounds` than its own rect: Settings' takes the pane's width and a row
  past either end.
- **The keyboard.** `keyProps(id)` on the focused slot or grip moves it a
  slot with the arrow keys along the axis, and to either end with Home and
  End, through the same `onMove`. A list says each new place in a polite
  live region ("Search: 2 of 7") and keeps the focus on what moved.
- **The order.** The caller's `onMove` applies `moveVisible`
  (`shared/model/barLayout.js`), so the items a filter hides keep their
  places between the ones moved round them.

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
the pick and leaves after it. The backup restore, the imports and the
markup strip's Picture button pick through it.

### Sizes and reveals: CSS asks capability

- **One size set.** Controls take the `--ctl-*` tokens ([Scales](#scales)),
  the same for every device: no `(pointer: coarse)` rule changes them. A
  thin target gets a larger hit area rather than a larger look (a resize
  grip's `::before`, a splitter's `hitAreaMargins`). Two sizes stay apart:
  the editing bar's 36 px buttons, which only a touch screen shows, and
  the text box's handles under `(pointer: coarse)` ([text_boxes.md](text_boxes.md)).
  `tests/designTokens.test.mjs` fails on any other `(pointer: coarse)`
  rule that sets a width or a height.
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
  (`onFold` through `doublePress`). Folded, the header is a bar in its
  slot beside the open windows (`DockBar`), and one press anywhere on it
  but a button opens the window. When that press was the first of a
  double-click, the second, landing on the opened grip within 800 ms,
  does not fold it again. A finger's double tap folds on its second
  release, and that tap's click, landing on the bar where the grip was
  (Notes, first in their column), does not open it again.
- **A fold keeps the window.** The folded window stays mounted in its
  panel, which takes no room (`.dockPanelFolded`): its body is inert and
  out of sight, at the size it had as it folded, so nothing in it lays out
  again. The focus leaves it, so a note's editor open there closes, and no
  editor opens there while it is folded (`useDockFolded`): the one a
  highlight made on the PDF opens, or an empty page's first block, mounts
  with the focus and its editing bar as the Notes open, and the jump to
  its row and the chat's cursor chip wait for them too. The window opens
  in the frame of the press on what it held, with nothing read again: the
  chat's conversation at the place it was read, a half-typed message, the
  notes at their scroll. While another window of the slot is open, a
  folded panel is pinned at no size, so the slot's sizes are the open
  windows' and a splitter stands only between two open windows. The slot
  keeps a saved layout for each set of folded windows.
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
2. A menu from a toolbar button: `useMenuTrigger`, the button spreading
   its `trigger(kind)` and the menu rendered outside the `InkTooltips`
   host ([Menus and submenus](#menus-and-submenus)).
3. A second action on an item: spread `doublePress(run)`, never
   `onDoubleClick` alone.
4. No `pointerType`, `(pointer: coarse)` or `maxTouchPoints` in feature
   code: ask `pointer.js`.
5. A control shown on hover: hide it under `@media (hover: hover)` or
   with `pointer-events: none` beside its `opacity: 0`, add the
   `:focus-visible` twin, and decide what shows it where nothing hovers.
6. Sizes from `--ctl-*`, and what sits around them by `calc()` of them.
7. Drag alone is not enough: what a drag files or orders has a menu row
   too (the folder menu's Move to, Move up, Move down), or keys on its
   grip (a Settings bar list, `useDragOrder`'s `keyProps`).
8. A text field's Enter handler returns early while an input method
   composes (`composing` in `chat/enterKey.js`), and an `autoFocus` that
   would raise an on-screen keyboard is `!touchTyping()`. A username or
   account field turns capitalization and correction off.
9. A file pick goes through `pickFiles`.

## Motion

Motion says where a thing comes from and where it goes. It never
decorates and never makes anyone wait. The timings, easings, travel,
scale and blur are tokens ([Scales](#scales)), and a value is chosen by
what the motion does, not by the nearest number:

- **Coming is slower than going.** A surface arrives on `--dur-slow` and
  leaves on `--dur-base`. A toast or a panel arrives on `--dur-long`. Hover,
  press and a tooltip's going take `--dur-fast`.
- **What moves eases out.** A move, a scale or a resize takes `--ease-out`,
  both ways: a quick start that settles softly. A cross-fade between two
  things in one place takes `--ease-swap`. A badge or a switch's thumb may
  overshoot once with `--ease-pop`, nothing else does. A colour or opacity
  fade leaves its easing out.
- **Small distances.** A surface grows from `--scale-in` and shrinks to
  `--scale-out`. Text lifts `--move-sm`, a toast `--move-md`, a panel
  `--move-lg`. A cross-fade passes through `--blur-swap`, which makes a
  short move read as a whole one.
- **Name the properties.** A transition lists the properties it animates,
  never `all`, so an unrelated change does not ride along.
- **Reduced motion ends everything at once.** The global rule in app.css
  ([Scales](#scales)) covers every recipe; a component adds a rule of its
  own only for a different still state.
- **A theme changes in one frame.** Every colour switches together: for
  the frame that draws the new theme the root carries `.themeSwitch`
  (`applyTheme`, `app/theme.js`), whose rule in motion.css turns every
  transition off, so a button or a row never eases from the old theme's
  colour while the rest has changed.

### Surfaces come and go

`shared/styles/motion.css` holds the recipes, keyed by the surfaces'
classes, so a new menu, popover or dialog moves with no code of its own.
They animate the individual `scale` and `translate` properties, never
`transform`, so a surface's own centring transform stays its own. Both
rest at none, so nothing a surface holds is positioned against it.

| Surface | Arrives | Leaves |
|---|---|---|
| Menus (`ContextMenu`) and popovers (`Popover`) | fades in growing from `--scale-in` over `--dur-slow`, from the point it opens from: `ContextMenu` sets that origin (`growFrom`), a popover grows from its button's corner (top right; `.popUp` bottom left; bottom centre in the compact shell's bar) | shrinks to `--scale-out` fading over `--dur-base` |
| Flyouts (`SubMenuItem`) | fade in over `--dur-fast`: growing, they would make a pass down the rows feel slow | at once |
| Dialogs (`DialogOverlay`) and the lightbox | the backdrop fades in over `--dur-base` while the dialog or picture grows from its middle over `--dur-slow` | together, over `--dur-base` |
| Tips and the citation preview | fade in where they stand over `--dur-base` | at once |
| The PDF selection popup, a note's selection toolbar and the caret's popups ("/", `[[`, `@`, LaTeX) | fade in over `--dur-fast`, so typing never waits on one | at once |
| Toasts: the reminder cards, the status pill | rise `--move-md` into place through `--blur-swap` over `--dur-long` | sink back out over `--dur-slow` (the cards; the pill fades) |

React takes a closed surface out of the page at once. `useLeave(kind)`
(`shared/ui/leave.js`), the callback ref on its root, puts a still copy
of it where it stood, and the copy plays its kind's way out
(`[data-leaving="surface" | "dialog" | "toast"]`) and goes.
`DialogOverlay`, `Popover`, `ContextMenu`, `ThreadPopover`, `Lightbox`
and the reminder cards carry it. The copy is plain DOM outside React:
inert, `aria-hidden`, without ids or guide anchors, and it takes no
pointer, so nothing in it answers a press or a key. What stands in its
DOM but over the window (a sub-dialog, the caret's popups) goes at once,
so the copy holds none of it. A surface that arrives takes the place of
a copy of its kind still leaving, as a native menu does: only one of a
kind is ever seen going, and never while another stands. A dialog opened
as one closes keeps the dim that was there (`data-over`). One opened
inside a dialog still arriving (Settings opened straight onto an editor,
as the chat's AI tiles do) waits for it to land, unseen, then arrives
itself (`data-wait`): growing together, its backdrop was laid out inside
the growing dialog and snapped to the window as it landed. Toasts stand
side by side, so a toast takes no other's place, and one leaving a stack
that moves (the reminders, hung from the window's bottom) leaves from
where it was seen. Under reduced motion there is no copy. Code that asks
whether a surface is open skips the copies and what they hold
(`:not([data-leaving], [data-leaving] *)`, as `topDialog()` in
appCommands.js does). A box read while a surface is arriving is the
smaller one it grows from: the guide's card waits for its anchor to land
(`arriving(el)`, `shared/ui/motion.js`, which counts only the arrivals
that move or size what they hold).

A pane that takes another's place comes into focus where it stands (a
Settings pane, keyed by its name; a window the compact shell's bottom
bar shows; the library's listing for another view, kind filter, folder
or label, its `.listingIn` played again on it in place), and the guide's
card rises `--move-sm` into place.

A page that takes another's place does not move at all: the switch is
instant and whole, since a fade would only make the reader wait. The tab
pressed is selected in the press's own frame (`selectTab`), the page it
leaves stays on screen, whole and in use, until the new page's tree lands
(`openBlock`), and the new page then takes its place in one frame, its
threads asked for alongside its tree and a PDF's pages drawn where they
were left, with their highlights and margin cards ([pdf_loading.md](pdf_loading.md)
"Coming back to a document"). Until it lands, a press on the tab of the
page on screen, or on the close of the tab pressed, takes the press back:
the open is let go, and the page on screen keeps its tab, not read again.
What an open was asked to land on or do (a `[[ref]]`'s block, the home
menu's Share…) goes with it when a later open overtakes it.

### Things that change in place

What changes without leaving its place uses a widget from
`shared/ui/MotionWidgets.jsx`, whose recipe is in motion.css. At rest each
is plain markup, and a first render never moves.

- **`IconSwap`** `swap from to`: two icons in one slot (copy and its
  check, play and pause, an eye and a struck eye). The one going shrinks
  into `--blur-swap` as the other grows out of it, on `--ease-swap`. Both
  stay in the DOM, so the slot never changes size. Not for an icon chosen
  by an item's kind, and not for a state with a spinner.
- **`MorphText`**: a short label that changes in place (Copy → Copied,
  Save → Saving…). The letters both texts share at each end stay; the
  part between cross-fades and its width eases, so the button around it
  resizes smoothly. Mid-swap, assistive technology reads the new label
  whole. It shows the new label at once under reduced motion, when the
  label changes faster than a swap takes (a progress count), and when an
  ellipsis cuts the old or the new one, and while `still` holds (a label
  a load changed, as a chat reply's read again). Not inside a surface
  placed from its size (the selection popup): its width would move it
  every frame.
  The chat's reply pill is one label from the start of a turn:
  "Thinking", each step running, "Responding", then its summary, every
  change a morph.
- **`Collapse`** `open still`: a body under the header that opens it. Its
  row grows from nothing over `--dur-slow` while it comes into focus, and
  folds over `--dur-base`; shut, nothing is rendered, and while it folds
  the body stays, inert. In a column with a gap, the gap above it folds
  with it; while it moves nothing scrolls it (a field focusing itself as
  it mounts). While `still` holds it opens and shuts at once: the outline
  moves only the fold a person makes, and a jump that reveals a block
  opens its parents still, since it scrolls to the block right away. The
  outline's bodies fade in without the blur. Not inside a surface placed
  from its size, which would move as it grows. A native `<details>` opens
  the same way where the browser animates `::details-content`.
- **`Chevron`** `open dir`: one glyph that turns with what it opens:
  `"right"` turns down, `"down"` flips up. A text caret ▸ turns the
  same way with `.turnCaret`.
- **`useSlidingPill(ref, value)`**: the chosen option's frame in a
  segmented group slides to the next one chosen (`Segmented`, the
  library's view and kind toggles). The group renders a `.slidePill` as
  its first child; its stylesheet gives the pill its look.
- **`.shimmerText`**: text that says work is under way (the chat's
  Thinking) carries a soft light across its letters while it holds.
- **`.countIn`**, keyed by the number: a badge's number rises into
  place when it changes; the Inbox badge also pops on when it comes.
- **`FADE_IN`**, spread on an `<img>`: a picture fades in once loaded
  (the library cards' snapshots, the Import and Export dialogs' Zotero
  mark); one already in the cache shows at once.
- **`.loadingHint`**: a lone "Loading…" placeholder that fades in only
  after `--dur-slow`, as a `Reveal`'s fallback does, so an answer that
  comes quickly takes the place of a blank, never of the word (the chat's
  History popover on its first read). What
  was read before shows instead while it is read again in place: the
  History popover keeps each page's list, the chat keeps the last twenty
  conversations it showed. Under reduced motion the word shows at once.
- **`Reveal`** `ready k fallback`: where a first load lands (the share
  popover's settings, the Inbox's listing, Recently deleted's pages).
  While it waits it holds, blank, the height its content last had under
  `k`, and its `fallback` ("Loading…") fades in only after `--dur-slow`,
  so a load that answers fast never shows it. Content that takes the
  place of a placeholder comes into focus as a pane does; content ready at
  once is plain. It is a column, and it never eases a height: what holds
  it changes size once, as the content lands, so it is not for a surface
  placed from its size (the thread popover), which would jump. Under
  reduced motion the fallback shows at once. Content already there is
  better than any placeholder: the share popover keeps a target's
  settings while it asks for them again, the Inbox opens each tab on the
  first page it showed last and refreshes it in place, and Recently deleted
  asks for its list with its module, as the pointer or the focus reaches
  its button, so it lands whole. A Settings pane does the same: its reads go
  out as the pointer or the focus reaches its sidebar button, the pane
  before stays until they are in (at most 250 ms), and a pane read before
  shows at once and is read again in place ([settings.md](settings.md)
  "What a pane reads"). Search results, which change with every key, take
  none.
- A switch's thumb travels with `--ease-pop`, its one overshoot.
- **`.shakeNo`**, replayed by removing and adding it: a field that
  refused what was typed shakes once, a small no (a wrong password at
  sign-in).

The desktop shell's and the extension's own stylesheets move on the same
tokens (their copies of tokens.css): the switch's thumb, the buttons, the
extension's drawer and cookie badge.

### Lists that change

A list whose items are added, removed or reordered in place wraps them in
`ListMotion` (`shared/ui/ListMotion.jsx`), which has no box of its own.
Its items are the elements under it with a `data-motion-key`, unique in
the list. An item may hold others, as a row's children body holds rows,
and what it holds travels with it.

- **What moves glides.** The page is laid out at once in its new order.
  An item whose place changed is drawn from where it stood and glides to
  its place on `--dur-slow` `--ease-out`: a Web Animation of `translate`
  ("list-move"), so the item's own animation and transitions are left
  alone, and it carries `data-moving` meanwhile. One interrupted starts
  again from where it was seen.
- **What comes comes in where it will rest** (`enter`, motion.css
  `[data-entering]`). `"fade"`, the default, fades in a moment late, so the
  items around it have opened its room first: an outline's row. `"rise"`
  lifts `--move-sm` into place: a message, a tab. `"card"` grows in as a
  surface does. A fading item never travels, so a box read right after the
  change is its own. A new item that holds one that was already there,
  such as the body a row was just indented into, does not come in. While
  it comes in the recipe stands in for the item's own animation, so an
  item in such a list has no arrival of its own.
- **What goes leaves from where it stood** while the rest closes up over
  its place (`leave`, `[data-leaving]`): `"row"` fades, `"tab"` sinks back,
  `"card"` shrinks away. It goes through a still copy made while it stands
  (`stillCopy`, leave.js), so the list renders the `keys` it holds, in
  order, for ListMotion to see which went. The copy drops its key and the
  attributes in `strip`, those app code finds the list's items by. Without
  `leave` it is gone at once. Its holder should position it
  (`position: relative`), so the copy is clipped and scrolled with the
  list.
- **Nothing moves** on a first render, in a new `scope` (another page,
  another filter), for a change marked `still` (a load, another client's
  or the agent's edit, a text box's measured size), while it is `off`,
  under reduced motion, or while the list is out of sight. It plays only
  when `shape` changed (by `same`; with neither, the `keys`). Only what is
  near the window moves, and a change that moves more than 60 items or
  brings or takes more than 8 does that part at once (a paste of
  many blocks, Mark all read). `moves={false}` leaves the places alone
  where they transition already (the comment margin's cards).
- **Boxes are the window's**, so an insert above the view that scroll
  anchoring absorbs moves nothing, and a scroll the change forces on the
  list's own scroller glides what it carries: a strip scrolled to its end
  that a close narrows scrolls back, and what is left closes up from the
  left. A glide is turned into the item's own pixels, so an item starts
  from where it stood under a text zoom around the list and under a zoom
  of its own (the Interface size every button carries, as the Comments
  window's rows do).
- **A popup over the window stays put.** An item holding what matches
  `hold`, such as the caret's "/" or `[[` menu, stays still, since a
  moving ancestor would carry it off. Text typed or pasted in the list,
  from any keyboard or input method, and a press outside the list (the
  editing bar's Insert…) land every glide at once, so a popup they open is
  placed against a row at rest. A press in the list aims at what is drawn
  and lands nothing.

`arriving(el)` counts a glide as an arrival, so the guide's card waits
for its anchor's row to land.

The notes outline is the first list on it. Its rows and their children
bodies are the items, with the agent's row still being written (a
create_block streaming, which glides with its neighbours when the person
changes the tree above it) and the backlinks and the tail under them; it
plays only when the tree's shape changed (`sameShape`,
`shared/model/blockModel.js`), so a keystroke or a fold moves nothing.

The chat's transcript is another: a question sent and its reply rise in
together (`enter="rise"`, `moves={false}`, keyed by their places, one
scope per conversation), while a conversation loaded, switched to or read
again shows still: its messages are the ones a load just delivered. Its
rows are keyed by the conversation too, so a switch renders its own from
the first frame, and in a conversation that came from a load each reply's
steps pill opens and shuts at once and its label changes without a morph
and is not read out: only this tab's own reply is a live region.

The tab strip (`OpenTabs`) is another. A tab opened rises in, one closed
sinks back out where it stood while the tabs after it glide over its
place, and a tab dragged along the strip moves the others through the
same glides. A tab still gliding under the pointer is not one to swap
with, so a swap never swaps back. Closed at the end of a strip scrolled
there, a tab sinks out where it was seen as the tabs before it glide over
its place from the left. Tabs read from this browser's cache or the
server's copy come still, and so does any change before the person has
pressed or typed anything in the window (the page it opened on getting
its tab): that is the window loading. The strip is as tall with no tab
as with one, so the topbar never grows as the first one opens.

The library's listing, in both views, is another. A row comes in fading
and goes fading, a card grows in and shrinks away, and a delete or an
item its sort moves glides the rest. A listing's `scope` is its view, kind
filter, folder, label, sort, filter text and how many rows Load more has
shown, so typing in the filter box, opening a folder or Load more moves
nothing: the rows arrive at once. Its holders (`.fileList`, `.fileGrid`)
position the copies. The listing mounts once it has loaded (or its read
has failed), so the first one a load or a page left brings is a first
render. The reads that are loads come still: the listing read again on
coming home, on the window's wake or as a metadata lookup ends, an import
started elsewhere landing, and the server's view history landing after
it. Each marks what it sets, and a change is still when all it
changed was read that way, so a delete right after a load still moves.

The small lists are on it too. An empty state can be an item of its own
(its key "empty"), so it fades in as the last item leaves; a section's
header is one, so it goes with its last row.

- **The Inbox**: its rows and day headers; a row read in Unread or
  archived leaves (`"row"`) and a new one fades in. Each tab is a scope,
  and Mark all read empties the tab at once (`still`).
- **Background tasks**: a task that finishes glides past the header to
  the top of Finished (with nothing else running, In progress goes with
  it and Finished's header glides up), one removed leaves (`"row"`), and
  Clear finished takes them all at once.
- **The PDF margin**: a card grows in and shrinks away (`"card"`), and
  `moves={false}`, since stackCards places the cards and their `top`
  moves them on `--dur-slow`. A new comment sent shows in its card over
  the emptied field while it is on its way, and stored, the thread's card
  takes that card's place still, where it stood: the two never stand
  together.
- **The Comments window**: a thread rises in (`"rise"`) and one with a
  new reply glides to the top. A list that fills from none (the page's
  threads loading) comes in still.
- **A thread's comments**: a reply rises in. A margin card opening to its
  whole thread is another scope.

The copies drop the ids the app and the tests find these items by
(`data-inbox-id`; `data-card-for`, `data-thread-id`, `data-comment-id`).

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
  Blocks, or the placements below. Blocks uses `POST /api/markdown-blocks`,
  the Markdown import parser, and inserts siblings, replacing an empty block
  without children.
- Other multiline text keeps its shape and moves to the level of the line it
  lands on (`mdLines.pastePlacements`; not in a code fence or `$$` math). The
  chooser names the placements by that line's container: At this level (in
  its quote, or beside its list item) / Nested (in a quote inside it, or under
  the item) / As copied / Blocks. Lines copied out of a quote merge into the
  quote they land in; a copied callout keeps its own level and stays a box.
  In a list, pasted lines of text become items. A single line pasted onto an
  empty item takes its place without the chooser (`- eggs` onto `> - ` is
  `> - eggs`). Blocks parses the text as copied and puts the line back as it
  was (`restore`).
- A clipboard containing one HTML table becomes a formatted Markdown table.
  Files upload and insert at the caret ([files and documents](block_centric.md#stage-4--files-and-documents-done-2026-09-13)).

An editable `![[embed]]` card writes to its source block, including checkbox,
image, table, and raw-text edits. Same-page sources use `onChangeText` and the
page's normal save path; cross-page sources use `PUT /blocks/{id}` with `base`
and reconcile the reference cache with the response ([collaboration](collab.md)).
Its editor shares `useMathUi` (`editor/useMathUi.js`: completion and
preview), and image/table paste with the ordinary row editor. The card footer navigates to the source.

### The selection toolbar

Selected text in a note's open editor gets Notion's floating bar
(`editor/SelectionToolbar.jsx`): bold, italic, underline and
strikethrough; inline code, inline equation and link; highlight and Color.
Every tool but Color is a block command run with the row's
`commandContext`, so a button does what its key does, and its title names
the account's chord. The command's `state` ([hotkeys.md](hotkeys.md))
presses the button (`.modeActive`, `aria-pressed`) where the selection
carries the mark and disables it where the command would do nothing. The
bar does not show where no tool applies, such as math or a code fence.
Color opens two rows under the tools: Default and the eight text colors,
then Default and the eight background tints (`TEXT_COLORS` in
`editor/mdMarks.js`, the palette the "/" color commands write). Each
choice is an "A" drawn as the text would come out, and the selection's
own color is the pressed one. A choice recolors the selection
(`markCommands.colorPlan` over `mdMarks.recolor`): part of a colored run
splits it, runs that end up alike merge, and Default takes the color off.

When it shows is `editor/useSelectionToolbar.js`. A mouse selection shows
it once the button is released, so a drag or a double-click never has it
jump along. A keyboard selection shows it at once. Escape puts it away
until the selection changes. On an on-screen keyboard it stays hidden,
and the editing bar has the same tools. It sits above the selection's
first line, or under its last when there is no room, inside the box the
note scrolls in. It is placed before it is painted and follows that box's
scrolling (`useFloatingPosition` with `follow`), and it is out of sight
while the selection is scrolled away. Its buttons take no focus, and a
press is kept from the editor and the row, so the selection stays where
it was. It joins the shared floating surface, at `--z-refpopup` with the
editor's other popups.

## File map (frontend/src)

| File | Owns |
|---|---|
| `app/App.jsx` | routing, block-tree editor state, docks, the page's live session glue, AI chat glue (decomposition in progress) |
| `collaboration/usePageCollab.js`, `shared/model/blockOps.js`, `collaboration/Presence.jsx` | the live session (ops out, ops + presence in), the pure tree diff/apply, the avatar stack / row chips ([collab.md](collab.md)), and `PersonAvatar`, the avatar of someone not on the page by username (a share's owner, a person to mention, a comment's author, an Inbox item's actor) |
| `collaboration/MirrorPopover.jsx`, `collaboration/MergeResolver.jsx`, `collaboration/conflictKinds.js`, `settings/SettingsMirrors.jsx` | a clone's sync pill with its settings and conflicts views, the conflict card + the row chip, what each kind of conflict row says (pure), Settings → Workspaces → Clones and Zotero links ([mirror.md](mirror.md)) |
| `app/boot.js`, `app/theme.js` | the first load's reads, sent as `main.jsx` starts and taken once by the code that would ask for them (`startBootReads`, `takeBootRead`: the server's config, the session), and the account's theme and language put on from the session's `profile` (`adoptAccountLook`, `languageSeen`; [settings.md](settings.md), [i18n.md](i18n.md)); the theme list (`THEMES`, `DARK_THEMES`, `themeScheme`) and `applyTheme`, which changes every colour in one frame ([Motion](#motion)) |
| `app/prefDefs.js`, `app/prefs.js` | every preference's key, default, codec and scope (`PREFS`); the hooks that make them state (`useAppPrefs`) and sync the account-scoped ones (`useProfileSync`) |
| `shared/model/barLayout.js`, `app/topbarItems.js`, `app/viewerToolItems.js`, `settings/BarLayoutSection.jsx` | a bar's order and modes (`defineBar`: the preference's codec, the edits, where each item goes) and the two bars on it: the topbar's configurable buttons, each on the bar or a row of the account menu with its popover anchored where the button would be (`.offBar`), and the viewer strip's tools, each on the strip or a row of its ⋯ menu; the Settings list that orders them ([settings.md](settings.md#bars-order-and-modes)) |
| `shared/ui/useDragOrder.js` | the one drag-to-reorder, by a hold on a button or by a grip, with its keys ([above](#reorder-by-dragging-shareduiusedragorderjs)) |
| `settings/SettingsDialog.jsx` + the `settings/Settings*.jsx` panes | the Settings dialog (`SettingsKit.jsx` holds the shared primitives incl. `AccountPicker`, the search-box-over-account-rows people picker, `PersonRow` (one person in a list of who has access or who is invited: the share popover, a workspace's members, Settings → Users' invitations), and `LogBox`) |
| `settings/settingsReads.js` | what the panes read through one store while the dialog is open: `PANE_READS` (each pane's paths, in order), `useSettingsRead(path, {poll})` for a section, `useSettingsReads` for one whose paths it learns as it goes ([settings.md](settings.md) "What a pane reads") |
| `settings/sectionPrefs.js`, `settings/syncState.js` | which account preferences each settings section holds (`SECTION_PREFS`), and how their sync reads: a section's scope tag (`profileSyncState`) and the Settings sync row's hint (`cloudSyncHint`) ([settings.md](settings.md)) |
| `settings/SettingsAppearance.jsx`, `settings/SettingsLibraryDisplay.jsx` | the Appearance pane (theme cards + PDF sample) and its Library section (a live `PageCard` beside its switches) ([settings.md](settings.md)) |
| `settings/BackupTasks.jsx` | Settings → Backups' task table and its editor `SubDialog` (scope `Segmented`, workspace and weekday `ToggleGroup`s, frequency and retention `MenuSelect`s, the cron preview); each row's `ActionMenu` ([settings.md](settings.md)) |
| `settings/SettingsCloudSignIn.jsx` | Settings → Server › Sign-in (the account server, the server client, the unknown-accounts policy) and the Account pane's Gamma Cloud row ([cloud_accounts.md](cloud_accounts.md)) |
| `transfers/ImportExport.jsx`, `transfers/transferFormats.js`, `shared/illustrations/` | the Import/Export dialogs, their format/source rules (`resolveExport` / `resolveImport`) and the decorative previews ([import_export.md](import_export.md)) |
| `transfers/ImportReviewDialog.jsx`, `transfers/ImportTree.jsx`, `transfers/importApi.js`, `transfers/importReview.js` | the import review: upload → review → import → summary in one `SubDialog` (filter `Segmented`, source and destination trees with checkboxes), the `/api/import/review` calls, and the pure selection/filter/tree helpers ([import_export.md](import_export.md)) |
| `shared/lib/xhrUpload.js` | the one multipart upload: progress, a processing callback, abort by function or `AbortSignal`, the workspace and tab-identity headers; behind `FileChip.postFile`, PDF uploads, backup restores and the import review |
| `shared/lib/keepalive.js` | the page's one ledger of `fetch` `keepalive` requests: each sender asks it for room in the browser's 64 KiB of keepalive bodies by priority (the queued page edits, then the small recoverable requests, then handwriting), and a request that does not fit goes as a plain fetch ([collab.md](collab.md) "Keepalive") |
| `pdf/noteAnchor.js` | where a highlight's note badge sits: the geometric last line of its rects, not the last stored rect |
| `shared/model/gammaLinks.js`, `GammaLinkCard` / `CitationPill` in `shared/ui/Widgets.jsx` | links into this library (page / block / citation) classified once and drawn the same in the chat and in notes: a page or block link as a card, a citation as a compact pill with a hover preview ([pdf_citations.md](pdf_citations.md)) |
| `pdf/pdfCitation.js`, `pdf/PdfCitationOverlay.jsx` | a citation link → the quoted passage highlighted on the cited PDF page ([pdf_citations.md](pdf_citations.md)) |
| `shared/lib/copyImage.js` | a shown picture (an `<img>` or an inline `<svg>`) to the clipboard as an image: the async clipboard writes only `image/png` and, in current Chromium, `image/svg+xml`, so a PNG goes as it is, another bitmap is redrawn as a PNG pixel for pixel, and a vector picture goes as its SVG at the shown size on the shown background (`shownSvg`) plus a PNG of that at twice the size. The `ClipboardItem` takes promised blobs, so Safari keeps the click's gesture; `canCopyImage` is false outside a secure context, where no legacy copy carries an image |
| `shared/lib/canvasSize.js`, `pdf/verticalScrollSnap.js` | the canvas backing-store cap and the one-finger vertical scroll alignment ([pdf_loading.md](pdf_loading.md)) |
| `shared/lib/viewerZoom.js`, `shared/model/zoom.js` | the Ctrl+wheel and two-finger pinch/pan gestures both zooming viewers read, and the zoom limits every entry point clamps to (above) |
| `shared/lib/stableActions.js`, `shared/lib/keepIfSame.js` | what a memoized row or page relies on: one stable wrapper per handler name calling the latest handler (`useStableActions` for the page tools, `makeStableActions` for the outliner's `rowProps`), and a derived map or object kept by identity while it reads the same (`keepIfSame` with `sameMap` / `sameObject`) ([frontend-refactor.md](frontend-refactor.md)) |
| `chat/ChatDock.jsx` | the AI chat panel (incl. agent wiring); header = a `.ctlBtnRow` of `.ctlBtn` icon buttons (the PDF zoom column's buttons laid flat) with the ⚙ settings popover |
| `pdf/PdfViewer.jsx` | the custom pdf.js viewer. A highlight's colour (`#rrggbbaa`, `shared/model/palette.js`) rides in `--hl` beside the dark-page variant the viewer derives for it (`darkPageColor`: the same hue, saturated, at a middle lightness, mostly transparent; `--hl-dark`, and `--hl-dark-line` for an area outline, which also draws a commented passage's underline on a light page), so on a dark page — Flip page colors or Gamma Dark — app.css draws that with normal blending in place of the screened pastel, whatever app the colour came from. The selection popup's swatches are the account's wash palette (`usePalette`, `shared/ui/paletteContext.js`). A jump to a highlight (its note's dot, a deep link, a search hit) pulses it once in its own hue: App's `triggerFlash` → `flashHighlightId` → `.pdfHlFlash` on its rects, a still ring under reduced motion |
| `ink/ink.js`, `ink/finishStroke.js`, `ink/inkInput.js`, `ink/stabilizer.js`, `ink/shapes.js`, `ink/gestures.js`, `ink/fingerChords.js`, `ink/stripKeys.js`, `ink/pencilActions.js`, `ink/hostInk.js`, `ink/ruler.js`, `ink/shapeTool.js`, `ink/zoomWindow.js`, `ink/surfaceScale.js`, `ink/inkStore.js`, `ink/inkFlush.js`, `ink/inkJournal.js`, `ink/inkLive.js`, `ink/grain.js`, `ink/InkStylePicker.jsx`, `ink/ZoomWindow.jsx`, `ink/InkLayer.jsx` | handwriting ([handwriting.md](handwriting.md)): the stroke codec + geometry (pure), the finish pipeline and brush table (the only width laws), pointer sampling, Smoothing, the shape recogniser, scribble detection, finger chords, the strip's keys, the Pencil mapping, the iPad shell's side of a stroke, the ruler, the Shapes tool's drag, the zoom window's rules, a screen distance in a surface's points (Duplicate's offset), the files/drafts store, the flush plan, the drafts journal, the peers' wet strokes, the pencil grain, the Style picker, the zoom window's panel, and the page layer + selection menu (`.inkEditMenu`, placed by `useSelectionMenuAnchor`) + notes card + `ColorChoices`, the swatches and custom colour that a pen preset's row, a text box's style and the selection menu's Color and Fill rows share |
| `markup/MarkupToolbar.jsx`, `markup/MarkupLayers.jsx`, `markup/PageTools.jsx` | the tool strip (`.pdfInkBar`: `ctlBtn`s and `colorBtn` swatches; the Text tool's `TypeIcon`), the one mount of a page surface's layers (text boxes, then ink), and the tools context they read, with `armedClasses` for a surface's armed-tool classes ([handwriting.md](handwriting.md)) |
| `markup/TextBoxLayer.jsx`, `markup/useTextBoxes.js`, `markup/textBox.js`, `markup/markup.css` | text boxes ([text_boxes.md](text_boxes.md)): the boxes of a page (frame, width handle and band in screen pixels over a box sized in the page's points), the box menu (the handwriting menu's `inkEditMenu` rows: `TextSizeIcon`, `PaletteIcon`, `PaintBucketIcon`, then Duplicate, Show note, Delete), and `TextStyleChoices`, the size numbers, the pen palette through `ColorChoices`, and the four backgrounds ("none" a struck-out swatch, `.textNoBackground`) that the menu and the strip's Text row share; the notes row's `TypeIcon` marker (`.textBoxMarker`) |
| `search/SearchPanel.jsx`, `search/snippets.js`, `library/librarySearch.js` | workspace search (Ctrl+F); `MarkedText`, the query's matches as `mark.searchMark` (quick open's rows and the `[[` picker's too), over the pure result text of `snippets.js`; the title scorer it shares with chat, and the library matcher (title + folder/label chips) shared by the home filter box and quick open |
| `library/QuickOpen.jsx` | quick open (Ctrl+P): a `.reportOverlay` palette over the library ([home_library.md](home_library.md)) — no query: recents, open tabs, the rest; a query: pages, folders and labels in sections, then the search-everywhere and create-page actions; rows are the chat mention picker's `chatMentionOption`; a query starting with `>` (Ctrl+Shift+P) is the command palette over App's `paletteCommands()` ([hotkeys.md](hotkeys.md)) |
| `library/PageOption.jsx` | a library page as a picker option, for quick open and the chat mention picker: kind glyph, marked title, authors and year, folder/label chips, then a check, "Current" or the time; also the Recent / Open tabs / Everything else headings (`recencySections`) |
| `chat/PaperMentionInput.jsx`, `chat/paperMentions.js` | chat mention picker (quick open's page list over the composer), mention text edits and `MAX_CHAT_REFERENCES` (six attached pages plus the current page) |
| `chat/ApprovalCard.jsx`, `chat/approvals.js` | the card a tool call waits on while the user decides ([ai.md](ai.md#asking-before-a-call-approvals)): the fetch handoff card's look (accent border and tint), a caption naming the permission, the change as a word diff (`del` on `--danger-bg`, `ins` on an `--ok` tint), **Allow once** as the one primary button, **Don't allow** ghost; the buttons never take the focus themselves. A card for a call naming several pages has a counted headline ("Rename 37 pages"), one diff line per page in the same scrolling box, and `.chatApprovalNote` lines for a folder or labels made first, the pages left out and the pages already so. An ask-only permission's card (Delete folders and labels) shows only **Allow once**, as `uiBtn sm primary danger`, and **Don't allow**, and raises no hint. A permission's state (Allow / Ask / Off) is a `MenuSelect` whose icon is tinted by state (`.agentPermState` in settings.css) |
| `chat/AgentChanges.jsx`, `chat/aiRevert.js` | what an agent reply changed, under its steps pill ([ai_tools.md](ai_tools.md#reverting-a-note-change)): `.chatChanges` rows behind an accent rule, a note row, a deleted folder's or label's row, or pages renamed, moved or labelled (one or many), ending in a frameless `ctlBtn` undo icon, "Revert all" a `uiBtn sm ghost` in the notes group's head only, a reverted row muted with its "Reverted" tag and a redo icon in the same place; a call that changed several pages is one row whose muted "Show the pages" `<summary>` (a native `<details>`, `.chatChangeItems`) unfolds each page's one-page row in a box that scrolls past 16em; a row that stops shows its reason, the approval card's `WordDiff`, and **Revert anyway** (`uiBtn sm`) beside **Keep it** (ghost) — a stop that can't be forced (`gone`, the pages' `kept`) shows **Dismiss** alone; a revert or redo that left some pages as they are adds a `role="status"` note with **Dismiss** only |
| `chat/chatErrors.js`, `chat/agentSteps.js`, `chat/chipText.js` | pure helpers of the chat panel: the error card's copy per failure kind (also the login check's strip), the agent's steps pill and change list, a context chip's plain-text preview ([ai.md](ai.md); its words through `search/snippets.js` `plainSnippet`, the one markdown-to-words rule) |
| `editor/BlockTree.jsx`, `editor/refLabels.js`, `shared/model/blockModel.js` | outliner rendering (its rows are memoized), every row's `[[ref]]` and Gamma-link labels resolved once per tree change by the pure `refLabels.js`, and the pure tree ops (`shared/model/highlightColors.js` is the highlight palette both share with the viewer). Line breaks in a rendered note: one Enter is a hard line break (`remark-breaks`), one blank line the paragraph break, and every further blank line a visible empty line (`expandBlankLines` in `editor/mdMarks.js`, applied by `mdPreprocess` outside math and code) — what the editor shows is what the note renders |
| `transfers/FileChip.jsx` | the file chip an upload link renders as — a small card (kind icon in a tinted square, name, download arrow), inline so it sits in a sentence, identical for every type; a PDF or markdown chip whose page exists gets an accent "open page" button before the arrow; a `ContextMenu` on a right-click or a held finger (`menuPress`) with "Open page" / "Add to library" (fed by `FileChipContext` from App and one batched `POST /pages/by-docs` per render) and download; also the shared `postFile` / `uploadFilesAsLines` upload helpers |
| `editor/MdTools.jsx` | in-place tools on rendered notes: `MdImage` (a toolbar of `ctlBtn` icons, shown on hover and while the picture is selected — zoom lightbox, caption via alt text, download, delete, the object menu's "⋯" — plus a drag grip on each side of the centred picture writing the Obsidian `![alt|300]` size; legacy Logseq `{:width N}` reads and normalizes on edit) and `MdTableWrap` ("+" strips shown on hover, while a cell is edited and while the table is selected, column/row handles placed by the hovered or touched cell (`pointerover`) opening menus — insert, align, delete — and click-a-cell in-place editing: an input over the cell, Tab/Shift-Tab hop cells across the commit remount via a module-level session map, Enter commits, Esc cancels; tables are never edited as raw markdown — a cell mousedown stops the block row's edit-on-mousedown; selecting, moving and deleting a whole table is the object frame, `MdObject.jsx`), backed by pure source transforms (`scanImages`/`scanTables` locate the nth rendered construct; `applyImageEdit`/`applyTableEdit` rewrite it, tables re-serialized pretty-printed; `formatTables` also runs when a block's raw editor closes) and `htmlTableToMarkdown` for the spreadsheet-paste path |
| `editor/MdObject.jsx` | the object frame around every rendered image, table and diagram, Mermaid or SVG (`MdObject`, wrapped by `BlockMarkdown`; kinds `image`, `table`, `diagram`). A press on the object's body (`.mdImgFrame`, `.mdTableWrap`, `.diagramCard`) selects it (`mdObjectSelected` ring, and a selected picture or diagram shows its toolbar and grips; an editable picture zooms on double-click or double tap, a read-only one on click) and never reaches the block row; a press on the frame's margin does reach it, and click-to-source puts the caret at the object's near end. One menu for all kinds, on a right-click or a held finger (`menuPress`), or from the "⋯" (`ObjectMenuButton`) ending a picture's or a diagram's toolbar: Edit markdown source (click-to-source with the object's offset), Move to ▸ new block above / below / another page (the block move's page picker), Copy image (a loaded picture or a drawn diagram, the element `objectPicture` finds in the frame, handed to `copyImage`; greyed out with the reason outside a secure context), Copy as markdown, Delete (Delete/Backspace on a selected object does the same); the table's corner handle opens it through `useObjectMenu`. The frame is the HTML5 drag source: `dragStart` publishes `{blockId, kind, idx}` as `_dragState.fragment` plus the `application/x-gamma-object` data type, and App's `onBlockDragOver`/`onBlockDrop` route it. The row's outer 30 % (6–14px) means a new sibling block (the block drop indicator); the middle means inside the block at the gap nearest the pointer (`dropGapAtPoint`: `renderedGaps` plus a gap above the first and below the last construct, mapped to a source line start by the object scanners or `blockStartInSource` + `gapInSource`; drawn by `BlockDropIndicator` as `dropIndicatorInside`). `scanObjects` merges `scanImages` / `scanTables` / `scanDiagramFences` (one index for both diagram kinds) into one source-ordered list; quoted tables and prefixed or unfinished fences are not `editable` (menu without move/delete, no drag) |
| `editor/mdScan.js` | the pure scanners the rendered view (mdPreprocess, MdTools, the object frame) and the editor's widgets share, so all agree on which construct is the nth one: `scanMathSpans`, `protectedSpans` (math, fences, inline code — where markdown syntax is not read), `scanImages`, `scanTables`, `parseTable`, `serializeTable`, and `blockSpans` (the fences and display math a line boundary must not split) |
| `editor/mdObjects.js` | the pure source-range algebra behind every object move (`tests/mdObjects.test.mjs`). `cutObject`: the object's markdown and the content without it; a whole-line object takes its lines and the blank lines around it close up to the wider gap, an image inside a text line leaves the text; `mapOffset` carries a drop offset across the cut and returns null inside the removed zone, so a drop onto itself is a no-op. `insertObject`: a paragraph of its own at a line start or the end, the next line's indentation kept. `moveObject` (same block) and `moveObjectInTree` (the one tree edit for the drop and the menu: `inside` splices source, `sibling` / `child` make a new block) — one `setBlocks`, so a cross-block move is one undo step and one op batch. Nothing new is stored; the object's markdown changes place |
| `editor/BlockCmEditor.jsx` | the CodeMirror 6 block editor (textarea-compatible facade) with live in-place rendering of closed `$…$`/`$$…$$` spans, ``` ``` ``` fences (highlight.js cards), `[[ref]]`/`![[embed]]` chips, `@` mention chips ([mentions.md](mentions.md)), `![alt](url)` images (the picture, sized like the rendered view, alt as caption; `scanImageSyntax` in `mdMarks.js` is the one image scanner, shared with `MdTools`) and GFM tables (`TableWidget`, a read-only table of the raw cells). Pictures and tables are objects, not text: they stay rendered while the caret rests at either end or steps through their lines, and only a selection reaching strictly inside shows the source. A click puts the caret after them; a right-click or a held finger (`menuPress`) drops it inside (the editor's "Edit markdown source"). The widget is a drag source through `onObjectDrag` (BlockRow's object action → `_dragState.fragment`), so a table can be dragged out of a block while it is being edited. A drop INTO an open editor is the editor's own: capture-phase `dragover`/`drop` on its host, recognized by the `application/x-gamma-object` data type, land at the line boundary nearest the pointer (the inside drop line, then `onMoveObject` `inside`); CodeMirror's default drop, which would paste the markdown at the caret as a copy, never runs. The object index comes from `mdScan.js`, so it matches the rendered view's frame. Markdown renders live too (headings, `**`/`*`/`` ` ``/`~~`/`==`, links + bare URLs, clickable `- [ ]` checkboxes, `- ` bullets, `1. ` numbers in tabular figures (Inter's own are proportional, so a `1.` item's text would start left of a `2.` item's), `---` rules, quote lines and full `> [!type]` callout boxes with their fold flag (a heading, list marker or rule after a line's quote marks renders as it does outside a quote), underline and colored runs — `<u>…</u>` and `<span style="color:…">` / `background:…` inline HTML, tags hidden, inline marks of `scanMarks` in `mdMarks.js` that nest with the rest) — the construct the caret touches stays raw source (line-level touch for heading/quote prefixes, marker-only touch for list markers so a todo's checkbox survives editing its text). Raw math gets VSCode-style bracket-pair colorization (depth-cycled `--bracket-*` colors, enclosing pair boxed). Opening a block's editor keeps its height (checked by notes.mjs): the rendered view's outer margins stop at the block's edges, a list, quote, callout or table hugs the text line above it as its source lines do, and the editor's widgets take the rendered metrics — the `$$` math and code cards their margins, a callout's first and last lines the box's 6px padding (the title 2px under it, 4px when foldable), the table widget the object frame's 3px halo, 6px under the line above and the 14px the rendered table keeps under it for its + row. Blank lines still differ (the rendered view folds one into a paragraph break), as does a heading directly under text. Decorations come from a `StateField`, not a ViewPlugin — plugin decorations may not replace line breaks (multi-line fences/`$$` would throw). Formatting hotkeys — Ctrl/Cmd+B/I/U/E, Ctrl+Shift+X/H toggle `**`/`*`/`<u>`/`` ` ``/`~~`/`==` Obsidian-style, Ctrl+K inserts `[sel](url)` (clipboard URL fills the slot); swallowed inside math/fences/inline code — are block commands of `editor/blockCommands.js`, planned by `markCommands.js` and dispatched to the editor; a system keyboard's or menu's `beforeinput` `formatBold` / `formatItalic` / `formatUnderline` / `formatStrikeThrough` runs the same commands. The system's typing aids (autocorrect, capitals, spelling, suggestions) are on in prose and off in a code fence or math, decided as the caret moves (`typingAids`, [ipad.md](ipad.md#the-editing-bar)) |
| `editor/EditBar.jsx` | the touch editing bar: the markup strip's surface (`.pdfInkBar`'s elevated box, border, radius and light-scheme shadow) with its `.pdfInkSep` separators, holding `ctlBtn`s with 16 px icons at the ink menu's finger size (36 px), then a Done ✓. It floats centred 6 px above the on-screen keyboard, never lower than the compact shell's bottom bar, else at the screen's edge, at `--z-editbar` while a note's editor is open on a touch screen. Every button but Undo / Redo (the page's block history) is a block command, disabled when its `when` fails. Presses never take focus ([ipad.md](ipad.md) "The editing bar") |
| `editor/SelectionToolbar.jsx`, `editor/useSelectionToolbar.js` | the selection toolbar over a note's selected text (*The selection toolbar* above): the formatting commands as `ctlBtn`s pressed or disabled by their `state`, with `.pdfInkSep` separators, and the color row; the hook decides when it shows (after the mouse button's release, hidden on Escape and on an on-screen keyboard) |
| `editor/clickToSource.js` | maps the rendered view back to the raw source by text (the layouts differ too much for coordinates): the clicked character a new editor opens on (`sourceOffsetAtPoint`), the gap line between two rendered blocks (`renderedBlocks`, `renderedGaps`, `blockStartInSource`, `gapInSource`), a Ctrl-selection's source range for the chat (`sourceRangeOfSelection`), and other people's carets placed on the rendered view (`renderedCaretRect`, via `locateInRendered`); `locateInSource` / `locateInRendered` / `gapInSource` are pure and unit-tested |
| `editor/mdMarks.js` | the inline-mark table (regex + class per marker, and a `close` where the closing delimiter differs: `<u>`…`</u>`, the colored run's `<span style>`…`</span>`) shared by the live renderer and the hotkeys, plus the pure `toggleMark`/`insertLink` transforms (wrap / unwrap / empty pair / per-line for multi-line selections) and `markedAt`, which reads the same target the toggle acts on. `scanMarks` allows proper nesting (`**a *b* c**`, `*a **b** c*`, a colored run in bold or bold in it; nothing inside inline code) and treats `***x***` as one bold+italic span with two `layers`, so Ctrl+B and Ctrl+I each peel off their own delimiters. `recolor` and `colorAt` are the color row's: a selection's runs restyled, split and merged, and the style they share |
| `editor/mdLines.js` | the pure structure of a note's lines (`tests/mdLines.test.mjs`): `parseLine` splits one into where it sits (quote marks, nested `> > `, and indentation) and what it is (a bullet, number, to-do, heading or a callout's `[!type]` title) and its text, and everything else works on that. `enterPlan` is the line-break Enter's Obsidian-style continuation (`> - [x] ` continues as `> - [ ] `; on an empty item the list ends, then a quote level); `pastePlacements` moves pasted lines to the level of the line they land on (the clipboard bullet above); `retypeLine` is the "/" menu's line commands, which swap what a line is and keep where it sits (`/todo` on `> - item` is `> - [ ] item`; `/quote` adds a quote level; `/callout` turns the quote the line starts into a callout, else starts a box there, one level in); `toggleQuotePlan` is Toggle quote (Ctrl+Shift+.) over the selected lines. Every line of a quote carries its marks, a blank one a bare `>`, so it stays one box here, in Obsidian and on GitHub |
| `editor/slashCommands.js`, `editor/SlashMenu.jsx`, `editor/useSlashMenu.js` (their text-only insertions in `editor/slashInserts.js`) | the "/" command catalog and its matching (pure, `tests/slashCommands.test.mjs`): headings, to-do, lists, quote, callout, divider, equations, `/page` (a new library page, linked) and `/note` (a sheet of paper), table, code, mermaid, image, date, link, embed, highlight, and the `hidden` text/background color commands from `mdMarks.TEXT_COLORS` that show only when the query matches. The bare "/" list is the catalog in order under the `SLASH_GROUPS` titles (Text, Math, Insert, Link, Style); a typed query is one ranked list, by tier: the command's own name, a name it begins, a word of its label it begins, a keyword it begins, then a VS Code-style fuzzy match of the name or label (anchored on the first letter, jumping to word starts: `/cb` → Code block), like the `\command` completion's ([latex_editing.md](latex_editing.md)). A command that `needs` an ability (`newPage`, `sheet`, `image`, `embed`) is left out where the editor lacks it (`filterSlashCommands`' `can`: a share's editor makes no library page, a comment's field writes text only). `SlashMenuPopup` draws a row as an icon tile (`MENU_ICONS` by the item's `icon` key; the colors keep a tinted "A" `glyph`), the label over a one-line description, and the `/name` to type at the right, the matched letters marked with search's `mark.searchMark` in the name or the label, wherever they matched; a key-hint footer (Tab or Enter inserts). The same popup is the "Paste as" chooser shown after a URL paste (gamma block link → mention/synced block/URL, other URLs → URL/titled link), without names. `useSlashMenu.js` owns the menu's trigger (typed only, never inside math or a code fence), its keys and the run of a command, for a note row and a comment's field; BlockTree owns the "Paste as" chooser |
| `editor/RefPicker.jsx`, `editor/refLists.js` | the `[[` link picker: a caret-anchored popup (the "/" menu's placement) with pages matched by title (`rankRefPages`, the library matcher) above note blocks from `/api/block-search`, each block one plain line under its page path (`refBlockText` / `refBlockPath`, search's `plainSnippet`); when that search stopped at the server's time budget (`partial`) the key-hint footer leads with a "stopped early" line; the typed text is marked with search's `MarkedText` and a page row says `pageKindLabel()` ("Page" / "PDF"), as Search, Quick Open and the library do. `pageByTitle` is the hand-typed rule: `[[title]]` closed by hand becomes `[[id]]` when exactly one page has that title, else it renders as a dashed `.unlinkedRef` chip; `refQueryAt`, `refInsertEdit` and `typedTitleLink` are the typed `[[query`, a picked row's edit and the hand-typed rule's edit. `editor/useRefPicker.js` owns the trigger, the block search, the keys and the insertion, for a note row and a comment's field |
| `editor/mentions.js`, `editor/MentionMenu.jsx`, `editor/useMentionMenu.js`, `app/useReminders.js`, `app/ReminderAlerts.jsx` | the `@` menu and mentions ([mentions.md](mentions.md)): the pure syntax, date words, rows and chip text (`mentions.js`); the menu's trigger, keys and insertion for a note row and a comment's field (`useMentionMenu.js`); the popup on the `[[` picker's surface and rows (`refPopup mentionMenu`; Date, People with a `PersonAvatar` each, Link to page with the folder path as the meta line; a row's aside such as "(You)" muted in `.mentionAside`); the chip (`.mentionChip`, the ref chip's shape in the quiet fill, a reminder accent-coloured behind a masked alarm clock, red once due), drawn by the rendered view and the editor alike; the due reminders as `.reminderCard`s on the floating surface in the bottom right corner (Open a `uiBtn sm`, dismiss a `uiClose uiCloseSm`) |
| `comments/ThreadPopover.jsx`, `comments/Thread.jsx`, `comments/CommentMargin.jsx`, `comments/CommentComposer.jsx`, `comments/CommentsPanel.jsx`, `comments/comments.css` | comments ([comments.md](comments.md) "In the app"): the row's `ctlBtn` control after the body (the open count in the accent, else revealed with the row like the ×), the thread popover (a portalled `.popover` placed by `useFloatingPosition` from its anchor, `PersonAvatar` + name + time over each comment rendered by `BlockMarkdown`, a `MenuButton` per comment, Resolve / Reopen as a `ctlBtn` icon on the first comment's author line), the composer (the note editor, `BlockCmEditor` with the `blockEditorCm` look in the comment's type, which grows to 40vh and then scrolls, inside a bordered `.commentBox` on the field surface that takes the focus ring while focused within; the chat's round `chatCircleBtn` send inside the box at the right, at `--ctl-icon-btn-xs` with a 12px arrow, on the text's line while it is one line and at its last line once it wraps; the note's `[[`, `@`, "/" and LaTeX popups over the popover), the PDF note badge's `.hasThread` accent, the PDF margin's cards (a `.commentCard` on `--bg-card` with a `--border` hairline, `--radius-md` and `--shadow-1`, lifted to `--shadow-2` while active, holding the popover's thread view), and the Comments dock window (an Open / Resolved `Segmented` in its header, a row per thread) |
| `inbox/InboxButton.jsx`, `inbox/InboxPanel.jsx`, `inbox/inbox.css` | the Inbox ([comments.md](comments.md) "The Inbox"): the topbar bell, an `iconBtn` with an accent count badge (`.inboxBadge`, after `.navBackBadge`, "9+" past nine; inline as a More sheet row's `trailing`), and the header popover under it: a `popoverTitle` row with Mark all read as `uiBtn ghost sm`, an Unread / All / Archived `Segmented`, `popoverSection` day captions, and a row per item (`PersonAvatar`, the sentence in bold while unread with an accent dot, the snippet behind a left rule, muted meta, `ctlBtn` actions revealed on hover and focus) |
| `editor/BacklinksPanel.jsx` | "Linked from N pages" under a page's notes: App's `/blocks/{id}/backlinks` list grouped by page, each linking block a three-line rendered snippet; a click opens its page at the block (`openBlockLink`, a link jump); the section is off until the account turns it on (`backlinksVisible`, Settings → Reading & editing › Notes — off, no backlinks are fetched), and its fold is the browser pref `backlinksCollapsed` |
| `editor/callouts.js` | remark plugin for `> [!note] Title` callouts (type aliases → note/tip/warning/danger/important/quote; each type's colour is a `--callout-*` token); Obsidian's `[!note]-` / `+` fold flag makes a native `<details>` with the title as `<summary>` (chevron in app.css) |
| `editor/codeHighlight.js` | the highlight.js (`lib/common`) wrapper and the code card's copy button, shared by editor + renderer; token colors are theme-aware `.hljs-*` rules in app.css. The fence scanner is `editor/fences.js`: `scanFences` (used by mdPreprocess's exclusions and BlockTree's Enter/Tab-in-fence handling) and `fenceInnerAt` |
| `editor/LatexEditor.jsx` | LaTeX aids while editing: the live preview docked to the editor column with a caret marker, the `\command` popup, `renderKatex`/`useCaretAnchored` shared helpers; `editor/latexCompletion.js` is the pure catalog (prefix/abbreviation/fuzzy tiers, snippets, Tab-out navigation) it re-exports; `editor/latexInput.js` supplies scalable delimiter pairing. See [LaTeX editing](latex_editing.md) for shortcuts and browser checks |
| `library/trashList.js` | the Recently deleted list asked for ahead of its dialog, as the pointer or the focus reaches the trash button (`prefetchTrash`, `aheadTrash`, `dropTrash`; [home_library.md](home_library.md) "Recently deleted") |
| `library/RecentlyDeleted.jsx` | the Recently deleted dialog: a `SubDialog` of `aiProvRow` rows (title, who deleted it and when, days left, folder) with Restore and a delete-for-good icon button behind App's confirm box, Empty in its `DialogButtons` ([home_library.md](home_library.md) "Recently deleted") |
| `library/libraryUtils.js` | the library's folder and label helpers over the folder and label trees ([home_library.md](home_library.md) "Folders and labels"), and `pageAttachment`, what a page carries |
| `shared/ui/Widgets.jsx`, `shared/ui/Menus.jsx`, `shared/ui/Icons.jsx` | shared components; `OpenTabs` in Widgets is the topbar's tab strip (a kind icon per tab, pinned tabs first, full width with a pin in place of the close button, the active tab kept in view clear of the right-edge fade, tabs that come, go and move through `ListMotion`, and on overflow a "⌄ n" popover listing every tab with a filter); `useMenuTrigger` in Menus is the menus a toolbar's buttons open on the press ([Menus and submenus](#menus-and-submenus)) |
| `shared/styles/motion.css`, `shared/ui/motion.js` | how things move ([Motion](#motion)): the recipes, keyed by the surfaces' classes and the widgets' markup, on the motion tokens; and the pure side (`reducedMotion`, `textSplit`, `longestMs`, `arriving`, `listGlides`, `sameKeys`; `tests/motion.test.mjs`) |
| `shared/ui/Surfaces.jsx`, `shared/ui/leave.js` | the shells of the surfaces that come and go (`DialogOverlay`, `Popover`), and a closed surface's way out through a still copy (`useLeave`, `leave`, `stillCopy`, `standing`, `standBack`; `tests/leave.test.mjs`) |
| `shared/ui/MotionWidgets.jsx` | what changes in place: `IconSwap`, `MorphText`, `Collapse`, `Chevron`, `useSlidingPill`, `FADE_IN`, `Reveal` ([Things that change in place](#things-that-change-in-place)) |
| `shared/ui/ListMotion.jsx` | a list whose items glide to their new places, come in where they will rest and leave from where they stood ([Lists that change](#lists-that-change)) |
| `shared/ui/wheelPan.js` | `useWheelPan`: a plain mouse wheel pans a sideways strip (the card strips, the tab strip) |
| `shared/ui/listKeys.js` | `stepList`: ↑/↓ between a search box and the results listed under it (Settings search, the move-to-page filter, the chat's page picker) |
| `shared/ui/DiagramFigure.jsx`, `shared/ui/ResizeGrip.jsx` | the diagram figure (a Mermaid diagram inline, or a sanitized SVG drawing shown as an image) with its toolbar of `ctlBtn`s, ended by a note's object "⋯" (`tools`) ([diagrams.md](diagrams.md)); the two-sided drag grips (`ResizeGrips`) + `useDragResize` hook that size centred note images and diagrams alike, a double-click or double tap on a grip restoring the natural size |
| `shared/ui/press.js`, `shared/lib/pointer.js`, `shared/lib/pickFiles.js` | one behaviour for mouse and finger (above): `menuPress` / `doublePress` / `pressAt` / `triggerPress`, the three device questions, and the system file picker opened from code |
| `app/CommandMenuItem.jsx` | a catalog command as a menu row (the View menu's Undo, Redo, Go to page, Command palette; [Menus and submenus](#menus-and-submenus)) |
| `shared/ui/menuAim.js` | pointer-trajectory ("safe triangle") hover intent for hierarchical menus — UI-agnostic, consumed by `shared/ui/Menus.jsx` |
