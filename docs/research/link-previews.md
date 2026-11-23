# Link previews: what a server-side fetch of a link gets

What a note's web link chip could say about itself, measured on
2026-11-22 from one server (no cookies, no account) against the
platforms people paste from most: Chinese video and social sites, the big
Western ones, and a few reference sites. The question was why chips in a
travel-planning note showed `bilibili.com` and `xiaohongshu.com` where the
rendered view should have shown each video's or note's title, and what a
better title fetch should do per platform.

The mechanics as built: [dev/mentions.md](../dev/mentions.md#the-link-card)
and `backend/gamma/link_preview.py`.

## What the old fetch did wrong

`GET /api/link-preview` read the first 128 KB of the page, decoded it as
UTF-8 and took `og:title` (only `property=`, only before `content=`) or
`<title>`. Three things lost titles that were there:

- **gzip nobody asked for.** Bilibili answers a request without
  `Accept-Encoding` with `Content-Encoding: gzip` anyway. The bytes read as
  UTF-8 are noise, so every Bilibili link had no title. urllib decompresses
  nothing on its own.
- **`name="og:title"`.** Xiaohongshu (and others) write the Open Graph tags
  with `name=`, which the old regexes did not read.
- **The charset.** A GBK page decoded as UTF-8 gives mojibake.
  `web_page.decode` (the chat's reader) already decodes by the header, the
  `<meta charset>` and the WHATWG aliases.

Two things gave wrong titles:

- **Gate pages.** A link the site will not show a server lands on another
  page whose title is the site's: Xiaohongshu redirects a note opened
  without its share token (`xsec_token`) to `rednote.com/404?…` titled
  "rednote (小红书) - global lifestyle hub"; Weibo to
  `passport.weibo.com/visitor/…` ("Sina Visitor System"); Douban to
  `sec.douban.com` ("豆瓣"); Reddit serves "Reddit" to a browser user agent.
- **The site's name in the title.** "…_哔哩哔哩_bilibili", "… - YouTube",
  "GitHub - owner/repo: …", "… · GitHub", "… - 小红书", "… - Wikipedia".

## Per platform

| Site | Page fetch | oEmbed | Title source chosen |
|---|---|---|---|
| Bilibili (`/video/BV…`, `b23.tv` short links) | 301 to a trailing slash, then 200, gzip; `<title>` at byte 1,266, `og:title`, `og:site_name` 哔哩哔哩, `<meta name="author">` the uploader. The `api.bilibili.com/x/web-interface/view` API answered an error page without cookies. | none | page head |
| Xiaohongshu | Without the share token: redirect to a 404 page. With it, the note's SSR head (`name="og:title"`, "… - 小红书"). | none | page head; the 404 gives no title |
| YouTube | `<title>` and `og:title` about 845 KB into a watch page, after `<body>` on a channel page | `youtube.com/oembed`: title, author | oEmbed; a channel page gives none |
| X | Script-built; a missing post is a 404 page titled "未找到帖子 - X" | `publish.twitter.com/oembed`: no title, the post's text in the HTML's first `<p>`, author | oEmbed, the post's text |
| Reddit | "Reddit" only | `reddit.com/oembed`: title, author | oEmbed |
| TikTok | not tried | `tiktok.com/oembed`: title (none for a post without a caption), author | oEmbed |
| Vimeo, Spotify | not tried | title (Vimeo's author too) | oEmbed |
| Zhihu | 403; its v4 API "请升级客户端" | none | none |
| Weibo | redirect to the visitor system | none | none |
| Douban | redirect to `sec.douban.com` | none | none |
| Douyin | 200 with no title: built by script | none public | none |
| WeChat articles | a verification page for a made-up id; real articles carry `og:title` | none | page head |
| Stack Overflow | 403 (a bot check) | none | none |
| GitHub, Wikipedia (also a non-ASCII path), arXiv, Nature | head as expected | — | page head, name trimmed |

A non-ASCII path (`zh.wikipedia.org/wiki/冰岛`) fails in urllib unless the
address is escaped first (`web_fetch.uri`).

## What was chosen

- oEmbed first where a site has an endpoint (YouTube, X, Reddit, TikTok,
  Vimeo, Spotify), the page head after. Discovery of an oEmbed endpoint
  from a page's `<link rel="alternate">` was left out: it costs a second
  fetch for every page, and the sites that need it are in the table.
- Only the head is read: at most 256 KB decompressed, stopping at `</head>`
  or `<body>`. YouTube's late title is why it goes through oEmbed rather
  than a bigger read.
- The browser's headers (`web_fetch.BROWSER_HEADERS`), in the reader's
  `Accept-Language`, so a title comes in their language where the site has
  one; the cache is per address and language.
- The site's name is cut from either end: `og:site_name`, the host, its
  domain's name and a short table of names titles carry (哔哩哔哩, 小红书,
  知乎, 豆瓣, 微博, 抖音, X, Wikipedia, 维基百科…). A title that is only a
  name or an error's ("出错啦!", "Just a moment…") is no title.
- A redirect to a sign-in, visitor, check or 404 page, or from a deep link
  to the site's home page, is no title.
- The author (the `author` meta tag, oEmbed's `author_name`) shows on the
  link card's muted line before the address.
- A miss is asked again after 15 minutes, a title kept for a day.

## Still open

- Xiaohongshu notes pasted without their share token, Zhihu, Weibo,
  Douban and Douyin give no title from a server. A preview fetched by the
  person's browser (the extension, the desktop shell) would see what they
  see; the browser handoff of paper fetching
  ([paper-fetching.md](paper-fetching.md)) is the nearest precedent.
- YouTube channel and playlist pages (no oEmbed, a title past the read).
- A platform-aware label without a fetch (X's `@user`, `r/subreddit`), as
  GitHub's links have.
