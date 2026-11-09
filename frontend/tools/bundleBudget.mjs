// The startup budget (docs/dev/frontend-refactor.md, "Lazy boundaries"):
// reads a build and checks what every first screen downloads before App
// renders, and that each lazy surface still comes in a chunk of its own.
//
//   node tools/bundleBudget.mjs                       # report and check frontend/dist (npm run budget)
//   node tools/bundleBudget.mjs --dist <dir>          # a build elsewhere (the merge skill's scratch build)
//   node tools/bundleBudget.mjs --reset "<reason>"    # every ceiling 10% over this build, with the reason
//   node tools/bundleBudget.mjs --reset "<reason>" startupJsBrotli   # that ceiling alone
//
// The startup graph is index.html's module entry and its modulepreloads (the
// App chunk vite.config.js's preloadApp adds), followed through their static
// imports; a dynamic import() is never followed. Startup CSS is the head's
// stylesheets. A file's Brotli bytes are its .br copy's size, what the
// backend sends (gamma/app.py), or the raw size when there is none.
//
// What fails (exit 1, one line per broken rule):
//   1. index.html has other than one module entry.
//   2. A module src/ imports with a literal import("…") has no chunk of its
//      own, or its chunk is in the startup graph. App (main.jsx) is the
//      exception: it must be modulepreloaded instead.
//   3. The build has other than one pdf.js worker.
//   4. A measured file of 1 KB or more has no .br or no .gz copy (precompress).
//   5. A figure is over its ceiling in tests/bundle-budget.json, or more than
//      20% under it: a split's saving is kept by lowering the ceiling.
// The lazy chunks, the worker, the locale catalogs and the build's total JS
// are reported, not gated. --reset refuses a build that breaks rules 1-4.
//
// Chunks are found by name, so rule 2 relies on Vite's default file names,
// [name]-[hash].js with an 8-character hash: a chunkFileNames or
// manualChunks setting makes it fail rather than pass.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const SRC = path.join(FRONTEND, "src");
export const DIST = path.join(FRONTEND, "dist");
export const CEILINGS_FILE = path.join(FRONTEND, "tests", "bundle-budget.json");
const CATALOGS = path.join("shared", "i18n", "locales");

// vite.config.js precompress: a smaller file gets no .br or .gz copy.
export const MIN_BYTES = 1024;
// The dynamic imports the page's head preloads (vite.config.js preloadApp).
export const PRELOADED = new Set(["App"]);
// --reset puts a ceiling this far over the build, rounded up to 1,000 bytes;
// a figure under FLOOR of its ceiling fails.
export const HEADROOM = 0.1;
export const FLOOR = 0.8;

// The gated figures, what each one reads from the report and what its
// ceiling guards; tests/bundle-budget.json holds a ceiling for each.
export const FIGURES = {
  startupJsBrotli: {
    label: "startup JS, Brotli",
    of: (r) => r.js.br,
    guards: "what every first screen downloads before App renders",
  },
  startupJsRaw: {
    label: "startup JS, raw",
    of: (r) => r.js.raw,
    guards: "what the iPad and Android apps parse at startup, from a server on the device where the download is free and parsing is not",
  },
  startupCssBrotli: {
    label: "startup CSS, Brotli",
    of: (r) => r.css.br,
    guards: "the head's render-blocking stylesheets",
  },
};

// ---- reading the build ----------------------------------------------------

// The chunks a minified chunk imports statically: `import … from "./x.js"`,
// `import "./x.js"` and `export … from "./x.js"`, never `import("./x.js")`.
const STATIC = /(?<![\w$.])(?:import|export)\s*(?:[\w$*{}\s,]*?\s*from\s*)?["'](\.\/[^"']+\.m?js)["']/g;
export const staticImports = (code) => [...new Set([...code.matchAll(STATIC)].map((m) => m[1]))];

// A chunk's [name] without its hash (which may hold "-" and "_").
export const chunkName = (file) => path.posix.basename(file).replace(/-[\w-]{8}\.m?js$/, "");

const attributes = (tag) => Object.fromEntries([...tag.matchAll(/\s([\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g)]
  .map((m) => [m[1].toLowerCase(), m[2] ?? m[3] ?? m[4] ?? true]));
// "/assets/x.js" → "assets/x.js"; another host's file is not the build's.
const local = (href) => (typeof href === "string" && !/^(?:[a-z]+:)?\/\//i.test(href) ? href.replace(/^\.?\//, "") : null);

// The page head's module entry scripts, modulepreloads and the build's own stylesheets.
export function startupRoots(html) {
  const roots = { entry: [], preload: [], css: [] };
  for (const [tag, name] of html.replace(/<!--[\s\S]*?-->/g, "").matchAll(/<(script|link)\b[^>]*>/gi)) {
    const a = attributes(tag);
    if (name.toLowerCase() === "script") {
      if (a.type === "module" && local(a.src)) roots.entry.push(local(a.src));
    } else if (a.rel === "modulepreload" && local(a.href)) roots.preload.push(local(a.href));
    else if (a.rel === "stylesheet" && /^assets\//.test(local(a.href) || "")) roots.css.push(local(a.href));
  }
  return roots;
}

// Every file the roots reach through static imports, the roots included.
export function graph(dist, roots) {
  const seen = new Set();
  const queue = [...roots];
  while (queue.length) {
    const file = queue.shift();
    if (seen.has(file)) continue;
    seen.add(file);
    for (const spec of staticImports(fs.readFileSync(path.join(dist, file), "utf8"))) {
      queue.push(path.posix.join(path.posix.dirname(file), spec));
    }
  }
  return seen;
}

// ---- reading the source ---------------------------------------------------

function sourceFiles(dir) {
  const out = [];
  (function walk(d) {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.(jsx?|mjs)$/.test(e.name)) out.push(p);
    }
  })(dir);
  return out.sort();
}

// Drops the comments that start a line. A comment after code is read as
// code: an import() commented out there fails as a boundary with no chunk,
// which is loud, where telling comments from strings and regexes could
// silently miss a real one.
export const stripComments = (code) => code.replace(/^[ \t]*\/\*[\s\S]*?\*\//gm, "").replace(/^[ \t]*\/\/.*$/gm, "");

// The chunk [name] a module gets: its file name, a package's by the file it
// resolves to (mermaid → mermaid.core.mjs).
function moduleName(spec) {
  let file = spec;
  if (!spec.startsWith(".")) {
    try { file = fileURLToPath(import.meta.resolve(spec)); } catch { /* not installed: its own name */ }
  }
  return path.basename(file).replace(/\.(jsx?|mjs)$/, "");
}

// Every literal import("…") in the source: the lazy boundaries.
export function dynamicImports(src = SRC) {
  const found = [];
  for (const file of sourceFiles(src)) {
    const code = stripComments(fs.readFileSync(file, "utf8"));
    for (const m of code.matchAll(/(?<![\w$.])import\(\s*(["'])([^"'`]+)\1\s*\)/g)) {
      found.push({ name: moduleName(m[2]), spec: m[2], from: path.relative(src, file).split(path.sep).join("/") });
    }
  }
  return found;
}

const catalogNames = (src) => {
  try {
    return fs.readdirSync(path.join(src, CATALOGS)).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5)).sort();
  } catch { return []; }
};

// ---- the report -------------------------------------------------------------

const sizes = (dist, file) => {
  const raw = fs.statSync(path.join(dist, file)).size;
  const br = path.join(dist, file + ".br");
  return { file, raw, br: fs.existsSync(br) ? fs.statSync(br).size : raw };
};
const sum = (rows) => rows.reduce((a, s) => ({ raw: a.raw + s.raw, br: a.br + s.br }), { raw: 0, br: 0 });

export function measure(dist = DIST, src = SRC) {
  const roots = startupRoots(fs.readFileSync(path.join(dist, "index.html"), "utf8"));
  const startup = graph(dist, [...roots.entry, ...roots.preload]);
  const js = fs.readdirSync(path.join(dist, "assets")).filter((f) => /\.m?js$/.test(f)).sort().map((f) => `assets/${f}`);
  const named = (name) => js.filter((f) => chunkName(f) === name);
  const lazy = [];
  for (const d of dynamicImports(src)) {
    if (lazy.some((l) => l.name === d.name)) continue;
    const chunks = named(d.name);
    lazy.push({
      ...d,
      chunks,
      preloaded: chunks.some((c) => roots.preload.includes(c)),
      inStartup: chunks.some((c) => startup.has(c)),
      beyond: sum([...graph(dist, chunks)].filter((f) => !startup.has(f)).map((f) => sizes(dist, f))),
    });
  }
  const catalogs = catalogNames(src).map((name) => ({ name, chunks: named(name), ...sum(named(name).map((f) => sizes(dist, f))) }));
  const workers = js.filter((f) => /^pdf\.worker\b/.test(path.posix.basename(f)));
  const startupJs = [...startup].map((f) => sizes(dist, f));
  const startupCss = roots.css.map((f) => sizes(dist, f));
  const measured = new Set(["index.html", ...startup, ...roots.css, ...workers,
    ...lazy.flatMap((l) => l.chunks), ...catalogs.flatMap((c) => c.chunks)]);
  const has = (f) => fs.existsSync(path.join(dist, f));
  return {
    dist,
    built: fs.statSync(path.join(dist, "index.html")).mtime,
    html: sizes(dist, "index.html"),
    entry: roots.entry,
    preload: roots.preload,
    startupJs,
    startupCss,
    js: sum(startupJs),
    css: sum(startupCss),
    lazy,
    workers: workers.map((f) => sizes(dist, f)),
    catalogs,
    total: { files: js.length, ...sum(js.map((f) => sizes(dist, f))) },
    missingCopies: [...measured].filter((f) => fs.statSync(path.join(dist, f)).size >= MIN_BYTES
      && !(has(f + ".br") && has(f + ".gz"))),
  };
}

// ---- the rules --------------------------------------------------------------

const bytes = (n) => n.toLocaleString("en-US");
const RESET = "npm run budget -- --reset";

// Rules 1-4, which no ceiling moves.
export function structure(r) {
  const fail = [];
  if (r.entry.length !== 1) fail.push(`index.html has ${r.entry.length} module entry scripts, not one${r.entry.length ? `: ${r.entry.join(", ")}` : ""}`);
  for (const l of r.lazy) {
    const where = `import("${l.spec}") in ${l.from}`;
    if (!l.chunks.length) fail.push(`${l.name} (${where}) has no chunk of its own: a static import puts it in the startup graph`);
    else if (PRELOADED.has(l.name)) {
      if (!l.preloaded) fail.push(`${l.name} (${where}) is not modulepreloaded from index.html: vite.config.js preloadApp did not find its chunk`);
    } else if (l.inStartup) fail.push(`${l.name} (${where}) is in the startup graph: ${l.chunks.join(", ")} is reached by a static import`);
  }
  if (r.workers.length !== 1) fail.push(`the build has ${r.workers.length} pdf.js worker files, not one${r.workers.length ? `: ${r.workers.map((w) => w.file).join(", ")}` : ""}`);
  for (const f of r.missingCopies) fail.push(`${f} has no .br or no .gz copy: vite.config.js precompress did not run on it`);
  return fail;
}

export function check(r, ceilings = readCeilings()) {
  const fail = structure(r);
  for (const [key, figure] of Object.entries(FIGURES)) {
    const now = figure.of(r);
    const c = ceilings[key];
    if (!c) {
      fail.push(`${figure.label} has no ceiling in tests/bundle-budget.json: ${RESET} "<reason>" ${key}`);
    } else if (now > c.max) {
      fail.push(`${figure.label} is ${bytes(now)} bytes, over its ceiling of ${bytes(c.max)}. It guards ${figure.guards}; `
        + `set ${c.set} at ${bytes(c.measured)}: ${c.why.replace(/[.\s]*$/, ".")} Take something off the first screen, or raise it in this change: ${RESET} "<what grew>" ${key}`);
    } else if (now < FLOOR * c.max) {
      fail.push(`${figure.label} is ${bytes(now)} bytes, more than ${Math.round((1 - FLOOR) * 100)}% under its ceiling of ${bytes(c.max)}: `
        + `lower it so the saving is kept: ${RESET} "<what was cut>" ${key}`);
    }
  }
  return fail;
}

// ---- the ceilings -----------------------------------------------------------

export function readCeilings(file = CEILINGS_FILE) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch (e) {
    if (e.code === "ENOENT") return {};
    throw e;
  }
}

// The ceilings with `keys` (every figure by default) set HEADROOM over the
// report, rounded up to 1,000 bytes, each recording its date, the bytes it
// was set on and the reason.
export function resetCeilings(r, ceilings, reason, keys = Object.keys(FIGURES), date = new Date().toISOString().slice(0, 10)) {
  if (!reason || !reason.trim()) throw new Error(`a ceiling needs its reason: ${RESET} "<reason>"`);
  const unknown = keys.filter((k) => !FIGURES[k]);
  if (unknown.length) throw new Error(`no figure ${unknown.join(", ")}: the figures are ${Object.keys(FIGURES).join(", ")}`);
  const out = {};
  for (const key of Object.keys(FIGURES)) {
    if (!keys.includes(key)) { if (ceilings[key]) out[key] = ceilings[key]; continue; }
    const measured = FIGURES[key].of(r);
    out[key] = { max: Math.ceil((measured * (1 + HEADROOM)) / 1000) * 1000, set: date, measured, why: reason.trim() };
  }
  return out;
}

// ---- the command --------------------------------------------------------------

function print(r, ceilings) {
  const line = (label, s, note = "") => console.log(`  ${label.padEnd(44)} ${bytes(s.raw).padStart(10)} ${bytes(s.br).padStart(10)}${note ? `  ${note}` : ""}`);
  const ceiling = (key) => (ceilings[key] ? `ceiling ${bytes(ceilings[key].max)}` : "no ceiling");
  console.log(`Startup budget of ${path.relative(process.cwd(), r.dist) || "."} (index.html written ${r.built.toISOString().slice(0, 16).replace("T", " ")} UTC)`);
  console.log(`  ${"".padEnd(44)} ${"raw".padStart(10)} ${"Brotli".padStart(10)}`);
  line("index.html", r.html);
  for (const f of r.startupJs) line(f.file, f);
  line("startup JS", r.js, `raw ${ceiling("startupJsRaw")}, Brotli ${ceiling("startupJsBrotli")}`);
  for (const f of r.startupCss) line(f.file, f);
  line("startup CSS", r.css, `Brotli ${ceiling("startupCssBrotli")}`);
  console.log("Lazy chunks, the bytes each adds beyond startup:");
  for (const l of r.lazy) line(`${l.name} (${l.from})`, l.beyond, PRELOADED.has(l.name) ? "preloaded with startup" : l.chunks.length ? "" : "no chunk");
  console.log("The pdf.js worker:");
  for (const w of r.workers) line(w.file, w);
  console.log("Locale catalogs, before App on that language:");
  for (const c of r.catalogs) line(c.chunks.join(", ") || `${c.name} (no chunk)`, c);
  line(`all JS, ${r.total.files} files`, r.total);
}

function main(args) {
  let dist = DIST;
  let reason = null;
  const keys = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--dist" && args[i + 1]) dist = path.resolve(args[++i]);
    else if (args[i] === "--reset" && args[i + 1] !== undefined) reason = args[++i];
    else if (reason !== null && !args[i].startsWith("--")) keys.push(args[i]);
    else {
      console.error(`unknown argument ${args[i]}: node tools/bundleBudget.mjs [--dist <dir>] [--reset "<reason>" [figure …]]`);
      return 2;
    }
  }
  if (!fs.existsSync(path.join(dist, "index.html"))) {
    console.error(`no build at ${dist}: run npm run build first`);
    return 1;
  }
  const r = measure(dist);
  let ceilings = readCeilings();
  if (reason !== null) {
    // a ceiling is set on a build whose boundaries hold
    const broken = structure(r);
    if (broken.length) {
      print(r, ceilings);
      for (const f of broken) console.log(`FAIL ${f}`);
      console.log(`the ceilings were not reset: ${path.relative(FRONTEND, CEILINGS_FILE)} is as it was`);
      return 1;
    }
    try { ceilings = resetCeilings(r, ceilings, reason, keys.length ? keys : undefined); } catch (e) {
      console.error(e.message);
      return 2;
    }
    fs.writeFileSync(CEILINGS_FILE, JSON.stringify(ceilings, null, 2) + "\n");
  }
  print(r, ceilings);
  if (reason !== null) {
    for (const key of keys.length ? keys : Object.keys(FIGURES)) {
      console.log(`wrote ${path.relative(FRONTEND, CEILINGS_FILE)}: ${key} ${bytes(ceilings[key].max)} (${bytes(ceilings[key].measured)} + ${HEADROOM * 100}%)`);
    }
  }
  const fail = check(r, ceilings);
  for (const f of fail) console.log(`FAIL ${f}`);
  if (!fail.length) console.log("ok");
  return fail.length ? 1 : 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2));
}
