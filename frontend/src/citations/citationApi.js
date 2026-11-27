// The server's store of the styles an account added (routers/citations.py,
// docs/dev/citations.md): the list, the search of the CSL repository, adding
// by repository id or from a .csl file, removing, and the XML a style
// renders with. Every call is the signed-in account's own: a guest or an
// integration token is refused with 403, a visitor with no account (an
// anonymous share view) with 401.
//
// The list is held here, once for the tab, for everyone who shows or uses
// it: the app's copies (useCitations), Settings → Citations and the Export
// dialog's Formatted bibliography read it through subscribeStyles and
// styleList (citations/CitationChoices.jsx useAddedStyles), and an add or
// a removal changes it in place. It also remembers the styles a render
// found gone from this server (a 404 for its XML), which citeRules.js
// styleInUse reads with it. The rows feed styleMeta (render.js), so an
// added style has its title wherever its id is shown, and each id has a
// revision, so an engine built from a style that was removed and added
// again (a new upload under the same id) is not reused.
import { API, apiError, apiJson } from "../shared/lib/utils";

// {loaded, styles: [{id, title, short_title, kind, locale, parent, source,
// added_at}], limit, downloads, own, error, missing}: `loaded` once a read
// answered, `downloads` whether this server may fetch from the repository
// (its citation_styles option; off, only a file can be added), `own` false
// for a viewer with no styles of their own here (401, 403), whose list is
// empty rather than an error, `error` a failed read's reason (the styles
// of the last good one stay), `missing` the ids a render found gone.
const UNREAD = Object.freeze({ loaded: false, styles: [], limit: 50, downloads: false, own: true, error: "", missing: [] });
let list = UNREAD;
let asking = null;
const listeners = new Set();
const revisions = new Map();

function publish(change) {
  list = Object.freeze({ ...list, ...change });
  for (const fn of listeners) fn();
}
const bump = (id) => revisions.set(id, (revisions.get(id) || 0) + 1);
const without = (rows, id) => rows.filter((row) => row.id !== id);

export const subscribeStyles = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
// The list as held now; a new object each time it changes.
export const styleList = () => list;
// The row of an added style as the list holds it, else null.
export const addedStyle = (id) => list.styles.find((row) => row.id === id) || null;
// Changes each time the style under `id` is added or removed.
export const styleRevision = (id) => revisions.get(id) || 0;

// Reads the list (GET /api/citations/styles) unless it is held already or
// on its way; `force` reads it again (Settings on each visit, a default
// the held list lacks). Resolves with the list, its failure in `error`.
export function loadStyles({ force = false } = {}) {
  if (asking) return asking;
  if (list.loaded && !force) return Promise.resolve(list);
  asking = apiJson(`${API}/citations/styles`).then(
    (answer) => publish({ loaded: true, styles: answer?.styles || [], limit: answer?.limit || 50,
      downloads: !!answer?.downloads, own: true, error: "", missing: [] }),
    (err) => {
      const none = err?.status === 401 || err?.status === 403;
      publish(none ? { ...UNREAD, loaded: true, own: false } : { loaded: true, error: err?.message || "failed" });
    }).then(() => list).finally(() => { asking = null; });
  return asking;
}

// {results: [{id, title, short_title, kind, dependent, updated}]}, the best
// 50 matches of `q` over title, short title and id. 403 when downloads are
// off on this server.
export const searchStyles = (q) => apiJson(`${API}/citations/repository?q=${encodeURIComponent(q || "")}`);

function added(row) {
  if (row?.id) {
    bump(row.id);
    publish({ styles: [...without(list.styles, row.id), row], missing: list.missing.filter((id) => id !== row.id) });
  }
  return row;
}

// Adds the repository's style `id`; answers its row. The stored id is the
// style's own, which differs from `id` when the repository renamed it.
export async function addStyle(id) {
  return added(await apiJson(`${API}/citations/styles`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }),
  }));
}

// Adds the style in a .csl file (a File or Blob); answers its row. A file
// that is no CSL style is refused with the reason (400).
export async function uploadStyle(file) {
  const form = new FormData();
  form.append("file", file, file?.name || "style.csl");
  return added(await apiJson(`${API}/citations/styles/upload`, { method: "POST", body: form }));
}

export async function removeStyle(id) {
  const answer = await apiJson(`${API}/citations/styles/${encodeURIComponent(id)}`, { method: "DELETE" });
  bump(id);
  publish({ styles: without(list.styles, id) });
  return answer;
}

// What an added style renders with: {xml, parent, locale}. `parent` names a
// bundled style when the added one is a dependent of it (then `xml` is the
// dependent itself and the parent's file renders); `locale` is the
// dependent's default-locale, which it imposes on its parent. A failure
// throws the server's reason with its status; a 404 (not on this server)
// is remembered in the list's `missing`.
export async function fetchStyleXml(id) {
  const res = await fetch(`${API}/citations/styles/${encodeURIComponent(id)}.csl`, { credentials: "include" });
  if (!res.ok) {
    if (res.status === 404 && !list.missing.includes(id)) publish({ missing: [...list.missing, id] });
    throw await apiError(res);
  }
  return { xml: await res.text(), parent: res.headers.get("X-Csl-Parent") || "", locale: res.headers.get("X-Csl-Locale") || "" };
}
