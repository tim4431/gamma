// Harness for the browser end-to-end suite (run.mjs): an ISOLATED backend
// over a throwaway data dir serving frontend/dist, accounts + API helpers,
// browser contexts logged in as those accounts, a real-PDF generator, and the
// step runner. Nothing here touches the developer's own data directory.
import { spawn, execFile, execFileSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { chromium, firefox, webkit } from "playwright";
import headed from "../../../tools/headed.cjs";
import { WEBKIT_TOUCH_NOTE, cancelledLoad, clickAfterCancelledTap, touchPoints } from "./webkit.mjs";
import { chordLabel, chordParts } from "../../src/shared/lib/hotkeys.js";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

// The browser runs on this machine's platform, so the page is on a Mac when
// the suite is: Mod is ⌘ there and the page labels keys as a Mac does. A
// step presses the page's Mod as "ControlOrMeta" (Playwright's Meta on a Mac,
// Control elsewhere) and keeps "Control" for the Ctrl key itself, as a
// pinch's Ctrl+wheel is on every platform. keyLabel is a chord as the page
// labels it here: "Mod-v" reads Ctrl+V on Windows and Linux and ⌘V on a Mac.
// keyParts is its key caps: ["Ctrl", "Shift", "K"], or ["⇧", "⌘", "K"].
export const MAC = process.platform === "darwin";
export const keyLabel = (chord) => chordLabel(chord, MAC);
export const keyParts = (chord) => chordParts(chord, MAC);
// The key that puts a text field's caret at the end of its line: End, and
// ⌘→ on a Mac, where End scrolls and leaves the caret where it is. The
// notes' editor binds End itself on every platform.
export const FIELD_END = MAC ? "Meta+ArrowRight" : "End";
const BACKEND = path.join(ROOT, "backend");
// A private build keeps concurrent development builds from replacing assets
// while a browser scenario is reloading the application.
const DIST = process.env.GAMMA_E2E_DIST || path.join(ROOT, "frontend", "dist");
// The backend interpreter: the project venv, or whatever GAMMA_E2E_PYTHON
// names (CI installs the requirements into the runner's python).
export const PYTHON = process.env.GAMMA_E2E_PYTHON || (process.platform === "win32"
  ? path.join(BACKEND, "venv", "Scripts", "python.exe")
  : path.join(BACKEND, "venv", "bin", "python"));

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const execFileAsync = promisify(execFile);

// The value after a command-line flag, unless the next word is another
// flag; `fallback` when the flag is absent.
export const argValue = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i > 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith("--") ? process.argv[i + 1] : fallback;
};

export const flags = {
  keep: process.argv.includes("--keep"),
  headed,
  continueOnFail: process.argv.includes("--continue"),
  only: (() => { const i = process.argv.indexOf("--only"); return i > 0 ? process.argv[i + 1] : ""; })(),
  group: process.argv.includes("--group"),
};

// Whether a scenario file with this step prefix should do its setup: an
// `--only` that is part of the prefix ("mir") or starts with it ("mirror:
// clone…"), or any `--only` inside an explicit `--group` (the group already
// chose the file; step() still filters its steps).
export function wanted(prefix) {
  return !flags.only || flags.group || prefix.includes(flags.only) || flags.only.startsWith(prefix);
}

// ---------------------------------------------------------------------------
// Server

// Every server a run started, the suite's own first: failure artifacts go
// under its temp dir and carry every server's log tail.
const servers = [];

// The developer's own off-site bucket must never receive a test server's
// copies: those variables are dropped, and a scenario sets its own.
const baseEnv = () => Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^GAMMA_(S3_|OFFSITE)/.test(key)));

// uvicorn on a port, gone with this process however that ends: stdin is a pipe
// only this process holds, so the backend of a killed run reads its end and
// exits rather than stay up for good, holding its port and its folder.
const SERVE = "import os, sys, threading, uvicorn; "
  + "threading.Thread(target=lambda: (sys.stdin.read(), os._exit(0)), daemon=True).start(); "
  + "uvicorn.run('app:app', host='127.0.0.1', port=int(sys.argv[1]))";

// A failed run's folder is kept to be read; a killed run could not remove its own.
const KEPT_FOR = 3 * 24 * 60 * 60 * 1000;

export function sweepRuns() {
  for (const name of fs.readdirSync(os.tmpdir())) {
    if (!name.startsWith("gamma-e2e-")) continue;
    const dir = path.join(os.tmpdir(), name);
    try { if (Date.now() - fs.statSync(dir).mtimeMs > KEPT_FOR) fs.rmSync(dir, { recursive: true, force: true }); } catch {}
  }
}

// `env` adds to the backend's environment (a second Gamma started as another
// kind of server, e.g. the publish scenario's share host). `static: false`
// serves the API alone, without a copy of frontend/dist (a script that
// drives no browser, tests/e2e/collabSoak.mjs, needs no build).
export class Server {
  constructor({ env = {}, static: serveApp = true } = {}) {
    servers.push(this);
    this.env = env;
    this.serveApp = serveApp;
    this.dir = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-"));
    this.dataDir = path.join(this.dir, "data");
    this.logPath = path.join(this.dir, "server.log");
    // The backend's own temp files (an import review's staged upload, an
    // export) go here too, so stop() takes them with the rest.
    this.tmpDir = path.join(this.dir, "tmp");
    fs.mkdirSync(this.tmpDir);
    this.port = 0;
    this.proc = null;
  }
  get base() { return `http://127.0.0.1:${this.port}`; }
  // manage.py on this server's data dir; its output. Not synchronous: a run
  // of them (a second each) would block the event loop past the backend's
  // 5 s keep-alive, and the next request would go down a socket it had closed.
  async manage(...args) {
    return this.python("manage.py", ...args);
  }
  // A script of backend/ on this server's data dir (a seed such as
  // tests/seed_work_folder.py, which imports gamma as manage.py does); its output.
  async python(...args) {
    const { stdout } = await execFileAsync(PYTHON, args, {
      cwd: BACKEND, env: { ...baseEnv(), GAMMA_DATA_DIR: this.dataDir, TMPDIR: this.tmpDir, ...this.env }, encoding: "utf8",
    });
    return stdout;
  }
  async start() {
    if (this.serveApp && !fs.existsSync(path.join(DIST, "index.html"))) throw new Error("frontend/dist is missing: run `npm run build` first");
    if (path.isAbsolute(PYTHON) && !fs.existsSync(PYTHON)) throw new Error(`backend venv python not found at ${PYTHON}`);
    fs.mkdirSync(this.dataDir, { recursive: true });
    // Serve a copy of the build: an `npm run build` elsewhere during a run
    // replaces dist/ and would otherwise 500 every page load until it ends.
    const dist = path.join(this.dir, "dist");
    if (this.serveApp) fs.cpSync(DIST, dist, { recursive: true });
    await this.manage("setup");
    this.port = await freePort();
    const log = fs.openSync(this.logPath, "a");
    this.proc = spawn(PYTHON, ["-c", SERVE, String(this.port)], {
      cwd: BACKEND, stdio: ["pipe", log, log],
      env: { ...baseEnv(), GAMMA_DATA_DIR: this.dataDir, ...(this.serveApp ? { GAMMA_STATIC_DIR: dist } : {}), TMPDIR: this.tmpDir, PYTHONIOENCODING: "utf-8", GAMMA_UPDATE_CHECK: "off", GAMMA_MODEL_CATALOG: "off", GAMMA_METADATA_LOOKUP: "off", ...this.env },
    });
    const t0 = Date.now();
    while (Date.now() - t0 < 30000) {
      try { const r = await fetch(`${this.base}/api/session`); if (r.status < 500) return; } catch {}
      if (this.proc.exitCode != null) break;
      await sleep(200);
    }
    throw new Error(`backend did not come up:\n${this.log().slice(-2000)}`);
  }
  log() { try { return fs.readFileSync(this.logPath, "utf8"); } catch { return ""; } }
  async stop() {
    if (this.proc && this.proc.exitCode == null) {
      if (process.platform === "win32") { try { execFileSync("taskkill", ["/pid", String(this.proc.pid), "/t", "/f"], { stdio: "ignore" }); } catch {} }
      else this.proc.kill("SIGTERM");
      await sleep(300);
    }
    // The temp dir survives on --keep and after any failure (its failures/
    // folder holds the screenshots and log tails), a failure's without the
    // copy of the build, which says nothing about it.
    const failed = results.some((r) => !r.ok);
    if (flags.keep || failed) console.log(`kept: ${this.dir}`);
    if (!flags.keep) { try { fs.rmSync(failed ? path.join(this.dir, "dist") : this.dir, { recursive: true, force: true }); } catch {} }
  }
}

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); });
    s.on("error", reject);
  });
}

// ---------------------------------------------------------------------------
// Accounts + API

// A signed-in account bound to one workspace: `api()` for JSON calls (the same
// header the app's fetch wrapper adds), `context()` for a browser context that
// carries its session cookie.
export class Account {
  constructor(server, name, password) {
    this.server = server; this.name = name; this.password = password;
    this.session = ""; this.ws = ""; this.defaultWs = "";
  }
  async login() {
    const r = await fetch(`${this.server.base}/api/login`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: this.name, password: this.password }),
    });
    if (!r.ok) throw new Error(`login ${this.name}: ${r.status} ${await r.text()}`);
    this.session = r.headers.getSetCookie().find((c) => c.startsWith("session=")).split(";")[0].slice("session=".length);
    const s = await (await fetch(`${this.server.base}/api/session`, { headers: { Cookie: `session=${this.session}` } })).json();
    this.defaultWs = s.default_workspace;
    if (!this.ws) this.ws = this.defaultWs;
    return this;
  }
  headers(extra = {}) {
    return { Cookie: `session=${this.session}`, "X-Gamma-Workspace": this.ws, ...extra };
  }
  async api(pathname, { method = "GET", body, form, raw = false } = {}) {
    const init = { method, headers: this.headers() };
    if (form) init.body = form;
    else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    const r = await fetch(`${this.server.base}${pathname}`, init);
    if (raw) return r;
    const text = await r.text();
    let data; try { data = JSON.parse(text); } catch { data = text; }
    if (!r.ok) { const e = new Error(`${method} ${pathname} -> ${r.status}: ${text.slice(0, 300)}`); e.status = r.status; e.data = data; throw e; }
    return data;
  }
  // The folder at `path` ("a/b": names from the top) or the label `name`,
  // made through its tree's ops where missing (POST /api/pages/folders/ops,
  // docs/dev/home_library.md "Folders and labels"); its id.
  async folder(path) {
    return this.treeBlock("folders", path.split("/").map((name) => name.trim()).filter(Boolean));
  }
  async label(name) {
    return this.treeBlock("labels", [name]);
  }
  async treeBlock(tree, names) {
    let node = (await this.api(`/api/blocks/${tree}/subtree`)).block;
    for (const name of names) {
      let next = node.children.find((child) => child.content === name);
      if (!next) {
        next = { id: `e2e${Math.random().toString(36).slice(2, 12)}`, children: [] };
        await this.api(`/api/pages/${tree}/ops`, { method: "POST",
          body: { ops: [{ op: "insert", id: next.id, parent: node.id, content: name }] } });
      }
      node = next;
    }
    return node.id;
  }
  // File a page in folders (paths) and labels (names), made where missing:
  // the page's `folders` / `labels` set to their ids.
  async file(pageId, { folders, labels } = {}) {
    const properties = {};
    if (folders) { properties.folders = []; for (const path of folders) properties.folders.push(await this.folder(path)); }
    if (labels) { properties.labels = []; for (const name of labels) properties.labels.push(await this.label(name)); }
    return this.api(`/api/blocks/${pageId}`, { method: "PUT", body: { properties } });
  }
  async upload(pathname, bytes, filename, type) {
    const fd = new FormData();
    fd.append("file", new Blob([bytes], { type }), filename);
    return this.api(pathname, { method: "POST", form: fd });
  }
  async context(browser, opts = {}) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, ...opts });
    if (this.session) await ctx.addCookies([{ name: "session", value: this.session, url: this.server.base }]);
    return ctx;
  }
}

// ---------------------------------------------------------------------------
// Browser

// Chromium, or the engine GAMMA_E2E_BROWSER names (webkit, firefox: each
// installed once with `npx playwright install <name>`). `scrollbars`:
// Chromium shows its scrollbars, which then take room in their scroller as
// on Windows, Linux and a Mac with a mouse. Playwright hides them by
// default.
export async function launchBrowser({ scrollbars = false } = {}) {
  const headless = { headless: !flags.headed };
  if (process.env.GAMMA_E2E_BROWSER === "webkit") {
    console.log(`  ${WEBKIT_TOUCH_NOTE}`);
    return english(await webkit.launch(headless));
  }
  if (process.env.GAMMA_E2E_BROWSER === "firefox") return english(await firefox.launch(headless));
  const opts = scrollbars ? { ...headless, ignoreDefaultArgs: ["--hide-scrollbars"] } : headless;
  try {
    return english(await chromium.launch(opts));
  } catch (e) {
    if (!/Executable doesn't exist/.test(String(e.message))) throw e;
    console.log("  (downloading Playwright's Chromium once)");
    execFileSync(process.platform === "win32" ? "npx.cmd" : "npx", ["playwright", "install", "chromium-headless-shell"],
      { cwd: path.join(ROOT, "frontend"), stdio: "inherit", shell: process.platform === "win32" });
    return english(await chromium.launch(opts));
  }
}

// The suite selects by English text, and the interface follows the
// browser's language by default (docs/dev/i18n.md), so every context is
// English unless a scenario asks for another locale. Tours offered by
// themselves (docs/dev/onboarding.md) would cover what other scenarios
// click, so every context turns "Suggest tours" off unless it passes
// `suggestTours: true`. A touch context in WebKit gets what Playwright's
// WebKit leaves out of a touch screen (webkit.mjs), or with
// `bareWebkitTap: true` Playwright's tap as it is.
function english(browser) {
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async ({ suggestTours = false, bareWebkitTap = false, ...options } = {}) => {
    const ctx = await newContext({ locale: "en-US", ...options });
    await ctx.addInitScript((on) => { try { localStorage.setItem("gamma-suggest-tours", on ? "1" : "0"); } catch {} }, suggestTours);
    // A context without touch is a mouse machine whatever the host says:
    // Chromium reports the system's touch digitizer, and a Remote Desktop
    // session brings the client's along (ten touch points over an RDP
    // connection from a touch device), which turns on the touch-only
    // controls and guide steps. A hasTouch context keeps Playwright's own
    // emulation.
    if (!options.hasTouch) await ctx.addInitScript(() => { Object.defineProperty(Navigator.prototype, "maxTouchPoints", { get: () => 0, configurable: true }); });
    else if (browser.browserType().name() === "webkit") {
      await ctx.addInitScript(touchPoints);
      if (!bareWebkitTap) await ctx.addInitScript(clickAfterCancelledTap);
    }
    const close = ctx.close.bind(ctx);
    ctx.close = async ({ lastLook: look = true, ...options } = {}) => {
      if (look) for (const page of ctx.pages()) await lastLook(page);
      return close(options);
    };
    return ctx;
  };
  return browser;
}

// A scenario closes its context in `finally`, which runs before step()'s
// catch: by then the page that shows the failure is gone. So a page closed
// during a step leaves its last look (a screenshot and its recorded
// problems), which step() writes out only if the step fails. A step timing
// its close (a page closed before a save is due) passes
// `{lastLook: false}` to `page.close` or `context.close`, which closes at
// once.
async function lastLook(page) {
  if (!running || page.isClosed()) return;
  let shot = null;
  try { shot = await page.screenshot({ type: "jpeg", quality: 80, timeout: 2000 }); } catch {}
  running.closed.push({ shot, problems: page.problems || [] });
}

// API answers that are a designed "no" rather than a failure.
const EXPECTED_FAILURES = [
  /POST \/api\/metadata\/fetch -> 404/, // a PDF without an arXiv id / DOI: nothing to fetch
  /PUT \/api\/chats\/\S+ -> 409/, // a chat save from an older copy: the session merges and saves again
];

// A page that records every API failure, console error and page error so a
// step can assert "nothing went wrong" instead of only "the thing appeared".
// (The browser's own "Failed to load resource" console line is skipped: the
// response listener already records the same failure with its URL.)
const openPages = new Set();

export async function openPage(ctx, url) {
  const page = await ctx.newPage();
  page.problems = [];
  openPages.add(page);
  page.on("close", () => openPages.delete(page));
  const close = page.close.bind(page);
  page.close = async ({ lastLook: look = true, ...options } = {}) => {
    if (look) await lastLook(page);
    return close(options);
  };
  page.on("response", (r) => {
    const u = r.url();
    if (!u.includes("/api/") || r.status() < 400) return;
    const line = `${r.request().method()} ${u.replace(/^https?:\/\/[^/]+/, "")} -> ${r.status()}`;
    if (!EXPECTED_FAILURES.some((re) => re.test(line))) page.problems.push(line);
  });
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) page.problems.push(`console: ${m.text().slice(0, 200)}`);
  });
  page.on("pageerror", (e) => {
    if (!cancelledLoad(String(e), page.url())) page.problems.push(`pageerror: ${String(e).slice(0, 200)}`);
  });
  if (url) await page.goto(url);
  return page;
}

// Settings → a pane by its sidebar button, once that pane is the one shown.
// A pane whose reads are not in keeps the one before on screen for up to
// HOLD_MS, its own button already marked (docs/dev/settings.md "What a pane
// reads"), so what a step looks for next could match in the pane before: the
// Server pane lists the shared workspaces with a Manage of their own.
// `name` is the button's label, which is also the pane head's.
export async function settingsPane(page, name) {
  await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name, exact: true }).click();
  await page.locator(`.settingsPane .setHead[data-setting="${name}"]`).waitFor();
}

// A chat model the browser can see, so the chat's composer is enabled without
// a real provider (with no AI connected it shows the setup card and refuses
// to send). Scenarios that drive the chat route /api/ai/models here on their
// context or page before loading, and fake /api/ai/chat themselves. The slide
// citation a page with metadata asks for by itself once AI looks connected
// gets an empty answer, since no provider is there to write it.
export const FAKE_AI_MODELS = {
  enabled: true, default: "demo:model", efforts: ["low", "medium", "high"],
  models: [{ id: "demo:model", provider: "demo", provider_name: "Demo", model: "model" }],
};
export async function fakeAiModels(target, models = FAKE_AI_MODELS) {
  await target.route("**/api/ai/models*", (route) => route.fulfill({ json: models }));
  await target.route("**/api/metadata/cite", (route) => route.fulfill({ json: { citation: "" } }));
}

export function assertNoProblems(page, allow = []) {
  const bad = page.problems.filter((p) => !allow.some((a) => (a instanceof RegExp ? a.test(p) : p.includes(a))));
  page.problems = [];
  if (bad.length) throw new Error(`problems on the page:\n  ${bad.join("\n  ")}`);
}

// Asserts that `act` opens (or changes) the surfaces `selector` matches in
// place: no frame shows one where it then leaves without changing its size
// or content — a menu painted on one side of its anchor and moved to the
// other, a card beside the last step's anchor. Every frame is read from a
// ResizeObserver made in that frame: observers run in the order they were
// made, so it sees the app's own same-frame corrections, as the paint does
// (docs/dev/ui-design.md, "Menus and popups open in place").
export async function assertOpensInPlace(page, selector, act, { ms = 600, what = selector } = {}) {
  await page.evaluate((selector) => {
    const frames = [], ids = new WeakMap();
    let n = 0, stop = false;
    const sample = () => frames.push([...document.querySelectorAll(selector)].map((el) => {
      if (!ids.has(el)) ids.set(el, ++n);
      const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
      return { id: ids.get(el), seen: cs.visibility !== "hidden" && cs.display !== "none" && r.width > 0,
        x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height), text: el.textContent };
    }));
    const tick = () => {
      if (stop) return;
      const ro = new ResizeObserver(() => { ro.disconnect(); sample(); });
      ro.observe(document.documentElement);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    window.__framesInPlace = () => { stop = true; return frames; };
  }, selector);
  await act();
  await sleep(ms);
  const frames = await page.evaluate(() => window.__framesInPlace());
  const last = new Map();
  let seen = false;
  for (const [i, frame] of frames.entries()) {
    for (const s of frame) {
      if (!s.seen) continue;
      seen = true;
      const p = last.get(s.id);
      if (p && p.w === s.w && p.h === s.h && p.text === s.text && (Math.abs(p.x - s.x) > 2 || Math.abs(p.y - s.y) > 2)) {
        throw new Error(`${what} was painted at (${p.x}, ${p.y}) and moved to (${s.x}, ${s.y}) in frame ${i}`);
      }
      last.set(s.id, s);
    }
  }
  if (!seen) throw new Error(`${what} never showed`);
}

// Resolves once a surface has arrived (docs/dev/ui-design.md, "Motion"):
// the finite animations on the element, inside it and on what holds it
// have ended. A box read before then is the smaller one a menu, a popover,
// a dialog or a zoomed picture grows from.
export async function arrived(locator) {
  await locator.evaluate((el) => {
    const finite = (a) => Number.isFinite(a.effect?.getComputedTiming().endTime);
    const all = el.getAnimations({ subtree: true });
    for (let n = el.parentElement; n; n = n.parentElement) all.push(...n.getAnimations());
    return Promise.all(all.filter(finite).map((a) => a.finished.catch(() => {})));
  });
}

// A press of the mouse's back button over the middle of `over` (a
// locator), else of the page, which Playwright's mouse has no name for:
// through the DevTools protocol, as the hardware's reaches Chromium. Unless
// the page takes it, its release goes back in the window's history.
export async function pressMouseBack(page, over = null) {
  const { width, height } = page.viewportSize();
  const box = over ? await over.boundingBox() : { x: 0, y: 0, width, height };
  const cdp = await page.context().newCDPSession(page);
  const at = { x: Math.round(box.x + box.width / 2), y: Math.round(box.y + box.height / 2), button: "back", clickCount: 1 };
  try {
    await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", buttons: 8, ...at });
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", buttons: 0, ...at });
  } finally {
    await cdp.detach().catch(() => {});
  }
}

// A selector's matches without the copies of closed surfaces on their way
// out (data-leaving, shared/ui/leave.js), for a count or a check taken
// the moment a menu or popover closes.
export const live = (selector) => selector.split(",").map((s) => `${s.trim()}:not([data-leaving], [data-leaving] *)`).join(", ");

// Asserts where an open menu stands from the button that opened it
// (useMenuTrigger, placement.js menuAnchor): its left edge on the button's,
// and its top 4 px under the button (`side` "below") or its bottom 4 px
// over it ("above").
export async function assertMenuFrom(menu, button, side, what = "the menu") {
  await arrived(menu);
  const m = await menu.boundingBox(), b = await button.boundingBox();
  const gap = side === "above" ? b.y - (m.y + m.height) : m.y - (b.y + b.height);
  if (Math.abs(gap - 4) > 1 || Math.abs(m.x - b.x) > 1) {
    throw new Error(`${what} is not 4 px ${side} its button, by its left edge: menu ${JSON.stringify(m)}, button ${JSON.stringify(b)}`);
  }
}

// Asserts an open menu reads as WAI-ARIA's menu (docs/dev/ui-design.md,
// "Menus and submenus"): `menu` is the one role="menu" around its items,
// vertical (no aria-orientation: the role's default), and sits inside
// `around` other menus (a flyout: 1, its parent). Each item of its own
// level sits in exactly one menu of that level, directly in the menu or in
// a group of it; a checkbox or radio item says whether it is checked; and
// every control in it is an item. Returns those items: {text, role,
// checked}. Find the menu by its name (getByRole("menu", {name})) to
// check its name too.
export async function assertMenuRoles(menu, what, around = 0) {
  const ITEMS = '[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]';
  const shape = await menu.evaluate((el, ITEMS) => {
    const menusAround = (node) => {
      let n = 0;
      for (let p = node.parentElement; p; p = p.parentElement) if (p.getAttribute("role") === "menu") n++;
      return n;
    };
    const own = (node) => node.closest('[role="menu"]') === el;
    return {
      role: el.getAttribute("role"),
      orientation: el.getAttribute("aria-orientation"),
      around: menusAround(el),
      items: [...el.querySelectorAll(ITEMS)].filter(own).map((item) => ({
        text: (item.querySelector(".ctxMenuText") || item).textContent.trim(),
        role: item.getAttribute("role"),
        checked: item.getAttribute("aria-checked"),
        menus: menusAround(item),
        // the nearest element with a role around it: its menu, or a group in it
        container: item.parentElement.closest("[role]")?.getAttribute("role"),
      })),
      strays: [...el.querySelectorAll("button, input, select, textarea, a[href], [tabindex]")]
        .filter((node) => own(node) && !node.matches(ITEMS)).map((node) => node.outerHTML.slice(0, 120)),
    };
  }, ITEMS);
  assertEq(shape.role, "menu", `${what} is a menu`);
  assertEq(shape.orientation, null, `${what} is vertical, the menu role's default`);
  assertEq(shape.around, around, around ? `${what} sits inside its parent menu` : `${what} sits inside no other menu`);
  assert(shape.items.length > 0, `${what} has items`);
  for (const item of shape.items) {
    assertEq(item.menus, around + 1, `${what}: "${item.text}" is inside exactly one menu of its level`);
    assert(["menu", "group"].includes(item.container), `${what}: "${item.text}" sits in its menu or a group of it, not a ${item.container}`);
    if (item.role !== "menuitem") assert(["true", "false"].includes(item.checked), `${what}: "${item.text}" (${item.role}) says whether it is checked`);
  }
  assertEq(shape.strays.join(" | "), "", `${what}: every control in it is a menu item`);
  return shape.items.map(({ text, role, checked }) => ({ text, role, checked }));
}

// ---------------------------------------------------------------------------
// PDFs: a small but real PDF (Helvetica text, one content stream per page)
// that pdf.js renders with a selectable text layer.

// `padBytes` appends one unreferenced stream of that many bytes, so a small
// text document can weigh as much as a scanned book (the timing probe's
// transport case) while every page stays tiny to parse and render. A page is
// its lines, or `{lines, box: [w, h]}` for a MediaBox other than US Letter,
// or `{runs: [{x, y, size, font, text, rgb}], fills, box}`: text placed where
// it says (PDF user space, y up: the page's top is y = h), in one of the
// standard fonts "F1" Helvetica, "F2" Times-Roman, "F3" Times-Italic, "F4"
// Symbol (its text in the Symbol encoding: "a" draws α, "W" draws Ω), "F5"
// Times-Bold, or "F6", Liberation Sans embedded in the file (EMBEDDED_FONT,
// its printable ASCII; `embeddedWidth` measures a text in it), `rgb` its
// colour (0 to 1), over `fills`: [{x, y, w, h, rgb}].
export function makePdf(pages, { padBytes = 0 } = {}) {
  const esc = (s) => s.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
  const objs = [];
  const add = (s) => { objs.push(s); return objs.length; };
  const catalog = add("");
  const pagesObj = add("");
  let fonts = ["Helvetica", "Times-Roman", "Times-Italic", "Symbol", "Times-Bold"]
    .map((name, i) => `/F${i + 1} ${add(`<< /Type /Font /Subtype /Type1 /BaseFont /${name} >>`)} 0 R`).join(" ");
  if (pages.some((pg) => pg.runs?.some((r) => r.font === "F6"))) {
    const { file, widths } = embeddedFont();
    const program = add(`<< /Length ${file.length} /Length1 ${file.length} >>\nstream\n${file.toString("latin1")}\nendstream`);
    const descriptor = add(`<< /Type /FontDescriptor /FontName /GAMMAE+LiberationSans /Flags 32 /FontBBox [-203 -303 1050 910] /ItalicAngle 0 /Ascent 905 /Descent -212 /CapHeight 729 /StemV 80 /FontFile2 ${program} 0 R >>`);
    fonts += ` /F6 ${add(`<< /Type /Font /Subtype /TrueType /BaseFont /GAMMAE+LiberationSans /FirstChar 32 /LastChar 126 /Widths [${widths.join(" ")}] /Encoding /WinAnsiEncoding /FontDescriptor ${descriptor} 0 R >>`)} 0 R`;
  }
  const kids = [];
  for (const pg of pages) {
    const [w, h] = (Array.isArray(pg) ? null : pg.box) || [612, 792];
    const colour = (r) => (r.rgb ? `${r.rgb.join(" ")} rg ` : "");
    const content = pg.runs
      ? [...(pg.fills || []).map((f) => `q ${colour(f)}${f.x} ${f.y} ${f.w} ${f.h} re f Q`),
        ...pg.runs.map((r) => `q BT ${colour(r)}/${r.font || "F2"} ${r.size || 10} Tf 1 0 0 1 ${r.x} ${r.y} Tm (${esc(r.text)}) Tj ET Q`)].join("\n")
      : ["BT", "/F1 20 Tf", `72 ${h - 72} Td`, "26 TL", ...(Array.isArray(pg) ? pg : pg.lines).map((l) => `(${esc(l)}) Tj T*`), "ET"].join("\n");
    const stream = add(`<< /Length ${Buffer.byteLength(content, "latin1")} >>\nstream\n${content}\nendstream`);
    kids.push(add(`<< /Type /Page /Parent ${pagesObj} 0 R /MediaBox [0 0 ${w} ${h}] /Resources << /Font << ${fonts} >> >> /Contents ${stream} 0 R >>`));
  }
  if (padBytes > 0) add(`<< /Length ${padBytes} >>
stream
${"x".repeat(padBytes)}
endstream`);
  objs[catalog - 1] = `<< /Type /Catalog /Pages ${pagesObj} 0 R >>`;
  objs[pagesObj - 1] = `<< /Type /Pages /Kids [${kids.map((k) => `${k} 0 R`).join(" ")}] /Count ${kids.length} >>`;
  let out = "%PDF-1.4\n";
  const offsets = [];
  objs.forEach((body, i) => { offsets.push(Buffer.byteLength(out, "latin1")); out += `${i + 1} 0 obj\n${body}\nendobj\n`; });
  const xref = Buffer.byteLength(out, "latin1");
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n` + offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  out += `trailer\n<< /Size ${objs.length + 1} /Root ${catalog} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

// The font makePdf embeds as "F6": pdf.js's own copy of Liberation Sans (SIL
// Open Font License), and its advances, in thousandths of the em, for the codes
// 32 to 126, read from its cmap and hmtx.
export const EMBEDDED_FONT = path.join(ROOT, "frontend", "node_modules", "pdfjs-dist", "standard_fonts", "LiberationSans-Regular.ttf");
let embedded = null;
function embeddedFont() {
  if (embedded) return embedded;
  const file = fs.readFileSync(EMBEDDED_FONT);
  const u16 = (o) => file.readUInt16BE(o), i16 = (o) => file.readInt16BE(o), u32 = (o) => file.readUInt32BE(o);
  const tables = {};
  for (let i = 0; i < u16(4); i++) tables[file.toString("latin1", 12 + 16 * i, 16 + 16 * i)] = u32(20 + 16 * i);
  const unitsPerEm = u16(tables.head + 18), metrics = u16(tables.hhea + 34);
  let map = 0; // the (3, 1) subtable, format 4
  for (let i = 0; i < u16(tables.cmap + 2); i++) {
    const r = tables.cmap + 4 + 8 * i;
    if (u16(r) === 3 && u16(r + 2) === 1) map = tables.cmap + u32(r + 4);
  }
  const segs = u16(map + 6), ends = map + 14, starts = ends + segs + 2, deltas = starts + segs, ranges = deltas + segs;
  const glyph = (c) => {
    for (let s = 0; s < segs; s += 2) {
      if (u16(ends + s) < c) continue;
      if (u16(starts + s) > c) return 0;
      const range = u16(ranges + s);
      if (!range) return (c + i16(deltas + s)) & 0xffff;
      const g = u16(ranges + s + range + 2 * (c - u16(starts + s)));
      return g ? (g + i16(deltas + s)) & 0xffff : 0;
    }
    return 0;
  };
  const widths = [];
  for (let c = 32; c <= 126; c++) widths.push(Math.round(u16(tables.hmtx + 4 * Math.min(glyph(c), metrics - 1)) * 1000 / unitsPerEm));
  return (embedded = { file, widths });
}

// How wide `text` is at `size` in the font makePdf embeds, in pt.
export const embeddedWidth = (text, size) => [...text].reduce((w, c) => w + (embeddedFont().widths[c.charCodeAt(0) - 32] || 0), 0) * size / 1000;

// ---------------------------------------------------------------------------
// Steps

export const results = [];

// The step in progress: `closed` holds the last looks of the pages it closed.
let running = null;

export async function step(name, fn) {
  if (flags.only && !name.includes(flags.only)) return;
  const t0 = Date.now();
  const outer = running;
  const mine = running = { closed: [] };
  try {
    const note = await fn();
    results.push({ name, ok: true, note: note || "", ms: Date.now() - t0 });
    console.log(`  ok    ${name}${note ? "  - " + note : ""}  (${Date.now() - t0} ms)`);
  } catch (e) {
    results.push({ name, ok: false, note: String((e && e.message) || e), ms: Date.now() - t0 });
    console.log(`  FAIL  ${name}\n        ${String((e && e.stack) || e).split("\n").join("\n        ")}`);
    await saveFailureArtifacts(name, e, mine.closed).catch(() => {});
    if (!flags.continueOnFail) throw e;
  } finally {
    running = outer;
  }
}

// On a failed step: a screenshot of every open page and of every page the
// step closed (its last look, `-closed` in the name), the pages' recorded
// problems and the tail of the server log, under <temp dir>/failures/ (the
// temp dir is then kept) — what a CI log alone can't tell you.
async function saveFailureArtifacts(name, err, closed = []) {
  const current = servers[0];
  if (!current) return;
  const dir = path.join(current.dir, "failures");
  fs.mkdirSync(dir, { recursive: true });
  const slug = `${String(results.length).padStart(2, "0")}-${name.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").slice(0, 60)}`;
  const pages = [...openPages].filter((p) => !p.isClosed());
  for (const [i, page] of pages.entries()) {
    try { await page.screenshot({ path: path.join(dir, `${slug}${pages.length > 1 ? `-${i + 1}` : ""}.png`) }); } catch {}
  }
  const shots = closed.filter((c) => c.shot);
  for (const [i, c] of shots.entries()) {
    fs.writeFileSync(path.join(dir, `${slug}-closed${shots.length > 1 ? `-${i + 1}` : ""}.jpg`), c.shot);
  }
  const problems = [...pages, ...closed].flatMap((p) => p.problems || []);
  fs.writeFileSync(path.join(dir, `${slug}.log`), [
    `# ${name}`, "", String((err && err.stack) || err), "", "## page problems", ...(problems.length ? problems : ["(none)"]),
    "", "## server log (tail)", current.log().slice(-8000),
    ...servers.slice(1).filter((s) => s.proc).flatMap((s) => ["", `## ${s.base} log (tail)`, s.log().slice(-4000)]),
  ].join("\n"));
  console.log(`        artifacts: ${dir}`);
}

export function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
export function assertEq(a, b, msg) { if (a !== b) throw new Error(`${msg || "not equal"}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); }
export function assertNear(a, b, tol, msg) { if (!(Math.abs(a - b) <= tol)) throw new Error(`${msg || "not near"}: ${a} vs ${b}`); }

// Poll until `fn` returns truthy (state that lands after a debounce or a
// websocket round trip).
export async function until(fn, { timeout = 8000, every = 100, what = "condition" } = {}) {
  const t0 = Date.now();
  let last;
  while (Date.now() - t0 < timeout) {
    try { last = await fn(); if (last) return last; } catch (e) { last = e; }
    await sleep(every);
  }
  throw new Error(`timed out waiting for ${what}${last instanceof Error ? `: ${last.message}` : ""}`);
}
