// A person's colour (docs/dev/collab.md "What the user sees"): one of the
// eight `--peer-N` colours, from their name, so someone looks the same
// wherever they are drawn — the page's avatar stack, a block's chips and
// carets, a comment, a mention, the Inbox. A share-link visitor's name is
// the one they chose. Two people may share a colour. Pure, so node tests it
// (tests/personColor.test.mjs).
export function personColor(name) {
  let hash = 0;
  for (const ch of String(name || "")) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return hash % 8;
}
