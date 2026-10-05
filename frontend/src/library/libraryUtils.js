// Pure application-domain helpers. Keeping these outside App makes the rules
// usable by dialogs, home views, and future tests without coupling them to React.

import { generateNKeysBetween } from "fractional-indexing";
import { fmtDate, t } from "../shared/i18n/i18n.js";

// How a folder's path reads: its names from the top, joined (the server's
// blocks_store.PATH_SEP). A name may hold any character, "/" and ","
// included, so a path is only ever shown, never split.
export const PATH_SEP = " / ";

// The library's "pages without a label" view is a pseudo-label: this sentinel
// stands in for it wherever a label id flows (labelFilter, the label tiles,
// the per-view sort/kind pref keys). "~" is no block-id character
// (blocks_store.BLOCK_ID_RE), so no label can have it.
export const NO_LABEL = "~none";
export const NO_LABEL_TITLE = t("No label");

// Folders and labels are blocks of two trees beside the pages
// (docs/dev/home_library.md "Folders and labels"); the library listing
// carries both (GET /blocks/root/children's `folders` / `labels`: block
// dicts with their `children`, in order). As lookups:
//   folders  Map id → {id, name, parent ("" at the top), position, pinned,
//            path: [names from the top], children: [ids, in order]}
//   labels   Map id → {id, name}, in order
//   top      the top-level folder ids, in order
// A folder share's listing has the shared folder as its one top folder.
export function libraryTree({ folders, labels } = {}) {
  const tree = { folders: new Map(), labels: new Map(), top: [] };
  const walk = (nodes, parent, path) => nodes.map((node) => {
    const name = node.content || "";
    const folder = { id: node.id, name, parent, position: node.position || "",
      pinned: node.properties?.pinned || "", path: [...path, name], children: [] };
    tree.folders.set(node.id, folder);
    folder.children = walk(node.children || [], node.id, folder.path);
    return node.id;
  });
  tree.top = walk(folders?.children || [], "", []);
  for (const node of labels?.children || []) tree.labels.set(node.id, { id: node.id, name: node.content || "" });
  return tree;
}
export const EMPTY_TREE = libraryTree();

// The folders right inside `parent` ("" = the top level), in order.
export const childFolders = (tree, parent) => (parent ? tree.folders.get(parent)?.children || [] : tree.top);
// The folder blocks right inside `parent`, in order.
export const siblingFolders = (tree, parent) => childFolders(tree, parent).map((id) => tree.folders.get(id));
// A folder's path as it reads ("" for an id the tree lacks).
export const folderPath = (tree, id) => tree.folders.get(id)?.path.join(PATH_SEP) || "";
// A label's name, the "No label" pseudo-label's title included.
export const labelName = (tree, id) => (id === NO_LABEL ? NO_LABEL_TITLE : tree.labels.get(id)?.name || "");
// Every folder as {id, name}, named by its path: what a typed name is
// matched against and what a folder picker or filter lists.
export const folderEntries = (tree) => [...tree.folders.keys()].map((id) => ({ id, name: folderPath(tree, id) }));

// The folders from the top down to `id`, ids; [] when it is no folder.
export function folderChain(tree, id) {
  const chain = [];
  for (let folder = tree.folders.get(id); folder; folder = tree.folders.get(folder.parent)) chain.unshift(folder.id);
  return chain;
}
// Whether `id` is the folder `ancestor` or one below it.
export const inFolder = (tree, id, ancestor) => folderChain(tree, id).includes(ancestor);
// `id` and every folder below it.
export function folderSubtree(tree, id) {
  const ids = new Set();
  const add = (fid) => { ids.add(fid); for (const child of tree.folders.get(fid)?.children || []) add(child); };
  if (tree.folders.has(id)) add(id);
  return ids;
}
// The folders of `order` (every folder by default) the `moving` folders can
// be put into: none of them, nor one below them.
export const folderTargets = (tree, moving, order = [...tree.folders.keys()]) =>
  order.filter((id) => !moving.some((m) => inFolder(tree, id, m)));
// The sibling a folder steps past to go one place up (step -1) or down (+1)
// in its folder's order; "" when it is already first or last.
export function folderNeighbour(tree, id, step) {
  const siblings = childFolders(tree, tree.folders.get(id)?.parent);
  const at = siblings.indexOf(id);
  return at < 0 ? "" : siblings[at + step] || "";
}

// A page's filing (`properties.folders` / `labels`) as the ids a block of
// the tree has: an id whose block is gone, or not synced here yet, names
// nothing and is passed by — and goes with the page's next refiling, which
// always writes from this.
export const filedIn = (map, ids) => (Array.isArray(ids) ? ids.filter((id) => map.has(id)) : []);
// The path of the first folder a page is filed in ("" when none).
export const firstFolderPath = (tree, ids) => folderPath(tree, filedIn(tree.folders, ids)[0]);

// A page's folders and labels as chips, {id, name} — a folder named by its
// path — from its properties: what a card, a row and a lookup show and match.
export function filingChips(tree, properties) {
  return {
    folders: filedIn(tree.folders, properties?.folders).map((id) => ({ id, name: folderPath(tree, id) })),
    labels: filedIn(tree.labels, properties?.labels).map((id) => ({ id, name: tree.labels.get(id).name })),
  };
}

// A page's folders after filing it into `folder` — a soft link, its other
// folders kept, except one above the new one: refining "readout" into
// "readout / nondestructive" shouldn't leave both levels.
export function addToFolder(tree, ids, folder) {
  const above = new Set(folderChain(tree, folder));
  return [...filedIn(tree.folders, ids).filter((id) => !above.has(id)), folder];
}

// The folder or label a typed name means among `items` ({id, name}): the
// one called that exactly, else ignoring case — the server's
// blocks_store.named. Null when none is.
export function findNamed(items, name) {
  const want = name.trim(), list = [...items];
  return list.find((it) => it.name.trim() === want)
    || list.find((it) => it.name.trim().toLowerCase() === want.toLowerCase()) || null;
}

// The positions (fractional keys, in order) for the `moving` folders put
// among `parent`'s folders before `before`, else last — those of them
// already among the siblings are counted out first, so a drop beside a
// neighbour lands between the right two.
export function folderPositions(tree, parent, before = "", moving = []) {
  const skip = new Set(moving);
  const siblings = siblingFolders(tree, parent).filter((folder) => !skip.has(folder.id));
  const at = siblings.findIndex((folder) => folder.id === before);
  const idx = at < 0 ? siblings.length : at;
  return generateNKeysBetween(siblings[idx - 1]?.position || null, siblings[idx]?.position || null, moving.length);
}
// The position for one folder put before `before`, else last; `moving` is
// the folder itself when it is already among them.
export const folderPosition = (tree, parent, before = "", moving = "") => folderPositions(tree, parent, before, [moving])[0];

// A stored timestamp → a Date (a bare one is UTC, as the server writes it).
const parseStamp = (iso) => new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);

export function formatRelativeTime(iso, now = Date.now()) {
  if (!iso) return "";
  const then = parseStamp(iso).getTime();
  const secs = Math.max(1, Math.floor((now - then) / 1000));
  if (secs < 60) return t("{n}s ago", { n: secs });
  const mins = Math.floor(secs / 60);
  if (mins < 60) return t("{n}m ago", { n: mins });
  const hours = Math.floor(mins / 60);
  if (hours < 24) return t("{n}h ago", { n: hours });
  const days = Math.floor(hours / 24);
  if (days < 7) return t("{n}d ago", { n: days });
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return t("{n}w ago", { n: weeks });
  const months = Math.floor(days / 30);
  if (months < 12) return t("{n}mo ago", { n: months });
  return t("{n}y ago", { n: Math.floor(days / 365) });
}

// The list view's date column, in the reader's time zone: "Today, 9:41",
// "Yesterday", "Sep 12" within the year, else the year alone.
export function formatShortDate(iso, now = Date.now()) {
  if (!iso) return "";
  const then = parseStamp(iso);
  if (Number.isNaN(then.getTime())) return "";
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const day = new Date(then);
  day.setHours(0, 0, 0, 0);
  const days = Math.round((today - day) / 86400000);
  if (days <= 0) return t("Today, {time}", { time: fmtDate(then, { hour: "numeric", minute: "2-digit" }) });
  if (days === 1) return t("Yesterday");
  if (then.getFullYear() === today.getFullYear()) return fmtDate(then, { month: "short", day: "numeric" });
  return String(then.getFullYear());
}

// The full date and time, for the date column's hover title.
export function formatFullDate(iso) {
  if (!iso) return "";
  const then = parseStamp(iso);
  return Number.isNaN(then.getTime()) ? "" : fmtDate(then, { dateStyle: "medium", timeStyle: "short" });
}

// The page's attachment — today the single PDF slot (`properties.doc_id` +
// `source_url` / `original_filename`). Every "what does this page carry"
// decision (layout, card badge, gating, copy) reads THIS, never the raw
// properties, so a later `properties.attachments` list is a drop-in
// (docs/dev/block_centric.md). Null for a page that is only blocks. `url` is
// where the file is fetched from: the stored source_url, else the stored copy
// by hash — a page stores no source_url that is its own doc_id's
// (gamma/blocks_store.py page_attachment, the twin).
export function pageAttachment(block) {
  const p = block?.properties || {};
  if (!p.doc_id && !p.source_url) return null;
  return { kind: "pdf", id: p.doc_id || "", url: p.source_url || `/api/uploads/${p.doc_id}.pdf`,
    name: p.original_filename || "" };
}

// What a card/row says a page is: the attachment kind, else just a page.
export function pageKindLabel(attachment) {
  return attachment ? "PDF" : t("Page");
}

// Title for a page whose title is empty: the attachment's file name or URL
// tail, else "Untitled". (The old "PDF Notes - …" prefix is gone; existing
// pages were normalized by `manage.py migrate`.)
export function defaultPageTitle(attachment) {
  if (!attachment) return "Untitled";
  if (attachment.name) return attachment.name;
  const tail = (attachment.url || "").split("/").pop() || "";
  let cleaned = tail;
  try { cleaned = decodeURIComponent(tail); } catch {}
  return cleaned.trim() || "Untitled";
}

export function metadataToDraft(metadata) {
  return {
    title: metadata?.title || "",
    authors: (metadata?.authors || []).join(", "),
    venue: metadata?.venue || "",
    year: metadata?.year || "",
    volume: metadata?.volume || "",
    pages: metadata?.pages || "",
    doi: metadata?.doi || "",
    arxiv_id: metadata?.arxiv_id || "",
    publisher: metadata?.publisher || "",
    isbn: metadata?.isbn || "",
  };
}

// The citation key of a BibTeX entry (`@article{key,` …), or "". Shown as the
// placeholder of the popover's Cite key row, so an unpinned page displays the
// key it is actually cited by without the client re-deriving it.
export function citationKeyOf(bibtex) {
  return (/^\s*@\s*[A-Za-z]+\s*\{\s*([^,\s]+)/.exec(bibtex || "") || [])[1] || "";
}

export function friendlyApiError(error) {
  const message = error?.message || "failed";
  return /Unexpected token|Method Not Allowed|not valid JSON/i.test(message)
    ? "endpoint missing — restart/update the server"
    : message.slice(0, 120);
}

// A library with nothing of the user's in it yet: no pages, or only the ones
// seeding put there (properties.seeded — the Welcome page every new account
// starts with, gamma/seed.py). What "Start your library" and the first
// tour's offer mean by a new library.
export function isFreshLibrary(pages) {
  return (pages || []).every((page) => page?.properties?.seeded);
}

export function findPageForUrl(url, pages) {
  const doiMatch = (url || "").match(/10\.\d{4,9}\/[^\s?#]+/);
  const doi = doiMatch ? decodeURIComponent(doiMatch[0]).replace(/[.,;)\]]+$/, "").toLowerCase() : "";
  const arxivMatch = (url || "").match(/arxiv(?:\.org\/(?:abs|pdf)\/|[:.])(\d{4}\.\d{4,5})/i);
  const arxivId = arxivMatch ? arxivMatch[1] : "";
  if (!doi && !arxivId) return null;

  for (const page of pages || []) {
    const properties = page.properties || {};
    const metadata = properties.meta || {};
    if (doi && (metadata.doi || "").toLowerCase() === doi) return page.id;
    if (arxivId && (metadata.arxiv_id === arxivId || (properties.source_url || "").includes(arxivId))) return page.id;
    if (doi && (properties.source_url || "").toLowerCase().includes(doi)) return page.id;
  }
  return null;
}

export function scorePaperMatch(text, page) {
  const normalizedText = (text || "").toLowerCase();
  if (!normalizedText) return 0;
  const words = new Set(normalizedText.split(/[^a-z0-9]+/).filter((word) => word.length > 3));
  const metadata = page.properties?.meta || {};
  let score = 0;
  if (metadata.doi && normalizedText.includes(String(metadata.doi).toLowerCase())) score += 20;
  if (metadata.arxiv_id && normalizedText.includes(metadata.arxiv_id)) score += 20;
  for (const author of (metadata.authors || [])) {
    const lastName = String(author).trim().split(/\s+/).pop().toLowerCase();
    if (lastName.length > 2 && normalizedText.includes(lastName)) score += 4;
  }
  for (const word of String(metadata.title || page.content || "").toLowerCase().split(/[^a-z0-9]+/)) {
    if (word.length > 3 && words.has(word)) score += 2;
  }
  if (metadata.year && normalizedText.includes(String(metadata.year))) score += 2;
  if (metadata.volume && new RegExp(`\\b${metadata.volume}\\b`).test(normalizedText)) score += 2;
  for (const word of String(metadata.venue || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/)) {
    if (word.length > 2 && normalizedText.includes(word)) score += 1;
  }
  return score;
}

export function normalizeLinkInput(value) {
  const input = (value || "").trim();
  if (!input) return "";
  if (/^https?:\/\//i.test(input)) return input;
  if (/^arxiv:/i.test(input)) return `https://arxiv.org/abs/${input.slice(6).trim()}`;
  if (/^\d{4}\.\d{4,5}(v\d+)?$/.test(input)) return `https://arxiv.org/abs/${input}`;
  return `https://doi.org/${input.replace(/^doi:\s*/i, "")}`;
}
