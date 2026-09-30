// The JavaScript the iPad app runs in JavaScriptCore (ipad/README.md): the
// web app's own ink codec and geometry (frontend/src/ink/ink.js), the
// notebook rules (frontend/src/notebook/notebook.js), and the replica —
// the mirror protocol and merges a device without a server runs
// (frontend/src/replica/*). Bundled by ipad/scripts/build-core.mjs into
// GammaIPad/Resources/gamma-core.js; Swift talks to it through
// GammaCore.swift in JSON strings only:
//
//   GammaCore.pure(name, argsJSON) → resultJSON           (synchronous)
//   GammaCore.run(name, argsJSON, host) → slot {done, value, error}
//       an async function over the device's host: the slot is settled once
//       the call returns, because every host method answers synchronously
//       (Swift runs each core on a thread of its own and blocks it on I/O)
//
// host.invoke(method, argsJSON) → '{"value": …}' | '{"error": "…"}' is the
// one native function; hostOf() turns it into round.js's host interface.

// --- what JavaScriptCore lacks ------------------------------------------------------
if (!globalThis.console) {
  const log = (...a) => globalThis.__gammaLog?.(a.map(String).join(" "));
  globalThis.console = { log, info: log, warn: log, error: log, debug() {} };
}
if (!globalThis.TextEncoder) {
  globalThis.TextEncoder = class TextEncoder {
    encode(text = "") {
      const out = [];
      for (const ch of String(text)) {
        let c = ch.codePointAt(0);
        if (c < 0x80) out.push(c);
        else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
        else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
        else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
      }
      return Uint8Array.from(out);
    }
  };
}
if (!globalThis.btoa) {
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  globalThis.btoa = (s) => {
    let out = "";
    for (let i = 0; i < s.length; i += 3) {
      const n = (s.charCodeAt(i) << 16) | ((s.charCodeAt(i + 1) || 0) << 8) | (s.charCodeAt(i + 2) || 0);
      out += A[(n >> 18) & 63] + A[(n >> 12) & 63] + (i + 1 < s.length ? A[(n >> 6) & 63] : "=") + (i + 2 < s.length ? A[n & 63] : "=");
    }
    return out;
  };
}

import { getStroke } from "perfect-freehand";
import * as ink from "../../frontend/src/ink/ink.js";
import * as notebook from "../../frontend/src/notebook/notebook.js";
import * as edits from "../../frontend/src/replica/edits.js";
import { syncRound } from "../../frontend/src/replica/round.js";
import { treeOf } from "../../frontend/src/replica/tree.js";
import * as views from "../../frontend/src/replica/views.js";
import { makeBlockId } from "../../frontend/src/shared/model/blockModel.js";

function hostOf(native) {
  const call = (method, ...args) => {
    const out = JSON.parse(native.invoke(method, JSON.stringify(args)));
    if (out.error) throw new Error(out.error);
    return out.value;
  };
  const config = call("config");
  const host = { config };
  for (const m of ["request", "getMeta", "setMeta", "localChanges", "acknowledge", "page", "writePage", "writeEdit", "removePage",
    "deleteHere", "state", "saveState", "pageOfBlock", "pullFiles", "pushFiles", "referencedFiles", "conflict", "note", "progress"]) {
    host[m] = async (...args) => call(m, ...args);
  }
  host.readInk = async (name) => { const text = call("readText", name); return text ? JSON.parse(text) : null; };
  host.storeInk = async (file) => call("storeText", ink.serializeInk(file), ".ink");
  return host;
}

// A pen stroke as the outline polygon every client fills (ink.js penOutline's
// points); a highlighter as its centre line and width.
function strokeGeometry(stroke) {
  const pts = ink.decodeStroke(stroke);
  if (stroke.tool === "highlighter") {
    return { kind: "line", points: pts.map((s) => [s.x, s.y]), width: stroke.size, color: stroke.color, opacity: stroke.opacity ?? 1 };
  }
  const outline = getStroke(pts.map((s) => [s.x, s.y, s.p ?? 0.5]), ink.outlineOptions(stroke));
  return { kind: "fill", points: outline, color: stroke.color, opacity: stroke.opacity ?? 1 };
}

// pdf.js's viewport transform at scale 1 (the frame Gamma's ink and
// highlights are in): PDF user space → [a, b, c, d, e, f] with
// vx = a·x + c·y + e, vy = b·x + d·y + f. `box`: the page's crop box
// [x0, y0, x1, y1]; `rotation`: its /Rotate.
function viewportTransform(box, rotation) {
  const [x0, y0, x1, y1] = box;
  const cx = (x1 + x0) / 2, cy = (y1 + y0) / 2;
  const r = ((rotation % 360) + 360) % 360;
  const [a, b, c, d] = r === 90 ? [0, 1, 1, 0] : r === 180 ? [-1, 0, 0, 1] : r === 270 ? [0, -1, -1, 0] : [1, 0, 0, -1];
  const ox = a === 0 ? Math.abs(cy - y0) : Math.abs(cx - x0);
  const oy = a === 0 ? Math.abs(cx - x0) : Math.abs(cy - y0);
  const width = a === 0 ? y1 - y0 : x1 - x0, height = a === 0 ? x1 - x0 : y1 - y0;
  return { transform: [a, b, c, d, ox - a * cx - c * cy, oy - b * cx - d * cy], width, height };
}

const PURE = {
  pageView: (snapshot, pageId) => views.pageView(snapshot, pageId),
  libraryRows: (roots) => views.libraryRows(roots),
  tree: (snapshot, pageId) => treeOf(snapshot, pageId),
  newInk: (page, w, h) => ink.newInk(page, w, h),
  newCanvasInk: (w, h) => ink.newCanvasInk(w, h),
  encodeStroke: (args) => ink.encodeStroke(args),
  appendStroke: (file, stroke) => ink.appendStroke(file, stroke),
  removeStrokes: (file, ids) => ink.removeStrokes(file, ids),
  hitStrokes: (file, x, y, r) => ink.hitStrokes(file, x, y, r),
  eraseAt: (file, x, y, r) => ink.eraseAt(file, x, y, r),
  mergeInk: (base, ours, theirs) => ink.mergeInk(base, ours, theirs),
  inkTimeline: (file) => ink.inkTimeline(file),
  inkBounds: (file) => ink.inkBounds(file),
  strokeBounds: (stroke) => ink.strokeBounds(stroke),
  geometry: (file) => (file?.strokes || []).map((s) => ({ id: s.id, ...strokeGeometry(s) })),
  strokeGeometry: (stroke) => strokeGeometry(stroke),
  normalizeTools: (list) => ink.normalizeTools(list),
  toolStyle: (preset) => ink.toolStyle(preset),
  defaultTools: () => ink.DEFAULT_TOOLS,
  paperLines: (paper) => notebook.paperLines(notebook.normalizePaper(paper)),
  normalizePaper: (paper, fallback) => notebook.normalizePaper(paper, fallback ?? null),
  paperSizes: () => notebook.PAPER_SIZES,
  viewportTransform: (box, rotation) => viewportTransform(box, rotation),
  makeId: () => makeBlockId(),
};

const RUN = {
  syncRound: (host) => syncRound(host),
  saveInk: (host, pageId, args) => edits.saveInk(host, pageId, args),
  createNotebook: (host, args) => edits.createNotebook(host, args),
  addSheet: (host, pageId, opts) => edits.addSheet(host, pageId, opts ?? {}),
  setSheetPaper: (host, pageId, sheetId, paper) => edits.setSheetPaper(host, pageId, sheetId, paper).then(() => null),
  setText: (host, pageId, blockId, content, base) => edits.setText(host, pageId, blockId, content, base ?? undefined).then(() => null),
  renamePage: (host, pageId, title, base) => edits.renamePage(host, pageId, title, base ?? undefined).then(() => null),
  addNote: (host, pageId, args) => edits.addNote(host, pageId, args),
  deleteBlock: (host, pageId, blockId) => edits.deleteBlock(host, pageId, blockId).then(() => null),
  createPage: (host, args) => edits.createPage(host, args),
  deletePage: (host, pageId) => edits.deletePage(host, pageId),
};

globalThis.GammaCore = {
  version: 1,
  pure(name, argsJSON) {
    const fn = PURE[name];
    if (!fn) throw new Error(`GammaCore.pure: no ${name}`);
    return JSON.stringify(fn(...JSON.parse(argsJSON || "[]")) ?? null);
  },
  run(name, argsJSON, native) {
    const slot = { done: false, value: null, error: null };
    const fn = RUN[name];
    Promise.resolve()
      .then(() => { if (!fn) throw new Error(`GammaCore.run: no ${name}`); return fn(hostOf(native), ...JSON.parse(argsJSON || "[]")); })
      .then((v) => { slot.value = JSON.stringify(v ?? null); slot.done = true; },
        (e) => { slot.error = String((e && (e.message || e)) || "error"); slot.done = true; });
    return slot;
  },
};
