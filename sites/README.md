# gammapdf.com

The product website: the front page, the pricing page, and the
repository's documents rendered as pages (the user guide, the privacy policy,
the Gamma Cloud terms), served by a Cloudflare Worker with static assets.
Nothing here is part of the app. The front page and `/pricing` are written by
hand; the front page tells three stories, shows six tiles and carries the
download and self-hosting sections, and the README and the user guide keep the
full feature list. The artwork, badges and
demos are the README's, the font is the app's, and every other page is a
Markdown file of the repository rendered at build time. The pricing page's
numbers follow the account server's plans
([docs/dev/billing.md](../docs/dev/billing.md) "The website").

```
sites/
  site/            the hand-written pages as deployed: index.html,
                   pricing.html, styles.css, site.js, docnav.js, 404.html,
                   robots.txt, _redirects, _headers, and media/ with the cuts of
                   the demos and their stills
  templates/       header and footer, pulled into every page by `<!--#include name -->`;
                   page.html wraps a Markdown page on its own (the privacy policy, the terms),
                   doc.html one with the documentation sidebar
  build.mjs        assembles dist/ (see below)
  src/index.js     the Worker: sends www and plain http to https://gammapdf.com,
                   serves everything else from the assets binding
  wrangler.jsonc   the Worker config, custom domains gammapdf.com + www
  dist/            build output, ignored
```

## What the build does

`npm run build` runs `build.mjs`, which:

1. copies `site/` to `dist/`, expanding the includes in every `.html`, and
   each `<!--#repofile path -->` into that file of the repository, escaped
   for HTML, with LF line ends; a file that is missing fails the build. The
   self-hosting section's compose block is
   `<!--#repofile docker-compose.yml.example -->`: what the page shows is the
   repository's file, never a copy, so it is edited there;
2. copies what the pages use from the repository into `dist/media/`: the
   favicon (`frontend/public/media/icons/`), the logo and the hero PNG, which
   is the page's `og:image` (`docs/assets/branding/`), the TestFlight and
   Chrome Web Store badges (`docs/assets/badges/`), the native-agentic
   demo (`docs/assets/demos/`), the hero
   still (`docs/assets/screenshots/hero-app.webp`, shot by
   `tools/readme-media/shoot-hero.mjs`), and the app's Latin subset of Inter
   with its licence, `inter-OFL.txt` (`desktop/ui/fonts/`). The site keeps no
   copies of these, so regenerating brand assets or re-recording that demo
   updates the site on its next deploy. The one exception is `site/media/`:
   the shorter cuts of ink-import and notes, which start at the marks and the
   math, and the stills of the three clips. A still is its clip's first
   picture: `site.js` swaps the animation in when the clip comes near, so a
   visit that stops at the top never fetches one, and without script or under
   `prefers-reduced-motion` the still stays.
   `tools/readme-media/site-cuts.py` writes them from the published demos;
   run it after re-recording one. The README's
   illustrations and its other demos are not copied; a picture a rendered document shows
   reaches `dist/media/` through the next step;
3. renders the Markdown documents listed in `DOCS` at the top of `build.mjs`:

   | Source | Page |
   |---|---|
   | `PRIVACY.md` | `/privacy/` |
   | `TERMS.md` | `/terms/` |
   | `DELETE_ACCOUNT.md` | `/delete-account/` (how to delete a Gamma Cloud account; the Play listing's Data safety form links it) |
   | `docs/user_guide/user_guide.md` | `/docs/` |

   Everything about a page comes from its Markdown: the title is the first
   heading, the description the first paragraph, heading ids follow GitHub's
   rules so an anchor written for the repository works on the site too. The
   sidebar lists the page's sections; on a narrow screen `site/docnav.js`,
   which runs right after it in the page and is not deferred, folds it before
   the first paint. The user guide's interactive demos
   (`docs/user_guide/demos/`, a page and its script each, since the site's
   CSP allows no inline script) are copied as they are under `/docs/demos/`,
   where the guide's links to them land. The developer guide and the research
   notes are written for contributors and are not published: they stay on
   GitHub, where a link from the user guide to one of them lands. A relative
   link in a document becomes the page it points at when that document is
   rendered too, a copy under `/media/` when it is a picture, and otherwise a
   link to the file on GitHub, so a document can point at code. Each page
   ends with a link to its source file;

4. writes `sitemap.xml` from every page in `dist/`, then checks that each
   internal link, picture and clip (`src`, `srcset`, `data-clip`) lands on a
   page or a file and each anchor on an id in it.
   `node build.mjs --strict` (what CI runs) fails on a broken one; a plain
   build only warns.

The pages have no framework. They self-host Inter from the app's Latin
subset: `styles.css` declares `/media/inter.woff2` and `_headers` preloads
it, so no font comes from a third party. The pages make three kinds of outbound
request. On the front page `site.js` asks the GitHub API for the release list
to fill in the
version, the per-OS installer links, the extension zip and the Android APK (a
visitor on Android gets the APK once a release carries one), and for the star
count, which the hero's Star on GitHub button shows whenever the API answers;
when it does not, its place in the button goes. It names the primary
download buttons after the visitor's system and marks the visitor's card in
the download section. On the pricing page it also
asks the account server which paid plans are on sale
(`https://account.gammapdf.com/api/plans`, [docs/dev/billing.md](../docs/dev/billing.md)
"The website") and turns the button of a plan not sold right now into a
disabled *Coming soon*; `_headers` lets that origin through `connect-src`.
The Windows card's badge is Microsoft's own image, from
`https://get.microsoft.com`, the one picture from outside the site: it is
lazy, so the browser fetches it as the section comes near, `_headers`
lets that origin through `img-src`, and `site.js` swaps a plain button in
when it cannot load.
Every element keeps a working fallback if a call fails. There are no
cookies and no analytics, and `PRIVACY.md`'s "The website" section lists
these requests, so a new one is added there.

The header's **Log in** link and the `/login`, `/account` and `/signup`
short links go to the Gamma Cloud account server at `account.gammapdf.com`
([docs/dev/cloud_accounts.md](../docs/dev/cloud_accounts.md)); the site
itself has no accounts. The header's **GitHub** link goes to the repository.
The hero's **Try the demo** button, the header's **Demo** link and the
`/demo` short link go to the public demo at
`demo.gammapdf.com`, a Gamma in demo mode where every visitor gets a
throwaway guest workspace ([docs/dev/guests.md](../docs/dev/guests.md)).

`_redirects` gives the short links (`/download/windows`,
`/connector` for the Chrome Web Store listing, `/ipad` for the iPad app's
TestFlight invitation, `/android` for the release that carries the APK,
`/guide`, `/github`, …); its sources must be relative paths, so the
redirect from `www.` and plain `http://` to `https://gammapdf.com` is the
Worker script's job. It is one 301 hop, so search engines index only the
https apex. The pages themselves link to the targets, not to the short
links: the build's link check knows only the pages it built, so an
`href="/android"` fails `--strict`. `/download` is not a page: it goes to
the front page's `#download` section (`/#download`, a 302 that keeps the
fragment), because the account server's deployed pages link to it. The
section's cards link to the latest release and carry `data-asset`, which
`site.js` points at the file once a release has it.
`_headers` sets the security headers, a preload of the font on every page but
the guide's demos, which do not use it, and a one-day cache on `/media/*`.

## Run locally

```bash
cd sites
npm install
npm run dev        # build, then wrangler dev on http://localhost:8787
```

`wrangler dev` needs the `workerd` binary that npm's install scripts fetch;
if npm reports pending install scripts, run `npm approve-scripts` for
`workerd` and `esbuild` once. Without it, `npm run build` still works and any
static file server over `dist/` shows the site (serve `index.html` for a
directory URL, as the Worker's `auto-trailing-slash` handling does).

## Deploy

Manually, from a machine that is logged in (`npx wrangler login` once):

```bash
cd sites
npm run deploy     # build + wrangler deploy
```

From CI: `.github/workflows/site.yml` checks a PR that touches `sites/`,
`docs/`, `desktop/ui/fonts/`, `docker-compose.yml.example`, `PRIVACY.md` or `TERMS.md` (a strict build, the key pages present, build directives
expanded, a wrangler dry run), and checks + deploys on a push to `main`
touching them or on a manual dispatch from any branch — the `build-site`
skill (`gh workflow run site.yml --ref dev`), so the site ships without a
merge. A documentation change on `main` therefore reaches the site by
itself. The app's `check.yml` and `docker.yml` skip site-only changes
([docs/dev/github_actions.md](../docs/dev/github_actions.md)). Deploying
needs two repository secrets:

| Secret | Value |
|---|---|
| `CLOUDFLARE_API_TOKEN` | an API token with the *Edit Cloudflare Workers* template (Workers Scripts: Edit, plus Zone → Workers Routes: Edit for the custom domains) |
| `CLOUDFLARE_ACCOUNT_ID` | the account id from the Workers overview page |

### First-time setup of the domain

1. Add `gammapdf.com` as a zone on the Cloudflare account (change the
   registrar's nameservers to the ones Cloudflare assigns). The `routes` in
   `wrangler.jsonc` are Custom Domains, which Cloudflare can only create
   inside a zone it serves.
2. Deploy. Wrangler creates the Worker, uploads `dist/`, and attaches
   `gammapdf.com` and `www.gammapdf.com` as custom domains (DNS records and
   certificates are created automatically). The Worker script then sends
   `www` and plain http to `https://gammapdf.com`.
3. Until the zone exists, comment the `routes` block out; the deploy then
   lands on the `*.workers.dev` preview URL only.

## Editing the site

- The front page tells three stories, each a heading and a sentence beside a
  clip, then six tiles, and does not follow the README feature by feature. The
  README, the guide and the Store listing (`desktop/assets/store/listing.md`)
  keep the full list; when a feature changes, check the story or tile that
  names it.
- Its Gamma Cloud *Coming soon* label is static text
  ([docs/dev/billing.md](../docs/dev/billing.md) "The website").
- The download section's Server and assistant cards spell out their commands
  by hand: keep them in step with the README's install section and the user
  guide's "Connect an assistant". Only the compose block comes from the file
  itself, and the sentence beside it says its Watchtower service keeps Gamma
  upgraded.
- The front page's `id="selfhost"` and `id="download"` stay: the account
  server's pages link to the first and reach the second through `/download`,
  and the build's link check does not see those pages.
- The survey behind the page, and why it was cut in November 2026, are in
  [docs/research/website.md](../docs/research/website.md).
- The page must work without `site.js` (it does: every link has a static
  fallback, and at phone width the header shows its links, since nothing can
  open the menu) and in both color schemes (the screenshot and demos are shown
  as recorded, slightly dimmed in dark mode).
- Documentation pages are edited in `docs/`, never here. A document that
  should also be on the site is added to `DOCS` in `build.mjs`. Link between
  documents with relative paths to the `.md` files, as GitHub needs anyway.
- A new hand-written page: add a `.html` under `site/` with the two
  includes; the sitemap picks it up.
