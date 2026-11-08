// Pure application-domain helpers. Keeping these outside App makes the rules
// usable by dialogs, home views, and future tests without coupling them to React.

import { generateNKeysBetween } from "fractional-indexing";
import { fmtDate, t, tn } from "../shared/i18n/i18n.js";
import { itemTypeOf } from "../shared/lib/itemTypes.js";

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
//            created, path: [names from the top], children: [ids, in order]}
//   labels   Map id → {id, name}, in order
//   top      the top-level folder ids, in order
// A folder share's listing has the shared folder as its one top folder.
export function libraryTree({ folders, labels } = {}) {
  const tree = { folders: new Map(), labels: new Map(), top: [] };
  const walk = (nodes, parent, path) => nodes.map((node) => {
    const name = node.content || "";
    const folder = { id: node.id, name, parent, position: node.position || "",
      pinned: node.properties?.pinned || "", created: node.created_at || "", path: [...path, name], children: [] };
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

// Sub-pages (docs/dev/home_library.md "Sub-pages"): a page whose
// `properties.parent_page` names another page is inside it. The listing
// gives every page its effective parent (`parent`, "" for a library page;
// the server's blocks_store.page_parent), which App's page blocks carry as
// `_parentPage`. How a page's path reads: its ancestors' titles, joined.
export const PAGE_PATH_SEP = " › ";
// The most pages a chain holds, its top included, so at most
// MAX_PAGE_DEPTH - 1 above a page (blocks_store.MAX_PAGE_DEPTH).
export const MAX_PAGE_DEPTH = 64;

// The pages above `id`, top first, as {id, title}, from `pagesById` (a Map
// of page blocks by id). The walk stops at a parent the map lacks, as the
// server's does at one that is not live; a chain that comes back to a page
// or holds more than MAX_PAGE_DEPTH pages (only a move not yet answered can
// make one) means no parent, as on the server. [] for a library page.
export function pageAncestors(pagesById, id) {
  const chain = [];
  const seen = new Set([id]);
  for (let page = pagesById.get(pagesById.get(id)?._parentPage); page; page = pagesById.get(page._parentPage)) {
    if (seen.has(page.id) || chain.length >= MAX_PAGE_DEPTH - 1) return [];
    seen.add(page.id);
    chain.unshift({ id: page.id, title: page.content || t("Untitled") });
  }
  return chain;
}
// The page `id` is filed by, from `pagesById`: its top's entry, or its own
// for a library page (the server's blocks_store.page_top); a sub-page's own
// folders count for nothing. undefined for a page the map lacks.
export const topPageOf = (pagesById, id) => pagesById.get(pageAncestors(pagesById, id)[0]?.id ?? id);
// Where a sub-page is, as a picker's second line names it ("Top › Mid");
// "" for a library page.
export const pagePathLabel = (pagesById, id) => pageAncestors(pagesById, id).map((a) => a.title).join(PAGE_PATH_SEP);
// Where a page is, its own title last ("Top › Mid › Page"): how Recently
// deleted names the page a restore puts a sub-page back inside.
export const pagePlaceLabel = (pagesById, id) =>
  [pagePathLabel(pagesById, id), pagesById.get(id)?.content || t("Untitled")].filter(Boolean).join(PAGE_PATH_SEP);
// The topmost of pages `ids`: those not inside another of them, at any
// depth, in their order, from `pages` (page blocks or listing rows, by
// their `_parentPage`). What Delete and the moves act on, so a page inside
// a picked page goes, or stays, with it.
export function topmostPages(pages, ids) {
  const parents = new Map((pages || []).map((page) => [page.id, page._parentPage]));
  const picked = new Set(ids);
  return ids.filter((id) => {
    const seen = new Set([id]);
    for (let up = parents.get(id); up && !seen.has(up); up = parents.get(up)) {
      if (picked.has(up)) return false;
      seen.add(up);
    }
    return true;
  });
}
// The ids of the pages inside pages `ids`, at any depth, parents first,
// from `pages` (page blocks or listing rows, by their `_parentPage`); none
// of `ids` among them.
export function pageDescendants(pages, ids) {
  const children = new Map();
  for (const page of pages || []) {
    if (!page._parentPage) continue;
    if (!children.has(page._parentPage)) children.set(page._parentPage, []);
    children.get(page._parentPage).push(page.id);
  }
  const queue = [...ids], seen = new Set(ids);
  for (let i = 0; i < queue.length; i++) {
    for (const sub of children.get(queue[i]) || []) {
      if (!seen.has(sub)) { seen.add(sub); queue.push(sub); }
    }
  }
  return queue.slice(ids.length);
}
// Pages `ids` dropped on page `target` (a library row or card, a Pages
// inside row, a crumb), from `pages` as above: `moving`, the topmost of
// them (as the menu's moves take them) that are not right inside it
// already, go inside it. `refused` says why none may: "self" when the
// target is one of them (a drag let go where it began), "inside" when it is
// a page inside one of them, where they cannot go, as Move into page…
// leaves those out of its list; "" otherwise.
export function pagesIntoPage(pages, ids, target) {
  const tops = topmostPages(pages, ids);
  if (tops.includes(target)) return { refused: "self", moving: [] };
  if (pageDescendants(pages, tops).includes(target)) return { refused: "inside", moving: [] };
  const parents = new Map((pages || []).map((page) => [page.id, page._parentPage]));
  return { refused: "", moving: tops.filter((id) => parents.get(id) !== target) };
}

// App's page list (`pageBlocks`): the listing's rows, `_parentPage` added,
// as the home view's cards and rows, the [[ and @ pickers and the chips
// read them: {id, content ("Untitled" when empty), _pageId, _attachment,
// _preview, _folders, _labels (ids the trees have), _folderChips,
// _labelChips ({id, name}, filingChips), _createdAt, _updatedAt, _pinned,
// _parentPage, _path, _subPages}. A page is filed by its top, so a
// sub-page's folders are its top's (the server's blocks_store.filed_folders)
// and its labels its own; `_path` is where it is (pagePathLabel, "" for a
// library page) and `_subPages` how many pages are right inside it.
export function pageBlocksOf(rows, tree) {
  const byId = new Map(rows.map((b) => [b.id, b]));
  const chains = rows.map((b) => (b._parentPage ? pageAncestors(byId, b.id) : []));
  const inside = new Map();
  for (const chain of chains) {
    const parent = chain.at(-1)?.id;
    if (parent) inside.set(parent, (inside.get(parent) || 0) + 1);
  }
  return rows.map((b, i) => {
    const chain = chains[i];
    const own = filingChips(tree, b.properties);
    const folders = chain.length ? filingChips(tree, byId.get(chain[0].id).properties).folders : own.folders;
    return {
      id: b.id,
      content: b.content || t("Untitled"),
      _pageId: b.id,
      _attachment: pageAttachment(b),
      _preview: b.preview || "",
      _folders: folders.map((c) => c.id),
      _labels: own.labels.map((c) => c.id),
      _folderChips: folders,
      _labelChips: own.labels,
      _createdAt: b.created_at || "",
      _updatedAt: b.updated_at || "",
      _pinned: b.properties?.pinned || "",
      _parentPage: b._parentPage || "",
      _path: chain.map((a) => a.title).join(PAGE_PATH_SEP),
      _subPages: inside.get(b.id) || 0,
    };
  });
}

// The confirmation of deleting pages `ids` (the library's Delete and the
// page header's trash button): {title, message, inside}. The pages inside
// them go to Recently deleted with them (the server's cascade), so `inside`
// holds their ids and the message counts them. One page is quoted by its
// title: `title` when given (the open page's own, which may be newer), else
// its entry's in `pages`.
export function deleteConfirm(pages, ids, title) {
  const inside = pageDescendants(pages, ids);
  const n = inside.length;
  if (ids.length === 1) {
    const page = title || (pages || []).find((p) => p.id === ids[0])?.content || t("Untitled");
    return {
      title: t("Delete page"),
      message: n ? tn("Move \"{page}\" and the {n} page inside it to Recently deleted? You can restore them from there for 30 days.",
        "Move \"{page}\" and the {n} pages inside it to Recently deleted? You can restore them from there for 30 days.", n, { page })
        : t("Move \"{page}\" to Recently deleted? You can restore it from there for 30 days.", { page }),
      inside,
    };
  }
  return {
    title: t("Delete {n} pages", { n: ids.length }),
    message: n ? tn("Move these {count} pages and the {n} page inside them to Recently deleted? You can restore them from there for 30 days.",
      "Move these {count} pages and the {n} pages inside them to Recently deleted? You can restore them from there for 30 days.", n, { count: ids.length })
      : t("Move these {n} pages to Recently deleted? You can restore them from there for 30 days.", { n: ids.length }),
    inside,
  };
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
export const parseStamp = (iso) => new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);

// Now in the server's stamp shape (db.page_now: microseconds, Z), so a
// stamp made here orders with the server's.
export const nowStamp = () => new Date().toISOString().replace("Z", "000Z");

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

// The page's attachment: its one PDF (`properties.doc_id` + `source_url` /
// `original_filename`). This is the one read of what a page carries: every
// decision on it (layout, card badge, gating, copy) reads this, never the
// raw properties. Null for a page that is only blocks. `url` is where the
// file is fetched from: the stored source_url, else the stored copy by
// hash — a page stores no source_url that is its own doc_id's
// (gamma/blocks_store.py page_attachment, the twin; tests/shared/
// attachment.json holds both to the same answers).
export function pageAttachment(block) {
  const p = block?.properties || {};
  if (!p.doc_id && !p.source_url) return null;
  return { kind: "pdf", id: p.doc_id || "", url: p.source_url || `/api/uploads/${p.doc_id}.pdf`,
    name: p.original_filename || "" };
}

// The doc id of the file an attachment shows: its id, else the stored upload
// its url serves (a duplicated page carries only /api/uploads/<doc id>.pdf),
// else "" (a PDF shown from another address alone, never stored). For what
// reads the file, not for gating (gamma/blocks_store.py attachment_file, the
// twin).
export function attachmentFile(attachment) {
  if (!attachment) return "";
  return attachment.id || /^\/api\/uploads\/([0-9A-Za-z_-]{1,128})\.pdf$/.exec(attachment.url || "")?.[1] || "";
}

// What a card/row says a page is: the attachment kind, else just a page.
export function pageKindLabel(attachment) {
  return attachment ? "PDF" : t("Page");
}

// Title for a page whose title is empty: the attachment's file name or URL
// tail, else "Untitled". A restored backup's pages titled "PDF Notes - <name>"
// are renamed to <name> by gamma/normalize.py.
export function defaultPageTitle(attachment) {
  if (!attachment) return "Untitled";
  if (attachment.name) return attachment.name;
  const tail = (attachment.url || "").split("/").pop() || "";
  let cleaned = tail;
  try { cleaned = decodeURIComponent(tail); } catch {}
  return cleaned.trim() || "Untitled";
}

// The metadata popover's editable copy of a record. `kind` is the item type
// the Type row shows: the record's own, or the one its fields imply.
export function metadataToDraft(metadata) {
  return {
    kind: itemTypeOf(metadata),
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
// seeding put there (properties.seeded — the Welcome page and the Sketchbook
// every new account starts with, gamma/seed.py). What "Start your library" and the first
// tour's offer mean by a new library.
export function isFreshLibrary(pages) {
  return (pages || []).every((page) => page?.properties?.seeded);
}

// The seeded Welcome page among the library's root pages (gamma/seed.py,
// properties.seeded), if the user still has it: the welcome deck's way in.
export function welcomePageOf(pages) {
  return (pages || []).find((page) => page?.properties?.seeded === "welcome") || null;
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

// How well a library page matches a reference's text (the link dialog's
// best match first): a DOI or arXiv id weighs most, then author surnames,
// then title words, the year and the volume, then venue words.
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

// Whether the link dialog's field holds a link rather than a search of the
// pages: a web address, an arXiv id (with "arXiv:" or without) or a DOI
// (with "doi:" or without), each of which normalizeLinkInput makes a URL.
export function isLinkInput(value) {
  const input = (value || "").trim();
  return /^https?:\/\/\S+$/i.test(input) || /^arxiv:\s*\d{4}\.\d{4,5}(v\d+)?$/i.test(input)
    || /^\d{4}\.\d{4,5}(v\d+)?$/.test(input) || /^(doi:\s*)?10\.\d{4,9}\/\S+$/i.test(input);
}

export function normalizeLinkInput(value) {
  const input = (value || "").trim();
  if (!input) return "";
  if (/^https?:\/\//i.test(input)) return input;
  if (/^arxiv:/i.test(input)) return `https://arxiv.org/abs/${input.slice(6).trim()}`;
  if (/^\d{4}\.\d{4,5}(v\d+)?$/.test(input)) return `https://arxiv.org/abs/${input}`;
  return `https://doi.org/${input.replace(/^doi:\s*/i, "")}`;
}
