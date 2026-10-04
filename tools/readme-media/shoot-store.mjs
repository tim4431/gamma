// The Chrome Web Store screenshots for the Gamma Connector listing
// (extension/STORE.md): Gamma itself, where the Connector's saves land.
// 1 the atom-arrays paper on Fig. 1 with a sentence highlighted, its comment
// in the notes and the paper's chat; 2 the library home (folders, recents,
// listing); 3 the home search with grouped library results. The store takes
// 1280 x 800, JPEG or 24-bit PNG without alpha: a 1280 x 800 window at 1x,
// saved as RGB PNGs in extension/store/. An isolated server with the curated
// export; the chat is the saved exchange from shoot-hero.mjs, so no AI is
// asked anything. `--inspect` saves the three views to the shot's directory
// before the highlight is made, without writing extension/store/.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, chromium, CURATED, openChat, phraseBox } from './runtime.mjs';
import { Server, Account, fakeAiModels } from '../../frontend/tests/e2e/harness.mjs';

const SCRATCH = path.resolve(process.env.MEDIA_SCRATCH || path.join(ROOT, 'artifacts/readme-media/store'));
const OUT = path.join(ROOT, 'extension/store');
const INSPECT = process.argv.includes('--inspect');
const PYTHON = path.join(ROOT, process.platform === 'win32' ? 'backend/venv/Scripts/python.exe' : 'backend/venv/bin/python');
const VIEW = { width: 1280, height: 800 };
const TITLE = 'A quantum processor based on coherent transport of entangled atom arrays';
const DOC = 'd4d785e2579cf38a50988b25';
const CHAT = [
  { role: 'user', text: 'What is the key idea of this paper in one sentence?', pdfs: [TITLE], pdfDocs: [DOC] },
  { role: 'ai', text: "The paper's key idea is to use optical tweezers to coherently shuttle entangled neutral-atom arrays in two dimensions, enabling programmable nonlocal connectivity for quantum error correction, simulation, and scalable quantum processing." },
];
const MODELS = { enabled: true, default: 'openai:gpt-5.6-luna', efforts: ['low', 'medium', 'high'],
  models: [{ id: 'openai:gpt-5.6-luna', provider: 'openai', provider_name: 'OpenAI', model: 'gpt-5.6-luna' }] };
const ATTENTION = 'p8oNV3s3XNhC';
const HIGHLIGHT = 'transport qubits across large distances';
const COMMENT = 'Moved 110 µm, and the Bell-state fidelity holds (Fig. 1d).';
// Hits in both quantum papers. No label or folder name may contain the query,
// or the filter suggestions cover the results.
const QUERY = process.env.QUERY || 'Steane';
fs.mkdirSync(SCRATCH, { recursive: true });

const server = new Server();
let browser;
try {
  await server.start();
  server.manage('create-user', 'demo', 'isolated-media-only');
  const account = await new Account(server, 'demo', 'isolated-media-only').login();
  await account.upload('/api/import-data', fs.readFileSync(path.join(ROOT, 'artifacts/readme-media/demo.zip')), 'demo.zip', 'application/zip');
  // Filed as the Connector files a save: a folder and labels (made where missing).
  await account.file(CURATED.atoms, { folders: ['Quantum/Neutral atoms'], labels: ['quantum computing', 'neutral atoms'] });
  await account.file(CURATED.qec, { folders: ['Quantum/Error correction'], labels: ['quantum computing', 'error correction'] });
  await account.file(ATTENTION, { folders: ['Machine learning'], labels: ['machine learning'] });
  await account.api(`/api/chats/${CURATED.atoms}`, { method: 'PUT', body: { messages: CHAT } });
  // A PDF search schedules the text extraction; wait until every PDF is indexed.
  for (let i = 0; ; i++) {
    if (!(await account.api('/api/pdf-search?q=quantum&limit=1')).indexing) break;
    if (i === 120) throw new Error('The PDFs were not indexed within two minutes');
    await new Promise(r => setTimeout(r, 1000));
  }

  browser = await chromium.launch({ headless: true });
  const ctx = await account.context(browser, { viewport: VIEW, deviceScaleFactor: 1, colorScheme: 'light', locale: 'en-US' });
  await fakeAiModels(ctx, MODELS);
  // The home search shows each result's details, as in the docs stills.
  await ctx.addInitScript(() => {
    try { localStorage.setItem('gamma-search-details-home', '1'); } catch {}
  });
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('Page error:', e.message));
  const shots = [];
  const shoot = async (name) => {
    const png = path.join(SCRATCH, `${name}.png`);
    await page.screenshot({ path: png });
    shots.push([png, name]);
    console.log('Shot', name);
  };

  // 1 — the paper. The other two open first, so the tabs show a reading session.
  for (const id of [ATTENTION, CURATED.qec, CURATED.atoms]) {
    await page.goto(`${server.base}/?page=${id}&ws=${account.ws}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-page="1"] .textLayer span', { timeout: 60000 });
  }
  await openChat(page);
  await page.locator('.chatBubbleRow.ai', { hasText: 'optical tweezers' }).waitFor({ timeout: 20000 });
  // Pages are lazy: bring page 2 in, then frame Fig. 1 over the sentence.
  await page.evaluate(() => document.querySelector('[data-page="2"]').scrollIntoView({ block: 'start' }));
  const sentence = page.locator('[data-page="2"] .textLayer span').filter({ hasText: HIGHLIGHT }).first();
  await sentence.waitFor({ timeout: 30000 });
  const first = await phraseBox(sentence, HIGHLIGHT);
  await page.mouse.move(500, 400);
  await page.mouse.wheel(0, first.y - (VIEW.height - 70));
  await page.waitForTimeout(2000);
  if (!INSPECT) {
    // Highlight the sentence (a double-press drag selects whole words) and comment on it.
    const b = await phraseBox(sentence, HIGHLIGHT);
    await page.mouse.move(b.x + 6, b.y + b.height / 2);
    await page.mouse.down({ clickCount: 2 });
    await page.mouse.move(b.x + b.width - 6, b.y + b.height / 2, { steps: 12 });
    await page.mouse.up();
    const selected = (await page.evaluate(() => window.getSelection()?.toString()))?.trim();
    if (selected !== HIGHLIGHT) throw new Error(`The drag selected "${selected}"`);
    await page.locator('.plainTip .colorBtn').first().click();
    await page.locator('.blockEditorCm .cm-content').waitFor();
    await page.keyboard.type(COMMENT, { delay: 10 });
    await page.keyboard.press('Escape');
    // The comment is saved shortly after the editor closes.
    for (let i = 0; ; i++) {
      const notes = (await account.api(`/api/blocks/${CURATED.atoms}/subtree`)).block.children || [];
      if (notes.some(x => x.properties?.quote?.includes(HIGHLIGHT) && x.content.includes('Bell-state fidelity holds'))) break;
      if (i === 20) throw new Error('The highlight and its comment were not saved');
      await page.waitForTimeout(500);
    }
  }
  // Nothing focused or hovered: no editor chrome, no cursor chip in the chat.
  await page.evaluate(() => document.activeElement?.blur());
  await page.click('.chatSelChip.isCursor .chatSelChipClose', { timeout: 2000 }).catch(() => {});
  // The conversation from its first line: scroll the chat's list to the top.
  await page.evaluate(() => {
    let el = document.querySelector('.chatBubbleRow');
    while (el && !(el.scrollHeight > el.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(el).overflowY))) el = el.parentElement;
    if (el) el.scrollTop = 0;
  });
  await page.mouse.move(VIEW.width - 4, VIEW.height - 4);
  await page.waitForTimeout(2000);
  if (INSPECT) console.log('At (889, 771):', await page.evaluate(() => {
    const el = document.elementFromPoint(889, 771);
    return el && (el.closest('[class]')?.outerHTML || '').slice(0, 300);
  }));
  await shoot('screenshot-1-paper');

  // 2 — the library home (a bare `/` would restore the open paper), without
  // the home chat, so the listing has the width.
  await page.click('[aria-label="Home"]');
  await page.waitForSelector('.recentsCarousel', { timeout: 30000 });
  await page.getByRole('button', { name: 'Close Chat' }).click({ timeout: 5000 }).catch(() => {});
  await page.mouse.move(VIEW.width - 4, VIEW.height - 4);
  await page.waitForTimeout(2500);
  await shoot('screenshot-2-library');

  // 3 — the home search, its results grouped by kind.
  await page.click('[aria-label="Search"]');
  await page.waitForSelector('.searchPopover .searchInput', { timeout: 5000 });
  await page.keyboard.press('Control+a');
  await page.keyboard.type(QUERY, { delay: 30 });
  await page.waitForSelector('.searchPopover .searchSection:has-text("Library PDFs")', { timeout: 15000 });
  await page.waitForFunction(() => ![...document.querySelectorAll('.searchPopover .searchHint')].some(h => h.textContent.startsWith('Searching')), null, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1000);
  await shoot('screenshot-3-search');

  if (!INSPECT) {
    // The store refuses an alpha channel: re-save as plain RGB.
    fs.mkdirSync(OUT, { recursive: true });
    for (const [png, name] of shots) {
      const dest = path.join(OUT, `${name}.png`);
      execFileSync(PYTHON, ['-c', `from PIL import Image; Image.open(r"${png}").convert("RGB").save(r"${dest}", optimize=True)`]);
      console.log('Saved', path.relative(ROOT, dest));
    }
  }
} finally {
  if (browser) await browser.close();
  await server.stop();
}
