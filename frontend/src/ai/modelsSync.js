// The order of useAiModels' requests, outside React so node tests it
// (tests/aiModels.test.mjs): which answers of GET /api/ai/models and of the
// writes (PUT /api/ai/models/roles, the import) land, and which are dropped.
//
// - A read that started before this tab's last write answers with the
//   roles as they were: it is dropped, and read again once the writes
//   settle. A write answered after a later one is dropped too.
// - Another account (or signing out) aborts what is in flight, and nothing
//   of the last account's answers lands.
// - The first read of an account moves this browser's old picks into it
//   once (aiModels.js importLegacyPicks).
//
// `request(path, init)` sends (apiJson's shape, `init.signal` included);
// `setInfo(next)` and `setFailed(bool)` are the hook's state setters
// (`next` a value or an updater); `onError(message)` hears a write the
// server refused; `canWrite()` is false for a share link's visitor;
// `storage()` is localStorage, or null where it can't be read.
import { importLegacyPicks } from "./aiModels.js";

const JSON_HEADERS = { "Content-Type": "application/json" };

// The roles a write answered ({roles, favorites, chat_migrated}, and
// `missing` when it names them) over the list read before.
export function withRoles(info, answer) {
  if (!info || !answer?.roles) return info;
  return { ...info, roles: answer.roles, favorites: answer.favorites || [], chat_migrated: !!answer.chat_migrated,
    ...(answer.missing ? { missing: answer.missing } : {}) };
}

export function createModelsSync({ request, setInfo, setFailed, onError, canWrite, storage }) {
  // The account the answers are for, its epoch (one per account), the
  // writes sent, a read dropped for a write, the requests in flight, the
  // account whose old picks were moved.
  const s = { user: undefined, epoch: 0, writes: 0, dropped: false, controllers: new Set(), imported: null };

  const send = (path, init = {}) => {
    const ctl = new AbortController();
    s.controllers.add(ctl);
    return request(path, { ...init, signal: ctl.signal }).finally(() => s.controllers.delete(ctl));
  };
  const post = (path, method, body) => send(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });

  function switchTo(next) {
    if (s.user === next) return;
    s.user = next;
    s.epoch += 1;
    s.dropped = false;
    for (const ctl of s.controllers) ctl.abort();
    s.controllers.clear();
    setInfo(null);
    setFailed(false);
  }

  // The answer of write `seq` of `epoch`: while no later write was sent,
  // its roles land and the read it dropped goes again.
  const landed = (seq, epoch) => (answer) => {
    if (epoch !== s.epoch || seq !== s.writes) return answer;
    setInfo((info) => withRoles(info, answer));
    if (s.dropped) { s.dropped = false; refresh(); }
    return answer;
  };

  // A write (`body` to `path`), shown at once through `optimistic(info)`;
  // the server's answer replaces it. A refused write says why and reads the
  // roles again. Resolves to the answer, or null.
  function write(path, method, body, optimistic = null) {
    if (!canWrite() || !s.user) return Promise.resolve(null);
    const seq = ++s.writes, epoch = s.epoch;
    if (optimistic) setInfo((info) => (info ? optimistic(info) : info));
    return post(path, method, body).then(landed(seq, epoch), (err) => {
      if (epoch !== s.epoch || err?.name === "AbortError") return null;
      onError?.(err?.message || String(err));
      if (seq === s.writes) { s.dropped = false; refresh(); }
      return null;
    });
  }

  // The one-time move of this browser's old picks, after the account's
  // first read. A write like the others: a read it overtakes is dropped.
  function importOnce() {
    if (!canWrite() || !s.user || s.imported === s.user) return;
    s.imported = s.user;
    const store = storage();
    if (!store) return;
    importLegacyPicks(store, (body) => {
      const seq = ++s.writes, epoch = s.epoch;
      return post("/ai/models/import", "POST", body).then(landed(seq, epoch));
    }).catch(() => {});
  }

  // Read the list (for `forUser`, when the caller knows the account before
  // the hook does: the session's first reads).
  function refresh(forUser) {
    if (typeof forUser === "string") switchTo(forUser);
    const epoch = s.epoch, writes = s.writes;
    return send("/ai/models").then((info) => {
      if (epoch !== s.epoch) return;
      if (writes !== s.writes) { s.dropped = true; return; }
      setInfo(info);
      setFailed(false);
      importOnce();
    }, (err) => {
      if (epoch === s.epoch && err?.name !== "AbortError") setFailed(true);
    });
  }

  return { switchTo, refresh, write, user: () => s.user };
}
