// The page's live session as a plain state machine — no React, no globals:
// the hook in collaboration/usePageCollab.js wires it to the app (fetch, WebSocket, React state)
// and the node tests drive it with fakes. One session per tab; it
//   - turns the block tree's transitions into ops (blockOps.diffTrees) and
//     sends them in debounced batches to POST /api/pages/{id}/ops — the
//     HTTP response is the ack (positions the server re-keyed come back
//     in it); HTTP saves continue when the socket drops, and a closing tab
//     attempts a keepalive flush (queued edits are not durable offline).
//     A batch holds at most MAX_OPS ops and keeps its id until the server
//     answers it, so a retry of a batch that did land is answered, not
//     applied twice; failed sends are retried, ever longer apart, never
//     given up, and a notice (onSaveNotice) stays up while edits wait;
//   - holds the page's websocket (/api/ws/page/{id}): incoming `ops`
//     batches from other clients are applied to the tree through
//     onRemoteOps, `reload` refetches, presence (`join` / `leave` /
//     `cursor`) lands in `peers`, and our own cursor goes out throttled —
//     except while we have unflushed edits: then the caret rides on the
//     batch itself (`cursor` on the POST, echoed on the fan-out), so the
//     others place it against the text it belongs to instead of an older
//     copy (offsets past the typed text would land it a few characters
//     off, and stay off once the batch mapped it further);
//   - reconciles concurrent edits of one block: a remote content `set`
//     for a block we have an unacknowledged `set` in flight for is deferred,
//     then applied only if the server ordered it AFTER ours (its seq is
//     higher than our ack's) — otherwise ours is the newer value and it is
//     dropped; structure and properties the other way round: remote
//     batches applied here after our edits but ordered before them on the
//     server get our batch put back on top when it comes by (`reassert`);
//   - catches up after a reconnect from the op log (GET …/ops?since=), or
//     asks for a reload when the log no longer reaches back;
//   - heals a batch the server refused for naming a block it doesn't have
//     (404 with `missing`) while the base holds that block: the block goes
//     out again ahead of the batch, where this tab has it, rather than the
//     whole queue being dropped and the page reloaded without our edits;
//   - survives a batch the page changed under (the server's `conflict`: a
//     block gone, moved to another page, a move making a cycle): the op it
//     names and every op depending on that block are dropped, the rest goes
//     out again and the page is refetched; every fetched tree gets this
//     tab's unsaved edits laid over it (`overlay`), so a reload never shows
//     — or bases a diff on — a page without them;
//   - holds a page's edits while the page is in Recently deleted (the
//     server's 404 for it, or a `trashed` message): nothing is dropped or
//     sent until the page loads again (restored).
//
// The "base" tree is what the server is known to hold from this tab's point
// of view once the queue has landed: every commit diffs against it and
// advances it; remote ops advance it too. Positions live in one Map shared
// with blockOps.
import { applyOps, diffTrees, indexTree, propsPatch, pushOp, seedPositions } from "../shared/model/blockOps.js";
import { t } from "../shared/i18n/i18n.js";

export const TYPING_DEBOUNCE_MS = 350;
export const STRUCTURAL_DEBOUNCE_MS = 80;
export const CURSOR_THROTTLE_MS = 80;
export const RETRY_MS = 3000;      // the first retry of a failed save; doubles each time …
export const MAX_RETRY_MS = 60000; // … up to this
export const MAX_RESCUES = 20; // blocks re-sent between two saves that go through, before a refusal is a conflict
export const MAX_OPS = 500;        // ops per batch, and …
export const MAX_CONTENT = 200_000; // … characters per block: the server's limits (gamma/ops.py)
const SOCKET_OPEN = 1; // WebSocket.OPEN
const CLOSE_REVOKED = 4403; // the server closed the socket: no access to the page any more

function pageSession(pageId = "") {
  return {
    pageId, base: [], pos: new Map(), queue: [], timer: null, sending: null,
    out: null, // the batch out ({id, ops, cursor}), kept until the server answers it
    inflight: new Map(), deferred: new Map(), retries: 0, rescues: 0, seq: 0,
    remote: 0, // remote batches applied so far; `queueMark`: the count when the queue last started
    queueMark: 0,
    pending: new Map(), catchingUp: null, reloading: false,
    problem: "", // why edits of this page wait (a failed save), "" when none do
    tooLong: new Map(), // id → {text, base}: text over MAX_CONTENT held back; the base keeps `base`
    gone: null, // {trashed}: the page is in Recently deleted (or deleted) — its edits wait, unsent
  };
}

// `tree` with the text of the blocks in `texts` (id → text) replaced.
function withTexts(tree, texts) {
  return (tree || []).map((n) => {
    const kids = n.children?.length ? withTexts(n.children, texts) : n.children;
    if (texts.has(n.id)) return { ...n, content: texts.get(n.id), children: kids };
    return kids === n.children ? n : { ...n, children: kids };
  });
}

// The base of a tree on screen: text held back as too long is replaced by
// the last text that can be saved.
const heldBack = (s, tree) => (s.tooLong.size
  ? withTexts(tree, new Map([...s.tooLong].map(([id, h]) => [id, h.base]))) : tree);

const sameCursor = (a, b) => !!a && !!b && a.block === b.block && a.anchor === b.anchor && a.head === b.head;

// A failed request worth another try (err.status, 0 when there was no
// answer): offline, a server error, a timeout, rate limiting — not a
// refusal. App's refetch of the page follows the same rule.
export const retryableStatus = (status) => !(status >= 400 && status < 500) || status === 408 || status === 429;

// The status of a failed call: utils.apiJson throws a 401 as a bare
// "401 Unauthorized" Error without one.
const statusOf = (err) => err?.status || (/^401\b/.test(err?.message || "") ? 401 : 0);

// Why edits wait, in words (never the browser's "Failed to fetch").
function saveProblem(err) {
  const status = statusOf(err);
  if (!status) return t("Not saved yet — the server can't be reached. Retrying…");
  if (status === 401) return t("Not saved yet — you're signed out. Sign in again and your edits are saved.");
  if (status === 409) return t("Not saved yet — this browser is signed in to another account now. Sign in again and your edits are saved.");
  return t("Not saved yet — the server answered {status}. Retrying…", { status });
}

const someNode = (node, test) => test(node) || (node.children || []).some((c) => someNode(c, test));

// Split `ops` into those that no longer apply now that the blocks in
// `gone` vanished (their own ops, and inserts under them — whose ids join
// `gone`, so ops on those go too — and moves into them) and the rest.
function dependents(ops, gone) {
  const kept = [], dropped = [];
  for (const op of ops) {
    const under = (op.op === "insert" || op.op === "move") && gone.has(op.parent);
    if (gone.has(op.id) || under) {
      if (op.op === "insert") gone.add(op.id);
      dropped.push(op);
    } else kept.push(op);
  }
  return { kept, dropped };
}

// deps:
//   clientId              — this tab's id (goes on every batch and the socket URL)
//   api(path, init)       — JSON call under /api; rejects with err.status on
//                           an HTTP error (utils.apiJson)
//   openSocket(pageId)    — a WebSocket-like object for the page's room
//                           (onopen/onmessage/onclose/onerror, send, close,
//                           readyState)
//   keepalivePost(path, body) — fire-and-forget POST while the tab closes
//   opts()                — the live options, read on every use so callers
//                           may pass fresh closures:
//       pageId                        the open page (root block id); "" → idle
//       canWrite                      false in a read-only share view (presence only)
//       onRemoteOps(ops, pageId, pos) apply a batch from another client to the
//                                     tree (pos: the shared id → position map)
//       onReload(pageId)              refetch the tree (a change ops can't express)
//       onStatus(text)                the status line
//       onSaveNotice(text, kind)      a notice that outlasts the status line:
//                                     "pending" — edits wait (a failed save being
//                                     retried, text too long), up until text is ""
//                                     again; "rejected" — a batch was refused for
//                                     good and dropped, or edits of a block gone
//       onGone(pageId, gone)          the open page went to Recently deleted
//                                     ({trashed}: its trash entry, when known) —
//                                     its edits wait — or is back (null)
//     onRemoteOps also gets the base the ops were applied to (`before`, what
//     the server held before them), for the undo history's text rebase.
//   onPeers(peers), onMe(me) — presence changes (the hook's React state)
//   onQueued()                — a local edit was queued (the clone's sync pill shows it as pending)
//   timers                — {set(fn, ms) → id, clear(id)}; default the globals
export function createCollabSession({ clientId, api, openSocket, keepalivePost, opts, onPeers, onMe, onQueued, timers }) {
  const later = timers?.set || ((fn, ms) => globalThis.setTimeout(fn, ms));
  const cancel = timers?.clear || ((id) => globalThis.clearTimeout(id));
  const o = () => opts();
  // Batch ids: this tab's own (the server keys them by client id too).
  const batchPrefix = Math.random().toString(36).slice(2, 8);
  let batchCount = 0;
  const nextBatchId = () => `${batchPrefix}${(++batchCount).toString(36)}`;

  const st = {
    session: pageSession(),   // queued writes retain their page after navigation
    sessions: new Set(),      // pages with queued or unacknowledged writes
    ws: null,
    wsPage: "",
    backoff: 1000,
    closed: true,
    reconnectTimer: null,
  };
  let peers = [];       // other clients on the page
  let me = { client: clientId, color: 0, connected: false };
  const setPeers = (next) => {
    peers = typeof next === "function" ? next(peers) : next;
    onPeers?.(peers);
  };
  const setMe = (next) => {
    const v = typeof next === "function" ? next(me) : next;
    if (v === me) return;
    me = v;
    onMe?.(me);
  };

  // --- presence out: state ---------------------------------------------------

  // latest: the caret as last reported by the editor (what a batch carries);
  // last: the caret the others were last told about (standalone or on a
  // batch); pending: a standalone send waiting for the throttle / a flush.
  const cursor = { latest: null, last: null, pending: null, timer: null };

  // The throttled standalone send. While a batch is queued or in flight the
  // caret waits: the batch carries it, and a standalone message could
  // overtake the batch and be read against text the others don't have yet.
  function armCursor() {
    const c = cursor;
    if (c.timer || !c.pending) return;
    c.timer = later(() => {
      c.timer = null;
      const p = c.pending;
      if (!p) return;
      if (st.session.queue.length || st.session.sending || st.session.out) return; // re-armed once the batch is acked
      c.pending = null;
      if (sameCursor(p, c.last)) return;
      if (!st.ws || st.ws.readyState !== SOCKET_OPEN) return;
      c.last = p;
      try { st.ws.send(JSON.stringify({ t: "cursor", ...p })); } catch {}
    }, CURSOR_THROTTLE_MS);
  }

  // A peer's caret that came with its batch: the positions are in the text
  // the batch produced, so the editor places them fresh (the `rev` bump)
  // rather than mapping an older caret through the change.
  function placePeer(client, cur) {
    if (!client || !cur) return;
    setPeers((prev) => prev.map((p) => (p.client === client
      ? { ...p, block: cur.block || "", anchor: cur.anchor ?? -1, head: cur.head ?? -1, rev: (p.rev || 0) + 1 }
      : p)));
  }

  // --- save notices ---------------------------------------------------------

  // The "pending" notice: text held back as too long on the open page, else
  // why some page's edits wait (not the open page's own Recently deleted:
  // App shows that on the page, with Restore). Reported only when it changes.
  let shown = "";
  function notice() {
    const long = st.session.tooLong.size
      ? t("A note is too long to save (at most {max} characters) — shorten it or split it into several notes.",
        { max: MAX_CONTENT.toLocaleString() })
      : "";
    const text = long || [st.session, ...st.sessions]
      .map((s) => (s.gone && s === st.session ? "" : s.problem)).find(Boolean) || "";
    if (text === shown) return;
    shown = text;
    o().onSaveNotice?.(text, "pending");
  }
  function setProblem(s, text) {
    if (s.problem === text) return;
    s.problem = text;
    notice();
  }

  // --- outgoing ops ----------------------------------------------------------

  // Changes the server made that this tab's base and screen don't show yet
  // (a merge, remote text held back until our ack, an insert that found its
  // block already there): the base takes them, and the screen when this is
  // the open page.
  function land(s, ops, own = false) {
    if (!ops.length || s !== st.session) return;
    const before = s.base;
    s.base = applyOps(s.base, ops, s.pageId, s.pos);
    o().onRemoteOps?.(ops, s.pageId, s.pos, before, own);
  }

  // Our batch was acked (`applied`: its ops as the server applied them)
  // after this tab applied remote batches made since the batch's edits
  // were: here theirs came last, on the server ours did (their seqs are
  // lower). Where both touched the same thing, ours goes back on top — a
  // block moved by both, one theirs deleted and ours re-created (a rescue:
  // with the text it has here), a property both set — unless a newer edit
  // of ours to that block is still queued. Structure is replayed in the
  // batch's order; text is not (the in-flight bookkeeping decides it).
  // `own`: no undo-history rebase.
  function reassert(s, applied) {
    const newer = new Map(); // id → our ops queued since
    for (const op of s.queue) newer.set(op.id, [...(newer.get(op.id) || []), op]);
    const shaped = (id) => (newer.get(id) || []).some((op) => op.op !== "set");
    const here = indexTree(s.base, s.pageId);
    // A block the batch itself deletes, with a block it holds then (a note
    // made and removed within one batch), stays gone.
    const deleted = new Set(applied.filter((op) => op.op === "delete").map((op) => op.id));
    const parentOf = new Map(applied.filter((op) => op.op === "insert" || op.op === "move").map((op) => [op.id, op.parent]));
    const doomed = (id) => {
      for (let at = id, n = 0; at && n < 10000; n++) {
        if (deleted.has(at)) return true;
        at = parentOf.get(at) ?? here.get(at)?.parent;
      }
      return false;
    };
    const again = [], revived = new Set();
    for (const op of applied) {
      if ((op.op === "move" || op.op === "delete") && !shaped(op.id)) again.push(op);
      else if (op.op === "insert" && !here.has(op.id) && !shaped(op.id) && !doomed(op.id)) {
        again.push(op);
        revived.add(op.id);
      } else if (op.op === "set" && revived.has(op.id)) again.push(op);
      else if (op.op === "set" && op.props) {
        const later = (newer.get(op.id) || []).filter((q) => q.op === "set" && q.props);
        const keys = Object.keys(op.props).filter((k) => !later.some((q) => k in q.props));
        if (keys.length) again.push({ op: "set", id: op.id, props: Object.fromEntries(keys.map((k) => [k, op.props[k]])) });
      }
    }
    for (const id of revived) for (const op of newer.get(id) || []) again.push(op); // its text typed since
    land(s, again, true);
  }

  // One content set of ours for block `id` is settled (acked or dropped).
  // false while more of ours for the block are still queued or out; else
  // the block is no longer protected and the remote set held back for it
  // (or null) is handed over.
  function settle(s, id) {
    const n = (s.inflight.get(id) || 1) - 1;
    if (n > 0) { s.inflight.set(id, n); return false; }
    s.inflight.delete(id);
    const d = s.deferred.get(id) || null;
    s.deferred.delete(id);
    return d;
  }

  // Content sets of ours that will never land (dropped): remote text held
  // back for their blocks applies now.
  function forget(s, ops) {
    const late = [];
    for (const op of ops) {
      if (op.op !== "set" || op.content === undefined) continue;
      const d = settle(s, op.id);
      if (d) late.push(d.op);
    }
    land(s, late);
  }

  // Ops re-creating block `id` and its subtree as the base holds them, or
  // null when the base doesn't have it or the budget is spent. Re-inserting
  // an id the server does have leaves that block as it is there
  // (create-if-absent), so descendants that did reach it converge instead
  // of failing; a move then puts the block where this tab has it — the
  // server may hold it under a block this very batch deletes (someone moved
  // it there meanwhile), and it must leave before that delete takes it along.
  function rescue(s, id) {
    if (!id || s.rescues >= MAX_RESCUES) return null;
    const hit = indexTree(s.base, s.pageId).get(id);
    if (!hit) return null;
    const out = [];
    const walk = (node, parent) => {
      const position = s.pos.get(node.id);
      out.push({ op: "insert", id: node.id, parent, ...(position ? { position } : {}),
        content: node.content || "", props: node.properties || {} });
      if (node === hit.node) out.push({ op: "move", id: node.id, parent, ...(position ? { position } : {}) });
      for (const c of node.children || []) walk(c, node.id);
    };
    walk(hit.node, hit.parent);
    return out;
  }

  // Park the page's edits: it is in Recently deleted (`trashed`: its entry,
  // when the server said) or gone. Nothing is dropped and nothing sent — the
  // batch out keeps its id, typing still queues — until the page loads
  // again (restored: `commit` of a load resumes).
  function park(s, trashed = null) {
    if (s.timer) { cancel(s.timer); s.timer = null; }
    const news = !s.gone || (!!trashed && !s.gone.trashed);
    if (news) s.gone = { trashed: trashed || s.gone?.trashed || null };
    setProblem(s, s.out || s.queue.length
      ? t("Edits to a page in Recently deleted are kept here and saved if it is restored.") : "");
    if (news && s === st.session) o().onGone?.(s.pageId, s.gone);
  }
  function unpark(s) {
    if (!s.gone) return;
    s.gone = null;
    s.retries = 0;
    setProblem(s, "");
    if (s === st.session) o().onGone?.(s.pageId, null);
    if ((s.out || s.queue.length) && !s.sending) {
      if (s.timer) cancel(s.timer);
      s.timer = later(() => { s.timer = null; send(s); }, 0);
    }
  }

  // Send the page's batch: the one out again (a retry, or what is left of a
  // refused one), else the next MAX_OPS ops of the queue, in order — the
  // rest follows the ack. The batch keeps its id until the server answers
  // it: a retry of one whose answer was lost is answered, not re-applied.
  function send(s = st.session) {
    if (s.sending) return s.sending;
    if (s.gone) return false; // parked until the page is back
    if (!s.out) {
      if (!s.queue.length) return null;
      // Our caret in the text this batch produces, for the page it belongs to.
      const cur = s === st.session && cursor.latest ? cursor.latest : null;
      s.out = { id: nextBatchId(), ops: s.queue.splice(0, MAX_OPS), cursor: cur, mark: s.queueMark };
    }
    const page = s.pageId;
    const batch = s.out;
    const { ops, cursor: cur } = batch;
    if (s.timer) { cancel(s.timer); s.timer = null; }
    const c = cursor;
    const p = (async () => {
      let saved = false;
      try {
        const res = await api(`/pages/${encodeURIComponent(page)}/ops`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client: clientId, batch: batch.id, ops, ...(cur ? { cursor: cur } : {}) }),
        });
        s.out = null;
        s.retries = 0;
        s.rescues = 0;
        setProblem(s, "");
        // What the others were told: the caret as the server stored it (it
        // is remapped when a merge moved the text under it).
        if (cur && s === st.session) c.last = res.cursor && res.cursor.block ? res.cursor : cur;
        // The ack: final positions, the text the server actually stored
        // for each content set (a merge onto someone else's change comes
        // back different from what we sent), and the deferred remote sets
        // decided.
        const stored = new Map(), found = new Map();
        for (const op of res.ops || []) {
          if ((op.op === "insert" || op.op === "move") && op.position) s.pos.set(op.id, op.position);
          if (op.op === "set" && op.content !== undefined) stored.set(op.id, op.content);
          if (op.op === "insert") found.set(op.id, op);
        }
        const ackSeq = res.seq || 0;
        // An ack proves only that this batch committed; earlier remote
        // batches may still be missing. Use the same ordered inbox as WS.
        if (s === st.session) await receive({ ...res, client: clientId, mark: batch.mark });
        const late = [], lateCursors = [];
        const moved = new Set(ops.filter((op) => op.op === "move").map((op) => op.id));
        for (const op of ops) {
          if (op.op === "insert") {
            // An insert the server already had leaves the block as it is
            // there (a retry, a rescue): this tab converges on it — unless
            // newer text of ours for it is on its way, or the batch moved
            // it on (a rescue: the echo is where it was before that).
            const got = found.get(op.id);
            if (got && !s.inflight.has(op.id) && !moved.has(op.id) && (got.parent !== op.parent || (got.content ?? "") !== (op.content ?? "")
                || Object.keys(propsPatch(op.props, got.props)).length)) late.push(got);
            continue;
          }
          if (op.op !== "set" || op.content === undefined) continue;
          const d = settle(s, op.id);
          if (d === false) continue;
          if (d && d.seq > ackSeq) { // theirs is newer: it wins
            late.push(d.op);
            if (d.cursor) lateCursors.push(d);
          } else if (stored.has(op.id) && stored.get(op.id) !== op.content) {
            // The server merged our change onto text someone else wrote
            // since our base: their spans and ours both survive. Lands
            // like a remote op — only now that no newer set of ours for the
            // block is queued or in flight (that one's base is the text we
            // sent, so the server patches it onto the merge and its ack
            // brings the whole result).
            late.push({ op: "set", id: op.id, content: stored.get(op.id) });
          }
        }
        land(s, late);
        if (s === st.session) for (const d of lateCursors) placePeer(d.client, d.cursor);
        saved = true;
      } catch (err) {
        saved = refused(s, batch, err);
      } finally {
        s.sending = null;
        if (!s.queue.length && !s.out) st.sessions.delete(s);
      }
      if ((s.out || s.queue.length) && !s.timer) send(s);
      else armCursor(); // a caret move held back while the batch was out
      return saved;
    })();
    s.sending = p;
    return p;
  }

  // A batch the server did not take. Returns what send() reports for it:
  // null when (what is left of) it goes out again at once, false when it
  // waits for a retry.
  function refused(s, batch, err) {
    const status = statusOf(err);
    const data = err?.data || {};
    const page = s.pageId;
    if (status === 404 && !data.missing && !data.conflict) {
      // The page itself is gone — in Recently deleted, most likely: the
      // edits wait for it to come back.
      park(s, data.trashed || null);
      return false;
    }
    // A block this batch already re-sent is missing again: the batch itself
    // removes it on the server (deletes what holds it there) — no rescue
    // can help, it is a conflict like any other.
    const lost = status === 404 && !batch.rescued?.has(data.missing) ? rescue(s, data.missing) : null;
    if (lost) {
      // The server never got this block (its insert was lost), lost it
      // since, or holds it under a block this batch deletes: send it again,
      // where this tab has it, ahead of the refused batch. Nothing of the
      // batch was written, so all of it goes out again (a new batch).
      s.rescues += 1;
      s.out = { ...batch, id: nextBatchId(), ops: [...lost, ...batch.ops],
        rescued: new Set([...(batch.rescued || []), data.missing]) };
      return null;
    }
    const failed = batch.ops[data.index];
    if (data.conflict && failed) {
      // The page changed under the batch (the block is gone, lives in
      // another page, the move would make a cycle): that op no longer
      // applies, nor does any op of the batch or the queue behind it on a
      // vanished block (its subtree) — they all go at once, not one per
      // round trip. The rest goes out again — text edits keep the base they
      // were made from, so the server merges them — and the page is
      // refetched: this screen still shows the refused change.
      let rest = batch.ops.filter((_, i) => i !== data.index), dropped = [failed];
      if (data.conflict !== "cycle") {
        const named = /^(block|parent) (\S+) is outside this page/.exec(err?.message || "");
        const gone = new Set([data.missing || named?.[2] || failed.id]);
        if (failed.op === "insert") gone.add(failed.id);
        const inBatch = dependents(rest, gone);
        const inQueue = dependents(s.queue, gone);
        rest = inBatch.kept;
        s.queue = inQueue.kept;
        dropped = [failed, ...inBatch.dropped, ...inQueue.dropped];
      }
      forget(s, dropped);
      s.out = rest.length ? { ...batch, id: nextBatchId(), ops: rest } : null;
      if (dropped.some((op) => op.op !== "delete" && op.op !== "move")) {
        // Text or a new note that can't be saved any more: never silently.
        const text = t("Someone else deleted or moved a note you were editing — that edit couldn't be saved.");
        o().onStatus?.(text);
        o().onSaveNotice?.(text, "rejected");
      }
      if (s === st.session) o().onReload?.(page);
      return null;
    }
    const signedOut = status === 401 || status === 409; // the X-Gamma-User guard: another account signed in
    if (signedOut || retryableStatus(status)) {
      // Offline, a server error, signed out: the batch waits, id and all,
      // and is tried again — ever longer apart, never given up (the
      // `online` event, the window regaining focus and a socket reconnect
      // try at once). Once the person signs in again it goes out. The
      // notice (the "save" pill, not the status line) goes once it is saved.
      s.retries += 1;
      setProblem(s, saveProblem(err));
      if (s.timer) cancel(s.timer); // a typing debounce armed while it was out
      s.timer = later(() => { s.timer = null; send(s); }, Math.min(RETRY_MS * 2 ** (s.retries - 1), MAX_RETRY_MS));
      return false;
    }
    // Refused for good (malformed, not allowed any more): this batch is
    // dropped and the page refetched to show what the server holds; the
    // rest of the queue still goes out.
    forget(s, batch.ops);
    s.out = null;
    const text = t("Save rejected: {message}", { message: err?.message || "" });
    o().onStatus?.(text);
    o().onSaveNotice?.(text, "rejected");
    if (s === st.session) o().onReload?.(page);
    return null;
  }

  function enqueue(ops, now = false) {
    const s = st.session;
    st.sessions.add(s);
    if (!s.queue.length) s.queueMark = s.remote; // what this tab had applied when these edits were made
    for (const op of ops) {
      // inflight counts the content sets queued or out per block (the ack
      // settles once per op of the batch, so the two must agree): a
      // keystroke folded into a set that already carries text adds
      // nothing; text folded into a queued props-only set makes one more.
      const into = pushOp(s.queue, op);
      if (op.op === "set" && op.content !== undefined && into?.content === undefined) {
        s.inflight.set(op.id, (s.inflight.get(op.id) || 0) + 1);
      }
    }
    onQueued?.();
    // The page is in Recently deleted: the edits wait for it to come back.
    if (s.gone) { park(s); return; }
    // A batch waits for its retry: the queue follows it, and typing never
    // cuts the wait short.
    if (s.out && !s.sending) return;
    const structural = ops.some((op) => op.op !== "set");
    const delay = now ? 0 : structural ? STRUCTURAL_DEBOUNCE_MS : TYPING_DEBOUNCE_MS;
    if (s.timer) cancel(s.timer);
    s.timer = later(() => { s.timer = null; send(s); }, delay);
  }

  // Called by the tree's transition effect. `isLoad`: the transition was a
  // load (or a remote apply) — the tree becomes the new base, nothing is
  // sent; `seq` says which op-log position a fetched tree reflects, so the
  // socket can catch up on what landed between the fetch and its hello.
  // `now`: skip the typing debounce (an editor closed). A fetched tree has
  // this tab's unsaved edits laid over it (`overlay`) before it gets here.
  function commit(tree, { isLoad = false, now = false, seq = null } = {}) {
    let s = st.session;
    const page = o().pageId;
    if (!page) return [];
    if (page !== s.pageId) {
      // A new page: its tree is the base, positions come from the server.
      // A page left with edits still unsaved keeps its session: coming back
      // resumes it — its queue and retries go on and its acks land on this
      // visit's tree.
      send(s);
      const kept = [...st.sessions].find((x) => x.pageId === page);
      s = st.session = kept || pageSession(page);
      if (kept) {
        kept.pending = new Map();
        kept.catchingUp = null;
        kept.reloading = false;
        kept.deferred.clear();
      }
      if (cursor.timer) cancel(cursor.timer);
      cursor.latest = cursor.last = cursor.pending = cursor.timer = null;
      s.seq = seq || 0;
      seedPositions(tree, s.pos);
      s.base = heldBack(s, tree);
      notice();
      unpark(s); // it loaded: back from Recently deleted
      return [];
    }
    if (isLoad) {
      seedPositions(tree, s.pos);
      s.base = heldBack(s, tree);
      unpark(s);
      if (seq != null) {
        s.seq = Math.max(s.seq, seq);
        for (const n of s.pending.keys()) if (n <= s.seq) s.pending.delete(n);
        s.reloading = false;
        receive({ seq: s.seq });
      }
      return [];
    }
    if (!o().canWrite) { s.base = tree; return []; }
    const ops = [];
    // Text over the server's limit is held back: the base keeps the last
    // text that can be saved, so the change is diffed — and sent — again
    // once it is short enough; meanwhile the notice says why.
    const held = new Map();
    for (const op of diffTrees(s.base, tree, page, s.pos)) {
      if (typeof op.content !== "string" || op.content.length <= MAX_CONTENT) { ops.push(op); continue; }
      held.set(op.id, { text: op.content, base: op.op === "insert" ? "" : op.base ?? "" });
      if (op.op === "insert") ops.push({ ...op, content: "" });
      else if (op.props) ops.push({ op: "set", id: op.id, props: op.props });
    }
    const wasLong = s.tooLong.size;
    s.tooLong = held;
    s.base = heldBack(s, tree);
    if (held.size || wasLong) notice();
    if (ops.length) enqueue(ops, now);
    return ops;
  }

  // Whether this block's text is held back as too long to save (the editor
  // stays open on it).
  function tooLong(id) {
    return st.session.tooLong.has(id);
  }

  // A tree fetched from the server with this tab's unsaved edits of that
  // page laid over it — the batch out, then the queue (ops apply
  // idempotently) — and text held back as too long put back. A reload or a
  // return to the page never shows the page without them, and never makes
  // a base that lacks them: they are still sent, not taken as saved.
  function overlay(pageId, tree) {
    const s = st.session.pageId === pageId ? st.session : [...st.sessions].find((x) => x.pageId === pageId);
    if (!s) return tree;
    const ops = [...(s.out?.ops || []), ...s.queue];
    let out = ops.length ? applyOps(tree, ops, pageId, s.pos) : tree;
    if (s.tooLong.size) {
      const index = indexTree(out, pageId);
      const texts = new Map();
      for (const [id, h] of s.tooLong) {
        const hit = index.get(id);
        if (!hit) continue;
        h.base = hit.node.content || ""; // what the server will hold: the base keeps it
        texts.set(id, h.text);
      }
      if (texts.size) out = withTexts(out, texts);
    }
    return out;
  }

  // Drain queued and in-flight batches, stopping on failure so the normal
  // retry policy takes over. hasPending() reports edits still unsaved.
  async function flush() {
    await Promise.all([...st.sessions].map(async (s) => {
      if (s.timer) { cancel(s.timer); s.timer = null; }
      // A batch waiting for its retry goes now; if it fails again the
      // retries start over rather than stretch (a flush is no failed retry).
      if (s.out && !s.sending) s.retries = 0;
      let saved = await (s.sending || send(s));
      // send() may start the next queued batch before the first promise
      // resolves. Wait for that batch too, even though its queue is now empty.
      while (saved !== false && (s.sending || s.queue.length || s.out)) {
        if (s.timer) { cancel(s.timer); s.timer = null; }
        saved = await (s.sending || send(s));
      }
    }));
  }

  function hasPending() {
    return [...st.sessions].some((s) => s.queue.length > 0 || !!s.sending || !!s.out);
  }

  // The network may be back (the browser's `online`, the window regaining
  // focus, the socket's hello): a batch waiting for its next try goes out
  // now, and should that fail the retries start over — short again, not a
  // minute behind the recovery.
  function retryNow() {
    for (const s of st.sessions) {
      if (!s.out || s.sending || s.gone) continue;
      if (s.timer) { cancel(s.timer); s.timer = null; }
      s.retries = 0;
      send(s);
    }
  }

  // App's refetch of the page answered that it is in Recently deleted
  // (`trashed`: its entry): its edits wait, like after a refused save.
  function gone(pageId, trashed = null) {
    const s = st.session.pageId === pageId ? st.session : [...st.sessions].find((x) => x.pageId === pageId);
    if (s) park(s, trashed);
  }

  // Tab closing / reloading: a keepalive POST of what is still unsaved — the
  // batch out under its own id (the server answers a copy that already
  // landed instead of applying it twice), then the queue. (Not for a page
  // in Recently deleted: the server refuses it.)
  function pagehide() {
    for (const s of st.sessions) {
      if (s.gone) continue;
      const batches = s.out ? [s.out] : [];
      for (let i = 0; i < s.queue.length; i += MAX_OPS) {
        batches.push({ id: nextBatchId(), ops: s.queue.slice(i, i + MAX_OPS) });
      }
      const page = s.pageId;
      s.queue = [];
      for (const b of batches) {
        const body = JSON.stringify({ client: clientId, batch: b.id, ops: b.ops });
        try { keepalivePost(`/pages/${encodeURIComponent(page)}/ops`, body); } catch {}
      }
    }
  }

  // App could not refetch the page (it keeps the tree on screen and tries
  // again): stop holding the socket's batches back for that load.
  function reloadFailed(pageId) {
    const s = st.session;
    if (s.pageId !== pageId || !s.reloading) return;
    s.reloading = false;
    receive({ seq: s.seq });
  }

  // --- incoming ---------------------------------------------------------------

  function applyRemoteBatch(msg) {
    const s = st.session;
    const page = s.pageId;
    if (msg.seq > s.seq) {
      // (a copy from the socket or the log keeps the mark our ack put on it)
      const had = s.pending.get(msg.seq);
      s.pending.set(msg.seq, had?.mark != null && msg.mark == null ? { ...msg, mark: had.mark } : msg);
    }
    while (!s.reloading && s.pending.has(s.seq + 1)) {
      const m = s.pending.get(++s.seq);
      s.pending.delete(s.seq);
      if (m.client === clientId) {
        // Ours, in its place in the order (its ack, or its fan-out while it
        // is still out): remote batches applied here since its edits were
        // made were ordered before it — ours goes back on top of them.
        const mark = m.mark ?? s.out?.mark;
        if (mark != null && s.remote > mark) reassert(s, m.ops || []);
        continue;
      }
      const now = [];
      let held = false;
      for (const op of m.ops || []) {
        if (op.op === "reload") { s.reloading = true; o().onReload?.(page); return; }
        if (op.op === "set" && op.content !== undefined && s.inflight.has(op.id)) {
          // Defer only competing content. Every property patch still applies.
          s.deferred.set(op.id, { op: { op: "set", id: op.id, content: op.content },
            seq: m.seq, client: m.client, cursor: m.cursor || null });
          if (op.props) now.push({ op: "set", id: op.id, props: op.props });
          held = true;
          continue;
        }
        now.push(op);
      }
      if (now.length) {
        s.remote += 1;
        const before = s.base;
        s.base = applyOps(s.base, now, page, s.pos);
        o().onRemoteOps?.(now, page, s.pos, before);
        // A move this tab can't place was ordered before an unsent delete
        // of ours that took its block, or where it goes, along here but not
        // yet on the server. A note moved out of a block we deleted
        // survives there: the page is refetched, our unsent edits laid over
        // it. One moved into it goes with it there: it goes here too —
        // unless we have edits of it on their way (the rescue keeps it).
        const moves = now.filter((op) => op.op === "move");
        if (moves.length) {
          const known = indexTree(before, page);
          if (moves.some((op) => !known.has(op.id))) o().onReload?.(page);
          const mine = new Set([...(s.out?.ops || []), ...s.queue].map((op) => op.id));
          const doomed = moves.filter((op) => known.has(op.id) && op.parent !== page && !known.has(op.parent)
            && !someNode(known.get(op.id).node, (n) => mine.has(n.id)));
          if (doomed.length) land(s, doomed.map((op) => ({ op: "delete", id: op.id })));
        }
      }
      // Place the caret against the text from this batch; held content's
      // caret waits with it until our write is acknowledged.
      if (m.cursor && !held) placePeer(m.client, m.cursor);
    }
  }

  // After a reconnect: what did we miss?
  function catchUp() {
    const s = st.session;
    const page = s.pageId;
    if (!page) return;
    if (s.catchingUp) return s.catchingUp;
    s.catchingUp = (async () => {
      try {
        const d = await api(`/pages/${encodeURIComponent(page)}/ops?since=${s.seq}`);
        if (st.session !== s) return;
        for (const b of d.batches || []) applyRemoteBatch(b);
        // A newer socket batch may have arrived while this snapshot of the
        // log was being fetched. Recover that gap too.
        if (s.pending.size && !s.reloading) queueMicrotask(() => catchUp());
      } catch (err) {
        if (st.session === s) o().onReload?.(page);
      } finally {
        s.catchingUp = null;
      }
    })();
    return s.catchingUp;
  }

  // The one ordered inbox for HTTP acks and socket batches.
  function receive(msg) {
    applyRemoteBatch(msg);
    const s = st.session;
    if (s.pending.size && !s.reloading) return catchUp();
  }

  // --- the socket -------------------------------------------------------------

  // Open the page's room (closing any other page's first); reconnects with
  // backoff until disconnect().
  function connect(pageId) {
    disconnect();
    st.closed = false;
    const open = () => {
      if (st.closed) return;
      let ws;
      try { ws = openSocket(pageId); } catch { return; }
      st.ws = ws;
      st.wsPage = pageId;
      ws.onopen = () => { st.backoff = 1000; };
      ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        const session = st.session;
        if (session.pageId !== pageId && msg.t !== "hello") return;
        switch (msg.t) {
          case "hello": {
            setMe({ client: msg.client, color: msg.color, connected: true });
            setPeers((msg.peers || []).filter((p) => p.client !== msg.client));
            // The tree was fetched before the socket opened (its seq came
            // with it); anything that landed in between is in the log.
            if (session.pageId === pageId && (msg.seq || 0) > session.seq) catchUp();
            // A (re)join starts with empty presence on the server: tell
            // the room where we are.
            cursor.last = null;
            if (cursor.latest) { cursor.pending = cursor.latest; armCursor(); }
            // Connected again: a save waiting for its retry goes now.
            retryNow();
            break;
          }
          case "join":
            setPeers((prev) => [...prev.filter((p) => p.client !== msg.peer.client), msg.peer]);
            break;
          case "leave":
            setPeers((prev) => prev.filter((p) => p.client !== msg.client));
            break;
          case "cursor":
            setPeers((prev) => prev.map((p) => (p.client === msg.client
              ? { ...p, block: msg.block, anchor: msg.anchor, head: msg.head, rev: (p.rev || 0) + 1 } : p)));
            break;
          case "ops":
            receive(msg);
            break;
          case "reload":
            if (msg.seq) receive({ ...msg, ops: [{ op: "reload" }] });
            else { session.reloading = true; o().onReload?.(pageId); }
            break;
          case "trashed":
            // The page went to Recently deleted: what is typed here waits
            // for it to be restored (its `reload` then refetches).
            park(session, msg.trashed || null);
            break;
          default:
        }
      };
      ws.onclose = (ev) => {
        if (st.ws === ws) st.ws = null;
        setMe((m) => (m.connected ? { ...m, connected: false } : m));
        setPeers([]);
        if (st.closed) return;
        if (ev?.code === CLOSE_REVOKED) {
          // Access to the page was taken away (a share stopped, a member
          // removed): nothing to reconnect to.
          st.closed = true;
          o().onStatus?.(t("You no longer have access to this page."));
          return;
        }
        st.reconnectTimer = later(open, st.backoff);
        st.backoff = Math.min(st.backoff * 2, 15000);
      };
      ws.onerror = () => {};
    };
    open();
  }

  // Close the socket and stop reconnecting. `clearPresence`: the page went
  // away (home library, read-only nothing) — the peer list empties now
  // rather than at the next hello.
  function disconnect({ clearPresence = false } = {}) {
    st.closed = true;
    if (st.reconnectTimer) { cancel(st.reconnectTimer); st.reconnectTimer = null; }
    if (st.ws) { st.ws.onclose = null; st.ws.close(); st.ws = null; st.wsPage = ""; }
    if (clearPresence) {
      setPeers([]);
      setMe((m) => (m.connected ? { ...m, connected: false } : m));
    }
  }

  // --- presence out -----------------------------------------------------------

  function sendCursor(cur) {
    const c = cursor;
    const next = { block: cur?.block || "", anchor: cur?.anchor ?? -1, head: cur?.head ?? -1 };
    c.latest = next;
    if (sameCursor(next, c.last) && !c.pending) return;
    c.pending = next;
    armCursor();
  }

  return {
    commit, flush, hasPending, sendCursor, pagehide, connect, disconnect,
    overlay, retryNow, reloadFailed, tooLong, gone,
    get peers() { return peers; },
    get me() { return me; },
  };
}
