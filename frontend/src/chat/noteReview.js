// The agents' changes as one store every surface reads, in the words of a
// diff: what an agent proposes is drawn where it would land before anyone
// decides, and what it changed stays marked where it landed.
//
// - Asking (`asking`): a change waiting on its approval card is drawn in
//   place, dashed, with the card's own answers (Keep, Undo), which answer
//   the card: an edit as tracked changes against what the note says now, a
//   new note as a ghost row where it would go, a note to delete struck out,
//   a note to move marked with where it goes (editor/BlockTree.jsx,
//   editor/NoteReviewBar.jsx), and a highlight, a drawing, a text box or a
//   picture on its PDF page or page of paper (markup/ReviewLayer.jsx). The
//   card and the drawing are one question: either answer takes both away.
//   An assistant over MCP asks the same way (`remote`, read with the page's
//   changes: gamma/agent_asks.py): its question in its client and its
//   drawing here are one question too, whichever is answered first.
// - Made: the server's journal (gamma/agent_changes.py, /api/ai/changes)
//   holds every change an agent made, Gamma AI's in the chat and an
//   assistant's over MCP, for the account it was made for. A page's changes
//   (`pages`: those that wait for review and the kept ones whose mark
//   stands) are read while something on screen draws the page (`watch`:
//   its notes' strip, its PDF pages and pages of paper, a tab group's view
//   of it), whether or not its notes show, and again on the server's nudge
//   (the window event gamma:ai-changes); a reply's change on a page on
//   screen is drawn at once as it lands in this tab (`landed`). One that
//   waits for review (made without asking) shows unfolded, with Keep and
//   Undo. A kept one (kept on its card, or here) leaves a mark at its
//   note's edge, as a diff's gutter does, until its mark is cleared or its
//   time is up (the account's choice, `setMarkLife`: a minute unless set
//   otherwise; never, till cleared) — an unfolded one stays while it is
//   unfolded; the mark unfolds it (`toggle`) to the change, which Undo
//   still takes back. A press on the change's row in the chat unfolds it
//   the same way (`reveal`), from the reply's own record when the journal
//   no longer holds it, and that one goes as it folds. Several edits of one
//   block in a row are one change, from the text before the first to the
//   text after the last.
//
// `states` holds what was decided about each change this tab has heard of
// ("open", "kept", "undone"), for the reply's rows; one not known yet is read
// in a batch (`useChangeState`).
import { useEffect, useSyncExternalStore } from "react";

const OPEN = "open";
const KEPT = "kept";
const UNDONE = "undone";
export const CHANGE_STATES = { OPEN, KEPT, UNDONE };
const EMPTY = [];

// pages: page id → its journal rows, oldest first (each with `change_id`
// and `state`); revealed: block id → a change unfolded from a reply's row
// that the journal does not draw ({rows, undo}), while it is unfolded;
// changes: block id → the change it draws (`chains`); asking: approval id →
// the call waiting on its card; expanded: the block ids whose kept change
// is unfolded, while it has one; refusals: block id → {text, force, diff}
// of the Undo or the Keep that stopped there.
// `markLife`: how long a kept change's mark stays, in ms (0: till cleared).
let snapshot = {
  pages: new Map(), revealed: new Map(), changes: new Map(), states: new Map(), asking: new Map(),
  expanded: new Set(), refusals: new Map(), markLife: 60000,
};
// The clock marks time out by; a test hands its own. Its timer holds no
// process open where there is one (node).
let clock = {
  now: () => Date.now(),
  set: (fn, ms) => { const id = setTimeout(fn, ms); id?.unref?.(); return id; },
  clear: (id) => clearTimeout(id),
};
let expiry = null; // the timer of the next mark whose time is up
const listeners = new Set();
// The requests: `(path, init)` → the answer, throwing the API's error (its
// `status` and `data`). The app hands its own (app/App.jsx, apiJson), as a
// test does; none before then.
let request = () => Promise.reject(new Error("The review is not connected"));
const post = (path, body) => request(path, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body) });

function set(next) {
  snapshot = next;
  for (const listener of listeners) listener();
}

// The tools that put something on a page as one new block (a drawing, a
// text box, a picture, a page of paper, a highlight): their change is the
// block itself, drawn as it is, not a text.
const OBJECT_TOOLS = new Set(["draw", "add_text_box", "place_picture", "add_sheet", "highlight"]);

// What a change made to the notes, block by block, as a note draws it:
// [{blockId, pageId, type, before, after}], `type` "edit" (a text before and
// after), "add" (a new note's text), "move" or "object" (a placed block);
// none for a read, a failure, a change that changed nothing, a deletion (the
// note is gone) or a change of the library.
export function noteChanges(a) {
  if (!a || a.error || a.noop || a.reverted || !a.block_id || !a.revert) return EMPTY;
  const at = { pageId: a.page_id || "" };
  const r = a.revert;
  if (a.tool === "edit_block") {
    if (typeof r.before !== "string" || typeof r.after !== "string" || r.before === r.after) return EMPTY;
    return [{ ...at, blockId: a.block_id, type: "edit", before: r.before, after: r.after }];
  }
  if (a.tool === "create_block") {
    if (Array.isArray(r.blocks)) {
      return r.blocks.filter((b) => Array.isArray(b) && typeof b[0] === "string")
        .map(([id, , , content]) => ({ ...at, blockId: id, type: "add", before: "", after: content || "" }));
    }
    return typeof r.after === "string" ? [{ ...at, blockId: a.block_id, type: "add", before: "", after: r.after }] : EMPTY;
  }
  if (a.tool === "move_block") return [{ ...at, blockId: a.block_id, type: "move", before: "", after: "" }];
  if (OBJECT_TOOLS.has(a.tool)) return [{ ...at, blockId: a.block_id, type: "object", before: "", after: "" }];
  return EMPTY;
}

// Whether a change of a note is a text, drawn as one (`noteChanges`'s
// type: an edit, a new note's text).
export const isTextChange = (type) => type === "edit" || type === "add";

// `changes` with the row's changes added. A text change chains onto the
// block's change when it starts from the text that change left; a move or
// a placed block joins the block's change (its ids, so Undo takes it back
// too), as an edit of a placed block does (it stays that block); an edit
// after a move is drawn as the edit, the move's id kept. An edit that
// chains onto nothing (the block was typed in since) is drawn alone, the
// earlier steps that still wait for review kept in it, so Keep and Undo
// reach them. A change carries its `steps` (the rows, oldest first), `ids`
// (their change ids), its `state` (open while one step waits for review,
// else kept), `keptAt` (when its latest step was kept, on this tab's
// clock) and the `agent` that made its latest step.
export function withChange(changes, row) {
  const parts = noteChanges(row);
  if (!parts.length) return changes;
  const next = new Map(changes);
  const agent = row.agent?.name || "";
  for (const c of parts) {
    const prev = next.get(c.blockId);
    next.delete(c.blockId);
    const step = (base, extra = {}) => {
      const steps = [...base.steps, row];
      return { ...base, ...extra, steps, ids: [...new Set(steps.map((s) => s.change_id))], agent: agent || base.agent,
        state: steps.some((s) => s.state === OPEN) ? OPEN : KEPT, keptAt: Math.max(...steps.map((s) => s.keptAt || 0)) };
    };
    if (prev && isTextChange(c.type) && isTextChange(prev.type) && prev.after === c.before) {
      // back where it started: nothing to mark, unless a step still waits
      const joined = step(prev, { after: c.after });
      if (prev.before !== c.after || joined.state === OPEN) next.set(c.blockId, joined);
    } else if (prev && (!isTextChange(c.type) || prev.type === "object")) {
      next.set(c.blockId, step(prev));
    } else if (prev && prev.type === "move") {
      next.set(c.blockId, step(prev, { type: c.type, before: c.before, after: c.after }));
    } else {
      next.set(c.blockId, step({ ...c, steps: prev ? prev.steps.filter((s) => s.state === OPEN) : [], agent: "" }));
    }
  }
  return next;
}

// Every page's changes as the blocks draw them, less the kept ones whose
// mark's time is up (an unfolded one stays while it is unfolded), then those
// unfolded from a reply's row that the journal does not hold (`revealed`,
// only the unfolded ones: rebuilt); and when the next mark's time is up
// (Infinity: none).
function chains(pages, revealed, expanded, life) {
  let all = new Map();
  for (const rows of pages.values()) for (const row of rows) all = withChange(all, row);
  const now = clock.now();
  let next = Infinity;
  const changes = new Map();
  for (const [blockId, change] of all) {
    const ends = change.state === KEPT && life > 0 && !expanded.has(blockId) ? change.keptAt + life : Infinity;
    if (ends <= now) continue;
    next = Math.min(next, ends);
    changes.set(blockId, change);
  }
  for (const [blockId, shown] of revealed) {
    if (changes.has(blockId)) continue;
    let own = new Map();
    for (const row of shown.rows) own = withChange(own, row);
    const change = own.get(blockId);
    if (change) changes.set(blockId, { ...change, undo: shown.undo });
  }
  return { changes, next };
}

// The map or set as it was while the filter left nothing of it out, so a
// row that reads it renders nothing again.
const unchanged = (all, left) => (left.size === all.size ? all : left);

// `next` with its changes drawn again, and the timer set for the next mark
// whose time comes. Every path that changes what is drawn comes through
// here, so here a change unfolded from a reply's row goes once it is
// folded, and a block stays unfolded only while it has a change to show
// (one undone, cleared or gone leaves none for the next to start unfolded).
function rebuilt(next) {
  const revealed = unchanged(next.revealed, new Map([...next.revealed].filter(([blockId]) => next.expanded.has(blockId))));
  const { changes, next: ends } = chains(next.pages, revealed, next.expanded, next.markLife);
  const expanded = unchanged(next.expanded, new Set([...next.expanded].filter((blockId) => changes.has(blockId))));
  const refusals = new Map(next.refusals);
  for (const blockId of refusals.keys()) if (!changes.has(blockId)) refusals.delete(blockId);
  if (expiry) clock.clear(expiry);
  expiry = Number.isFinite(ends) ? clock.set(() => { expiry = null; set(rebuilt(snapshot)); }, ends - clock.now() + 20) : null;
  return { ...next, revealed, expanded, changes, refusals };
}

// `ids` were decided (`state`), or left by the server's word (unknown here:
// read again). A kept one stays, folded to its mark, until cleared.
function settle(ids, state = "") {
  if (!ids.length) return;
  const gone = new Set(ids);
  const pages = new Map();
  const now = clock.now();
  for (const [page, rows] of snapshot.pages) {
    pages.set(page, rows.flatMap((r) => (!gone.has(r.change_id) ? [r] : state === KEPT ? [{ ...r, state: KEPT, keptAt: now }] : [])));
  }
  const states = new Map(snapshot.states);
  for (const id of ids) if (state) states.set(id, state); else states.delete(id);
  const revealed = new Map([...snapshot.revealed].filter(([, shown]) => !shown.rows.some((r) => gone.has(r.change_id))));
  // folded to its mark, though the strip's stepping had unfolded it
  const expanded = state !== KEPT ? snapshot.expanded
    : new Set([...snapshot.expanded].filter((blockId) => !snapshot.changes.get(blockId)?.ids.some((id) => gone.has(id))));
  set(rebuilt({ ...snapshot, pages, states, revealed, expanded }));
}

// The ids whose state a row asked for, read together once the render is
// done; those being read now; and those whose read failed, each with when
// it may be read again (a brief outage is read again soon, a server that
// keeps failing is not asked on every render).
let wanted = new Set();
let reading = null;
const inflight = new Set();
const retryAt = new Map();
const RETRY_FIRST = 30000;
const RETRY_MOST = 300000;
let retryIn = RETRY_FIRST;
const unread = (id) => !snapshot.states.has(id) && !inflight.has(id) && !(retryAt.get(id) > clock.now());
function readStates() {
  const ids = [...wanted].filter(unread).slice(0, 500);
  wanted = new Set();
  reading = null;
  if (!ids.length) return;
  for (const id of ids) inflight.add(id);
  request(`/ai/changes?ids=${encodeURIComponent(ids.join(","))}`).then((answer) => {
    retryIn = RETRY_FIRST;
    const states = new Map(snapshot.states);
    // An id the journal no longer holds has no state to show: the row
    // then goes by the mark its conversation saved. One decided or read
    // with its page meanwhile keeps that newer word.
    for (const id of ids) {
      retryAt.delete(id);
      if (!states.has(id)) states.set(id, answer?.states?.[id] || "");
    }
    set({ ...snapshot, states });
  }, () => {
    const at = clock.now() + retryIn;
    retryIn = Math.min(retryIn * 2, RETRY_MOST);
    for (const id of ids) retryAt.set(id, at);
  }).finally(() => { for (const id of ids) inflight.delete(id); });
}

// The pages on screen, each with how many surfaces draw it (`watch`), and
// the pages whose last surface went in this task: dropped once it ends,
// unless one comes back meanwhile (a surface mounted again in the same
// render, a PDF's pages laid out anew), so their rows are not read again.
const watching = new Map();
const leaving = new Set();
// The window the server's nudges are heard on while a page is watched
// (none where there is no window: node).
let nudges = null;
function onNudge(e) {
  const pageId = e.detail?.pageId;
  if (pageId && watching.has(pageId)) noteReview.load(pageId);
}
function hearNudges(on) {
  const target = on && typeof window !== "undefined" && typeof window.addEventListener === "function" ? window : null;
  if (target === nudges) return;
  nudges?.removeEventListener("gamma:ai-changes", onNudge);
  target?.addEventListener("gamma:ai-changes", onNudge);
  nudges = target;
}
// The newest load sent (all pages), and the newest applied for each page:
// an answer older than one applied already is left (a burst of nudges
// sends several reads, whose answers can come in any order), as is one
// sent before its page was dropped.
let loadSeq = 0;
const applied = new Map();
// Whether an MCP question is about the page: the page it is drawn on (a
// move's, the note's page now).
const askOn = (ask, pageId) => ask.remote && (ask.preview.src_page_id || ask.preview.page_id) === pageId;

// Where a waiting call draws (`asking`): a note (an edit, a deletion, a
// move), a parent's place (a new note) or a surface (a PDF page by its
// page and number, a page of paper by its id).
const ON_BLOCK = new Set(["edit_block", "delete_block", "move_block"]);
const ON_SURFACE = new Set(["draw", "add_text_box", "place_picture", "highlight"]);
export const surfaceKey = (pageId, surface) => (typeof surface === "number" ? `${pageId}:${surface}` : String(surface || ""));
function askSurface(ask) {
  const p = ask.preview || {};
  if (!ON_SURFACE.has(ask.tool) || !p.shape?.kind) return "";
  return p.sheet_id ? p.sheet_id : p.pdf_page ? surfaceKey(p.page_id, p.pdf_page) : "";
}
// Where a placed block an agent made (a change of kind "object") sits on
// its surface, from the properties its revert record keeps: {surface (a
// `surfaceKey`), frame ([width, height] the box is measured in; null: the
// surface's own points), box ([x0, y0, x1, y1])}. A highlight and a drawing
// on a PDF page by their position, a text box and a picture by their frame,
// on a PDF page or on the page of paper they were made under; null for one
// that cannot be told so (a drawing on a page of paper: its strokes are in
// its file), which its row in the notes marks alone.
export function objectFrame(row) {
  const props = row?.revert?.props;
  if (!props || typeof props !== "object") return null;
  const pos = props.pdf_position;
  const r = pos?.boundingRect;
  if (r && pos.pageNumber && pos.width && pos.height) {
    return { surface: surfaceKey(row.page_id || "", pos.pageNumber), frame: [pos.width, pos.height], box: [r.x1, r.y1, r.x2, r.y2] };
  }
  const placed = props.text_box || props.picture;
  if (!placed || ![placed.x, placed.y, placed.w, placed.h].every(Number.isFinite)) return null;
  const surface = props.pdf_page ? surfaceKey(row.page_id || "", props.pdf_page) : String(row.revert.parent || "");
  return surface ? { surface, frame: null, box: [placed.x, placed.y, placed.x + placed.w, placed.y + placed.h] } : null;
}

// Whether a waiting call can be drawn where it would land.
function drawable(tool, p) {
  if (tool === "edit_block") return !!p.block_id && typeof p.text === "string";
  if (ON_BLOCK.has(tool)) return !!p.block_id;
  if (tool === "create_block") return !!p.parent_id && typeof p.text === "string";
  return !!askSurface({ tool, preview: p });
}

export const noteReview = {
  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot: () => snapshot,
  // How the store reaches the server: `fn(path, init)` (app/App.jsx; tests).
  setRequest(fn) { request = fn; },
  // The clock marks time out by: {now, set(fn, ms), clear(id)} (tests).
  setClock(next) { clock = next; },
  // How long a kept change's mark stays, in ms; 0 keeps it till cleared
  // (the account's choice: ReviewMarkLife, below, which app/App.jsx
  // mounts). The pages on screen are read again: a longer time than they
  // were read with brings back marks they left out.
  setMarkLife(ms) {
    const life = Math.max(0, Number(ms) || 0);
    if (life === snapshot.markLife) return;
    set(rebuilt({ ...snapshot, markLife: life }));
    for (const pageId of watching.keys()) noteReview.load(pageId);
  },
  // Something on screen draws the page's changes: the first to come reads
  // them, and while one stays they are read again on each of the server's
  // nudges about the page; once the last has gone they are dropped. Returns
  // what to call when it goes.
  watch(pageId) {
    if (!pageId) return () => {};
    const n = watching.get(pageId) || 0;
    watching.set(pageId, n + 1);
    hearNudges(true);
    if (!n && !leaving.delete(pageId)) noteReview.load(pageId);
    let on = true;
    return () => {
      if (!on) return;
      on = false;
      const left = (watching.get(pageId) || 1) - 1;
      if (left) { watching.set(pageId, left); return; }
      watching.delete(pageId);
      leaving.add(pageId);
      Promise.resolve().then(() => {
        if (!leaving.delete(pageId)) return; // watched again meanwhile
        noteReview.drop(pageId);
        if (!watching.size) hearNudges(false);
      });
    };
  },
  // The page's changes its note draws, read again. Those that went since
  // were decided elsewhere: their states are read again too.
  async load(pageId) {
    if (!pageId) return;
    const seq = ++loadSeq;
    let answer;
    const life = snapshot.markLife;
    try {
      answer = await request(`/ai/changes?page_id=${encodeURIComponent(pageId)}${life ? `&marks_for=${life / 1000}` : ""}`);
    } catch {
      return; // a viewer, the server away: nothing to draw
    }
    if (seq <= (applied.get(pageId) || 0)) return;
    applied.set(pageId, seq);
    // A kept one's time is the server's word of how long ago, on this
    // tab's clock: a clock that is off times nothing out early or late.
    const now = clock.now();
    const rows = (answer?.changes || []).map((r) => ({ ...r, change_id: r.id,
      keptAt: r.state === OPEN ? 0 : now - (Number(r.kept_ago) || 0) * 1000 }));
    const before = snapshot.pages.get(pageId) || [];
    const ids = new Set(rows.map((r) => r.change_id));
    const pages = new Map(snapshot.pages);
    if (rows.length) pages.set(pageId, rows); else pages.delete(pageId);
    const states = new Map(snapshot.states);
    for (const r of rows) states.set(r.change_id, r.state === OPEN ? OPEN : KEPT);
    for (const r of before) if (!ids.has(r.change_id)) states.delete(r.change_id);
    // What an assistant over MCP asks about on the page: the server's list
    // stands in for the one read before. Answered as a chat card is.
    const asking = new Map([...snapshot.asking].filter(([, ask]) => !askOn(ask, pageId)));
    for (const a of answer?.asks || []) {
      if (!a?.id || !drawable(a.tool, a.preview || {})) continue;
      asking.set(a.id, { id: a.id, tool: a.tool, perm: a.perm || "", preview: a.preview, agent: a.agent || "", remote: true,
        decide: (decision, note) => post(`/ai/approvals/${encodeURIComponent(a.id)}`, { decision, note: note || "" }) });
    }
    set(rebuilt({ ...snapshot, pages, states, asking }));
  },
  // A page left the screen: its changes and an assistant's questions about
  // it are read again when it comes back (a read on its way is left).
  drop(pageId) {
    applied.set(pageId, loadSeq);
    const asking = new Map([...snapshot.asking].filter(([, ask]) => !askOn(ask, pageId)));
    if (!snapshot.pages.has(pageId) && asking.size === snapshot.asking.size) return;
    const pages = new Map(snapshot.pages);
    pages.delete(pageId);
    set(rebuilt({ ...snapshot, pages, asking: unchanged(snapshot.asking, asking) }));
  },
  // An action of a reply streaming in this tab landed (ChatDock): drawn at
  // once, before the server's word comes — kept, folded to its mark, when
  // it was allowed on its card; waiting for review when it ran unasked. One
  // on a page off screen has its state alone: the page reads its changes
  // when it comes.
  landed(action) {
    const id = action?.change_id;
    if (!id) return;
    const state = action.approval ? KEPT : OPEN;
    const states = new Map(snapshot.states).set(id, state);
    const page = action.page_id || "";
    const rows = snapshot.pages.get(page) || [];
    if (!noteChanges(action).length || !watching.has(page) || rows.some((r) => r.change_id === id)) {
      set({ ...snapshot, states });
      return;
    }
    const pages = new Map(snapshot.pages);
    pages.set(page, [...rows, { ...action, id, state, at: new Date().toISOString(), agent: { name: "" },
      keptAt: state === KEPT ? clock.now() : 0 }]);
    set(rebuilt({ ...snapshot, pages, states }));
  },
  // A call waits on its approval card; `decide(decision, note)` answers it
  // ("once", "chat", "always" or "deny", with the line of what to do
  // instead) as the card does, and `labels` are the card's words for the
  // answers' titles: {permission, kind}. Drawn where it would land, when its
  // preview says where (a note edit's whole new text; a new note's parent,
  // place and text; a note to delete or move; a shape on a surface).
  ask(approval, decide, labels = {}) {
    const p = approval?.preview;
    if (!p || !drawable(approval.tool, p)) return;
    const asking = new Map(snapshot.asking);
    asking.set(approval.id, { id: approval.id, tool: approval.tool, perm: approval.perm || "", preview: p, decide,
      permission: labels.permission || "", kind: labels.kind || "" });
    set({ ...snapshot, asking });
  },
  // The card was answered, or its reply ended.
  settle(id) {
    if (!snapshot.asking.has(id)) return;
    const asking = new Map(snapshot.asking);
    asking.delete(id);
    set({ ...snapshot, asking });
  },
  // Unfold or fold a kept change's mark; one whose time is up goes as it
  // folds, as does one unfolded from a reply's row that the journal does
  // not hold.
  toggle(blockId, open = !snapshot.expanded.has(blockId)) {
    if (open === snapshot.expanded.has(blockId)) return;
    const expanded = new Set(snapshot.expanded);
    if (open) expanded.add(blockId); else expanded.delete(blockId);
    set(rebuilt({ ...snapshot, expanded }));
  },
  // Unfold one change, folding the others (the page's strip stepping).
  focus(blockId) {
    set(rebuilt({ ...snapshot, expanded: new Set(blockId ? [blockId] : []) }));
  },
  // A reply's row was pressed: unfold the change it names in its note. One
  // the journal does not draw (gone from it, or saved before it) is drawn
  // from the reply's own record, `undo({force})` taking it back as the row
  // does. One undone (the journal's word when it is known, else the
  // conversation's) no longer stands: there is nothing to unfold.
  reveal(action, { undo } = {}) {
    const state = action?.change_id ? snapshot.states.get(action.change_id) : "";
    if (state ? state === UNDONE : action?.reverted) return;
    const parts = noteChanges({ ...action, reverted: false });
    if (!parts.length) return;
    const blockId = parts[0].blockId;
    const expanded = new Set(snapshot.expanded).add(blockId);
    // One the journal holds, though its mark's time is up: unfolded, it shows.
    const held = [...snapshot.pages.values()].some((rows) => rows.some((r) => r.change_id && r.change_id === action.change_id));
    const known = snapshot.changes.get(blockId);
    if ((known && (!action.change_id || known.ids.includes(action.change_id))) || held) {
      set(rebuilt({ ...snapshot, expanded }));
      return;
    }
    const revealed = new Map(snapshot.revealed);
    revealed.set(blockId, { rows: [{ ...action, reverted: false, change_id: action.change_id || "", state: KEPT }], undo });
    set(rebuilt({ ...snapshot, revealed, expanded }));
  },
  // Keep changes (by id): kept in the journal, folded to their marks. One
  // the server did not keep (undone or kept elsewhere meanwhile) is what
  // the journal says: its state is read again, and its page with it.
  async keep(ids) {
    const list = [...new Set(ids)].filter(Boolean);
    if (!list.length) return;
    if (list.length === 1) {
      const answer = await post(`/ai/changes/${encodeURIComponent(list[0])}`, { decision: "keep" });
      settle(list, answer?.state || KEPT);
      return;
    }
    const done = new Set((await post("/ai/changes/keep", { ids: list }))?.kept || []);
    const rest = list.filter((id) => !done.has(id));
    const reread = [...snapshot.pages].filter(([, rows]) => rows.some((r) => rest.includes(r.change_id))).map(([page]) => page);
    settle([...done], KEPT);
    settle(rest);
    for (const pageId of reread) if (watching.has(pageId)) noteReview.load(pageId);
  },
  // Clear the marks of kept changes (by id): no longer drawn; the changes
  // stand, and their rows in the chat still undo them.
  async clear(ids) {
    const list = [...new Set(ids)].filter(Boolean);
    if (!list.length) return;
    await post("/ai/changes/clear", { ids: list });
    const gone = new Set(list);
    const pages = new Map();
    for (const [page, rows] of snapshot.pages) pages.set(page, rows.filter((r) => !gone.has(r.change_id)));
    set(rebuilt({ ...snapshot, pages }));
  },
  // Undo or redo one change (`decision` "undo" or "redo"): resolves once
  // done, throws the API's refusal (chat/aiRevert.js revertRefusal reads it).
  async decide(id, decision, { force = false } = {}) {
    const answer = await post(`/ai/changes/${encodeURIComponent(id)}`, { decision, force });
    settle([id], answer?.state || (decision === "redo" ? KEPT : UNDONE));
    return answer;
  },
  // Keep a block's change that waits for review. Resolves to null, or to
  // why it could not ({text, force: "", diff: null}), which every surface
  // drawing the block then shows on its bar, as an Undo's refusal.
  async keepBlock(blockId) {
    const change = snapshot.changes.get(blockId);
    if (!change || change.state !== OPEN) return null;
    noteReview.clearRefusal(blockId);
    try {
      await noteReview.keep(waitingIds(change));
      return null;
    } catch (err) {
      const why = { text: err?.message || String(err), force: "", diff: null };
      set({ ...snapshot, refusals: new Map(snapshot.refusals).set(blockId, why) });
      return why;
    }
  },
  // Take a block's change back, its steps newest first. Resolves to null, or
  // to the refusal of the step that stopped (`refused(err)` words it: {text,
  // force, diff}), which the block's bar then shows.
  async undoBlock(blockId, { force = false, refused } = {}) {
    const change = snapshot.changes.get(blockId);
    if (!change) return null;
    noteReview.clearRefusal(blockId);
    const steps = change.undo ? [() => change.undo({ force })]
      : [...change.ids].reverse().map((id) => () => noteReview.decide(id, "undo", { force }));
    for (const run of steps) {
      try {
        await run();
      } catch (err) {
        const why = refused ? refused(err) : { text: err?.message || String(err), force: "" };
        set({ ...snapshot, refusals: new Map(snapshot.refusals).set(blockId, why) });
        return why;
      }
    }
    if (change.undo) {
      const revealed = new Map(snapshot.revealed);
      revealed.delete(blockId);
      set(rebuilt({ ...snapshot, revealed }));
    }
    return null;
  },
  clearRefusal(blockId) {
    if (!snapshot.refusals.has(blockId)) return;
    const refusals = new Map(snapshot.refusals);
    refusals.delete(blockId);
    set({ ...snapshot, refusals });
  },
  // The page's changes: Keep All keeps those that wait for review, Undo All
  // takes them back newest first and stops at the first that stops (its
  // block's bar says why): resolves to that block's id, or "". Clear marks
  // clears the kept ones' marks.
  keepPage(pageId) {
    return noteReview.keep(pageChanges(pageId).filter((c) => c.state === OPEN).flatMap(waitingIds));
  },
  async undoPage(pageId, { refused } = {}) {
    const all = pageChanges(pageId).filter((c) => c.state === OPEN).sort((a, b) => (last(b) > last(a) ? 1 : -1));
    for (const change of all) {
      if (await noteReview.undoBlock(change.blockId, { refused })) return change.blockId;
    }
    return "";
  },
  clearPage(pageId) {
    const ids = pageChanges(pageId).filter((c) => c.state === KEPT && !c.undo).flatMap((c) => c.ids);
    const revealed = new Map([...snapshot.revealed].filter(([blockId]) => snapshot.changes.get(blockId)?.pageId !== pageId));
    if (revealed.size !== snapshot.revealed.size) set(rebuilt({ ...snapshot, revealed }));
    return noteReview.clear(ids);
  },
  // A row asked for a change's state: read with the others asked for now
  // (not again while it is being read, nor straight after a failed read).
  want(id) {
    if (!id || wanted.has(id) || !unread(id)) return;
    wanted.add(id);
    if (!reading) reading = Promise.resolve().then(readStates);
  },
};

const last = (change) => change.steps[change.steps.length - 1]?.at || "";
// The ids of a change's steps that wait for review: what its Keep keeps. A
// chain can hold a step kept already (on its card, say) before one made
// without asking; the server keeps only those that wait, and one sent that
// it did not keep would be read again as if its state were unknown.
const waitingIds = (change) => change.steps.filter((s) => s.state === OPEN).map((s) => s.change_id);
const pageChanges = (pageId) => [...snapshot.changes.values()].filter((c) => c.pageId === pageId);

// Watch the page's changes while the component shows it and `enabled` (the
// account's own: none for a viewer or through a share link): `watch`.
export function useWatchPage(pageId, enabled = true) {
  useEffect(() => (enabled && pageId ? noteReview.watch(pageId) : undefined), [pageId, enabled]);
}
// The account's time for a kept change's mark, in minutes (aiMarkMinutes;
// 0: till cleared), handed to the store: mounted once by app/App.jsx, it
// draws nothing.
export function ReviewMarkLife({ minutes = 1 }) {
  useEffect(() => { noteReview.setMarkLife(minutes * 60000); }, [minutes]);
  return null;
}

// What each row reads: its own entry only, so a change elsewhere renders
// nothing here.
const useSelect = (fn) => useSyncExternalStore(noteReview.subscribe, fn);
// How long a kept change's mark stays (ms; 0: till cleared).
export const useMarkLife = () => useSelect(() => snapshot.markLife);
// The change a block draws, whether its mark is unfolded, the refusal its
// Undo met.
export const useNoteChange = (blockId) => useSelect(() => snapshot.changes.get(blockId) || null);
export const useExpanded = (blockId) => useSelect(() => snapshot.expanded.has(blockId));
export const useRefusal = (blockId) => useSelect(() => snapshot.refusals.get(blockId) || null);
// The waiting calls by where they draw, built once per change of `asking`.
let askCache = { asking: null, byBlock: new Map(), byParent: new Map(), bySurface: new Map() };
function askIndex() {
  if (askCache.asking === snapshot.asking) return askCache;
  const byBlock = new Map(), byParent = new Map(), bySurface = new Map();
  for (const ask of snapshot.asking.values()) {
    const p = ask.preview;
    if (ON_BLOCK.has(ask.tool)) byBlock.set(p.block_id, ask);
    else if (ask.tool === "create_block") byParent.set(p.parent_id, [...(byParent.get(p.parent_id) || []), ask]);
    else {
      const key = askSurface(ask);
      if (key) bySurface.set(key, [...(bySurface.get(key) || []), ask]);
    }
  }
  askCache = { asking: snapshot.asking, byBlock, byParent, bySurface };
  return askCache;
}
// The call waiting on its card that a note draws on itself (an edit, a
// deletion, a move).
export const useNoteAsk = (blockId) => useSelect(() => askIndex().byBlock.get(blockId) || null);
// The new notes waiting on their cards under a parent, drawn as ghost rows.
export const useGhostAsks = (parentId) => useSelect(() => askIndex().byParent.get(parentId) || EMPTY);
// Whether a new note waits to go under the parent (its list of children
// shows, though it is empty).
export const useAsksUnder = () => useSelect(() => askIndex().byParent);
// The shapes waiting on their cards on a surface (`surfaceKey`).
export const useSurfaceAsks = (key) => useSelect(() => askIndex().bySurface.get(key) || EMPTY);
// The placed blocks agents made on each surface, with where they sit: built
// once per change of `changes`.
let objectCache = { changes: null, bySurface: new Map() };
function objectIndex() {
  if (objectCache.changes === snapshot.changes) return objectCache;
  const bySurface = new Map();
  for (const change of snapshot.changes.values()) {
    if (change.type !== "object") continue;
    const at = objectFrame(change.steps[0]);
    if (at) bySurface.set(at.surface, [...(bySurface.get(at.surface) || []), { change, ...at }]);
  }
  objectCache = { changes: snapshot.changes, bySurface };
  return objectCache;
}
// The placed blocks agents made on a surface, waiting for review or kept and
// marked: [{change, frame, box}].
export const useSurfaceChanges = (key) => useSelect(() => objectIndex().bySurface.get(key) || EMPTY);
// The block ids of a page's changes and how many wait for review (an
// object stable until they change), for the page's strip.
let pageCache = new Map();
export const usePageChanges = (pageId) => useSelect(() => {
  const list = pageChanges(pageId);
  const ids = list.map((c) => c.blockId);
  const open = list.filter((c) => c.state === OPEN).length;
  const cached = pageCache.get(pageId);
  if (cached && cached.open === open && cached.ids.length === ids.length && cached.ids.every((id, i) => id === ids[i])) return cached;
  const value = { ids, open };
  pageCache = new Map(pageCache).set(pageId, value);
  return value;
});
// What the person decided about a change ("open", "kept", "undone"; "" while
// unknown or when the journal no longer holds it), read when first asked.
export function useChangeState(id) {
  const state = useSelect(() => (id ? snapshot.states.get(id) : undefined));
  if (id && state === undefined) noteReview.want(id);
  return state || "";
}
