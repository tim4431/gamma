// The PDF translation bench: what the translated view sends to the model, and
// (with --live) what comes back, for fixed papers, so a change to the
// segmenter, the planner or the prompt can be measured before and after.
// docs/dev/translation.md "The bench" describes the numbers.
//
//   node tests/bench/translateBench.mjs [paper.pdf ...] [--label NAME] [--out DIR] [--pages 1-3]
//   node tests/bench/translateBench.mjs --live [--judge] [--lang zh-CN] [--parallel 3] ...
//   node tests/bench/translateBench.mjs --compare before.json after.json
//   node tests/bench/translateBench.mjs --record tests/fixtures/translatePaper.json
//
// With no PDF it compiles the fixture paper (tests/fixtures/translate/paper.tex,
// needs pdflatex on PATH). Pages are segmented and planned with the viewer's
// own modules (pdf/pdfTranslate.js, pdf/translatePlan.js, pdf/pdfFonts.js)
// under pdf.js in Node. Writes <out>/<label>.json and <out>/<label>.html; the
// default out directory is the OS temp dir.
//
// --live translates through an isolated Gamma server (the e2e harness: a free
// port, a throwaway data dir) with a provider from the environment:
//   GAMMA_BENCH_PROTOCOL  anthropic | openai | …   (an AI provider protocol)
//   GAMMA_BENCH_API_KEY   the key
//   GAMMA_BENCH_MODEL     the model id
//   GAMMA_BENCH_BASE_URL  optional, for a compatible server
//   GAMMA_BENCH_EFFORT    optional reasoning effort ("" = the server's default)
// --judge (with --live) asks the same model, directly, which renderings each
// frequent source term got across the translations: one call per run.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { segmentPage, textRuns } from "../../src/pdf/pdfTranslate.js";
import { BREAK, ENDS_SENTENCE, TOKEN, chunkUnits, createPlanner, requestFields } from "../../src/pdf/translatePlan.js";
import { pageFaces } from "../../src/pdf/pdfFonts.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "../..");
const FIXTURE_TEX = path.join(FRONTEND, "tests/fixtures/translate/paper.tex");

// --- arguments ----------------------------------------------------------------

function parseArgs(argv) {
  const opts = { pdfs: [], label: "", out: path.join(os.tmpdir(), "gamma-translate-bench"), pages: null,
                 live: false, judge: false, lang: "zh-CN", parallel: 3, compare: null, record: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === "--label") opts.label = next();
    else if (a === "--out") opts.out = path.resolve(next());
    else if (a === "--pages") opts.pages = next();
    else if (a === "--live") opts.live = true;
    else if (a === "--judge") opts.judge = true;
    else if (a === "--lang") opts.lang = next();
    else if (a === "--parallel") opts.parallel = Math.max(1, Number(next()) || 3);
    else if (a === "--compare") opts.compare = [next(), next()];
    else if (a === "--record") opts.record = path.resolve(next());
    else if (a.startsWith("--")) throw new Error(`unknown option ${a}`);
    else opts.pdfs.push(path.resolve(a));
  }
  return opts;
}

function pageRange(spec, count) {
  if (!spec) return Array.from({ length: Math.min(count, 40) }, (_, i) => i + 1);
  const out = [];
  for (const part of spec.split(",")) {
    const [a, b] = part.split("-").map((n) => Number(n));
    for (let p = a; p <= (b || a); p++) if (p >= 1 && p <= count) out.push(p);
  }
  return out;
}

// --- the fixture paper ------------------------------------------------------

function compileFixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-bench-tex-"));
  fs.copyFileSync(FIXTURE_TEX, path.join(dir, "paper.tex"));
  for (let pass = 0; pass < 2; pass++) {
    try {
      execFileSync("pdflatex", ["-interaction=nonstopmode", "-halt-on-error", "paper.tex"], { cwd: dir, stdio: "pipe" });
    } catch (err) {
      throw new Error(`pdflatex failed on the fixture paper (is TeX on PATH?): ${String(err.stdout || err.message).slice(-600)}`);
    }
  }
  return path.join(dir, "paper.pdf");
}

// --- reading pages ----------------------------------------------------------

async function loadPdf(file, spec) {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const doc = await pdfjs.getDocument({ data: new Uint8Array(fs.readFileSync(file)), verbosity: 0 }).promise;
  const known = new Map();
  const pages = [];
  for (const pn of pageRange(spec, doc.numPages)) {
    const page = await doc.getPage(pn);
    const vp = page.getViewport({ scale: 1 });
    const tc = await page.getTextContent();
    const faces = await pageFaces(page, new Set(tc.items.map((it) => it.fontName)), { known });
    const runs = textRuns(tc.items, vp.transform, { faces, styles: tc.styles });
    pages.push({ pn, width: vp.width, height: vp.height, runs });
    page.cleanup();
  }
  await doc.destroy();
  return pages;
}

// --- planning ---------------------------------------------------------------

// A whole-document job, pages in order: every page is in the job, so
// paragraphs join across page breaks as they do in the viewer.
function plan(pages) {
  const paras = new Map(pages.map((p) => [p.pn, segmentPage(p.runs)]));
  const planner = createPlanner({ paras: (pn) => paras.get(pn) || null, inJob: (pn) => paras.has(pn) });
  const chunks = [];
  for (const p of pages) {
    for (const c of chunkUnits(planner.planPage(p.pn))) chunks.push({ pn: p.pn, ...c });
  }
  return { paras, chunks };
}

// --- dry metrics ------------------------------------------------------------

const MATHISH = /[\u0370-\u03ff\u1f00-\u1fff\u2190-\u21ff\u2200-\u22ff\u2a00-\u2aff\u27c0-\u27ef\u2980-\u29ff\ue000-\uf8ff\u0300-\u036f]|[\u{1d400}-\u{1d7ff}]/gu;
function plainOf(text) {
  return text.replace(TOKEN, "").replaceAll(BREAK, " ").trim();
}

function blockOf(paras, t) {
  return paras.get(t.pn)?.[t.i];
}

// A sent text that ends mid-sentence: body text (two lines or more) whose
// last characters close no sentence. Each one is a sentence the model got
// half of.
function endsMid(text, block) {
  const plain = plainOf(text);
  return !!plain && (block?.nLines || 1) >= 2 && !ENDS_SENTENCE.test(plain);
}

function startsMid(text) {
  return /^[a-z]/.test(plainOf(text));
}

function dryMetrics(docs) {
  const m = { pages: 0, blocks: 0, kept: 0, units: 0, chunks: 0, chars: 0, endsMid: 0, startsMid: 0,
              joined: 0, tokens: 0, mathChars: 0, contextMathChars: 0 };
  for (const d of docs) {
    m.pages += d.pages.length;
    for (const paras of d.plan.paras.values()) {
      m.blocks += paras.length;
      m.kept += paras.filter((p) => !p.translate).length;
    }
    m.chunks += d.plan.chunks.length;
    for (const c of d.plan.chunks) {
      // The context around a chunk goes as extracted: its glyph soup counts apart.
      const ctx = Object.values(c.context || {}).join(" ");
      m.contextMathChars += (ctx.match(MATHISH) || []).length;
      for (const u of c.units) {
        m.units += 1;
        m.chars += u.src.length;
        const last = blockOf(d.plan.paras, u.targets[u.targets.length - 1]);
        if (endsMid(u.src, last)) m.endsMid += 1;
        if (startsMid(u.src)) m.startsMid += 1;
        if (u.src.includes(BREAK)) m.joined += 1;
        m.tokens += (u.src.match(TOKEN) || []).length;
        m.mathChars += (u.src.replace(TOKEN, "").match(MATHISH) || []).length;
      }
    }
  }
  return m;
}

// --- live translation -------------------------------------------------------

function liveEnv() {
  const env = {
    protocol: process.env.GAMMA_BENCH_PROTOCOL || "", key: process.env.GAMMA_BENCH_API_KEY || "",
    model: process.env.GAMMA_BENCH_MODEL || "", baseUrl: process.env.GAMMA_BENCH_BASE_URL || "",
    effort: process.env.GAMMA_BENCH_EFFORT || "",
  };
  if (!env.protocol || !env.key || !env.model) {
    throw new Error("--live needs GAMMA_BENCH_PROTOCOL, GAMMA_BENCH_API_KEY and GAMMA_BENCH_MODEL");
  }
  return env;
}

async function usageTotals(account) {
  const u = await account.api("/api/ai/usage");
  return u?.windows?.all || {};
}

function diffUsage(a, b) {
  const out = {};
  for (const k of ["calls", "input", "output", "cache_read", "cache_write"]) out[k] = (b[k] || 0) - (a[k] || 0);
  return out;
}

async function translateLive(docs, opts) {
  const env = liveEnv();
  const { Server, Account } = await import("../e2e/harness.mjs");
  // The model catalog on, as on a real server: the effort a translation
  // asks for by default is the model's lowest level, which it looks up.
  const server = new Server({ env: { GAMMA_MODEL_CATALOG: "on" } });
  try {
    await server.start();
    await server.manage("create-user", "bench", "bench-pw");
    const account = await new Account(server, "bench", "bench-pw").login();
    await account.api("/api/ai/providers", { method: "POST", body: {
      protocol: env.protocol, api_key: env.key, models: env.model, ...(env.baseUrl ? { base_url: env.baseUrl } : {}),
    } });
    const before = await usageTotals(account);
    const t0 = Date.now();
    for (const d of docs) {
      const up = await account.upload("/api/uploads", fs.readFileSync(d.file), path.basename(d.file), "application/pdf");
      const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: {
        default_title: d.name, source_url: up.source_url,
      } });
      d.pageId = created.id;
      // The viewer's queue: N workers over one list of chunks.
      const queue = d.plan.chunks.map((c, n) => ({ c, n }));
      const worker = async () => {
        for (let item = queue.shift(); item; item = queue.shift()) {
          const { c } = item;
          const started = Date.now();
          try {
            const res = await account.api("/api/ai/translate", { method: "POST", body: {
              texts: c.texts, lang: opts.lang, model: "", effort: env.effort, stream: false,
              ...requestFields({ pageId: d.pageId, ...c.context }),
            } });
            c.translations = res.translations;
            c.cached = !!res.cached;
            c.redone = res.redone || 0;
            c.effort = res.effort ?? "";
          } catch (err) {
            c.error = String(err.message || err);
          }
          c.ms = Date.now() - started;
        }
      };
      await Promise.all(Array.from({ length: opts.parallel }, worker));
    }
    const wall = Date.now() - t0;
    const usage = diffUsage(before, await usageTotals(account));
    return { wall, usage };
  } finally {
    await server.stop();
  }
}

// What came back. The server keeps every formula token (a paragraph that
// broke one is redone, then sent back as its source), so what the model got
// wrong shows as `redone` and `unchanged`, not in the final texts.
function liveMetrics(docs, live) {
  const m = { failedChunks: 0, unchanged: 0, redone: 0, breaksLost: 0, chunkMsMedian: 0,
              wallMs: live.wall, effort: "", usage: live.usage };
  const times = [];
  const efforts = new Set();
  for (const d of docs) {
    for (const c of d.plan.chunks) {
      if (c.error || !c.translations) { m.failedChunks += 1; continue; }
      times.push(c.ms);
      m.redone += c.redone || 0;
      if (!c.cached) efforts.add(c.effort || "(none)");
      c.units.forEach((u, j) => {
        const t = c.translations[j] || "";
        if (t === u.src) m.unchanged += 1;
        if (u.src.includes(BREAK) && !t.includes(BREAK)) m.breaksLost += 1;
      });
    }
  }
  times.sort((a, b) => a - b);
  m.chunkMsMedian = times.length ? times[Math.floor(times.length / 2)] : 0;
  m.effort = [...efforts].join(", ");
  return m;
}

// --- term consistency (the judge) -------------------------------------------

const STOP = new Set(("a an the of in on at to for from by with and or as is are was were be been this that these " +
  "those it its we our their which who whom whose when where than then so such can may will would also into over " +
  "under between both each every not no only all any more most other same very while within without").split(" "));

function frequentTerms(sources, limit = 15) {
  const counts = new Map();
  for (const s of sources) {
    const words = plainOf(s).toLowerCase().match(/[a-z][a-z-]+/g) || [];
    for (let n = 2; n <= 3; n++) {
      for (let i = 0; i + n <= words.length; i++) {
        const gram = words.slice(i, i + n);
        if (STOP.has(gram[0]) || STOP.has(gram[n - 1])) continue;
        const k = gram.join(" ");
        counts.set(k, (counts.get(k) || 0) + 1);
      }
    }
  }
  return [...counts].filter(([, n]) => n >= 3).sort((a, b) => b[1] - a[1]).slice(0, limit).map(([k]) => k);
}

async function callModel(env, prompt) {
  const base = (env.baseUrl || "").replace(/\/+$/, "");
  if (env.protocol === "anthropic") {
    const res = await fetch(`${base || "https://api.anthropic.com"}/v1/messages`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-api-key": env.key, "anthropic-version": "2023-06-01" },
      body: JSON.stringify({ model: env.model, max_tokens: 4000, messages: [{ role: "user", content: prompt }] }),
    });
    if (!res.ok) throw new Error(`judge call: HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
    const data = await res.json();
    return (data.content || []).map((b) => b.text || "").join("");
  }
  const root = base ? (/\/v\d+$/.test(base) ? base : `${base}/v1`) : "https://api.openai.com/v1";
  const res = await fetch(`${root}/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${env.key}` },
    body: JSON.stringify({ model: env.model, messages: [{ role: "user", content: prompt }] }),
  });
  if (!res.ok) throw new Error(`judge call: HTTP ${res.status} ${(await res.text()).slice(0, 200)}`);
  const data = await res.json();
  return data.choices?.[0]?.message?.content || "";
}

async function judge(docs, opts) {
  const env = liveEnv();
  const pairs = [];
  for (const d of docs) {
    for (const c of d.plan.chunks) {
      if (!c.translations) continue;
      c.units.forEach((u, j) => pairs.push([plainOf(u.src), plainOf(c.translations[j] || "")]));
    }
  }
  const terms = frequentTerms(pairs.map(([s]) => s));
  // No number is no measurement: null, never a 0 that reads as better than 1.0.
  if (!terms.length) return { terms: 0, meanRenderings: null, perTerm: {}, error: "no frequent terms in what came back" };
  const prompt = [
    `Below are paragraphs from one paper and their ${opts.lang} translations.`,
    "For each English term in the list, find every place the term occurs in a source paragraph and write down,",
    "exactly as it appears, how the matching translation renders it. Reply with ONLY a JSON object mapping each",
    "term to the list of DISTINCT renderings used (an empty list when the term never occurs).",
    "", `Terms: ${JSON.stringify(terms)}`, "",
    ...pairs.map(([s, t], n) => `[${n + 1}] SOURCE: ${s}\n[${n + 1}] TRANSLATION: ${t}`),
  ].join("\n");
  let reply;
  try {
    reply = await callModel(env, prompt);
  } catch (err) {
    return { terms: 0, meanRenderings: null, perTerm: {}, error: String(err.message || err) };
  }
  let parsed = {};
  try { parsed = JSON.parse(reply.slice(reply.indexOf("{"), reply.lastIndexOf("}") + 1)); } catch {}
  const perTerm = {};
  for (const term of terms) {
    const list = Array.isArray(parsed[term]) ? [...new Set(parsed[term].map(String))] : [];
    if (list.length) perTerm[term] = list;
  }
  const counted = Object.values(perTerm);
  return {
    terms: counted.length,
    meanRenderings: counted.length ? counted.reduce((n, l) => n + l.length, 0) / counted.length : null,
    perTerm,
    ...(counted.length ? {} : { error: "the judge's reply named none of the terms" }),
  };
}

// --- output -----------------------------------------------------------------

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const mark = (s) => esc(s).replace(/\{(v\d+|br)\}/g, '<b class="tok">{$1}</b>');

function html(result, docs) {
  const rows = [];
  for (const d of docs) {
    rows.push(`<h2>${esc(d.name)}</h2>`);
    d.plan.chunks.forEach((c, n) => {
      rows.push(`<h3>p.${c.pn} · request ${n + 1}${c.ms ? ` · ${c.ms} ms` : ""}${c.error ? ` · <span class="err">${esc(c.error)}</span>` : ""}</h3>`);
      if (c.context && (c.context.section || c.context.before || c.context.after)) {
        rows.push(`<p class="ctx">section: ${esc(c.context.section || "—")} · before: ${esc((c.context.before || "").slice(-120))} · after: ${esc((c.context.after || "").slice(0, 120))}</p>`);
      }
      rows.push("<table>");
      c.units.forEach((u, j) => {
        const last = blockOf(d.plan.paras, u.targets[u.targets.length - 1]);
        const flags = [endsMid(u.src, last) ? "ends mid-sentence" : "", startsMid(u.src) ? "starts mid-sentence" : "",
                       u.src.includes(BREAK) ? "joined" : ""].filter(Boolean).join(", ");
        rows.push(`<tr><td>${mark(u.src)}</td><td>${mark(c.translations?.[j] ?? "")}</td><td class="flags">${esc(flags)}</td></tr>`);
      });
      rows.push("</table>");
    });
  }
  const metrics = Object.entries({ ...result.dry, ...(result.live || {}) })
    .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</td></tr>`).join("");
  return `<!doctype html><meta charset="utf-8"><title>Translation bench · ${esc(result.label)}</title>
<style>body{font:14px/1.45 system-ui,sans-serif;margin:24px;max-width:1300px}table{border-collapse:collapse;width:100%;margin:6px 0 18px}
td,th{border:1px solid #ccc;padding:6px 8px;vertical-align:top;text-align:left}td.flags{width:140px;color:#a33;font-size:12px}
.tok{color:#06c}.ctx{color:#666;font-size:12px}.err{color:#c00}h3{font-size:13px;margin:14px 0 2px;color:#444}</style>
<h1>Translation bench · ${esc(result.label)}</h1><table>${metrics}</table>${rows.join("\n")}`;
}

function compare([a, b]) {
  const A = JSON.parse(fs.readFileSync(a, "utf8")), B = JSON.parse(fs.readFileSync(b, "utf8"));
  const flat = (r) => ({ ...r.dry, ...(r.live || {}), ...(r.judge ? { meanRenderings: r.judge.meanRenderings } : {}) });
  const fa = flat(A), fb = flat(B);
  // Numbers and strings only (not `usage`); a null judge result stays, shown as —.
  const isObject = (v) => v !== null && typeof v === "object";
  const keys = [...new Set([...Object.keys(fa), ...Object.keys(fb)])].filter((k) => !isObject(fa[k]) && !isObject(fb[k]));
  const w = Math.max(...keys.map((k) => k.length));
  console.log(`${"".padEnd(w)}  ${A.label.padStart(12)}  ${B.label.padStart(12)}`);
  const show = (v) => (v === null || v === undefined ? "—" : String(v));
  for (const k of keys) console.log(`${k.padEnd(w)}  ${show(fa[k]).padStart(12)}  ${show(fb[k]).padStart(12)}`);
}

// --- main -------------------------------------------------------------------

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.compare) return compare(opts.compare);
  if (opts.record) {
    const file = compileFixture();
    const pages = await loadPdf(file, opts.pages);
    const round = (v) => Math.round(v * 100) / 100;
    const out = { source: "tests/fixtures/translate/paper.tex", pages: pages.map((p) => ({
      pn: p.pn, width: round(p.width), height: round(p.height),
      runs: p.runs.map((r) => ({ str: r.str, x: round(r.x), y: round(r.y), w: round(r.w), h: round(r.h),
                                 font: r.font, face: r.face, asc: round(r.asc), desc: round(r.desc) })),
    })) };
    fs.writeFileSync(opts.record, JSON.stringify(out));
    console.log(`recorded ${pages.length} pages, ${pages.reduce((n, p) => n + p.runs.length, 0)} runs → ${opts.record}`);
    return;
  }
  const files = opts.pdfs.length ? opts.pdfs : [compileFixture()];
  const docs = [];
  for (const file of files) {
    const pages = await loadPdf(file, opts.pages);
    docs.push({ file, name: opts.pdfs.length ? path.basename(file) : "fixture paper", pages, plan: plan(pages) });
  }
  const label = opts.label || `run-${new Date().toISOString().replace(/[:.]/g, "-")}`;
  const result = { label, lang: opts.lang, files: docs.map((d) => d.name), dry: dryMetrics(docs) };
  if (opts.live) {
    const live = await translateLive(docs, opts);
    result.live = liveMetrics(docs, live);
    if (opts.judge) result.judge = await judge(docs, opts);
  }
  result.chunks = docs.flatMap((d) => d.plan.chunks.map((c) => ({
    doc: d.name, pn: c.pn, context: c.context || null, texts: c.texts,
    translations: c.translations || null, ms: c.ms || 0, error: c.error || "",
  })));
  fs.mkdirSync(opts.out, { recursive: true });
  const json = path.join(opts.out, `${label}.json`);
  fs.writeFileSync(json, JSON.stringify(result, null, 1));
  fs.writeFileSync(path.join(opts.out, `${label}.html`), html(result, docs));
  console.log(JSON.stringify({ dry: result.dry, live: result.live, judge: result.judge && { terms: result.judge.terms, meanRenderings: result.judge.meanRenderings } }, null, 1));
  console.log(`→ ${json}`);
}

await main();
