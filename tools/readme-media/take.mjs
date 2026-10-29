// The pieces every README recorder shares: an isolated library with the
// curated export, a take's marks and manifest, a paced pointer and keyboard,
// and the scripted demo model. The recorders hold only their story.
//
// A take writes into its scratch folder, artifacts/readme-media/<name>,
// where the renderers (render_feature_demos.py, render-suite.py) read it:
//   frames/      the retina screencast (runtime.mjs startCapture)
//   timing.json  marks, framing and checks, written last and only when every
//                check passed; compose.Take refuses a folder without it
//   failure.png  the page when the recorder failed (timing.failed.json then
//                holds what was measured)
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { ROOT, RETINA, VIEW, startCapture, addCursor } from './runtime.mjs';
import { Server, Account } from '../../frontend/tests/e2e/harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const MEDIA = path.join(ROOT, 'artifacts/readme-media');
export const DEMO_EXPORT = path.resolve(process.env.DEMO_EXPORT || path.join(MEDIA, 'demo.zip'));
export const PASSWORD = 'isolated-media-only';
// The curated paper (Bluvstein et al. 2022, Nature, CC BY 4.0) in demo.zip.
export const ATOMS = { id: 'fy0-h_BqOHcH', title: 'A quantum processor based on coherent transport of entangled atom arrays' };
export const INSPECT = process.argv.includes('--inspect');

/** The take's scratch folder, artifacts/readme-media/<name>. */
export function scratch(name) {
  const dir = path.join(MEDIA, name);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

/** An isolated server (harness Server: free port, throwaway data dir) whose
 *  first user holds the curated export. `shared` names a shared workspace the
 *  first user owns and the others edit; the export goes there. */
export async function library({ users = ['demo'], shared = null, env = {} } = {}) {
  if (!fs.existsSync(DEMO_EXPORT)) throw new Error(`No curated export at ${DEMO_EXPORT}: run export-demo.py, or set DEMO_EXPORT`);
  const server = new Server({ env });
  try {
    await server.start();
    for (const user of users) await server.manage('create-user', user, PASSWORD);
    let ws = null;
    if (shared) {
      const out = await server.manage('create-workspace', shared, users[0], 'shared');
      ws = (out.match(/workspace (\S+)/) || [])[1];
      if (!ws) throw new Error(`No workspace id in: ${out}`);
      for (const user of users.slice(1)) await server.manage('set-member', ws, user, 'editor');
    }
    const accounts = await Promise.all(users.map((user) => new Account(server, user, PASSWORD).login()));
    if (ws) for (const account of accounts) account.ws = ws;
    await accounts[0].upload('/api/import-data', fs.readFileSync(DEMO_EXPORT), 'demo.zip', 'application/zip');
    return { server, accounts, account: accounts[0], ws: accounts[0].ws };
  } catch (error) {
    // The caller never gets the server to stop: stop it here.
    await server.stop().catch(() => {});
    throw error;
  }
}

/** A retina page signed in as `account`: light English UI, the drawn arrow
 *  cursor, tours off (launchRetina). `uiScale` is the Interface size. */
export async function openPage(browser, account, { url = '', uiScale = 0, clipboard = false, cursor = true, init = null } = {}) {
  const context = await browser.newContext(RETINA);
  await context.addCookies([{ name: 'session', value: account.session, url: account.server.base }]);
  if (clipboard) await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: account.server.base });
  if (uiScale) await context.addInitScript((scale) => { try { localStorage.setItem('gamma-ui-scale', String(scale)); } catch {} }, uiScale);
  if (init) await context.addInitScript(init);
  if (cursor) await addCursor(context);
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  page.on('pageerror', (e) => console.log('pageerror:', String(e).slice(0, 200)));
  if (url) await page.goto(new URL(url, account.server.base).href, { waitUntil: 'networkidle' });
  return page;
}

/** Collaborators' sessions send each keystroke 40 ms after it instead of
 *  after the app's 350 ms typing pause, so their words arrive key by key on
 *  the recorded screen. Only for the others' contexts: the recorded view runs
 *  the app as it is. */
export const fastPeerTyping = () => {
  const later = window.setTimeout;
  window.setTimeout = (fn, ms, ...rest) => later(fn, ms === 350 ? 40 : ms, ...rest);
};

const round = (box) => box && Object.fromEntries(['x', 'y', 'width', 'height'].map((k) => [k, +box[k].toFixed(1)]));
const r3 = (x) => +x.toFixed(3);
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export const smooth = (u) => u * u * (3 - 2 * u);

async function boxOf(page, target) {
  if (target && typeof target.x === 'number') return target;
  const locator = typeof target === 'string' ? page.locator(target).first() : target;
  const box = await locator.boundingBox();
  if (!box) throw new Error(`Nothing on screen for ${typeof target === 'string' ? target : 'the locator'}`);
  return box;
}

/** A pointer that remembers where it is and moves on the clock: samples
 *  16 ms apart along a polyline, smoothstep-eased, so a move takes the time
 *  asked on any machine. */
export function hand(page, start = [VIEW.width - 180, VIEW.height - 140]) {
  let at = [...start];
  async function travel(points, ms, ease = true) {
    const t0 = performance.now(), n = Math.max(1, Math.ceil(ms / 16));
    for (let i = 1; i <= n; i++) {
      const u = (ease ? smooth : (v) => v)(i / n) * (points.length - 1);
      const j = Math.min(points.length - 2, Math.floor(u)), f = u - j;
      at = points[j].map((v, k) => v + (points[j + 1][k] - v) * f);
      await page.mouse.move(at[0], at[1]);
      const rest = t0 + ms * i / n - performance.now();
      if (rest > 0) await sleep(rest);
    }
  }
  const self = {
    get at() { return [...at]; },
    async park(x, y) { at = [x, y]; await page.mouse.move(x, y); },
    glide: (to, ms = 550) => travel([at, to], ms),
    /** Glide to a point of `target` (a selector, locator or box) given as fractions of its box; its box. */
    async to(target, ms = 550, fx = 0.5, fy = 0.5) {
      const box = await boxOf(page, target);
      await travel([at, [box.x + box.width * fx, box.y + box.height * fy]], ms);
      return round(box);
    },
    async press(ms = 80) { await page.mouse.down(); await sleep(ms); await page.mouse.up(); },
    async click(target, ms = 550, fx = 0.5, fy = 0.5) { const box = await self.to(target, ms, fx, fy); await sleep(90); await self.press(); return box; },
    /** Press at the first point, follow the path in `ms` (uneased: a hand's stroke), release. */
    async drag(points, ms, { modifiers = [], ease = false } = {}) {
      await travel([at, points[0]], 280);
      for (const key of modifiers) await page.keyboard.down(key);
      await page.mouse.down();
      await travel(points, ms, ease);
      await page.mouse.up();
      for (const key of modifiers) await page.keyboard.up(key);
    },
    /** An eased wheel scroll of `dy` CSS px over `ms` at the pointer. */
    async wheel(dy, ms = 450) {
      const n = Math.max(1, Math.ceil(ms / 16)), t0 = performance.now();
      let done = 0;
      for (let i = 1; i <= n; i++) {
        const next = Math.round(dy * smooth(i / n));
        await page.mouse.wheel(0, next - done);
        done = next;
        const rest = t0 + ms * i / n - performance.now();
        if (rest > 0) await sleep(rest);
      }
    },
  };
  return self;
}

/** Type like a person: `ms` per key with ±`jitter`, scheduled from the first
 *  key so one slow round trip does not push the rest back. */
export async function typeText(page, text, { ms = 30, jitter = 0.15, seed = 7 } = {}) {
  let s = seed;
  const random = () => (s = (s * 1103515245 + 12345) % 2 ** 31) / 2 ** 31;
  const t0 = performance.now();
  let due = 0;
  for (const ch of text) {
    await page.keyboard.type(ch);
    due += ms * (1 - jitter + 2 * jitter * random()) + (ch === ' ' ? 4 : 0);
    const rest = t0 + due - performance.now();
    if (rest > 0) await sleep(rest);
  }
}

// Two retina screencasts at once slow each other down (frames drop, input
// lags), so a take holds this lock from its first frame to its last; the
// setup before it runs in parallel. The lock is machine-wide, so takes in
// other checkouts and worktrees wait too. A lock whose process is gone is
// taken over.
const LOCK = path.join(os.tmpdir(), 'gamma-readme-media.capture.lock');
async function captureLock(name) {
  for (let waited = 0; ; waited += 500) {
    try { fs.mkdirSync(LOCK); fs.writeFileSync(path.join(LOCK, 'owner'), `${process.pid} ${name}`); break; }
    catch (e) {
      if (e.code !== 'EEXIST') throw e;
      let pid = 0;
      try { pid = +fs.readFileSync(path.join(LOCK, 'owner'), 'utf8').split(' ')[0]; } catch {}
      let alive = false;
      try { if (pid) { process.kill(pid, 0); alive = true; } } catch (e) { alive = e.code === 'EPERM'; }
      if (pid && !alive) { fs.rmSync(LOCK, { recursive: true, force: true }); continue; }
      if (waited % 10000 === 0) console.log(`waiting for another capture (${LOCK})`);
      await sleep(500);
    }
  }
  const release = () => { try { if (fs.readFileSync(path.join(LOCK, 'owner'), 'utf8').startsWith(`${process.pid} `)) fs.rmSync(LOCK, { recursive: true, force: true }); } catch {} };
  process.once('exit', release);
  return release;
}

/** One recording: the capture, its marks (capture seconds), the framing
 *  boxes a camera may use (CSS px), and the checks that decide whether the
 *  take is kept. */
export class Take {
  constructor(dir, name) {
    this.dir = dir; this.name = name;
    this.marks = {}; this.framing = {}; this.checks = {}; this.details = {};
  }
  async start(page, options = {}) {
    this.page = page;
    this.release = await captureLock(this.name);
    // The kept take's manifest goes only now, as its frames are replaced: an
    // --inspect run or a setup that fails leaves the kept take as it was.
    for (const f of ['timing.json', 'timing.failed.json', 'failure.png']) fs.rmSync(path.join(this.dir, f), { force: true });
    try { this.capture = await startCapture(page, path.join(this.dir, 'frames'), options); }
    catch (error) { this.release(); throw error; }
    this.t0 = Date.now() - this.capture.clock() * 1000;
    this.mark('start');
  }
  clock() { return this.capture.clock(); }
  mark(name, t = this.clock()) { this.marks[name] = r3(t); return this.marks[name]; }
  /** Record the box of `target` (selector, locator or box) as framing `name`. */
  async frame(name, target) { this.framing[name] = round(await boxOf(this.page, target)); return this.framing[name]; }
  /** Watch the page: `probe` runs in the page on every animation frame and
   *  returns {name: truthy}; the first time a name is truthy is its mark,
   *  the moment the viewer sees it rather than when the recorder noticed. */
  async watch(probe) {
    await this.page.evaluate(`(() => {
      const probe = ${probe.toString()};
      const seen = window.__takeSeen = window.__takeSeen || {};
      const tick = () => {
        let now = {};
        try { now = probe() || {}; } catch {}
        for (const k in now) if (now[k] && !seen[k]) seen[k] = Date.now();
        requestAnimationFrame(tick);
      };
      tick();
    })()`);
  }
  /** The watched marks so far, merged into `marks` (a recorded mark wins). */
  async seen() {
    const seen = await this.page.evaluate(() => window.__takeSeen || {});
    for (const [k, ms] of Object.entries(seen)) if (!(k in this.marks)) this.marks[k] = r3((ms - this.t0) / 1000);
    return seen;
  }
  /** Wait until the page shows watched `name`; its mark. */
  async until(name, timeout = 30000) {
    await this.page.waitForFunction((k) => window.__takeSeen && window.__takeSeen[k], name, { timeout });
    await this.seen();
    return this.marks[name];
  }
  check(name, ok, detail) {
    this.checks[name] = !!ok;
    if (detail !== undefined) this.details[name] = detail;
    if (!ok) console.log(`check failed: ${name}${detail !== undefined ? ' ' + JSON.stringify(detail).slice(0, 300) : ''}`);
    return !!ok;
  }
  async stop() {
    if (!this.capture) return;
    await this.seen().catch(() => {});
    const capture = this.capture;
    this.capture = null;
    try { await capture.stop(); } finally { this.release?.(); }
  }
  /** Stop the capture and write timing.json, only if every check passed. */
  async finish(extra = {}) {
    await this.stop();
    const meta = JSON.parse(fs.readFileSync(path.join(this.dir, 'frames', 'frames.json'), 'utf8'));
    const marks = Object.fromEntries(Object.entries(this.marks).sort((a, b) => a[1] - b[1]));
    let commit = '';
    try { commit = execFileSync('git', ['rev-parse', '--short', 'HEAD'], { cwd: ROOT, encoding: 'utf8' }).trim(); } catch {}
    const failed = Object.keys(this.checks).filter((k) => !this.checks[k]);
    const timing = {
      take: this.name, recorded: { at: new Date().toISOString(), commit }, view: VIEW,
      frames: { dir: 'frames', count: meta.frames.length, first: meta.frames[0].t, last: meta.frames.at(-1).t },
      marks, framing: this.framing, checks: this.checks, details: this.details, ...extra,
    };
    if (!Object.keys(this.checks).length) failed.push('(no checks)');
    fs.writeFileSync(path.join(this.dir, failed.length ? 'timing.failed.json' : 'timing.json'), JSON.stringify(timing, null, 2));
    console.log('marks:', JSON.stringify(marks));
    if (failed.length) throw new Error(`Take refused, failed checks: ${failed.join(', ')}`);
    console.log(`kept: ${path.join(this.dir, 'timing.json')} (${meta.frames.length} frames)`);
    return timing;
  }
  async fail(error) {
    console.log('FAILED:', error && (error.stack || error));
    if (this.page) await this.page.screenshot({ path: path.join(this.dir, 'failure.png') }).catch(() => {});
    await this.stop().catch(() => {});
    // finish() has written the fuller one when checks refused the take.
    const failed = path.join(this.dir, 'timing.failed.json');
    if (this.t0 && !fs.existsSync(failed)) fs.writeFileSync(failed, JSON.stringify({ take: this.name, error: String(error), marks: this.marks, framing: this.framing, checks: this.checks, details: this.details }, null, 2));
    process.exitCode = 1;
  }
}

/** Start the scripted model (demo-model.mjs) in its own process and save it
 *  as the account's AI connection: an OpenAI-compatible service on a local
 *  port. Gamma runs the conversation, its tools, approval cards and edits as
 *  with any model; only the model's words are written in advance. */
export async function demoModel(account, script, args = {}, { model = 'demo-model' } = {}) {
  const proc = spawn(process.execPath, [path.join(HERE, 'demo-model.mjs'), script, JSON.stringify({ ...args, model })], { stdio: ['ignore', 'pipe', 'inherit'] });
  const close = () => { proc.removeAllListeners('exit'); proc.kill(); };
  const port = await new Promise((resolve, reject) => {
    let out = '';
    proc.stdout.on('data', (d) => {
      out += d;
      const line = out.split('\n').find((l) => l.startsWith('{'));
      if (line) resolve(JSON.parse(line).port);
    });
    proc.on('exit', (code) => reject(new Error(`demo-model exited (${code}) before listening`)));
  });
  const base = `http://127.0.0.1:${port}`;
  try {
    const settings = await account.api('/api/ai/providers', { method: 'POST', body: {
      protocol: 'openai', name: 'Demo', api_key: `sk-demo-${crypto.randomBytes(9).toString('hex')}`, base_url: base, models: model } });
    const entry = settings.providers.filter((p) => !p.shared).at(-1);
    const test = await account.api(`/api/ai/providers/${entry.id}/test`, { method: 'POST', body: {} });
    if (!test.ok) throw new Error(`The demo model failed its test: ${JSON.stringify(test)}`);
    return { id: entry.id, model, base, log: async () => (await fetch(`${base}/__log`)).json(), close };
  } catch (error) {
    close();
    throw error;
  }
}
