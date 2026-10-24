// A surface that leaves with motion (shared/ui/leave.js, docs/dev/ui-design.md
// "Motion"): the still copy React's removal leaves behind, where it goes,
// what it carries, and when there is none. jsdom does no layout, so every
// element is given one box: shown, as in a browser.
import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

// (pretendToBeVisual: a jsdom document is hidden otherwise, and a hidden tab leaves no copy)
const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, getComputedStyle: dom.window.getComputedStyle });
globalThis.navigator ??= dom.window.navigator;
dom.window.Element.prototype.getClientRects = function () { return [{ width: 1, height: 1 }]; };
const { leave, stillCopy, useLeave } = await import("../src/shared/ui/leave.js");
const React = (await import("react")).default;
const { flushSync } = (await import("react-dom")).default;
const { createRoot } = (await import("react-dom/client")).default;

const settle = () => new Promise((r) => setTimeout(r, 0));
const make = (html) => {
  const host = document.createElement("div");
  host.innerHTML = html;
  document.body.append(host);
  return host;
};

test("the copy carries what the markup does not, and nothing to find it by", () => {
  const host = make('<div id="m" data-guide="x"><input id="f" value="a"><input type="checkbox"><div data-tour="t"></div><svg><clipPath id="c"></clipPath></svg></div>');
  const node = host.firstChild;
  node.querySelector("input").value = "typed";
  node.querySelector("[type=checkbox]").checked = true;
  const { copy } = stillCopy(node);
  assert.equal(copy.querySelector("input").value, "typed");
  assert.equal(copy.querySelector("[type=checkbox]").checked, true);
  assert.equal(copy.querySelectorAll(":not(svg *)[id], [data-guide], [data-tour]").length + (copy.id ? 1 : 0), 0);
  assert.equal(copy.querySelector("clipPath").id, "c", "a drawing keeps the ids it points at");
  assert.equal(copy.getAttribute("aria-hidden"), "true");
  assert.ok(copy.hasAttribute("inert"));
  host.remove();
});

test("once the surface has gone, its copy stands where it stood and goes when its animation ends", async () => {
  const host = make('<span class="a"></span><div class="menu" style="position: fixed">Rows</div><span class="b"></span>');
  const node = host.querySelector(".menu");
  leave(node, "surface");
  node.remove();
  await Promise.resolve();
  const copy = host.querySelector("[data-leaving]");
  assert.ok(copy, "a copy");
  assert.equal(copy.dataset.leaving, "surface");
  assert.equal(copy.previousSibling.className, "a");
  assert.equal(copy.nextSibling.className, "b");
  assert.equal(copy.textContent, "Rows");
  copy.dispatchEvent(new window.Event("animationend"));
  assert.equal(host.querySelector("[data-leaving]"), null, "gone with its own animation's end");
  host.remove();
});

test("a child's animation ending does not take the copy away", async () => {
  const host = make('<div class="menu" style="position: fixed"><i class="spin"></i></div>');
  const node = host.firstChild;
  leave(node, "surface");
  node.remove();
  await Promise.resolve();
  const copy = host.querySelector("[data-leaving]");
  copy.querySelector(".spin").dispatchEvent(new window.Event("animationend", { bubbles: true }));
  assert.ok(host.querySelector("[data-leaving]"));
  copy.dispatchEvent(new window.Event("animationend"));
  host.remove();
});

test("no copy when the surface stays, when its place went with it, or when it was hidden", async () => {
  const stays = make('<div style="position: fixed"></div>');
  leave(stays.firstChild, "surface");
  await Promise.resolve();
  assert.equal(stays.querySelectorAll("[data-leaving]").length, 0, "still in the page: a ref handed over");

  const gone = make('<div style="position: fixed"></div>');
  leave(gone.firstChild, "surface");
  gone.remove();
  await Promise.resolve();
  assert.equal(gone.querySelectorAll("[data-leaving]").length, 0, "its parent left with it");

  const hidden = make('<div style="position: fixed; visibility: hidden"></div>');
  leave(hidden.firstChild, "surface");
  hidden.firstChild.remove();
  await Promise.resolve();
  assert.equal(hidden.querySelectorAll("[data-leaving]").length, 0, "never seen, so nothing to see go");
  hidden.remove();
});

test("a surface in the flow leaves from its box without holding its room", async () => {
  const host = make('<div class="toast" style="position: static">Due</div>');
  const node = host.firstChild;
  leave(node, "toast");
  node.remove();
  await Promise.resolve();
  const copy = host.querySelector("[data-leaving]");
  assert.equal(copy.style.position, "absolute");
  assert.equal(copy.style.boxSizing, "border-box");
  host.remove();
});

test("useLeave: React's removal of the element leaves its copy, and a ref of one's own is kept", async () => {
  const host = make("");
  const root = createRoot(host);
  const own = { current: null };
  function Menu({ open }) {
    const ref = useLeave("surface", own);
    return open ? React.createElement("div", { ref, className: "menu", style: { position: "fixed" } }, "Rows") : null;
  }
  flushSync(() => root.render(React.createElement(Menu, { open: true })));
  assert.equal(own.current?.className, "menu");
  flushSync(() => root.render(React.createElement(Menu, { open: false })));
  assert.equal(own.current, null);
  await settle();
  const copy = host.querySelector("[data-leaving]");
  assert.ok(copy, "the copy stands where the menu stood");
  assert.equal(copy.className, "menu");
  root.unmount();
  host.remove();
});

test("a surface that arrives takes the place of one of its kind still leaving", async () => {
  const host = make("");
  const root = createRoot(host);
  function Menu({ which }) {
    const ref = useLeave("surface");
    return React.createElement("div", { ref, className: `menu ${which}`, style: { position: "fixed" } }, which);
  }
  function Pair({ open }) {
    return React.createElement(React.Fragment, null, open.map((which) => React.createElement(Menu, { key: which, which })));
  }
  // one menu closes as another opens, in one commit: no copy of the first
  flushSync(() => root.render(React.createElement(Pair, { open: ["a"] })));
  flushSync(() => root.render(React.createElement(Pair, { open: ["b"] })));
  await settle();
  assert.equal(host.querySelectorAll("[data-leaving]").length, 0, "the arrival in the same commit wins");
  // it closes; a copy leaves; another opens before the copy has gone
  flushSync(() => root.render(React.createElement(Pair, { open: [] })));
  await settle();
  assert.equal(host.querySelectorAll("[data-leaving]").length, 1);
  flushSync(() => root.render(React.createElement(Pair, { open: ["c"] })));
  assert.equal(host.querySelectorAll("[data-leaving]").length, 0, "the copy goes as the next one arrives");
  assert.equal(host.querySelectorAll(".menu").length, 1);
  root.unmount();
  host.remove();
});

test("a copy holds no surface standing over the window and no copy already leaving", () => {
  const host = make('<div class="modal" style="position: fixed"><p>Body</p><div class="sub" style="position: fixed">Sub</div><div data-leaving="surface">Old</div></div>');
  const { copy } = stillCopy(host.firstChild);
  assert.equal(copy.querySelector("p").textContent, "Body");
  assert.equal(copy.querySelector(".sub"), null, "a sub-dialog goes at once");
  assert.equal(copy.querySelector("[data-leaving]"), null);
  host.remove();
});

test("a toast's copy is put back where it was seen, and a toast takes no other's place", async () => {
  const host = make("");
  const root = createRoot(host);
  function Card({ id }) {
    const ref = useLeave("toast");
    return React.createElement("div", { ref, className: `card ${id}`, style: { position: "static" } }, id);
  }
  const Stack = ({ shown }) => React.createElement(React.Fragment, null, shown.map((id) => React.createElement(Card, { key: id, id })));
  flushSync(() => root.render(React.createElement(Stack, { shown: ["a", "b", "c"] })));
  // jsdom lays nothing out: the card reads as seen 30 px lower than its copy lands
  const seen = { left: 0, top: 30, width: 1, height: 1 };
  host.querySelector(".a").getBoundingClientRect = () => seen;
  flushSync(() => root.render(React.createElement(Stack, { shown: ["b", "c", "d"] })));
  await settle();
  const copy = host.querySelector("[data-leaving]");
  assert.ok(copy, "dismissed as the next one comes: it still leaves");
  assert.equal(copy.style.top, "30px", "where it was seen");
  root.unmount();
  host.remove();
});

test("a dialog opened as another closes keeps the dim: it is marked as taking that one's place", async () => {
  const host = make("");
  const root = createRoot(host);
  function Dialog({ id }) {
    const ref = useLeave("dialog");
    return React.createElement("div", { ref, className: `overlay ${id}`, style: { position: "fixed" } }, id);
  }
  const One = ({ id }) => (id ? React.createElement(Dialog, { key: id, id }) : null);
  flushSync(() => root.render(React.createElement(One, { id: "palette" })));
  assert.equal(host.querySelector(".palette").dataset.over, undefined, "nothing was there before it");
  flushSync(() => root.render(React.createElement(One, { id: null })));
  await settle();
  flushSync(() => root.render(React.createElement(One, { id: "settings" })));
  assert.equal(host.querySelector(".settings").dataset.over, "", "it opened over the palette's copy");
  assert.equal(host.querySelectorAll("[data-leaving]").length, 0);
  root.unmount();
  host.remove();
});
