// A web address as a note's chip and its link card (shared/ui/LinkCard.jsx)
// show it, in the rendered view and the editor alike
// (docs/dev/mentions.md "The link card"): its words or a readable label,
// the site's icon, a short muted line, and the page's title and author
// from GET /api/link-preview (backend gamma/link_preview.py), asked once
// per address. Pure but for that fetch and useSitePreview, so node tests
// the labels (tests/webLinks.test.mjs).
import { useEffect, useState } from "react";
import { t } from "../i18n/i18n.js";

// --- a web page's title --------------------------------------------------------

// The page's {title, author}: a bare link's chip label, its card's title
// and the author on its card's muted line. `sitePreview` is what is known
// now (null before the answer), `loadSitePreview` the answer ({} for
// none), `useSitePreview` both for a component.
export const NO_PREVIEW = Object.freeze({});
const sitePreviews = new Map();   // href → {title, author} | Promise
const isWeb = (href) => /^https?:\/\//i.test(href || "");
export function sitePreview(href) {
  const p = sitePreviews.get(href);
  return p && !(p instanceof Promise) ? p : null;
}
export function loadSitePreview(href) {
  if (!isWeb(href)) return Promise.resolve(NO_PREVIEW);
  const known = sitePreviews.get(href);
  if (known) return known instanceof Promise ? known : Promise.resolve(known);
  const asked = fetch(`/api/link-preview?url=${encodeURIComponent(href)}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => (d?.title ? { title: d.title, author: d.author || "" } : NO_PREVIEW))
    .catch(() => NO_PREVIEW)
    .then((p) => { sitePreviews.set(href, p); return p; });
  sitePreviews.set(href, asked);
  return asked;
}
export function useSitePreview(href, enabled = true) {
  const [preview, setPreview] = useState(() => sitePreview(href) || NO_PREVIEW);
  useEffect(() => {
    if (!enabled || !isWeb(href)) return undefined;
    let dead = false;
    loadSitePreview(href).then((p) => { if (!dead) setPreview(p); });
    return () => { dead = true; };
  }, [href, enabled]);
  return sitePreview(href) || preview;
}

// --- labels ---------------------------------------------------------------------

export const hostOf = (href) => { try { return new URL(href).hostname.replace(/^www\./, ""); } catch { return ""; } };

// GitHub addresses read without a fetch: owner/repo, #issue or PR, the file
// a blob or tree link points at.
function githubLabel(href) {
  try {
    const u = new URL(href);
    if (!/(^|\.)github\.com$/i.test(u.hostname)) return "";
    const p = u.pathname.split("/").filter(Boolean);
    if (p.length === 0) return t("GitHub");
    if (p.length === 1) return p[0];
    const repo = `${p[0]}/${p[1]}`;
    if (["issues", "pull", "discussions"].includes(p[2]) && p[3]) return `${repo} #${p[3]}`;
    if (["blob", "tree"].includes(p[2]) && p.length > 3) return `${repo} · ${p[p.length - 1]}`;
    if (p[2] === "releases") return `${repo} · releases`;
    if (p[2] === "commit" && p[3]) return `${repo} @ ${p[3].slice(0, 7)}`;
    return repo;
  } catch { return ""; }
}

// Whether a web link's words are only its address (a bare link, or link
// text that is itself an address): its chip then shows what it points to.
export const bareLinkText = (text) => !String(text || "").trim() || /^(https?:\/\/|www\.)/i.test(String(text).trim());
// What a web link's chip says: its own words, else GitHub's readable name,
// the site's title (`title`, sitePreview's) or the host. `fetches`:
// whether the site's title would change it (it has no words, and is no
// GitHub link).
export function webLinkLabel(href, text, title) {
  if (!bareLinkText(text)) return { label: text, fetches: false };
  const gh = githubLabel(href);
  return { label: gh || title || hostOf(href) || text || href, fetches: !gh };
}
// The site's icon a web link's chip shows ("" for an address with no host).
export function siteIconUrl(href) {
  const host = hostOf(href);
  return host ? `https://icons.duckduckgo.com/ip3/${host}.ico` : "";
}
// An address as one muted line: host and path, no scheme, cut in the middle.
export function shortUrl(href) {
  const s = String(href || "").replace(/^https?:\/\/(www\.)?/i, "").replace(/\/$/, "");
  return s.length > 56 ? `${s.slice(0, 34)}…${s.slice(-18)}` : s;
}
