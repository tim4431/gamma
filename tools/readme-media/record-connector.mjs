// README "Connect your research" demo: on a Physical Review Letters article
// page the Gamma Connector's badge has found the paper; its popup files it
// under Quantum/Neutral atoms with the label "rydberg gates", saves it, and
// the PDF opens in Gamma with its folder and label.
//
//   node tools/readme-media/record-connector.mjs [--inspect]
//
// Runs on its own: an isolated harness server with registry lookups on
// (take.mjs library()), the curated export, the atom-arrays paper filed under
// Quantum/Neutral atoms so the folder exists. Full Chromium at 2x with the
// unpacked extension; its window is 40 px shorter than the frame, and the
// render (render-suite.py connector) puts a browser toolbar there: its states
// are screenshots of toolbar() below with the extension's real badge. One
// take, three screencasts in the scratch folder:
//   frames/  the APS page (piece a), up to the pointer reaching the toolbar
//   popup/   the popup opened as a page through its `?tab=` hook (piece b),
//            composited under the toolbar icon; its height over time is
//            sampled, since the render cuts it out of a 1440 px wide page
//   gamma/   the paper opening in Gamma (piece c)
// Marks are each piece's own capture seconds (a*, b*, c*). `--inspect` runs
// the story without capturing and saves a screenshot of every beat.
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, VIEW, launchRetinaExtension, startCapture, addCursor } from './runtime.mjs';
import { library, hand, typeText, Take, scratch, sleep, ATOMS, INSPECT } from './take.mjs';

const NAME = 'connector';
const DIR = scratch(NAME);
// The toolbar pictures the render reads: an --inspect run keeps its own.
const BARS = INSPECT ? scratch(path.join(NAME, 'inspect')) : DIR;
const EXT = path.join(ROOT, 'extension');
const DOI = '10.1103/PhysRevLett.123.170503';
const ARTICLE = `https://journals.aps.org/prl/abstract/${DOI}`;
const TITLE = /Parallel Implementation of High-Fidelity Multiqubit/i;
const FOLDER = 'Quantum/Neutral atoms';
const LABEL = 'rydberg gates';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';
const TOOLBAR = 40, PAGE_H = VIEW.height - TOOLBAR, POPUP_W = 360;
// Where the pointer rests on the toolbar icon (from its top left): its upper
// left, so the arrow leaves the badge (lower right) in view.
const HOT = [4, 3];
// A pointer move along a polyline in `ms`, eased over its whole length (the
// hand's glide goes straight, and on the APS page that crosses Sign in). It
// is paced by the clock: each step goes where the pointer should be by now,
// so a slow page drops steps instead of stretching the move.
async function path2(page, h, points, ms) {
  const all = [h.at, ...points], legs = all.slice(1).map((p, i) => Math.hypot(p[0] - all[i][0], p[1] - all[i][1]));
  const total = legs.reduce((a, b) => a + b, 0), t0 = performance.now();
  for (let u = 0; u < 1; u = Math.min(1, (performance.now() - t0) / ms)) {
    const e = u * u * (3 - 2 * u);
    let d = e * total, k = 0;
    while (k < legs.length - 1 && d > legs[k]) d -= legs[k++];
    const f = legs[k] ? Math.min(1, d / legs[k]) : 1;
    await page.mouse.move(all[k][0] + (all[k + 1][0] - all[k][0]) * f, all[k][1] + (all[k + 1][1] - all[k][1]) * f);
    await sleep(Math.max(0, 16 - ((performance.now() - t0) % 16)));
  }
  await h.park(...points.at(-1));
}
// The hand's to/click, clock-paced like path2: in the popup each pointer
// move can take a frame or two, which would stretch a step-paced glide.
async function glideTo(page, h, target, ms, fx = 0.5, fy = 0.5) {
  const box = await (typeof target === 'string' ? page.locator(target).first() : target).boundingBox();
  await path2(page, h, [[box.x + box.width * fx, box.y + box.height * fy]], ms);
  return box;
}
async function clickAt(page, h, target, ms, fx, fy) {
  await glideTo(page, h, target, ms, fx, fy);
  await sleep(90);
  await h.press();
}
// Wait until `expression` is truthy in the page. The popup's CSP refuses
// waitForFunction's eval; an evaluate from DevTools is not subject to it.
async function poll(page, expression, timeout = 60000) {
  for (const t0 = Date.now(); Date.now() - t0 < timeout; await sleep(40)) if (await page.evaluate(expression).catch(() => false)) return;
  throw new Error(`Timed out waiting for ${expression.slice(0, 120)}`);
}

// A light browser toolbar: navigation, the address, the extension's icon and badge.
const ICON = (name) => 'data:image/png;base64,' + fs.readFileSync(path.join(EXT, 'assets/icons', name)).toString('base64');
const ARROW = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M4 2.5v16.2l4.3-3.9 2.9 6.4 3-1.3-2.8-6.3h5.9z" fill="#16181d" stroke="#fff" stroke-width="1.7" stroke-linejoin="round"/></svg>';
const glyph = (d) => `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#5f6368" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
function toolbar({ host, rest, badge, state }) {
  return `<!doctype html><meta charset="utf-8"><style>
  * { box-sizing: border-box; }
  body { margin: 0; font: 13px "Segoe UI", system-ui, sans-serif; }
  .bar { height: ${TOOLBAR}px; display: flex; align-items: center; gap: 6px; padding: 0 10px; background: #eef1f5; border-bottom: 1px solid #d8dce2; }
  .nav { width: 28px; height: 28px; display: grid; place-items: center; border-radius: 50%; }
  .url { flex: 1; height: 28px; margin: 0 8px; display: flex; align-items: center; gap: 8px; padding: 0 12px; background: #fff; border-radius: 14px; color: #1f1f1f; white-space: nowrap; overflow: hidden; }
  .url .rest { color: #5f6368; }
  .ext { position: relative; width: 28px; height: 28px; display: grid; place-items: center; border-radius: 50%; }
  .ext.hover { background: #dde1e7; } .ext.open { background: #d3d8e0; }
  .ext img { width: 16px; height: 16px; }
  .badge { position: absolute; right: -3px; bottom: 1px; padding: 0 3px; min-width: 14px; height: 12px; border-radius: 3px; font: 600 8.5px/12px "Segoe UI", sans-serif; color: #fff; text-align: center; }
  .cursor { position: absolute; left: ${HOT[0]}px; top: ${HOT[1]}px; margin: -2.5px 0 0 -4px; filter: drop-shadow(0 1.5px 2px rgba(0,0,0,.35)); }
  </style><div class="bar">
    <span class="nav">${glyph('<path d="M19 12H5M12 19l-7-7 7-7"/>')}</span>
    <span class="nav">${glyph('<path d="M5 12h14M12 5l7 7-7 7"/>')}</span>
    <span class="nav">${glyph('<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>')}</span>
    <div class="url">${glyph('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>')}<span>${host}<span class="rest">${rest}</span></span></div>
    <span class="ext ${state}"><img src="${ICON(badge ? 'icon32.png' : 'icon32-off.png')}">${badge ? `<span class="badge" style="background:${badge.color}">${badge.text}</span>` : ''}${state === 'hover' ? `<span class="cursor">${ARROW}</span>` : ''}</span>
    <span class="nav">${glyph('<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>')}</span>
  </div>`;
}

// APS answers a headless browser with a Cloudflare check but serves curl the
// page itself: its requests go through curl and are handed to the browser at
// their own address, so the extension reads the real page.
// APS can take half a minute to answer and now and then answers 502, so a
// request is tried again on a server error, the article and its PDF are
// fetched while the server starts (the save's PDF fetch is cut from the clip
// anyway), and each address is fetched once per run.
const PDF = `http://link.aps.org/pdf/${DOI}`;   // the page's citation_pdf_url
const fetched = new Map(), routed = [];
async function curlOnce(url) {
  if (!fetched.has(url)) fetched.set(url, (async () => {
    let got;
    for (let i = 0; i < 4; i++) {
      got = await curl(url);
      if (got.status < 500) break;
      await sleep(1500);
    }
    if (got.status >= 500) fetched.delete(url);
    return got;
  })());
  const t0 = Date.now(), got = await fetched.get(url);
  routed.push([url.slice(0, 100), got.status, Date.now() - t0]);
  return got;
}
function curl(url) {
  const file = path.join(DIR, `aps-${process.pid}-${Math.random().toString(36).slice(2)}.tmp`);
  return new Promise((resolve, reject) => execFile('curl', ['-sL', '-A', UA, '-o', file, '-w', '%{http_code} %{content_type}', url], (error, out) => {
    if (error) return reject(error);
    const [status, ...type] = out.split(' ');
    const body = fs.readFileSync(file);
    fs.rmSync(file, { force: true });
    resolve({ status: Number(status), contentType: type.join(' ') || undefined, body });
  }));
}

const take = new Take(DIR, NAME);
const mark = (name) => INSPECT ? 0 : take.mark(name);
let lib, context, sampling = false, sampler = null;
try {
  for (const url of [ARTICLE, PDF]) curlOnce(url).catch(() => {});
  lib = await library({ env: { GAMMA_METADATA_LOOKUP: 'on' } });
  const { account, server } = lib;
  await account.file(ATOMS.id, { folders: [FOLDER] });   // the folder the popup offers (made where missing)

  context = await launchRetinaExtension(EXT, { height: PAGE_H });
  await addCursor(context);
  // The extension and the Gamma tab use the server as localhost:<port> (the
  // harness listens on 127.0.0.1; localhost is the same loopback server).
  const GAMMA = server.base.replace('//127.0.0.1:', '//localhost:');
  await context.addCookies([GAMMA, server.base].map((url) => ({ name: 'session', value: account.session, url })));
  context.on('page', (p) => p.setDefaultTimeout(20000));
  // The right dock holds the notes alone: a fresh browser's layout also has
  // the chat under them, which on an account without an AI service is a
  // panel of "connect a service" cards. (A layout is a browser preference.)
  await context.addInitScript((origin) => {
    if (location.origin !== origin) return;
    try { localStorage.setItem('gamma-layout', JSON.stringify({ left: [], right: ['notes'], bottom: [] })); } catch {}
  }, GAMMA);
  let sw = context.serviceWorkers()[0];
  if (!sw) sw = await context.waitForEvent('serviceworker');
  await sw.evaluate((base) => chrome.storage.sync.set({ server: base }), GAMMA);
  const extId = new URL(sw.url()).host;
  const popupUrl = (tab) => `chrome-extension://${extId}/popup.html${tab ? `?tab=${tab}` : ''}`;
  // A first popup signs the extension in with the session cookie.
  const signIn = await context.newPage();
  await signIn.goto(popupUrl());
  await signIn.waitForSelector('#view-main:not(.hidden)', { timeout: 15000 });
  await signIn.close();
  const badgeOf = async (tabId) => {
    const [text, rgba] = await sw.evaluate(async (id) => [await chrome.action.getBadgeText({ tabId: id }), await chrome.action.getBadgeBackgroundColor({ tabId: id })], tabId);
    return text ? { text, color: `rgb(${rgba.slice(0, 3).join(',')})` } : null;
  };
  const tabOf = (pattern) => sw.evaluate(async (p) => (await chrome.tabs.query({ url: p }))[0]?.id, pattern);

  // --- the APS page, detected (off camera) ---------------------------------
  // The consent and analytics scripts are left out (a banner would cover the page).
  await context.route(/^https:\/\/(cmp\.osano\.com|www\.googletagmanager\.com|scholar\.google\.com)\//, (route) => route.abort());
  await context.route(/^https?:\/\/(journals|link)\.aps\.org\//, async (route) => route.fulfill(await curlOnce(route.request().url())));
  const A = context.pages()[0] || await context.newPage();
  A.setDefaultTimeout(20000);
  await A.goto(ARTICLE, { waitUntil: 'domcontentloaded', timeout: 150000 });
  await A.waitForLoadState('load', { timeout: 90000 }).catch(() => {});
  // The page names the institution whose network fetched it: that notice goes.
  const removed = await A.evaluate(() => {
    const notice = (el) => /^\s*access (provided )?by\s+\S/i.test(el.textContent) && el.textContent.length < 120;
    const hits = [...document.querySelectorAll('body *')].filter((el) => notice(el) && ![...el.children].some(notice));
    for (const el of hits) {
      const box = el.parentElement;
      el.remove();
      if (box && !box.textContent.trim()) box.remove();
    }
    return hits.length;
  });
  await A.locator('h3, h1').filter({ hasText: TITLE }).first().waitFor();
  await A.evaluate(() => document.fonts.ready);
  const tabId = await tabOf('*://journals.aps.org/*');
  let badge = null;
  for (let i = 0; i < 40 && !(badge = await badgeOf(tabId)); i++) await sleep(500);
  take.check('detected', badge && !['!', '?'].includes(badge.text), badge);
  if (!badge) throw new Error('The Connector did not detect the APS paper');
  await sleep(1500);   // late images and fonts settle before the first frame

  // The toolbar's states before the save, and where its icon is.
  const bar = await context.newPage();
  const shoot = async (name, options) => {
    await bar.setContent(toolbar(options));
    await bar.screenshot({ path: path.join(BARS, `toolbar-${name}.png`), clip: { x: 0, y: 0, width: VIEW.width, height: TOOLBAR } });
  };
  const aps = { host: 'journals.aps.org', rest: `/prl/abstract/${DOI}` };
  await shoot('page', { ...aps, badge, state: '' });
  await shoot('hover', { ...aps, badge, state: 'hover' });
  await shoot('open', { ...aps, badge, state: 'open' });
  const icon = await bar.locator('.ext').boundingBox();
  await bar.close();
  // Chrome hangs the popup right-aligned under the icon.
  const popupAt = { x: Math.round(icon.x + icon.width + 4 - POPUP_W), y: TOOLBAR + 4 };

  // --- a: the APS page, the pointer goes up to the badge ----------------------
  await A.bringToFront();
  const title = await A.locator('h3, h1').filter({ hasText: TITLE }).first().boundingBox();
  const ha = hand(A, [title.x + Math.min(title.width, 640) * 0.62, title.y + title.height * 0.8]);
  await ha.park(...ha.at);
  if (INSPECT) await A.screenshot({ path: path.join(DIR, 'inspect-1-aps.png') });
  else {
    await take.start(A, { height: PAGE_H });
    await take.frame('apsTitle', title);
    await sleep(1000);
    // Up to the toolbar's icon: the page's last pixel row under it; the
    // toolbar's hover state takes the pointer from there.
    mark('aGlide');
    // Through the banner, right of the header's buttons, and up to the icon.
    await path2(A, ha, [[VIEW.width - 22, 115], [icon.x + HOT[0], 1]], 750);
    mark('aIcon');
    // The drawn pointer leaves the page: from here the toolbar's hover state
    // draws it on the icon. The APS page's screencast often sends no frame
    // for this alone, so the render also paints the page's top strip under
    // the icon from the first frame (render-suite.py Browser).
    await A.evaluate(() => { for (const c of document.querySelectorAll('[id="__fakecur"]')) c.style.visibility = 'hidden'; });
    await sleep(400);
    mark('aHidden');
    await take.seen();
  }

  // --- b: the popup -----------------------------------------------------------
  const B = await context.newPage();
  const heights = [];
  const piece = async (page, dir) => {
    await take.seen();
    await take.capture.stop();
    take.page = page;
    take.capture = await startCapture(page, path.join(DIR, dir), { height: PAGE_H });
    take.t0 = Date.now() - take.capture.clock() * 1000;
  };
  if (!INSPECT) {
    await piece(B, 'popup');
    sampling = true;
    sampler = (async () => {
      while (sampling) {
        // The popup's height: its content, and an open menu hanging below it.
        const h = await B.evaluate(() => !document.body ? 0 : Math.ceil(Math.max(document.body.getBoundingClientRect().bottom,
          ...[...document.querySelectorAll('.ctxMenu:not(.hidden)')].map((m) => m.getBoundingClientRect().bottom + 8)))).catch(() => 0);
        heights.push([+take.clock().toFixed(3), h]);
        await sleep(40);
      }
    })();
  }
  await B.goto(popupUrl(tabId));
  const popupProbe = () => {
    const $ = (id) => document.getElementById(id);
    const on = (id) => $(id) && !$(id).classList.contains('hidden');
    return {
      bReady: on('found') && /Parallel Implementation/i.test($('title')?.textContent || '') && /Levine/.test($('sub')?.textContent || ''),
      bFolderMenu: on('folder-menu'),
      bFolder: /Neutral atoms/.test($('folder-btn')?.textContent || '') && !on('folder-menu'),
      bLabel: [...document.querySelectorAll('#labels-box .categoryTag')].some((c) => c.textContent.trim() === 'rydberg gates'),
      bProgress: on('progress'),
      // The progress row first reads "starting…", then "saving to your
      // library…"; the render ends the press before the next words (the
      // PDF fetch's).
      bProgressNext: on('progress') && /\S/.test($('progress-text')?.textContent || '') && !/^(starting|saving to your library)/i.test(($('progress-text')?.textContent || '').trim()),
      bSaved: on('existing') && /Saved to/.test($('existing-where')?.textContent || ''),
    };
  };
  if (!INSPECT) await take.watch(popupProbe);
  // A watched name's mark is the frame the probe saw it in, not when this noticed.
  const popupSeen = async (name, timeout = 60000) => {
    await poll(B, INSPECT ? `(${popupProbe})().${name}` : `!!(window.__takeSeen || {}).${name}`, timeout);
    if (!INSPECT) await take.seen();
  };
  await popupSeen('bReady');
  const shownTitle = await B.locator('#title').innerText();
  const shownSub = await B.locator('#sub').innerText();
  take.check('popupTitle', TITLE.test(shownTitle), shownTitle);
  take.check('popupAuthors', /Levine/.test(shownSub), shownSub);
  if (!INSPECT) {
    await take.frame('popupHead', '#view-main .head');
    await take.frame('popupForm', '#found');
  }
  // The pointer comes in from the toolbar's icon, just above the popup.
  const hb = hand(B, [icon.x + HOT[0] - popupAt.x, 1]);
  await hb.park(...hb.at);
  if (INSPECT) await B.screenshot({ path: path.join(DIR, 'inspect-2-popup.png') });
  await sleep(900);   // the eye reads the title and authors
  // A folder.
  mark('bFolderGo');
  await clickAt(B, hb, '#folder-btn', 480, 0.55, 0.5);
  const item = B.locator('#folder-menu .ctxMenuItem').filter({ hasText: /Neutral atoms$/ }).first();
  await item.waitFor();
  if (!INSPECT) await take.frame('folderMenu', '#folder-menu');
  await sleep(250);
  await clickAt(B, hb, item, 400, 0.35, 0.5);
  await sleep(300);
  // A label: typed, committed with a comma.
  mark('bLabelGo');
  // Into the field's right part, which the typed label never reaches, so
  // the arrow does not cover it.
  await clickAt(B, hb, '#labels', 420, 0.85, 0.5);
  await sleep(100);
  mark('bType');
  await typeText(B, LABEL, { ms: 50 });
  await typeText(B, ',', { ms: 50 });
  mark('bTyped');
  await sleep(150);
  // The label suggestions open over the Save button.
  if (await B.locator('#label-menu').isVisible()) await B.keyboard.press('Escape');
  await B.locator('#label-menu').waitFor({ state: 'hidden' });
  await popupSeen('bLabel');
  await sleep(100);
  mark('bSaveGo');
  // Save is pressed on its left part: the saved view puts nothing there
  // (Open in Gamma ends just above it), so the pointer stays put through
  // the save and the cut past its wait shows one pointer, hovering nothing.
  await glideTo(B, hb, '#save', 450, 0.2, 0.4);
  mark('bFilled');
  if (INSPECT) await B.screenshot({ path: path.join(DIR, 'inspect-3-filled.png') });
  await sleep(850);   // the filled popup holds
  mark('bSave');
  await hb.press();
  await popupSeen('bSaved', 240000);
  const savedText = await B.locator('#existing-msg').innerText();
  take.check('popupSaved', /Saved to your library/.test(savedText), savedText);
  if (!INSPECT) await take.frame('popupSaved', '#existing');
  // Where Open in Gamma is pressed, in the frame's coordinates (the Gamma
  // tab's pointer starts under it, see below).
  const openBox = await B.locator('#open-existing').boundingBox();
  const pressAt = [popupAt.x + openBox.x + openBox.width / 2, popupAt.y + openBox.y + openBox.height / 2 - TOOLBAR];
  if (!INSPECT) await take.frame('openButton', { x: pressAt[0] - openBox.width / 2, y: pressAt[1] - openBox.height / 2, width: openBox.width, height: openBox.height });
  if (INSPECT) await B.screenshot({ path: path.join(DIR, 'inspect-4-saved.png') });
  await sleep(1000);   // "Saved to your library" holds
  mark('bOpenGo');
  await glideTo(B, hb, '#open-existing', 450, 0.5, 0.5);
  await sleep(200);
  // The click closes the popup, and a screencast of a closed page never
  // stops, so its capture ends just before it: the drawn cursor's press
  // ring plays from a pointerdown sent to the document alone (no button
  // hears it), and the real click follows once the capture has stopped.
  mark('bPress');
  await B.evaluate(() => document.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })));
  await sleep(300);
  mark('bEnd');
  if (!INSPECT) { sampling = false; await sampler; await take.seen(); await take.capture.stop(); take.capture = null; }
  const opened = context.waitForEvent('page', { timeout: 30000 });
  await hb.press();

  // --- c: the paper in Gamma --------------------------------------------------
  const C = await opened;
  C.setDefaultTimeout(20000);
  await C.bringToFront();
  if (!INSPECT) {
    take.page = C;
    take.capture = await startCapture(C, path.join(DIR, 'gamma'), { height: PAGE_H });
    take.t0 = Date.now() - take.capture.clock() * 1000;
    mark('cStart');
  }
  await C.waitForLoadState('domcontentloaded');
  const gammaProbe = () => {
    // Page 1 shows a drawn bitmap (pageRaster.js draws into a hidden twin,
    // marked data-spare, and swaps it in) under its text layer.
    const page1 = document.querySelector('.pdfPageWrap[data-page="1"]');
    const shown = page1 && [...page1.querySelectorAll('.pdfPageCanvas canvas')].some((c) => !('spare' in c.dataset) && c.style.display !== 'none' && c.width > 0);
    // The notes column's chips: categoryBadge (a folder's also folderChip).
    const chips = [...document.querySelectorAll('.categoryBadge')].map((c) => c.textContent.trim());
    const calm = !!(shown && page1.querySelector('.textLayer span')) && chips.includes('rydberg gates')
      && !document.querySelector('.pillSpin, .statusPill, .chatPanel');
    if (!calm) window.__calmSince = 0;
    else if (!window.__calmSince) window.__calmSince = performance.now();
    const settled = calm && performance.now() - window.__calmSince > 700;
    return {
      cPainted: !!(shown && page1.querySelector('.textLayer span')),
      cChips: chips.some((t) => /Neutral atoms/.test(t)) && chips.includes('rydberg gates'),
      // Settled: all that, and no spinner or status pill ("Rendering page…",
      // the metadata look-up's) for 0.7 s on end.
      cSettled: settled,
    };
  };
  if (!INSPECT) await take.watch(gammaProbe);
  // The cursor rests in the empty notes column, off the title (hovered, the
  // title would show its edit box), from before the page is shown: first
  // low in the column, then, once the chips are laid out, straight below
  // the spot Open in Gamma was pressed and under the note's first line, so
  // the camera's popup framing keeps it in view near where it was.
  const hc = hand(C, [1215, 600]);
  await hc.park(...hc.at);
  await poll(C, `(${gammaProbe})().cChips`, 90000);
  const restAt = await C.evaluate(([x]) => {
    const boxes = [...document.querySelectorAll('.categoryBadge')].map((c) => c.getBoundingClientRect()).filter((r) => r.width);
    return [x, Math.max(...boxes.map((r) => r.bottom)) + 80];
  }, pressAt);
  await hc.park(...restAt);
  const under = await C.evaluate(([x, y]) => { const el = document.elementFromPoint(x, y); return el ? `${el.closest('.blockRow, [contenteditable], button, a') ? 'HOVERS ' : ''}${el.tagName}.${el.className}`.slice(0, 100) : ''; }, restAt);
  take.details.gammaPointer = { pressAt, restAt, under };
  if (!INSPECT) mark('cParked');
  // Opening the paper looks up its metadata (a spinner on the info button,
  // then a status pill): the clip shows the page once that has settled.
  await poll(C, `(${gammaProbe})().cSettled`, 90000);
  await sleep(300);
  if (!INSPECT) {
    await take.seen();
    mark('cReady');
    // The notes column's title and its folder and label chips.
    await take.frame('chips', await C.evaluate(() => {
      const boxes = [...document.querySelectorAll('.categoryBadge')].map((c) => c.getBoundingClientRect()).filter((r) => r.width);
      const x = Math.min(...boxes.map((r) => r.left)), y = Math.min(...boxes.map((r) => r.top));
      return { x, y, width: Math.max(...boxes.map((r) => r.right)) - x, height: Math.max(...boxes.map((r) => r.bottom)) - y };
    }));
    await take.frame('pdfPage', '[data-page="1"]');
  }
  await sleep(3600);
  if (!INSPECT) { mark('cEnd'); await take.stop(); }
  await C.screenshot({ path: path.join(DIR, INSPECT ? 'inspect-5-gamma.png' : 'connector-final.png') });

  // The toolbar after the save: the APS tab's badge, and the Gamma tab's address.
  const gammaUrl = new URL(C.url());
  const savedBadge = await badgeOf(tabId);
  take.check('savedBadge', savedBadge && savedBadge.text === '✓', savedBadge);
  const bar2 = await context.newPage();
  const shoot2 = async (name, options) => {
    await bar2.setContent(toolbar(options));
    await bar2.screenshot({ path: path.join(BARS, `toolbar-${name}.png`), clip: { x: 0, y: 0, width: VIEW.width, height: TOOLBAR } });
  };
  await shoot2('saved', { ...aps, badge: savedBadge, state: 'open' });
  await shoot2('gamma', { host: gammaUrl.host, rest: gammaUrl.pathname + gammaUrl.search.replace(/[?&]ws=[^&]*/, '').replace(/^&/, '?'),
    badge: await badgeOf(await tabOf(`${gammaUrl.origin}/*`)), state: '' });
  await bar2.close();

  // The saved page, through the API: the PRL paper with its PDF, in the
  // folder, with the label.
  const { children } = await account.api('/api/blocks/root/children');
  const saved = children.find((p) => (p.properties?.meta?.doi || '').toLowerCase() === DOI.toLowerCase() || (p.properties?.source_url || '').includes(DOI));
  const trees = await account.api('/api/library/folders');
  const folderPath = (id) => (trees.folders.find((f) => f.id === id)?.path || []).join('/');
  const labelName = (id) => trees.labels.find((l) => l.id === id)?.name || '';
  const folders = (saved?.properties?.folders || []).map(folderPath);
  const labels = (saved?.properties?.labels || []).map(labelName);
  take.check('saved', !!saved && TITLE.test(saved.content || ''), saved && { id: saved.id, title: saved.content });
  take.check('pdf', !!saved?.properties?.doc_id, saved?.properties?.doc_id);
  take.check('folder', folders.includes(FOLDER), folders);
  take.check('labels', labels.includes(LABEL), labels);
  take.check('opened', saved && gammaUrl.searchParams.toString().includes(saved.id) || (saved && C.url().includes(saved.id)), C.url());
  // After a reload the page still shows its folder and label.
  await C.reload({ waitUntil: 'domcontentloaded' });
  const after = await poll(C, `(() => { const s = (${gammaProbe})(); return s.cPainted && s.cChips; })()`).then(() => true, () => false);
  take.check('reload', after);
  take.check('noChat', !(await C.locator('.chatPanel').count()));
  take.details.removedNotices = removed;
  take.details.aps = routed.filter(([url]) => /aps\.org\/(prl\/(abstract|pdf)|pdf)\//.test(url));

  if (INSPECT) console.log('inspect:', JSON.stringify({ badge, savedBadge, icon, popupAt, checks: take.checks, details: take.details }));
  else await take.finish({ pieces: { page: 'frames', popup: 'popup', gamma: 'gamma' }, toolbar: TOOLBAR, popup: { ...popupAt, width: POPUP_W }, icon, badge, savedBadge, heights });
} catch (error) {
  if (INSPECT && context) for (const [i, p] of context.pages().entries()) {
    await p.screenshot({ path: path.join(DIR, `inspect-failed-${i}.png`) }).catch(() => {});
  }
  await take.fail(error);
} finally {
  sampling = false;
  if (take.capture) await take.stop().catch(() => {});
  if (context) await context.close().catch(() => {});
  if (lib) await lib.server.stop().catch(() => {});
}
