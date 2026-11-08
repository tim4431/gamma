// Counts app/App.jsx's state and effect hooks and its code lines, for the
// ratchet in tests/appState.test.mjs (docs/dev/frontend-refactor.md, "New
// state"): new state goes into an owner beside its feature, not into App.
//
//   node tools/appState.mjs              # App's hooks by name, its code lines and their ceiling
//   node tools/appState.mjs --list       # every counted hook call, file:line: hook
//   node tools/appState.mjs --baseline   # record a lower count in tests/app-state-baseline.json
//
// What counts, per file of FILES:
//   hooks  calls of the hooks that hold state or run behaviour (STATE,
//          EFFECTS), as one sum, so moving a value from a state to a ref is
//          net zero. Besides React's, STATE has the shared helpers that each
//          hold one state for their caller: usePersistedState and
//          usePersistedFlag (a useState over localStorage, shared/lib/utils.js)
//          and useCopied (a copy's flash, shared/ui/Widgets.jsx).
//          INFO's hooks are printed but not counted: useMemo, useCallback and
//          the shared glue like them (useStableActions, useWheelPan's callback
//          ref, useLatch, a lazy surface's mount latch), useSyncExternalStore
//          (how App reads a store) and useTextScale (a shared owner). A
//          feature's owner hooks are neither.
//   unknown  the hooks a file imports from React or shared/ that are in
//          neither list. The test fails on one until a list has it.
//   lines  code lines: lines with code on them once comments are gone, JSX
//          comments with their braces, so a comment costs nothing.
//
// The baseline holds the hook count exactly: more fails, and fewer fails
// until --baseline records it. Its lines is a ceiling, the smallest size
// recorded plus HEADROOM. --baseline never raises either: it refuses while a
// hook count is above the baseline, and the ceiling only falls. A change to
// what STATE or EFFECTS hold is recorded by hand in the baseline, in the
// same commit.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(FRONTEND, "src");
export const BASELINE = path.join(FRONTEND, "tests", "app-state-baseline.json");
export const FILES = ["app/App.jsx"];
export const STATE = ["useState", "useReducer", "useRef", "usePersistedState", "usePersistedFlag", "useCopied"];
export const EFFECTS = ["useEffect", "useLayoutEffect"];
export const INFO = ["useMemo", "useCallback", "useStableActions", "useWheelPan", "useLatch", "useSyncExternalStore", "useTextScale"];
export const HEADROOM = 1000;
const COUNTED = [...STATE, ...EFFECTS];

// Comments, strings, template literals and regex literals in one pass, so
// that none of them is read inside another ("//" in a string, a quote in a
// regex). A JSX comment is a comment with its braces. A slash starts a regex
// literal where an expression starts: at a line's start, after an operator,
// an opening bracket, an arrow, `return` or `case`. After a closing bracket
// it divides, after < or another > it is a JSX tag's, and so is a slash
// before >.
const LEXEMES = new RegExp([
  /\{\s*\/\*(?:[^*]|\*(?!\/))*\*\/\s*\}/.source,
  /\/\/[^\n]*/.source,
  /\/\*[\s\S]*?\*\//.source,
  /"(?:[^"\\\n]|\\[\s\S])*"/.source,
  /'(?:[^'\\\n]|\\[\s\S])*'/.source,
  /`(?:[^`\\]|\\[\s\S])*`/.source,
  /(?<=(?:^|[=(,:;!&|?{[+\-*%~^]|=>|\b(?:return|case))\s*)\/(?![*/>])(?:[^/\\\n[]|\\.|\[(?:[^\]\\\n]|\\.)*\])+\/[a-z]*/.source,
].join("|"), "gm");

const blank = (s) => s.replace(/[^\n]/g, " ");

// The source with its comments blanked, line breaks kept; with `strings`,
// strings, template literals and regex literals blanked too.
export function code(source, { strings = false } = {}) {
  return source.replace(LEXEMES, (m) => (strings || /^(?:\/[/*]|\{)/.test(m) ? blank(m) : m));
}

const CALL = new RegExp(String.raw`\b(${[...COUNTED, ...INFO].join("|")})\s*\(`, "g");
const IMPORT = /\bimport\s+([^;]*?)\s+from\s+["']([^"']+)["']/g;
const HOOK = /^use[A-Z]/;

// The hooks `source` imports from React or shared/ (by their local names,
// with the useX calls on React or on a namespace import, `W.useX`) that
// neither COUNTED nor INFO has. A feature's owner hooks come from its own
// folder and are not asked about.
function unknownHooks(source) {
  const text = code(source);
  const names = new Set(), spaces = new Set(["React"]);
  for (const [, what, from] of text.matchAll(IMPORT)) {
    if (from !== "react" && !/(?:^|\/)shared\//.test(from)) continue;
    for (const spec of what.replace(/[{}]/g, ",").split(",")) {
      const [name, local = name] = spec.trim().split(/\s+as\s+/);
      if (name === "*") spaces.add(local);
      else if (HOOK.test(name)) names.add(local);
    }
  }
  const call = new RegExp(String.raw`\b(?:${[...spaces].join("|")})\s*\.\s*(use[A-Z]\w*)`, "g");
  for (const m of code(source, { strings: true }).matchAll(call)) names.add(m[1]);
  return [...names].filter((h) => !COUNTED.includes(h) && !INFO.includes(h)).sort();
}

// { hooks, byHook, lines, at, unknown }: the counted sum, every hook's calls
// (INFO's too), the code lines, "line: hook" for each counted call, and the
// hooks from React or shared/ that no list has.
export function measure(source) {
  const text = code(source, { strings: true });
  const byHook = Object.fromEntries([...COUNTED, ...INFO].map((h) => [h, 0]));
  const at = [];
  let line = 1, from = 0;
  for (const m of text.matchAll(CALL)) {
    for (let i = from; i < m.index; i++) if (text[i] === "\n") line++;
    from = m.index;
    byHook[m[1]]++;
    if (COUNTED.includes(m[1])) at.push(`${line}: ${m[1]}`);
  }
  const hooks = COUNTED.reduce((n, h) => n + byHook[h], 0);
  const lines = code(source).split("\n").filter((l) => l.trim()).length;
  return { hooks, byHook, lines, at, unknown: unknownHooks(source) };
}

// { file: measure } over the tracked files, paths relative to src/.
export function scan(files = FILES) {
  return Object.fromEntries(files.map((f) => [f, measure(fs.readFileSync(path.join(SRC, f), "utf8"))]));
}

// What --baseline writes for `found` over the recorded `base`, and the files
// whose hooks rose ({ file, hooks, was }), whose recorded count is kept. The
// ceiling is the lower of the recorded one and this size plus HEADROOM; a
// file new to the baseline starts at its size plus HEADROOM.
export function record(base, found) {
  const baseline = {}, rose = [];
  for (const [file, { hooks, lines }] of Object.entries(found)) {
    const was = base[file];
    if (was && hooks > was.hooks) rose.push({ file, hooks, was: was.hooks });
    baseline[file] = {
      hooks: was ? Math.min(was.hooks, hooks) : hooks,
      lines: Math.min(was?.lines ?? Infinity, lines + HEADROOM),
    };
  }
  return { baseline, rose };
}

export const readBaseline = () => JSON.parse(fs.readFileSync(BASELINE, "utf8"));

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const found = scan();
  const base = fs.existsSync(BASELINE) ? readBaseline() : {};
  if (args[0] === "--baseline") {
    const { baseline, rose } = record(base, found);
    if (rose.length) {
      for (const r of rose) console.error(`${r.file}: ${r.hooks} state and effect hooks, the baseline ${r.was}`);
      console.error("Nothing written. New state goes into an owner module beside its feature (docs/dev/frontend-refactor.md \"New state\").");
      process.exit(1);
    }
    fs.writeFileSync(BASELINE, JSON.stringify(baseline, null, 2) + "\n");
    console.log(`wrote ${path.relative(FRONTEND, BASELINE)}`, baseline);
  } else if (args[0] === "--list") {
    for (const [file, { at }] of Object.entries(found)) for (const x of at) console.log(`${file}:${x}`);
  } else {
    const pad = Math.max(...[...COUNTED, ...INFO].map((h) => h.length));
    for (const [file, m] of Object.entries(found)) {
      const b = base[file];
      console.log(`${file}: ${m.hooks} state and effect hooks (baseline ${b?.hooks ?? "none"}), ${m.lines} code lines (ceiling ${b?.lines ?? "none"})`);
      for (const h of COUNTED) console.log(`  ${h.padEnd(pad)} ${String(m.byHook[h]).padStart(4)}`);
      for (const h of INFO) console.log(`  ${h.padEnd(pad)} ${String(m.byHook[h]).padStart(4)}  not counted`);
      if (m.unknown.length) console.log(`  in neither STATE, EFFECTS nor INFO: ${m.unknown.join(", ")}`);
    }
  }
}
