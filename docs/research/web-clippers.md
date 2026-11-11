# Web clippers: what a page with no paper should save as

November 2026. The Gamma Connector saved a page that names no paper (a blog
post, a news story, a GitHub issue) as a page carrying only its title and
link (`web_url`), with a note saying no PDF was found. Notes apps that clip
the web keep the page's content too. This note records how six of them do
it and what Gamma took from them. How Gamma's clip works now is in
[dev/extension.md](../dev/extension.md#web-clips).

## What others offer

| Clipper | Modes | How the article is found | Pictures | What the clip becomes |
|---|---|---|---|---|
| OneNote Web Clipper | Full page, Region, Article, Recipe, Product, Bookmark | Article: a server-side augmentation service; Full page and Region are screenshots | Kept in the page | A OneNote page in the chosen section; Article is editable text and pictures, Bookmark a link with a preview |
| Evernote Web Clipper | Article, Simplified article, Full page, Bookmark, Screenshot; the default is a setting | Article keeps the page's styling, Simplified drops ads, menus and sidebars | Kept in the note | A note in the chosen notebook with tags; Bookmark adds the page's picture and meta description |
| Joplin Web Clipper | Simplified page, Complete page (Markdown or HTML), Selection, Screenshot, URL | Simplified is Firefox's Reader View code (Mozilla Readability) | Downloaded as the note's resources | A Markdown note in the chosen notebook with tags |
| Obsidian Web Clipper | One action; the selection, else the highlights, else the article; templates pick the fields | Defuddle (Obsidian's own, Readability-like), output through DOMPurify | Hotlinked by default; a command downloads them later | A Markdown file, the page's metadata (author, published, site) in its properties |
| SiYuan (siyuan-chrome) | Whole page (popup, `Ctrl+Shift+S`), selection (context menu, `Ctrl+Shift+C`) | Readability on a cleaned copy of the page (`keepClasses`, computed bold and italic written in, inline SVG made pictures, KaTeX and code protected) | Fetched in the browser as data URLs and sent with the HTML; when that fails the kernel downloads them itself (`/api/format/netImg2LocalAssets`) | The kernel converts the HTML to Markdown (Lute, `/api/extension/copy`) and creates a document under the chosen parent |
| Zotero Connector | Save the item (a translator's record) with a snapshot; any page as "Web Page with Snapshot" | No extraction: SingleFile saves the whole page as one HTML file | Inside the snapshot | An item with the snapshot attached; Zotero 7 can annotate snapshots |

What they share:

- **The article is the default content, the link the fallback.** Each one
  that extracts uses a Reader View-style algorithm (Readability or a
  descendant) and leaves out navigation, sidebars and footers. Screenshots
  and full-page copies are for pages extraction gets wrong.
- **A selection is its own mode.** Joplin and SiYuan list it beside the
  article; Obsidian takes it whenever there is one.
- **Pictures are fetched in the browser when they are kept.** The browser
  has the page's cookies and passes a site's hotlink rules, which a server
  fetch may not. Obsidian is the exception: it keeps the address and
  downloads later.
- **The clip goes where the user files it**, a section, a notebook or a
  parent document, with tags. The mode can be the user's default (Evernote).

## What fits Gamma

Gamma's notes are Markdown blocks, nested under their headings, so the
clip has to become blocks rather than a stored HTML page. That rules out
Zotero's snapshot and OneNote's screenshots as the main content: neither
can be edited, searched block by block or linked to a block. The parts that
fit:

1. **Article, Selection, Link.** These are the three that make blocks or
   a link. The popup shows them as one row above the folder and labels,
   only on a page that names no paper. Article is the default. Selection is
   chosen when text is selected as the popup opens, as Obsidian does. The
   last pick between Article and Link is remembered, as Evernote's default
   is, and used by the keyboard shortcut and *Save page to Gamma*. A
   selection is never remembered, since it belongs to that page.
2. **Readability in the browser, Markdown on the server, as SiYuan
   splits it.** Finding the article needs the live page: lazy pictures'
   real addresses, what a srcset shows, a formula's TeX, a code block's
   language. So the browser runs Mozilla Readability on a copy of the page,
   after writing those onto it. The HTML to Markdown step is on the server
   (`gamma/web_clip.py`). There it reuses the MathML-to-LaTeX reader the
   metadata code already has and the parser the Markdown import uses, and
   pytest covers it. Readability is about 90 KB, so the extension injects
   it only when a clip starts (`chrome.scripting`) instead of on every page.
3. **Pictures fetched and uploaded by the browser**, like SiYuan's. One
   that fails keeps its address, as Obsidian's do, and the note still shows
   it while the site serves it. A server-side fallback like SiYuan's was
   left out: a picture the browser couldn't fetch is usually behind the
   same wall for the server.
4. **A source line** (`[site](url) · author · date`) as the clip's first
   block, as OneNote and Evernote head a clip with its address. The page
   also keeps the address as `web_url`, which the "already in your library"
   check matches. That check runs only when the popup opens on the page;
   looking up every page as it loads would send each address visited to
   the server.

## Left out, and why

- **Screenshots and full-page copies.** A stitched full-page screenshot
  needs scrolling capture or the `debugger` permission, which has an alarming
  install warning. A screenshot is also a picture, not notes. Selection
  covers the case where extraction picks the wrong part.
- **Templates** (Obsidian). Gamma has no front matter for a template to
  fill; the page's properties and the source line hold the metadata.
- **Highlighting on the page before clipping** (OneNote's Article
  highlighter, Obsidian's highlights). This would need a page overlay and
  state across visits, which is out of scope for the Connector
  ([dev/extension.md](../dev/extension.md) "Non-goals").

## Open

- Pages that render their text inside shadow DOM or canvas give
  Readability nothing; the clip then keeps the link and says so.
- Readability splits a title on `|`, `-`, `/`, `>` and `»` only. The
  Connector also strips a trailing site name after `—`, `–`, `·` or `:`
  when the page names its site.
- A clip of a page saved before is not refreshed. Saving again files the
  page that is there, as for papers.

## Sources

- Microsoft, [Getting started with the OneNote Web Clipper](https://support.microsoft.com/en-us/onenote/getting-started-with-the-onenote-web-clipper).
- Evernote, [Clip formats](https://help.evernote.com/hc/en-us/articles/209125827-Clip-formats) and [Evernote Web Clipper Quick Start Guide](https://help.evernote.com/hc/en-us/articles/209125877-Evernote-Web-Clipper-Quick-Start-Guide).
- Joplin forum, [Web Clipper is now available](https://discourse.joplinapp.org/t/web-clipper-is-now-available-beta-feature/154) (Simplified mode built on Reader View), and [DeepWiki's summary of the clipper's source](https://deepwiki.com/laurent22/joplin/3.4-web-clipper-extension).
- Obsidian, [Web Clipper](https://obsidian.md/clipper), [obsidianmd/obsidian-clipper](https://github.com/obsidianmd/obsidian-clipper) and [kepano/defuddle](https://github.com/kepano/defuddle).
- SiYuan, [siyuan-note/siyuan-chrome](https://github.com/siyuan-note/siyuan-chrome): `content.js` (`siyuanGetReadability`, the image fetch) and `background.js` (`/api/extension/copy`, `netImg2LocalAssets`), read November 2026.
- Zotero, [Adding items to Zotero](https://www.zotero.org/support/adding_items_to_zotero) and the [SingleFile snapshot pull request](https://github.com/zotero/zotero-connectors/pull/319).
- Mozilla, [Readability.js](https://github.com/mozilla/readability) 0.6.0, vendored as `extension/lib/Readability.js`.
