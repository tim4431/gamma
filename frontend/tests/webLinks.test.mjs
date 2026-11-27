// What a web link's chip and card say before and after its page's title
// comes (shared/lib/webLinks.js): the chip of the rendered view and the
// editor read the same label.
import assert from "node:assert/strict";
import { test } from "node:test";
import { bareLinkText, shortUrl, siteIconUrl, webLinkLabel } from "../src/shared/lib/webLinks.js";

test("a web link's chip shows its words, else GitHub's name, the page's title or the host", () => {
  assert.deepEqual(webLinkLabel("https://x.org/p", "the paper", "Ignored"), { label: "the paper", fetches: false });
  assert.deepEqual(webLinkLabel("https://www.x.org/p", "https://www.x.org/p", ""), { label: "x.org", fetches: true });
  assert.deepEqual(webLinkLabel("https://x.org/p", "", "A title"), { label: "A title", fetches: true });
  assert.deepEqual(webLinkLabel("https://github.com/o/r/pull/12", "", "Title"), { label: "o/r #12", fetches: false });
  assert.equal(webLinkLabel("https://github.com/o/r/blob/main/src/a.js", "").label, "o/r · a.js");
  assert.equal(webLinkLabel("https://github.com/o/r/commit/abcdef1234", "").label, "o/r @ abcdef1");
  assert.ok(bareLinkText("www.x.org") && bareLinkText(" ") && !bareLinkText("words"));
});

test("the icon and the muted line read the host; an address with none has no icon", () => {
  assert.equal(siteIconUrl("https://www.x.org/p"), "https://icons.duckduckgo.com/ip3/x.org.ico");
  assert.equal(siteIconUrl("not a url"), "");
  assert.equal(shortUrl("https://www.x.org/a/"), "x.org/a");
  const long = shortUrl(`https://x.org/${"a".repeat(80)}`);
  assert.equal(long.length, 53);
  assert.ok(long.startsWith("x.org/") && long.includes("…"));
});
