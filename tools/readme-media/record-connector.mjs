// README "Connect your research" demo: the Gamma Connector saves a Physical
// Review Letters paper from its APS page into a folder, with a label, and the
// paper opens in Gamma.
//
// Prepared by `run-case.mjs connector` (an isolated server with registry
// lookups on, the curated export, a session in session.txt). Full Chromium at
// 2× with the unpacked extension; its window is 40 px shorter than the frame,
// and the render puts a browser toolbar there: these toolbar states are
// screenshots of toolbar.html below, with the extension's real badge. Three
// captures: the APS page (a/), the popup opened as a page through its
// `?tab=` hook (b/, composited under the toolbar icon) and the paper in
// Gamma (c/). Marks, the popup's height over time and the icon's place go
// to connector_marks.json. `--inspect` saves the APS page and the popup.
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, VIEW, launchRetinaExtension, startCapture, configureContext, addCursor, pointer, readSession, BASE } from './runtime.mjs';

const SCRATCH = process.cwd();
const INSPECT = process.argv.includes('--inspect');
const EXT = path.join(ROOT, 'extension');
const DOI = '10.1103/PhysRevLett.123.170503';
const ARTICLE = `https://journals.aps.org/prl/abstract/${DOI}`;
const FOLDER = /Neutral atoms$/;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';
const LABEL = 'rydberg gates';
const TOOLBAR = 40, PAGE_H = VIEW.height - TOOLBAR;
const SESSION = readSession(SCRATCH);

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
  .cursor { position: absolute; left: 11px; top: 9px; margin: -2.5px 0 0 -4px; filter: drop-shadow(0 1.5px 2px rgba(0,0,0,.35)); }
  </style><div class="bar">
    <span class="nav">${glyph('<path d="M19 12H5M12 19l-7-7 7-7"/>')}</span>
    <span class="nav">${glyph('<path d="M5 12h14M12 5l7 7-7 7"/>')}</span>
    <span class="nav">${glyph('<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>')}</span>
    <div class="url">${glyph('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>')}<span>${host}<span class="rest">${rest}</span></span></div>
    <span class="ext ${state}"><img src="${ICON(badge ? 'icon32.png' : 'icon32-off.png')}">${badge ? `<span class="badge" style="background:${badge.color}">${badge.text}</span>` : ''}${state === 'hover' ? `<span class="cursor">${ARROW}</span>` : ''}</span>
    <span class="nav">${glyph('<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>')}</span>
  </div>`;
}

const context = await launchRetinaExtension(EXT, { height: PAGE_H });
const marks = {}, heights = [];
let capture;
try {
  await configureContext(context);
  await addCursor(context);
  await context.addCookies([{ name: 'session', value: SESSION, url: BASE }]);
  let sw = context.serviceWorkers()[0];
  if (!sw) sw = await context.waitForEvent('serviceworker');
  await sw.evaluate((server) => chrome.storage.sync.set({ server }), BASE);
  const extId = new URL(sw.url()).host;
  const popupUrl = (tab) => `chrome-extension://${extId}/popup.html${tab ? `?tab=${tab}` : ''}`;
  const signIn = await context.newPage();
  await signIn.goto(popupUrl());
  await signIn.waitForSelector('#view-main:not(.hidden)', { timeout: 15000 });
  await signIn.close();
  const badgeOf = async (tabId) => {
    const [text, rgba] = await sw.evaluate(async (id) => [await chrome.action.getBadgeText({ tabId: id }), await chrome.action.getBadgeBackgroundColor({ tabId: id })], tabId);
    return text ? { text, color: `rgb(${rgba.slice(0, 3).join(',')})` } : null;
  };
  const tabOf = (pattern) => sw.evaluate(async (p) => (await chrome.tabs.query({ url: p }))[0]?.id, pattern);

  // --- a: the APS page, detected ------------------------------------------
  // APS answers a headless browser with a Cloudflare check, but serves curl
  // the page itself: its requests are fetched with curl and handed to the
  // browser at their own address, so the extension reads the real page. The
  // consent and analytics scripts are left out (a banner would cover it).
  await context.route(/^https:\/\/(cmp\.osano\.com|www\.googletagmanager\.com|scholar\.google\.com)\//, route => route.abort());
  let fetched = 0;
  await context.route(/^https:\/\/journals\.aps\.org\//, async route => {
    const file = path.join(SCRATCH, `aps-${++fetched}.tmp`);
    const type = await new Promise((resolve, reject) => execFile('curl', ['-sL', '-A', UA, '-o', file, '-w', '%{http_code} %{content_type}', route.request().url()],
      (error, out) => error ? reject(error) : resolve(out)));
    const [status, ...contentType] = type.split(' ');
    const body = fs.readFileSync(file);
    fs.rmSync(file);
    await route.fulfill({ status: Number(status), contentType: contentType.join(' ') || undefined, body });
  });
  const A = context.pages()[0] || await context.newPage();
  await A.goto(ARTICLE, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await A.waitForLoadState('load', { timeout: 60000 }).catch(() => {});
  // The page names the institution whose network fetched it: leave that out.
  await A.evaluate(() => {
    // The notice with the name in it ("Access Provided by <b>…</b>"), not just its first words.
    const notice = (el) => /^\s*access (provided )?by\s+\S/i.test(el.textContent) && el.textContent.length < 120;
    const hits = [...document.querySelectorAll('body *')].filter(el => notice(el) && ![...el.children].some(notice));
    for (const el of hits) {
      const box = el.parentElement;
      el.remove();
      if (box && !box.textContent.trim()) box.remove();
    }
  });
  await A.waitForTimeout(2500);
  const tabId = await tabOf('*://journals.aps.org/*');
  let badge = null;
  for (let i = 0; i < 40 && !(badge = await badgeOf(tabId)); i++) await A.waitForTimeout(500);
  if (!badge) throw new Error('The Connector did not detect the APS paper');
  marks.badge = badge;

  // The toolbar's states, and where its icon is.
  const bar = await context.newPage();
  const shoot = async (name, options) => {
    await bar.setContent(toolbar(options));
    await bar.screenshot({ path: path.join(SCRATCH, `toolbar-${name}.png`), clip: { x: 0, y: 0, width: VIEW.width, height: TOOLBAR } });
  };
  const aps = { host: 'journals.aps.org', rest: `/prl/abstract/${DOI}`, badge };
  await shoot('page', { ...aps, state: '' });
  await shoot('hover', { ...aps, state: 'hover' });
  await shoot('open', { ...aps, state: 'open' });
  const icon = await bar.locator('.ext').boundingBox();
  marks.icon = icon;

  if (INSPECT) {
    await A.screenshot({ path: path.join(SCRATCH, 'inspect-aps.png') });
    const P = await context.newPage();
    await P.goto(popupUrl(tabId));
    await P.waitForSelector('#found:not(.hidden)', { timeout: 30000 });
    await P.waitForTimeout(3000);
    await P.click('#folder-btn');
    console.log('folders:', await P.locator('#folder-menu .ctxMenuItem').allInnerTexts());
    await P.screenshot({ path: path.join(SCRATCH, 'inspect-popup.png') });
    console.log('badge', badge, 'icon', icon);
  } else {
    await bar.close();
    await A.bringToFront();
    capture = await startCapture(A, path.join(SCRATCH, 'a'), { height: PAGE_H });
    const clockA = capture.clock;
    const { glide } = pointer(A, 760, 560);
    await A.mouse.move(760, 560);
    await A.waitForTimeout(900);
    marks.a0 = clockA();
    // Read the title, then up to the toolbar's icon: the page's last pixel row
    // under it; the render takes the pointer from there into the toolbar.
    const title = await A.locator('h3, h1').filter({ hasText: /Parallel Implementation/i }).first().boundingBox().catch(() => null);
    if (title) await glide(title.x + Math.min(title.width, 600) * 0.55, title.y + title.height / 2, 30);
    await A.waitForTimeout(1300);
    await glide(icon.x + icon.width / 2, 1, 36);
    marks.a1 = clockA();
    await A.evaluate(() => { const c = document.getElementById('__fakecur'); if (c) c.style.opacity = '0'; });
    await A.waitForTimeout(400);
    marks.aHidden = clockA();
    await capture.stop(); capture = null;

    // --- b: the popup --------------------------------------------------------
    const B = await context.newPage();
    const startB = Date.now();
    capture = await startCapture(B, path.join(SCRATCH, 'b'), { height: PAGE_H });
    const clockB = capture.clock;
    let sampling = true;
    const sampler = (async () => {
      while (sampling) {
        // The popup's height: its content, and an open menu hanging below it.
        const h = await B.evaluate(() => !document.body ? 0 : Math.ceil(Math.max(document.body.getBoundingClientRect().bottom,
          ...[...document.querySelectorAll('.ctxMenu:not(.hidden)')].map(m => m.getBoundingClientRect().bottom + 8)))).catch(() => 0);
        heights.push([clockB(), h]);
        await new Promise(r => setTimeout(r, 60));
      }
    })();
    await B.goto(popupUrl(tabId));
    marks.b0 = clockB();
    await B.waitForSelector('#found:not(.hidden)', { timeout: 30000 });
    await B.waitForFunction(() => /Parallel Implementation/i.test(document.getElementById('title')?.textContent || ''), null, { timeout: 30000 });
    marks.bReady = clockB();
    marks.title = await B.locator('#title').innerText();
    const b = pointer(B, 300, 30);
    await B.mouse.move(300, 30);
    await B.waitForTimeout(1200);
    // A folder, a label, Save.
    await b.glideTo('#folder-btn', 0.5, 0.5, 25);
    await B.mouse.down(); await B.mouse.up();
    const item = B.locator('#folder-menu .ctxMenuItem').filter({ hasText: FOLDER }).first();
    await item.waitFor();
    await B.waitForTimeout(500);
    await b.glideTo(item, 0.4, 0.5, 20);
    await B.mouse.down(); await B.mouse.up();
    await B.waitForTimeout(600);
    await b.glideTo('#labels', 0.3, 0.5, 20);
    await B.mouse.down(); await B.mouse.up();
    await B.keyboard.type(LABEL, { delay: 70 });
    await B.keyboard.type(',', { delay: 70 });
    await B.waitForTimeout(500);
    // The label suggestions open over the Save button.
    await B.keyboard.press('Escape');
    await B.locator('#label-menu').waitFor({ state: 'hidden' });
    await B.waitForTimeout(400);
    await b.glideTo('#save', 0.5, 0.5, 25);
    marks.bSave = clockB();
    await B.mouse.down(); await B.mouse.up();
    await B.waitForSelector('#result:not(.hidden) a', { timeout: 120000 });
    marks.bSaved = clockB();
    marks.result = await B.locator('#result').innerText();
    await B.waitForTimeout(1300);
    const opened = context.waitForEvent('page', { timeout: 30000 });
    // The link can wrap: aim at its first line, not the middle of its box.
    const link = await B.locator('#result a').evaluate(a => { const r = a.getClientRects()[0]; return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
    await b.glide(link.x, link.y, 22);
    await B.waitForTimeout(250);
    // The click closes the popup (and a screencast of a closed page never
    // stops), so its capture ends with the pointer on the link.
    marks.b1 = clockB();
    sampling = false; await sampler;
    await capture.stop(); capture = null;
    await B.mouse.down(); await B.mouse.up();

    // --- c: the paper in Gamma -----------------------------------------------
    const C = await opened;
    await C.bringToFront();
    capture = await startCapture(C, path.join(SCRATCH, 'c'), { height: PAGE_H });
    const clockC = capture.clock;
    await C.waitForLoadState('domcontentloaded');
    marks.c0 = clockC();
    await C.waitForSelector('[data-page="1"] .textLayer span', { timeout: 90000 });
    const closeChat = C.getByRole('button', { name: 'Close Chat', exact: true });
    if (await closeChat.isVisible().catch(() => false)) await closeChat.click();
    marks.cReady = clockC();
    await C.waitForTimeout(3200);
    marks.c1 = clockC();
    await capture.stop(); capture = null;
    const gammaUrl = new URL(C.url());
    const gammaTab = await tabOf(`${gammaUrl.origin}/*`);
    const page = await context.newPage();
    // The address shows the default local server rather than the throwaway port.
    await page.setContent(toolbar({ host: 'localhost:9001', rest: gammaUrl.pathname + gammaUrl.search.replace(/[?&]ws=[^&]*/, ''), badge: await badgeOf(gammaTab), state: '' }));
    await page.screenshot({ path: path.join(SCRATCH, 'toolbar-gamma.png'), clip: { x: 0, y: 0, width: VIEW.width, height: TOOLBAR } });
    await C.screenshot({ path: path.join(SCRATCH, 'connector-final.png') });

    // The saved page: the PRL paper with its PDF, in the folder, with the label.
    const headers = { Cookie: `session=${SESSION}`, ...(process.env.MEDIA_WORKSPACE ? { 'X-Gamma-Workspace': process.env.MEDIA_WORKSPACE } : {}) };
    const { children } = await (await fetch(`${BASE}/api/blocks/root/children`, { headers })).json();
    const saved = children.find(p => (p.properties?.meta?.doi || '').toLowerCase() === DOI.toLowerCase() || (p.properties?.source_url || '').includes(DOI));
    // The page is filed by folder and label ids; the library's trees name them.
    const trees = await (await fetch(`${BASE}/api/library/folders`, { headers })).json();
    const folderPath = id => (trees.folders.find(f => f.id === id)?.path || []).join('/');
    const labelName = id => trees.labels.find(l => l.id === id)?.name || '';
    const verified = {
      saved: !!saved, pdf: !!saved?.properties?.doc_id,
      folder: (saved?.properties?.folders || []).map(folderPath).join(', '),
      labels: (saved?.properties?.labels || []).map(labelName).join(', '),
      title: saved?.content || '', elapsed: (Date.now() - startB) / 1000,
    };
    if (!verified.saved || !verified.pdf || !verified.folder.includes('Neutral atoms') || !verified.labels.includes(LABEL)) {
      throw new Error(`The paper was not saved as expected: ${JSON.stringify({ verified, props: saved?.properties })}`);
    }
    fs.writeFileSync(path.join(SCRATCH, 'connector_marks.json'), JSON.stringify({ marks, heights, verified, toolbar: TOOLBAR }, null, 1));
    console.log('Recorded:', JSON.stringify(verified));
  }
} finally {
  if (capture) await capture.stop().catch(() => {});
  await context.close();
}
