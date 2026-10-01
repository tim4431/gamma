// One sync round of a replica — a copy of one workspace of a Gamma server
// kept on a device with no server of its own (the iPad app,
// docs/dev/ipad.md). The same mirror protocol and rules as the desktop's
// local-server copy (gamma/sync_engine.py, docs/dev/mirror.md): the
// remote's change feed and whoami, a saved base per page and a three-way
// merge from it (replica/reconcile.js), only this copy's own edits pushed
// as op batches under batch ids kept until their answer is read, files by
// content hash, tombstones for deleted pages. The remote needs nothing it
// does not already give a mirror.
//
// The device's storage, network and files are the HOST's, an object the
// app passes in (the iPad's is Swift, ipad/GammaIPad/ReplicaHost.swift;
// the tests' is in memory). Everything is async:
//
//   config                      {remoteWs, user, mode: "two-way" | "pull"}
//   request(method, path, body) → {status, body}: JSON over the remote's
//                               API with the replica's write token
//   getMeta() / setMeta(meta)   {remote_cursor, retry: {page: flags}}
//   localChanges()              {pages: [ids edited here since acknowledged],
//                               deleted: [ids deleted here]}
//   acknowledge(page, version)  the round took the page as of `version`
//   page(id)                    {snapshot | null, version}
//   writePage(id, snapshot, version) → new version, or 0 when the page
//                               changed here after `version` (the round
//                               then reads it again); a sync write never
//                               counts as an edit made here
//   removePage(id)              the remote deleted it: gone here
//   state(id) / saveState(id, state | null)   {remote_seq, base, pending}
//   pageOfBlock(id)             the local page holding a block, or null
//   readInk(name) / storeInk(ink) → the parsed file / its stored name
//   pullFiles(names) / pushFiles(names) → how many travelled (the host
//                               skips names it has / the remote has, and
//                               stores a pulled file only when it is what
//                               its name says)
//   referencedFiles()           every upload name the local pages name
//   conflict(c) / note(entry)   the round's decisions and its log
//   progress?(p)                {done, total, page} while it runs
import { makeBlockId } from "../shared/model/blockModel.js";
import { known, ownEdits, reconcileRemoteOps, split, strays, unlanded } from "./reconcile.js";
import { merge as textMerge } from "./textmerge.js";
import { apply, applyLocal, diff, same, snapshotFromTree, subtreeIds, uploadName, uploadRefs } from "./tree.js";

export const CLIENT = "sync";
export const MAX_OPS = 500;
export const UNKNOWN_SEQ = -1;
const WRITE_TRIES = 5;

export class RemoteError extends Error {
  constructor(status, detail = "") {
    super(`remote answered ${status}${detail ? `: ${detail}` : ""}`);
    this.status = status;
    this.detail = detail;
  }
}
// The page waits for the next round, quietly (not an error).
export class PageDeferred extends Error {}

function remoteApi(host) {
  const call = async (method, path, body, ok = [200]) => {
    const r = await host.request(method, path, body ?? null);
    if (!ok.includes(r.status)) {
      const detail = r.body && typeof r.body === "object" ? r.body.detail || "" : String(r.body || "");
      throw new RemoteError(r.status, typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return r.body;
  };
  return {
    get: (path) => call("GET", path),
    post: (path, body) => call("POST", path, body, [200, 201]),
    del: (path) => call("DELETE", path, null, [200, 404]),
  };
}

async function remoteTree(remote, pageId) {
  try {
    const out = await remote.get(`/api/blocks/${pageId}/subtree`);
    return [snapshotFromTree(out.block), Number(out.seq || 0)];
  } catch (e) {
    if (e instanceof RemoteError && e.status === 404) return [null, 0];
    throw e;
  }
}

// The host's ink files as tree.applyLocal reads and stores them (the
// device's editors, replica/edits.js, use the same).
export function inkFiles(host) {
  return {
    read: async (url) => { const name = uploadName(url); return name ? host.readInk(name) : null; },
    store: async (ink) => `/api/uploads/${await host.storeInk(ink)}`,
  };
}

function stats(ops) {
  const out = { add: 0, del: 0, mod: 0 };
  for (const op of ops) {
    if (op.op === "insert") out.add++;
    else if (op.op === "delete") out.del++;
    else out.mod++;
  }
  return out;
}

async function note(ctx, pageId, action, title, ops) {
  await ctx.host.note({ page_id: pageId, action, title: title || "", stats: ops ? stats(ops) : null, at: new Date().toISOString() });
}

async function record(ctx, pageId, conflicts) {
  for (const c of conflicts) await ctx.host.conflict({ page_id: pageId, block_id: c.id, ...c });
}

// Write the page here unless it changed since `version`; → the new version.
async function write(host, pageId, snapshot, version) {
  const v = await host.writePage(pageId, snapshot, version);
  if (!v) throw new PageDeferred(`${pageId}: edited here while the round wrote it`);
  return v;
}

// --- pushing -----------------------------------------------------------------

// _send: the batches written into the page's state before the first goes,
// each under an id of its own; → base with the ops applied.
async function send(ctx, pageId, seq, base, ops) {
  if (ops.length) {
    const batches = [];
    for (let i = 0; i < ops.length; i += MAX_OPS) batches.push({ id: makeBlockId() + makeBlockId(), ops: ops.slice(i, i + MAX_OPS) });
    await ctx.host.saveState(pageId, { remote_seq: seq, base, pending: { batches, at: new Date().toISOString() } });
    for (const b of batches) await ctx.remote.post(`/api/pages/${pageId}/ops`, { client: CLIENT, batch: b.id, ops: b.ops });
  }
  return apply(base, ops);
}

// _confirm_push: a push whose answer was never read goes again under its
// batch ids, minus what the remote shows; what landed becomes the base.
async function confirmPush(ctx, pageId, state, resend) {
  const { base } = state;
  const [now] = await remoteTree(ctx.remote, pageId);
  const landed = [];
  for (const batch of now ? state.pending.batches : []) {
    const parts = batch.ops.map((op) => unlanded(op, base, now));
    const rest = parts.map(([, r]) => r).filter(Boolean);
    let sent = false;
    if (rest.length && resend) {
      try {
        await ctx.remote.post(`/api/pages/${pageId}/ops`, { client: CLIENT, batch: batch.id, ops: rest });
        sent = true;
      } catch (e) {
        const s = e.status ?? 0;
        if (s === 0 || s === 408 || s === 429 || (s >= 500 && s < 600 && s !== 507)) throw e; // the link: still pending
      }
    }
    for (const [l, r] of parts) {
      if (l) landed.push(l);
      if (r && sent) landed.push(r);
    }
  }
  const after = apply(base, landed);
  await ctx.host.saveState(pageId, { remote_seq: UNKNOWN_SEQ, base: after, pending: null });
  if (landed.length) { await note(ctx, pageId, "pushed", "", landed); ctx.report.pages_pushed++; }
  return { remote_seq: UNKNOWN_SEQ, base: after, pending: null };
}

// --- whole pages ---------------------------------------------------------------

async function pullWhole(ctx, pageId, tree, seq, action) {
  const { host } = ctx;
  ctx.report.files_pulled += await host.pullFiles([...uploadRefs(Object.values(tree))]);
  const { version } = await host.page(pageId);
  await write(host, pageId, tree, version);
  await host.saveState(pageId, { remote_seq: seq, base: tree, pending: null });
  await note(ctx, pageId, action, tree[pageId]?.content, diff({ [pageId]: tree[pageId] }, tree, pageId, { withBase: false }));
  ctx.report.pages_pulled++;
}

async function createRemotePage(ctx, pageId, local) {
  const root = local[pageId];
  let out;
  try {
    out = await ctx.remote.post("/api/pages", { id: pageId, title: root.content, properties: root.props });
  } catch (e) {
    if (e.status === 409) throw new PageDeferred(`${pageId}: the page appeared on the remote meanwhile`);
    throw e;
  }
  return { parent: "root", position: out?.position || "", content: out?.content ?? root.content,
    props: { ...(out?.properties ?? root.props) } };
}

async function pushWhole(ctx, pageId, local, action) {
  const { host, remote } = ctx;
  await host.saveState(pageId, { remote_seq: UNKNOWN_SEQ, base: { [pageId]: local[pageId] }, pending: { create: true } });
  const root = await createRemotePage(ctx, pageId, local);
  await host.saveState(pageId, { remote_seq: UNKNOWN_SEQ, base: { [pageId]: root }, pending: null });
  ctx.report.files_pushed += await host.pushFiles([...uploadRefs(Object.values(local))]);
  const ops = diff({ [pageId]: local[pageId] }, local, pageId, { withBase: false });
  const after = await send(ctx, pageId, UNKNOWN_SEQ, { [pageId]: root }, ops);
  await host.saveState(pageId, { remote_seq: UNKNOWN_SEQ, base: after, pending: null });
  const [back, seq] = await remoteTree(remote, pageId);
  await host.saveState(pageId, { remote_seq: seq, base: back || local, pending: null });
  await note(ctx, pageId, action, local[pageId]?.content, ops);
  ctx.report.pages_pushed++;
}

async function deleteHere(ctx, pageId, local) {
  await ctx.host.removePage(pageId);
  await ctx.host.saveState(pageId, null);
  await note(ctx, pageId, "deleted here", local[pageId]?.content, null);
  ctx.report.pages_deleted++;
}

async function deleteThere(ctx, pageId, tree) {
  await ctx.remote.del(`/api/blocks/${pageId}`);
  await ctx.host.saveState(pageId, null);
  await note(ctx, pageId, "deleted there", tree[pageId]?.content, null);
  ctx.report.pages_deleted++;
}

// --- blocks the remote moved in from another page ------------------------------------

// _move_over, for a replica: blocks the remote's ops insert into this page
// that another local page holds leave that page (its local tree and its
// base, so its own round never takes them for a deletion made here) and
// arrive here as the remote has them, with what that page changed in them
// since its base carried over (the text merged into the remote's, the
// property keys changed there laid over) — `edits` learns those changes, so
// the push sends them. Blocks made there under them come along; blocks that
// were under them at that page's base and are not under them now stay there,
// at its top level.
async function moveOver(ctx, pageId, ops, tree, edits, conflicts) {
  const { host } = ctx;
  const carried = new Map(); // id → {content, props}
  const leave = []; // [{page, take: ids leaving it, park: ids staying at its top level}]
  const byPage = new Map();
  for (const op of ops) {
    if (op.op !== "insert") continue;
    const q = await host.pageOfBlock(op.id);
    if (q && q !== pageId) {
      if (!byPage.has(q)) byPage.set(q, []);
      byPage.get(q).push(op.id);
    }
  }
  const extra = [];
  for (const [q, tops] of byPage) {
    const { snapshot: qLocal } = await host.page(q);
    const qState = await host.state(q);
    if (!qLocal) continue;
    const qBase = qState?.base || {};
    const take = new Set(), park = new Set();
    for (const top of tops) {
      if (!(top in qLocal)) continue;
      const theirs = subtreeIds(tree, top);
      for (const bid of subtreeIds(qLocal, top)) {
        // its base there: that page's, or what it was when that page's round
        // this time found it gone there (sync_engine's _left)
        const mine = qLocal[bid], was = qBase[bid] ?? ctx.left.get(bid);
        if (theirs.has(bid)) {
          if (was && (mine.content !== was.content || !same(mine.props, was.props))) {
            const r = tree[bid];
            let content = r.content, props = { ...r.props };
            if (mine.content !== was.content) {
              const [text, clean] = textMerge(was.content, mine.content, r.content);
              if (!clean || text !== mine.content) {
                conflicts.push({ id: bid, kind: "merged", mine: mine.content, theirs: r.content, result: text, base: was.content });
              }
              content = text;
            }
            for (const [k, v] of Object.entries(mine.props)) if (!same(v, was.props[k])) props[k] = v;
            for (const k of Object.keys(was.props)) if (!(k in mine.props)) delete props[k];
            carried.set(bid, { content, props });
            const kinds = edits.get(bid) || new Set();
            if (content !== r.content) kinds.add("content");
            if (!same(props, r.props)) kinds.add("props");
            if (kinds.size) edits.set(bid, kinds);
          }
          take.add(bid);
        } else if (!was) {
          // made here under it: it comes along, as a new block
          extra.push({ op: "insert", id: bid, parent: mine.parent, position: mine.position, content: mine.content, props: { ...mine.props } });
          edits.set(bid, new Set(["new"]));
          take.add(bid);
        } else {
          // moved elsewhere or deleted there: it stays in that page, at its top level
          park.add(bid);
        }
      }
    }
    leave.push({ page: q, take, park });
  }
  return { carried, extra, leave };
}

// The other half of moveOver, once this page holds the blocks: they leave
// the pages they were in, here and in those pages' bases.
async function leavePages(host, leave) {
  for (const { page: q, take, park } of leave) {
    for (let attempt = 1; ; attempt++) {
      const { snapshot, version } = await host.page(q);
      if (!snapshot) break;
      const next = {};
      for (const [bid, b] of Object.entries(snapshot)) {
        if (take.has(bid)) continue;
        next[bid] = park.has(bid) && take.has(b.parent) ? { ...b, parent: q } : b;
      }
      if (await host.writePage(q, next, version)) break;
      if (attempt >= WRITE_TRIES) throw new PageDeferred(`${q}: edited here while blocks moved out of it`);
    }
    const state = await host.state(q);
    if (state) {
      const base = {};
      for (const [bid, b] of Object.entries(state.base || {})) if (!take.has(bid)) base[bid] = b;
      await host.saveState(q, { ...state, base });
    }
  }
}

// --- one page ---------------------------------------------------------------------

// _sync_page. flags: {seq (the remote feed's, or null), remoteGone, localGone}.
async function syncPage(ctx, pageId, flags) {
  const { host, remote, report } = ctx;
  const pushAllowed = ctx.mode === "two-way";
  let state = await host.state(pageId);
  let { snapshot: local, version: startVersion } = await host.page(pageId);
  const ack = (v) => (pushAllowed ? host.acknowledge(pageId, v) : null);
  if (state?.pending?.batches) state = await confirmPush(ctx, pageId, state, pushAllowed && !!local);
  const base = state ? state.base : {};
  const unsettled = !state || state.remote_seq < 0;
  const creating = !!state?.pending?.create;

  // --- one side deleted it
  if (flags.remoteGone && !flags.localGone) {
    if (!local) { await host.saveState(pageId, null); await ack(startVersion); return; }
    if (!state || creating || diff(base, local, pageId).length) {
      // edited here since, or never reconciled here: the tombstone says nothing about this copy's page
      if (pushAllowed) {
        await record(ctx, pageId, [{ id: pageId, kind: "page_restored", mine: local[pageId].content, once: true,
          result: "the other side deleted this page; it was edited here, so it came back there" }]);
        await pushWhole(ctx, pageId, local, "restored there");
        await ack(startVersion);
      }
      return;
    }
    await deleteHere(ctx, pageId, local);
    await ack(startVersion);
    return;
  }
  if (flags.localGone && !local && (state || flags.seq == null)) {
    await ack(0); // the tombstone is taken care of below, whichever way
    if (!state) return;
    const [tree, seq] = await remoteTree(remote, pageId);
    if (!tree) { await host.saveState(pageId, null); return; }
    const untouched = unsettled ? !diff(base, tree, pageId).length : seq === state.remote_seq;
    if (untouched && pushAllowed) { await deleteThere(ctx, pageId, tree); return; }
    await pullWhole(ctx, pageId, tree, seq, "restored here");
    await record(ctx, pageId, [{ id: pageId, kind: "page_restored_from_remote", theirs: tree[pageId].content,
      result: "this page was deleted here but edited on the other side, so it came back" }]);
    return;
  }

  // --- both exist (or one side's is new)
  const remoteChanged = unsettled || (flags.seq != null && flags.seq !== state.remote_seq);
  const [tree, seq] = remoteChanged ? await remoteTree(remote, pageId) : [base, state.remote_seq];
  if (!tree) {
    if ((!state || creating) && local && pushAllowed) {
      await pushWhole(ctx, pageId, local, "created there");
      await ack(startVersion);
    }
    return;
  }
  if (!local) { await pullWhole(ctx, pageId, tree, seq, "created here"); return; }

  // 1. what the remote changed since the base, applied here with the merge
  // rules; 2. this copy's own edits pushed. Read, merge, write — again when
  // the page was edited here meanwhile.
  let page, merged, pushOps, conflicts, leave = [];
  for (let attempt = 1; ; attempt++) {
    page = await host.page(pageId);
    local = page.snapshot;
    if (!local) return; // deleted here meanwhile: the next round
    conflicts = [];
    let known_ = base, remoteOps = [];
    if (remoteChanged) {
      known_ = known(base, local, tree, conflicts);
      remoteOps = reconcileRemoteOps(known_, local, tree, pageId, conflicts);
    }
    const edits = ownEdits(known_, local);
    merged = local;
    if (remoteChanged && remoteOps.length) {
      report.files_pulled += await host.pullFiles([...uploadRefs(Object.values(tree))]);
      const moves = await moveOver(ctx, pageId, remoteOps, tree, edits, conflicts);
      const { carried, extra } = moves;
      leave = moves.leave;
      const res = await applyLocal(local, [...remoteOps, ...extra], inkFiles(host));
      merged = res.snapshot;
      for (const [bid, c] of carried) if (merged[bid]) merged[bid] = { ...merged[bid], content: c.content, props: c.props };
      for (const m of res.merged) conflicts.push({ ...m, base: (known_[m.id] || {}).content ?? m.base });
    }
    // what differs only through this round's own writes is put back as the remote has it
    const elsewhere = new Set();
    const back = diff(merged, tree, pageId);
    for (const op of back) if (op.op === "insert" && (await host.pageOfBlock(op.id)) && !(op.id in merged)) elsewhere.add(op.id);
    const stray = strays(back, merged, edits, elsewhere);
    if (stray.length) merged = apply(merged, stray);
    pushOps = pushAllowed ? split(diff(tree, merged, pageId), edits) : [];
    if (merged === local || same(merged, local)) break;
    const v = await host.writePage(pageId, merged, page.version);
    if (v) { page = { snapshot: merged, version: v }; break; }
    if (attempt >= WRITE_TRIES) throw new PageDeferred(`${pageId}: edited here throughout the round`);
  }
  await leavePages(host, leave);
  await record(ctx, pageId, conflicts);
  // blocks the remote no longer holds that stay here (an edit here beat their
  // deletion there, or they moved to another page there): what they were at
  // the base, for the page they went to (moveOver)
  for (const [bid, b] of Object.entries(base)) if (bid !== pageId && !(bid in tree) && bid in merged) ctx.left.set(bid, b);
  if (remoteChanged) {
    // the remote's tree is the base from here on: a round cut short after
    // this never applies these changes again
    await host.saveState(pageId, { remote_seq: seq, base: tree, pending: null });
    const pulled = diff(local, merged, pageId);
    if (pulled.length) { await note(ctx, pageId, "pulled", tree[pageId]?.content, pulled); report.pages_pulled++; }
  }
  if (!pushAllowed) return; // receive only: edits made here wait for the first round that may push
  // what is edited here after this version is pushed by the next round
  const pushedAt = page.version;
  if (!pushOps.length) { await host.acknowledge(pageId, pushedAt); return; }

  const files = uploadRefs(pushOps);
  if (unsettled) for (const n of uploadRefs(Object.values(merged))) files.add(n);
  report.files_pushed += await host.pushFiles([...files]);
  let after;
  try {
    after = await send(ctx, pageId, seq, tree, pushOps);
  } catch (e) {
    if (e.status === 403 && /outside this page/.test(e.detail || "")) {
      throw new PageDeferred(`${pageId}: a block moved to another page on the remote; retried next round`);
    }
    throw e;
  }
  await host.saveState(pageId, { remote_seq: UNKNOWN_SEQ, base: after, pending: null });
  const [back2, seq2] = await remoteTree(remote, pageId);
  if (!back2) return;
  // the remote's answer (re-keyed positions, its merges) lands here, over
  // anything edited here since the push was computed
  const settle = diff(merged, back2, pageId);
  for (let attempt = 1; settle.length; attempt++) {
    const now = await host.page(pageId);
    if (!now.snapshot) break;
    const res = await applyLocal(now.snapshot, settle, inkFiles(host));
    if (await host.writePage(pageId, res.snapshot, now.version)) break;
    if (attempt >= WRITE_TRIES) throw new PageDeferred(`${pageId}: edited here throughout the round`);
  }
  await host.saveState(pageId, { remote_seq: seq2, base: back2, pending: null });
  await host.acknowledge(pageId, pushedAt);
  await note(ctx, pageId, "pushed", merged[pageId]?.content, pushOps);
  report.pages_pushed++;
}

// --- the round ----------------------------------------------------------------------------

async function feedAll(remote, cursor) {
  const pages = new Map(), deleted = new Map();
  for (let i = 0; i < 200; i++) {
    const out = await remote.get(`/api/sync/changes?since=${encodeURIComponent(cursor)}&limit=1000`);
    for (const p of out.pages) pages.set(p.id, p.seq);
    for (const d of out.deleted) deleted.set(d.id, d.deleted_at);
    cursor = out.cursor;
    if (!out.more) break;
  }
  return { pages, deleted, cursor };
}

// One round: → the status to show ({last_sync, last_error, pages_pulled,
// pages_pushed, pages_deleted, files_pulled, files_pushed, mode,
// remote_user, remote_role, retry}). Rounds of one replica must not
// overlap (the host runs one at a time).
export async function syncRound(host) {
  const remote = remoteApi(host);
  const cfg = host.config || {};
  const report = { pages_pulled: 0, pages_pushed: 0, pages_deleted: 0, files_pulled: 0, files_pushed: 0, errors: [] };
  const meta = { remote_cursor: "", retry: {}, ...((await host.getMeta()) || {}) };
  let mode = cfg.mode === "pull" ? "pull" : "two-way";
  const me = await remote.get("/api/sync/whoami");
  if (cfg.remoteWs && me.workspace?.id !== cfg.remoteWs) {
    throw new RemoteError(409, `the token reaches workspace ${me.workspace?.id}, not ${cfg.remoteWs}`);
  }
  if (mode === "two-way" && (me.scope !== "write" || me.role === "viewer")) {
    report.errors.push("the token or your role on the remote is read-only: pulling only");
    mode = "pull";
  }
  const ctx = { host, remote, report, mode, left: new Map() };
  const feed = await feedAll(remote, meta.remote_cursor || "");
  const here = mode === "two-way" ? await host.localChanges() : { pages: [], deleted: [] };
  const localPages = new Set(here.pages), localDeleted = new Set(here.deleted);
  const todo = new Map();
  for (const id of new Set([...feed.pages.keys(), ...feed.deleted.keys(), ...localPages, ...localDeleted])) {
    todo.set(id, { seq: feed.pages.has(id) ? feed.pages.get(id) : null,
      remoteGone: feed.deleted.has(id) && !feed.pages.has(id), localGone: localDeleted.has(id) && !localPages.has(id) });
  }
  for (const [id, flags] of Object.entries(meta.retry || {})) if (!todo.has(id)) todo.set(id, flags);
  const failed = {};
  const order = [...todo.keys()].sort();
  for (let n = 0; n < order.length; n++) {
    const id = order[n];
    await host.progress?.({ done: n, total: order.length, page: id });
    try {
      await syncPage(ctx, id, todo.get(id));
    } catch (e) {
      failed[id] = todo.get(id);
      if (!(e instanceof PageDeferred)) report.errors.push(`${id}: ${e.message || e}`);
    }
  }
  // files the pages here name but the device lacks (a round cut short): fetched every round
  try {
    report.files_pulled += await host.pullFiles(await host.referencedFiles());
  } catch (e) {
    report.errors.push(`files: ${e.message || e}`);
  }
  await host.setMeta({ ...meta, remote_cursor: feed.cursor, retry: failed });
  const { errors, ...counts } = report;
  return { ...counts, last_sync: new Date().toISOString(), last_error: errors[0] || "", mode,
    remote_user: me.user || "", remote_role: me.role || "", retry: failed };
}
