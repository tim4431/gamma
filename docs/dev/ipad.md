# Gamma on the iPad (and other tablets)

The iPad app is the web app installed to the home screen. There is no
Swift client: the browser ink layer already gives Apple Pencil pressure,
tilt, hover, palm rejection and Safari's touch-gesture handling
([handwriting.md](handwriting.md)), and every other feature — the block
editor, search, AI, sharing, workspaces — is the same React code the
desktop shell hosts. The reasoning, and what the upstream fork's native
PDFKit/PencilKit app was measured against, is in
[research/ipad.md](../research/ipad.md).

## What the user does

1. Open the server's URL in Safari on the iPad and sign in.
2. Share button → **Add to Home Screen** → Add. Chrome and Edge on a
   desktop or Android tablet offer **Install Gamma** in the address bar
   for the same result.
3. Gamma opens full screen from the icon, without Safari's toolbars,
   signed in as before (the session cookie lives a year; the installed app
   has its own cookie jar, so the first launch may ask once).

Writing then works as everywhere else: a Pencil draws right away, fingers
scroll and pinch, the tool strip's presets are per browser (the installed
app counts as one browser, separate from Safari).

## What the install adds, and where it lives

| Piece | Where | Notes |
|---|---|---|
| The manifest | `frontend/public/media/manifest.webmanifest` | `display: standalone`, `start_url` `/` (the app then picks the last used workspace), the three PNG icons. Unversioned, so under `/media/` (sent `no-cache`, [repository.md](repository.md)). The backend registers its media type, `application/manifest+json`, because Windows and slim images lack it (`gamma/app.py`; `tests/test_static.py`). |
| The icons | `frontend/public/media/icons/apple-touch-icon.png` (180), `icon-192.png`, `icon-512.png`, `icon-maskable-512.png` | Rendered from the one brand mark by `tools/branding/build.mjs` like every other icon ([design/brand](../../design/brand/README.md)); never edited by hand. The `bleed` option of `mark.mjs` gives them a full-bleed square plate — the OS masks the corners itself, and iOS paints transparent corners black — and the maskable one keeps the mark inside the inner 80%. |
| The head tags | `frontend/index.html` | `manifest`, `apple-touch-icon`, `apple-mobile-web-app-capable` / `mobile-web-app-capable`, the title, `apple-mobile-web-app-status-bar-style` **default**; the pre-paint script adds `theme-color` for the stored theme (below). |
| The status bar colour | `index.html`'s pre-paint script, then `app/App.jsx`, `paintStatusBar` (the theme effect and the phone-topbar effect) | With the *default* status-bar style the bar sits above the viewport and is painted with `theme-color`. Before the bundle loads, the pre-paint script writes the resolved theme's chrome colour from a small table of each theme's `--bg-page` (`tests/themes.test.mjs` holds it to the tokens), so a cold start never shows another theme's bar. From then on the meta is set to the topbar's background (the chrome, `--bg-page`) whenever the theme changes, so the bar continues the topbar for every theme — or to the library's (the content surface, `--bg-surface`) while the compact layout shows no topbar. Both are resolved to a hex first (`tokenHex`): most tokens are `color-mix()` expressions. `black-translucent` was rejected: it puts content under the bar with fixed light text, wrong on the light themes. |
| Standalone-mode CSS | `shared/styles/app.css`, `@media (display-mode: standalone)` | The document stops rubber-banding (`overscroll-behavior: none` on html/body; the panes still scroll) and `.app` pads `env(safe-area-inset-bottom)` for the home indicator. There is no top inset to absorb with the default status bar. |

## Layout by orientation

An iPad's Safari sends a desktop-class UA, so the layout follows the
screen, not the device (`PHONE_MQ` / `useIsPhone` in App.jsx):

- **Upright** — a coarse pointer, portrait, at most 1024px wide (every iPad
  up to the 12.9" Pro, and Android tablets) — gets the compact shell the
  phone uses (`.phoneUI`): the PDF or the notes full width, Notes and Chat
  as full-screen panels, one bottom bar (below). Two docks in 820px would
  leave ~200px columns, too narrow for the chat header's buttons. At this
  width the bar's items are roomier and its popovers keep
  a menu's width at the right end instead of spanning the screen (the
  `min-width: 701px` block under the phone rules in `app.css`).
- **Landscape** gets the desktop docks, as on a laptop. Turning the tablet
  swaps the shell live; the dock arrangement is kept for the way back.
- A phone keeps the compact shell both ways: 700px wide or less, or a
  coarse pointer on a screen at most 500px tall.

### The compact shell

- **The bottom bar** (`.phoneBottomBar`): view tabs on the left — Library
  (home: the topbar has no Home button here, so it stays on a page's bar
  and carries the `header.home` anchor), the page's PDF and Notes, Chat —
  and the topbar's actions on the right: Add, Search, a page's Share (and
  a clone's Sync pill), More. Every item is a 22px icon over its word
  (`.barLabel`, `--fs-2xs`, hidden on the desktop topbar), about 50px tall.
- **More** is the account button (`header.account`) under another name:
  its sheet is the account menu with the rest of the topbar on top —
  Background tasks, the open folder's Share this folder, and the View
  menu's rows (`viewMenuItems`; of the window toggles only PDF, since the
  tabs switch Notes and Chat). The tasks, folder-share and View buttons are
  not rendered; their popovers open from those rows in the sheet's place.
  While something runs, More shows the tasks spinner.
- **The topbar** holds only tabs and Back, so with neither (a library with
  no tab open) it is not shown (`.topbar.phoneBare`), and theme-color
  switches to the library's `--bg-surface` so the status bar continues it
  (`paintStatusBar` in App.jsx).

Nothing else is tablet-specific: the viewport meta already disables
browser zoom in favour of the viewer's own pinch-zoom, `touch-action:
manipulation` removes double-tap zoom, and both layouts carry the touch
rules the ink layer and the viewer already have.

## Not built (and why)

- **No service worker / offline shell.** Installability on iOS does not
  need one, and a cache layer would sit on top of the asset cache rules
  in [repository.md](repository.md). Offline reading is the separate
  "offline library" item in the upstream study, to be scoped on its own.
- **Pencil double-tap and squeeze** are not exposed to web content by
  iPadOS; Pencil hover is (a `pen` pointer with no buttons) and already
  shows the tool footprint.
- **A native wrapper** (a `WKWebView` shell like the desktop's Electron
  one) would only add App Store distribution and system share-sheet
  integration. It stays out until distribution is a goal; the web app is
  the product either way, as the desktop shell's black-box rule already
  says ([desktop.md](desktop.md)).

## Tests

- `backend/tests/test_static.py`: the manifest's media type and cache
  header.
- `tests/e2e/scenarios/ipad.mjs` (`npm run e2e -- --only ipad`), in
  Chromium touch contexts:
  - Tablet-sized: the manifest parses with `standalone` display, and every
    icon it and the `apple-touch-icon` link name is a real PNG.
    `theme-color` matches what sits under the status bar (the library with
    no tab open, the topbar once one is) and follows a theme change. The
    bundled stylesheet carries the standalone block (the document
    overscroll rule and the home-indicator inset). Chromium cannot emulate
    `display-mode`: CDP accepts the feature, but `matchMedia` ignores it.
  - Upright (834×1194) is the compact shell: the PDF full width, Notes full
    screen from the bottom bar. Rotating to landscape brings the docks back.
  - A 390px phone: no topbar without tabs, the bar's words (Library Chat
    Add Search More, then Library Notes Chat Add Search Share More on a
    page), the listing bar inside the screen, a long title wrapping with its
    chips under it, More's sheet opening Background tasks, and the Library
    tab going home.
  - Only the device can check the Add to Home Screen flow, the status-bar
    paint and the home indicator.
- `node tools/branding/build.mjs --check` pins the icons to the mark.
