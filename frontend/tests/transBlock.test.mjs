// What the translated view's overlay draws for a block (src/pdf/transBlock.js).
import test from "node:test";
import assert from "node:assert/strict";
import { REF_SCALE, blockBoxes, blockLanding, blockLeading, blockSetting, blockShow, cropBox, linesReached, maskedBlocks, maskedRuns } from "../src/pdf/transBlock.js";

const entry = (texts, over = {}) => ({
  paras: texts.map(() => ({ translate: true })), texts, queued: false, busy: new Set(), partial: {}, failed: new Set(), ...over,
});

test("a block with text shows it, keyed by its final text; a partial streams under the block's own key", () => {
  assert.deepEqual(blockShow(entry(["译文"]), 0, false), { text: "译文", streaming: false, again: false, busy: false, key: "0:译文" });
  const typing = entry([undefined, undefined], { partial: { 1: "译" } });
  assert.deepEqual(blockShow(typing, 1, true), { text: "译", streaming: true, again: false, busy: false, key: 1 });
  // The job's end leaves busy and partial null.
  assert.deepEqual(blockShow(entry(["译文"], { busy: null, partial: null }), 0, false).text, "译文");
});

test("a text asked for again shimmers over the one it had until its new words stream over it, under the old key", () => {
  const asked = entry(["旧译文"], { busy: new Set([0]) });
  assert.deepEqual(blockShow(asked, 0, false), { text: "旧译文", streaming: false, again: false, busy: true, key: "0:旧译文" });
  const typing = entry(["旧译文"], { busy: new Set([0]), partial: { 0: "新" } });
  assert.deepEqual(blockShow(typing, 0, false), { text: "新", streaming: true, again: true, busy: false, key: "0:旧译文" });
  // A block that came back as its source, asked for again, waits under the wash.
  assert.deepEqual(blockShow(entry([null], { busy: new Set([0]) }), 0, true), { pending: true, busy: true });
});

test("a block whose words went to the part before it is masked, with nothing written", () => {
  assert.deepEqual(blockShow(entry([""]), 0, false), { text: "", streaming: false, again: false, busy: false, key: 0 });
});

test("a block shown in the original has nothing drawn, and a figure's text only once it was asked for", () => {
  assert.equal(blockShow(entry(["译文"], { original: new Set([0]) }), 0, false), null);
  assert.deepEqual(maskedBlocks(entry(["译文", "二"], { original: new Set([0]) })), [1]);
  const figure = (texts, over) => {
    const e = entry(texts, over);
    e.paras = [{ translate: false, figure: true }];
    return e;
  };
  assert.equal(blockShow(figure([undefined], { queued: true }), 0, true), null, "never pending with its page");
  assert.equal(blockShow(figure(["图"]), 0, false).text, "图");
  assert.deepEqual(blockShow(figure([undefined], { busy: new Set([0]) }), 0, true), { pending: true, busy: true });
});

test("a block lands when its final text is new to the overlay; a mount and a text it has already show nothing landing", () => {
  const before = entry([undefined, undefined, "旧", "同"], { partial: { 1: "译" } });
  const now = entry(["新", "译文", "再译", "同"]);
  assert.equal(blockLanding(before, now, 0), "new", "over the original");
  assert.equal(blockLanding(before, now, 1), "streamed", "over its own words");
  assert.equal(blockLanding(before, now, 2), "new", "asked again");
  assert.equal(blockLanding(before, now, 3), null);
  assert.equal(blockLanding(null, now, 0), null, "a mount");
  assert.equal(blockLanding(before, entry([undefined, "", null, "同"]), 1), null, "no text lands");
  assert.equal(blockLanding(before, entry([undefined, "译文"], { partial: null }), 1), "streamed", "the job's end clears the partials after they were seen");
});

test("a streaming block has reached the lines that start above where its text ends", () => {
  const lines = [{ top: 0 }, { top: 24 }, { top: 48 }, { top: 72 }];
  assert.equal(linesReached(lines, 10), 1);
  assert.equal(linesReached(lines, 24), 1, "a line that starts at the text's end is not reached");
  assert.equal(linesReached(lines, 25), 2);
  assert.equal(linesReached(lines, 500), 4);
});

test("the overlay leaves alone a block it does not translate, one back as its source, and one a finished job left", () => {
  const figure = entry(["译文"]);
  figure.paras[0].translate = false;
  assert.equal(blockShow(figure, 0, true), null);
  assert.equal(blockShow(entry([null], { queued: true }), 0, true), null, "came back as its source");
  assert.equal(blockShow(entry([undefined], { queued: true }), 0, false), null, "no job is working on the page");
  assert.equal(blockShow(entry([undefined], { busy: null, partial: null }), 0, false), null);
});

test("a block still waiting is washed while queued, shimmers in flight, and is marked once it failed", () => {
  assert.deepEqual(blockShow(entry([undefined], { queued: true }), 0, true), { pending: true, busy: false });
  assert.deepEqual(blockShow(entry([undefined], { busy: new Set([0]) }), 0, true), { pending: true, busy: true });
  assert.deepEqual(blockShow(entry([undefined], { queued: true, failed: new Set([0]) }), 0, true), { failed: true });
  assert.deepEqual(blockShow(entry([undefined], { failed: new Set([0]) }), 0, false), { failed: true }, "after the job too");
  assert.deepEqual(maskedBlocks(entry([undefined], { failed: new Set([0]) })), [], "its original is not masked");
  assert.deepEqual(blockShow(entry([undefined], { queued: true, failed: new Set([0]), busy: new Set([0]) }), 0, true),
    { pending: true, busy: true }, "asked again, it is in flight");
  assert.equal(blockShow(entry([undefined]), 0, true), null, "neither queued nor in flight");
});

test("a block's box stops at its foot, and its masks stay on its lines", () => {
  const p = { size: 10, x1: 50, y1: 100, x2: 150, y2: 122, lines: [{ x1: 50, y1: 100, x2: 150, y2: 110 }, { x1: 50, y1: 112, x2: 150, y2: 122 }] };
  const whole = blockBoxes(p, 2), cut = blockBoxes({ ...p, foot: 120 }, 2);
  assert.equal(whole.box.height, 47, "22 pt and its pad");
  assert.equal(cut.box.height, 40, "from the box's top to the foot, the same pad above both");
  assert.deepEqual(cut.lines, whole.lines);
  assert.equal(blockBoxes({ ...p, foot: 140 }, 2).box.height, 47, "a foot past the box leaves it");
});

test("a block's boxes stand out from its lines and fill the leading between them", () => {
  const p = {
    size: 10, x1: 50, y1: 100, x2: 150, y2: 140,
    lines: [
      { x1: 50, y1: 100, x2: 150, y2: 110 }, // the next line is close: to its top
      { x1: 50, y1: 112, x2: 120, y2: 122 }, // the next line is far: its own bottom
      { x1: 50, y1: 135, x2: 90, y2: 140 },
    ],
  };
  const { box, lines } = blockBoxes(p, 2);
  assert.deepEqual(box, { left: 98.5, top: 198.5, width: 203, height: 83 });
  assert.deepEqual(lines, [
    { left: 0, top: 0, width: 203, height: 27 },
    { left: 0, top: 24, width: 143, height: 23 },
    { left: 0, top: 70, width: 83, height: 13 },
  ]);
  assert.deepEqual(blockBoxes({ ...p, lines: undefined }, 2).lines, [], "a block with no lines has no masks");
});

test("a line whose glyph box runs into the next one's keeps its own bottom", () => {
  // A title's first line reaches past the next line's top, which ends sooner.
  const p = {
    size: 28, x1: 50, y1: 100, x2: 400, y2: 170,
    lines: [{ x1: 50, y1: 100, x2: 400, y2: 138 }, { x1: 50, y1: 130, x2: 200, y2: 170 }],
  };
  const { lines } = blockBoxes(p, 1);
  assert.equal(lines[0].height, 38 + 1.5, "down to its own bottom, not the next line's top");
  assert.equal(lines[1].height, 40 + 1.5);
  // Clear of the next line, it still fills the leading between them.
  const q = { ...p, lines: [{ x1: 50, y1: 100, x2: 400, y2: 128 }, { x1: 50, y1: 134, x2: 200, y2: 170 }] };
  assert.equal(blockBoxes(q, 1).lines[0].height, 34 + 1.5);
});

test("a one-line block's room widens the box its text fills, and the mask stays on the line", () => {
  const p = { size: 10, x1: 100, y1: 200, x2: 160, y2: 210, lines: [{ x1: 100, y1: 200, x2: 160, y2: 210 }] };
  const grown = { ...p, room: { x1: 60, x2: 200 } };
  const before = blockBoxes(p, 2), after = blockBoxes(grown, 2);
  assert.equal(after.box.left, 60 * 2 - 1.5);
  assert.equal(after.box.width, 140 * 2 + 3);
  assert.equal(after.box.top, before.box.top);
  assert.equal(after.box.height, before.box.height);
  const at = ({ box, lines: [l] }) => [box.left + l.left, box.top + l.top, l.width, l.height];
  assert.deepEqual(at(after), at(before), "the same place on the page");
  const runs = [{ x: 110, y: 209, w: 20, h: 10 }, { x: 170, y: 209, w: 20, h: 10 }];
  assert.deepEqual([...maskedRuns(runs, [grown], [0])], [0], "the room masks nothing");
});

test("the overlay masks a block with text or a part gone before it, not a left, failed, waiting or figure block", () => {
  const e = entry(["译文", "", null, undefined, undefined, "标签"], {
    queued: true, failed: new Set([3]), left: new Set([2]), partial: { 4: "译" },
  });
  e.paras[5].translate = false;
  assert.deepEqual(maskedBlocks(e), [0, 1, 4]);
  e.partial = {};
  assert.deepEqual(maskedBlocks(e), [0, 1], "a paragraph waiting keeps its original");
});

test("the runs under a mask are those of its lines, with the leading; the rest of the page is the original's", () => {
  const para = (x1, y1) => ({
    size: 10, x1, y1, x2: x1 + 100, y2: y1 + 22,
    lines: [{ x1, y1, x2: x1 + 100, y2: y1 + 10 }, { x1, y1: y1 + 12, x2: x1 + 60, y2: y1 + 22 }],
  });
  const run = (x, y, over = {}) => ({ x, y, w: 40, h: 10, ...over });
  const paras = [para(50, 100), para(50, 300)];
  const runs = [
    run(50, 108), // on the first line
    run(60, 120), // on the second
    run(150, 120), // beside the second, which is shorter
    run(50, 308), // on the block that is not masked
    run(60, 108, { rotated: true }), // an arXiv stamp across it
  ];
  assert.deepEqual([...maskedRuns(runs, paras, [0])], [0, 1]);
  assert.deepEqual([...maskedRuns(runs, paras, [])], []);
});

test("the boxes scale as a whole, so the runs under a mask are what it covers at any zoom", () => {
  const p = { size: 10, x1: 50, y1: 100, x2: 150, y2: 110, lines: [{ x1: 50, y1: 100, x2: 150, y2: 110 }] };
  const at = (scale) => blockBoxes(p, scale);
  const half = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v / REF_SCALE]));
  assert.deepEqual(half(at(REF_SCALE).box), at(1).box, "the pad scales with the rest");
  assert.deepEqual(half(at(REF_SCALE).lines[0]), at(1).lines[0]);
  const { box, lines } = at(REF_SCALE);
  // A run's middle past the line's end by less than the pad, in pt.
  const run = { x: 140, y: 106, w: 20.8, h: 10 };
  assert.ok((box.left + lines[0].left + lines[0].width) / REF_SCALE >= run.x + run.w / 2, "the mask reaches it");
  assert.deepEqual([...maskedRuns([run], [p], [0])], [0]);
  assert.deepEqual([...maskedRuns([{ ...run, w: 22 }], [p], [0])], [], "and goes no further");
});

const lines = (...tops) => tops.map((y1) => ({ x1: 0, x2: 100, y1, y2: y1 + 9 }));

test("Latin and Cyrillic take the block's own pitch, within what papers set; CJK keeps its own", () => {
  const block = (...tops) => ({ size: 10, lines: lines(...tops) });
  assert.equal(blockLeading(block(0, 12, 24), "de"), 1.2);
  assert.equal(blockLeading(block(0, 12, 24), "ru"), 1.2);
  assert.equal(blockLeading(block(0, 12, 24, 36, 60), "en"), 1.2, "a wide gap in it is not its pitch");
  assert.equal(blockLeading(block(0, 10, 20), "fr"), 1.12, "held at the tight end");
  assert.equal(blockLeading(block(0, 20, 40), "fr"), 1.3, "and the loose end");
  assert.equal(blockLeading(block(0), "de"), 1.15, "one line has no pitch");
  for (const lang of ["zh-CN", "zh-TW", "ja", "ko"]) assert.equal(blockLeading(block(0, 12, 24), lang), 1.28);
});

test("a narrow box is not justified, and an opener's indent is the page's, in px of the layout scale", () => {
  const block = { size: 10, x1: 0, x2: 250, lines: lines(0, 12), indent: 12 };
  assert.deepEqual(blockSetting(block, "de"), { lead: 1.2, ragged: false, indent: 24, italic: false });
  assert.equal(blockSetting({ ...block, x2: 120 }, "de").ragged, true);
  assert.equal(blockSetting({ ...block, x2: 120, room: { x1: 0, x2: 250 } }, "de").ragged, false, "by the room it has");
  assert.equal(blockSetting({ ...block, indent: -14 }, "de").indent, 0, "a hanging one has no place to go");
  assert.equal(blockSetting({ ...block, indent: null }, "de").indent, 0, "one line has none");
  assert.equal(blockSetting({ ...block, centred: true }, "de").indent, 0, "a centred block's first line is only shorter");
});

test("a block's italic stays, but in CJK text, which has none", () => {
  const block = { size: 10, x1: 0, x2: 250, lines: lines(0, 12), italic: true };
  assert.equal(blockSetting(block, "de").italic, true);
  assert.equal(blockSetting(block, "zh-CN").italic, false);
  assert.equal(blockSetting(block, "ja").italic, false);
});

test("a formula inside the line keeps its box; a tall one hangs out of a smaller layout box", () => {
  const near = (a, b) => Object.entries(b).forEach(([k, v]) => assert.ok(Math.abs(a[k] - v) < 1e-9, `${k}: ${a[k]} against ${v}`));
  const span = (up, down) => ({ x1: 0, x2: 20, y1: 100 - up, y2: 100 + down, base: 100 });
  near(cropBox(span(7, 1), 10), { w: 2, h: 0.8, top: 0, bottom: 0, shift: -0.1 });
  const tall = cropBox(span(12, 5), 10);
  near(tall, { h: 1.7, top: -0.35, bottom: -0.3, shift: -0.2 });
  // The layout box is what the line sees: the drawn box less the margins.
  assert.ok(tall.h + tall.top + tall.bottom <= 1.05 + 1e-9);
  // The drawn box stays where it was: its bottom edge, below the baseline.
  assert.ok(Math.abs(tall.shift + tall.bottom - -0.5) < 1e-9);
});

test("a formula wholly above or below the line's room has a layout box of nothing, at the room's edge", () => {
  const span = (up, down) => ({ x1: 0, x2: 10, y1: 100 - up, y2: 100 + down, base: 100 });
  const above = cropBox(span(15, -11), 10);
  assert.ok(Math.abs(above.h + above.top + above.bottom) < 1e-9);
  assert.ok(Math.abs(above.shift - 0.85) < 1e-9);
  const below = cropBox(span(-5, 9), 10);
  assert.ok(Math.abs(below.h + below.top + below.bottom) < 1e-9);
  assert.ok(Math.abs(below.shift - -0.2) < 1e-9);
});
