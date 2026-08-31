// Records docs/demo-connector.gif sources: arXiv abs page (segment A),
// the extension popup driven as a page (segment B, composited as an overlay),
// and the saved paper opening in Gamma (segment C).
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const EXT = 'D:/Codes/Github/gamma/extension';
const SERVER = 'http://127.0.0.1:9002';
const ARXIV = 'https://arxiv.org/abs/2312.03982';
const CURSOR = () => addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;z-index:99999;width:14px;height:14px;'
    + 'border-radius:50%;background:rgba(0,0,0,.45);border:2px solid #fff;'
    + 'pointer-events:none;margin:-8px 0 0 -8px;transition:transform .05s';
  document.body.appendChild(c);
  addEventListener('mousemove', e => c.style.transform =
    `translate(${e.clientX}px,${e.clientY}px)`, true);
});

const userDir = path.resolve('chrome-profile');
fs.rmSync(userDir, { recursive: true, force: true });
const context = await chromium.launchPersistentContext(userDir, {
  headless: false,
  args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`, '--headless=new'],
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: 'light',
  slowMo: 60,
  recordVideo: { dir: 'video-conn', size: { width: 1440, height: 900 } },
});
await context.addInitScript(CURSOR);
let sw = context.serviceWorkers()[0];
if (!sw) sw = await context.waitForEvent('serviceworker');
await sw.evaluate((server) => chrome.storage.sync.set({ server }), SERVER);
const extId = new URL(sw.url()).host;

// --- pre-step (not part of the GIF): sign in through the popup ---
const p0 = await context.newPage();
await p0.goto(`chrome-extension://${extId}/popup.html`);
await p0.waitForSelector('#view-login:not(.hidden)', { timeout: 15000 });
await p0.fill('#login-user', 'demo');
await p0.fill('#login-pass', 'demopw');
await p0.click('#login-btn');
await p0.waitForSelector('#view-main:not(.hidden)', { timeout: 15000 });
await p0.close();

// --- segment A: the arXiv page ---
const marks = {};
const pageA = await context.newPage();
marks.tA = Date.now();
await pageA.goto(ARXIV, { waitUntil: 'domcontentloaded', timeout: 60000 });
await pageA.waitForTimeout(2500);
// detection: poll the badge for this tab
const tabId = await sw.evaluate(async () => {
  const tabs = await chrome.tabs.query({ url: '*://arxiv.org/*' });
  return tabs[0].id;
});
for (let i = 0; i < 30; i++) {
  const badge = await sw.evaluate((id) => chrome.action.getBadgeText({ tabId: id }), tabId);
  if (badge) { marks.badge = badge; break; }
  await pageA.waitForTimeout(500);
}
marks.a0 = (Date.now() - marks.tA) / 1000;
// no scroll — keep the title in frame; the camera zoom is added in post
await pageA.mouse.move(640, 320, { steps: 25 });
await pageA.waitForTimeout(1400);
// drift toward the toolbar corner as if clicking the extension icon
await pageA.mouse.move(1380, 40, { steps: 45 });
await pageA.waitForTimeout(700);
marks.a1 = (Date.now() - marks.tA) / 1000;

// --- segment B: the popup, opened as a page (?tab= hook) ---
const pageB = await context.newPage();
marks.tB = Date.now();
await pageB.goto(`chrome-extension://${extId}/popup.html?tab=${tabId}`);
await pageB.waitForSelector('#view-main:not(.hidden)', { timeout: 15000 });
await pageB.waitForSelector('#found:not(.hidden)', { timeout: 15000 });
marks.popupH = await pageB.evaluate(() => document.body.scrollHeight);
marks.title = await pageB.evaluate(() => document.getElementById('title').textContent);
await pageB.waitForTimeout(1400);
marks.b0 = (Date.now() - marks.tB) / 1000;
// pick the folder the agent created
const fol = await pageB.locator('#folder').boundingBox();
await pageB.mouse.move(fol.x + fol.width / 2, fol.y + fol.height / 2, { steps: 25 });
await pageB.waitForTimeout(400);
await pageB.selectOption('#folder', 'quantum computing');
await pageB.waitForTimeout(900);
const sv = await pageB.locator('#save').boundingBox();
await pageB.mouse.move(sv.x + sv.width / 2, sv.y + sv.height / 2, { steps: 25 });
await pageB.mouse.down(); await pageB.mouse.up();
await pageB.waitForSelector('#result.ok:not(.hidden), #result.msg.ok:not(.hidden)', { timeout: 120000 });
await pageB.waitForTimeout(1600);
const link = await pageB.locator('#result a').boundingBox();
const openPromise = context.waitForEvent('page', { timeout: 30000 });
await pageB.mouse.move(link.x + link.width / 2, link.y + link.height / 2, { steps: 25 });
await pageB.waitForTimeout(400);
marks.b1 = (Date.now() - marks.tB) / 1000;
await pageB.mouse.down(); await pageB.mouse.up();
marks.b2 = (Date.now() - marks.tB) / 1000;

// --- segment C: the paper opens in Gamma ---
const pageC = await openPromise;
marks.tC = Date.now();
await pageC.waitForLoadState('domcontentloaded');
await pageC.waitForSelector('[data-page="1"] .textLayer span', { timeout: 90000 });
await pageC.waitForTimeout(3500);
await pageC.mouse.move(700, 450, { steps: 30 });
await pageC.waitForTimeout(1200);
marks.c1 = (Date.now() - marks.tC) / 1000;
await pageC.screenshot({ path: 'conn-final.png' });

const vA = pageA.video(), vB = pageB.video(), vC = pageC.video();
await context.close();
marks.videoA = await vA.path();
marks.videoB = await vB.path();
marks.videoC = await vC.path();
fs.writeFileSync('conn_marks.json', JSON.stringify(marks, null, 1));
console.log(JSON.stringify(marks, null, 1));
