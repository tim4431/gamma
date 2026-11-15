// A control's Alt variant (shared/ui/altVariant.js): shown while Alt is held
// over it, an Alt pressed before its listener started included.
import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true });
Object.assign(globalThis, { window: dom.window, document: dom.window.document });
globalThis.navigator ??= dom.window.navigator;
const { useAltVariant } = await import("../src/shared/ui/altVariant.js");
const React = (await import("react")).default;
const { createRoot } = (await import("react-dom/client")).default;

const settle = async () => { for (let i = 0; i < 3; i++) await new Promise((r) => setTimeout(r, 0)); };
const key = (type) => window.dispatchEvent(new window.KeyboardEvent(type, { key: "Alt", altKey: type === "keydown" }));

function mount() {
  const host = document.createElement("div");
  document.body.append(host);
  const seen = {};
  function Control() {
    const [alt, props] = useAltVariant();
    seen.props = props;
    return React.createElement("button", { "data-alt": String(alt) }, "New chat");
  }
  const root = createRoot(host);
  root.render(React.createElement(Control));
  return { seen, armed: () => host.querySelector("button").dataset.alt === "true",
    done: () => { root.unmount(); host.remove(); } };
}

test("Alt pressed as the pointer arrives, before the control listens, arms it", async () => {
  const c = mount();
  await settle();
  c.seen.props.onPointerEnter({ pointerType: "mouse", altKey: false }); // its render comes later
  key("keydown");
  await settle();
  assert.equal(c.armed(), true);
  key("keyup");
  await settle();
  assert.equal(c.armed(), false);
  c.done();
});

test("an Alt held elsewhere leaves a control the pointer has not reached alone", async () => {
  const c = mount();
  await settle();
  key("keydown");
  await settle();
  assert.equal(c.armed(), false);
  key("keyup");
  c.done();
});
