// Published pages' public addresses (docs/dev/mirror.md "Publishing"):
// https://<username>-pages.gammapdf.com/<slug>-<page id> on the share host.
// The slug is decoration (routing uses the trailing id); these mirror
// backend/gamma/publish.py `slug` / `page_host_user`, and the cases in
// tests/shared/slug.json pin both sides.

const SLUG_MAX = 60;
const PLACEHOLDER = "{username}";
const LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

// The title ASCII-folded (NFKD, marks dropped), lowercased, every run of
// anything but [a-z0-9] one "-", trimmed, at most 60 characters; "" when
// nothing is left (a CJK title).
export function slugify(title) {
  return String(title || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX).replace(/-+$/, "");
}

// The path a published page's pretty address ends in: /<slug>-<id>, or /<id>.
export function publicPath(title, pageId) {
  const s = slugify(title);
  return `/${s ? `${s}-` : ""}${pageId}`;
}

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// The username a page hostname names under the share host's `page_host`
// pattern (server-config), "" when `hostname` is not a page host.
export function pageHostUser(pattern, hostname) {
  if (!pattern || !pattern.includes(PLACEHOLDER)) return "";
  const [head, tail] = pattern.toLowerCase().split(PLACEHOLDER);
  const host = String(hostname || "").trim().toLowerCase().replace(/:\d+$/, "");
  const m = host.match(new RegExp(`^${escapeRe(head)}([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)${escapeRe(tail)}$`));
  const labels = host.split(".");
  return m && host.length <= 253 && labels.length >= 2 && labels.every((l) => LABEL.test(l)) ? m[1] : "";
}
