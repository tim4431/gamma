// The surface App hands DocumentPane's own viewers (documents/viewerSurface.js):
// the paperclip's Replace accept, and the surface's shape and stability, the
// hook run through React's test renderer of a function component.
import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { replaceAccept, useViewerSurface } from "../src/documents/viewerSurface.js";
import { EXTENSIONS } from "../src/documents/formats.js";

test("replaceAccept offers the document's format's extensions, and nothing for no document", () => {
  assert.equal(replaceAccept({ format: "pdf" }), ".pdf");
  const image = replaceAccept({ format: "image" }).split(",");
  for (const ext of [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"]) assert.ok(image.includes(ext), ext);
  assert.ok(image.every((ext) => EXTENSIONS[ext].format === "image"));
  const text = replaceAccept({ format: "text" }).split(",");
  assert.ok(text.includes(".tex") && text.includes(".json") && !text.includes(".md") && !text.includes(".pdf"));
  assert.equal(replaceAccept(null), "");
  assert.equal(replaceAccept({ format: "nothing" }), "");
});

// The hook's surface read off one render of a component that calls it.
function surfaceOf(state, handlers) {
  let got = null;
  function Probe() {
    got = useViewerSurface(state, handlers);
    return null;
  }
  renderToStaticMarkup(React.createElement(Probe));
  return got;
}

test("the surface carries the state, the scale's three and the handlers given, null for the rest", () => {
  const doc = { format: "image", name: "fig.png", hash: "a1", at: "path" };
  const calls = [];
  const { surface, reportRef } = surfaceOf(
    { doc, fileUrl: "/api/uploads/a1.png", pageId: "p1", readOnly: false, hidden: false, phone: false, scale: "page-width",
      regions: [], anchors: [], flashId: null, regionMode: true, commentMargin: null, marginRef: {}, scrollRef: {}, captureRef: {} },
    { onZoomTo: (z) => calls.push(["zoom", z]), onEffectiveScale: null, onRegion: (pos, extra) => calls.push(["region", pos, extra]),
      placeOf: () => ({ top: 12, left: 0, scale: 1 }), onReveal: null },
  );
  assert.equal(surface.doc, doc);
  assert.equal(surface.fileUrl, "/api/uploads/a1.png");
  assert.equal(surface.pageId, "p1");
  assert.equal(surface.regionMode, true);
  assert.equal(surface.moveMode, false, "no highlight waits to be moved");
  assert.equal(surface.reportRef, reportRef);
  assert.deepEqual(surface.place, { top: 12, left: 0, scale: 1 });
  assert.equal(surface.scale.value, "page-width");
  surface.scale.onZoomTo(1.2);
  surface.onRegion({ width: 1 }, { color: "#f00" });
  assert.deepEqual(calls, [["zoom", 1.2], ["region", { width: 1 }, { color: "#f00" }]]);
  for (const k of ["onReveal", "onPlace", "onMarkDone", "onRegionJump", "onRegionContext", "onRegionThread", "onMarginLost", "onAskRegion",
    "onAnchor", "onHighlightAction", "onChatSelection"]) {
    assert.equal(surface[k], null, `${k} is null when App gives none`);
  }
  assert.equal(surface.scale.onEffectiveScale, null);
  assert.equal(surface.mark, null, "no line link, no mark");
});

test("a line link's mark rides the surface, and onMarkDone reaches App's handler", () => {
  const mark = { kind: "line", pageId: "p1", line: 2, to: null, quote: "gradient descent" };
  let done = 0;
  const { surface } = surfaceOf(
    { doc: { format: "text", name: "method.tex" }, fileUrl: "/api/uploads/b2.tex", pageId: "p1", mark },
    { onMarkDone: () => { done++; } },
  );
  assert.equal(surface.mark, mark);
  surface.onMarkDone();
  assert.equal(done, 1);
});

test("the code view's selection reaches App: onAnchor's id comes back, the bar's action and the chat's lines pass through", () => {
  const calls = [];
  const position = { line: 16, col: 0, end: 16, end_col: 33, hash: "c3", prefix: "", suffix: "\n" };
  const { surface } = surfaceOf(
    { doc: { format: "text", name: "main.tex", hash: "c3" }, fileUrl: "/api/uploads/c3.tex", pageId: "p1" },
    {
      onAnchor: (pos, quote, extras) => { calls.push(["anchor", pos, quote, extras]); return "h1"; },
      onHighlightAction: (id, action, value) => calls.push(["action", id, action, value]),
      onChatSelection: (text, additive, spot) => calls.push(["chat", text, additive, spot]),
    },
  );
  assert.equal(surface.onAnchor(position, "Weight decay is the oldest remedy", { comment: true }), "h1");
  surface.onHighlightAction("h1", "style", "underline");
  surface.onChatSelection("Weight decay", true, { line: 16, end: 16, file: "main.tex" });
  surface.onChatSelection("", false);
  assert.deepEqual(calls, [
    ["anchor", position, "Weight decay is the oldest remedy", { comment: true }],
    ["action", "h1", "style", "underline"],
    ["chat", "Weight decay", true, { line: 16, end: 16, file: "main.tex" }],
    ["chat", "", false, undefined],
  ]);
});
