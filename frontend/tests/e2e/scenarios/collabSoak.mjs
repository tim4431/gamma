// The crowd soak for real-time collaboration (docs/dev/collab.md "Testing"):
// N tabs, each the app's own page session (src/collaboration/collabSession.js)
// over Node's fetch and WebSocket against a real server, typing, deleting,
// moving and setting things on a few shared pages at once, through socket
// drops and network outages. Every batch, retry, catch-up, refetch and caret
// is the production code's; a tab here is App.jsx's part of the contract
// only (SoakTab). When the typing stops, every tab must show what the server
// holds, and no word typed may be gone but by the merge's documented rules,
// which the oracle checks by replaying the batch that lost it through the
// server's own merge (gamma/textmerge.py).
//
// The engine is runSoak(); tests/e2e/collabSoak.mjs is its command line
// (any size, the per-window table, --strict leveling), and
// collabSoakScenarios is the short run of the collab-soak group.
import { execFile, execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { monitorEventLoopDelay } from "node:perf_hooks";
import { promisify } from "node:util";
import { PYTHON, ROOT, sleep } from "../harness.mjs";
import { createCollabSession, retryableStatus } from "../../../src/collaboration/collabSession.js";
import { applyOps } from "../../../src/shared/model/blockOps.js";
import { findBlock, insertBlockAt, makeId, removeBlockTree, updateBlockTree } from "../../../src/shared/model/blockModel.js";

const BACKEND = path.join(ROOT, "backend");
const execFileAsync = promisify(execFile);

// ---------------------------------------------------------------------------
// Words

// A seeded generator (mulberry32): the same seed makes the same choices.
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let x = a;
    x = Math.imul(x ^ (x >>> 15), x | 1);
    x ^= x + Math.imul(x ^ (x >>> 7), x | 61);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

// The filler is lowercase and a token is 8 capitals and digits, so the two
// never share a character class: a token cut or glued to a word is no
// token any more.
const FILLER = "alpha beta gamma delta epsilon zeta theta kappa lambda sigma omega".split(" ");
const TOKEN_CHARS = "BCDFGHJKLMNPQRSTVWXZ23456789";
const WORD = /(?<![^ ])[^ ]+(?![^ ])/g;
const isToken = (w) => /^[BCDFGHJKLMNPQRSTVWXZ2-9]{8}$/.test(w);
const isFiller = (w) => /^[a-z]+$/.test(w);
const COLORS = ["#ffe28fa6", "#aaebaaa6", "#9bcdffa6", "#e6b4ffa6"];
const NOTES_KEPT = 4; // private notes a tab holds at most

// The whole words of a text, with where each starts.
const words = (text) => [...(text || "").matchAll(WORD)].map((m) => ({ word: m[0], at: m.index }));
const countWord = (text, word) => words(text).filter((w) => w.word === word).length;

// `word` typed at a word boundary: before the first word or after any.
function insertWord(text, word, rand) {
  if (!text) return { text: word, caret: word.length };
  const cuts = [0, ...[...text.matchAll(/ /g)].map((m) => m.index), text.length];
  const at = cuts[Math.floor(rand() * cuts.length)];
  if (at === 0) return { text: `${word} ${text}`, caret: word.length };
  return { text: `${text.slice(0, at)} ${word}${text.slice(at)}`, caret: at + 1 + word.length };
}

// The word at `at` taken out with one space beside it: the one before it
// (`before`) or after it, whichever there is.
function removeWord(text, at, length, before) {
  let start = at, end = at + length;
  if (before && start > 0) start -= 1;
  else if (end < text.length) end += 1;
  else if (start > 0) start -= 1;
  return text.slice(0, start) + text.slice(end);
}

const filler = (rand, n) => Array.from({ length: n }, () => FILLER[Math.floor(rand() * FILLER.length)]).join(" ");

// ---------------------------------------------------------------------------
// Numbers

const pct = (xs, p) => {
  if (!xs.length) return NaN;
  const s = Float64Array.from(xs).sort();
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
};
const ms = (x) => (Number.isFinite(x) ? String(Math.round(x)) : "-");
const mb = (x) => (Number.isFinite(x) ? (x / 1048576).toFixed(1) : "-");

// The process's resident memory and CPU seconds: /proc on Linux, ps on
// macOS, nothing elsewhere.
let clockTicks = 0;
async function processStats(pid) {
  try {
    if (process.platform === "linux") {
      clockTicks ||= Number(execFileSync("getconf", ["CLK_TCK"], { encoding: "utf8" }).trim()) || 100;
      const status = fs.readFileSync(`/proc/${pid}/status`, "utf8");
      const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
      const fields = stat.slice(stat.lastIndexOf(")") + 2).split(" ");
      return { rss: Number(status.match(/VmRSS:\s+(\d+)/)[1]) * 1024, cpu: (Number(fields[11]) + Number(fields[12])) / clockTicks };
    }
    if (process.platform === "darwin") {
      const { stdout } = await execFileAsync("ps", ["-o", "rss=,time=", "-p", String(pid)]);
      const [rss, time] = stdout.trim().split(/\s+/);
      const cpu = time.split(":").reduce((sum, part) => sum * 60 + Number(part), 0);
      return { rss: Number(rss) * 1024, cpu };
    }
  } catch {}
  return { rss: NaN, cpu: NaN };
}

const fileSize = (file) => { try { return fs.statSync(file).size; } catch { return NaN; } };

// The op log's limits (gamma/ops.py), read from the server's own code.
function opLogLimits() {
  const out = execFileSync(PYTHON, ["-c", "import json\nfrom gamma import ops\nprint(json.dumps([ops.KEEP_OPS, ops.PRUNE_EVERY, ops.KEEP_OPS_BYTES]))"],
    { cwd: BACKEND, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const [keep, every, bytes] = JSON.parse(out);
  return { keep, every, bytes };
}

// Rows and payload bytes of each page's op log, read without a write lock.
const OPLOG_PY = `import json, sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(json.dumps(c.execute("SELECT page_id, COUNT(*), SUM(LENGTH(ops)) FROM page_ops GROUP BY page_id").fetchall()))`;
async function opLogRows(db) {
  try {
    const { stdout } = await execFileAsync(PYTHON, ["-c", OPLOG_PY, db], { encoding: "utf8" });
    return Object.fromEntries(JSON.parse(stdout).map(([page, rows, bytes]) => [page, { rows, bytes }]));
  } catch { return {}; }
}

// Each case {base, ours, theirs, word} merged as the server merges it, and
// whether either side rewrote words where the token was (replaced text
// with other text in one save, read by words, over or beside the token's
// place, or typing the token as part of it): character merging interleaves
// such a rewrite with what the other side typed beside it, by design
// (gamma/textmerge.py "Typing merges character by character").
const REPLAY_PY = `import json, re, sys
from gamma import textmerge
out = []
for c in json.load(sys.stdin):
    base, word = c["base"], c["word"]
    text, clean = textmerge.merge(base, c["ours"], c["theirs"])
    where = [(m.start(), m.end()) for m in re.finditer(r"(?<![^ ])" + re.escape(word) + r"(?![^ ])", base)]
    rewrites = []
    for side in (c["ours"], c["theirs"]):
        where += [(s, e) for s, e, i in textmerge._hunks(base, side) if word in i]
        rewrites += [(s, e, i) for s, e, i in textmerge._hunks(base, side, True) if e > s and i]
    rewrite = any(word in i or any(s <= b and a <= e for a, b in where) for s, e, i in rewrites)
    out.append({"merged": text, "clean": clean, "rewrite": rewrite})
print(json.dumps(out))`;
function replay(cases) {
  if (!cases.length) return [];
  const r = spawnSync(PYTHON, ["-c", REPLAY_PY], {
    cwd: BACKEND, input: JSON.stringify(cases), encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  });
  if (r.status !== 0) throw new Error(`textmerge replay failed: ${r.stderr}`);
  return JSON.parse(r.stdout);
}

// ---------------------------------------------------------------------------
// Pages

// `n` pages of filler blocks in `account`'s workspace: [{id, shared: [ids], hot, seq, texts}]
// (hot: the first block, where half of all typing goes).
export async function seedPages(account, n, { blocks = 8, seed = 1 } = {}) {
  const rand = mulberry32(seed * 7919);
  const pages = [];
  for (let p = 0; p < n; p++) {
    const page = await account.api("/api/pages", { method: "POST", body: { title: `Soak ${p + 1}` } });
    for (let b = 0; b < blocks; b++) {
      await account.api("/api/blocks", { method: "POST", body: { parent_id: page.id, content: filler(rand, 12) } });
    }
    const tree = await account.api(`/api/blocks/${page.id}/subtree`);
    const shared = tree.block.children.map((c) => c.id);
    pages.push({ id: page.id, shared, hot: shared[0], seq: tree.seq || 0, texts: new Map(tree.block.children.map((c) => [c.id, c.content])) });
  }
  return pages;
}

// A tree as the comparison reads it: ids, text, properties and order.
const norm = (list) => (list || []).map((n) => [n.id, n.content || "",
  JSON.stringify(Object.entries(n.properties || {}).filter(([, v]) => v != null).sort(([a], [b]) => (a < b ? -1 : 1))), norm(n.children)]);
const walk = (list, fn, parent = null) => { for (const n of list || []) { fn(n, parent); walk(n.children, fn, n.id); } };

// ---------------------------------------------------------------------------
// A tab: the app's page session with App.jsx's side of its contract

class SoakTab {
  constructor(soak, i, account, page) {
    this.soak = soak; this.i = i; this.account = account; this.page = page.id; this.pageInfo = page;
    this.clientId = `soak${i}${makeId().slice(0, 6)}`;
    this.rand = mulberry32((soak.seed * 1000003 + i * 7907) >>> 0);
    this.tree = [];
    this.live = [];            // our tokens on shared blocks, oldest first
    this.notes = new Set();    // our private notes still there
    this.offline = false;
    this.faults = [];          // [start, end] spans with the network or the socket down (end: the next hello)
    this.seen = new Set();     // tokens this tab has shown
    this.loading = false; this.remoteInLoad = false;
    this.refresh = { token: 0, timer: null, tries: 0 };
    this.sock = null; this.sockets = 0; this.joinedAt = 0;
    const tab = this;
    this.options = {
      pageId: this.page, canWrite: true,
      onRemoteOps: (ops, pageId, pos) => tab.remote(ops, pageId, pos),
      onReload: (pageId) => { if (pageId === tab.page) { soak.count("refetch"); tab.load(); } },
      onStatus: () => {},
      onSaveNotice: (text, kind) => { if (kind === "rejected" && text) soak.count("rejected"); },
      onGone: (pageId, gone) => { if (gone) soak.count("gone"); },
      onComments: () => {}, onBuild: () => {}, onInkLive: () => {},
    };
    this.session = createCollabSession({
      clientId: this.clientId,
      api: (p, init) => tab.api(p, init),
      openSocket: (id) => tab.openSocket(id),
      keepalivePost: () => {},
      opts: () => tab.options,
    });
  }

  // utils.apiJson over Node's fetch: the account's cookie and workspace as
  // the browser sends them, a bare "401 Unauthorized", else an error with
  // the status and the JSON body; offline, what a browser's fetch throws.
  async api(p, init = {}) {
    const soak = this.soak;
    if (this.offline) { soak.count("offline call"); throw new TypeError("Failed to fetch"); }
    const method = init.method || "GET";
    const isOps = method === "POST" && /^\/pages\/[^/]+\/ops$/.test(p);
    const t0 = performance.now();
    let r, text;
    try {
      r = await fetch(`${soak.server.base}/api${p}`, { ...init, headers: { ...this.account.headers(), ...(init.headers || {}) } });
      text = await r.text();
    } catch (err) { soak.count("network error"); throw err; }
    const took = performance.now() - t0;
    soak.status(r.status);
    let data; try { data = JSON.parse(text); } catch { data = null; }
    if (isOps) {
      const post = { tab: this.i, page: decodeURIComponent(p.split("/")[2]), t: t0, ms: took, body: JSON.parse(init.body), status: r.status, resp: r.ok ? data : null };
      soak.posts.push(post);
      soak.rec("ack", t0, took);
      if (!r.ok && data?.conflict) soak.count(`conflict ${data.conflict}`);
    } else if (method === "GET" && /\/ops\?since=/.test(p)) {
      soak.count(r.status === 410 ? "catch-up 410" : "catch-up");
    }
    if (r.status === 401) throw new Error("401 Unauthorized");
    if (!r.ok) {
      const err = new Error(typeof data?.detail === "string" ? data.detail : text || `HTTP ${r.status}`);
      err.status = r.status; err.data = data;
      throw err;
    }
    return data;
  }

  // The page's socket, as the browser's: offline it still opens, then fails
  // with 1006 a moment later (a browser's `new WebSocket` never throws for
  // the network, and collabSession stops reconnecting if it does).
  // `drop()` cuts it as a network would: 1006, and nothing more read.
  openSocket(pageId) {
    const soak = this.soak;
    const sock = { dead: false, cut: false, onopen: null, onmessage: null, onclose: null, onerror: null };
    let real = null;
    const die = (code) => {
      if (sock.dead) return;
      sock.dead = true;
      soak.count(`close ${code}`);
      sock.onerror?.({});
      sock.onclose?.({ code });
    };
    sock.drop = () => {
      if (sock.dead || sock.cut) return;
      sock.cut = true;
      try { real?.close(); } catch {}
      setTimeout(() => die(1006), 0);
    };
    if (this.offline) {
      Object.assign(sock, { readyState: 0, send() { throw new Error("not open"); }, close() {} });
      setTimeout(() => { sock.readyState = 3; die(1006); }, 50);
      return sock;
    }
    if (this.sockets++) soak.count("reconnect");
    const url = `ws://127.0.0.1:${soak.server.port}/api/ws/page/${encodeURIComponent(pageId)}?client=${this.clientId}&ws=${this.account.ws}`;
    real = new WebSocket(url, { headers: { Cookie: `session=${this.account.session}` } });
    Object.defineProperty(sock, "readyState", { get: () => (sock.dead || sock.cut ? 3 : real.readyState) });
    sock.send = (data) => real.send(data);
    sock.close = () => { sock.dead = true; real.close(); };
    real.onopen = () => { if (!sock.dead && !sock.cut) sock.onopen?.(); };
    real.onmessage = (ev) => {
      if (sock.dead || sock.cut) return;
      this.observe(ev.data);
      sock.onmessage?.(ev);
    };
    real.onerror = () => {};
    real.onclose = (ev) => { if (!sock.cut) die(ev.code); };
    this.sock = sock;
    return sock;
  }

  // What the socket brought, for the fan-out times: each peer batch by
  // page:seq, and when this tab (re)joined.
  observe(data) {
    let m;
    try { m = JSON.parse(data); } catch { return; }
    const now = performance.now();
    if (m.t === "hello") {
      this.joinedAt = now;
      if (!this.offline) for (const f of this.faults) if (f[1] == null) f[1] = now;
    } else if (m.t === "ops" && m.client !== this.clientId) {
      this.soak.arrivals.push({ key: `${this.page}:${m.seq}`, tab: this.i, t: now });
    }
  }

  // App.jsx loadBlocksForBlock: the page fetched, this tab's unsaved edits
  // laid over it (overlay), committed as a load with its seq; a failed
  // fetch tells the session (reloadFailed) and tries again from 2 s,
  // doubling up to 30 s. An answer a newer fetch overtook is dropped.
  async load() {
    const r = this.refresh;
    const token = ++r.token;
    clearTimeout(r.timer);
    const current = () => token === r.token && !this.closed;
    let data;
    try {
      data = await this.api(`/blocks/${encodeURIComponent(this.page)}/subtree`);
    } catch (err) {
      if (!current()) return;
      this.soak.count("refetch failed");
      this.session.reloadFailed(this.page);
      if (err?.status === 404 && err.data?.trashed) { this.session.gone(this.page, err.data.trashed); return; }
      if (retryableStatus(err?.status || 0)) {
        r.timer = setTimeout(() => { if (current()) this.load(); }, Math.min(2000 * 2 ** r.tries++, 30000));
      }
      return;
    }
    if (!current()) return;
    const children = this.session.overlay(this.page, data.block?.children || []);
    this.tree = children;
    this.saw(children, true);
    r.tries = 0;
    // The transition effect's load commit; batches it applies at once land
    // on the tree without a commit of their own, and the effect's next run
    // (a "remote" transition) commits them.
    this.loading = true; this.remoteInLoad = false;
    try { this.session.commit(children, { isLoad: true, seq: data.seq ?? null }); } finally { this.loading = false; }
    if (this.remoteInLoad) this.session.commit(this.tree);
  }

  // App.jsx onRemoteOps (applyRemoteRef): the ops applied to the tree and
  // the transition committed at once (flushSync), so the session diffs it
  // before the next batch advances its base; a batch that can't apply
  // refetches the page.
  remote(ops, pageId, pos) {
    if (pageId !== this.page) return;
    const treeOps = ops.filter((op) => !(op.op === "set" && op.id === pageId));
    if (!treeOps.length) return;
    try { this.tree = applyOps(this.tree, treeOps, pageId, pos); } catch {
      this.soak.count("remote ops failed to apply");
      queueMicrotask(() => this.load());
      return;
    }
    this.saw(treeOps, false);
    if (this.loading) { this.remoteInLoad = true; return; }
    this.session.commit(this.tree);
  }

  // Tokens shown for the first time: the visible latency of each, from its
  // typing (a token of a refetch counts once too, but not as a time).
  saw(nodes, fetched) {
    const now = performance.now();
    const look = (text) => {
      for (const { word } of words(text)) {
        if (!isToken(word) || this.seen.has(word)) continue;
        this.seen.add(word);
        const tok = this.soak.tokens.get(word);
        if (!tok || tok.owner === this.i || fetched) continue;
        const owner = this.soak.tabs[tok.owner];
        if (this.soak.faulted(this, tok.born, now) || this.soak.faulted(owner, tok.born, now)) continue;
        this.soak.rec("visible", tok.born, now - tok.born);
      }
    };
    if (fetched) walk(nodes, (n) => look(n.content));
    else for (const op of nodes) if (typeof op.content === "string") look(op.content);
  }

  // An edit: the tree changes and its transition effect commits it.
  edit(tree) {
    this.tree = tree;
    this.session.commit(tree);
  }

  sharedNodes() {
    const out = [];
    walk(this.tree, (n) => { if (this.soak.sharedIds.has(n.id)) out.push(n); });
    return out;
  }

  newToken(block, isPrivate) {
    const soak = this.soak;
    let word;
    do word = Array.from({ length: 8 }, () => TOKEN_CHARS[Math.floor(this.rand() * TOKEN_CHARS.length)]).join("");
    while (soak.tokens.has(word));
    soak.tokens.set(word, { owner: this.i, page: this.page, block, private: isPrivate, born: performance.now(), retired: false });
    this.seen.add(word);
    return word;
  }

  // One thing a person does. Past `keepLive` tokens of ours the oldest one
  // goes first, and a tab holding NOTES_KEPT notes deletes one rather than
  // add another, so the pages stop growing and memory and the database can
  // level off.
  act() {
    const soak = this.soak;
    const rand = this.rand;
    const shared = this.sharedNodes();
    if (!shared.length) return;
    if (soak.keepLive && this.live.length > soak.keepLive && this.retire()) return;
    const hot = shared.find((n) => n.id === this.pageInfo.hot);
    const target = hot && rand() < 0.5 ? hot : shared[Math.floor(rand() * shared.length)];
    const text = target.content || "";
    const r = rand();
    if (r < 0.55) {
      const word = this.newToken(target.id, false);
      const typed = insertWord(text, word, rand);
      this.live.push(word);
      soak.count("type token");
      this.edit(updateBlockTree(this.tree, target.id, (b) => ({ ...b, content: typed.text })));
      this.session.sendCursor({ block: target.id, anchor: typed.caret, head: typed.caret });
    } else if (r < 0.65) {
      if (words(text).filter((w) => isFiller(w.word)).length >= 16) return;
      const typed = insertWord(text, filler(rand, 1), rand);
      soak.count("type word");
      this.edit(updateBlockTree(this.tree, target.id, (b) => ({ ...b, content: typed.text })));
      this.session.sendCursor({ block: target.id, anchor: typed.caret, head: typed.caret });
    } else if (r < 0.78) {
      const fill = words(text).filter((w) => isFiller(w.word));
      if (!fill.length) return;
      const w = fill[Math.floor(rand() * fill.length)];
      soak.count("delete word");
      this.edit(updateBlockTree(this.tree, target.id, (b) => ({ ...b, content: removeWord(text, w.at, w.word.length, rand() < 0.5) })));
    } else if (r < 0.84) {
      const color = COLORS.filter((c) => c !== target.properties?.color)[Math.floor(rand() * (COLORS.length - 1))];
      soak.count("set property");
      this.edit(updateBlockTree(this.tree, target.id, (b) => ({ ...b, properties: { ...(b.properties || {}), color } })));
    } else if (r < 0.9 && this.notes.size < NOTES_KEPT) {
      // a private note: under the page or a shared block, never a parent
      const id = `${makeId()}`;
      const parent = rand() < 0.5 ? null : target.id;
      const siblings = parent ? findBlock(this.tree, parent)?.children || [] : this.tree;
      const word = this.newToken(id, true);
      this.notes.add(id);
      soak.notes.set(id, { owner: this.i, page: this.page, deleted: false });
      soak.count("insert note");
      this.edit(insertBlockAt(this.tree, parent, Math.floor(rand() * (siblings.length + 1)),
        { id, content: `${filler(rand, 3)} ${word}`, properties: {}, children: [] }));
    } else if (r < 0.94) {
      const mine = [...this.notes].filter((id) => findBlock(this.tree, id));
      if (!mine.length) return;
      const id = mine[Math.floor(rand() * mine.length)];
      this.notes.delete(id);
      soak.notes.get(id).deleted = true;
      soak.count("delete note");
      this.edit(removeBlockTree(this.tree, id));
    } else {
      // a shared block moved: to the top at any place, or under another shared block
      const other = shared[Math.floor(rand() * shared.length)];
      if (other.id === target.id) return;
      const without = removeBlockTree(this.tree, target.id);
      if (!findBlock(without, other.id)) return; // it is inside the one moving
      const parent = rand() < 0.5 ? null : other.id;
      const siblings = parent ? findBlock(without, parent)?.children || [] : without;
      soak.count("move block");
      this.edit(insertBlockAt(without, parent, Math.floor(rand() * (siblings.length + 1)), findBlock(this.tree, target.id)));
    }
  }

  // Our oldest token this tab shows, taken out with a space beside it.
  retire() {
    for (let k = 0; k < this.live.length; k++) {
      const word = this.live[k];
      const tok = this.soak.tokens.get(word);
      const node = findBlock(this.tree, tok.block);
      const hit = node && words(node.content).find((w) => w.word === word);
      if (!hit) continue;
      this.live.splice(k, 1);
      tok.retired = true;
      this.soak.count("retire token");
      this.edit(updateBlockTree(this.tree, tok.block, (b) => ({ ...b, content: removeWord(b.content, hit.at, word.length, this.rand() < 0.5) })));
      return true;
    }
    return false;
  }

  // The network goes for `ms`: calls fail, the socket drops and new ones
  // fail; then it is back and, as the browser's `online` event does,
  // a save waiting for its retry goes at once.
  outage(msLong) {
    this.offline = true;
    this.faults.push([performance.now(), null]);
    this.sock?.drop();
    setTimeout(() => {
      this.offline = false;
      if (!this.closed) this.session.retryNow();
    }, msLong);
  }

  drop() {
    if (!this.sock || this.sock.dead || this.sock.cut) return false;
    this.faults.push([performance.now(), null]);
    this.sock.drop();
    return true;
  }
}

// ---------------------------------------------------------------------------
// The run

// N tabs (accounts[i % accounts.length], pages[i % pages.length]) for
// `seconds`, then quiet until every edit is saved and every tab shows the
// server's tree; the oracle's verdict. Returns the report (report.failures
// empty when all is well). `faults`: a socket drop every `dropEvery` s and
// a `outageMs` outage every `outageEvery` s, each on a random tab.
// `sabotage`: one blind replace (a set without base) mid-run, which the
// oracle must name.
export async function runSoak({
  server, accounts, workspace, pages, clients = accounts.length, seconds = 20, seed = 1, keepLive = 25,
  faults = false, dropEvery = 4, outageEvery = 6, outageMs = 4000, windowS = 30, sabotage = false, log = () => {},
}) {
  const soak = {
    server, seed, keepLive,
    tabs: [], tokens: new Map(), notes: new Map(), posts: [], arrivals: [],
    sharedIds: new Set(pages.flatMap((p) => p.shared)),
    counts: {}, statuses: {}, metrics: { ack: [], visible: [] }, samples: [], loops: [], oplog: [],
    count(what) { this.counts[what] = (this.counts[what] || 0) + 1; },
    status(code) { this.statuses[code] = (this.statuses[code] || 0) + 1; },
    rec(name, t, value) { this.metrics[name].push([t, value]); },
    // whether `tab`'s network or socket was down at any time from a to b
    faulted(tab, a, b) { return tab.faults.some(([s, e]) => s <= b && (e == null || e >= a)); },
  };
  const db = path.join(server.dataDir, "workspaces", workspace, "pages.db");
  const usersDb = path.join(server.dataDir, "users.db");
  const limits = opLogLimits();
  const logMark = server.log().length;

  const rand = mulberry32(seed);
  for (let i = 0; i < clients; i++) soak.tabs.push(new SoakTab(soak, i, accounts[i % accounts.length], pages[i % pages.length]));
  for (const tab of soak.tabs) { await tab.load(); tab.session.connect(tab.page); }
  await sleep(300);

  // The server every 5 s: memory, CPU and the databases' sizes; the op log
  // every 30 s (a python of its own, off the event loop).
  const sample = async () => {
    const { rss, cpu } = await processStats(server.proc.pid);
    soak.samples.push({ t: performance.now(), rss, cpu, pages: fileSize(db), wal: fileSize(`${db}-wal`), users: fileSize(usersDb) });
  };
  const loop = monitorEventLoopDelay({ resolution: 10 });
  loop.enable();
  const t0 = performance.now();
  const end = t0 + seconds * 1000;
  const timers = [];
  let stopping = false;
  const every = (msGap, fn) => { const id = setInterval(() => { if (!stopping) fn(); }, msGap); timers.push(() => clearInterval(id)); };
  await sample();
  soak.oplog.push({ t: t0, rows: await opLogRows(db) });
  every(5000, sample);
  every(windowS * 1000, async () => { soak.oplog.push({ t: performance.now(), rows: await opLogRows(db) }); });
  every(windowS * 1000, () => {
    soak.loops.push({ t: performance.now(), p50: loop.percentile(50) / 1e6, p99: loop.percentile(99) / 1e6, max: loop.max / 1e6 });
    loop.reset();
  });

  // Each tab acts every 150–450 ms.
  for (const tab of soak.tabs) {
    const next = () => {
      if (stopping || performance.now() >= end) return;
      try { tab.act(); } catch (err) { soak.count("act threw"); log(`tab ${tab.i}: ${err.stack || err}`); }
      const id = setTimeout(next, 150 + tab.rand() * 300);
      timers.push(() => clearTimeout(id));
    };
    const id = setTimeout(next, tab.rand() * 300);
    timers.push(() => clearTimeout(id));
  }
  if (faults) {
    every(dropEvery * 1000, () => {
      const up = soak.tabs.filter((t) => !t.offline && t.sock && !t.sock.dead);
      if (up.length && performance.now() < end) { up[Math.floor(rand() * up.length)].drop(); soak.count("fault: socket drop"); }
    });
    every(outageEvery * 1000, () => {
      const online = soak.tabs.filter((t) => !t.offline);
      if (online.length && performance.now() + outageMs < end) { online[Math.floor(rand() * online.length)].outage(outageMs); soak.count("fault: outage"); }
    });
  }
  let sabotaged = null;
  if (sabotage) {
    // A set without a base, from a client of its own: it replaces the text
    // with one that lacks a token someone else typed. Tried again in 500 ms
    // while tab 0 is offline (--faults) or the POST fails, so a failed try
    // neither ends the run unhandled nor leaves the server running.
    const tab = soak.tabs[0];
    const tryIt = async () => {
      if (sabotaged || stopping) return;
      const node = !tab.offline && findBlock(tab.tree, tab.pageInfo.hot);
      const foreign = node && words(node.content).find((w) => isToken(w.word) && soak.tokens.get(w.word)?.owner !== tab.i);
      if (!foreign) { setTimeout(attempt, 500); return; }
      const batch = `sabotage${makeId().slice(0, 6)}`;
      const content = removeWord(node.content, foreign.at, foreign.word.length, true);
      try {
        const resp = await tab.api(`/pages/${encodeURIComponent(tab.page)}/ops`, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client: `${tab.clientId}x`, batch, ops: [{ op: "set", id: node.id, content }] }) });
        sabotaged = { token: foreign.word, batch, seq: resp.seq };
        log(`sabotage: batch ${batch} (seq ${resp.seq}) replaced block ${node.id} without its token ${foreign.word}`);
      } catch (err) {
        soak.count("sabotage retried");
        log(`sabotage: batch ${batch} failed (${err.message}), trying again`);
        setTimeout(attempt, 500);
      }
    };
    const attempt = () => tryIt().catch((err) => { soak.count("sabotage threw"); log(`sabotage: ${err.stack || err}`); });
    setTimeout(attempt, (seconds * 1000) / 2);
  }

  while (performance.now() < end) await sleep(Math.min(250, end - performance.now()));
  stopping = true;
  for (const stop of timers) stop();
  const tRun = performance.now();
  soak.loops.push({ t: tRun, p50: loop.percentile(50) / 1e6, p99: loop.percentile(99) / 1e6, max: loop.max / 1e6 });
  loop.disable();

  // Quiet: the network back everywhere, every edit saved, then every tab
  // showing what the server holds (a socket in its reconnect backoff, a
  // refetch waiting for its retry, as in the browser).
  for (const tab of soak.tabs) if (tab.offline) { tab.offline = false; tab.session.retryNow(); }
  const failures = [];
  for (let round = 0; soak.tabs.some((t) => t.session.hasPending()) && performance.now() - tRun < 30000; round++) {
    for (const tab of soak.tabs) tab.session.retryNow();
    await Promise.all(soak.tabs.map((t) => t.session.flush()));
    await sleep(100);
  }
  const unsaved = soak.tabs.filter((t) => t.session.hasPending()).map((t) => t.i);
  if (unsaved.length) failures.push(`edits still unsaved 30 s after the typing stopped: tabs ${unsaved.join(", ")}`);
  const serverTrees = new Map();
  let divergent = [];
  const tQuiet = performance.now();
  while (true) {
    divergent = [];
    for (const page of pages) serverTrees.set(page.id, (await accounts[0].api(`/api/blocks/${page.id}/subtree`)).block.children);
    for (const tab of soak.tabs) {
      if (JSON.stringify(norm(tab.tree)) !== JSON.stringify(norm(serverTrees.get(tab.page)))) divergent.push(tab.i);
    }
    if (!divergent.length || performance.now() - tQuiet > 20000) break;
    await sleep(200);
  }
  const quiesceMs = performance.now() - tRun;
  if (divergent.length) failures.push(`tabs ${divergent.join(", ")} still differ from the server's tree 20 s after every edit was saved`);
  await sample();
  soak.oplog.push({ t: performance.now(), rows: await opLogRows(db) });
  for (const tab of soak.tabs) { tab.closed = true; clearTimeout(tab.refresh.timer); tab.session.disconnect(); }

  // --- the oracle -----------------------------------------------------------
  const texts = new Map(); // block → final text, every page
  const pageOf = new Map();
  for (const [pageId, children] of serverTrees) walk(children, (n) => { texts.set(n.id, n.content || ""); pageOf.set(n.id, pageId); });
  const counts = new Map();
  for (const text of texts.values()) for (const { word } of words(text)) if (isToken(word)) counts.set(word, (counts.get(word) || 0) + 1);
  for (const page of pages) {
    const missing = page.shared.filter((id) => pageOf.get(id) !== page.id);
    if (missing.length) failures.push(`shared blocks gone from page ${page.id}: ${missing.join(", ")}`);
  }
  for (const [id, note] of soak.notes) {
    if (note.deleted && texts.has(id)) failures.push(`private note ${id} of tab ${note.owner} still there after its delete`);
    if (!note.deleted && !texts.has(id)) failures.push(`private note ${id} of tab ${note.owner} gone`);
  }

  // Each block's stored text by seq, from the seed and every answered batch
  // (the soak is the only writer).
  const history = new Map();
  const put = (id, seq, text) => { const h = history.get(id) || []; h.push([seq, text]); history.set(id, h); };
  for (const page of pages) for (const [id, text] of page.texts) put(id, page.seq, text);
  const answered = new Map(); // page:seq → post
  for (const post of soak.posts) {
    if (!post.resp?.seq) continue;
    const key = `${post.page}:${post.resp.seq}`;
    if (answered.has(key)) continue; // a retry answered from the log
    answered.set(key, post);
    for (const op of post.resp.ops || []) if (typeof op.content === "string" && op.id !== post.page) put(op.id, post.resp.seq, op.content);
  }
  for (const h of history.values()) h.sort((a, b) => a[0] - b[0]);
  const storedBefore = (id, seq) => (history.get(id) || []).filter(([q]) => q < seq).at(-1)?.[1] ?? null;
  const contentOp = (post, id) => post.body.ops.filter((op) => op.id === id && typeof op.content === "string").at(-1);
  const storedOp = (post, id) => (post.resp.ops || []).filter((op) => op.id === id && typeof op.content === "string").at(-1);

  // The batch that lost `word` from `block` (`retired`: the owner's delete
  // that should have taken it out), as a case to replay.
  const cases = [];
  const caseFor = (word, tok, kind) => {
    const h = history.get(tok.block) || [];
    const has = (text) => countWord(text, word) > 0;
    let post = null;
    if (kind === "kept") {
      post = [...answered.values()].filter((p) => p.tab === tok.owner && (() => { const op = contentOp(p, tok.block); return op && has(op.base ?? "") && !has(op.content); })())
        .sort((a, b) => a.resp.seq - b.resp.seq)[0] || null;
      if (post && !has(storedOp(post, tok.block)?.content ?? "")) {
        const back = h.find(([q, text]) => q > post.resp.seq && has(text));
        return { word, ...tok, kind, verdict: `UNEXPLAINED: deleted in seq ${post.resp.seq}, back in seq ${back?.[0]}`, seq: back?.[0] };
      }
    } else {
      const first = h.findIndex(([, text]) => has(text));
      if (first < 0) {
        post = [...answered.values()].filter((p) => has(contentOp(p, tok.block)?.content ?? "")).sort((a, b) => a.resp.seq - b.resp.seq)[0] || null;
      } else {
        const gone = h.findIndex(([, text], k) => k > first && !has(text));
        post = gone >= 0 ? answered.get(`${pageOf.get(tok.block) || tok.page}:${h[gone][0]}`) || null : null;
        if (!post && gone >= 0) post = [...answered.values()].find((p) => p.resp.seq === h[gone][0] && storedOp(p, tok.block)) || null;
      }
    }
    if (!post) return { word, ...tok, kind, verdict: kind === "kept" ? "UNEXPLAINED: its delete was never saved" : "UNEXPLAINED: never saved" };
    const op = contentOp(post, tok.block);
    const c = {
      word, ...tok, kind, seq: post.resp.seq, tab: post.tab, client: post.body.client, batch: post.body.batch,
      base: op?.base ?? null, ours: op?.content ?? null, theirs: storedBefore(tok.block, post.resp.seq), stored: storedOp(post, tok.block)?.content ?? null,
    };
    if (!op) c.verdict = "UNEXPLAINED: the batch sent no text for the block";
    else if (op.base == null) c.verdict = "UNEXPLAINED: a set without a base replaced the text";
    else if (kind === "lost" && has(c.base) && !has(c.ours)) c.verdict = "UNEXPLAINED: the batch's own text left it out";
    else if (c.theirs == null) c.verdict = "UNEXPLAINED: no stored text before the batch";
    return c;
  };
  for (const [word, tok] of soak.tokens) {
    const n = counts.get(word) || 0;
    const expected = tok.private ? (soak.notes.get(tok.block).deleted ? 0 : 1) : tok.retired ? 0 : 1;
    if (n > 1) cases.push({ word, ...tok, kind: "twice", verdict: `UNEXPLAINED: there ${n} times` });
    else if (tok.private) {
      // a note's text never changes: the note's own checks above cover it
      if (n < expected && texts.has(tok.block)) cases.push({ word, ...tok, kind: "lost", verdict: "UNEXPLAINED: gone from its note" });
    } else if (n < expected) cases.push(caseFor(word, tok, "lost"));
    else if (n > expected) cases.push(caseFor(word, tok, "kept"));
  }
  const toReplay = cases.filter((c) => !c.verdict);
  replay(toReplay.map(({ base, ours, theirs, word }) => ({ base, ours, theirs, word }))).forEach((r, k) => {
    const c = toReplay[k];
    Object.assign(c, r);
    if (r.merged !== c.stored) c.verdict = "UNEXPLAINED: the stored text is not the merge";
    else if (!r.clean) c.verdict = "clash";
    else if (r.rewrite) c.verdict = "rewrite";
    else c.verdict = `UNEXPLAINED: a clean merge ${c.kind === "kept" ? "kept" : "lost"} it`;
  });
  const unexplained = cases.filter((c) => c.verdict.startsWith("UNEXPLAINED"));
  for (const c of unexplained) {
    failures.push(`token ${c.word} of tab ${c.owner} ${c.kind === "lost" ? "lost" : c.kind === "kept" ? "kept after its delete" : "doubled"}` +
      `${c.seq ? ` (seq ${c.seq}${c.tab != null ? `, tab ${c.tab}` : ""}${c.batch ? `, batch ${c.batch}` : ""})` : ""}: ${c.verdict.slice(13)}`);
  }

  // The op log's bound, and the server's health.
  const lastLog = soak.oplog.at(-1).rows;
  for (const page of pages) {
    const rows = lastLog[page.id]?.rows || 0;
    if (rows > limits.keep + limits.every) failures.push(`page ${page.id}'s op log holds ${rows} rows (KEEP_OPS ${limits.keep} + PRUNE_EVERY ${limits.every})`);
  }
  const fiveHundreds = Object.entries(soak.statuses).filter(([code]) => Number(code) >= 500);
  if (fiveHundreds.length) failures.push(`server errors: ${fiveHundreds.map(([code, n]) => `${n}× ${code}`).join(", ")}`);
  if (server.proc.exitCode != null) failures.push(`the server exited (${server.proc.exitCode})`);
  const serverLog = server.log().slice(logMark);
  const tracebacks = (serverLog.match(/Traceback \(most recent call last\)|Exception in ASGI application/g) || []).length;
  if (tracebacks) failures.push(`server.log has ${tracebacks} traceback(s)`);
  for (const bad of ["act threw", "remote ops failed to apply", "sabotage threw"]) if (soak.counts[bad]) failures.push(`${bad}: ${soak.counts[bad]}`);
  if (sabotage && !unexplained.some((c) => c.batch === sabotaged?.batch)) failures.push(`the sabotage batch ${sabotaged?.batch} was not named`);

  const report = buildReport(soak, { t0, tRun, seconds, windowS, clients, accounts: accounts.length, pages: pages.length, seed, faults, keepLive, quiesceMs, limits });
  report.failures = failures;
  report.cases = cases.map((c) => ({ verdict: c.verdict, token: c.word, kind: c.kind, owner: c.owner, page: c.page, block: c.block,
    seq: c.seq, tab: c.tab, client: c.client, batch: c.batch, base: c.base, ours: c.ours, theirs: c.theirs, stored: c.stored, merged: c.merged, clean: c.clean }));
  report.divergent = divergent.map((i) => ({ tab: i, page: soak.tabs[i].page, tab_tree: norm(soak.tabs[i].tree), server_tree: norm(serverTrees.get(soak.tabs[i].page)) }));
  report.sabotage = sabotaged;
  return report;
}

// ---------------------------------------------------------------------------
// The report

function buildReport(soak, { t0, tRun, seconds, windowS, clients, accounts, pages, seed, faults, keepLive, quiesceMs, limits }) {
  // fan-out: from the POST that got a seq to each peer's socket reading it,
  // neither side's network or socket down in between
  const sent = new Map();
  for (const post of soak.posts) if (post.resp?.seq) { const key = `${post.page}:${post.resp.seq}`; if (!sent.has(key)) sent.set(key, post); }
  const fanout = [];
  for (const a of soak.arrivals) {
    const post = sent.get(a.key);
    if (!post) continue;
    const to = soak.tabs[a.tab], from = soak.tabs[post.tab];
    if (soak.faulted(to, post.t, a.t) || (from && soak.faulted(from, post.t, a.t))) continue;
    fanout.push([post.t, a.t - post.t]);
  }
  const series = { ack: soak.metrics.ack, fanout, visible: soak.metrics.visible };
  const stats = (xs) => ({ n: xs.length, p50: pct(xs, 50), p95: pct(xs, 95), p99: pct(xs, 99), max: xs.length ? Math.max(...xs) : NaN });
  const windows = [];
  for (let a = t0; a < tRun - 1; a += windowS * 1000) {
    const b = Math.min(a + windowS * 1000, tRun);
    const pick = (xs) => xs.filter(([t]) => t >= a && t < b).map(([, v]) => v);
    const s = soak.samples.filter((x) => x.t <= b + 2500).at(-1) || {};
    const loop = soak.loops.find((l) => l.t >= b - 1) || {};
    windows.push({
      from: (a - t0) / 1000, to: (b - t0) / 1000,
      batches: soak.posts.filter((p) => p.t >= a && p.t < b && p.resp).length / ((b - a) / 1000),
      ack: stats(pick(series.ack)), fanout: stats(pick(series.fanout)), visible: stats(pick(series.visible)),
      rss: s.rss, pages_db: s.pages, wal: s.wal, loop_p99: loop.p99,
    });
  }
  const first = soak.samples[0], last = soak.samples.at(-1);
  return {
    config: { clients, accounts, pages, seconds, seed, faults, keep_live: keepLive },
    totals: {
      batches: soak.posts.filter((p) => p.resp).length,
      batches_per_s: soak.posts.filter((p) => p.resp).length / seconds,
      ack: stats(series.ack.map(([, v]) => v)), fanout: stats(series.fanout.map(([, v]) => v)), visible: stats(series.visible.map(([, v]) => v)),
      quiesce_ms: quiesceMs,
      counts: soak.counts, statuses: soak.statuses,
      loop: { p99: Math.max(...soak.loops.map((l) => l.p99)), max: Math.max(...soak.loops.map((l) => l.max)) },
      server: {
        rss_start: first?.rss, rss_peak: Math.max(...soak.samples.map((s) => s.rss)), rss_end: last?.rss,
        cpu_s: last?.cpu - first?.cpu, pages_db: [first?.pages, last?.pages], wal: [first?.wal, last?.wal], users_db: [first?.users, last?.users],
      },
      oplog: soak.oplog.at(-1).rows, limits,
      tokens: soak.tokens.size,
    },
    windows,
    samples: soak.samples.map((s) => ({ ...s, t: (s.t - t0) / 1000 })),
  };
}

// The leveling checks of a long run: memory and the database flat over its
// last third, and no window's fan-out p99 far past the first full window's.
export function levelingFailures(report) {
  const out = [];
  const T = report.config.seconds;
  const third = (k) => report.samples.filter((s) => s.t >= (k * T) / 3 && s.t <= ((k + 1) * T) / 3);
  const mid = third(1), last = third(2);
  if (mid.length && last.length) {
    const midRss = Math.max(...mid.map((s) => s.rss)), lastRss = Math.max(...last.map((s) => s.rss));
    if (lastRss > 1.1 * midRss) out.push(`server memory grew in the last third: ${mb(lastRss)} MB against ${mb(midRss)} MB`);
    const from = last[0].pages, to = last.at(-1).pages;
    if (to > from * 1.05) out.push(`pages.db grew ${(((to - from) / from) * 100).toFixed(1)} % in the last third`);
  } else out.push("too short a run to judge leveling");
  const full = report.windows.filter((w) => w.to - w.from >= 29);
  if (full.length > 1) {
    const limit = Math.max(3 * full[0].fanout.p99, 50);
    for (const w of full) if (w.fanout.p99 > limit) out.push(`fan-out p99 ${ms(w.fanout.p99)} ms in ${w.from}–${w.to} s, past ${ms(limit)} ms`);
  }
  return out;
}

export function printReport(report, log = console.log) {
  const t = report.totals;
  const row = (cells, widths) => cells.map((c, k) => String(c).padStart(widths[k])).join("  ");
  const widths = [9, 9, 15, 15, 15, 7, 9, 6, 7];
  log(row(["window s", "batch/s", "ack p50/95/99", "fan p50/95/99", "seen p50/95/99", "rss MB", "db+wal MB", "loop", "posts"], widths));
  for (const w of report.windows) {
    log(row([`${Math.round(w.from)}–${Math.round(w.to)}`, w.batches.toFixed(1),
      `${ms(w.ack.p50)}/${ms(w.ack.p95)}/${ms(w.ack.p99)}`, `${ms(w.fanout.p50)}/${ms(w.fanout.p95)}/${ms(w.fanout.p99)}`,
      `${ms(w.visible.p50)}/${ms(w.visible.p95)}/${ms(w.visible.p99)}`, mb(w.rss), `${mb(w.pages_db)}+${mb(w.wal)}`, ms(w.loop_p99), w.ack.n], widths));
  }
  const line = (name, s) => `${name.padEnd(9)} n ${s.n}  p50 ${ms(s.p50)}  p95 ${ms(s.p95)}  p99 ${ms(s.p99)}  max ${ms(s.max)} ms`;
  log("");
  log(`${report.config.clients} tabs (${report.config.accounts} accounts) on ${report.config.pages} pages for ${report.config.seconds} s, seed ${report.config.seed}` +
    `${report.config.faults ? ", faults" : ""}: ${t.batches} batches (${t.batches_per_s.toFixed(1)}/s), quiet after ${ms(t.quiesce_ms)} ms`);
  log(line("ack", t.ack));
  log(line("fan-out", t.fanout));
  log(line("visible", t.visible) + "  (with the 350 ms debounce, and text held while the reader types in that block)");
  const c = t.counts;
  log(`actions   ${["type token", "type word", "delete word", "retire token", "set property", "insert note", "delete note", "move block"].map((k) => `${k} ${c[k] || 0}`).join(", ")}`);
  log(`faults    socket drops ${c["fault: socket drop"] || 0}, outages ${c["fault: outage"] || 0}; reconnects ${c.reconnect || 0}; closes ` +
    `${Object.entries(c).filter(([k]) => k.startsWith("close ")).map(([k, n]) => `${k.slice(6)}×${n}`).join(" ") || "none"}`);
  log(`sync      catch-ups ${c["catch-up"] || 0}, 410s ${c["catch-up 410"] || 0}, refetches ${c.refetch || 0} (failed ${c["refetch failed"] || 0}), rejected notices ${c.rejected || 0}, ` +
    `conflicts ${Object.entries(c).filter(([k]) => k.startsWith("conflict ")).map(([k, n]) => `${k.slice(9)}×${n}`).join(" ") || "none"}`);
  log(`http      ${Object.entries(t.statuses).map(([k, n]) => `${k}×${n}`).join(" ")}`);
  log(`driver    event-loop delay p99 ${ms(t.loop.p99)} ms, max ${ms(t.loop.max)} ms${t.loop.p99 > 20 ? "  (past 20 ms: the latencies above carry it)" : ""}`);
  const s = t.server;
  log(`server    rss ${mb(s.rss_start)} → peak ${mb(s.rss_peak)} → ${mb(s.rss_end)} MB, cpu ${Number.isFinite(s.cpu_s) ? s.cpu_s.toFixed(1) : "-"} s; ` +
    `pages.db ${mb(s.pages_db[0])} → ${mb(s.pages_db[1])} MB, wal ${mb(s.wal[0])} → ${mb(s.wal[1])} MB, users.db ${mb(s.users_db[1])} MB`);
  log(`op log    ${Object.values(t.oplog).map((r) => `${r.rows} rows/${(r.bytes / 1024).toFixed(0)} KB`).join(", ")} (bound ${t.limits.keep} + ${t.limits.every} rows)`);
  const verdicts = {};
  for (const k of report.cases) verdicts[k.verdict] = (verdicts[k.verdict] || 0) + 1;
  log(`tokens    ${t.tokens} typed; ${Object.entries(verdicts).map(([v, n]) => `${v} ${n}`).join(", ") || "none lost"}` +
    "  (clash: a span both changed, the stored text stands; rewrite: words replaced in one save met typing beside them)");
  for (const f of report.failures) log(`FAIL      ${f}`);
}

// ---------------------------------------------------------------------------
// The collab-soak group: a short run with faults on a fixed seed, its
// correctness only (the speed and leveling are the command line's).

export async function collabSoakScenarios({ server, alice, bob, step, assert }) {
  const out = await server.manage("create-workspace", "Soak", "alice", "shared");
  const team = (out.match(/workspace (\S+)/) || [])[1];
  assert(team, `shared workspace id from: ${out}`);
  await server.manage("set-member", team, "bob", "editor");
  const aliceT = Object.assign(Object.create(Object.getPrototypeOf(alice)), alice, { ws: team });
  const bobT = Object.assign(Object.create(Object.getPrototypeOf(bob)), bob, { ws: team });
  const pages = await seedPages(aliceT, 2);

  await step("collab soak: 5 tabs on 2 pages for 20 s with faults converge and lose nothing", async () => {
    const report = await runSoak({ server, accounts: [aliceT, bobT], workspace: team, pages, clients: 5, seconds: 20, seed: 1, faults: true, keepLive: 10 });
    const t = report.totals;
    console.log(`    ${t.batches} batches, ${t.tokens} tokens, ${report.cases.length} lost by the merge's rules, ` +
      `${t.counts.reconnect || 0} reconnects, ${t.counts.refetch || 0} refetches, quiet after ${ms(t.quiesce_ms)} ms`);
    if (report.failures.length) {
      fs.writeFileSync(path.join(server.dir, "forensic.json"), JSON.stringify(report, null, 1));
      printReport(report, (s) => console.log(`    ${s}`));
    }
    assert(!report.failures.length, `${report.failures.join("; ")} (forensic.json in ${server.dir})`);
  });
}
