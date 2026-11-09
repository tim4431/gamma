// The startup budget (tools/bundleBudget.mjs, docs/dev/frontend-refactor.md
// "Lazy boundaries"): its readers over a small build written to a temp
// directory, each rule failing once, and the real source's lazy boundaries.
// The check of the real build is `npm run budget`, after `npm run build`.
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";
import {
  CEILINGS_FILE, FIGURES, FLOOR, MIN_BYTES, SRC, check, chunkName, dynamicImports, graph, measure,
  readCeilings, resetCeilings, startupRoots, staticImports, stripComments,
} from "../tools/bundleBudget.mjs";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TOOL = path.join(FRONTEND, "tools", "bundleBudget.mjs");

// Minified code padded past MIN_BYTES, so precompress would copy it.
const pad = (code, bytes = 2 * MIN_BYTES) => `${code}\n/*${"x".repeat(bytes)}*/`;
const FILES = {
  "index.html": pad(`<!doctype html><html><head>
<!-- <script type="module" src="/assets/commented-AAAAAAAA.js"></script> -->
<script crossorigin src="/assets/index-AAAAAAAA.js" type="module"></script>
<link rel="stylesheet" crossorigin href="/assets/index-AAAAAAAA.css">
<link href="/assets/App-BBBBBBBB.js" crossorigin rel="modulepreload">
<link crossorigin href="/assets/App-BBBBBBBB.css" rel="stylesheet">
<link rel="stylesheet" href="https://fonts.example/a.css">
</head></html>`),
  "assets/index-AAAAAAAA.js": pad(`const a=1;export{a as r};__vitePreload(()=>import("./App-BBBBBBBB.js"),__vite__mapDeps([0]));const m=["assets/App-BBBBBBBB.js"];`),
  "assets/App-BBBBBBBB.js": pad(`import{r as x}from"./index-AAAAAAAA.js";import"./shared-CCCC-C_C.js";const P=()=>import("./Panel-DDDDDDDD.js");`),
  "assets/shared-CCCC-C_C.js": pad(`export*from"./deep-EEEEEEEE.js";`),
  "assets/deep-EEEEEEEE.js": pad(`export const d=1;`),
  "assets/Panel-DDDDDDDD.js": pad(`import"./shared-CCCC-C_C.js";import{q}from"./panelOnly-FFFFFFFF.js";`),
  "assets/panelOnly-FFFFFFFF.js": pad(`export const q=1;`),
  "assets/pdf.worker.min-GGGGGGGG.mjs": pad(`self.onmessage=()=>{};`),
  "assets/zh-HHHHHHHH.js": pad(`export default{a:"b"};`),
  "assets/index-AAAAAAAA.css": pad(`a{color:red}`),
  "assets/App-BBBBBBBB.css": pad(`b{color:blue}`),
  "assets/tiny-IIIIIIII.js": `export const t=1;`,
};
const SOURCE = {
  "main.jsx": `import "./shared/styles/app.css";\nPromise.resolve().then(() => import("./app/App.jsx"));`,
  "app/App.jsx": [
    `import { lazySurface } from "../shared/ui/lazy";`,
    `// const Ghost = lazySurface(() => import("../ghost/Ghost"));`,
    `/* import("../ghost/Block") */`,
    `const glob = "./media/*.svg"; const next = "*/";`,
    `const Panel = lazySurface(() => import("../panel/Panel"));`,
  ].join("\n"),
  "panel/Panel.jsx": `export default function Panel() { return null; }`,
  "shared/i18n/locales/zh.json": `{"a": "b"}`,
};

function write(dir, files) {
  for (const [rel, body] of Object.entries(files)) {
    if (body === null) continue;
    const file = path.join(dir, rel);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, body);
  }
}

// A build and its source: `dist` and `src` replace files (null drops one),
// `after` changes the written build (to drop a copy).
function fixture({ dist = {}, src = {}, after } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-budget-"));
  const files = { ...FILES, ...dist };
  write(path.join(root, "dist"), files);
  for (const [rel, body] of Object.entries(files)) {
    if (body === null || Buffer.byteLength(body) < MIN_BYTES) continue;
    const file = path.join(root, "dist", rel);
    fs.writeFileSync(file + ".br", zlib.brotliCompressSync(body));
    fs.writeFileSync(file + ".gz", zlib.gzipSync(body));
  }
  write(path.join(root, "src"), { ...SOURCE, ...src });
  after?.(path.join(root, "dist"));
  return { dist: path.join(root, "dist"), src: path.join(root, "src"), done: () => fs.rmSync(root, { recursive: true, force: true }) };
}

// Ceilings at the fixture's own figures (a reset's rounding to 1,000 bytes
// would put a tiny build under the floor).
const fitted = (r) => Object.fromEntries(Object.entries(FIGURES)
  .map(([key, figure]) => [key, { max: figure.of(r), set: "2026-11-06", measured: figure.of(r), why: "the fixture's size" }]));

function run(options, ceilings) {
  const f = fixture(options);
  try {
    const r = measure(f.dist, f.src);
    return { r, fail: check(r, ceilings ?? fitted(r)) };
  } finally { f.done(); }
}

test("static imports are read from minified chunks, dynamic ones are not", () => {
  const code = [
    `import{r as x,h as L}from"./index-Ut0nNz_e.js";`,
    `import"./side-AAAAAAAA.js";`,
    `import*as P from"./ns-AAAAAAAA.js";`,
    `import d,{e}from"./mix-AAAAAAAA.js";`,
    `export{a as b}from"./re-AAAAAAAA.js";`,
    `export*from"./star-AAAAAAAA.js";`,
    `import{$e as y}from'./single-AAAAAAAA.mjs';`,
    `const C=__vitePreload(()=>import("./ChatDock-ffXbWvWk.js"),__vite__mapDeps([7]));`,
    `x.import("./no-AAAAAAAA.js");`,
    `const m=["assets/App-CzL1KSOr.js","./also-AAAAAAAA.js"];`,
    `import("./dyn-AAAAAAAA.js");`,
  ].join("");
  assert.deepEqual(staticImports(code), [
    "./index-Ut0nNz_e.js", "./side-AAAAAAAA.js", "./ns-AAAAAAAA.js", "./mix-AAAAAAAA.js",
    "./re-AAAAAAAA.js", "./star-AAAAAAAA.js", "./single-AAAAAAAA.mjs",
  ]);
  assert.deepEqual(staticImports(`import"./a-AAAAAAAA.js";import"./a-AAAAAAAA.js";`), ["./a-AAAAAAAA.js"], "once each");
});

test("a chunk's name is its file name less Vite's 8-character hash", () => {
  assert.equal(chunkName("assets/RecentlyDeleted-Bd2a-Y6t.js"), "RecentlyDeleted");
  assert.equal(chunkName("assets/SharePopover-3wZMH-ee.js"), "SharePopover");
  assert.equal(chunkName("assets/CommentMargin-3LpIgF-S.js"), "CommentMargin");
  assert.equal(chunkName("assets/index-B9fWrEk-.js"), "index");
  assert.equal(chunkName("assets/abnfDiagram-O67JEVCF-CM1qZszc.js"), "abnfDiagram-O67JEVCF");
  assert.equal(chunkName("assets/mermaid.core-BmWviPqf.js"), "mermaid.core");
  assert.equal(chunkName("assets/pdf.worker.min-BVPM1tzH.mjs"), "pdf.worker.min");
});

test("index.html's roots: the module entry, its modulepreloads and the build's stylesheets, in any attribute order", () => {
  assert.deepEqual(startupRoots(FILES["index.html"]), {
    entry: ["assets/index-AAAAAAAA.js"],
    preload: ["assets/App-BBBBBBBB.js"],
    css: ["assets/index-AAAAAAAA.css", "assets/App-BBBBBBBB.css"],
  });
  assert.deepEqual(startupRoots(`<script>var a = "<script type=module>";</script><script type='module' src='./assets/x-AAAAAAAA.js'></script>`).entry,
    ["assets/x-AAAAAAAA.js"]);
});

test("the source scan leaves out the comments that start a line", () => {
  const code = [
    `// import("./a")`,
    `  /* import("./b")`,
    `     import("./c") */ x();`,
    `const glob = "./media/*.svg"; import("./d"); // import("./e")`,
    "const re = /[`'\"]/; import(\"./f\");",
  ].join("\n");
  assert.deepEqual([...stripComments(code).matchAll(/import\("([^"]+)"\)/g)].map((m) => m[1]), ["./d", "./e", "./f"],
    "a comment after code counts, so one commented out there fails loudly instead of a real one going missing");
});

test("the startup graph follows static imports from the entry and the preloaded App, not import()", () => {
  const f = fixture();
  try {
    assert.deepEqual([...graph(f.dist, ["assets/index-AAAAAAAA.js", "assets/App-BBBBBBBB.js"])].sort(), [
      "assets/App-BBBBBBBB.js", "assets/deep-EEEEEEEE.js", "assets/index-AAAAAAAA.js", "assets/shared-CCCC-C_C.js",
    ]);
    const r = measure(f.dist, f.src);
    assert.deepEqual(r.startupJs.map((s) => s.file).sort(), [
      "assets/App-BBBBBBBB.js", "assets/deep-EEEEEEEE.js", "assets/index-AAAAAAAA.js", "assets/shared-CCCC-C_C.js",
    ]);
    const size = (rel) => fs.statSync(path.join(f.dist, rel)).size;
    assert.equal(r.css.raw, size("assets/index-AAAAAAAA.css") + size("assets/App-BBBBBBBB.css"));
    assert.equal(r.css.br, size("assets/index-AAAAAAAA.css.br") + size("assets/App-BBBBBBBB.css.br"), "Brotli is the .br copy's size");
    assert.deepEqual(r.lazy.map((l) => [l.name, l.from, l.chunks]), [
      ["Panel", "app/App.jsx", ["assets/Panel-DDDDDDDD.js"]],
      ["App", "main.jsx", ["assets/App-BBBBBBBB.js"]],
    ], "the commented-out imports are not boundaries");
    const panel = r.lazy[0];
    assert.equal(panel.beyond.raw, size("assets/Panel-DDDDDDDD.js") + size("assets/panelOnly-FFFFFFFF.js"), "a lazy chunk's bytes beyond startup");
    assert.deepEqual(r.catalogs.map((c) => [c.name, c.chunks]), [["zh", ["assets/zh-HHHHHHHH.js"]]]);
    assert.equal(r.total.files, 9);
  } finally { f.done(); }
});

test("the fixture build passes", () => {
  assert.deepEqual(run().fail, []);
});

test("a lazy module without a chunk of its own, or reached statically, fails", () => {
  const missing = run({ src: { "app/App.jsx": `${SOURCE["app/App.jsx"]}\nconst Gone = lazySurface(() => import("../gone/Gone"));` } }).fail;
  assert.deepEqual(missing, [`Gone (import("../gone/Gone") in app/App.jsx) has no chunk of its own: a static import puts it in the startup graph`]);
  const statically = run({ dist: { "assets/App-BBBBBBBB.js": pad(`${FILES["assets/App-BBBBBBBB.js"]}import"./Panel-DDDDDDDD.js";`) } }).fail;
  assert.equal(statically.length, 1);
  assert.match(statically[0], /^Panel \(import\("\.\.\/panel\/Panel"\) in app\/App\.jsx\) is in the startup graph/);
});

test("App must be modulepreloaded", () => {
  const html = FILES["index.html"].replace(/<link href="\/assets\/App-BBBBBBBB\.js"[^>]*>/, "");
  assert.deepEqual(run({ dist: { "index.html": html } }).fail,
    [`App (import("./app/App.jsx") in main.jsx) is not modulepreloaded from index.html: vite.config.js preloadApp did not find its chunk`]);
});

test("index.html has exactly one module entry", () => {
  const two = FILES["index.html"].replace("</head>", `<script type="module" src="/assets/deep-EEEEEEEE.js"></script></head>`);
  assert.ok(run({ dist: { "index.html": two } }).fail.some((l) => l.startsWith("index.html has 2 module entry scripts")));
});

test("the build has exactly one pdf.js worker", () => {
  assert.deepEqual(run({ dist: { "assets/pdf.worker.min-GGGGGGGG.mjs": null } }).fail, ["the build has 0 pdf.js worker files, not one"]);
  const two = run({ dist: { "assets/pdf.worker-JJJJJJJJ.mjs": FILES["assets/pdf.worker.min-GGGGGGGG.mjs"] } }).fail;
  assert.deepEqual(two, ["the build has 2 pdf.js worker files, not one: assets/pdf.worker-JJJJJJJJ.mjs, assets/pdf.worker.min-GGGGGGGG.mjs"]);
});

test("a measured file of 1 KB or more needs its .br and .gz", () => {
  const { fail } = run({ after: (dist) => fs.rmSync(path.join(dist, "assets/App-BBBBBBBB.js.gz")) });
  assert.deepEqual(fail, ["assets/App-BBBBBBBB.js has no .br or no .gz copy: vite.config.js precompress did not run on it"]);
  // a chunk under MIN_BYTES gets no copies, and needs none
  const small = run({ dist: { "assets/deep-EEEEEEEE.js": `export const d=1;` } });
  assert.ok(small.r.startupJs.some((s) => s.file === "assets/deep-EEEEEEEE.js"));
  assert.deepEqual(small.fail, []);
});

test("a figure fails over its ceiling, with its reason, and more than 20% under it", () => {
  const f = fixture();
  try {
    const r = measure(f.dist, f.src);
    const at = (key, max) => ({ ...fitted(r), [key]: { max, set: "2026-11-06", measured: max, why: "Why it is there" } });
    assert.deepEqual(check(r, at("startupJsBrotli", r.js.br)), [], "at the ceiling passes");
    const over = check(r, at("startupJsBrotli", r.js.br - 1));
    assert.equal(over.length, 1);
    assert.match(over[0], /^startup JS, Brotli is [\d,]+ bytes, over its ceiling of [\d,]+\. It guards what every first screen downloads before App renders; set 2026-11-06 at [\d,]+: Why it is there\. Take something/);
    assert.match(over[0], /--reset "<what grew>" startupJsBrotli$/);
    assert.deepEqual(check(r, at("startupCssBrotli", Math.floor(r.css.br / FLOOR))), [], "at the floor passes");
    const under = check(r, at("startupCssBrotli", Math.floor(r.css.br / FLOOR) + 1));
    assert.equal(under.length, 1);
    assert.match(under[0], /^startup CSS, Brotli is [\d,]+ bytes, more than 20% under its ceiling of [\d,]+: lower it so the saving is kept/);
    const { startupJsRaw, ...rest } = fitted(r);
    assert.deepEqual(check(r, rest), [`startup JS, raw has no ceiling in tests/bundle-budget.json: npm run budget -- --reset "<reason>" startupJsRaw`]);
  } finally { f.done(); }
});

test("--reset sets a ceiling 10% over the build, rounded up to 1,000 bytes, with its reason", () => {
  const r = { js: { raw: 2_717_864, br: 731_215 }, css: { raw: 423_702, br: 72_111 } };
  const old = { startupJsBrotli: { max: 1, set: "2026-01-01", measured: 1, why: "old" }, startupJsRaw: { max: 2, set: "2026-01-01", measured: 2, why: "old" } };
  assert.deepEqual(resetCeilings(r, old, " the ink tools entered App ", undefined, "2026-11-06"), {
    startupJsBrotli: { max: 805_000, set: "2026-11-06", measured: 731_215, why: "the ink tools entered App" },
    startupJsRaw: { max: 2_990_000, set: "2026-11-06", measured: 2_717_864, why: "the ink tools entered App" },
    startupCssBrotli: { max: 80_000, set: "2026-11-06", measured: 72_111, why: "the ink tools entered App" },
  });
  assert.deepEqual(resetCeilings(r, old, "grew", ["startupJsRaw"], "2026-11-06"), {
    startupJsBrotli: old.startupJsBrotli,
    startupJsRaw: { max: 2_990_000, set: "2026-11-06", measured: 2_717_864, why: "grew" },
  }, "only the figures named");
  assert.throws(() => resetCeilings(r, old, "  "), /needs its reason/);
  assert.throws(() => resetCeilings(r, old, "x", ["startupJs"]), /no figure startupJs/);
});

test("the command reports a build, fails without one and resets no ceiling on a broken one", () => {
  const none = spawnSync(process.execPath, [TOOL, "--dist", path.join(os.tmpdir(), "gamma-budget-none")], { encoding: "utf8" });
  assert.equal(none.status, 1);
  assert.match(none.stderr, /^no build at .*: run npm run build first/);
  const f = fixture();
  try {
    // the repository's ceilings are far above the fixture: the floor fails
    const out = spawnSync(process.execPath, [TOOL, "--dist", f.dist], { encoding: "utf8" });
    assert.equal(out.status, 1, out.stderr);
    assert.match(out.stdout, /^ {2}startup JS {2,}[\d,]+ +[\d,]+ {2}raw ceiling/m);
    assert.match(out.stdout, /^FAIL startup JS, Brotli is [\d,]+ bytes, more than 20% under its ceiling/m);
    const bad = spawnSync(process.execPath, [TOOL, "--bogus"], { encoding: "utf8" });
    assert.equal(bad.status, 2);
    // the real source's boundaries have no chunks in the fixture: no reset on a broken build
    const before = fs.readFileSync(CEILINGS_FILE, "utf8");
    const reset = spawnSync(process.execPath, [TOOL, "--dist", f.dist, "--reset", "never written"], { encoding: "utf8" });
    const after = fs.readFileSync(CEILINGS_FILE, "utf8");
    if (after !== before) fs.writeFileSync(CEILINGS_FILE, before);
    assert.equal(after, before, "tests/bundle-budget.json is as it was");
    assert.equal(reset.status, 1);
    assert.match(reset.stdout, /^FAIL ChatDock .* has no chunk of its own/m);
    assert.match(reset.stdout, /^the ceilings were not reset/m);
  } finally { f.done(); }
});

// The real source, which `npm run budget` scans for the boundaries it checks.
test("the source's lazy boundaries: App, every lazySurface, pdf.js and mermaid", () => {
  const found = dynamicImports(SRC);
  const names = new Set(found.map((d) => d.name));
  for (const name of ["App", "ChatDock", "SettingsDialog", "CommentMargin", "pdf", "mermaid.core"]) {
    assert.ok(names.has(name), `${name} is a lazy boundary`);
  }
  assert.equal(found.find((d) => d.name === "App").from, "main.jsx");
  assert.equal(found.find((d) => d.name === "mermaid.core").spec, "mermaid", "a package by the file it resolves to");
  // every lazySurface call has a literal import, so none slips past the scan
  let calls = 0, literal = 0;
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.(jsx?|mjs)$/.test(e.name) && path.relative(SRC, p) !== path.join("shared", "ui", "lazy.jsx")) {
        const code = stripComments(fs.readFileSync(p, "utf8"));
        calls += (code.match(/\blazySurface\(/g) || []).length;
        literal += (code.match(/\blazySurface\(\s*\(\)\s*=>\s*import\(\s*["'][^"']+["']\s*\)/g) || []).length;
      }
    }
  };
  walk(SRC);
  assert.ok(calls >= 15, `${calls} lazySurface calls`);
  assert.equal(literal, calls, "every lazySurface(() => import(\"…\")) names its module literally");
});

test("MIN_BYTES is vite.config.js's precompress threshold", () => {
  const config = fs.readFileSync(path.join(FRONTEND, "vite.config.js"), "utf8");
  assert.equal(Number(config.match(/const MIN_BYTES = (\d+);/)?.[1]), MIN_BYTES);
  assert.match(config, /preloadApp\(\)/, "the App chunk is preloaded by the build");
});

test("tests/bundle-budget.json holds a ceiling for every figure, each with its reason", () => {
  const ceilings = readCeilings(CEILINGS_FILE);
  assert.deepEqual(Object.keys(ceilings), Object.keys(FIGURES));
  for (const [key, c] of Object.entries(ceilings)) {
    assert.deepEqual(Object.keys(c), ["max", "set", "measured", "why"], key);
    assert.ok(Number.isInteger(c.max) && c.max > 0, `${key}: a positive whole max`);
    assert.ok(Number.isInteger(c.measured) && c.measured > 0 && c.measured <= c.max, `${key}: set on a build under it`);
    assert.match(c.set, /^\d{4}-\d{2}-\d{2}$/, `${key}: the date it was set`);
    assert.ok(c.why.trim().length > 10, `${key}: why it is where it is`);
  }
});
