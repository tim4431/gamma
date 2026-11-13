// Links into a Gamma library, written as ordinary markdown: the AI's
// citations of a PDF passage (`/?page=<id>&pdf_page=3&quote=…`) and of a text
// file's lines (`/?page=<id>&line=12-14&quote=…`), the ⋮⋮ menu's "copy link"
// (`?block=<id>`) and a page link (`/?page=<id>`). One classifier for the
// chat, the rendered notes and the "Paste as" chooser — they used to detect
// these different ways and disagreed about what counted.
//
// The ORIGIN is deliberately not part of the test: a server moves (a desktop
// sidecar on 127.0.0.1, a NAS URL, a workspace mirrored onto another server),
// and a link pasted before the move still names the pages of this library.
// The caller confirms by resolving the id locally — a link to somebody else's
// Gamma simply doesn't resolve and stays an external link. Ids are random.

const ID_RE = /^[a-zA-Z0-9_-]+$/;
// A line link's line and its quote: mcp_links.line_span's and MAX_QUOTE's
// rule too (tests/shared/linelinks.json).
const LINE_RE = /^(\d{1,8})(?:-(\d{1,8}))?$/;
const MAX_LINE = 10_000_000;
const MAX_LINE_QUOTE = 4000; // a code line's whole text
const MAX_QUOTE = 2000;

// href → { kind: "block" | "page" | "citation" | "line", … } | null.
//   block:    { kind, blockId }
//   page:     { kind, pageId }
//   citation: { kind, pageId, page, quote }      — quote "" means "just open page N"
//   line:     { kind, pageId, line, to, quote }  — `line=N` (to null) or `line=N-M`;
//             quote the first line's verbatim text, "" for the number alone
// `foreign` marks a link written against a different host than this tab's.
export function parseGammaLink(href, origin = "http://localhost") {
  if (!href) return null;
  let url;
  try { url = new URL(href, origin); } catch { return null; }
  if (url.pathname !== "/" && url.pathname !== "") return null;
  const foreign = /^https?:\/\//i.test(href) && url.origin !== origin;

  const blockId = url.searchParams.get("block");
  if (blockId) return ID_RE.test(blockId) ? { kind: "block", blockId, foreign } : null;

  const pageId = url.searchParams.get("page");
  if (!pageId || !ID_RE.test(pageId)) return null;

  const rawPage = url.searchParams.get("pdf_page");
  const rawLine = url.searchParams.get("line");
  const quote = url.searchParams.get("quote") || "";
  // A PDF page and a line are two documents' places: a link naming both is
  // malformed, not one or the other.
  if (rawPage != null && rawLine != null) return null;
  if (rawLine != null) {
    const m = LINE_RE.exec(rawLine);
    if (!m) return null;
    const line = Number(m[1]);
    const to = m[2] == null ? null : Number(m[2]);
    if (line < 1 || line > MAX_LINE || (to != null && (to < line || to > MAX_LINE))) return null;
    // No least length: a code line is often `}` or `\fi`.
    if (quote.length > MAX_LINE_QUOTE) return null;
    return { kind: "line", pageId, line, to, quote, foreign };
  }
  if (rawPage == null) return { kind: "page", pageId, foreign };
  const page = Number(rawPage);
  if (!Number.isSafeInteger(page) || page < 1 || page > 5000) return null;
  // A quote is optional (open the page), but a present one must be usable:
  // too short to identify a passage, or too long to have been copied, is a
  // malformed link rather than a page pointer.
  if (quote && (quote.trim().length < 8 || quote.length > MAX_QUOTE)) return null;
  return { kind: "citation", pageId, page, quote, foreign };
}

// Whether a link points at a place in a document — a PDF passage or a text
// file's lines — and is drawn as a citation pill rather than a page card.
export function isSourceLink(link) {
  return link?.kind === "citation" || link?.kind === "line";
}

// The page/block this link points at, for resolving a label against the
// library (the same lookup [[ref]] chips use).
export function gammaLinkId(link) {
  return link ? (link.kind === "block" ? link.blockId : link.pageId) : null;
}

// Drop the origin and the workspace so a pasted link is stored host-free —
// notes written today keep working after the server moves. Relative input is
// returned unchanged apart from `ws`.
export function relativeGammaLink(href, origin = "http://localhost") {
  let url;
  try { url = new URL(href, origin); } catch { return href; }
  url.searchParams.delete("ws");
  return `/${url.search}`;
}

// Every Gamma link a markdown source holds, parsed: markdown link targets
// plus bare URLs (remark autolinks those, so they render as links too).
export function gammaLinksIn(text) {
  const out = [];
  const targets = /]\(([^)\s]+)\)|(^|\s)(https?:\/\/\S+|\/?\?\S+)/g;
  for (const m of String(text || "").matchAll(targets)) {
    const link = parseGammaLink(m[1] || m[3]);
    if (link) out.push(link);
  }
  return out;
}

// Whether a markdown source cites more than one source (a paper's passages,
// a file's lines): then each citation pill also names its source
// ("Vaswani · p. 2", "main.tex · line 7").
export function citesSeveralSources(text) {
  return new Set(gammaLinksIn(text).filter(isSourceLink).map((link) => link.pageId)).size > 1;
}

// The page/block ids a markdown source links to — what a renderer must
// resolve before it can label (and claim) those links.
export function gammaLinkIds(text) {
  const out = [];
  for (const link of gammaLinksIn(text)) {
    const id = gammaLinkId(link);
    if (id && !out.includes(id)) out.push(id);
  }
  return out;
}
