# Product websites: what note and reading apps put on their front page

Survey made in September 2026, before the gammapdf.com page was written.
Thirteen sites were fetched: Obsidian, Logseq, Notion, Zotero, Readwise
Reader, Paperpile, Anytype, Craft, Capacities, Tana, Reflect, Joplin, AFFiNE
and SiYuan. Logseq (JS-rendered), Anytype (features page gone) and AFFiNE
(blocked) were filled in from their READMEs and search snippets, so their
section order is unknown. Tana had pivoted to meetings and was dropped. The
page that came out of this lives in `sites/` ([sites/README.md](../../sites/README.md)).

## What recurs (eight or more of the ten readable sites)

- A headline of three to six words and one sentence under it that names the
  category. Obsidian: "Sharpen your thinking. / The free and flexible app for
  your private thoughts." Zotero: "Your personal research assistant / free,
  easy-to-use tool to help you collect, organize, annotate, cite, and share
  research." SiYuan puts keyword chips in the subline ("WYSIWYG · Block Ref ·
  E2EE Sync · Privacy-first").
- One primary call to action (Download, OS-detected on Obsidian and SiYuan;
  "Try free" on the hosted products) with a lighter secondary (more
  platforms, GitHub, a demo).
- A real product picture in or right under the hero: a screenshot (Zotero,
  Paperpile), a short video (Notion, Capacities, Reflect), two devices
  (Readwise). Only Obsidian, Craft and Joplin use an illustration instead.
- One feature grid of five to nine items (Zotero is nothing but that grid),
  then alternating rows of text and image, each row one feature.
- A statement about data ownership, in plain words. Obsidian: "Your thoughts
  are yours … no one else can read them, not even us"; Zotero: a nonprofit
  with "no financial interest in your private information"; Logseq: "your
  data will always be yours"; Reflect and Anytype: end-to-end encryption.
- A closing call to action repeating the hero's, and a footer grouped
  Product / Resources / Community.

## What is optional

- Testimonials: the indie tools quote individual users with a role
  (Capacities includes a scientist); Joplin quotes the press (Lifehacker,
  PCMag) instead; Notion quotes executives.
- Community proof for open-source tools: GitHub stars, contributor avatars
  (Joplin), sponsors and Open Collective (Logseq, Joplin).
- A FAQ (Readwise only, ten questions), pricing on the home page (Reflect
  only), an integrations logo strip (Readwise, Reflect, Craft), a
  problem-then-solution opener (Capacities: three pains, then "what if your
  notes worked like your mind?").
- Paperpile's navigation is organized by workflow verb (Find and collect,
  Read and annotate, Cite and write, Share and collaborate) with "Apps &
  tools" as its own group; Joplin gives every feature row its own button.

## What Gamma's page took from this

Section order: hero (headline, category subline, OS-detected Download plus
demo and GitHub secondaries, a hand-drawn arrow at the GitHub one, the app
screenshot in a window frame), three value pillars (your library on your
machine, papers and notes are one thing, open source and open formats), ten
feature rows that follow the README's sections and reuse its demos and
illustrations, a "Get Gamma" platform section in Paperpile's apps-and-tools
spirit (Windows, macOS, Linux, Docker, the extension, Codex, Claude Code and
MCP), a self-hosting section with the Compose file, a data-ownership section
in Zotero's register, a six-question FAQ, a closing call to action, and the
grouped footer.

The first version carried the README's full copy, about 2,800 words. In
October 2026 it was cut to about 1,100: one sentence or a few short bullets
per feature, as on the surveyed sites, with the detail left to the README and
the guide. The hero's keyword chips went with it.

In November 2026 the front page stopped following the README feature by
feature. At 1920×911 it still ran 13.2 screens, with 989 visible words, 49
links and 16 images in its main content, most of it repeating what the README
and the guide list in full. The three pillars, the ten feature rows with their
Guide lines, the Star on GitHub button with its arrow, the platform and
self-hosting sections, the data-ownership section and the FAQ went. The page
now tells three stories with a clip each, then six text tiles, a band on who
keeps the data and a closing call to action: 6.0 screens, 294 words, 9 links
and 4 images. The platform and self-hosting sections became the `/download`
page, with two of the FAQ's questions, and "Is Gamma free?" became a note on
`/pricing`.

Later that month the developer compared this page with the one before it and
took three things back: the centred hero with the Star on GitHub button and its
arrow, each story's text and clip in two columns, and the download and
self-hosting sections, at the bottom of the front page again. The `/download`
page went, and its address now leads to the section. The section shows the
store badges and the Server, Claude Code and Codex commands, and self-hosting
shows `docker-compose.yml.example`, which the build copies in. The shorter
clips, the tiles, the band and the pricing page stayed, and the closing call to
action went. The page now runs 7.3
screens at 1920×911 (10.2 at 390 wide), with 931 words, 29 links and 7 images.

Tone: lead with what the reader does, state privacy as fact, no adjectives
for their own sake. Testimonials, press and community counts were left out:
there is nothing honest to show there yet, and the survey found them optional.
The GitHub star count is fetched live. At first it showed only once it was
large enough to say something; since November 2026 the hero's Star on GitHub
button shows it whenever the API answers.
