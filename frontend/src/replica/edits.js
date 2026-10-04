// Edits made on the device (the iPad's editors, docs/dev/ipad.md): each
// reads the page from the replica's host (replica/round.js), applies ops
// the way a server would (tree.applyLocal: a text edited from an older base
// merges, a drawing edited from an older ink_url merges by stroke), writes
// it back marked as edited here, and the next round pushes it. The ops are
// the server's vocabulary (gamma/ops.py), so an edit made here is the same
// edit a browser makes there.
//
// Beyond round.js's host interface an editor needs:
//   writeEdit(id, snapshot, version) → new version, or 0 when the page
//                               changed after `version`; marks it edited here
//   deleteHere(id)              the page is deleted here (a tombstone the
//                               next round carries to the remote)
import { generateKeyBetween } from "fractional-indexing";
import { inkProps } from "../ink/ink.js";
import {
  firstSheetId, isSheet, newSheet, normalizePaper, paperBefore, sheetIdAfter, sheetsOf,
} from "../notebook/notebook.js";
import { diffTrees } from "../shared/model/blockOps.js";
import {
  findBlock, indentBlock, insertChild, insertSibling, makeBlockId, moveSibling, outdentBlock, removeBlockTree,
} from "../shared/model/blockModel.js";
import { inkFiles } from "./round.js";
import { applyLocal, childrenOf, treeOf, treeOrder } from "./tree.js";

const TRIES = 5;
const noNulls = (props) => Object.fromEntries(Object.entries(props).filter(([, v]) => v !== null && v !== undefined));

// Apply `ops` to the page here: → {before, after}, the snapshot they were
// applied to and the one after them.
async function applyHere(host, pageId, ops) {
  for (let attempt = 1; ; attempt++) {
    const { snapshot, version } = await host.page(pageId);
    if (!snapshot) throw new Error(`no page ${pageId} here`);
    const { snapshot: next } = await applyLocal(snapshot, ops, inkFiles(host));
    if (await host.writeEdit(pageId, next, version)) return { before: snapshot, after: next };
    if (attempt >= TRIES) throw new Error(`page ${pageId} kept changing`);
  }
}

// Apply `ops` to the page here: → the page's snapshot after them.
export async function editPage(host, pageId, ops) {
  return (await applyHere(host, pageId, ops)).after;
}

// The key after the last child of `parent`.
function keyAtEnd(snapshot, parent) {
  const kids = childrenOf(snapshot).get(parent) || [];
  const last = kids.length ? snapshot[kids[kids.length - 1]].position || null : null;
  return generateKeyBetween(last, null);
}

// --- handwriting -------------------------------------------------------------------

// The browser's ink flush (app/App.jsx flushInk), on the device: the file
// stored as the bytes every client writes (ink.js serializeInk) under their
// hash, the block's properties derived from it (ink.js inkProps), and the
// file the strokes were drawn onto as the base, so a drawing someone else
// changed meanwhile is merged by stroke. A new group is inserted — under
// `parent` (a PDF page's groups at the page's top level, a notebook
// sheet's under the sheet); a group erased empty is deleted unless its
// block holds a caption or notes. → the ink_url the block names now (the
// merged file when there was a merge), "" when it was deleted.
export async function saveInk(host, pageId, { blockId, ink, baseUrl = "", parent = null }) {
  const { snapshot } = await host.page(pageId);
  if (!snapshot) throw new Error(`no page ${pageId} here`);
  const existing = snapshot[blockId];
  if (!ink.strokes.length) {
    const hasNotes = existing && ((existing.content || "").trim() || (childrenOf(snapshot).get(blockId) || []).length);
    if (!existing) return "";
    if (!hasNotes) { await editPage(host, pageId, [{ op: "delete", id: blockId }]); return ""; }
  }
  const url = `/api/uploads/${await host.storeInk(ink)}`;
  const props = inkProps(ink, url);
  const ops = existing
    ? [{ op: "set", id: blockId, props, base_props: { ink_url: baseUrl || "" } }]
    : [{ op: "insert", id: blockId, parent: parent || pageId, position: keyAtEnd(snapshot, parent || pageId), content: "",
      props: noNulls(props) }];
  const after = await editPage(host, pageId, ops);
  return after[blockId]?.props.ink_url || "";
}

// --- sheets --------------------------------------------------------------------------

// A new notebook: a page with one sheet (notebook.js firstSheetId, the id
// the browser gives it too), filed in `folders` (folder block ids). → the
// page's id.
export async function createNotebook(host, { id = makeBlockId(), title = "", folders = [], paper = null } = {}) {
  const first = newSheet(firstSheetId(id), paper);
  const root = { parent: "root", position: "", content: title, props: folders.length ? { folders } : {} };
  const snapshot = { [id]: root,
    [first.id]: { parent: id, position: generateKeyBetween(null, null), content: "", props: first.properties } };
  if (!(await host.writeEdit(id, snapshot, 0))) throw new Error(`a page ${id} exists here`);
  return id;
}

// A new sheet, as the browser adds one (app/App.jsx addSheetAfter): right
// after block `after`, among its siblings, else after the page's last
// sheet, else at the page's end. After a sheet its id follows from that
// sheet's (sheetIdAfter; the first at the end: firstSheetId), so two
// devices adding "the page after this one" add one page, and `once` adds
// none when that page is there already. Its paper is `paper`, else that of
// the page nearest before it; it starts folded (notebook.js newSheet).
// → its id.
export async function addSheet(host, pageId, { paper = null, after = null, once = false } = {}) {
  const { snapshot } = await host.page(pageId);
  if (!snapshot) throw new Error(`no page ${pageId} here`);
  if (after && !snapshot[after]) throw new Error(`no block ${after} here`);
  const tree = treeOf(snapshot, pageId);
  const anchor = after || sheetsOf(tree).at(-1)?.id || null;
  let id = !anchor ? firstSheetId(pageId) : isSheet({ properties: snapshot[anchor].props }) ? sheetIdAfter(anchor) : makeBlockId();
  if (snapshot[id]) {
    if (once) return id;
    id = makeBlockId();
  }
  let parent = pageId, position = keyAtEnd(snapshot, pageId);
  if (anchor) {
    parent = snapshot[anchor].parent;
    const kids = childrenOf(snapshot).get(parent) || [];
    const next = kids[kids.indexOf(anchor) + 1];
    const a = snapshot[anchor].position || null, b = next ? snapshot[next].position || null : null;
    position = a !== null && b !== null && a >= b ? generateKeyBetween(a, null) : generateKeyBetween(a, b);
  }
  const sheet = newSheet(id, paper ?? paperBefore(tree, anchor));
  await editPage(host, pageId, [{ op: "insert", id, parent, position, content: "", props: sheet.properties }]);
  return id;
}

export async function setSheetPaper(host, pageId, sheetId, paper) {
  return editPage(host, pageId, [{ op: "set", id: sheetId, props: { sheet: normalizePaper(paper) } }]);
}

// --- text ------------------------------------------------------------------------------

// A block's text, edited from `base` (the text the editor showed): merged
// into a text that moved on meanwhile.
export async function setText(host, pageId, blockId, content, base) {
  return editPage(host, pageId, [{ op: "set", id: blockId, content, ...(base !== undefined ? { base } : {}) }]);
}

export async function renamePage(host, pageId, title, base) {
  return setText(host, pageId, pageId, title, base);
}

// A new note at the end of `parent` (the page by default). → its id.
export async function addNote(host, pageId, { parent = null, content = "", id = makeBlockId() } = {}) {
  const { snapshot } = await host.page(pageId);
  if (!snapshot) throw new Error(`no page ${pageId} here`);
  await editPage(host, pageId, [{ op: "insert", id, parent: parent || pageId, position: keyAtEnd(snapshot, parent || pageId), content, props: {} }]);
  return id;
}

export async function deleteBlock(host, pageId, blockId) {
  return editPage(host, pageId, [{ op: "delete", id: blockId }]);
}

// --- the outline -----------------------------------------------------------------------

// The editing bar's outline edits (docs/dev/ipad.md "Editing the notes"),
// made the browser's way: shared/model/blockModel.js's own change of the
// page's tree, sent as the ops the browser sends for it (blockOps.js
// diffTrees: the fewest moves, each keyed between its new neighbours).
// → its undo (`restore` makes it), or null when it changes nothing (the
// first block indented, the last one moved down).
//
// An undo says where each block the edit moved, made or took away stood
// before, among its neighbours rather than by its key, so its keys are
// worked out when it runs, as the browser's undo works them out
// (editor/blockHistory.js planRestore): a round may have re-keyed a block
// since (the server re-keys an insert whose key a sibling has). Each
// placement is {id, parent, after: the sibling it followed, null for the
// first; node: the block and what it holds, when the edit took it away},
// or {id, gone: true} for a block the edit made.
async function reshape(host, pageId, change) {
  const { snapshot } = await host.page(pageId);
  if (!snapshot) throw new Error(`no page ${pageId} here`);
  const tree = treeOf(snapshot, pageId);
  const next = change(tree);
  if (next === tree) return null;
  const ops = diffTrees(tree, next, pageId, new Map());
  if (!ops.length) return null;
  const { before, after } = await applyHere(host, pageId, ops);
  return placementsOf(before, after, pageId, new Set(ops.map((op) => op.id)));
}

export const indent = (host, pageId, blockId) => reshape(host, pageId, (tree) => indentBlock(tree, blockId));
export const outdent = (host, pageId, blockId) => reshape(host, pageId, (tree) => outdentBlock(tree, blockId));
// One step up (dir -1) or down (+1) among its siblings.
export const moveBlock = (host, pageId, blockId, dir) => reshape(host, pageId, (tree) => moveSibling(tree, blockId, dir));
// A new empty note `id` right after block `after`, among its siblings (the
// browser's Enter).
export const addNoteAfter = (host, pageId, after, id) =>
  reshape(host, pageId, (tree) => insertSibling(tree, after, { id, content: "", properties: {}, children: [] }, true));

// An outline edit's undo, or an undo's redo. → the one that takes it back.
export const restore = (host, pageId, placements) => reshape(host, pageId, (tree) => placed(tree, pageId, placements));

// Where the blocks `ids` stood in `before`, in its order, so a block put
// back after a sibling finds that sibling already back.
function placementsOf(before, after, pageId, ids) {
  const kids = childrenOf(before);
  const out = [];
  for (const id of treeOrder(before, pageId)) {
    if (!ids.has(id)) continue;
    const { parent, content, props } = before[id];
    const siblings = kids.get(parent);
    const i = siblings.indexOf(id);
    out.push({ id, parent, after: i > 0 ? siblings[i - 1] : null,
      ...(id in after ? {} : { node: { id, content, properties: props, children: treeOf(before, id) } }) });
  }
  for (const id of ids) if (!(id in before)) out.push({ id, gone: true });
  return out;
}

// `tree` with each block placed as `placements` say. One whose parent is
// gone, or is now inside the block itself, stays where it is.
function placed(tree, pageId, placements) {
  let next = tree;
  for (const p of placements) {
    const here = findBlock(next, p.id);
    if (p.gone) { if (here) next = removeBlockTree(next, p.id); continue; }
    const node = here || p.node;
    const parent = p.parent === pageId ? null : findBlock(next, p.parent);
    if (!node || (p.parent !== pageId && (!parent || findBlock([node], p.parent)))) continue;
    if (here) next = removeBlockTree(next, p.id);
    const siblings = p.parent === pageId ? next : findBlock(next, p.parent).children || [];
    if (p.after && siblings.some((b) => b.id === p.after)) next = insertSibling(next, p.after, node, true);
    else next = p.parent === pageId ? [node, ...next] : insertChild(next, p.parent, node, false);
  }
  return next;
}

// A page made here (a text page), filed in `folders` (folder block ids):
// → its id.
export async function createPage(host, { id = makeBlockId(), title = "", folders = [] } = {}) {
  const snapshot = { [id]: { parent: "root", position: "", content: title, props: folders.length ? { folders } : {} } };
  if (!(await host.writeEdit(id, snapshot, 0))) throw new Error(`a page ${id} exists here`);
  return id;
}

export async function deletePage(host, pageId) {
  await host.deleteHere(pageId);
}
