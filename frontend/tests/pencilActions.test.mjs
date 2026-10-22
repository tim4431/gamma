// node --test tests/  (from frontend/) — the Pencil's double tap and squeeze.
import assert from "node:assert/strict";
import { test } from "node:test";
import { PENCIL_CHOICES, SQUEEZE_HOLDS, SQUEEZE_TAP_MS, backTool, pencilAction, pencilStep, stylusPen } from "../src/ink/pencilActions.js";

const tools = [{ id: "pen1", kind: "pen" }, { id: "pen2", kind: "pen" }, { id: "hl1", kind: "highlighter" }];
const prefs = { tools, pen: "pen2" };

test("switchEraser toggles the eraser against the tool before it, else the stylus pen", () => {
  assert.deepEqual(pencilAction({ action: "switchEraser" }, prefs, { tool: "pen1", previous: null }), { pick: "eraser" });
  assert.deepEqual(pencilAction("switchEraser", prefs, { tool: "eraser", previous: "pen1" }), { pick: "pen1" });
  assert.deepEqual(pencilAction("switchEraser", prefs, { tool: "eraser", previous: "gone" }), { pick: "pen2" }, "a removed preset falls back to the pen");
  assert.deepEqual(pencilAction("switchEraser", prefs, { tool: "eraser", previous: "eraser" }), { pick: "pen2" });
  assert.deepEqual(pencilAction("switchEraser", prefs, { tool: null, previous: "select" }), { pick: "eraser" });
});

test("switchPrevious goes back to the tool armed before, the strip's tools included", () => {
  assert.deepEqual(pencilAction("switchPrevious", prefs, { tool: "pen1", previous: "select" }), { pick: "select" });
  assert.deepEqual(pencilAction("switchPrevious", prefs, { tool: "pen1", previous: "text" }), { pick: "text" });
  assert.deepEqual(pencilAction("switchPrevious", prefs, { tool: "pen1", previous: null }), { pick: "pen2" });
  assert.deepEqual(pencilAction("switchPrevious", { tools, pen: "nope" }, { tool: "hl1", previous: null }), { pick: "pen1" }, "no last pen: the first pen");
});

test("the palette and the attributes open the armed tool's row, or arm the pen with its row", () => {
  assert.deepEqual(pencilAction("showColorPalette", prefs, { tool: "pen1", options: false }), { ui: { open: true, options: true } });
  assert.deepEqual(pencilAction("showInkAttributes", prefs, { tool: "pen1", options: true }), { ui: { open: true, options: false } });
  // the ruler's row open is not the tool's: the gesture opens the tool's in its place
  assert.deepEqual(pencilAction("showColorPalette", prefs, { tool: "pen1", options: "ruler" }), { ui: { open: true, options: true } });
  assert.deepEqual(pencilAction("showColorPalette", prefs, { tool: null, options: false }), { ui: { open: true, tool: "pen2", options: true } });
});

test("anything else is nothing", () => {
  assert.equal(pencilAction("ignore", prefs, { tool: "pen1" }), null);
  assert.equal(pencilAction({}, prefs, {}), null);
  assert.equal(pencilAction(null, {}, {}), null);
});

test("backTool is the tool armed before, else the stylus pen: the eraser's return reads it too", () => {
  assert.equal(backTool(prefs, { tool: "eraser", previous: "pen1" }), "pen1");
  assert.equal(backTool(prefs, { tool: "eraser", previous: "select" }), "select");
  assert.equal(backTool(prefs, { tool: "eraser", previous: "shape" }), "shape", "the Shapes tool is a tool to go back to");
  assert.equal(backTool(prefs, { tool: "shape", previous: "pen1" }), "pen1", "the Shapes tool's off gives back the tool before it");
  assert.equal(backTool(prefs, { tool: "shape", previous: "shape" }), "pen2");
  assert.equal(backTool(prefs, { tool: "eraser", previous: "eraser" }), "pen2", "the same tool is no way back");
  assert.equal(backTool(prefs, { tool: "eraser", previous: "gone" }), "pen2", "a removed preset falls back to the pen");
  assert.equal(backTool(prefs, { tool: "eraser", previous: null }), "pen2");
  assert.equal(backTool({ tools, pen: "nope" }, { tool: "eraser" }), "pen1", "no last pen: the first pen");
  assert.equal(backTool({}, {}), null);
});

test("stylusPen: the last pen armed while it is a pen on the strip, else the first pen, else the first preset; the gestures fall back to it", () => {
  // a highlighter first in the row, and the last pen armed since hidden (off the strip)
  const row = [{ id: "hl1", kind: "highlighter" }, { id: "pen1", kind: "pen" }, { id: "pen2", kind: "pen" }];
  assert.equal(stylusPen(row, "pen2").id, "pen2");
  assert.equal(stylusPen(row, "hidden").id, "pen1", "a hidden pen: the first pen, not the highlighter before it");
  assert.equal(stylusPen(row, "hl1").id, "pen1", "a highlighter is never the stylus pen while a pen is on the strip");
  assert.equal(stylusPen([{ id: "hl1", kind: "highlighter" }], "hidden").id, "hl1", "no pen on the strip: the first preset");
  assert.equal(stylusPen([], "pen1"), undefined);
  assert.equal(stylusPen(undefined, null), undefined);
  const hidden = { tools: row, pen: "hidden" };
  assert.equal(backTool(hidden, { tool: "eraser", previous: null }), "pen1", "the eraser's return");
  assert.deepEqual(pencilAction("switchPrevious", hidden, { tool: "hl1", previous: null }), { pick: "pen1" }, "Switch to last tool");
  assert.deepEqual(pencilAction("showColorPalette", hidden, { tool: null, options: false }), { ui: { open: true, tool: "pen1", options: true } },
    "Show options with the hand armed: the stylus pen's row");
  assert.deepEqual(pencilAction({ gesture: "squeeze", action: "switchEraser", phase: "began" }, { ...hidden, squeeze: "holdEraser" }, { tool: "eraser", previous: null }),
    { began: { ignored: false, pick: "pen1" } }, "Eraser while held, from the eraser");
});

// --- the choices (Settings → Reading & editing → Apple Pencil) ---------------------
const tap = (action = "switchEraser") => ({ gesture: "tap", action });
const squeeze = (phase, action = "switchEraser") => ({ gesture: "squeeze", action, ...(phase ? { phase } : {}) });

test("System setting is the default and follows the system's action; an unknown stored choice reads as it", () => {
  assert.deepEqual(PENCIL_CHOICES, ["system", "eraser", "previous", "options", "undo", "none"]);
  assert.deepEqual(SQUEEZE_HOLDS, ["holdEraser", "holdPrevious"]);
  for (const choices of [{}, { tap: "system", squeeze: "system" }, { tap: "bogus", squeeze: "holdBogus" }]) {
    const p = { ...prefs, ...choices };
    assert.deepEqual(pencilAction(tap("switchEraser"), p, { tool: "pen1" }), { pick: "eraser" });
    assert.deepEqual(pencilAction(squeeze(null, "switchPrevious"), p, { tool: "pen1", previous: "hl1" }), { pick: "hl1" });
    assert.equal(pencilAction(tap("runSystemShortcut"), p, { tool: "pen1" }), null);
  }
  assert.equal(pencilAction(tap("switchEraser"), { ...prefs, tap: "holdEraser" }, { tool: "pen1" }).pick, "eraser",
    "a hold choice is the squeeze's alone: on the double tap it reads as System setting");
});

test("a gesture's own choice stands in for the system's, each gesture its own", () => {
  const p = { ...prefs, tap: "undo", squeeze: "options" };
  assert.deepEqual(pencilAction(tap("switchEraser"), p, { tool: "pen1" }), { undo: true });
  assert.deepEqual(pencilAction(squeeze(null, "switchEraser"), p, { tool: "pen1", options: false }), { ui: { open: true, options: true } });
  assert.deepEqual(pencilAction(tap("ignore"), { ...prefs, tap: "eraser" }, { tool: "eraser", previous: "pen1" }), { pick: "pen1" });
  assert.deepEqual(pencilAction(tap("ignore"), { ...prefs, tap: "previous" }, { tool: "pen1", previous: "select" }), { pick: "select" });
  assert.equal(pencilAction(tap("switchEraser"), { ...prefs, tap: "none" }, { tool: "pen1" }), null);
  assert.deepEqual(pencilAction("switchEraser", { ...prefs, tap: "undo" }, {}), { undo: true }, "a bare action string is a double tap");
});

test("a squeeze without phases (an older shell) acts at once, a hold choice as its toggle", () => {
  assert.deepEqual(pencilAction(squeeze(null), { ...prefs, squeeze: "holdEraser" }, { tool: "pen1" }), { pick: "eraser" });
  assert.deepEqual(pencilAction(squeeze(null), { ...prefs, squeeze: "holdEraser" }, { tool: "eraser", previous: "pen1" }), { pick: "pen1" });
  assert.deepEqual(pencilAction(squeeze(null), { ...prefs, squeeze: "holdPrevious" }, { tool: "pen1", previous: "hl1" }), { pick: "hl1" });
  assert.deepEqual(pencilAction({ gesture: "tap", action: "switchEraser", phase: "began" }, prefs, { tool: "pen1" }), { pick: "eraser" },
    "a double tap has no phases");
});

test("a squeeze in phases: its start answers for every choice, and its end acts for the others", () => {
  const p = { ...prefs, squeeze: "eraser" };
  assert.deepEqual(pencilAction(squeeze("began"), p, { tool: "pen1" }), { began: { ignored: false } });
  const open = { since: 1000, ignored: false, hold: null };
  assert.deepEqual(pencilAction(squeeze("ended"), p, { tool: "pen1", squeeze: open, now: 1100 }), { pick: "eraser" });
  assert.equal(pencilAction(squeeze("cancelled"), p, { tool: "pen1", squeeze: open, now: 1100 }), null, "a cancel acts only for a hold");
  assert.equal(pencilAction(squeeze("ended"), p, { tool: "pen1", squeeze: null, now: 1100 }), null, "no start seen: nothing");
  assert.deepEqual(pencilAction(squeeze("ended", "switchPrevious"), prefs, { tool: "pen1", previous: "hl1", squeeze: open, now: 1100 }),
    { pick: "hl1" }, "System setting acts at the end too");
});

test("Eraser while held: the start arms the eraser, the end goes back unless the squeeze was a short one that did nothing", () => {
  const p = { ...prefs, squeeze: "holdEraser" };
  assert.deepEqual(pencilAction(squeeze("began"), p, { tool: "select" }), { began: { ignored: false, pick: "eraser" } });
  const held = (since, used) => ({ since, ignored: false, hold: { tool: "select", used } });
  assert.deepEqual(pencilAction(squeeze("ended"), p, { tool: "eraser", squeeze: held(1000, false), now: 1000 + SQUEEZE_TAP_MS }),
    { release: { pick: "select" } }, "held 300 ms: back to the tool before");
  assert.deepEqual(pencilAction(squeeze("ended"), p, { tool: "eraser", squeeze: held(1000, true), now: 1100 }),
    { release: { pick: "select" } }, "short, but it erased: back");
  assert.deepEqual(pencilAction(squeeze("ended"), p, { tool: "eraser", squeeze: held(1000, false), now: 1100 }),
    { release: { stay: true } }, "short and nothing erased: the eraser stays, a toggle");
  assert.deepEqual(pencilAction(squeeze("cancelled"), p, { tool: "eraser", squeeze: held(1000, false), now: 1500 }),
    { release: { pick: "select" } }, "a cancel goes back as an end does");
  assert.deepEqual(pencilAction(squeeze("ended"), p, { tool: "eraser", squeeze: { since: 0, ignored: false, hold: { tool: null, used: false } }, now: 900 }),
    { release: { pick: null } }, "back to the hand");
});

test("Eraser while held with the eraser armed holds the tool to go back to: a short squeeze gives it back", () => {
  const p = { ...prefs, squeeze: "holdEraser" };
  assert.deepEqual(pencilAction(squeeze("began"), p, { tool: "eraser", previous: "pen1" }), { began: { ignored: false, pick: "pen1" } });
  assert.deepEqual(pencilAction(squeeze("began"), p, { tool: "eraser", previous: null }), { began: { ignored: false, pick: "pen2" } },
    "no tool before: the stylus pen");
  const held = (since, used) => ({ since, ignored: false, hold: { tool: "eraser", used } });
  assert.deepEqual(pencilAction(squeeze("ended"), p, { tool: "pen1", squeeze: held(1000, false), now: 1100 }),
    { release: { stay: true } }, "short: the pen stays, the toggle back");
  assert.deepEqual(pencilAction(squeeze("ended"), p, { tool: "pen1", squeeze: held(1000, false), now: 1500 }),
    { release: { pick: "eraser" } }, "held: the eraser again");
  assert.deepEqual(pencilAction(squeeze("ended"), p, { tool: "pen1", squeeze: held(1000, true), now: 1100 }),
    { release: { pick: "eraser" } }, "short, but it drew: the eraser again");
});

// Squeezes through usePencil's step (pencilStep, as ink/usePencil.js
// pencilRef runs it) over the strip as small as it goes: the armed tool,
// and the one before it, which follows each switch (useInkStrip's
// inkPrevRef). → (ms, at) => the tool armed after a squeeze's start at
// `at` and after its end `ms` later.
function squeezes(prefs, tool) {
  let previous = null, open = null;
  const send = (phase, now) => {
    const step = pencilStep(squeeze(phase), prefs, { tool, previous, squeeze: open }, now);
    open = step.squeeze;
    for (const [what, id] of step.run) if (what === "pick" && id !== tool) { previous = tool; tool = id; }
    return tool;
  };
  return (ms, at) => [send("began", at), send("ended", at + ms)];
}

test("Eraser while held: short squeezes toggle the eraser either way, a held one comes back", () => {
  const squeeze = squeezes({ ...prefs, squeeze: "holdEraser" }, "pen1");
  assert.deepEqual(squeeze(100, 0), ["eraser", "eraser"], "a short squeeze arms the eraser");
  assert.deepEqual(squeeze(100, 1000), ["pen1", "pen1"], "the next gives the pen back");
  assert.deepEqual(squeeze(100, 2000), ["eraser", "eraser"]);
  assert.deepEqual(squeeze(600, 3000), ["pen1", "eraser"], "held with the eraser armed: the pen while it lasts");
  assert.deepEqual(squeeze(100, 4000), ["pen1", "pen1"]);
  assert.deepEqual(squeeze(600, 5000), ["eraser", "pen1"], "held with the pen armed: the eraser while it lasts");
});

test("a squeeze in a phase this page does not know does nothing", () => {
  for (const choice of ["system", "eraser", "holdEraser", "undo"]) {
    const p = { ...prefs, squeeze: choice };
    for (const phase of ["changed", "moved", 5, true]) {
      assert.equal(pencilAction(squeeze(phase), p, { tool: "pen1" }), null, `${choice} ${phase}`);
      assert.equal(pencilAction(squeeze(phase), p, { tool: "eraser", squeeze: { since: 0, ignored: false, hold: { tool: "pen1", used: false } }, now: 900 }),
        null, `${choice} ${phase}, held open`);
    }
  }
  assert.deepEqual(pencilAction(squeeze(null), { ...prefs, squeeze: "eraser" }, { tool: "pen1" }), { pick: "eraser" }, "no phase is an older shell's squeeze");
  assert.deepEqual(pencilAction({ gesture: "tap", action: "switchEraser", phase: "changed" }, prefs, { tool: "pen1" }), { pick: "eraser" },
    "a double tap has no phases to know");
});

test("Last tool while held arms the tool to go back to, and returns from it", () => {
  const p = { ...prefs, squeeze: "holdPrevious" };
  assert.deepEqual(pencilAction(squeeze("began"), p, { tool: "pen1", previous: "hl1" }), { began: { ignored: false, pick: "hl1" } });
  assert.deepEqual(pencilAction(squeeze("began"), p, { tool: "pen1", previous: null }), { began: { ignored: false, pick: "pen2" } });
  assert.deepEqual(pencilAction(squeeze("ended"), p, { tool: "hl1", squeeze: { since: 0, ignored: false, hold: { tool: "pen1", used: true } }, now: 200 }),
    { release: { pick: "pen1" } });
});

test("while a stroke or a claim is in progress nothing acts but a held squeeze's end", () => {
  const busy = { tool: "pen1", previous: "hl1", busy: true };
  assert.equal(pencilAction(tap("switchEraser"), prefs, busy), null);
  assert.equal(pencilAction(tap("switchEraser"), { ...prefs, tap: "undo" }, busy), null, "a double tap mid-stroke never undoes");
  assert.equal(pencilAction(squeeze(null), prefs, busy), null);
  assert.deepEqual(pencilAction(squeeze("began"), { ...prefs, squeeze: "holdEraser" }, busy), { began: { ignored: true } });
  const ignored = { since: 0, ignored: true, hold: null };
  assert.equal(pencilAction(squeeze("ended"), prefs, { ...busy, busy: false, squeeze: ignored, now: 900 }), null,
    "nor does the end of a squeeze that began then");
  assert.equal(pencilAction(squeeze("ended"), prefs, { ...busy, squeeze: { since: 0, ignored: false, hold: null }, now: 900 }), null);
  assert.deepEqual(pencilAction(squeeze("ended"), { ...prefs, squeeze: "holdEraser" },
    { ...busy, tool: "eraser", squeeze: { since: 0, ignored: false, hold: { tool: "pen1", used: false } }, now: 900 }),
  { release: { pick: "pen1" } }, "the end of a held squeeze is never lost");
});

// --- usePencil's step: the squeeze it holds, what it runs ----------------------------
test("pencilStep runs a pick after its tick, ticking only for a switch; undo and a row patch as they come", () => {
  assert.deepEqual(pencilStep(tap("switchEraser"), prefs, { tool: "pen1" }, 0), { squeeze: null, run: [["haptic", "tool"], ["pick", "eraser"]] });
  assert.deepEqual(pencilStep(tap("switchPrevious"), prefs, { tool: "pen2", previous: null }, 0).run, [["pick", "pen2"]],
    "the stylus pen armed already: no tick");
  assert.deepEqual(pencilStep(tap(), { ...prefs, tap: "undo" }, { tool: "pen1" }, 0).run, [["undo"]]);
  assert.deepEqual(pencilStep(tap("showColorPalette"), prefs, { tool: "pen1", options: false }, 0).run, [["ui", { open: true, options: true }]]);
  assert.deepEqual(pencilStep(tap("ignore"), prefs, { tool: "pen1" }, 0), { squeeze: null, run: [] });
  assert.deepEqual(pencilStep(tap(), prefs, { tool: "pen1", busy: true }, 0), { squeeze: null, run: [] }, "mid-stroke");
});

test("pencilStep opens a squeeze at its start, a hold keeping the tool and the lasso's selection, and closes it at its end", () => {
  const selection = { page: 1, items: [{ id: "g1", ids: ["a"] }] };
  assert.deepEqual(pencilStep(squeeze("began"), prefs, { tool: "pen1", selection }, 500), { squeeze: { since: 500, ignored: false, hold: null }, run: [] },
    "System setting: a start only answers");
  assert.deepEqual(pencilStep(squeeze("began"), { ...prefs, squeeze: "holdEraser" }, { tool: "pen1", busy: true }, 500),
    { squeeze: { since: 500, ignored: true, hold: null }, run: [] }, "a start mid-stroke is ignored, its end with it");
  const held = pencilStep(squeeze("began"), { ...prefs, squeeze: "holdEraser" }, { tool: "select", selection }, 500);
  assert.deepEqual(held, { squeeze: { since: 500, ignored: false, hold: { tool: "select", used: false, selection } },
    run: [["pick", "eraser"], ["haptic", "tool"]] });
  assert.deepEqual(pencilStep(squeeze("began"), { ...prefs, squeeze: "holdPrevious" }, { tool: "pen2", previous: null }, 0).run, [],
    "a hold whose tool is armed already: no switch");
  const open = { since: 0, ignored: false, hold: null };
  assert.deepEqual(pencilStep(squeeze("ended"), { ...prefs, squeeze: "eraser" }, { tool: "pen1", squeeze: open }, 900),
    { squeeze: null, run: [["haptic", "tool"], ["pick", "eraser"]] });
  assert.equal(pencilStep(squeeze("changed"), prefs, { tool: "pen1", squeeze: open }, 900).squeeze, open, "an unknown phase leaves it open");
});

test("a held squeeze's end goes back to the lasso with what is left of its selection; a short one that did nothing stays", () => {
  const p = { ...prefs, squeeze: "holdEraser" }, selection = { page: 1, items: [{ id: "g1", ids: ["a"] }] };
  const fromLasso = pencilStep(squeeze("began"), p, { tool: "select", selection }, 1000).squeeze;
  assert.deepEqual(pencilStep(squeeze("ended"), p, { tool: "eraser", squeeze: fromLasso }, 1600),
    { squeeze: null, run: [["pick", "select"], ["haptic", "tool"], ["restore", selection]] }, "the selection restored after the switch");
  assert.deepEqual(pencilStep(squeeze("cancelled"), p, { tool: "eraser", squeeze: fromLasso }, 1600).run.at(-1), ["restore", selection], "a cancel too");
  assert.deepEqual(pencilStep(squeeze("ended"), p, { tool: "eraser", squeeze: fromLasso }, 1100), { squeeze: null, run: [] },
    "short and nothing erased: the eraser stays, nothing restored");
  const used = { ...fromLasso, hold: { ...fromLasso.hold, used: true } };
  assert.deepEqual(pencilStep(squeeze("ended"), p, { tool: "eraser", squeeze: used }, 1100).run.at(-1), ["restore", selection], "short, but it erased");
  const fromPen = pencilStep(squeeze("began"), p, { tool: "pen1", selection }, 1000).squeeze;
  assert.deepEqual(pencilStep(squeeze("ended"), p, { tool: "eraser", squeeze: fromPen }, 1600).run, [["pick", "pen1"], ["haptic", "tool"]],
    "back to a pen: nothing to restore");
  const noSelection = pencilStep(squeeze("began"), p, { tool: "select", selection: null }, 1000).squeeze;
  assert.deepEqual(pencilStep(squeeze("ended"), p, { tool: "eraser", squeeze: noSelection }, 1600).run, [["pick", "select"], ["haptic", "tool"]]);
});

test("pencilStep's gate: on a read-only page, or where no page takes ink, only a held squeeze's end acts; an end closes the squeeze anyway", () => {
  const held = { since: 0, ignored: false, hold: { tool: "pen1", used: false, selection: null } };
  for (const gate of [{ readOnly: true }, { takesInk: false }]) {
    const what = JSON.stringify(gate);
    assert.deepEqual(pencilStep(tap("switchEraser"), prefs, { tool: "pen1", ...gate }, 0), { squeeze: null, run: [] }, what);
    assert.deepEqual(pencilStep(squeeze("began"), { ...prefs, squeeze: "holdEraser" }, { tool: "pen1", ...gate }, 0), { squeeze: null, run: [] },
      `${what}: no squeeze opens`);
    assert.deepEqual(pencilStep(squeeze("ended"), { ...prefs, squeeze: "eraser" }, { tool: "pen1", squeeze: { since: 0, ignored: false, hold: null }, ...gate }, 900),
      { squeeze: null, run: [] }, `${what}: a plain squeeze's end closes it and does nothing`);
    assert.deepEqual(pencilStep(squeeze("cancelled"), { ...prefs, squeeze: "holdEraser" }, { tool: "eraser", squeeze: held, ...gate }, 900),
      { squeeze: null, run: [["pick", "pen1"], ["haptic", "tool"]] }, `${what}: a held squeeze goes back, wherever the page is now`);
    assert.equal(pencilStep(tap("switchEraser"), prefs, { tool: "eraser", squeeze: held, ...gate }, 0).squeeze, held, `${what}: a tap leaves it open`);
  }
});
