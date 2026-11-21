// The native apps' shells around the web app (docs/dev/ipad.md "The host
// contract"), played by stand-ins in the page for the ipad and android
// groups: the host's side of every message, the iPad's transport (a
// `window.gammaNative` its user script declares, WebKit's reply handler)
// and the Android app's, which is the app's own document-start script
// (android/app/src/main/assets/shell/host.js, run as it is) over a stand-in
// for its message port. Also what both groups draw with: a PDF page in a
// shell context, and a line held still for the hold.
import fs from "node:fs";
import path from "node:path";
import { ROOT } from "./harness.mjs";
import { waitForPdf } from "./scenarios/pdf.mjs";

// The shell's side, installed with ctx.addInitScript(fakeShell, options)
// before the page's scripts in every frame (Playwright serializes it, so it
// keeps to itself). `platform` "ipad" (the default) declares what the iPad
// app's user script declares (HostInfo, every flag this build's shell
// declares; the other options override them, e.g. {hold: false} for an
// older shell) and answers through `window.webkit.messageHandlers.gamma`;
// "android" stands in for the Android app's message port
// `gammaAndroidHost` (androidShell below adds the app's script and its
// HostInfo). Either way it records what the page posts, a haptic included
// (`window.e2eHostSent`, every message but the file and save chunks), and
// answers it as the shell does: a claim taken (`claims: false` declines
// every one), the opened files, the bar, the saves. `files` (not a flag:
// [{id, name, type, b64}]) are what the system opened in the app, held as
// OpenedFiles.swift and OpenedFiles.kt hold them: listed, read in chunks (of
// 1000 bytes here, so a file takes several) and let go by `fileDone`. Like
// the shell, it still holds them after a reload (the init script runs
// again); `window.e2eHold(file)` adds one later, as a share while the page
// is up does, and `window.e2eHeld` is what it holds. `bar` (not a flag
// either) is the person's choice of the shell's bar as it starts, true for
// up, which `shellBar` sets and answers as AppModel.barHidden does. A save
// (`save`, `saveData`, `saveDone`, `saveUrl`) is kept as Saves.kt keeps it,
// chunks appended at their offsets, and lands whole in `window.e2eSaved`
// ({name, mime, size, parts: base64 chunks} for a blob, {url, name} for a
// URL); `refuseSave` refuses every one.
export function fakeShell(options = {}) {
  const { platform = "ipad", files = [], bar = true, claims = true, refuseSave = false, ...flags } = options;
  let barShown = bar;
  const sent = [];
  window.e2eHostSent = sent;
  const withBytes = (f) => ({ ...f, bytes: Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0)) });
  const held = files.map(withBytes);
  window.e2eHeld = held;
  window.e2eHold = (f) => { held.push(withBytes(f)); };
  const saved = [];
  window.e2eSaved = saved;
  const saves = new Map();
  let nextSave = 0;
  const answer = (msg) => {
    if (msg.type === "claim") return { taken: claims };
    if (msg.type === "files") return { files: held.map(({ id, name, type, bytes }) => ({ id, name, type, size: bytes.length })) };
    if (msg.type === "fileData") {
      const file = held.find((f) => f.id === msg.id);
      if (!file) return {};
      let bin = "";
      for (const b of file.bytes.subarray(msg.offset, msg.offset + 1000)) bin += String.fromCharCode(b);
      return { data: btoa(bin) };
    }
    if (msg.type === "fileDone") {
      const i = held.findIndex((f) => f.id === msg.id);
      if (i >= 0) held.splice(i, 1);
    }
    if (msg.type === "shellBar") {
      if (typeof msg.shown === "boolean") barShown = msg.shown;
      return { shown: barShown };
    }
    if (msg.type === "save") {
      if (refuseSave) return {};
      const id = `save-${++nextSave}`;
      saves.set(id, { name: msg.name, mime: msg.mime, size: msg.size, parts: [], have: 0 });
      return { id };
    }
    if (msg.type === "saveData") {
      const f = saves.get(msg.id);
      if (!f) throw new Error("Gamma for Android has no such save.");
      if (msg.offset !== f.have) throw new Error(`A save's data came at ${msg.offset}, not ${f.have}.`);
      f.parts.push(msg.data);
      f.have += atob(msg.data).length;
      return {};
    }
    if (msg.type === "saveDone") {
      const f = saves.get(msg.id);
      saves.delete(msg.id);
      const kept = !!f && !msg.cancel && f.have === f.size;
      if (kept) saved.push({ name: f.name, mime: f.mime, size: f.size, parts: f.parts });
      return { saved: kept };
    }
    if (msg.type === "saveUrl") {
      saved.push({ url: msg.url, name: msg.name });
      return {};
    }
    return {};
  };
  if (platform === "android") {
    // The port: JSON strings both ways. The page's posts ({id, msg}) are
    // answered on a later task ({reply, value} or {reply, error}, the
    // shell's refusal sentence), as the app's main thread answers them;
    // `{hello: true}` counts in `e2eHellos`. Native's own messages go out
    // with `window.e2eSend(msg)` as {send, msg}, resolving with the page's
    // {result, value} as native reads it (nothing is {}) or rejecting with
    // its error.
    const listeners = new Set();
    const results = new Map();
    let sends = 0;
    window.e2eHellos = 0;
    const deliver = (m) => {
      const event = new MessageEvent("message", { data: JSON.stringify(m) });
      for (const fn of listeners) fn(event);
    };
    window.gammaAndroidHost = {
      postMessage(data) {
        if (typeof data !== "string") throw new TypeError("gammaAndroidHost carries strings.");
        const m = JSON.parse(data);
        if (m.hello) { window.e2eHellos += 1; return; }
        if (m.result != null) {
          const done = results.get(m.result);
          results.delete(m.result);
          done?.(m);
          return;
        }
        if (m.id == null || !m.msg) return;
        if (m.msg.type !== "fileData" && m.msg.type !== "saveData") sent.push(m.msg);
        setTimeout(() => {
          let value;
          try { value = answer(m.msg); } catch (err) { deliver({ reply: m.id, error: err.message }); return; }
          deliver({ reply: m.id, value });
        });
      },
      addEventListener(type, fn) { if (type === "message") listeners.add(fn); },
      removeEventListener(type, fn) { listeners.delete(fn); },
    };
    window.e2eSend = (msg) => new Promise((resolve, reject) => {
      const n = ++sends;
      results.set(n, (m) => (typeof m.error === "string" ? reject(new Error(m.error)) : resolve(m.value ?? {})));
      deliver({ send: n, msg });
    });
    return;
  }
  const native = { platform: "ipad", version: "0.2.0", ink: true,
    inkLooks: ["pressure", "dash", "blend", "stabilize", "pencil", "calligraphy", "snap"],
    hold: true, pencil: true, pencilPhases: true, pencilRoll: true, haptics: true, openFiles: true, shellBar: true, editMenu: true, keepAwake: true, ...flags };
  native.receive = (msg) => new Promise((resolve, reject) => (native.queue ||= []).push({ msg, resolve, reject }));
  window.gammaNative = native;
  window.webkit = { messageHandlers: { gamma: { postMessage: (msg) => {
    if (msg.type !== "fileData") sent.push(JSON.parse(JSON.stringify(msg)));
    return Promise.resolve(answer(msg));
  } } } };
}

// An iPad's own Safari, which would be offered Add to Home Screen; the shell never is.
export const IPAD_UA = "Mozilla/5.0 (iPad; CPU OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1";
// An Android tablet's web view: "Android" without "Mobile", so a tablet's layout.
export const ANDROID_UA = "Mozilla/5.0 (Linux; Android 15; SM-X920 Build/AP3A.240905.015; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/141.0.0.0 Safari/537.36";

// The Android app's document-start script, and the HostInfo the app
// declares in it (docs/dev/ipad.md "The host contract"; a device with a
// vibrator plays the haptics).
export const ANDROID_HOST_JS = fs.readFileSync(path.join(ROOT, "android/app/src/main/assets/shell/host.js"), "utf8");
export const ANDROID_INFO = { haptics: true, hold: true, ink: true, inkLooks: ["pressure", "dash", "blend", "stabilize", "pencil", "calligraphy", "snap"],
  keepAwake: true, openFiles: true, pencil: true, pencilGestures: ["tap", "squeeze"], platform: "android", save: true, shellBar: true, version: "0.1.0" };

// The Android app's shell on a context: the port's stand-in, then the app's
// script with its HostInfo in place of __GAMMA_HOST_INFO__ (as
// Kotlin's String.replace does), keys sorted as the app writes them.
// `options` are fakeShell's; its flags override ANDROID_INFO's (undefined
// leaves a flag out).
export async function androidShell(ctx, options = {}) {
  const { files, bar, claims, refuseSave, ...flags } = options;
  const info = { ...ANDROID_INFO, ...flags };
  const sorted = Object.fromEntries(Object.keys(info).sort().filter((k) => info[k] !== undefined).map((k) => [k, info[k]]));
  await ctx.addInitScript(fakeShell, { files, bar, claims, refuseSave, platform: "android" });
  await ctx.addInitScript({ content: ANDROID_HOST_JS.replaceAll("__GAMMA_HOST_INFO__", JSON.stringify(sorted)) });
}

// A PDF page in a shell context (`flags` are the shell's options), the
// strip open with the stylus pen, and the shell's side played in the page:
// the pen going down on page 1 (the page claims it, the stand-in takes it),
// the host's `hold` and `stroke` with samples [x, y, p, t, a?, z?] in px
// from the page's corner (a `stroke` naming, as `held`, the hold whose
// shape the shell drew), and what the page posted. Native's messages go
// as each shell sends them: the iPad's to gammaNative.receive, the Android
// app's over its port.
export async function shellPaper({ alice, browser, server, openPage, until, makePdf }, platform, name, flags = {}) {
  const android = platform === "android";
  const up = await alice.upload("/api/uploads", makePdf([[`A page the ${android ? "stylus" : "Pencil"} ${name} on.`]]), `${name}.pdf`, "application/pdf");
  const paper = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
    body: { default_title: `${android ? "Android" : "Shell"} ${name}`, source_url: up.source_url } });
  const ctx = await alice.context(browser, { hasTouch: true, viewport: android ? { width: 1280, height: 800 } : { width: 1194, height: 834 },
    userAgent: android ? ANDROID_UA : IPAD_UA });
  if (android) await androidShell(ctx, flags);
  else await ctx.addInitScript(fakeShell, flags || {});
  const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&block=${paper.id}`);
  await waitForPdf(page, 1);
  await page.click("button[aria-label='Handwriting tools']");
  await page.waitForSelector(".pdfInkBar .inkToolBtn.modeActive .inkToolInk");
  const pdf = page.locator('[data-page="1"]');
  const geometry = () => pdf.evaluate((el) => {
    const box = el.getBoundingClientRect();
    return { left: box.left, top: box.top, k: box.width / el.querySelector(".inkLayer").viewBox.baseVal.width };
  });
  const sent = (type) => page.evaluate((type) => window.e2eHostSent.filter((m) => m.type === type), type);
  const receive = (msg) => page.evaluate(([msg, android]) => (android ? window.e2eSend(msg) : window.gammaNative.receive(msg)), [msg, android]);
  const toViewport = async (rows) => { const g = await geometry(); return rows.map(([x, y, ...rest]) => [g.left + x, g.top + y, ...rest]); };
  const down = async (pointerId, x, y) => {
    const claims = (await sent("claim")).length;
    await pdf.evaluate((el, [pointerId, x, y]) => {
      const box = el.getBoundingClientRect();
      el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, pointerType: "pen", pointerId,
        button: 0, buttons: 1, pressure: 0.5, clientX: box.left + x, clientY: box.top + y }));
    }, [pointerId, x, y]);
    const claim = await until(async () => (await sent("claim")).slice(claims).find((m) => m.pointerId === pointerId),
      { what: `pointer ${pointerId} claimed` });
    await until(async () => (await pdf.locator(".inkCanvas").evaluate((c) => c.style.display)) === "none", { what: "the draft handed over" });
    return claim;
  };
  const shell = { pdf, geometry, sent, receive, toViewport, down,
    hold: async (pointerId, rows) => receive({ type: "hold", pointerId, samples: await toViewport(rows) }),
    stroke: async (pointerId, rows, held) => receive({ type: "stroke", pointerId, samples: await toViewport(rows), ...(held == null ? {} : { held }) }) };
  return { ctx, page, paper, shell };
}

// A line drawn left to right over 300 ms at `y`, then held still to 780 ms
// (six-value samples, the pen at 50°): the stillness anchor is its end at
// 300 ms, so the hold's snap time is T_SNAP, 900 ms.
export const heldLine = (y) => [
  ...Array.from({ length: 16 }, (_, i) => [100 + 10 * i, y + (i % 2 ? 0.6 : -0.6), 0.5, 20 * i, 50, 90]),
  ...Array.from({ length: 6 }, (_, j) => [250 + (j % 2 ? 0.5 : -0.25), y + (j % 2 ? 0.25 : -0.25), 0.5, 380 + 80 * j, 50, 90]),
];
export const T_SNAP = 900;
