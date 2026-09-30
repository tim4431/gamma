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
import { inkProps, serializeInk } from "../ink/ink.js";
import { firstSheetId, isSheet, normalizePaper, sheetIdAfter } from "../notebook/notebook.js";
import { makeBlockId } from "../shared/model/blockModel.js";
import { applyLocal, childrenOf } from "./tree.js";

const TRIES = 5;
const uploadName = (url) => /\/api\/uploads\/([0-9A-Za-z_-]+\.[0-9A-Za-z]{1,12})/.exec(url || "")?.[1] || "";
const inkFiles = (host) => ({
  read: async (url) => { const n = uploadName(url); return n ? host.readInk(n) : null; },
  store: async (ink) => `/api/uploads/${await host.storeInk(ink)}`,
});
const noNulls = (props) => Object.fromEntries(Object.entries(props).filter(([, v]) => v !== null && v !== undefined));

// Apply `ops` to the page here: → the page's snapshot after them.
export async function editPage(host, pageId, ops) {
  for (let attempt = 1; ; attempt++) {
    const { snapshot, version } = await host.page(pageId);
    if (!snapshot) throw new Error(`no page ${pageId} here`);
    const { snapshot: next } = await applyLocal(snapshot, ops, inkFiles(host));
    if (await host.writeEdit(pageId, next, version)) return next;
    if (attempt >= TRIES) throw new Error(`page ${pageId} kept changing`);
  }
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

// The bytes a drawing is stored as (and hashed by), for a host that names
// files itself.
export const inkBytes = (ink) => serializeInk(ink);

// --- notebooks -----------------------------------------------------------------------

// A new notebook page with its first sheet (notebook.js firstSheetId, the
// id the browser gives it too). → the page's id.
export async function createNotebook(host, { id = makeBlockId(), title = "", folder = "", paper = null } = {}) {
  const sheet = normalizePaper(paper);
  const root = { parent: "root", position: "", content: title, props: { notebook: { sheet }, ...(folder ? { folder } : {}) } };
  const snapshot = { [id]: root, [firstSheetId(id)]: { parent: id, position: generateKeyBetween(null, null), content: "", props: { sheet } } };
  if (!(await host.writeEdit(id, snapshot, 0))) throw new Error(`a page ${id} exists here`);
  return id;
}

// A new sheet after the last, with the notebook's paper (or `paper`); its
// id follows from the sheet before it, like the browser's. → its id.
export async function addSheet(host, pageId, paper = null) {
  const { snapshot } = await host.page(pageId);
  if (!snapshot) throw new Error(`no page ${pageId} here`);
  const sheets = (childrenOf(snapshot).get(pageId) || []).filter((bid) => isSheet({ properties: snapshot[bid].props }));
  let id = sheets.length ? sheetIdAfter(sheets[sheets.length - 1]) : firstSheetId(pageId);
  if (snapshot[id]) id = makeBlockId();
  const sheet = normalizePaper(paper ?? snapshot[pageId].props.notebook?.sheet);
  await editPage(host, pageId, [{ op: "insert", id, parent: pageId, position: keyAtEnd(snapshot, pageId), content: "", props: { sheet } }]);
  return id;
}

export async function setSheetPaper(host, pageId, sheetId, paper) {
  return editPage(host, pageId, [{ op: "set", id: sheetId, props: { sheet: normalizePaper(paper) } }]);
}

// The paper new sheets get (the root's `notebook`).
export async function setNotebookPaper(host, pageId, paper) {
  const { snapshot } = await host.page(pageId);
  const nb = { ...(snapshot?.[pageId]?.props.notebook || {}), sheet: normalizePaper(paper) };
  return editPage(host, pageId, [{ op: "set", id: pageId, props: { notebook: nb } }]);
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

// A page made here (a text page): → its id.
export async function createPage(host, { id = makeBlockId(), title = "", folder = "" } = {}) {
  const snapshot = { [id]: { parent: "root", position: "", content: title, props: folder ? { folder } : {} } };
  if (!(await host.writeEdit(id, snapshot, 0))) throw new Error(`a page ${id} exists here`);
  return id;
}

export async function deletePage(host, pageId) {
  await host.deleteHere(pageId);
}
