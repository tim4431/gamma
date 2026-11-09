// The App ratchet (docs/dev/frontend-refactor.md, "New state"): new state
// goes into an owner module beside its feature, so app/App.jsx's state and
// effect hooks may not grow, and its code lines stay under a ceiling.
// tools/appState.mjs counts them; tests/app-state-baseline.json records the
// hook count exactly and the ceiling.
import test from "node:test";
import assert from "node:assert/strict";
import { FILES, HEADROOM, measure, readBaseline, record, scan } from "../tools/appState.mjs";

const hooks = (src) => measure(src).hooks;

test("the counter takes state and effect hooks and leaves the rest", () => {
  for (const call of [
    "const [a, setA] = useState(0);",
    "const [a] = React.useState(0);",
    "const [s, dispatch] = useReducer(reduce, {});",
    "const r = useRef(null);",
    'const [w, setW] = usePersistedState("gamma-w", 300);',
    'const [o, setO] = usePersistedFlag("gamma-o", false);',
    "const [c, flash, reset] = useCopied();",
    "useEffect(() => {}, []);",
    "useLayoutEffect (() => {});",
  ]) assert.equal(hooks(call), 1, call);
  for (const call of [
    "const v = useMemo(() => 1, []);",
    "const f = useCallback(() => {}, []);",
    "const p = useSyncExternalStore(sub, get);",
    "const a = useStableActions({ f });",
    "const pan = useWheelPan();",
    "const mounted = useLatch(open);",
    "const scale = useTextScale({});",
    "const strip = useInkStrip({});",
    "myuseState(0); useStateful(0);",
    "const useState = 1;",
    "// useState(0) in a comment",
    "/* useEffect(() => {}) in a block */",
    "<div>{/* useRef(null) in JSX */}</div>",
    // biome-ignore lint/suspicious/noTemplateCurlyInString: a source snippet the scanner reads as text, not a template
    'const s = "useState(0)"; const t = \'useRef(1)\'; const u = `useEffect(${x})`;',
  ]) assert.equal(hooks(call), 0, call);
  // What comes after a URL, a regex literal, a template literal or a
  // division is still read as code. A slash misread as a regex, or a regex
  // misread as a division, leaves a backtick unpaired, and the code up to
  // the next template would read as a template's text.
  for (const before of [
    'const u = "http://example.com";',
    "const re = /\\/\\//;",
    "const re = /`/;",
    "const re = /[\"']/g;",
    "const f = (s) => /```mermaid/.test(s);",
    // biome-ignore lint/suspicious/noTemplateCurlyInString: a source snippet the scanner reads as text, not a template
    "const t = `a ${b ? `c ${d}` : \"e\"} f`;",
    "function f(s) { return /`/.test(s); }",
    "const n = a / b / c;",
    "const n = (a) / 2, s = `/`;",
    "const n = arr[0] / 2, s = `/`;",
    "<i>{n}/{total} {`a/b`}</i>",
    "<Foo\n  a={x}\n/>; const s = `/`;",
  ]) assert.equal(hooks(`${before}\nuseState(0);\nconst z = \`z\`;`), 1, before);
  assert.deepEqual(measure("useState(0);\n\n// x\nuseEffect(() => {});").at, ["1: useState", "4: useEffect"]);
  assert.equal(measure("useMemo(() => 1); useCallback(f);").byHook.useMemo, 1, "INFO's hooks are reported");
});

test("a hook from React or shared/ that no list has is named", () => {
  const src = [
    'import React, { useState, useId, useDeferredValue as deferred } from "react";',
    'import { CopyBox, useCopied, useToggle } from "../shared/ui/Widgets";',
    'import useBoxes from "../shared/ui/boxes.js";',
    'import { useInkStrip } from "../ink/useInkStrip.js";',
    'import * as W from "../shared/ui/Widgets.jsx";',
    'import * as inkLive from "../ink/inkLive";',
    '// import { useCommented } from "../shared/x";',
    "const [p, start] = React.useTransition();",
    "const [on, toggle] = W.useToggle2(); const c = W.useCopied();",
    "const live = inkLive.useLive();",
  ].join("\n");
  assert.deepEqual(measure(src).unknown, ["deferred", "useBoxes", "useId", "useToggle", "useToggle2", "useTransition"]);
});

test("comment and blank lines are not code lines", () => {
  const src = [
    "// a comment",
    "",
    "/* a block",
    "   over lines */",
    "const a = 1; // a trailing comment",
    "   ",
    "const s = `a template",
    "",
    "line`;",
    "<div>",
    "  {/* a JSX comment */}",
    "  {/* over",
    "      lines */}",
    "  {/* not alone */ x}",
    "</div>",
  ].join("\n");
  assert.equal(measure(src).lines, 6);
});

test("--baseline never records a rise", () => {
  const base = { "app/App.jsx": { hooks: 10, lines: 1500 } };
  const at = (hooks, lines) => ({ "app/App.jsx": { hooks, lines } });
  assert.deepEqual(record(base, at(11, 400)), {
    baseline: { "app/App.jsx": { hooks: 10, lines: 1400 } },
    rose: [{ file: "app/App.jsx", hooks: 11, was: 10 }],
  });
  assert.deepEqual(record(base, at(8, 600)), { baseline: at(8, 1500), rose: [] }, "the ceiling only falls");
  assert.deepEqual(record(base, at(8, 400)), { baseline: at(8, 400 + HEADROOM), rose: [] });
  assert.deepEqual(record({}, at(8, 400)), { baseline: at(8, 400 + HEADROOM), rose: [] }, "a new file starts at its size plus HEADROOM");
});

test("the baseline names the tracked files", () => {
  assert.deepEqual(Object.keys(readBaseline()).sort(), [...FILES].sort(), "after a change to FILES, node tools/appState.mjs --baseline records them");
});

const base = readBaseline();
const found = scan();
// A tracked file missing from the baseline fails the test above.
const tracked = FILES.filter((f) => base[f]);

test("every hook App takes from React or shared/ is counted or printed", () => {
  const unknown = FILES.filter((f) => found[f].unknown.length).map((f) =>
    `${f} imports ${found[f].unknown.join(", ")} from React or shared/, which tools/appState.mjs neither counts nor prints. A hook that holds a value for its caller, as useCopied does, goes into STATE (or EFFECTS), and the baseline's hooks take the new count by hand in this commit. Glue such as useStableActions, and a shared owner such as useTextScale, go into INFO (docs/dev/frontend-refactor.md "New state").`);
  assert.deepEqual(unknown, []);
});

test("App gains no state or effect hooks", () => {
  const over = tracked.filter((f) => found[f].hooks > base[f].hooks).map((f) =>
    `${f} holds ${found[f].hooks} state and effect hooks, the baseline ${base[f].hooks}. New state goes into an owner module beside its feature, a hook or a store whose pure part has a node test (docs/dev/frontend-refactor.md "New state"); a fix folds its value into state already held. node tools/appState.mjs --list prints the counted lines.`);
  assert.deepEqual(over, []);
});

test("the baseline records App's lower hook count", () => {
  const under = tracked.filter((f) => found[f].hooks < base[f].hooks).map((f) =>
    `${f} holds ${found[f].hooks} state and effect hooks, the baseline ${base[f].hooks}: record the lower count with node tools/appState.mjs --baseline, in this commit.`);
  assert.deepEqual(under, []);
});

test("App stays under its line ceiling", () => {
  const over = tracked.filter((f) => found[f].lines > base[f].lines).map((f) =>
    `${f} has ${found[f].lines} code lines, its ceiling ${base[f].lines}: move code out of App first, in a commit of its own (docs/dev/frontend-refactor.md "New state"). Comment and blank lines do not count.`);
  assert.deepEqual(over, []);
});
