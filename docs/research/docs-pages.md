# Documentation pages: the layout, and diagrams without a browser

Made in November 2026, when the design pages on gammapdf.com
(`/docs/design/`) were rewritten around diagrams and the documentation's
pages were restyled. Two questions: what makes a product's documentation
easy to read, with Tailscale's knowledge base as the reference the
developer named, and how a diagram can be drawn on a site whose
Content-Security-Policy allows only its own scripts, from a source GitHub
draws too. The mechanics as built are in
[sites/README.md](../../sites/README.md#what-the-build-does).

## What Tailscale's pages do

Seen at tailscale.com/kb ("What is Tailscale?", "Connection types"):

- **Three columns**: a sidebar tree of sections, the page (about 800px),
  and an *On this page* column with the page's headings, nested one level,
  the one being read marked, and *Scroll to top* below.
- **A breadcrumb** (Docs › Technical overviews › page) above the title, and
  under it a "Last validated" date, *Copy as Markdown* and *View as
  Markdown*.
- **Running text in near-black**, not grey, at about 17px.
- **Callouts** with an icon and a tinted box ("For more information …").
- **Steps numbered in small discs**, sub-steps lettered.
- **Figures** drawn for the page, with a caption under each.

Taken for gammapdf.com: the three columns from 1280px (narrower, the
sidebar keeps the page's headings), the breadcrumb, darker running text,
callouts, and a card for the pages before and after. Left out: search (the
site has no backend and no framework), *Copy as Markdown* (each page ends
with a link to its source), a "last validated" date (a design page says
what is true now, and git keeps when), visible captions (GitHub would not
show them; `accTitle` labels a diagram for a screen reader instead), and
the discs (Markdown's `start` attribute would be lost to a CSS counter, so
the numbers stay the list's own, set in the text colour).

## Drawing a diagram

The design pages are also read on GitHub, which draws a `mermaid` fence
itself, so a fence is the one source for both. The question is how the
site draws it, under `script-src 'self'` and a privacy policy that lists
every outbound request.

| Option | Cost | Verdict |
|---|---|---|
| Mermaid in the page, self-hosted | `mermaid.min.js` 12.0 is 5.6 MB (1.6 MB gzipped), on every page with a diagram; drawn after load, so the page jumps; nothing without script | declined |
| `@mermaid-js/mermaid-cli` at build time | a Chromium download in every `site.yml` run, for one build step | declined |
| SVG files drawn by hand | GitHub shows them too, but a change needs an editor and a diff says nothing | declined |
| beautiful-mermaid 1.1.3 at build time | MIT, from Craft; ELK's layout in plain JavaScript, synchronous in Node; about 10 MB in `node_modules`, nothing at run time | **chosen** |

What beautiful-mermaid's output needed, and its limits as measured on the
18 diagrams of the design pages:

- **It imports Inter from Google Fonts** in each SVG's style. The build
  drops the import and sets the page's own faces.
- **Its colours are CSS variables named `--bg`, `--fg`, `--line`,
  `--accent`, `--muted`, `--surface`, `--border`**, which the site's tokens
  already use (the site's `--line` is a hairline, too faint for an edge).
  The build renames them `--dg-*` and `styles.css` sets those from the
  tokens, so one SVG is drawn in either scheme with no second render.
- **A wide left-to-right flow is shrunk to the column** until its text
  reads at 9px. The build fails `--strict` on a diagram wider than the
  column, and a phone scrolls one sideways below 75 %.
- **A node first named in an edge keeps its id as its label**, even when a
  later line gives it one: define nodes before the edges that use them.
- **State diagrams lay out poorly** (the start dot mid-drawing, the edges
  doubled back); a lifecycle is drawn as a flowchart instead.
- **An `actor` in a sequence diagram overlaps its first message's label**;
  a plain participant does not. A decision diamond grows with its label, so
  a question goes in a rounded box.
- A group's header can overflow its box by a few pixels when its text is
  much wider than what it holds.

Real Mermaid (12.0, the app's copy) parsed and drew all 18 fences, so
GitHub draws what the site draws, in Mermaid's own look.
