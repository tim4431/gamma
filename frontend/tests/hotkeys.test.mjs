import test from "node:test";
import assert from "node:assert/strict";
import {
  bindable, chordFromEvent, chordLabel, chordParts, conflicts, dispatch, effectiveKeys, heldAlone, heldFlags,
  heldFromEvent, heldKey, isHeldKey, normalizeChord,
} from "../src/shared/lib/hotkeys.js";

const key = (code, keyName, mods = {}) => ({ code, key: keyName, ...mods });

test("normalizeChord canonicalizes modifier order, case and aliases", () => {
  assert.equal(normalizeChord("shift-mod-K"), "Mod-Shift-k");
  assert.equal(normalizeChord("Cmd-b"), "Mod-b");
  assert.equal(normalizeChord("Alt-ArrowUp"), "Alt-ArrowUp");
  assert.equal(normalizeChord("f2"), "F2");
  assert.equal(normalizeChord("Mod--"), "Mod--");
  assert.equal(normalizeChord("Mod-esc"), "Mod-Escape");
  assert.equal(normalizeChord("bogus-k"), "");
  assert.equal(normalizeChord(""), "");
  assert.equal(normalizeChord(null), "");
});

test("chordFromEvent reads the physical key, so Shift does not change it", () => {
  assert.equal(chordFromEvent(key("KeyK", "K", { ctrlKey: true, shiftKey: true }), false), "Mod-Shift-k");
  assert.equal(chordFromEvent(key("BracketLeft", "{", { ctrlKey: true, shiftKey: true }), false), "Mod-Shift-[");
  assert.equal(chordFromEvent(key("Minus", "-", { metaKey: true }), true), "Mod--");
  assert.equal(chordFromEvent(key("F2", "F2"), false), "F2");
  assert.equal(chordFromEvent(key("ArrowUp", "ArrowUp", { altKey: true }), false), "Alt-ArrowUp");
  // A bare modifier is no chord.
  assert.equal(chordFromEvent(key("ControlLeft", "Control", { ctrlKey: true }), false), "");
  // Mac: ⌘ is Mod, the control key stays Ctrl.
  assert.equal(chordFromEvent(key("KeyP", "p", { metaKey: true, shiftKey: true }), true), "Mod-Shift-p");
  assert.equal(chordFromEvent(key("KeyP", "p", { ctrlKey: true }), true), "Ctrl-p");
  // Without a code (synthetic events) the key name serves.
  assert.equal(chordFromEvent({ key: "Enter", ctrlKey: true }, false), "Mod-Enter");
});

test("labels follow the platform", () => {
  assert.equal(chordLabel("Mod-Shift-k", false), "Ctrl+Shift+K");
  assert.equal(chordLabel("Mod-Shift-k", true), "⇧⌘K");
  assert.equal(chordLabel("Alt-ArrowUp", false), "Alt+↑");
  assert.equal(chordLabel("Alt-ArrowUp", true), "⌥↑");
  assert.equal(chordLabel("F2", true), "F2");
  assert.deepEqual(chordParts("Ctrl-Alt-Shift-Mod-a", true), ["⌃", "⌥", "⇧", "⌘", "A"]);
  assert.deepEqual(chordParts("Mod--", false), ["Ctrl", "-"]);
  assert.deepEqual(chordParts("nope-x", false), []);
  assert.deepEqual(chordParts("Mod", false), ["Ctrl"], "a lone modifier, as in a guide's {key:Mod}");
  assert.deepEqual(chordParts("Mod", true), ["⌘"]);
  assert.deepEqual(chordParts("Alt", true), ["⌥"]);
});

test("bindable refuses keys that would replace typing", () => {
  assert.ok(bindable("Mod-k"));
  assert.ok(bindable("F5"));
  assert.ok(bindable("Shift-F5"));
  assert.ok(!bindable("k"));
  assert.ok(!bindable("Enter"));
  assert.ok(!bindable(""));
});

test("effectiveKeys: the override wins, null unbinds, defaults otherwise", () => {
  const cmd = { id: "x", keys: ["Mod-y", "Mod-Shift-z"] };
  assert.deepEqual(effectiveKeys(cmd, {}), ["Mod-y", "Mod-Shift-z"]);
  assert.deepEqual(effectiveKeys(cmd, { x: "Alt-q" }), ["Alt-q"]);
  assert.deepEqual(effectiveKeys(cmd, { x: null }), []);
  assert.deepEqual(effectiveKeys({ id: "n", keys: null }, {}), []);
});

test("dispatch runs the first matching command that applies and swallows the key", () => {
  const log = [];
  const ev = (code, k, mods) => {
    const e = key(code, k, mods);
    e.preventDefault = () => log.push("prevented");
    e.stopPropagation = () => log.push("stopped");
    return e;
  };
  const commands = [
    { id: "a", keys: "Mod-k", when: (c) => c.ok, run: () => log.push("a") },
    { id: "b", keys: "Mod-k", run: () => log.push("b") },
    { id: "c", keys: "Mod-j", edits: true, run: () => log.push("c") },
    { id: "d", keys: "Mod-d", run: () => { log.push("d-declined"); return false; } },
    { id: "e", keys: "Mod-d", run: () => log.push("e") },
  ];
  assert.equal(dispatch(commands, ev("KeyK", "k", { ctrlKey: true }), { ok: false }, {}, false).id, "b");
  assert.equal(dispatch(commands, ev("KeyK", "k", { ctrlKey: true }), { ok: true }, {}, false).id, "a");
  assert.equal(dispatch(commands, ev("KeyJ", "j", { ctrlKey: true }), { readOnly: true }, {}, false), null);
  assert.equal(dispatch(commands, ev("KeyD", "d", { ctrlKey: true }), {}, {}, false).id, "e");
  // A rebinding moves the command; the old chord no longer fires it.
  assert.equal(dispatch(commands, ev("KeyQ", "q", { altKey: true }), {}, { c: "Alt-q" }, false).id, "c");
  assert.equal(dispatch(commands, ev("KeyJ", "j", { ctrlKey: true }), {}, { c: "Alt-q" }, false), null);
  // An IME composition is never a shortcut.
  assert.equal(dispatch(commands, { ...ev("KeyK", "k", { ctrlKey: true }), isComposing: true }, {}, {}, false), null);
  assert.deepEqual(log, ["b", "prevented", "stopped", "a", "prevented", "stopped", "d-declined", "e", "prevented", "stopped", "c", "prevented", "stopped"]);
});

test("a held key is a lone modifier; off a Mac Ctrl is Mod", () => {
  assert.equal(heldKey("Mod", true), "Mod");
  assert.equal(heldKey("Ctrl", true), "Ctrl");
  assert.equal(heldKey("Ctrl", false), "Mod");
  assert.equal(heldKey("Alt", false), "Alt");
  assert.equal(heldKey("Shift", false), "Shift");
  assert.equal(heldKey("Mod-k", false), "", "a chord with a key is no held key");
  assert.equal(heldKey("", false), "");
});

test("the recorder reads a modifier press as a held key", () => {
  assert.equal(heldFromEvent({ key: "Meta" }, true), "Mod", "⌘ on a Mac");
  assert.equal(heldFromEvent({ key: "Control" }, true), "Ctrl", "a Mac's control key");
  assert.equal(heldFromEvent({ key: "Control" }, false), "Mod");
  assert.equal(heldFromEvent({ key: "Meta" }, false), "", "the Windows key");
  assert.equal(heldFromEvent({ key: "Alt" }, true), "Alt");
  assert.equal(heldFromEvent({ key: "Shift" }, false), "Shift");
  assert.equal(heldFromEvent({ key: "k" }, false), "");
  assert.ok(isHeldKey({ key: "Meta" }, "Mod", true));
  assert.ok(!isHeldKey({ key: "Control" }, "Mod", true), "a Mac's Ctrl is not ⌘");
  assert.ok(isHeldKey({ key: "Control" }, "Ctrl", false), "a Mac's Ctrl binding is Ctrl elsewhere");
  assert.ok(!isHeldKey({ key: "Alt" }, "Mod-k", false));
});

test("heldAlone wants the held key down and no other modifier", () => {
  const mouse = (mods) => ({ ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...mods });
  assert.ok(heldAlone(mouse({ ctrlKey: true }), "Mod", false));
  assert.ok(!heldAlone(mouse({ ctrlKey: true }), "Mod", true), "a Mac's Ctrl+click is the right click, not ⌘");
  assert.ok(heldAlone(mouse({ metaKey: true }), "Mod", true));
  assert.ok(!heldAlone(mouse({ ctrlKey: true, shiftKey: true }), "Mod", false));
  assert.ok(!heldAlone(mouse({ ctrlKey: true, metaKey: true }), "Mod", false));
  assert.ok(heldAlone(mouse({ altKey: true }), "Alt", true));
  assert.ok(!heldAlone(mouse({}), "Mod", false));
  assert.ok(!heldAlone(mouse({ ctrlKey: true }), "", false), "no held key, no drag");
  assert.deepEqual(heldFlags("Mod", true), { ctrlKey: false, metaKey: true, altKey: false, shiftKey: false });
  assert.deepEqual(heldFlags("Mod", false), { ctrlKey: true, metaKey: false, altKey: false, shiftKey: false });
  assert.ok(heldAlone(heldFlags("Shift", false), "Shift", false), "what the guide's demo sends passes");
});

test("conflicts lists every chord more than one command answers to", () => {
  const commands = [
    { id: "a", keys: "Mod-k" }, { id: "b", keys: "Mod-j" }, { id: "c", keys: ["Mod-y", "Mod-Shift-z"] },
  ];
  assert.equal(conflicts(commands, {}).size, 0);
  const c = conflicts(commands, { b: "Mod-k", c: "Mod-Shift-z" });
  assert.deepEqual([...c.keys()], ["Mod-k"]);
  assert.deepEqual(c.get("Mod-k").map((x) => x.id), ["a", "b"]);
});
