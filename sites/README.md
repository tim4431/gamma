# gammapdf.com

The product website: the front page, the pricing page, and the repository's
documents rendered as pages (the user guide, the developer guide, the
research notes, the privacy policy), served by a Cloudflare Worker with
static assets. Nothing here is part of the app, and nothing shown on the
site is written for the site alone: the front page's copy follows the README
and the Store listing, the artwork is the README's, and every other page is
a Markdown file of the repository rendered at build time. The pricing page's
numbers follow the account server's plans
([docs/dev/billing.md](../docs/dev/billing.md) "The website").

```
sites/
  site/            the hand-written pages as deployed: index.html, pricing.html,
                   styles.css, site.js, 404.html, robots.txt, _redirects, _headers
  templates/       header and footer, pulled into every page by `<!--#include name -->`;
                   page.html wraps a Markdown page on its own (the privacy policy),
                   doc.html one with the documentation sidebar
  build.mjs        assembles dist/ (see below)
  src/index.js     the Worker: sends www to the apex, serves everything else
                   from the assets binding
  wrangler.jsonc   the Worker config, custom domains gammapdf.com + www
  dist/            build output, ignored
```

## What the build does

`npm run build` runs `build.mjs`, which:

1. copies `site/` to `dist/`, expanding the includes in every `.html`;
2. copies the artwork the front page uses from the repository into
   `dist/media/`: the favicon (`frontend/public/media/icons/`), the hero PNG
   and the illustration SVGs (`docs/assets/branding/`), nine of the demos
   (`docs/assets/demos/`) and the hero still
   (`docs/assets/screenshots/hero-app.webp`, shot by
   `tools/readme-media/shoot-hero.mjs`). The site keeps no copies of its own,
   so regenerating brand assets or re-recording a demo updates the site on
   its next deploy;
3. renders the Markdown documents listed in `DOCS` at the top of `build.mjs`:

   | Source | Page |
   |---|---|
   | `PRIVACY.md` | `/privacy/` |
   | `docs/user_guide.md` | `/docs/` |
   | `docs/dev/*.md` | `/docs/dev/` (the README) and `/docs/dev/<name>/` |
   | `docs/research/*.md` | `/docs/research/` (the README) and `/docs/research/<name>/` |

   Everything about a page comes from its Markdown: the title is the first
   heading, the description the first paragraph, heading ids follow GitHub's
   rules so an anchor written for the repository works on the site too. The
   sidebar groups the pages by section; a section's index page (its README)
   decides their order and labels through the order and text of its links,
   table links first, so the developer guide's topic table is the developer
   sidebar. A relative link in a document becomes the page it points at when
   that document is rendered too, a copy under `/media/` when it is a
   picture, and otherwise a link to the file on GitHub, so a document can
   point at code. Each page ends with a link to its source file;

4. writes `sitemap.xml` from every page in `dist/`, then checks that each
   internal link lands on a page and each anchor on an id in it.
   `node build.mjs --strict` (what CI runs) fails on a broken one; a plain
   build only warns.

The pages have no framework and no web fonts (the brand's system font
stack), and make one kind of outbound request: `site.js` asks the GitHub API
for the release list to fill in the version, the per-OS installer links and
the extension zip, and for the star count. Every element keeps a working
fallback link if that call fails. There are no cookies and no analytics, in
line with the app's privacy policy.

The header's **Log in** link and the `/login`, `/account` and `/signup`
short links go to the Gamma Cloud account server at `account.gammapdf.com`
([docs/dev/cloud_accounts.md](../docs/dev/cloud_accounts.md)); the site
itself has no accounts. The hero's **Try the demo** button, the header's
**Demo** link and the `/demo` short link go to the public demo at
`demo.gammapdf.com`, a Gamma in demo mode where every visitor gets a
throwaway guest workspace ([docs/dev/guests.md](../docs/dev/guests.md)).

`_redirects` gives the short links (`/download`, `/download/windows`,
`/guide`, `/github`, …); its sources must be relative paths, so the `www.`
to apex redirect is the Worker script's job. `_headers` sets the security
headers and a one-day cache on `/media/*`.

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
`docs/` or `PRIVACY.md` (a strict build, the key pages present, includes
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
   `www` to the apex.
3. Until the zone exists, comment the `routes` block out; the deploy then
   lands on the `*.workers.dev` preview URL only.

## Editing the site

- The front page's copy comes from the README and the Store listing
  (`desktop/assets/store/listing.md`); keep the three in step when a feature
  changes. Its "Guide:" line under each feature mirrors the README's
  "→ Guide:" line and points into `/docs/`.
- Section order and tone follow the survey in
  [docs/research/website.md](../docs/research/website.md).
- The page must work without `site.js` (it does: every link has a static
  fallback) and in both color schemes (the illustrations are light-only;
  the screenshot and demos are shown as recorded).
- Documentation pages are edited in `docs/`, never here. A document that
  should also be on the site is added to `DOCS` in `build.mjs`; a new file
  in `docs/dev/` or `docs/research/` is published on the next build with
  nothing to configure. Link between documents with relative paths to the
  `.md` files, as GitHub needs anyway.
- A new hand-written page: add a `.html` under `site/` with the two
  includes; the sitemap picks it up.
