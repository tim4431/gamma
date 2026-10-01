// The website's hero still: the curated atom-arrays paper on page 2 (Fig. 1),
// a sentence highlighted with a comment in its notes, and the paper's AI chat
// under them. The chat is a real exchange: the question and the model's answer
// from the earlier hero (docs/assets/screenshots/01-annotated-pdf.png), saved
// as the paper's conversation; no AI is asked anything here. The model list is
// answered with the demo account's model (as the e2e harness's fakeAiModels
// does), so the chat shows its composer rather than the "connect an AI service"
// card. A 1440 x 900 window at 2x on an isolated server with the curated
// export. Writes the PNG master to the shot's directory and
// docs/assets/screenshots/hero-app.webp (the site's). `--inspect` saves the
// view before the highlight is made.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, RETINA, launchRetina, CURATED } from './runtime.mjs';
import { Server, Account, fakeAiModels } from '../../frontend/tests/e2e/harness.mjs';

const SCRATCH = path.resolve(process.env.MEDIA_SCRATCH || path.join(ROOT, 'artifacts/readme-media/hero'));
const OUT = path.join(ROOT, 'docs/assets/screenshots/hero-app.webp');
const INSPECT = process.argv.includes('--inspect');
const PYTHON = path.join(ROOT, process.platform === 'win32' ? 'backend/venv/Scripts/python.exe' : 'backend/venv/bin/python');
const TITLE = 'A quantum processor based on coherent transport of entangled atom arrays';
const DOC = 'd4d785e2579cf38a50988b25';
const CHAT = [
  { role: 'user', text: 'What is the key idea of this paper in one sentence?', pdfs: [TITLE], pdfDocs: [DOC] },
  { role: 'ai', text: "The paper's key idea is to use optical tweezers to coherently shuttle entangled neutral-atom arrays in two dimensions, enabling programmable nonlocal connectivity for quantum error correction, simulation, and scalable quantum processing." },
];
const MODELS = { enabled: true, default: 'openai:gpt-5.6-luna', efforts: ['low', 'medium', 'high'],
  models: [{ id: 'openai:gpt-5.6-luna', provider: 'openai', provider_name: 'OpenAI', model: 'gpt-5.6-luna' }] };
const HIGHLIGHT = 'transport qubits across large distances';
const COMMENT = 'Moved 110 µm, and the Bell-state fidelity holds (Fig. 1d).';
fs.mkdirSync(SCRATCH, { recursive: true });

const server = new Server();
let browser;
try {
  await server.start();
  server.manage('create-user', 'demo', 'isolated-media-only');
  const account = await new Account(server, 'demo', 'isolated-media-only').login();
  await account.upload('/api/import-data', fs.readFileSync(path.join(ROOT, 'artifacts/readme-media/demo.zip')), 'demo.zip', 'application/zip');
  await account.api(`/api/blocks/${CURATED.atoms}`, { method: 'PUT', body: { properties: { folder: 'Quantum/Neutral atoms', category: 'quantum computing, neutral atoms' } } });
  await account.api(`/api/chats/${CURATED.atoms}`, { method: 'PUT', body: { messages: CHAT } });

  browser = await launchRetina();
  const ctx = await account.context(browser, RETINA);
  await fakeAiModels(ctx, MODELS);
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('Page error:', e.message));
  // The other two papers open first, so the tab strip shows a reading session.
  for (const id of ['p8oNV3s3XNhC', CURATED.qec, CURATED.atoms]) {
    await page.goto(`${server.base}/?page=${id}&ws=${account.ws}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[data-page="1"] .textLayer span', { timeout: 60000 });
  }
  if (!await page.locator('.chatInput').isVisible().catch(() => false)) {
    await page.keyboard.press('Control+Shift+p');
    await page.keyboard.type('chat');
    await page.getByRole('dialog', { name: 'Command palette' }).locator('[role="option"][aria-selected="true"]', { hasText: 'chat' }).waitFor();
    await page.keyboard.press('Enter');
  }
  await page.locator('.chatBubbleRow.ai', { hasText: 'optical tweezers' }).waitFor({ timeout: 20000 });
  // Pages are lazy: bring page 2 in, then frame Fig. 1 over the sentence.
  await page.evaluate(() => document.querySelector('[data-page="2"]').scrollIntoView({ block: 'start' }));
  const sentence = page.locator('[data-page="2"] .textLayer span').filter({ hasText: HIGHLIGHT }).first();
  await sentence.waitFor({ timeout: 30000 });
  const phraseBox = () => sentence.evaluate((el, phrase) => {
    const text = el.firstChild, i = text.textContent.indexOf(phrase);
    const r = document.createRange();
    r.setStart(text, i); r.setEnd(text, i + phrase.length);
    const b = r.getBoundingClientRect();
    return { x: b.x, y: b.y, width: b.width, height: b.height };
  }, HIGHLIGHT);
  // The sentence 80 px above the window's foot: the figure fills the view above it.
  const first = await phraseBox();
  await page.mouse.move(600, 450);
  await page.mouse.wheel(0, first.y - (900 - 80));
  await page.waitForTimeout(2000);

  if (INSPECT) {
    await page.screenshot({ path: path.join(SCRATCH, 'inspect.png') });
    console.log('Saved', path.join(SCRATCH, 'inspect.png'));
  } else {
    // Highlight the sentence (a double-press drag selects whole words) and comment on it.
    const b = await phraseBox();
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
    // Nothing focused or hovered: no editor chrome, no cursor chip in the chat.
    await page.evaluate(() => document.activeElement?.blur());
    await page.click('.chatSelChip.isCursor .chatSelChipClose', { timeout: 2000 }).catch(() => {});
    await page.mouse.move(1435, 895);
    await page.waitForTimeout(2000);
    const notes = (await account.api(`/api/blocks/${CURATED.atoms}/subtree`)).block.children || [];
    if (!notes.some(x => x.properties?.quote?.includes(HIGHLIGHT) && x.content.includes('Bell-state fidelity holds'))) throw new Error('The highlight and its comment were not saved');
    const png = path.join(SCRATCH, 'hero-app.png');
    await page.screenshot({ path: png });
    // Lossy WebP for the page: a fraction of the PNG, the text still crisp at 2x.
    execFileSync(PYTHON, ['-c', `from PIL import Image; Image.open(r"${png}").convert("RGB").save(r"${OUT}", "WEBP", quality=88, method=6)`]);
    console.log('Saved', OUT, (fs.statSync(OUT).size / 1048576).toFixed(2), 'MiB');
  }
} finally {
  if (browser) await browser.close();
  await server.stop();
}
