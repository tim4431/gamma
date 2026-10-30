import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { segmentPage } from "../src/pdf/pdfTranslate.js";
import {
  BREAK, chunkUnits, continues, createPlanner, isCaption, isHeading, pageLayout, partials,
  readingOrder, requestChunk, requestFields, results,
} from "../src/pdf/translatePlan.js";

const PAPER = JSON.parse(fs.readFileSync(new URL("./fixtures/translatePaper.json", import.meta.url)));
const PAGES = new Map(PAPER.pages.map((p) => [p.pn, segmentPage(p.runs)]));
const at = (pn, s) => PAGES.get(pn).findIndex((b) => b.text.includes(s));

const PROSE = "the ensemble of atoms shares one excitation and loses its coherence when the blockade";
// A block as the segmenter makes it, for the rules' own tests.
const block = (text, { size = 10, nLines = 3, indent = 0, translate = true, x1 = 54, x2 = 300, y1 = 100, src } = {}) =>
  ({ text, src: src ?? text, spans: [], size, nLines, indent: nLines > 1 ? indent : null, translate,
     x1, x2, y1, y2: y1 + nLines * 11.5, lines: [] });

test("reading order goes down the left column before the right one", () => {
  const blocks = PAGES.get(1);
  const order = readingOrder(blocks).map((i) => blocks[i].text);
  const pos = (s) => order.findIndex((t) => t.includes(s));
  assert.ok(pos("We study how") < pos("I. INTRODUCTION"));
  assert.ok(pos("I. INTRODUCTION") < pos("Ensembles of neutral atoms"));
  assert.ok(pos("We consider N identical atoms") < pos("Hamiltonian reads"), "left column ends before the right one starts");
  assert.ok(pos("Hamiltonian reads") < pos("III. RESULTS"));
});

test("a full-width band of whitespace starts a region: the reference list reads after both columns", () => {
  const blocks = PAGES.get(2);
  const { order, col } = pageLayout(blocks);
  const conclusion = at(2, "We have shown that"), carried = at(2, "with a rate that scales");
  const refs = blocks.findIndex((b) => b.text.startsWith("[1]"));
  assert.ok(order.indexOf(conclusion) < order.indexOf(carried));
  assert.ok(order.indexOf(carried) < order.indexOf(refs));
  assert.notEqual(col[conclusion], col[carried]);
  assert.notEqual(col[conclusion].split(".")[0], col[refs].split(".")[0]);
});

test("a gap beside a figure is no region break: the column under the figure reads first", () => {
  // A figure fills the left column's top; the right column has a heading
  // with space around it there.
  const blocks = [
    block("right column paragraph at the top of the page " + PROSE, { x1: 316, x2: 562, y1: 60, nLines: 6 }),
    block("4. Comparison", { x1: 316, x2: 420, y1: 150, nLines: 1 }),
    block("right column paragraph after the heading " + PROSE, { x1: 316, x2: 562, y1: 180, nLines: 12 }),
    block("of sigma polarized light, left column text under the figure " + PROSE, { x1: 54, x2: 300, y1: 300, nLines: 10 }),
  ];
  const order = readingOrder(blocks);
  assert.deepEqual(order, [3, 0, 1, 2]);
});

test("headings and captions are told from running text", () => {
  assert.equal(isHeading(block("II. MODEL", { nLines: 1 }), 10), true);
  assert.equal(isHeading(block("Acknowledgments", { nLines: 1 }), 10), true);
  assert.equal(isHeading(block("Hamiltonian reads", { nLines: 1 }), 10), false);
  for (const c of ["TABLE I. Fitted cloud widths.", "FIG. 2. The measured decay", "FIG. S.2. Detection fidelity",
                   "Figure A1. The setup", "Fig. A.1. Optics", "Supplemental Information Fig. S3. left the profile",
                   "Extended Data Fig. 3 | Spectra", "Figure 4: Results"]) {
    assert.equal(isCaption(block(c)), true, c);
  }
  for (const c of ["FIG. 1 (color). (a) Relevant levels", "FIG. 3 (color online). Accuracy and loss",
                   "FIGURE 1 (a) Schematic illustration", "Fig. 1 Conceptual overview. a Illustration", "TABLE II Parameters"]) {
    assert.equal(isCaption(block(c)), true, c);
  }
  for (const t of ["Figure 1 compares the predicted rate", "Figs. 2–4 show the decay", "Tables 1–3 summarize the fits",
                   "Fig. 4 (solid line in (a)) along with the fit", "Figs. 2 Panels"]) {
    assert.equal(isCaption(block(t)), false, t);
  }
});

test("a paragraph goes on in the next block only when it breaks off mid-sentence", () => {
  const a = block(`${PROSE}. In the rotating frame the`);
  assert.equal(continues(a, block("Hamiltonian reads", { nLines: 1 }), 10), true, "one capitalised line after a word no sentence ends on");
  assert.equal(continues(block(`${PROSE} a weighted sum`), block("Scaled Dot-Product Attention", { nLines: 1 }), 10), false);
  assert.equal(continues(block(`${PROSE}. It ends here.`), block(`next paragraph starts ${PROSE}`), 10), false);
  assert.equal(continues(a, block(`opens a paragraph ${PROSE}`, { indent: 10 }), 10), false);
  assert.equal(continues(a, block("III. RESULTS", { nLines: 1 }), 10), false);
  assert.equal(continues(a, block(`smaller footnote text ${PROSE}`, { size: 8 }), 10), false);
  assert.equal(continues(block("one line", { nLines: 1 }), block(`goes on ${PROSE}`), 10), false);
  assert.equal(continues(a, block(`goes on without indent ${PROSE}`), 10), true);
  // A hyphenated word goes on in lower case only: not into an axis label.
  const hyphen = block(`${PROSE} creating a dipole trap at the small waist lo-`);
  assert.equal(continues(hyphen, block("Light-Cavity Detuning (MHz)", { nLines: 1 }), 10), false);
  assert.equal(continues(hyphen, block("cation (see SI D)", { nLines: 1 }), 10), true);
  // Labels and table cells are no running text.
  assert.equal(continues(block("Dichroic: 780/785", { nLines: 2 }), block("Pellicle beamsplitter", { nLines: 2 }), 10), false);
  assert.equal(continues(block(`${PROSE} the`), block("Rb Rb Cs Rb K Li Sr Sr", { nLines: 2 }), 10), false);
  // A one-line B is a paragraph's last line, or introduces an equation;
  // a narrow label after "…will be" is neither.
  const be = block(`${PROSE}, a possible realization will be`);
  assert.equal(continues(be, block("Time, T", { nLines: 1 }), 10, { wide: false }), false);
  assert.equal(continues(be, block("Protocol", { nLines: 1 }), 10, { wide: false, intro: true }), true);
  assert.equal(continues(be, block("given by the ratio of the rates.", { nLines: 1 }), 10, { wide: false }), true);
  // Without indents on the page, a capitalised B opens a paragraph.
  const reveal = block(`${PROSE}. These data reveal`);
  const next = block(`At the full trap depth we measure a coherence time ${PROSE}`);
  assert.equal(continues(reveal, next, 10, { spaced: true }), false);
  assert.equal(continues(reveal, next, 10, { spaced: false }), true);
  // A citation superscript after the period still ends the sentence.
  assert.equal(continues(block(`${PROSE} and coherent atom rearrangement.45`, { src: `${PROSE} and coherent atom rearrangement.{v1}` }),
    block(`furthermore such methods ${PROSE}`), 10), false);
});

test("the fixture's paragraphs join across both column breaks and the page break", () => {
  const planner = createPlanner({ paras: (pn) => PAGES.get(pn) || null, inJob: (pn) => PAGES.has(pn) });
  const units = [...planner.planPage(1), ...planner.planPage(2)];
  const joined = units.filter((u) => u.targets.length > 1);
  assert.deepEqual(joined.map((u) => u.targets.map((t) => t.pn)), [[1, 1], [1, 2], [2, 2]]);
  const [column, page] = joined;
  assert.ok(column.src.includes(`In the rotating frame the ${BREAK} Hamiltonian reads`), column.src);
  assert.ok(page.src.includes(`design rule for ${BREAK} superatom experiments`), page.src);
  // Every translatable block is planned exactly once.
  const planned = units.flatMap((u) => u.targets.map((t) => `${t.pn}:${t.i}`));
  assert.equal(new Set(planned).size, planned.length);
  const translatable = [...PAGES].flatMap(([pn, bs]) => bs.map((b, i) => (b.translate ? `${pn}:${i}` : null)).filter(Boolean));
  assert.deepEqual([...planned].sort(), translatable.sort());
});

test("a unit renumbers its blocks' formula tokens and carries its context", () => {
  const planner = createPlanner({ paras: (pn) => PAGES.get(pn) || null, inJob: (pn) => PAGES.has(pn) });
  const units = [...planner.planPage(1), ...planner.planPage(2)];
  const column = units.find((u) => u.src.includes(`rotating frame the ${BREAK} Hamiltonian`));
  const numbers = [...column.src.matchAll(/\{v(\d+)\}/g)].map((m) => Number(m[1]));
  assert.deepEqual(numbers, numbers.map((_, k) => k + 1), "one sequence across the parts");
  assert.ok(column.tokens.every((t) => column.targets.some((x) => x.pn === t.pn && x.i === t.i)));
  const where = units.find((u) => u.src.startsWith("where"));
  const equation = PAGES.get(1).find((b) => !b.translate && b.text.includes("Vij ninj"));
  assert.equal(where.before, equation.text.trim(), "the text before a 'where' paragraph is its equation");
  assert.equal(where.section, "II. MODEL");
  const chunks = chunkUnits(units);
  assert.ok(chunks.every((c) => c.texts.length <= 10));
  assert.deepEqual(Object.keys(chunks[0].context), ["before", "after", "section"]);
});

test("a running head at body size is furniture: a page-break join skips it", () => {
  const prev = [block(`${PROSE} of the cloud is described by the`, { nLines: 20, y1: 60 })];
  const next = [
    block("SMITH et al.", { nLines: 1, y1: 40, x1: 260, x2: 330 }),
    block(`thermal distribution of the atoms in the trap ${PROSE}`, { nLines: 20, y1: 70 }),
  ];
  const pages = new Map([[1, prev], [2, next]]);
  const units = createPlanner({ paras: (pn) => pages.get(pn) || null }).planPage(1);
  assert.deepEqual(units[0].targets, [{ pn: 1, i: 0 }, { pn: 2, i: 1 }]);
});

test("a page planned before the page its paragraph starts on still joins it, once", () => {
  const planner = createPlanner({ paras: (pn) => PAGES.get(pn) || null, inJob: () => true });
  const first = planner.planPage(2);
  const carried = first.find((u) => u.targets.some((t) => t.pn === 1));
  assert.deepEqual(carried.targets, [{ pn: 1, i: at(1, "Figure 1 compares") }, { pn: 2, i: at(2, "superatom experiments") }]);
  assert.ok(!planner.planPage(1).some((u) => u.targets.some((t) => t.pn === 2)));
});

test("walking back to a long paragraph's start never drops the block being planned", () => {
  const long = (n) => `${PROSE} `.repeat(Math.ceil(n / PROSE.length)).slice(0, n).trim();
  const pages = new Map([[1, [block(`${long(4400)} the`, { nLines: 40 })]], [2, [block(`continued ${long(3300)}.`, { nLines: 30 })]]]);
  const planner = createPlanner({ paras: (pn) => pages.get(pn) || null });
  const second = planner.planPage(2);
  assert.ok(second.some((u) => u.targets.some((t) => t.pn === 2 && t.i === 0)), "page 2's block is planned");
  const first = planner.planPage(1);
  assert.deepEqual(first.flatMap((u) => u.targets), [{ pn: 1, i: 0 }]);
});

test("a page outside the job is never joined into; filled blocks are skipped", () => {
  const alone = createPlanner({ paras: (pn) => PAGES.get(pn) || null, inJob: (pn) => pn === 1 }).planPage(1);
  assert.ok(alone.every((u) => u.targets.every((t) => t.pn === 1)));
  const own = createPlanner({ paras: (pn) => (pn === 1 ? PAGES.get(1) : null) }).planPage(1);
  assert.equal(own.filter((u) => u.targets.length > 1).length, 1, "the column join stays");
  const skip = at(1, "Ensembles of neutral atoms");
  const units = createPlanner({ paras: (pn) => PAGES.get(pn) || null, filled: (pn, i) => pn === 1 && i === skip }).planPage(1);
  assert.ok(!units.some((u) => u.targets.some((t) => t.i === skip)));
});

test("chunks close at their unit or character limit and carry the edges' context", () => {
  const unit = (n, k) => ({ src: "x".repeat(n), chars: n, parts: ["x"], targets: [{ pn: 1, i: k }], tokens: [],
                            before: `before ${k}`, after: `after ${k}`, section: `section ${k}` });
  const chunks = chunkUnits([unit(10, 0), unit(10, 1), unit(10, 2)], { maxUnits: 2 });
  assert.deepEqual(chunks.map((c) => c.texts.length), [2, 1]);
  assert.deepEqual(chunks[0].context, { before: "before 0", after: "after 1", section: "section 0" });
  assert.deepEqual(chunkUnits([unit(1300, 0), unit(1300, 1), unit(10, 2)], { maxChars: 2500 }).map((c) => c.texts.length), [2, 1]);
});

test("a request's fields name the paper, whether it waits for the brief and whether the server may keep it", () => {
  assert.deepEqual(requestFields({ pageId: "p", waitBrief: false, before: "b", section: "" }),
    { page_id: "p", wait_brief: false, keep: true, before: "b" });
  assert.deepEqual(requestFields({ head: "Opening text", keep: false, after: "a", section: "1 Introduction" }),
    { head: "Opening text", wait_brief: true, keep: false, after: "a", section: "1 Introduction" });
  assert.deepEqual(requestFields(), { wait_brief: true, keep: true }, "by default a request waits for the brief and may be kept");
});

const unit2 = (parts, tokens = []) => ({ parts, targets: parts.map((_, k) => ({ pn: 1, i: k })), tokens });

test("a joined translation is split back at the marker, or by length when the model lost it", () => {
  const u = unit2(["In the rotating frame the", "Hamiltonian reads"]);
  assert.deepEqual(results(u, "在旋转坐标系中，{br}哈密顿量为").map((r) => r.text), ["在旋转坐标系中，", "哈密顿量为"]);
  const [x, y] = results(u, "在旋转坐标系中，哈密顿量写作如下形式。").map((r) => r.text);
  assert.equal(x + y, "在旋转坐标系中，哈密顿量写作如下形式。");
  assert.equal(x, "在旋转坐标系中，", "cut at the clause mark near the source's share");
  // Doubled markers: no space appears between CJK characters.
  assert.deepEqual(results(u, "在旋转坐标系中，{br}驱动系统的{br}哈密顿量为").map((r) => r.text).join(""), "在旋转坐标系中，驱动系统的哈密顿量为");
  assert.ok(results(u, "only one {br}").every((r) => r.text), "an empty half is refilled by length");
  // Never cut inside a number or after an abbreviation.
  const de = unit2(["Für eine kugelförmige Wolke erreicht die Blockadetreue", "0.987 bei der optimalen Rabi-Frequenz und fällt dann ab."]);
  for (const r of results(de, "Für eine kugelförmige Wolke erreicht die Blockadetreue 0.987 bei der optimalen Rabi-Frequenz, wie in Abb. 3 gezeigt, und fällt dann ab.")) {
    assert.ok(!/^\d|\d\.$|Abb\.$/.test(r.text), r.text);
  }
  // Nor at a comma between digits.
  const comma = unit2(["Die Blockadetreue erreicht den Wert", "0,987 und 1,000 Zählungen bei der optimalen Frequenz."]);
  for (const r of results(comma, "Die Blockadetreue erreicht den Wert 0,987 und 1,000 Zählungen bei der optimalen Frequenz.")) {
    assert.ok(!/\d,$/.test(r.text) && !/^\d/.test(r.text), r.text);
  }
  // A three-part chain keeps both markers.
  assert.deepEqual(results(unit2(["a", "b", "c"]), "甲{br}乙{br}丙").map((r) => r.text), ["甲", "乙", "丙"]);
});

test("a block that only held a hyphenated word's tail is masked, not re-sent", () => {
  const u = unit2(["the blockade fails at large separation.", ""]);
  assert.deepEqual(results(u, "阻塞在大间距时失效。").map((r) => r.text), ["阻塞在大间距时失效。", ""]);
  assert.deepEqual(results(unit2(["same", "text"]), "same {br} text").map((r) => r.text), [null, null], "a reply equal to the source is untranslated");
});

test("tokens land in the block they belong to, by its own numbers", () => {
  const u = { parts: ["where {v1} is", "and {v2} the rate"], targets: [{ pn: 1, i: 4 }, { pn: 2, i: 0 }],
              tokens: [{ pn: 1, i: 4, k: 3 }, { pn: 2, i: 0, k: 1 }] };
  assert.deepEqual(results(u, "其中 {v1} 是{br}而 {v2} 为速率").map((r) => r.text), ["其中 {v3} 是", "而 {v1} 为速率"]);
  assert.deepEqual(results(u, "其中 {v1} 和 {v2}{br}是速率").map((r) => r.text), ["其中 {v3} 和 {v:2.0.1}", "是速率"]);
  assert.deepEqual(partials(u, "其中 {v1} 是{br}而 {v"), [{ pn: 1, i: 4, text: "其中 {v3} 是" }, { pn: 2, i: 0, text: "而" }]);
  assert.deepEqual(partials(u, "其中 {v1} 是{b"), [{ pn: 1, i: 4, text: "其中 {v3} 是" }]);
});

// A request answering with the given outcomes in turn: an Error is thrown,
// anything else returned.
const answering = (...outcomes) => {
  const run = async () => {
    run.calls += 1;
    const o = outcomes.shift();
    if (o instanceof Error) throw o;
    return o;
  };
  run.calls = 0;
  return run;
};
const refused = (retryAfter) => Object.assign(new Error("Too many AI requests"), { status: 429, retryAfter });
const quick = { pauseMs: 1 };

test("a chunk's failed request is asked once more, then the failure stands", async () => {
  const ok = { translations: ["x"] };
  let run = answering(new Error("network"), ok);
  assert.equal(await requestChunk(run, null, quick), ok);
  assert.equal(run.calls, 2);
  run = answering(new Error("network"), new Error("still down"), ok);
  await assert.rejects(requestChunk(run, null, quick), /still down/);
  assert.equal(run.calls, 2);
  // A stream cut off before its final line counts as a failure too.
  run = answering(null, null);
  assert.equal(await requestChunk(run, null, quick), null);
  assert.equal(run.calls, 2);
});

test("a refusal because the account's calls are all in use waits as the server says", async () => {
  const ok = { translations: ["x"] };
  let run = answering(refused(0.005), refused(0.005), refused(0.005), new Error("blip"), ok);
  assert.equal(await requestChunk(run, null, quick), ok, "the waits leave the one plain retry");
  assert.equal(run.calls, 5);
  // Past its patience the refusal stands; a 429 without Retry-After (the
  // shared allowance used up) at once.
  run = answering(refused(0.005), ok);
  await assert.rejects(requestChunk(run, null, { pauseMs: 1, patienceMs: 0 }), /Too many/);
  run = answering(Object.assign(new Error("allowance used up"), { status: 429 }), ok);
  await assert.rejects(requestChunk(run, null, quick), /allowance/);
  assert.equal(run.calls, 1);
});

test("halting ends a request's wait without asking again", async () => {
  const ctl = new AbortController();
  const run = answering(refused(60), { translations: ["x"] });
  const asked = requestChunk(run, ctl.signal);
  setTimeout(() => ctl.abort(), 5);
  assert.equal(await asked, null);
  assert.equal(run.calls, 1);
});
