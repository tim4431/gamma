// The papers this browser last left showing their translation, with the key
// (language and model) they were shown under (docs/dev/translation.md "A paper
// opened again"). The viewer seeds a paper's pages from its kept translations
// only when this says so, so a paper never translated here asks the server
// nothing, a colleague's translation of it never appears on its own, and
// "Show original" holds across a reload. Per account and workspace, in the
// browser's own storage (`storage`, under `name`), at most `max` papers, the
// one used longest ago going first. A paper is named by the file the server
// keeps its translations under (translateState.scopeOf's `file`): the viewer's
// URL may carry a share token or a cache-busting `annots=`, and a duplicated
// page shows the same file. Storage that fails (a private window, a full
// quota) remembers nothing and breaks nothing.
export const RECALL_MAX = 300;

// A key's language and what translates into it: `<lang>|<model>`, the model a
// registry id (`<entry>:<name>`, a shared entry's `server:<entry>:<name>`), a
// service's `engine:<id>`, or "" when nothing does.
const splitKey = (key) => {
  const at = key.indexOf("|");
  return at < 0 ? [key, ""] : [key.slice(0, at), key.slice(at + 1)];
};
// An entry's id is 8 URL-safe characters (gamma/ai_settings.new_provider_id).
const ENTRY = /^(?:server:)?[A-Za-z0-9_-]{8}:/;

// Whether two keys name one language and one model, whatever connection
// reaches it: the server keeps translations under the model's name
// (routers/translate.py _kept_model), so a connection made again (a new
// entry id) or a model reached through a shared connection instead of one's
// own finds the same rows.
export function sameModel(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  const [la, ma] = splitKey(a), [lb, mb] = splitKey(b);
  return la === lb && !!ma && ENTRY.test(ma) && ENTRY.test(mb) && ma.replace(ENTRY, "") === mb.replace(ENTRY, "");
}

// The key a paper opens under (docs/dev/translation.md "A paper opened
// again"), given `remembered`, the key this browser last left it showing its
// translation under, and `key`, the one the settings hold now: `key` when
// both name one model (`sameModel`). With no chat model on offer
// (`noChatModel`: the free service, or nothing, stands in for one), a paper
// left under a chat model's key in the same language keeps that key, so it
// opens from what is kept under it. Else null: it opens in the original.
export function openKey(remembered, key, { noChatModel = false } = {}) {
  if (!remembered) return null;
  if (sameModel(remembered, key)) return key;
  const [lang, model] = splitKey(remembered);
  return noChatModel && model && !model.startsWith("engine:") && lang === splitKey(key || "")[0] ? remembered : null;
}

// The language a key names, a code of TRANSLATE_LANGS, which is a BCP-47 tag.
export const langOfKey = (key) => splitKey(key || "")[0];
// The model a key names: what its requests send.
export const modelOfKey = (key) => splitKey(key || "")[1];

export function createRecall(storage, name, max = RECALL_MAX) {
  // [[file, key], …], the paper used last at the end.
  const read = () => {
    try {
      const list = JSON.parse(storage?.getItem(name) || "[]");
      return Array.isArray(list) ? list.filter((x) => Array.isArray(x) && typeof x[0] === "string" && typeof x[1] === "string") : [];
    } catch {
      return [];
    }
  };
  const write = (list) => {
    try { storage?.setItem(name, JSON.stringify(list)); } catch {}
  };
  return {
    // The key the paper was last left showing its translation under, else null.
    keyOf(file) {
      if (!file) return null;
      return read().find(([f]) => f === file)?.[1] ?? null;
    },
    // The paper shows its translation under `key`: it goes last, the oldest
    // past `max` going out.
    remember(file, key) {
      if (!file || !key) return;
      const list = read();
      const last = list[list.length - 1];
      if (last && last[0] === file && last[1] === key) return;
      const next = list.filter(([f]) => f !== file);
      next.push([file, key]);
      write(next.slice(Math.max(0, next.length - max)));
    },
    // The paper was left in the original (Show original, Forget).
    forget(file) {
      const list = read();
      const next = list.filter(([f]) => f !== file);
      if (next.length !== list.length) write(next);
    },
  };
}
