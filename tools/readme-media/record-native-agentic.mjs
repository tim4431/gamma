// One PDF conversation: Ctrl+drag a figure into the chat and ask about it,
// follow the answer's passage citation, then the agent finds follow-up papers
// online and asks before saving them.
// Requires a disposable curated workspace with AI on its owning account.
import fs from 'node:fs';
import path from 'node:path';
import { ROOT, RETINA, launchRetina, startCapture, configureContext, addCursor } from './runtime.mjs';
import { Account } from '../../frontend/tests/e2e/harness.mjs';
import { getDocument } from '../../frontend/node_modules/pdfjs-dist/legacy/build/pdf.mjs';
import { citationRuns, matchCitation } from '../../frontend/src/pdf/pdfCitation.js';
import { parseGammaLink } from '../../frontend/src/shared/model/gammaLinks.js';

const scratch = path.resolve(process.env.MEDIA_SCRATCH || path.join(ROOT, 'artifacts/readme-media/revised'));
const state = JSON.parse(fs.readFileSync(path.join(scratch, 'workspace.json')));
if (state.removed) throw new Error('Recording workspace was removed');
const account = new Account({ base: state.base }, state.username, '');
account.session = fs.readFileSync(path.join(scratch, 'session.txt'), 'utf8').trim();
account.ws = state.workspace;
const { children: pages } = await account.api('/api/blocks/root/children');
const paper = pages.find(p => p.content?.includes('coherent transport'));
if (!paper) throw new Error('Curated atom-arrays paper missing');
const pdfResponse = await account.api(paper.properties.source_url, {raw:true});
if (!pdfResponse.ok) throw new Error('Cannot load the curated PDF for citation verification');
const pdf = await getDocument({data:new Uint8Array(await pdfResponse.arrayBuffer())}).promise;
await account.api(`/api/chats/${paper.id}`, { method: 'DELETE' });
await account.api('/api/prefs/open-tabs', { method: 'PUT', body: { value: [] } });
const browser = await launchRetina();
let context, page, capture;
const marks = {}, actions = [], verified = {}, framing = {}, requests = [];
try {
  context = await account.context(browser, RETINA);
  await configureContext(context);
  // Serve the actual private build while all API/PDF requests reach Gamma.
  if (process.env.GAMMA_MEDIA_DIST) {
    const dist = path.resolve(process.env.GAMMA_MEDIA_DIST);
    await context.route(`${state.base}/**`, async route => {
      const u = new URL(route.request().url());
      if (u.pathname === '/') return route.fulfill({ path: path.join(dist, 'index.html'), contentType: 'text/html' });
      if (u.pathname.startsWith('/assets/')) {
        const file = path.resolve(dist, '.' + u.pathname);
        if (!file.startsWith(dist + path.sep)) throw new Error('Invalid asset path');
        return route.fulfill({ path: file });
      }
      return route.continue();
    });
  }
  await context.addInitScript(() => localStorage.setItem('gamma-theme', 'light'));
  await addCursor(context);
  page = await context.newPage();
  page.on('request', r => {
    if (new URL(r.url()).pathname === '/api/ai/chat' && r.method() === 'POST') {
      const body = r.postDataJSON();
      requests.push({ pageId: body.page_id, images: body.images?.length || 0 });
    }
  });
  page.on('pageerror', e => console.log('Page error:', e.message));
  page.on('response', r => { if(r.url().includes('/api/') && r.status()>=400) console.log('API error:',r.status(),new URL(r.url()).pathname); });
  page.on('response', async r => { if(r.url().includes('/api/blocks/root/children')) { const j=await r.json().catch(()=>({})); console.log('Library loaded:',j.children?.length); } });
  let clock = () => 0;
  const mark = name => { marks[name] = clock(); console.log(name, marks[name]); };
  const hold = ms => page.waitForTimeout(ms);
  async function click(locator) {
    await locator.scrollIntoViewIfNeeded();
    const b = await locator.boundingBox();
    await page.mouse.move(b.x+b.width/2,b.y+b.height/2,{steps:25});
    await locator.click();
  }
  async function answer(phase, expectedReplies) {
    let previous = 0;
    for (let i=0;i<480;i++) {
      await hold(500);
      if (await page.locator('.chatErrorCard').count()) throw new Error('AI request failed: ' + await page.locator('.chatErrorCard').first().innerText());
      const count = await page.locator('.chatToolAction').count();
      if (count !== previous) {
        actions.push({phase,at:clock(),count,text:await page.locator('.chatToolActionHead').allTextContents()});
        previous=count;
      }
      if (await page.locator('.chatBubbleRow.ai').count() >= expectedReplies && !await page.locator('.chatStopBtn').count() && !await page.locator('.chatTyping').count()) {
        if ((await page.locator('.chatBubbleRow.ai').last().innerText()).length>60) break;
      }
      if(i===479) throw new Error('AI did not finish');
    }
    mark(`${phase}Answer`);
    await hold(2200);
  }
  await page.goto(`${state.base}/?page=${paper.id}&ws=${account.ws}`);
  await page.locator('.textLayer span').first().waitFor({timeout:60000});
  const notes = page.getByRole('button',{name:'Close Notes',exact:true});
  if(await notes.isVisible()) await click(notes);
  await page.locator('.chatInput').waitFor();
  const sash = page.locator('[role="separator"][data-panel-group-direction="horizontal"]');
  const sashBox = await sash.boundingBox();
  await page.mouse.move(sashBox.x+2,sashBox.y+200);
  await page.mouse.down();
  await page.mouse.move(790,sashBox.y+200,{steps:30});
  await page.mouse.up();
  // The shot opens on Figure 1 (page 2): jumping there on camera repaints the
  // whole PDF pane, the costliest second of the clip.
  const initialPage = page.getByRole('textbox', {name:'Current page',exact:true});
  await initialPage.fill('2'); await initialPage.press('Enter');
  await page.locator('[data-page="2"] .textLayer span').first().waitFor({timeout:60000});
  await hold(1200);
  const health = page.locator('.chatHealthStrip');
  if (await health.count()) throw new Error('Demo AI connection is unhealthy; fix it before recording');
  await page.screenshot({path:path.join(scratch,'agentic-setup.png')});
  if(process.argv.includes('--inspect')) {
    console.log(await page.locator('[role="separator"]').evaluateAll(es=>es.map(e=>({html:e.outerHTML.slice(0,400),box:e.getBoundingClientRect().toJSON()}))));
    console.log('Input:',await page.locator('.chatInput').boundingBox());
  } else {
    capture = await startCapture(page, path.join(scratch, 'frames-agentic'));
    clock = capture.clock;
    mark('start');
    await hold(800);
    // Figure 1c,d on page 2: the parity and fidelity plots, Ctrl+dragged into the chat.
    const sheet = await page.locator('[data-page="2"]').first().boundingBox();
    // Coordinates relative to the actual PDF page; they scale with its width.
    const box = {x:sheet.x + sheet.width * .515, y:sheet.y + sheet.width * .365,
      width:sheet.width * .43, height:sheet.width * .22};
    framing.figure = box;
    await page.mouse.move(box.x, box.y, {steps:25});
    await hold(500); mark('boxStart');
    await page.keyboard.down('Control'); await page.mouse.down();
    for (let i = 1; i <= 40; i++) {
      await page.mouse.move(box.x + box.width * i / 40, box.y + box.height * i / 40);
      await hold(25);
    }
    await page.mouse.up(); await page.keyboard.up('Control');
    await page.locator('.chatImgPreview img').waitFor();
    verified.boxAttachment = await page.locator('.chatImgPreview img').count();
    await hold(1000); mark('boxReady');
    // A plot's extracted text can be a stray axis digit: keep the picture as
    // the context and remove that text chip through the UI.
    const passageChip = page.getByTitle('Remove this passage', {exact:true});
    if (await passageChip.isVisible()) await click(passageChip);
    await click(page.locator('.chatInput'));
    mark('questionZoom');
    await hold(700);
    await page.keyboard.insertText('What do panels c and d show about moving entangled atoms? Answer in 2 short bullets, citing short verbatim passages from the text without ellipses.');
    await hold(2200);
    await page.keyboard.press('Enter'); mark('pdfSent');
    await answer('pdf', 1);
    verified.expandedSteps = await page.locator('.chatToolDetail').count();
    if (verified.expandedSteps) throw new Error('Keep individual tool steps collapsed');
    await page.locator('.chatBubbleRow.ai').last().scrollIntoViewIfNeeded();
    const citations = page.locator('.chatBubbleRow.ai a.chatCite.gammaLink-citation');
    await citations.first().waitFor();
    // Pick an actual answer link whose quote matches the PDF.js text exactly.
    // Model links can include ellipses or omit formula/reference characters.
    let citation;
    for (const candidate of await citations.all()) {
      const parsed = parseGammaLink(await candidate.getAttribute('href'), state.base);
      if (parsed?.kind !== 'citation' || !parsed.quote || parsed.pageId !== paper.id) continue;
      const text = await (await pdf.getPage(parsed.page)).getTextContent();
      const match = matchCitation(citationRuns(text.items), parsed.quote);
      if (match.status === 'matched' && !match.approximate) { citation = candidate; break; }
    }
    if (!citation) throw new Error('No exact passage citation in the answer');
    await citation.scrollIntoViewIfNeeded();
    framing.citation = await citation.boundingBox();
    verified.citationHref = await citation.getAttribute('href');
    await hold(2000); mark('citationStart');
    await page.screenshot({path:path.join(scratch,'agentic-answer.png')});
    // Hover the answer's real passage link to preview its quote, then follow it.
    const pill = await citation.boundingBox();
    await page.mouse.move(pill.x + pill.width / 2, pill.y + pill.height / 2, {steps:25});
    await page.locator('.chatCitePreview').waitFor();
    framing.preview = await page.locator('.chatCitePreview').boundingBox();
    await hold(1800);
    mark('citationClick');
    await citation.click();
    await page.locator('.pdfCitationMark').first().waitFor({timeout:20000});
    await hold(700); mark('citationReady');
    verified.citationMarks = await page.locator('.pdfCitationMark').count();
    verified.citationNotice = await page.locator('.pdfCitationNotice').allTextContents();
    if (verified.citationNotice.length) throw new Error('Citation must resolve to an exact passage');
    framing.passage = await page.locator('.pdfCitationMark').evaluateAll(es => {
      const boxes = es.map(e => e.getBoundingClientRect());
      const x = Math.min(...boxes.map(b => b.x)), y = Math.min(...boxes.map(b => b.y));
      return {x,y,width:Math.max(...boxes.map(b=>b.right))-x,height:Math.max(...boxes.map(b=>b.bottom))-y};
    });
    await page.screenshot({path:path.join(scratch,'agentic-citation.png')});
    await hold(3500); mark('passageEnd');

    // The agent's reach outside the library: it searches for follow-up work,
    // and saving a paper waits on an approval card (Save papers: Ask).
    mark('saveStart');
    await click(page.locator('.chatInput'));
    await hold(700);
    await page.keyboard.insertText('Find the two most-cited papers that build on this one and save them to my library.');
    await hold(1800);
    await page.keyboard.press('Enter'); mark('saveSent');
    const card = page.locator('.chatApproval:not(.sent)').first();
    await card.waitFor({timeout:240000});
    await hold(400); mark('approval');
    framing.approval = await card.boundingBox();
    await hold(2200);
    await click(card.getByRole('button', {name:'Allow in this chat', exact:true}));
    mark('allowed');
    await answer('save', 2);
    const changes = page.locator('.chatBubbleRow.ai').last().locator('.chatChanges');
    await changes.waitFor();
    await changes.scrollIntoViewIfNeeded();
    framing.changes = await changes.boundingBox();
    framing.saveAnswer = await page.locator('.chatBubbleRow.ai').last().boundingBox();
    await page.mouse.move(framing.changes.x + 80, framing.changes.y + framing.changes.height - 10, {steps:25});
    await hold(3500); mark('end');
    framing.frames = await capture.stop(); capture = null;
    await page.screenshot({path:path.join(scratch,'agentic-final.png')});
    // Both questions belong to this PDF's chat, and two papers really landed.
    const saved=await account.api(`/api/chats/${paper.id}`);
    fs.writeFileSync(path.join(scratch,'agentic-saved.json'),JSON.stringify(saved,null,2));
    const savedActions = (saved.messages || []).flatMap(m => m.actions || []);
    verified.persistedTools = savedActions.map(a => a.tool || a.kind);
    const questions = (saved.messages || []).filter(m => m.role === 'user');
    const replies = (saved.messages || []).filter(m => m.role === 'ai');
    const library = (await account.api('/api/blocks/root/children')).children;
    verified.newPages = library.filter(p => !pages.some(q => q.id === p.id)).map(p => p.content);
    verified.savedPapers = savedActions.filter(a => a.kind === 'save' && !a.existed).length;
    verified.savedFigure = questions[0]?.images?.length === 1;
    verified.singleConversation = questions.length === 2;
    verified.pdfChat = paper.id;
    verified.requests = requests;
    if (!verified.singleConversation || replies.length !== 2) throw new Error('Expected two saved PDF questions and answers');
    if (!verified.savedFigure || requests[0]?.images !== 1) throw new Error('The first question must carry the selected figure');
    if (verified.savedPapers < 2 || verified.newPages.length < 2) throw new Error('Expected two papers saved into the library');
    if (requests.length < 2 || requests.some(r=>r.pageId !== paper.id)) throw new Error('Both questions must use the PDF context');
  }
  await context.close(); context=null;
  if(!process.argv.includes('--inspect')) {
    const { frames, ...rest } = framing;
    fs.writeFileSync(path.join(scratch,'agentic-timeline.json'),JSON.stringify({frames,marks,actions,framing:rest,verified},null,2));
  }
} catch(error) {
  if(page&&!page.isClosed()) await page.screenshot({path:path.join(scratch,'agentic-failure.png')}).catch(()=>{});
  throw error;
} finally {
  if(capture) await capture.stop().catch(()=>{});
  if(context) await context.close();
  await browser.close();
  await pdf.destroy();
}
