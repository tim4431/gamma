// The agents' changes as one store every surface reads, in the words of a
// diff, and the account's way of taking them (Overleaf's Editing /
// Reviewing, Word's Track Changes).
//
// - The mode (`reviewing`, the account pref "ai-review"): in Editing (false,
//   the default) an AI edit goes through as the permissions say, asking
//   first where they ask (on the chat's approval card, or in an
//   assistant's own question over MCP, as the connection's switch says),
//   and is journaled kept. Reviewing (true) makes an edit that can be
//   drawn where it lands (a note written, added, moved or deleted, a
//   highlight, a drawing, a text box, a picture, a page of paper) at once,
//   without asking, as a suggestion: journaled open, until it is accepted
//   or rejected. The server reads the same pref (gamma/agent_changes.py
//   reviewing); editor/ReviewMode.jsx switches it.
// - Suggestions: the server's journal (gamma/agent_changes.py,
//   /api/ai/changes) holds every change an agent made, Gamma AI's in the
//   chat and an assistant's over MCP, for the account it was made for; a
//   page's open ones (`pages`) are read while something on screen draws the
//   page (`watch`: its notes' header, its PDF pages and pages of paper, a
//   tab group's view of it), whether or not its notes show, and again on
//   the server's nudge (the window event gamma:ai-changes); a reply's
//   suggestion on a page on screen is drawn at once as it lands in this tab
//   (`landed`). Each is drawn where it landed until it is decided, however
//   the mode stands since: an edit as tracked changes, a new note as new, a
//   deleted note struck through at its old place, a move tinted
//   (editor/BlockTree.jsx), a placed block framed on its PDF page or page
//   of paper (markup/ReviewLayer.jsx), each with Accept and Reject
//   (editor/ReviewButtons.jsx). Accept keeps it in the journal, Reject
//   undoes it; either way it goes from here.
// - Shown (`shown`): a press on a change's row in the chat shows that one
//   change where it landed, from the reply's own record, until it is
//   hidden (Esc, a press outside it, its Hide) or decided; one at a time. A
//   suggestion is drawn already: the press lands on it.
//
// Several edits of one block in a row are one change, from the text before
// the first to the text after the last. `states` holds what was decided
// about each change this tab has heard of ("open", "kept", "undone"), for
// the reply's rows; one not known yet is read in a batch (`useChangeState`).
import { useEffect, useSyncExternalStore } from "react";

const OPEN = "open";
const KEPT = "kept";
const UNDONE = "undone";
export const CHANGE_STATES = { OPEN, KEPT, UNDONE };
// Whether an action's change was taken back: the journal's word (`state`,
// "" while unknown or no longer held), else the mark its conversation
// saved.
export const isUndone = (action, state) => (state ? state === UNDONE : !!action?.reverted);
const EMPTY = [];

// pages: page id → its suggestions' journal rows, oldest first (each with
// `change_id`); shown: the change shown from a reply's row ({blockId, ids,
// rows}) while it shows, or null; changes: block id → the change it draws
// (`chains`); refusals: block id → {text, force, diff} of the Reject or the
// Accept that stopped there; reviewing: the account's mode.
let snapshot = {
  pages: new Map(), shown: null, changes: new Map(), states: new Map(), refusals: new Map(), reviewing: false,
};
// The clock a failed read waits by; a test hands its own.
const SYSTEM_CLOCK = { now: () => Date.now() };
let clock = SYSTEM_CLOCK;
const listeners = new Set();
// The requests: `(path, init)` → the answer, throwing the API's error (its
// `status` and `data`). The app hands its own (app/App.jsx, apiJson), as a
// test does; none before then.
let request = () => Promise.reject(new Error("The review is not connected"));
const send = (method, path, body) => request(path, { method, headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body) });
const post = (path, body) => send("POST", path, body);

function set(next) {
  snapshot = next;
  hearHides(!!next.shown);
  for (const listener of listeners) listener();
}

// While a change shown from a reply's row shows, Esc or a press outside it
// hides it, wherever it is drawn (a note's row, a deleted note's struck
// row, a frame on a PDF page or a page of paper, with the notes shown or
// not): one listener on the document while one shows (none where there is
// no document: node). What draws it carries `data-review-shown`; a press
// within the box of one counts as on it (a frame takes no pointer, the page
// under it does), and every change's buttons (`.reviewBtns`) keep their
// presses.
const SHOWN_ATTR = "data-review-shown";
let hides = null;
const within = (el, x, y) => {
  const r = el.getBoundingClientRect();
  return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
};
function onHideKey(e) {
  if (e.key === "Escape") noteReview.hide();
}
function onHidePress(e) {
  if (e.target?.closest?.(`.reviewBtns, [${SHOWN_ATTR}]`)) return;
  if ([...(hides?.querySelectorAll(`[${SHOWN_ATTR}]`) || [])].some((el) => within(el, e.clientX, e.clientY))) return;
  noteReview.hide();
}
function hearHides(on) {
  const target = on && typeof document !== "undefined" && typeof document.addEventListener === "function" ? document : null;
  if (target === hides) return;
  hides?.removeEventListener("keydown", onHideKey);
  hides?.removeEventListener("pointerdown", onHidePress);
  target?.addEventListener("keydown", onHideKey);
  target?.addEventListener("pointerdown", onHidePress);
  hides = target;
}
// The attribute what draws the shown change carries, so a press on it keeps
// it shown: spread on its element (none for a suggestion).
export const shownMark = (change) => (change?.shown ? { [SHOWN_ATTR]: "" } : null);

// The tools that put something on a page as one new block (a drawing, a
// text box, a picture, a page of paper, a highlight): their change is the
// block itself, drawn as it is, not a text.
const OBJECT_TOOLS = new Set(["draw", "add_text_box", "place_picture", "add_sheet", "highlight"]);
// A block row of a revert record (create_block's paste, delete_block's
// subtree: gamma/ai_tools/notes.py): [id, parent_id, position, content,
// properties], a parent before its children.
const blockRows = (rows) => (Array.isArray(rows) ? rows.filter((b) => Array.isArray(b) && typeof b[0] === "string") : EMPTY);

// What a change made to the notes, block by block, as a note draws it:
// [{blockId, pageId, type, before, after}], `type` "edit" (a text before and
// after), "add" (a new note's text), "move", "object" (a placed block) or
// "delete" (the note that went, its text `before`, with its `parentId`, its
// sibling key `position`, how many notes went with it, `count`, and their
// ids, `under`); none for a read, a failure, a change that changed nothing,
// a deletion too large to record or a change of the library.
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
      return blockRows(r.blocks).map(([id, , , content]) => ({ ...at, blockId: id, type: "add", before: "", after: content || "" }));
    }
    return typeof r.after === "string" ? [{ ...at, blockId: a.block_id, type: "add", before: "", after: r.after }] : EMPTY;
  }
  if (a.tool === "delete_block") {
    const [top, ...under] = blockRows(r.blocks);
    if (!top) return EMPTY;
    return [{ ...at, blockId: top[0], type: "delete", before: top[3] || "", after: "", parentId: top[1] || "",
      position: typeof top[2] === "string" ? top[2] : "", count: under.length, under: under.map((b) => b[0]) }];
  }
  if (a.tool === "move_block") return [{ ...at, blockId: a.block_id, type: "move", before: "", after: "" }];
  if (OBJECT_TOOLS.has(a.tool)) return [{ ...at, blockId: a.block_id, type: "object", before: "", after: "" }];
  return EMPTY;
}

// Whether a change of a note is a text, drawn as one (`noteChanges`'s
// type: an edit, a new note's text).
export const isTextChange = (type) => type === "edit" || type === "add";

const byAt = (x, y) => (x.at > y.at ? 1 : x.at < y.at ? -1 : 0);
// A change with its `steps` (the rows, oldest first) summed up: `ids`
// (their change ids), its `state` (open while one step waits for review,
// else kept), `at` (when its latest step was made) and the `agent` that
// made its latest step, by name, with its `stamp` (gamma/agents.py stamp:
// chat/agents.js stampLabel words it).
function summed(change, row) {
  const name = row.agent?.name || "";
  return { ...change, ids: [...new Set(change.steps.map((s) => s.change_id))], agent: name || change.agent || "",
    stamp: name ? row.agent : change.stamp || null, state: change.steps.some((s) => s.state === OPEN) ? OPEN : KEPT,
    at: change.steps[change.steps.length - 1]?.at || "" };
}

// `changes` with the row's changes added. A text change chains onto the
// block's change when it starts from the text that change left; a move or
// a placed block joins the block's change (its ids, so Reject takes it back
// too), as an edit of a placed block does (it stays that block); an edit
// after a move is drawn as the edit, the move's id kept. An edit that
// chains onto nothing (the block was typed in since) is drawn alone, the
// earlier steps that still wait for review kept in it, so Accept and
// Reject reach them. A deletion takes the block's change into it, and the
// changes of the notes that went with it, which no row draws now.
export function withChange(changes, row) {
  const parts = noteChanges(row);
  if (!parts.length) return changes;
  const next = new Map(changes);
  for (const c of parts) {
    const prev = next.get(c.blockId);
    next.delete(c.blockId);
    const step = (base, extra = {}) => summed({ ...base, ...extra, steps: [...base.steps, row] }, row);
    if (c.type === "delete") {
      const inner = (c.under || EMPTY).flatMap((id) => {
        const gone = next.get(id);
        next.delete(id);
        return gone ? gone.steps : EMPTY;
      });
      next.set(c.blockId, summed({ ...prev, ...c, steps: [...(prev?.steps || EMPTY), ...inner, row].sort(byAt) }, row));
    } else if (prev && isTextChange(c.type) && isTextChange(prev.type) && prev.after === c.before) {
      // back where it started: nothing to draw, unless a step still waits
      const joined = step(prev, { after: c.after });
      if (prev.before !== c.after || joined.state === OPEN) next.set(c.blockId, joined);
    } else if (prev && (!isTextChange(c.type) || prev.type === "object")) {
      next.set(c.blockId, step(prev));
    } else if (prev && prev.type === "move") {
      next.set(c.blockId, step(prev, { type: c.type, before: c.before, after: c.after }));
    } else {
      next.set(c.blockId, step({ ...c, steps: prev ? prev.steps.filter((s) => s.state === OPEN) : [], agent: "", stamp: null }));
    }
  }
  return next;
}

// Every page's suggestions as the blocks draw them, then the change shown
// from a reply's row (`shown: true`) on each block it changed (every note
// of a paste) that draws no suggestion.
function chains(pages, shown) {
  let changes = new Map();
  for (const rows of pages.values()) for (const row of rows) changes = withChange(changes, row);
  if (shown) {
    let own = new Map();
    for (const row of shown.rows) own = withChange(own, row);
    for (const [blockId, change] of own) if (!changes.has(blockId)) changes.set(blockId, { ...change, shown: true });
  }
  return changes;
}

// The map as it was while the filter left nothing of it out, so a row that
// reads it renders nothing again.
const unchanged = (all, left) => (left.size === all.size ? all : left);

// `next` with its changes drawn again. Every path that changes what is
// drawn comes through here, so here the shown change goes once no block
// draws it (a suggestion is drawn there instead, or the journal holds it as
// one), and a refusal goes with its change.
function rebuilt(next) {
  const changes = chains(next.pages, next.shown);
  const shown = next.shown && [...changes.values()].some((c) => c.shown) ? next.shown : null;
  const refusals = unchanged(next.refusals, new Map([...next.refusals].filter(([blockId]) => changes.has(blockId))));
  return { ...next, shown, changes, refusals };
}

// `ids` were decided (`state`), or left by the server's word (unknown here:
// read again): no longer suggestions, and the shown change goes with them.
function settle(ids, state = "") {
  if (!ids.length) return;
  const gone = new Set(ids);
  const pages = new Map();
  for (const [page, rows] of snapshot.pages) pages.set(page, rows.filter((r) => !gone.has(r.change_id)));
  const states = new Map(snapshot.states);
  for (const id of ids) if (state) states.set(id, state); else states.delete(id);
  const shown = snapshot.shown?.ids.some((id) => gone.has(id)) ? null : snapshot.shown;
  set(rebuilt({ ...snapshot, pages, states, shown }));
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
// The mode's reads and writes: the newest read sent, the newest write sent
// and how many writes are on their way. A read's answer is left when a
// newer read was sent, or when a write was on its way as it was sent or
// was sent since (the server may have answered it before the write), so a
// switch is never undone by a read; a failed write puts the mode back
// unless a newer write was sent.
let modeRead = 0;
let modeWrite = 0;
let modeWriting = 0;
const MODE_PATH = "/prefs/ai-review";

// Where a surface is: a PDF page by its page and number, a page of paper
// by its id.
export const surfaceKey = (pageId, surface) => (typeof surface === "number" ? `${pageId}:${surface}` : String(surface || ""));
// Where a placed block an agent made (a change of kind "object") sits on
// its surface, from the properties its revert record keeps: {surface (a
// `surfaceKey`), frame ([width, height] the box is measured in; null: the
// surface's own points), box ([x0, y0, x1, y1])}. A highlight and a drawing
// on a PDF page by their position, a text box and a picture by their frame,
// on a PDF page or on the page of paper they were made under; null for one
// that cannot be told so (a drawing on a page of paper: its strokes are in
// its file), which its row in the notes draws alone.
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

// Where a deleted note's row goes among its parent's notes now (`list`, in
// order): before the first whose sibling key comes after its own, else
// last. A note with no key yet (made in this tab since) is passed over.
export function deletedAt(list, change) {
  const i = list.findIndex((b) => typeof b.position === "string" && b.position > change.position);
  return i < 0 ? list.length : i;
}

// Where a jump to a change lands (app/App.jsx landOnChange): its page
// (`pageId`) and the note to land on (`blockId`); for a deleted note, its
// struck row (`struck`, its id) under its parent (`blockId`, the page's
// root for a note at the top); for a text box or a picture on a PDF page,
// that page (`pdfPage`: such a block keeps no position on it; a
// highlight's and a drawing's are reached through their note). `change`:
// one the notes draw (`changes`'s), or a change's record (a reply's action,
// a journal row), whose first note it lands on.
export function changeLanding(change) {
  const c = change?.steps ? change : { ...(noteChanges({ ...change, reverted: false })[0]
    || { pageId: change?.page_id || "", blockId: change?.block_id || "" }), steps: [change] };
  if (c.type === "delete") return { pageId: c.pageId, blockId: c.parentId || c.pageId, struck: c.blockId, pdfPage: 0 };
  const props = c.type === "object" ? c.steps[0]?.revert?.props : null;
  const placed = props && !props.pdf_position && (props.text_box || props.picture);
  return { pageId: c.pageId, blockId: c.blockId, struck: "", pdfPage: placed ? Number(props.pdf_page) || 0 : 0 };
}

export const noteReview = {
  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot: () => snapshot,
  // How the store reaches the server: `fn(path, init)` (app/App.jsx; tests).
  setRequest(fn) { request = fn; },
  // The clock a failed read waits by: {now} (tests); none, the system's again.
  setClock(next = SYSTEM_CLOCK) { clock = next; },
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
  // The page's suggestions, read again (GET /api/ai/changes?page_id: the
  // account's open changes on it, oldest first). Those that went since were
  // decided elsewhere: their states are read again too.
  async load(pageId) {
    if (!pageId) return;
    const seq = ++loadSeq;
    let answer;
    try {
      answer = await request(`/ai/changes?page_id=${encodeURIComponent(pageId)}`);
    } catch {
      return; // a viewer, the server away: nothing to draw
    }
    if (seq <= (applied.get(pageId) || 0)) return;
    applied.set(pageId, seq);
    const rows = (answer?.changes || []).filter((r) => r?.id && (r.state || OPEN) === OPEN)
      .map((r) => ({ ...r, change_id: r.id, state: OPEN }));
    const before = snapshot.pages.get(pageId) || [];
    const ids = new Set(rows.map((r) => r.change_id));
    const pages = new Map(snapshot.pages);
    if (rows.length) pages.set(pageId, rows); else pages.delete(pageId);
    const states = new Map(snapshot.states);
    for (const r of rows) states.set(r.change_id, OPEN);
    for (const r of before) if (!ids.has(r.change_id)) states.delete(r.change_id);
    set(rebuilt({ ...snapshot, pages, states }));
  },
  // A page left the screen: its suggestions are read again when it comes
  // back (a read on its way is left).
  drop(pageId) {
    applied.set(pageId, loadSeq);
    if (!snapshot.pages.has(pageId)) return;
    const pages = new Map(snapshot.pages);
    pages.delete(pageId);
    set(rebuilt({ ...snapshot, pages }));
  },
  // An action of a reply streaming in this tab landed (ChatDock): its state
  // is the journal's (`change_state`: "open" for a suggestion, else kept),
  // and a suggestion on a page on screen is drawn at once, before the
  // server's word comes. One on a page off screen has its state alone: the
  // page reads its suggestions when it comes.
  landed(action) {
    const id = action?.change_id;
    if (!id) return;
    const state = action.change_state === OPEN ? OPEN : KEPT;
    const states = new Map(snapshot.states).set(id, state);
    const page = action.page_id || "";
    const rows = snapshot.pages.get(page) || [];
    if (state !== OPEN || !noteChanges(action).length || !watching.has(page) || rows.some((r) => r.change_id === id)) {
      set({ ...snapshot, states });
      return;
    }
    const pages = new Map(snapshot.pages);
    pages.set(page, [...rows, { ...action, id, change_id: id, state, at: new Date().toISOString(), agent: { name: "" } }]);
    set(rebuilt({ ...snapshot, pages, states }));
  },
  // A reply's row was pressed: show the change it names where it landed,
  // from the reply's own record; the change shown before goes. Returns the
  // block it is drawn on (its first note): a suggestion's, drawn already (or
  // once its page is read); "" for one undone (`isUndone`), which no longer
  // stands, or one no note draws.
  reveal(action) {
    const id = action?.change_id || "";
    if (isUndone(action, id ? snapshot.states.get(id) : "")) return "";
    const parts = noteChanges({ ...action, reverted: false });
    if (!parts.length) return "";
    const blockId = parts[0].blockId;
    const drawn = snapshot.changes.get(blockId);
    if (id && ((drawn && !drawn.shown && drawn.ids.includes(id)) || snapshot.states.get(id) === OPEN)) return blockId;
    const shown = { blockId, ids: id ? [id] : [], rows: [{ ...action, reverted: false, change_id: id, state: KEPT }] };
    set(rebuilt({ ...snapshot, shown }));
    return blockId;
  },
  // Stop showing the change shown from a reply's row.
  hide() {
    if (snapshot.shown) set(rebuilt({ ...snapshot, shown: null }));
  },
  // Accept changes (by id): kept in the journal, no longer suggestions. One
  // the server did not keep (undone or kept elsewhere meanwhile) is what
  // the journal says: its state is read again, and its page with it.
  async accept(ids) {
    const list = [...new Set(ids)].filter(Boolean);
    if (!list.length) return;
    if (list.length === 1) {
      await noteReview.decide(list[0], "keep");
      return;
    }
    const done = new Set((await post("/ai/changes/keep", { ids: list }))?.kept || []);
    const rest = list.filter((id) => !done.has(id));
    const reread = [...snapshot.pages].filter(([, rows]) => rows.some((r) => rest.includes(r.change_id))).map(([page]) => page);
    settle([...done], KEPT);
    settle(rest);
    for (const pageId of reread) if (watching.has(pageId)) noteReview.load(pageId);
  },
  // Keep, undo or redo one change (`decision` "keep", "undo" or "redo"):
  // settles by the answer's state, resolves once done, throws the API's
  // refusal (chat/aiRevert.js revertRefusal reads it).
  async decide(id, decision, { force = false } = {}) {
    const answer = await post(`/ai/changes/${encodeURIComponent(id)}`, { decision, force });
    settle([id], answer?.state || (decision === "undo" ? UNDONE : KEPT));
    return answer;
  },
  // Accept a block's suggestion. Resolves to null, or to why it could not
  // (`failure`), which every surface drawing the block then shows by its
  // buttons, as a Reject's refusal.
  async acceptBlock(blockId) {
    const change = snapshot.changes.get(blockId);
    if (!change || change.state !== OPEN) return null;
    noteReview.clearRefusal(blockId);
    try {
      await noteReview.accept(waitingIds(change));
      return null;
    } catch (err) {
      return refuse(blockId, failure(err));
    }
  },
  // Reject a block's change: its steps undone newest first. Resolves to
  // null, or to the refusal of the step that stopped (`refused(err)` words
  // it: {text, force, diff}; else `failure`), which the block's buttons
  // then show.
  async rejectBlock(blockId, { force = false, refused = failure } = {}) {
    const change = snapshot.changes.get(blockId);
    if (!change) return null;
    noteReview.clearRefusal(blockId);
    for (const id of [...change.ids].reverse()) {
      try {
        await noteReview.decide(id, "undo", { force });
      } catch (err) {
        return refuse(blockId, refused(err));
      }
    }
    return null;
  },
  clearRefusal(blockId) {
    if (!snapshot.refusals.has(blockId)) return;
    const refusals = new Map(snapshot.refusals);
    refusals.delete(blockId);
    set({ ...snapshot, refusals });
  },
  // The page's suggestions: Accept all keeps them, Reject all undoes them
  // newest first and stops at the first that stops (its block's buttons say
  // why): resolves to that block's id, or "".
  acceptPage(pageId) {
    return noteReview.accept(pageChanges(pageId).flatMap(waitingIds));
  },
  async rejectPage(pageId, { refused } = {}) {
    const all = pageChanges(pageId).sort((a, b) => (b.at > a.at ? 1 : -1));
    for (const change of all) {
      if (await noteReview.rejectBlock(change.blockId, { refused })) return change.blockId;
    }
    return "";
  },
  // A row asked for a change's state: read with the others asked for now
  // (not again while it is being read, nor straight after a failed read).
  want(id) {
    if (!id || wanted.has(id) || !unread(id)) return;
    wanted.add(id);
    if (!reading) reading = Promise.resolve().then(readStates);
  },
  // The account's mode, read again (GET /api/prefs/ai-review: true is
  // Reviewing); the server away leaves it as last known, as does an answer
  // a write may have overtaken.
  async loadMode() {
    const seq = ++modeRead;
    const since = modeWriting ? -1 : modeWrite;
    let answer;
    try {
      answer = await request(MODE_PATH);
    } catch {
      return;
    }
    if (seq !== modeRead || modeWriting || since !== modeWrite) return;
    if ((answer?.value === true) !== snapshot.reviewing) set({ ...snapshot, reviewing: answer?.value === true });
  },
  // Switch the mode: shown at once, written to the account (PUT
  // /api/prefs/ai-review), and put back as it was when the write fails,
  // whose error it throws.
  async setReviewing(on) {
    const was = snapshot.reviewing;
    const seq = ++modeWrite;
    modeWriting += 1;
    set({ ...snapshot, reviewing: !!on });
    try {
      await send("PUT", MODE_PATH, { value: !!on });
    } catch (err) {
      if (seq === modeWrite) set({ ...snapshot, reviewing: was });
      throw err;
    } finally {
      modeWriting -= 1;
    }
  },
};

// The ids of a change's steps that wait for review: what its Accept keeps.
// The server keeps only those that wait, and one sent that it did not keep
// would be read again as if its state were unknown.
const waitingIds = (change) => change.steps.filter((s) => s.state === OPEN).map((s) => s.change_id);
// A page's suggestions (the change shown from a reply's row is none).
const pageChanges = (pageId) => [...snapshot.changes.values()].filter((c) => c.pageId === pageId && c.state === OPEN);
// Why a block's Accept or Reject stopped, as its buttons show it: the
// error's words, nothing to force; held for the block, and returned.
const failure = (err) => ({ text: err?.message || String(err), force: "", diff: null });
function refuse(blockId, why) {
  set({ ...snapshot, refusals: new Map(snapshot.refusals).set(blockId, why) });
  return why;
}

// Watch the page's changes while the component shows it and `enabled` (the
// account's own: none for a viewer or through a share link): `watch`.
export function useWatchPage(pageId, enabled = true) {
  useEffect(() => (enabled && pageId ? noteReview.watch(pageId) : undefined), [pageId, enabled]);
}

// What each row reads: its own entry only, so a change elsewhere renders
// nothing here.
const useSelect = (fn) => useSyncExternalStore(noteReview.subscribe, fn);
// The change a block draws ({blockId, pageId, type, before, after, steps,
// ids, state, agent, stamp, at}, and `shown` for the one shown from a
// reply's row), and the refusal its Reject or Accept met.
export const useNoteChange = (blockId) => useSelect(() => snapshot.changes.get(blockId) || null);
export const useRefusal = (blockId) => useSelect(() => snapshot.refusals.get(blockId) || null);
// The account's mode (true: Reviewing).
export const useReviewing = () => useSelect(() => snapshot.reviewing);
// The changes by where they draw, built once per change of `changes`: the
// deleted notes by their parent, in their sibling keys' order, and the
// placed blocks on each surface with where they sit.
let drawCache = { changes: null, byParent: new Map(), bySurface: new Map() };
function drawIndex() {
  if (drawCache.changes === snapshot.changes) return drawCache;
  const byParent = new Map(), bySurface = new Map();
  for (const change of snapshot.changes.values()) {
    if (change.type === "delete") {
      byParent.set(change.parentId, [...(byParent.get(change.parentId) || []), change]);
    } else if (change.type === "object") {
      const at = objectFrame(change.steps[0]);
      if (at) bySurface.set(at.surface, [...(bySurface.get(at.surface) || []), { change, ...at }]);
    }
  }
  for (const list of byParent.values()) list.sort((a, b) => (a.position > b.position ? 1 : a.position < b.position ? -1 : 0));
  drawCache = { changes: snapshot.changes, byParent, bySurface };
  return drawCache;
}
// The deleted notes drawn under each parent (struck through at their
// places): a map of parent id → its list.
export const useDeletedByParent = () => useSelect(() => drawIndex().byParent);
// The placed blocks agents made on a surface that are drawn there (the
// suggestions, and the change shown from a reply's row): [{change, frame,
// box}].
export const useSurfaceChanges = (key) => useSelect(() => drawIndex().bySurface.get(key) || EMPTY);
// The block ids of a page's suggestions and how many there are (an object
// stable until they change), for the header's Editing / Reviewing menu.
let pageCache = new Map();
export const usePageChanges = (pageId) => useSelect(() => {
  const ids = pageChanges(pageId).map((c) => c.blockId);
  const cached = pageCache.get(pageId);
  if (cached && cached.ids.length === ids.length && cached.ids.every((id, i) => id === ids[i])) return cached;
  const value = { ids, open: ids.length };
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
