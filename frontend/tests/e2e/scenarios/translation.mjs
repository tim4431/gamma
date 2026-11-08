// The PDF translated view on a two-column page (docs/dev/translation.md):
// a paragraph that the column break cuts in two goes to the model as one
// text with {br} where the cut was, and comes back split into both columns'
// boxes; its formulas (a Greek letter in the Symbol font, an italic
// variable) and its citation marker go as {v1}, {v2}… tokens, and the
// formula comes back as a crop of the page inside the translation; the
// request names the page (for the paper's brief), the section heading and
// the text around its texts. "Translate whole document" then does the two
// pages after it, each request with the text before and after it across
// the page breaks. A job asks for the paper's brief as it starts, sends its
// first chunk without waiting for it, runs on when the reader leaves the
// PDF, and asks again for what went without the brief. Settings keeps
// translations and clears them, the viewer's included; "Forget this paper's
// translations…", asked first, forgets one paper's. Ctrl+Shift+O shows the
// original and the translation, and a hidden button's ⋯ row opens the
// button's menu. The session's first translation to
// finish says Export carries it, and Export → Annotated PDF writes the
// translation shown into the file, the original once it is hidden. A viewer
// of the workspace is offered neither Forget nor Clear. A page's reference
// list goes to no model and stays selectable as the original until "Translate
// references too". A page keeps the cap it settled at through a remount while
// a job works on it, and a job that lands under a hidden PDF measures once the
// PDF is back. A paper this browser left translated comes back after a reload
// from the kept translations, with one lookup and no request to translate, and
// its export reads the pages not seen, which the dialog counts; one left in
// the original, translated by another account only, or by a viewer (whose
// translation the workspace does not keep), shows nothing and looks nothing
// up. The translate endpoints and the kept translations' are answered
// locally. The workspace's glossary is the exception: an account of its own
// translates through the server, in front of a local stand-in for a chat
// model's API, so the provider's requests show the Terms line the server
// builds; its paper opens from what the server keeps with the AI connection
// removed and once it is made again; a term comes from Settings and from the
// selection's translation, a second time refused in the reader's words, and a
// reader sees the glossary without its edit controls.
import fs from "node:fs";
import http from "node:http";
import { Account, FAKE_AI_MODELS, assertMenuFrom, assertMenuRoles, fakeAiModels, wanted } from "../harness.mjs";
import { dragSelectPdfText, waitForPdf } from "./pdf.mjs";

// A paragraph on a tinted panel with a formula in it, one on plain paper right
// under another so their boxes meet, and a one-line heading in bold blue
// centred over the column.
const PANEL = { x: 60, y: 560, w: 492, h: 96, rgb: [0.925, 0.925, 0.925] };
function paintedPage() {
  const runs = [];
  const line = (x, y, text, font = "F2") => runs.push({ x, y, size: 10, font, text });
  runs.push({ x: 176, y: 700, size: 14, font: "F5", text: "Methods", rgb: [0, 0, 0.8] });
  line(72, 640, "The panel holds a paragraph about the atoms and their long lived");
  line(72, 628, "states, which we set apart here on a ground of its own for the");
  line(72, 616, "reader who wants the main result of the work in one short place");
  line(72, 604, "with the field");
  line(135, 604, "W", "F4");
  line(146, 604, "of the drive in it and a few more words to fill.");
  line(72, 500, "The first paragraph on plain paper runs over two lines that are");
  line(72, 488, "long enough for a translation of about their own length to fill.");
  line(72, 477, "The second paragraph starts under it with no gap at all, so that");
  line(72, 465, "the boxes of the two blocks meet between their lines right here.");
  return { runs, fills: [PANEL] };
}

// A page laid out in two columns of Times at 10pt, baselines 12pt apart.
// The left column's second paragraph runs on into the right column.
function twoColumnPage() {
  const runs = [];
  const line = (x, y, text, font = "F2") => runs.push({ x, y, size: 10, font, text });
  runs.push({ x: 72, y: 724, size: 11, font: "F2", text: "1 Introduction" });
  line(82, 700, "Ensembles of neutral atoms interact over");
  line(72, 688, "long distances when they are excited to");
  line(72, 676, "Rydberg states, and a blockade forms [3].");
  line(82, 664, "The collective Rabi frequency grows with");
  line(72, 652, "the number of atoms, and each atom feels");
  line(72, 640, "the field");
  line(111, 640, "W", "F4");
  line(121, 640, "of the drive and its detuning in");
  line(72, 628, "and the position");
  line(141, 628, "x", "F3");
  line(148, 628, ", in the rotating frame the");
  line(330, 700, "continues with the Hamiltonian of the");
  line(330, 688, "system, which we now write down in full");
  line(330, 676, "for the reader who wants every detail.");
  return { runs };
}

// One column: a paragraph, the next section's heading, a paragraph.
function secondPage() {
  const runs = [];
  const line = (x, y, text) => runs.push({ x, y, size: 10, font: "F2", text });
  line(82, 724, "Here the atoms sit in optical tweezers that");
  line(72, 712, "hold each one in place, so the distances");
  line(72, 700, "between them are known to a few percent.");
  runs.push({ x: 72, y: 676, size: 11, font: "F2", text: "2 Model" });
  line(82, 652, "We write the Hamiltonian of the driven");
  line(72, 640, "atoms with their interaction terms in it.");
  return { runs };
}

// Two paragraphs: one the model translates, one the server gives up on.
function stubbornPage() {
  const runs = [];
  const line = (x, y, text) => runs.push({ x, y, size: 10, font: "F2", text });
  line(82, 724, "A willing paragraph that the model translates");
  line(72, 712, "on the first request that it is asked to make.");
  line(82, 688, "A stubborn paragraph that the model keeps");
  line(72, 676, "getting wrong until it is asked once more.");
  return { runs };
}

function thirdPage() {
  const runs = [];
  const line = (x, y, text) => runs.push({ x, y, size: 10, font: "F2", text });
  line(82, 724, "The interaction falls off with the sixth");
  line(72, 712, "power of the distance between two atoms.");
  return { runs };
}

// A paragraph over a reference list, an entry a block, enough to be one by itself.
const ENTRIES = Array.from({ length: 8 }, (_, k) => `[${k + 1}] A. Smith and B. Jones, Phys. Rev. Lett. ${96 + k}, 01040${k} (200${k}).`);
function referencesPage() {
  const runs = [];
  const line = (x, y, text) => runs.push({ x, y, size: 10, font: "F2", text });
  line(82, 724, "The atoms are held apart by the blockade, so the");
  line(72, 712, "array keeps its order through the whole sequence.");
  ENTRIES.forEach((entry, k) => line(72, 676 - 24 * k, entry));
  return { runs };
}

// Twelve short paragraphs: two chunks, of ten and of two.
const COUNTED = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];
function longPage(opening = "Paragraph") {
  const runs = [];
  const line = (x, y, text) => runs.push({ x, y, size: 10, font: "F2", text });
  COUNTED.forEach((name, k) => {
    line(82, 724 - 36 * k, `${opening} ${name} says how the atoms in the lattice`);
    line(72, 712 - 36 * k, "move once the drive is switched on and off.");
  });
  return { runs };
}

export async function translationScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage }) {
  if (!wanted("translation")) return;
  let page, pageId, docId, stubbornDoc;
  const requests = [];
  // A step's own answer for the translate endpoint (route, body), when set.
  let respond = null;
  // The brief's requests, and the kept translations' DELETEs (their paths);
  // what GET /api/translations/stats answers.
  const briefs = [];
  const forgotten = [];
  let kept = { documents: 2, paragraphs: 42, bytes: 37 * 1024 };
  let asViewer = false;
  // What the server keeps, as the fake translate endpoint leaves it: page id
  // → Map(text → translation), for a request with `keep`; a Clear or a Forget
  // drops it all. The lookups (POST /api/translations/lookup) answer from it,
  // and are kept in `lookups`. `everyText`: a page whose every text another
  // account of the workspace has translated and kept.
  const keptRows = new Map();
  const lookups = [];
  let everyText = "";

  // Settings → Language and Translation.
  const openTranslation = async () => {
    await page.locator('[data-guide="header.account"]').click();
    await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
    await page.getByRole("navigation", { name: "Settings categories" })
      .getByRole("button", { name: "Language and Translation", exact: true }).click();
  };

  await step("translation: setup, a paper whose first page's paragraph runs over the column break", async () => {
    const pdf = makePdf([twoColumnPage(), secondPage(), thirdPage()]);
    const up = await alice.upload("/api/uploads", pdf, "columns.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Two columns", source_url: up.source_url } });
    pageId = created.id;
    docId = up.doc_id;
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx, { ...FAKE_AI_MODELS,
      translate_engines: [{ id: "engine:microsoft", label: "Microsoft (free)" }] });
    // Every text comes back as "译:" + each of its pieces, tokens and
    // breaks kept: what a model following the prompt returns.
    await ctx.route("**/api/ai/translate", async (route) => {
      const body = route.request().postDataJSON();
      requests.push(body);
      if (respond) return respond(route, body);
      const translations = body.texts.map((t) => `译:${t.split("{br}").join("{br}译:")}`);
      if (body.keep !== false && body.page_id) {
        if (!keptRows.has(body.page_id)) keptRows.set(body.page_id, new Map());
        body.texts.forEach((t, k) => keptRows.get(body.page_id).set(t, translations[k]));
      }
      await route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify({ translations, model: "demo:model", cached: false })}\n` });
    });
    // The paper's brief is there at once.
    await ctx.route("**/api/ai/translate/brief", async (route) => {
      briefs.push(route.request().postDataJSON());
      await route.fulfill({ contentType: "application/x-ndjson", body: `${JSON.stringify({ ready: true })}\n` });
    });
    await ctx.route((url) => url.pathname.startsWith("/api/translations"), async (route) => {
      const { pathname } = new URL(route.request().url());
      if (route.request().method() === "GET") return route.fulfill({ json: kept });
      if (pathname === "/api/translations/lookup") {
        const body = route.request().postDataJSON();
        lookups.push(body);
        const rows = keptRows.get(body.page_id);
        const translations = body.texts.map((t) => (body.page_id === everyText
          ? `译:${t.split("{br}").join("{br}译:")}` : rows?.get(t) ?? null));
        return route.fulfill({ json: { translations, model: "model" } });
      }
      keptRows.clear();
      forgotten.push(pathname);
      if (pathname === "/api/translations") kept = { documents: 0, paragraphs: 0, bytes: 0 };
      return route.fulfill({ json: { removed: 3 } });
    });
    // While `asViewer`, the session says Alice only views her workspace.
    await ctx.route("**/api/session", async (route) => {
      if (!asViewer) return route.fallback();
      const response = await route.fetch();
      const json = await response.json();
      for (const w of json.workspaces || []) if (w.id === alice.ws) w.role = "viewer";
      await route.fulfill({ response, json });
    });
    // The last export was another format, so the hint's Export… has to
    // open Annotated PDF itself.
    await ctx.addInitScript(() => {
      try { localStorage.setItem("gamma-export-opts", JSON.stringify({ format: "markdown" })); } catch {}
    });
    // The overlay's reads of a page's pixels: its one context that asks for
    // reading often, of more than pdf.js's single test pixels.
    await ctx.addInitScript(() => {
      window.transFades = [];
      window.addEventListener("animationstart", (e) => {
        if (e.animationName === "pdfTransFade") window.transFades.push(e.target.parentElement.className);
      }, true);
      const read = CanvasRenderingContext2D.prototype.getImageData;
      window.transReads = 0;
      CanvasRenderingContext2D.prototype.getImageData = function (x, y, w, h, ...rest) {
        if (w > 1 && this.getContextAttributes?.().willReadFrequently) window.transReads += 1;
        return read.call(this, x, y, w, h, ...rest);
      };
    });
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
  });

  await step("translation: speed is chosen independently of chat and persists after reload", async () => {
    await openTranslation();
    const speed = page.getByRole("button", { name: "Translation speed", exact: true });
    assertEq(await speed.innerText(), "Default");
    // Whatever the chat's speed is (another group on this worker may have set alice's), it stays.
    const chatSpeed = (await alice.api("/api/prefs/profile")).value?.chatSpeed || "";
    await speed.click();
    const menu = page.locator(".uiSelectMenu");
    assertEq((await menu.getByRole("menuitemradio").allTextContents()).join(","), "Default,Flex,Fast", "the tiers by name");
    await menu.getByRole("menuitemradio", { name: "Fast", exact: true }).click();
    await until(async () => (await alice.api("/api/prefs/profile")).value?.translateSpeed === "fast");
    assertEq((await alice.api("/api/prefs/profile")).value?.chatSpeed || "", chatSpeed, "the chat's speed is left as it was");
    await page.reload();
    await waitForPdf(page, 1);
    await openTranslation();
    assertEq(await speed.innerText(), "Fast");
    await page.getByRole("button", { name: "Close settings", exact: true }).click();
    assertNoProblems(page);
  });

  await step("translation: the cut paragraph goes as one text with its formulas as tokens, and fills both columns", async () => {
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
    await until(async () => (await boxes.allTextContents()).some((t) => /^译:\s*continues/.test(t)),
      { what: "the right column's box shows the paragraph's second piece" });
    const sent = requests.flatMap((r) => r.texts);
    const joined = sent.find((t) => t.includes("{br}"));
    assert(joined, `a joined text was sent: ${JSON.stringify(sent)}`);
    assert(/rotating frame the \{br\} continues with the Hamiltonian/.test(joined), joined);
    assert(/the field \{v\d+\} of the drive/.test(joined), `Ω is a token: ${joined}`);
    assert(/position \{v\d+\}/.test(joined), `x is a token: ${joined}`);
    assert(sent.some((t) => /blockade forms \{v\d+\}\./.test(t)), `the citation marker is a token: ${JSON.stringify(sent)}`);
    assert(!sent.some((t) => t.includes("optical tweezers")), "only the page being read was translated");
    const withJoin = requests.find((r) => r.texts.includes(joined));
    assertEq(withJoin.page_id, pageId, "the request names its page");
    assert(!("head" in withJoin), "a stored PDF's requests name the page alone");
    assertEq(withJoin.speed, "fast", "translation requests carry their own speed setting");
    assertEq(withJoin.wait_brief, false, "the job's first chunk does not wait for the paper's brief");
    assertEq(withJoin.keep, true, "its translations may be kept, the default");
    // The job asked for the paper's brief as it started.
    assertEq(briefs.length, 1, "one request for the brief");
    assertEq(briefs[0].page_id, pageId);
    assert(!("head" in briefs[0]), "the server reads its own copy");
    assertEq(briefs[0].speed, "fast");
    assertEq(briefs[0].keep, true);
    // The section heading rides along: as the request's section, or as one
    // of its texts when the request starts with it.
    assert(withJoin.section === "1 Introduction" || withJoin.texts.includes("1 Introduction"), JSON.stringify(withJoin));
    const texts = await boxes.allTextContents();
    assert(texts.some((t) => t.startsWith("译:The collective Rabi frequency")), JSON.stringify(texts));
    assert(texts.some((t) => t.includes("blockade forms [3].")), "the citation marker is back as text");
    assertNoProblems(page);
  });

  await step("translation: the session's first finished translation says Export carries it, and its button opens Annotated PDF", async () => {
    // The first job ended in the step before; its button stays 8 s.
    const pill = page.locator(".statusPill", { hasText: "Export now to get a PDF with the translation in it" });
    await pill.getByRole("button", { name: "Export…", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Export", exact: true });
    await dialog.waitFor();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    const summary = await dialog.innerText();
    assert(/translation into .+ replaces the original text on 1 page\. To export the original, show it first/.test(summary),
      `the dialog opens on the annotated PDF, translated: ${summary}`);
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "detached" });
    assertNoProblems(page);
  });

  await step("translation: a formula comes back as a crop of the page inside the translated line", async () => {
    const math = page.locator('[data-page="1"] .pdfTransMath');
    await until(async () => (await math.count()) >= 2, { what: "the two formulas in the translation" });
    await until(() => math.first().evaluate((el) => !el.classList.contains("bare") && el.querySelector("canvas").width > 0),
      { what: "the formula drawn from the page's pixels" });
    const shown = await math.evaluateAll((els) => els.map((el) => {
      // The crop holds the glyph: dark pixels on the white page.
      const c = el.querySelector("canvas");
      const px = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
      let ink = 0;
      for (let i = 0; i < px.length; i += 4) if (px[i + 3] > 128 && px[i] + px[i + 1] + px[i + 2] < 384) ink += 1;
      return {
        text: el.querySelector(".pdfTransMathText").textContent,
        w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height, ink,
      };
    }));
    assert(shown.some((s) => s.text === "Ω"), `the Greek letter's crop: ${JSON.stringify(shown)}`);
    assert(shown.every((s) => s.w > 2 && s.h > 4), `crops have a size: ${JSON.stringify(shown)}`);
    assert(shown.every((s) => s.ink >= 4), `crops show their glyph: ${JSON.stringify(shown)}`);
    // Copying the translation carries the formula's text.
    const copied = await page.locator('[data-page="1"] .pdfTransText', { hasText: "译:The collective" })
      .evaluate((el) => el.textContent);
    assert(copied.includes("Ω"), copied);
    assertNoProblems(page);
  });

  await step("translation: the overlay carries its language and the page's face, and a zoom writes nothing on it and no font size", async () => {
    const layer = page.locator('[data-page="1"] .pdfTransLayer');
    assertEq(await layer.getAttribute("lang"), requests[0].lang, "the layer says what language it is in");
    const set = await page.locator('[data-page="1"] .pdfTransText').first().evaluate((el) => ({
      family: getComputedStyle(el).fontFamily, rendering: getComputedStyle(el).textRendering,
    }));
    assert(/^"?Times New Roman/.test(set.family), `the page's face: ${set.family}`);
    assertEq(set.rendering, "geometricprecision");
    // The zoom reaches the layer through the zoom layer's --viewer-scale, so its
    // own style never changes: read the scale it shows from its rendered size
    // (laid out at REF_SCALE, 2).
    const shown = () => layer.evaluate((el) => 2 * el.getBoundingClientRect().width / el.offsetWidth);
    const sizes = () => page.locator('[data-page="1"] .pdfTransText').evaluateAll((els) => els.map((el) => getComputedStyle(el).fontSize));
    const before = await shown(), sizesBefore = await sizes();
    await layer.evaluate((el) => {
      window.transWrites = [];
      window.transWatch = new MutationObserver((changes) => window.transWrites.push(...changes.map((c) => c.target.className)));
      window.transWatch.observe(el, { attributes: true, attributeFilter: ["style"], subtree: true });
    });
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await until(async () => Math.abs((await shown()) - before) > 0.01, { what: "the layer carried to the new zoom" });
    await page.waitForTimeout(700); // the sharp redraw lands, and the crops are drawn from it
    const writes = await page.evaluate(() => { window.transWatch.disconnect(); return window.transWrites; });
    assertEq(writes.join(), "", "no write on the layer or in it");
    assertEq((await sizes()).join(), sizesBefore.join(), "the text keeps the size it is laid out at");
    assertNoProblems(page);
  });

  await step("translation: at 60% no translated text is cut, and each block keeps the ratio it fitted at", async () => {
    const layer = page.locator('[data-page="1"] .pdfTransLayer');
    const scale = () => layer.evaluate((el) => 2 * el.getBoundingClientRect().width / el.offsetWidth);
    const fits = () => page.locator('[data-page="1"] .pdfTransText').evaluateAll((els) => els.map((el) => el.style.getPropertyValue("--fit")));
    const fitsBefore = await fits();
    let steps = 0;
    while ((await scale()) > 0.61 && steps < 12) {
      const was = await scale();
      await page.getByRole("button", { name: "Zoom out", exact: true }).click();
      await until(async () => (await scale()) < was, { what: "a zoom step" });
      steps += 1;
    }
    assert((await scale()) <= 0.61, `at ${await scale()}`);
    const blocks = await page.locator('[data-page="1"] .pdfTransPara').evaluateAll((paras) => paras.flatMap((para) => {
      const text = para.querySelector(".pdfTransText");
      if (!text) return [];
      return [{
        clipped: [para, text].filter((el) => getComputedStyle(el).overflow !== "visible").length,
        height: text.offsetHeight, room: para.clientHeight, scrolled: text.scrollWidth - text.clientWidth,
      }];
    }));
    assert(blocks.length >= 4, JSON.stringify(blocks));
    assert(blocks.every((b) => !b.clipped), `nothing clips: ${JSON.stringify(blocks)}`);
    assert(blocks.every((b) => b.height <= b.room + 1 && b.scrolled <= 1), `all of it is in its box: ${JSON.stringify(blocks)}`);
    assertEq((await fits()).join(), fitsBefore.join(), "the fit does not depend on the zoom");
    for (let k = 0; k < steps; k += 1) {
      const was = await scale();
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
      await until(async () => (await scale()) > was, { what: "a zoom step back" });
    }
    assertNoProblems(page);
  });

  await step("translation: Export → Annotated PDF writes the translation shown in place of the original text, and the original once it is hidden", async () => {
    // The dialog's Annotated PDF with its switches as they are (highlights
    // and notes on): what its review step says, what the job is asked, and
    // the file it downloads, as pdf.js reads its first page.
    const exportPdf = async () => {
      await page.click("button[aria-label='View']");
      await page.locator(".menuPopover .ctxMenuItem", { hasText: "Export…" }).click();
      const dialog = page.getByRole("dialog", { name: "Export", exact: true });
      await dialog.waitFor();
      await dialog.getByRole("button", { name: "Annotated PDF", exact: true }).click();
      await dialog.getByRole("button", { name: "Next", exact: true }).click();
      const summary = await dialog.innerText();
      const sent = page.waitForRequest((r) => new URL(r.url()).pathname === "/api/jobs/export" && r.method() === "POST");
      const download = page.waitForEvent("download", { timeout: 30000 });
      await dialog.getByRole("button", { name: "Export", exact: true }).click();
      const body = (await sent).postDataJSON();
      const file = await download;
      await dialog.getByRole("heading", { name: "Export ready", exact: true }).waitFor();
      await dialog.getByRole("button", { name: "Done", exact: true }).click();
      await dialog.waitFor({ state: "detached" });
      const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
      const doc = await pdfjs.getDocument({ data: new Uint8Array(fs.readFileSync(await file.path())), verbosity: 0 }).promise;
      const first = await (await doc.getPage(1)).getTextContent();
      await doc.destroy();
      // Without spaces: pdf.js splits the runs a line was set in.
      return { summary, body, text: first.items.map((item) => item.str).join("").replace(/\s+/g, "") };
    };

    const translated = await exportPdf();
    assert(/translation into .+ replaces the original text on 1 page/.test(translated.summary),
      `the summary says the translation goes in: ${translated.summary}`);
    const sent = translated.body.translation;
    assert(sent, `the job carries the translation: ${JSON.stringify(translated.body)}`);
    assertEq(sent.doc_id, docId, "made on the page's own file");
    assertEq(sent.pages.map((p) => p.page).join(), "1", "the page translated, alone");
    const parts = sent.pages[0].blocks.flatMap((b) => b.parts);
    assert(parts.some((p) => typeof p === "string" && p.startsWith("译:The collective Rabi")), JSON.stringify(parts));
    const omega = parts.find((p) => typeof p === "object" && p.text === "Ω");
    assert(omega && omega.page === 1 && omega.box.length === 4 && omega.box[2] > omega.box[0],
      `the formula goes as its page and box: ${JSON.stringify(omega)}`);
    assert(parts.some((p) => typeof p === "string" && p.includes("[3]")), "the citation marker goes as its text");
    assert(sent.pages[0].blocks.every((b) => b.lines.length && b.lines.every((l) => l.length === 4)), "each block has its lines");
    // The fake translation is the source behind "译:", so the original
    // shows only inside it.
    const opening = "Ensemblesofneutralatomsinteractover";
    assert(translated.text.includes("译:ThecollectiveRabifrequency"), `the file holds the translation: ${translated.text}`);
    assertEq(translated.text.split(opening).length - 1, 1, `the original lines are gone: ${translated.text}`);
    assert(translated.text.includes(`译:${opening}`), translated.text);

    // Hidden: the original, and no word of a translation.
    await page.getByRole("button", { name: "Show original", exact: true }).click();
    await page.locator('[data-page="1"] .pdfTransLayer').waitFor({ state: "detached" });
    const original = await exportPdf();
    assert(!original.summary.includes("replaces the original text"), original.summary);
    assert(!("translation" in original.body), JSON.stringify(original.body));
    assert(original.text.includes(opening) && !original.text.includes("译"), original.text);
    await page.getByRole("button", { name: "Show translation", exact: true }).click();
    await page.locator('[data-page="1"] .pdfTransPara').first().waitFor();
    assertNoProblems(page);
  });

  await step("translation: a translated export that failed starts again from Background tasks with the translation the server kept", async () => {
    // The first job is sent another file's doc_id, so it fails as one would
    // whose PDF was replaced meanwhile; the server keeps the layout it was
    // sent, and the tray's Start again names the job instead of sending it.
    await page.route("**/api/jobs/export", async (route) => {
      const body = route.request().postDataJSON();
      await route.continue({ postData: JSON.stringify({ ...body, translation: { ...body.translation, doc_id: "0".repeat(64) } }) });
    }, { times: 1 });
    await page.click("button[aria-label='View']");
    await page.locator(".menuPopover .ctxMenuItem", { hasText: "Export…" }).click();
    const dialog = page.getByRole("dialog", { name: "Export", exact: true });
    await dialog.waitFor();
    await dialog.getByRole("button", { name: "Annotated PDF", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    const answered = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/jobs/export" && r.request().method() === "POST");
    await dialog.getByRole("button", { name: "Export", exact: true }).click();
    const first = await (await answered).json();
    assertEq(first.params.translation, requests[0].lang, "its params name the language alone");
    await dialog.getByRole("heading", { name: "Export failed", exact: true }).waitFor();
    assert(/changed after it was translated/.test(await dialog.getByRole("alert").innerText()), "it failed on the file");
    // its page is open: the dialog goes back to the setup, which reads the viewer afresh
    await dialog.getByRole("button", { name: "Export again…", exact: true }).waitFor();
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await dialog.waitFor({ state: "detached" });

    await page.click("button[aria-label='Background tasks']");
    const row = page.locator(".taskRow.failed").first();
    const sent = page.waitForRequest((r) => new URL(r.url()).pathname === "/api/jobs/export" && r.method() === "POST");
    const again = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/jobs/export" && r.request().method() === "POST");
    await row.getByRole("button", { name: /^Start .+ again$/ }).click();
    const body = (await sent).postDataJSON();
    assertEq(body.translation_of, first.id, "Start again names the failed job");
    assert(!("translation" in body), `and sends no layout: ${JSON.stringify(body)}`);
    const second = await (await again).json();
    assert(second.id && second.id !== first.id, JSON.stringify(second));
    // the layout it writes is the kept one, another file's: it fails the same way
    await until(async () => /changed after it was translated/.test(
      await page.evaluate(async (id) => (await (await fetch(`/api/jobs/${id}`)).json()).error || "", second.id)),
    { what: "the retried job failing on the kept layout's file" });
    await page.keyboard.press("Escape");
    assertNoProblems(page);
  });

  await step("translation: a drag across the columns' translated paragraphs selects and copies them in reading order", async () => {
    // The left column's first paragraph, its second, then the right column:
    // the right column's block comes before the second paragraph by index.
    const indices = await page.locator('[data-page="1"] .pdfTransPara[data-block]').evaluateAll((els) => els.map((el) => Number(el.dataset.block)));
    assert(indices.some((n, k) => k && n < indices[k - 1]), `drawn in reading order, not by block index: ${indices}`);
    await page.locator('[data-page="1"] .pdfTransText', { hasText: "Ensembles" }).scrollIntoViewIfNeeded();
    const from = await wordBox(page, '[data-page="1"] .pdfTransText', "Ensembles");
    const to = await wordBox(page, '[data-page="1"] .pdfTransText', "Hamiltonian");
    assert(from && to && to.x > from.x + 100, `two columns: ${JSON.stringify({ from, to })}`);
    await drag(page, from, to);
    const copied = await page.evaluate(() => String(getSelection()));
    const at = (s) => copied.indexOf(s);
    assert(at("Ensembles") >= 0 && at("Ensembles") < at("collective Rabi") && at("collective Rabi") < at("continues"),
      `left column first, then the right one: ${copied}`);
    // The popup opens for it once the mouse moves on; a press elsewhere closes both.
    await page.mouse.move(to.x + to.w + 14, to.y + to.h + 14, { steps: 4 });
    await page.locator(".plainTip").waitFor();
    await page.mouse.click(to.x + to.w + 14, to.y - 60);
    await page.locator(".plainTip").waitFor({ state: "detached" });
    assertNoProblems(page);
  });

  // A right-click on `loc` at its left end; the menu it opens.
  const rightClick = async (loc) => {
    const b = await loc.boundingBox();
    await page.mouse.click(b.x + 6, b.y + Math.min(b.height / 2, 8), { button: "right" });
    const menu = page.getByRole("menu");
    await menu.waitFor();
    return menu;
  };
  const rows = async (menu, what) => (await assertMenuRoles(menu, what)).map((item) => item.text).join(",");
  const copied = () => page.evaluate(() => window.copiedTexts.at(-1));
  // What Copy writes, kept instead of reaching the clipboard.
  const keepCopies = (p) => p.evaluate(() => {
    window.copiedTexts = [];
    navigator.clipboard.writeText = async (text) => { window.copiedTexts.push(text); };
  });

  await step("translation: a right-click on a translated paragraph opens its menu: Copy, and Show original here hands its blocks back to the text layer", async () => {
    await keepCopies(page);
    await page.evaluate(() => getSelection().removeAllRanges());
    const cut = page.locator('[data-page="1"] .pdfTransText', { hasText: "continues" });
    await cut.scrollIntoViewIfNeeded();
    let menu = await rightClick(cut);
    assertEq(await rows(menu, "the paragraph menu"), "Copy translation,Copy original,Translate again,Show original here");
    // The paragraph the column break cut is one: both parts.
    await menu.getByRole("menuitem", { name: "Copy translation", exact: true }).click();
    await menu.waitFor({ state: "detached" });
    const translation = await copied();
    assert(translation.startsWith("译:The collective Rabi") && /译:\s*continues with the Hamiltonian/.test(translation), translation);
    menu = await rightClick(cut);
    await menu.getByRole("menuitem", { name: "Copy original", exact: true }).click();
    const original = await copied();
    assert(original.startsWith("The collective Rabi") && original.includes("continues with the Hamiltonian") && !original.includes("译"), original);

    menu = await rightClick(cut);
    await menu.getByRole("menuitem", { name: "Show original here", exact: true }).click();
    await until(async () => (await page.locator('[data-page="1"] .pdfTransText', { hasText: /continues|collective Rabi/ }).count()) === 0,
      { what: "both parts of the paragraph shown in the original" });
    assert((await page.locator('[data-page="1"] .pdfTransText').count()) > 0, "the other paragraphs keep their translation");
    // The original is the text layer's again: it takes the pointer and a drag selects it.
    const span = page.locator('[data-page="1"] .textLayer span', { hasText: "continues with the" }).first();
    await until(() => span.evaluate((el) => !el.classList.contains("transMasked")), { what: "its spans no longer standing down" });
    const from = await wordBox(page, '[data-page="1"] .textLayer', "continues");
    const to = await wordBox(page, '[data-page="1"] .textLayer', "Hamiltonian");
    assertEq(await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest(".textLayer"), [from.x + 2, from.y + from.h / 2]), true);
    await drag(page, from, to);
    assertEq(await page.evaluate(() => String(getSelection()).trim()), "continues with the Hamiltonian");
    // Its menu, from the original: Copy takes the selection, and the way back.
    menu = await rightClick(span);
    assertEq(await rows(menu, "the menu of a paragraph shown in the original"),
      "Copy translation,Copy original,Translate again,Show translation here");
    await menu.getByRole("menuitem", { name: "Copy original", exact: true }).click();
    assertEq(await copied(), "continues with the Hamiltonian", "the selection's text");
    await page.mouse.click(from.x, from.y - 40);
    menu = await rightClick(span);
    await menu.getByRole("menuitem", { name: "Show translation here", exact: true }).click();
    await page.locator('[data-page="1"] .pdfTransText', { hasText: "continues" }).waitFor();
    assertNoProblems(page);
  });

  await step("translation: Translate again asks once, afresh, for the whole paragraph and replaces its text", async () => {
    const start = requests.length;
    // Held until the paragraphs it leaves alone are measured while it is out.
    let answer;
    const held = new Promise((resolve) => { answer = resolve; });
    respond = async (route, body) => {
      await held;
      return route.fulfill({ contentType: "application/x-ndjson", body: `${JSON.stringify({
        translations: body.texts.map((t) => `再译:${t.split("{br}").join("{br}再译:")}`), model: "demo:model", cached: false, failed: [], brief: true })}\n` });
    };
    const other = page.locator('[data-page="1"] .pdfTransText', { hasText: "Ensembles" });
    const size = () => other.evaluate((el) => getComputedStyle(el).fontSize);
    try {
      const before = await size();
      const cut = page.locator('[data-page="1"] .pdfTransText', { hasText: "continues" });
      const menu = await rightClick(cut);
      await menu.getByRole("menuitem", { name: "Translate again", exact: true }).click();
      await until(() => requests.length > start, { what: "the paragraph's request" });
      await page.waitForTimeout(300);
      assertEq(await size(), before, "a paragraph left alone keeps its size while another is asked for again");
      answer();
      const texts = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
      await until(async () => {
        const now = await texts.allTextContents();
        return now.some((t) => /^再译:\s*continues/.test(t)) && now.some((t) => t.startsWith("再译:The collective Rabi"));
      }, { what: "both parts of the paragraph in their new words" });
      const sent = requests.slice(start);
      assertEq(sent.length, 1, "one request");
      assertEq(sent[0].texts.length, 1, "for the paragraph alone");
      assert(/rotating frame the \{br\} continues/.test(sent[0].texts[0]), sent[0].texts[0]);
      assertEq(sent[0].fresh, true, "asked afresh");
      assertEq(sent[0].wait_brief, true, "waiting for the paper's brief");
      assert((await texts.allTextContents()).some((t) => t.startsWith("译:Ensembles")), "the other paragraphs keep theirs");
      await page.locator(".pdfTransBar").waitFor({ state: "detached" });
    } finally {
      answer();
      respond = null;
    }
    assertNoProblems(page);
  });

  await step("translation: on a touch screen, the selection popup's ⋯ opens the paragraph's menu, whose Copy takes what was selected", async () => {
    const ctx = await alice.context(browser, { hasTouch: true });
    try {
      await fakeAiModels(ctx, FAKE_AI_MODELS);
      await ctx.route("**/api/ai/translate", (route) => route.fulfill({ contentType: "application/x-ndjson", body: `${JSON.stringify({
        translations: route.request().postDataJSON().texts.map((t) => `译:${t.split("{br}").join("{br}译:")}`), model: "demo:model", cached: false })}\n` }));
      await ctx.route("**/api/ai/translate/brief", (route) => route.fulfill({ contentType: "application/x-ndjson", body: `${JSON.stringify({ ready: true })}\n` }));
      const touch = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
      await waitForPdf(touch, 1);
      // A finger's press, then a selection in the translation: the popup
      // opens once the selection has stopped changing.
      await touch.getByRole("button", { name: "Translate", exact: true }).tap();
      await touch.locator('[data-page="1"] .pdfTransText', { hasText: "continues" }).waitFor();
      await keepCopies(touch);
      const selected = await touch.evaluate(() => {
        const el = [...document.querySelectorAll('[data-page="1"] .pdfTransText')].find((e) => e.textContent.includes("continues"));
        const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        for (let n = walk.nextNode(); n; n = walk.nextNode()) {
          const k = n.data.indexOf("continues with the");
          if (k < 0) continue;
          const range = document.createRange();
          range.setStart(n, k);
          range.setEnd(n, k + "continues with the".length);
          getSelection().removeAllRanges();
          getSelection().addRange(range);
          return true;
        }
        return false;
      });
      assert(selected, "the words selected");
      const tip = touch.locator(".plainTip");
      await tip.waitFor();
      const more = tip.getByRole("button", { name: "Paragraph actions", exact: true });
      await more.tap();
      const menu = touch.getByRole("menu");
      await assertMenuFrom(menu, more, "below", "the popup's paragraph menu");
      assertEq((await assertMenuRoles(menu, "the popup's paragraph menu")).map((item) => item.text).join(","),
        "Copy translation,Copy original,Translate again,Show original here");
      await menu.getByRole("menuitem", { name: "Copy translation", exact: true }).tap();
      assertEq(await touch.evaluate(() => window.copiedTexts.at(-1)), "continues with the", "the selection's words");
      assertNoProblems(touch);
    } finally {
      await ctx.close();
    }
  });

  await step("translation: a highlight made from translated text, by the keyboard, lies on the original lines with the original quote, and stays when the translation is hidden", async () => {
    const highlights = async (s) => ((await alice.api(`/api/blocks/${pageId}/subtree`)).block.children || [])
      .filter((b) => (b.properties?.quote || "").includes(s));
    // Inside the right column's translation, the second part of the paragraph
    // the column break cut.
    await page.locator('[data-page="1"] .pdfTransText', { hasText: "continues" }).scrollIntoViewIfNeeded();
    const from = await wordBox(page, '[data-page="1"] .pdfTransText', "continues");
    const to = await wordBox(page, '[data-page="1"] .pdfTransText', "Hamiltonian");
    await drag(page, from, to);
    await page.mouse.move(to.x + to.w + 14, to.y + to.h + 14, { steps: 4 });
    const tip = page.locator(".plainTip");
    await tip.waitFor();
    assertEq(await tip.getByRole("button", { name: "Translate selection", exact: true }).count(), 0, "a translation is not offered for translating");
    assert(await tip.getByRole("button", { name: "Link this reference to a paper (DOI / arXiv / existing PDF)", exact: true }).count(), "Link is named");
    // A colour, by the keyboard: Enter on the focused swatch.
    await tip.locator('[data-guide="pdf.highlightColor"]').first().focus();
    await page.keyboard.press("Enter");
    await until(async () => (await highlights("continues with the Hamiltonian")).length === 1, { what: "the highlight, made by Enter" });
    const [hl] = await highlights("continues with the Hamiltonian");
    const quote = hl.properties.quote;
    assert(!quote.includes("译"), `the original's words: ${quote}`);
    assert(quote.indexOf("The collective Rabi frequency") >= 0 && quote.indexOf("The collective Rabi frequency") < quote.indexOf("continues"),
      `both parts of the cut paragraph, in order: ${quote}`);
    const position = hl.properties.pdf_position;
    assertEq(position.pageNumber, 1);
    assert(position.rects.length >= 5, `a rect per original line of both parts: ${JSON.stringify(position.rects)}`);

    // Hidden, the highlight lies over the original's lines of both columns.
    await page.getByRole("button", { name: "Show original", exact: true }).click();
    await page.locator('[data-page="1"] .pdfTransLayer').waitFor({ state: "detached" });
    const covers = (word) => page.evaluate(([id, word]) => {
      const span = [...document.querySelectorAll('[data-page="1"] .textLayer span')].find((s) => s.textContent.includes(word));
      if (!span) return "no span";
      const b = span.getBoundingClientRect();
      const x = b.x + Math.min(b.width / 2, 20), y = b.y + b.height / 2;
      return [...document.querySelectorAll(`[data-page="1"] div[data-hl-id="${id}"]`)].some((el) => {
        const r = el.getBoundingClientRect();
        return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
      });
    }, [hl.id, word]);
    assertEq(await covers("The collective Rabi"), true, "over the left column's line");
    assertEq(await covers("continues with the"), true, "over the right column's line");
    assertEq(await covers("Ensembles of neutral"), false, "not over the paragraph before");
    await page.getByRole("button", { name: "Show translation", exact: true }).click();
    await page.locator('[data-page="1"] .pdfTransPara').first().waitFor();
    assertNoProblems(page);
  });

  await step("translation: a highlighted translated paragraph still selects, its menu carries the highlight's, selecting it again makes no second one, and a right-click in the selection closes the popup", async () => {
    const highlights = async () => ((await alice.api(`/api/blocks/${pageId}/subtree`)).block.children || [])
      .filter((b) => (b.properties?.quote || "").includes("continues with the Hamiltonian"));
    assertEq((await highlights()).length, 1);
    await keepCopies(page);
    await page.evaluate(() => getSelection().removeAllRanges());
    const from = await wordBox(page, '[data-page="1"] .pdfTransText', "continues");
    const to = await wordBox(page, '[data-page="1"] .pdfTransText', "Hamiltonian");
    // The highlight's rects over the translation take no pointer.
    assertEq(await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest(".pdfTransText"), [from.x + 2, from.y + from.h / 2]),
      true, "the translated text is under the pointer, not the highlight");
    // A right-click: the paragraph's menu, with the highlight's own rows.
    const cut = page.locator('[data-page="1"] .pdfTransText', { hasText: "continues" });
    let menu = await rightClick(cut);
    assertEq(await rows(menu, "the menu of a highlighted paragraph"), "Copy translation,Copy original,Translate again,Show original here,Highlight");
    await menu.getByRole("menuitem", { name: "Highlight", exact: true }).click();
    const flyout = menu.locator(".ctxSubMenu");
    await flyout.getByRole("menuitem", { name: "Delete", exact: true }).waitFor();
    await page.keyboard.press("Escape");
    await menu.waitFor({ state: "detached" });
    // Selected again: the highlight's bar, and no second highlight.
    await drag(page, from, to);
    assertEq(await page.evaluate(() => String(getSelection()).trim()), "continues with the Hamiltonian");
    await page.mouse.move(to.x + to.w + 14, to.y + to.h + 14, { steps: 4 });
    const tip = page.locator(".plainTip");
    await tip.waitFor();
    assert(await tip.getByRole("button", { name: /^Change to / }).count() > 0, "the highlight's own bar: its colours change it");
    // A right-click in the selection: the popup goes, the menu's Copy takes the selection.
    await page.mouse.click(from.x + from.w / 2, from.y + from.h / 2, { button: "right" });
    menu = page.getByRole("menu");
    await menu.waitFor();
    await tip.waitFor({ state: "detached" });
    await page.mouse.move(from.x + 30, from.y + 30, { steps: 4 });
    await page.waitForTimeout(200);
    assertEq(await tip.count(), 0, "the popup does not come back as the hand moves to the menu");
    await menu.getByRole("menuitem", { name: "Copy translation", exact: true }).click();
    assertEq(await copied(), "continues with the Hamiltonian", "the selection's words");
    await page.mouse.click(from.x, from.y - 40);
    assertEq((await highlights()).length, 1, "still one highlight");
    assertNoProblems(page);
  });

  await step("translation: the whole document goes on with the pages after it, each request with the text around it", async () => {
    const start = requests.length;
    const briefsBefore = briefs.length;
    await page.getByRole("button", { name: "Show original", exact: true }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Translate whole document" }).click();
    const later = () => requests.slice(start);
    const sentWith = (s) => later().find((r) => r.texts.some((t) => t.includes(s)));
    await until(() => !!(sentWith("optical tweezers") && sentWith("sixth")), { what: "the second and third pages' requests" });
    assert(!later().some((r) => r.texts.some((t) => t.includes("collective Rabi"))), "the first page is not asked again");
    const second = sentWith("optical tweezers");
    assertEq(second.page_id, pageId, "the request names its page");
    // Its section is the first page's heading; the text before it is the
    // first page's last paragraph, the text after it the third page's.
    assertEq(second.section, "1 Introduction", "the section comes from the page before");
    assert(/wants every detail\.$/.test(second.before || ""), `before: ${JSON.stringify(second.before)}`);
    assert(/^The interaction falls off/.test(second.after || ""), `after: ${JSON.stringify(second.after)}`);
    const third = sentWith("sixth");
    assertEq(third.section, "2 Model", "the third page sits under the second page's heading");
    assert(/interaction terms in it\.$/.test(third.before || ""), `before: ${JSON.stringify(third.before)}`);
    // A new job asks for the brief again, and only its first chunk goes
    // without waiting for it.
    assertEq(briefs.length - briefsBefore, 1, "each job asks for the paper's brief as it starts");
    assertEq(JSON.stringify(later().map((r) => r.wait_brief)), JSON.stringify(later().map((_, n) => n > 0)));
    await page.locator('[data-page="2"]').scrollIntoViewIfNeeded();
    const boxes = page.locator('[data-page="2"] .pdfTransPara .pdfTransText');
    await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:Here the atoms sit")),
      { what: "the second page's translation" });
    assertNoProblems(page);
  });

  await step("translation: the first chunk lands while the paper's brief is made; the job runs on after leaving the PDF, its row, named for the paper, reaching Done, and asks again for the chunk that went without the brief", async () => {
    const up = await alice.upload("/api/uploads", makePdf([longPage()]), "long.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Long paper", source_url: up.source_url } });
    await page.goto(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
    // The server as it is while it makes the paper's brief, until
    // window.__makeBrief(): the brief's request and a chunk that waits for
    // it answer a "brief" stage line, then nothing until it is made; a
    // chunk that does not wait goes on to the route above at once. A route
    // can only answer whole, so the page's fetch is wrapped: released, a
    // request goes on to the routes above.
    await page.evaluate(() => {
      const fetchFirst = window.fetch;
      let make = null;
      const made = new Promise((resolve) => { make = resolve; });
      window.__makeBrief = () => { window.__briefMade = true; make(); };
      window.fetch = function (url, opts) {
        if (!String(url).includes("/ai/translate")) return fetchFirst.apply(this, arguments);
        const brief = String(url).includes("/ai/translate/brief");
        if (!brief && (JSON.parse(opts.body).wait_brief === false || window.__briefMade)) return fetchFirst.apply(this, arguments);
        const line = (s) => new TextEncoder().encode(`${s}\n`);
        const body = new ReadableStream({
          async start(out) {
            opts.signal?.addEventListener("abort", () => out.error(new DOMException("Aborted", "AbortError")));
            out.enqueue(line('{"stage": "brief"}'));
            await made;
            if (!brief) out.enqueue(line('{"stage": "translate"}'));
            const res = await fetchFirst.call(window, url, opts);
            out.enqueue(new Uint8Array(await res.arrayBuffer()));
            out.close();
          },
        });
        return Promise.resolve(new Response(body, { headers: { "Content-Type": "application/x-ndjson" } }));
      };
    });
    // The server's answer says whether the brief was there; a chunk asked
    // again comes back in new words ("再译:").
    let briefMade = false;
    const answered = new Set();
    respond = (route, body) => {
      const again = answered.has(JSON.stringify(body.texts));
      answered.add(JSON.stringify(body.texts));
      const translations = body.texts.map((t) => `${again ? "再译" : "译"}:${t}`);
      return route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify({ translations, model: "demo:model", cached: false, failed: [], brief: briefMade })}\n` });
    };
    const start = requests.length;
    const briefsBefore = briefs.length;
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
    await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:Paragraph one")),
      { what: "the first chunk's text, while the brief is being made" });
    const pill = page.locator(".statusPill .pillText", { hasText: "Reading the paper first" });
    await pill.waitFor({ timeout: 5000 });
    const first = requests.slice(start);
    assertEq(first.length, 1, "the second chunk waits for the brief");
    assertEq(first[0].wait_brief, false);
    assertEq(first[0].texts.length, 10);
    // Home: the viewer goes and the pill with it; the job runs on.
    await page.locator('[data-guide="header.home"]').first().click();
    await pill.waitFor({ state: "detached", timeout: 5000 });
    await page.click("button[aria-label='Background tasks']");
    // The row names the page, the paper and the language.
    const row = page.locator(".taskRow", { hasText: "Translate p. 1 of “Long paper” into" });
    await row.waitFor();
    assert(!(await row.evaluate((el) => el.classList.contains("done"))), "the job still runs");
    assert((await row.innerText()).includes("reading the paper"), await row.innerText());
    // Made: the waiting chunk translates with the brief, and the first is
    // asked again, with it.
    briefMade = true;
    await page.evaluate(() => window.__makeBrief());
    await until(() => row.evaluate((el) => el.classList.contains("done")), { what: "the job's row done" });
    assert((await row.innerText()).includes("Done"), await row.innerText());
    const sent = requests.slice(start);
    assertEq(sent.length, 3, `the two chunks and the first again: ${JSON.stringify(sent.map((r) => r.texts.length))}`);
    assertEq(sent[1].wait_brief, true);
    assertEq(sent[1].texts.length, 2);
    assertEq(JSON.stringify(sent[2].texts), JSON.stringify(sent[0].texts), "the chunk that went without the brief is asked again");
    assertEq(sent[2].wait_brief, true);
    assertEq(briefs.length - briefsBefore, 1, "the brief was asked for once");
    // Back on the paper, its translation is there without a click, the
    // first chunk's in its new words.
    await page.keyboard.press("Escape");
    await page.locator(".tabStrip .tab", { hasText: "Long paper" }).first().click();
    await waitForPdf(page, 1);
    await until(async () => {
      const texts = await boxes.allTextContents();
      return texts.some((t) => t.startsWith("再译:Paragraph one")) && texts.some((t) => t.startsWith("译:Paragraph twelve"));
    }, { what: "the paper's translation, back on it" });
    await page.getByRole("button", { name: "Show original", exact: true }).waitFor();
    respond = null;
    assertNoProblems(page);
  });

  await step("translation: a refusal of a busy account waits instead of stopping; a paragraph the server could not translate shows the original until Try again", async () => {
    const up = await alice.upload("/api/uploads", makePdf([stubbornPage()]), "stubborn.pdf", "application/pdf");
    stubbornDoc = up.doc_id;
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Stubborn", source_url: up.source_url } });
    await page.goto(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
    // The first request is refused as the server refuses one while the
    // account's AI calls are all in use; asked again a second later, the
    // reply names the stubborn paragraph as failed (sent back as itself).
    const start = requests.length;
    const asked = [];
    let refused = false;
    respond = (route, body) => {
      asked.push(Date.now());
      if (!refused) {
        refused = true;
        return route.fulfill({ status: 429, headers: { "Retry-After": "1" }, contentType: "application/json",
          body: JSON.stringify({ detail: "Too many AI requests are running for your account at once (at most 6)." }) });
      }
      const failed = body.texts.flatMap((t, i) => (t.startsWith("A stubborn") ? [i] : []));
      const translations = body.texts.map((t, i) => (failed.includes(i) ? t : `译:${t}`));
      return route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify({ translations, model: "demo:model", cached: false, failed })}\n` });
    };
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
    await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:A willing")),
      { what: "the willing paragraph, translated after the wait" });
    assertEq(requests.length - start, 2, "refused once, then asked again");
    assert(asked[1] - asked[0] >= 900, `asked again after the server's Retry-After, not at once: ${asked[1] - asked[0]} ms`);
    const pill = page.locator(".statusPill", { hasText: "1 paragraph could not be translated" });
    await pill.waitFor({ timeout: 5000 });
    assert(!(await boxes.allTextContents()).some((t) => t.includes("stubborn")), "the stubborn paragraph shows its original");
    // A quiet mark beside it stays once the pill has gone, and opens its menu.
    const mark = page.locator('[data-page="1"] .pdfTransFailed');
    assertEq(await mark.count(), 1, "one mark");
    const beside = await page.evaluate(() => {
      const m = document.querySelector('[data-page="1"] .pdfTransFailed').getBoundingClientRect();
      const span = [...document.querySelectorAll('[data-page="1"] .textLayer span')].find((s) => s.textContent.includes("A stubborn"));
      const s = span.getBoundingClientRect();
      return m.right <= s.left + 1 && m.top <= s.top + s.height && m.bottom >= s.top;
    });
    assert(beside, "the mark stands left of the stubborn paragraph's lines");
    await mark.click();
    const menu = page.getByRole("menu");
    assertEq((await assertMenuRoles(menu, "a failed paragraph's menu")).map((item) => item.text).join(","),
      "Copy original,Translate this paragraph");
    await assertMenuFrom(menu, mark, "below", "a failed paragraph's menu");
    // Asked alone and failing again, it keeps its mark, and the pill says so.
    await menu.getByRole("menuitem", { name: "Translate this paragraph", exact: true }).click();
    const alone = page.locator(".statusPill", { hasText: "This paragraph could not be translated." });
    await alone.waitFor({ timeout: 5000 });
    const focused = requests.slice(start + 2);
    assertEq(focused.length, 1, "one request");
    assertEq(JSON.stringify(focused[0].texts.map((t) => t.slice(0, 11))), JSON.stringify(["A stubborn "]), "for the stubborn paragraph alone");
    assertEq(focused[0].fresh, true);
    assertEq(await mark.count(), 1, "still marked");
    // Try again asks for it once more, and it lands; the mark goes.
    respond = null;
    await alone.locator(".pillRetryBtn", { hasText: "Try again" }).click();
    await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:A stubborn")),
      { what: "the stubborn paragraph, translated on the second try" });
    const again = requests.slice(start + 3);
    assertEq(again.length, 1, "one request");
    assertEq(JSON.stringify(again[0].texts.map((t) => t.slice(0, 11))), JSON.stringify(["A stubborn "]), "for the stubborn paragraph alone");
    await mark.waitFor({ state: "detached" });
    assertNoProblems(page, ["POST /api/ai/translate -> 429"]);
  });

  await step("translation: Settings keeps translations by default, says what the workspace keeps, and clears it, the viewer's too", async () => {
    const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
    assert((await boxes.count()) > 0, "the stubborn paper is translated");
    await openTranslation();
    const keep = page.getByRole("checkbox", { name: "Keep translations", exact: true });
    assertEq(await keep.isChecked(), true, "on by default");
    const stats = page.locator('.settingRow[data-setting="Kept in this workspace"]');
    await until(async () => (await stats.innerText()).includes("42 paragraphs in 2 documents (37 KB)"),
      { what: "what the workspace keeps" });
    await stats.getByRole("button", { name: "Clear", exact: true }).click();
    await page.locator(".confirmModal .reportModalBtns button", { hasText: "Clear" }).click();
    await until(() => forgotten.includes("/api/translations"), { what: "the DELETE of the workspace's kept translations" });
    await until(async () => (await stats.innerText()).includes("Nothing kept yet"), { what: "nothing kept any more" });
    assert(await stats.getByRole("button", { name: "Clear", exact: true }).isDisabled(), "nothing left to clear");
    await keep.uncheck();
    await until(async () => (await alice.api("/api/prefs/profile")).value?.translateKeep === false,
      { what: "the switch saved to the account" });
    await page.getByRole("button", { name: "Close settings", exact: true }).click();
    // What the viewer showed of the cleared translations goes too, so the
    // next click asks the server again.
    await until(async () => (await boxes.count()) === 0, { what: "the translation gone from the page" });
    const start = requests.length;
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:A willing")),
      { what: "the paper translated again" });
    assert(requests.length > start, "asked the server again");
    assertNoProblems(page);
  });

  await step("translation: the translate menu greys what has nothing to do, and Forget this paper's translations, a danger row apart, asks first; then the server forgets the paper and the next click translates it afresh", async () => {
    const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
    assert((await boxes.count()) > 0, "the stubborn paper is translated");
    const start = requests.length;
    const openMenu = async () => {
      await page.getByRole("button", { name: "Show original", exact: true }).click({ button: "right" });
      const menu = page.getByRole("menu", { name: "Translate", exact: true });
      await menu.waitFor();
      return menu;
    };
    try {
      let menu = await openMenu();
      assertEq((await assertMenuRoles(menu, "the translate menu")).map((item) => item.text).join(","),
        "Translate this page,Translate whole document,Show original,Translate into,Translation settings…,Forget this paper's translations…");
      // The one page is translated: both rows that would start a job say so.
      const whole = menu.getByRole("menuitem", { name: "Translate whole document" });
      assert(await whole.isDisabled(), "nothing left to translate");
      assertEq(await whole.getAttribute("title"), "Every page is translated");
      assertEq(await menu.getByRole("menuitem", { name: /^Show original/ }).locator(".ctxMenuKey").innerText(), "Ctrl+Shift+O", "the toggle's key");
      const forget = menu.getByRole("menuitem", { name: "Forget this paper's translations…" });
      assert(await forget.evaluate((el) => el.classList.contains("danger") && el.previousElementSibling?.getAttribute("role") === "separator"),
        "a danger row in a group of its own");
      // The language flyout: the languages as radios, the one in use checked.
      await menu.getByRole("menuitem", { name: "Translate into" }).click();
      const langs = await assertMenuRoles(menu.locator(".ctxSubMenu"), "the language flyout", 1);
      assert(langs.length > 5 && langs.every((item) => item.role === "menuitemradio"), JSON.stringify(langs));
      assertEq(langs.filter((item) => item.checked === "true").length, 1, "one language checked");
      await forget.click();
      // Asked first, saying what goes and for whom; Cancel forgets nothing.
      const confirm = page.locator(".confirmModal");
      const message = await confirm.locator(".confirmMessage").innerText();
      assert(/every language and model/.test(message) && /everyone in this workspace/.test(message), message);
      assert(await confirm.locator(".confirmIcon.danger").count(), "a destructive action's chip");
      await confirm.locator(".reportModalBtns button", { hasText: "Cancel" }).click();
      await confirm.waitFor({ state: "detached" });
      assertEq(forgotten.includes(`/api/translations/${stubbornDoc}`), false, "nothing forgotten on Cancel");
      assert((await boxes.count()) > 0, "the translation stays");
      menu = await openMenu();
      await menu.getByRole("menuitem", { name: "Forget this paper's translations…" }).click();
      await confirm.locator(".reportModalBtns button", { hasText: "Forget" }).click();
      await until(() => forgotten.includes(`/api/translations/${stubbornDoc}`), { what: "the DELETE of the paper's kept translations" });
      await until(async () => (await boxes.count()) === 0, { what: "the translation gone from the page" });
      await page.getByRole("button", { name: "Translate", exact: true }).click();
      await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:A willing")),
        { what: "the paper translated again" });
      const again = requests.slice(start);
      assert(again.length > 0, "asked the server again");
      assert(again.every((r) => r.keep === false), `not to be kept, the switch being off: ${JSON.stringify(again.map((r) => r.keep))}`);
      assertNoProblems(page);
    } finally {
      await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { translateKeep: true } } });
    }
  });

  await step("translation: Ctrl+Shift+O shows the original and the translation, the palette lists the commands, and a hidden button's ⋯ row opens the translate menu", async () => {
    const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
    await until(async () => (await boxes.count()) > 0, { what: "the paper translated" });
    const strip = page.locator('[data-guide="viewer.tools"]');
    const button = strip.locator('[data-guide="viewer.translate"]');
    const top = async () => (await strip.boundingBox()).y;
    const before = await top();
    await page.keyboard.press("Control+Shift+O");
    await until(async () => (await boxes.count()) === 0, { what: "the original shown" });
    await until(async () => (await button.getAttribute("aria-label")) === "Show translation", { what: "the button names its next click" });
    await page.keyboard.press("Control+Shift+O");
    await until(async () => (await boxes.count()) > 0, { what: "the translation shown again" });
    await until(async () => (await button.getAttribute("aria-label")) === "Show original", { what: "the button names its next click" });
    assertEq(await top(), before, "the strip did not move");
    // The palette lists the three, the toggle with its key; the page and the
    // document are translated already, so they are left out.
    await page.keyboard.press("Control+Shift+p");
    const dialog = page.getByRole("dialog", { name: "Command palette" });
    await dialog.getByRole("textbox", { name: "Type a command" }).fill(">translat");
    const toggle = dialog.getByRole("option", { name: /Show or hide the translation/ });
    await toggle.waitFor();
    assert((await toggle.textContent()).includes("Ctrl+Shift+O"), await toggle.textContent());
    assertEq(await dialog.getByRole("option", { name: /Translate whole document/ }).count(), 0, "nothing left to translate");
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "detached" });
    // Hidden, the button is a row of the strip's ⋯ once nothing pins it on
    // the strip, and that row opens the menu the button's right-click does.
    const buttonSetting = async (on) => {
      await openTranslation();
      const box = page.getByRole("checkbox", { name: "Translation button", exact: true });
      if (on) await box.check(); else await box.uncheck();
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
    };
    await buttonSetting(false);
    try {
      await button.waitFor({ state: "visible" });
      await page.keyboard.press("Control+Shift+O");
      await until(async () => (await boxes.count()) === 0, { what: "the original shown" });
      await button.waitFor({ state: "detached" });
      await strip.getByRole("button", { name: "More tools", exact: true }).click();
      const more = page.getByRole("menu", { name: "More tools", exact: true });
      await more.getByRole("menuitem", { name: "Translate", exact: true }).click();
      await more.waitFor({ state: "detached" });
      const menu = page.getByRole("menu", { name: "Translate", exact: true });
      assertEq((await assertMenuRoles(menu, "the translate menu from the ⋯ row")).map((item) => item.text).join(","),
        "Translate this page,Translate whole document,Show translation,Translate into,Translation settings…,Forget this paper's translations…");
      await menu.getByRole("menuitem", { name: /^Show translation/ }).click();
      await until(async () => (await boxes.count()) > 0, { what: "the translation shown from the menu" });
      // Shown, the button is on the strip again, whatever its mode.
      await button.waitFor();
      assertNoProblems(page);
    } finally {
      await buttonSetting(true);
    }
  });

  await step("translation: a viewer of the workspace translates, but is offered neither Forget this paper's translations nor Clear", async () => {
    kept = { documents: 1, paragraphs: 3, bytes: 2048 };
    asViewer = true;
    try {
      await page.reload();
      await waitForPdf(page, 1);
      const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
      await page.getByRole("button", { name: "Translate", exact: true }).click();
      await until(async () => (await boxes.count()) > 0, { what: "the paper translated for a viewer" });
      await page.getByRole("button", { name: "Show original", exact: true }).click({ button: "right" });
      const menu = page.getByRole("menu", { name: "Translate" });
      await menu.getByRole("menuitem", { name: "Show original" }).waitFor();
      assertEq(await menu.getByRole("menuitem", { name: /^Forget/ }).count(), 0, "no Forget for a viewer");
      await page.keyboard.press("Escape");
      await openTranslation();
      const stats = page.locator('.settingRow[data-setting="Kept in this workspace"]');
      await until(async () => (await stats.innerText()).includes("3 paragraphs in 1 document"), { what: "what the workspace keeps" });
      assertEq(await stats.getByRole("button", { name: "Clear", exact: true }).count(), 0, "no Clear for a viewer");
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      assertNoProblems(page);
    } finally {
      asViewer = false;
      await page.reload();
      await waitForPdf(page, 1);
    }
  });

  await step("translation: a page's references stay in the original, selectable beside the translation, and Translate references too asks for them", async () => {
    const up = await alice.upload("/api/uploads", makePdf([referencesPage()]), "references.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "References", source_url: up.source_url } });
    await page.goto(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
    const start = requests.length;
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
    await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:The atoms are held")),
      { what: "the paragraph over the list, translated" });
    assert(!requests.slice(start).flatMap((r) => r.texts).some((t) => t.includes("Phys. Rev.")), "the list goes to no model");
    assert(!(await boxes.allTextContents()).some((t) => t.includes("Phys. Rev.")), "nothing is drawn over it");

    // The page shows a translation, and the list is still the original's:
    // the pointer reaches its text, a drag selects it, the popup opens on it.
    const under = (span) => span.evaluate((el) => {
      const b = el.getBoundingClientRect();
      const at = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
      return { list: !!at?.closest(".textLayer"), translation: !!at?.closest(".pdfTransText") };
    });
    const entry = page.locator('[data-page="1"] .textLayer span', { hasText: "Phys. Rev. Lett. 98" }).first();
    await entry.waitFor({ state: "attached" });
    await until(() => page.locator('[data-page="1"] .textLayer .transMasked').count().then((n) => n > 0),
      { what: "the text under the translation standing down" });
    await entry.scrollIntoViewIfNeeded();
    assertEq(JSON.stringify(await under(entry)), JSON.stringify({ list: true, translation: false }), "the entry takes the pointer");
    const body = page.locator('[data-page="1"] .textLayer span', { hasText: "array keeps its order" }).first();
    assertEq(JSON.stringify(await under(body)), JSON.stringify({ list: false, translation: true }), "the translation takes it where one is drawn");
    const box = await entry.boundingBox();
    await page.mouse.move(box.x + 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2, { steps: 8 });
    await page.mouse.up();
    assert((await page.evaluate(() => String(getSelection()))).includes("Phys. Rev. Lett. 98"), "the drag selected the entry");
    // The popup waits for the mouse to move on from where it was released.
    await page.mouse.move(box.x + box.width + 12, box.y + box.height + 12, { steps: 4 });
    await page.locator(".plainTip").waitFor();
    // A press outside closes it, the selection with it.
    await page.mouse.click(box.x + box.width + 40, box.y - 2);
    await page.locator(".plainTip").waitFor({ state: "detached" });

    // A drag from the first entry, left original, up into the translation is
    // one selection over both, by block: the popup opens, and the highlight
    // has both blocks' original lines and words.
    const quoted = async (s) => ((await alice.api(`/api/blocks/${created.id}/subtree`)).block.children || [])
      .filter((b) => (b.properties?.quote || "").includes(s));
    const from = await wordBox(page, '[data-page="1"] .textLayer', "Lett. 96");
    const to = await wordBox(page, '[data-page="1"] .pdfTransText', "array");
    await drag(page, from, to);
    await page.mouse.move(to.x + to.w + 14, to.y + to.h + 14, { steps: 4 });
    const tip = page.locator(".plainTip");
    await tip.waitFor();
    await tip.locator('[data-guide="pdf.highlightColor"]').first().click();
    await until(async () => (await quoted("Lett. 96")).length === 1, { what: "the highlight over the entry and the paragraph" });
    const crossed = (await quoted("Lett. 96"))[0].properties;
    assert(crossed.quote.startsWith("The atoms are held apart") && !crossed.quote.includes("译") && !crossed.quote.includes("Lett. 97"),
      `the paragraph's original, then the entry: ${crossed.quote}`);
    assertEq(crossed.pdf_position.rects.length, 3, "the paragraph's two lines and the entry's one");

    const before = requests.length;
    await page.getByRole("button", { name: "Show original", exact: true }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Translate references too" }).click();
    await until(async () => (await boxes.allTextContents()).filter((t) => t.includes("Phys. Rev.")).length === ENTRIES.length,
      { what: "the list, translated" });
    const asked = requests.slice(before).flatMap((r) => r.texts);
    assertEq(asked.filter((t) => t.includes("Phys. Rev.")).length, ENTRIES.length, "each entry is asked for");
    assert(!asked.some((t) => t.includes("The atoms are held")), "the paragraph is not asked again");
    await page.getByRole("button", { name: "Show original", exact: true }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Show original" }).waitFor();
    assertEq(await page.getByRole("menuitem", { name: "Translate references too" }).count(), 0, "nothing is left to ask for");
    await page.keyboard.press("Escape");
    assertNoProblems(page);
  });

  await step("translation: every mask lies under every text, a crop between them, and nothing stands between a crop and the layer", async () => {
    const up = await alice.upload("/api/uploads", makePdf([paintedPage()]), "painted.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Painted", source_url: up.source_url } });
    await page.goto(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    const paras = page.locator('[data-page="1"] .pdfTransPara');
    await until(async () => (await page.locator('[data-page="1"] .pdfTransMath:not(.bare)').count()) > 0 && (await paras.count()) >= 3,
      { what: "the blocks, with the formula drawn" });
    await page.waitForFunction(() => !document.getAnimations().some((a) => a.animationName === "pdfTransFade"));
    // What makes a stacking context, read off the elements as the browser has them.
    const stacks = (el) => {
      const s = getComputedStyle(el);
      const reasons = [];
      if (s.position !== "static" && s.zIndex !== "auto") reasons.push("z-index");
      if (s.opacity !== "1") reasons.push("opacity");
      if (s.transform !== "none") reasons.push("transform");
      if (s.filter !== "none") reasons.push("filter");
      if (s.contain !== "none") reasons.push("contain");
      if (s.overflow !== "visible") reasons.push("overflow");
      if (s.isolation !== "auto") reasons.push("isolation");
      if (s.mixBlendMode !== "normal") reasons.push("blend");
      if (s.willChange !== "auto") reasons.push("will-change");
      return reasons;
    };
    const between = await page.evaluate(`(${(found) => {
      const stacking = new Function(`return ${found}`)();
      const crop = document.querySelector('[data-page="1"] .pdfTransMath:not(.bare)');
      const out = [];
      for (let el = crop.parentElement; el && !el.classList.contains("pdfTransLayer"); el = el.parentElement) {
        const why = stacking(el);
        if (why.length) out.push(`${el.className}: ${why}`);
      }
      return out;
    }})(${JSON.stringify(stacks.toString())})`);
    assertEq(between.join("; "), "", "from the crop up to the layer");
    // The crop itself lies over the masks (-2) and under the text: a tall one
    // hangs into the lines around it, which stay in sight over its paper.
    assertEq(await page.locator('[data-page="1"] .pdfTransMath:not(.bare)').first().evaluate((el) => getComputedStyle(el).zIndex), "-1");
    // The last block's mask laid over the first block's text, taking the
    // pointer: the hit test goes in paint order, and the text is on top.
    const order = await page.evaluate(() => {
      const wrap = document.querySelector('[data-page="1"]');
      const texts = [...wrap.querySelectorAll(".pdfTransText")];
      const span = texts[0].firstElementChild.getClientRects()[0];
      const x = span.left + span.width / 2, y = span.top + span.height / 2;
      const mask = wrap.querySelector(".pdfTransPara:last-of-type .pdfTransMask");
      const para = mask.parentElement.getBoundingClientRect();
      const was = mask.getAttribute("style");
      const k = mask.offsetWidth / mask.getBoundingClientRect().width;
      mask.style.left = `${(span.left - para.left) * k}px`;
      mask.style.top = `${(span.top - para.top) * k}px`;
      mask.style.pointerEvents = "auto";
      const hit = document.elementsFromPoint(x, y).map((el) => el.className);
      mask.setAttribute("style", was);
      return hit;
    });
    const text = order.findIndex((c) => String(c).includes("pdfTransText"));
    const mask = order.findIndex((c) => String(c).includes("pdfTransMask"));
    assert(text >= 0 && mask > text, `the text over a later block's mask: ${order.join(" | ")}`);
    assertNoProblems(page);
  });

  await step("translation: a tinted ground and a coloured ink come from the page, once, and a heading keeps its shape", async () => {
    const heading = page.locator('[data-page="1"] .pdfTransPara', { hasText: "译:Methods" });
    const panel = page.locator('[data-page="1"] .pdfTransPara', { hasText: "译:The panel holds" });
    const plain = page.locator('[data-page="1"] .pdfTransPara', { hasText: "译:The first paragraph" });
    await until(() => panel.locator(".pdfTransMask.tinted").count().then((n) => n > 0), { what: "the panel's ground read" });
    assertEq(await panel.evaluate((el) => el.style.getPropertyValue("--ground")), "rgb(236 236 236)", "the panel's own grey");
    assertEq(await plain.evaluate((el) => el.style.getPropertyValue("--ground") + el.className.includes("inked")), "false", "paper has no paint");
    assertEq(await plain.locator(".pdfTransMask.tinted").count(), 0);
    assertEq(await panel.locator(".pdfTransInk").count(), 0, "black ink is the page's usual");

    assert(await heading.evaluate((el) => el.classList.contains("inked") && /rgb\(0 0 2\d\d\)/.test(el.style.getPropertyValue("--ink"))),
      `the heading's blue: ${await heading.evaluate((el) => el.style.getPropertyValue("--ink"))}`);
    const set = await heading.evaluate((el) => {
      const text = el.querySelector(".pdfTransText"), style = getComputedStyle(text), mask = el.querySelector(".pdfTransMask");
      return {
        ink: getComputedStyle(text.querySelector(".pdfTransInk")).color, weight: Number(style.fontWeight), align: style.textAlign,
        wider: el.getBoundingClientRect().width / mask.getBoundingClientRect().width,
      };
    });
    assert(set.ink.startsWith("rgb(0, 0, 2"), `its words: ${set.ink}`);
    assert(set.weight >= 600, `bold: ${set.weight}`);
    assertEq(set.align, "center");
    assert(set.wider > 3, `it has the column's width to fill: ${set.wider}`);

    assertEq(await page.evaluate(() => window.transReads), 1, "one read for the page");
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await page.waitForTimeout(700); // the sharp redraw lands
    await page.getByRole("button", { name: "Show original", exact: true }).click();
    await page.locator('[data-page="1"] .pdfTransLayer').waitFor({ state: "detached" });
    await page.getByRole("button", { name: "Show translation", exact: true }).click();
    await panel.locator(".pdfTransMask.tinted").first().waitFor();
    assertEq(await page.evaluate(() => window.transReads), 1, "a zoom and a remount read nothing");
    assertNoProblems(page);
  });

  await step("translation: a tinted mask matches its ground under each treatment, as a crop does", async () => {
    // A very short text, so a mask lies bare past its end.
    await page.getByRole("button", { name: "Show original", exact: true }).click();
    const png = async () => (await page.screenshot()).toString("base64");
    const colours = (shot, points) => page.evaluate(async ([data, pts]) => {
      const img = new Image();
      img.src = `data:image/png;base64,${data}`;
      await img.decode();
      const c = document.createElement("canvas");
      c.width = img.width; c.height = img.height;
      const ctx = c.getContext("2d", { willReadFrequently: false });
      ctx.drawImage(img, 0, 0);
      return pts.map(([x, y]) => [...ctx.getImageData(Math.round(x), Math.round(y), 1, 1).data].slice(0, 3));
    }, [shot, points]);
    await page.getByRole("button", { name: "Show translation", exact: true }).click();
    const panelMask = page.locator('[data-page="1"] .pdfTransPara', { hasText: "译:The panel holds" }).locator(".pdfTransMask").last();
    await panelMask.waitFor();
    await panelMask.scrollIntoViewIfNeeded();
    await page.waitForTimeout(800); // the page's sharp redraw
    const points = await panelMask.evaluate((mask) => {
      // Bare mask, right of the last line's words; bare panel, below the mask.
      const box = mask.getBoundingClientRect();
      const text = mask.parentElement.querySelector(".pdfTransText > span").getClientRects();
      const end = Math.max(...[...text].filter((r) => r.bottom > box.top + 2).map((r) => r.right));
      return { inside: [Math.min(box.right - 3, Math.max(end + 6, box.right - 12)), box.top + box.height - 3], outside: [box.right - 3, box.bottom + 6], gap: box.right - end };
    });
    assert(points.gap > 10, `the mask lies bare past the words: ${JSON.stringify(points)}`);
    // A corner of the formula's crop, which is the panel's ground: with the
    // block's masks out of the way the crop stands on nothing of the page, as
    // it does where the text runs past them.
    const crop = await page.locator('[data-page="1"] .pdfTransMath:not(.bare)').first().evaluate((el) => {
      const box = el.getBoundingClientRect();
      return [box.left + 1, box.top + 1];
    });
    const masks = page.locator('[data-page="1"] .pdfTransPara', { hasText: "译:The panel holds" }).locator(".pdfTransMask");
    const treat = (theme, flip) => page.evaluate(([name, on]) => {
      document.documentElement.setAttribute("data-theme", name);
      document.querySelector(".pdfViewer").classList.toggle("pdfDark", on);
    }, [theme, flip]);
    const theme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
    const flipped = await page.evaluate(() => document.querySelector(".pdfViewer").classList.contains("pdfDark"));
    for (const [name, flip] of [["light", false], ["light", true], ["gamma-dark", false], ["sepia", false], ["solarized", false], ["gray", false], ["gamma-light", false]]) {
      await treat(name, flip);
      await page.waitForTimeout(150);
      const [inside, outside] = await colours(await png(), [points.inside, points.outside]);
      const off = (a) => Math.max(...a.map((v, c) => Math.abs(v - outside[c])));
      assert(off(inside) <= 1, `${name}${flip ? " flipped" : ""}: mask ${inside} against the panel ${outside}`);
      await masks.evaluateAll((els) => els.forEach((el) => { el.style.visibility = "hidden"; }));
      const [corner] = await colours(await png(), [crop]);
      await masks.evaluateAll((els) => els.forEach((el) => { el.style.visibility = ""; }));
      assert(off(corner) <= 2, `${name}${flip ? " flipped" : ""}: crop ${corner} against the panel ${outside}`);
    }
    await treat(theme, flipped);
    assertNoProblems(page);
  });

  await step("translation: streaming masks only the lines reached, at the page's body size, the percentage counts it, and only a landing fades", async () => {
    const up = await alice.upload("/api/uploads", makePdf([longPage("Passage")]), "motion.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Motion", source_url: up.source_url } });
    await page.goto(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
    // The server's stream, line by line: each request waits for what the step
    // says to it (window.transRequests[k].say and .end).
    await page.evaluate(() => {
      const fetchFirst = window.fetch;
      window.transRequests = [];
      window.fetch = function (url, opts) {
        if (!String(url).endsWith("/api/ai/translate")) return fetchFirst.apply(this, arguments);
        const body = new ReadableStream({
          start(out) {
            window.transRequests.push({
              texts: JSON.parse(opts.body).texts,
              say: (line) => out.enqueue(new TextEncoder().encode(`${JSON.stringify(line)}\n`)),
              end: () => out.close(),
            });
          },
        });
        return Promise.resolve(new Response(body, { headers: { "Content-Type": "application/x-ndjson" } }));
      };
    });
    const say = (k, line) => page.evaluate(([n, l]) => window.transRequests[n].say(l), [k, line]);
    const land = (k, translations) => page.evaluate(([n, ts]) => {
      window.transRequests[n].say({ translations: ts, model: "demo:model", cached: false, failed: [], brief: true });
      window.transRequests[n].end();
    }, [k, translations]);
    // A job starting moves nothing on the strip: its progress is a bar under
    // the button's glyph, and the button says its click stops the job.
    const strip = page.locator('[data-guide="viewer.tools"]');
    const stripBox = async () => JSON.stringify(await strip.boundingBox());
    const idle = await stripBox();
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    await until(() => page.evaluate(() => window.transRequests.length === 2), { what: "both chunks asked for" });
    const [first, second] = await page.evaluate(() => window.transRequests.map((r) => r.texts));
    assertEq(first.length + second.length, 12);
    const stop = page.getByRole("button", { name: "Stop translating", exact: true });
    await stop.locator(".pdfTransBar").waitFor();
    assertEq(await stripBox(), idle, "the strip stays where it was as the job starts");
    await stop.hover();
    assert(await stop.locator(".pdfTransStop").isVisible(), "the stop glyph under the pointer");
    assert(!(await stop.locator(".pdfTransGlyph").isVisible()), "in the glyph's place");
    await page.mouse.move(5, 5);

    // Text streaming onto the first paragraph, nothing back yet.
    const percent = async () => Number(await page.locator(".pdfTransBar").getAttribute("data-progress"));
    const typing = page.locator('[data-page="1"] .pdfTransPara.streaming');
    await say(0, { i: [0], text: `译:${first[0].slice(0, 30)}` });
    await typing.waitFor();
    assertEq(await typing.locator(".pdfTransMask").count(), 1, "the line the text has reached is masked");
    assertEq(await typing.locator(".pdfTransPendingLine").count(), 1, "the line after it stays washed");
    await until(async () => (await percent()) > 0, { what: "the percentage moving before anything landed" });
    const early = await percent();
    await say(0, { i: [0], text: `译:${first[0]} ${first[0]}` });
    await until(() => typing.locator(".pdfTransMask").count().then((n) => n === 2), { what: "the second line reached" });
    assertEq(await typing.locator(".pdfTransPendingLine").count(), 0);
    await until(async () => (await percent()) > early, { what: "the percentage counting on" });

    // The first chunk lands, longer than its lines, so the page's body has a size.
    const was = await percent();
    await land(0, first.map((t) => `译:${t} ${t}`));
    await until(() => page.locator('[data-page="1"] .pdfTransPara:not(.streaming) .pdfTransText').count().then((n) => n === first.length),
      { what: "the first chunk's paragraphs" });
    assert((await percent()) >= was, "a landing replaces the streamed share, and never lowers it");
    await until(() => page.evaluate((n) => window.transFades.length >= n, first.length), { what: "the fades starting" });
    const fades = await page.evaluate(() => window.transFades);
    assertEq(fades.length, first.length, `each landed paragraph fades once: ${fades.join(" | ")}`);
    assertEq(fades.filter((c) => c.includes("streamed")).length, 1, "the one that streamed fades from its words");
    const fits = (await page.locator('[data-page="1"] .pdfTransPara .pdfTransText').evaluateAll((els) => els.map((el) => Number(el.style.getPropertyValue("--fit")))))
      .sort((a, b) => a - b);
    assert(fits[0] < 1, `the text had to shrink: ${fits}`);
    const body = fits[Math.floor((fits.length - 1) / 4)];

    await say(1, { i: [0], text: `译:${second[0].slice(0, 12)}` });
    await typing.waitFor();
    assertEq(await typing.locator(".pdfTransText").evaluate((el) => Number(el.style.getPropertyValue("--fit"))), body, "it starts at what the page's body takes");
    await land(1, second.map((t) => `译:${t} ${t}`));
    await typing.waitFor({ state: "detached" });
    await page.locator(".pdfTransBar").waitFor({ state: "detached" });
    assertEq(await stripBox(), idle, "nor as it ends");

    // Shown again, nothing moves; the fade never sat on the paragraph.
    await until(() => page.evaluate((n) => window.transFades.length === n, first.length + second.length), { what: "the last chunk's fades" });
    const landed = await page.evaluate(() => window.transFades.length);
    await page.getByRole("button", { name: "Show original", exact: true }).click();
    await page.locator('[data-page="1"] .pdfTransLayer').waitFor({ state: "detached" });
    await page.getByRole("button", { name: "Show translation", exact: true }).click();
    await page.locator('[data-page="1"] .pdfTransPara').first().waitFor();
    await page.waitForTimeout(100);
    assertEq(await page.evaluate(() => window.transFades.length), landed, "a mount does not animate");
    assertEq(await page.locator(".pdfTransPara.landing").count(), 0);

    // Reduced motion ends the fade at once, by the app's one rule.
    await page.emulateMedia({ reducedMotion: "reduce" });
    const stilled = await page.locator('[data-page="1"] .pdfTransPara').first().evaluate((para) => {
      para.classList.add("landing");
      const fade = getComputedStyle(para.querySelector(".pdfTransText"));
      const out = { name: fade.animationName, duration: fade.animationDuration, para: getComputedStyle(para).animationName };
      para.classList.remove("landing");
      return out;
    });
    await page.emulateMedia({ reducedMotion: null });
    assertEq(JSON.stringify(stilled), JSON.stringify({ name: "pdfTransFade", duration: "0.001s", para: "none" }));
    assertNoProblems(page);
  });

  // A page of `opening`'s twelve paragraphs, open, its translate requests held.
  // `prepare(id)` runs before it opens.
  const heldPage = async (opening, prepare = async () => {}) => {
    const up = await alice.upload("/api/uploads", makePdf([longPage(opening)]), `${opening.toLowerCase()}.pdf`, "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: opening, source_url: up.source_url } });
    await prepare(created.id);
    await page.goto(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
    const land = await holdRequests(page);
    return { id: created.id, land };
  };
  const layerCap = () => page.locator('[data-page="1"] .pdfTransLayer').evaluate((el) => el.style.getPropertyValue("--cap"));
  const bodySizes = () => page.locator('[data-page="1"] .pdfTransPara.bodyText .pdfTransText')
    .evaluateAll((els) => els.map((el) => getComputedStyle(el).fontSize).join());

  await step("translation: a page's settled cap holds through a remount while a job works on it again", async () => {
    const { land } = await heldPage("Settled");
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    await until(() => page.evaluate(() => window.transRequests.length === 2), { what: "both chunks asked for" });
    const [first, second] = await page.evaluate(() => window.transRequests.map((r) => r.texts));
    await land(0, first.map((t) => `译:${t} ${t}`));
    await land(1, second.map((t) => `译:${t} ${t}`), [0]); // one the server could not translate
    await page.locator(".pdfTransBar").waitFor({ state: "detached" });
    const cap = await layerCap();
    assert(cap && Number(cap) < 1, `the page settled under a cap: ${cap}`);
    const sizes = await bodySizes();

    // The page is not done: a click asks for that paragraph, and the job works.
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    await until(() => page.evaluate(() => window.transRequests.length === 3), { what: "the paragraph asked again" });
    const menu = async (item) => {
      await page.getByRole("button", { name: "Stop translating", exact: true }).click({ button: "right" });
      // The toggle's row carries its key after its name.
      await page.getByRole("menuitem", { name: new RegExp(`^${item}`) }).click();
    };
    await menu("Show original");
    await page.locator('[data-page="1"] .pdfTransLayer').waitFor({ state: "detached" });
    await menu("Show translation");
    await page.locator('[data-page="1"] .pdfTransPara .pdfTransText').first().waitFor();
    assertEq(await layerCap(), cap, "a layer mounted while the job works writes the cap the page settled at");
    assertEq(await bodySizes(), sizes, "and nothing that had landed moves");
    const third = await page.evaluate(() => window.transRequests[2].texts);
    await land(2, third.map((t) => `译:${t}`));
    await page.locator(".pdfTransBar").waitFor({ state: "detached" });
    assertNoProblems(page);
  });

  await step("translation: a job that lands while a held citation keeps the hidden PDF's page up measures once the PDF is back", async () => {
    const quote = "Hidden one says how the atoms in the lattice";
    const cite = (id) => alice.api(`/api/chats/${id}`, { method: "PUT", body: { messages: [
      { role: "user", text: "Where?" },
      { role: "ai", text: `Here [p. 1](/?page=${id}&pdf_page=1&quote=${encodeURIComponent(quote)}).` },
    ] } });
    const { id, land } = await heldPage("Hidden", cite);
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    await until(() => page.evaluate(() => window.transRequests.length === 2), { what: "both chunks asked for" });
    await page.getByRole("link", { name: "p. 1", exact: true }).click();
    await page.locator('[data-page="1"] .pdfCitationMark').first().waitFor();
    const palette = async () => {
      await page.keyboard.press("Control+Shift+P");
      const box = page.getByRole("textbox", { name: "Type a command" });
      await box.waitFor();
      await box.fill(">Show or hide the PDF");
      await page.keyboard.press("Enter");
    };
    // Hidden with no click, so the citation stays and holds the page rendered.
    await palette();
    await until(() => page.evaluate(() => document.querySelector(".viewerWrap")?.classList.contains("pdfHidden")), { what: "the PDF hidden" });
    assertEq(await page.locator('[data-page="1"] .pdfTransLayer').count(), 1, "the layer stays mounted under display:none");
    const [first, second] = await page.evaluate(() => window.transRequests.map((r) => r.texts));
    await land(0, first.map((t) => `译:${t} ${t}`));
    await land(1, second.map((t) => `译:${t} ${t}`));
    await until(() => page.locator('[data-page="1"] .pdfTransPara:not(.streaming) .pdfTransText').count().then((n) => n === 12), { what: "every paragraph landed, unseen" });
    // The translation's key does nothing to a PDF that is not on screen.
    const asked = await page.evaluate(() => window.transRequests.length);
    await page.keyboard.press("Control+Shift+O");
    await page.waitForTimeout(200);
    assertEq(await page.locator('[data-page="1"] .pdfTransLayer').count(), 1, "Ctrl+Shift+O hides nothing while the PDF is closed");
    assertEq(await page.evaluate(() => window.transRequests.length), asked, "nor asks for anything");
    await palette();
    await until(() => page.evaluate(() => !document.querySelector(".viewerWrap")?.classList.contains("pdfHidden")), { what: "the PDF shown" });
    const over = () => page.locator('[data-page="1"] .pdfTransPara .pdfTransText')
      .evaluateAll((els) => els.filter((el) => el.offsetHeight > el.parentElement.clientHeight + 1).length);
    await until(async () => (await over()) === 0, { what: "every paragraph measured in its box once the PDF is back" });
    const fits = await page.locator('[data-page="1"] .pdfTransText').evaluateAll((els) => els.map((el) => Number(el.style.getPropertyValue("--fit"))));
    assert(fits.some((f) => f < 1), `they shrank to fit: ${fits}`);
    await page.keyboard.press("Escape");
    await alice.api(`/api/chats/${id}`, { method: "PUT", body: { messages: [] } });
    assertNoProblems(page);
  });

  // A paper opened again (docs/dev/translation.md "A paper opened again").
  // A paper of `pages` on a page of its own, opened: {id, open()}, open()
  // loading its address afresh, as a reload does.
  const openPaper = async (title, file, pages) => {
    const up = await alice.upload("/api/uploads", makePdf(pages), file, "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: title, source_url: up.source_url } });
    const open = async () => {
      await page.goto(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
      await waitForPdf(page, 1);
    };
    await open();
    return { id: created.id, open };
  };
  const firstPage = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
  const shownAgain = (opening) => until(async () => (await firstPage.allTextContents()).some((t) => t.startsWith(`译:${opening} one says`)),
    { what: "page 1's translation" });
  let keptPaper = null;

  await step("translation: a paper this browser left translated comes back after a reload with no request to translate and one lookup, and Export reads the pages not seen", async () => {
    keptPaper = await openPaper("Kept", "kept.pdf", [longPage("Kept"), secondPage(), thirdPage(), longPage("Far"), longPage("Farthest")]);
    await page.getByRole("button", { name: "Translate", exact: true }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Translate whole document" }).click();
    await until(() => [...(keptRows.get(keptPaper.id)?.keys() || [])].some((t) => t.startsWith("Farthest twelve")),
      { what: "page 5 translated and kept" });
    await page.locator(".pdfTransBar").waitFor({ state: "detached" });
    await shownAgain("Kept");
    await page.waitForTimeout(800);
    const asked = requests.length, briefed = briefs.length, looked = lookups.length;

    await keptPaper.open();
    await shownAgain("Kept");
    await page.waitForTimeout(800);
    const mine = lookups.slice(looked);
    assertEq(mine.length, 1, "one lookup");
    assertEq(requests.length, asked, "no request to translate");
    assertEq(briefs.length, briefed, "nor for the paper's brief");
    assertEq(mine[0].page_id, keptPaper.id, "the lookup names the page");
    assert(mine[0].texts.some((t) => t.includes("optical tweezers")), "the page beside it is looked up too");
    assert(!mine[0].texts.some((t) => t.startsWith("Farthest")), "page 5 is not near the view");
    await page.getByRole("button", { name: "Show original", exact: true }).waitFor();

    // Export writes every page translated, page 5 read now, through the lookup alone.
    await page.click("button[aria-label='View']");
    await page.locator(".menuPopover .ctxMenuItem", { hasText: "Export…" }).click();
    const dialog = page.getByRole("dialog", { name: "Export", exact: true });
    await dialog.waitFor();
    await dialog.getByRole("button", { name: "Annotated PDF", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    // The pages not seen are read in as the dialog opens: it counts what the file will hold.
    await dialog.getByText(/replaces the original text on 5 pages/).waitFor();
    const sent = page.waitForRequest((r) => new URL(r.url()).pathname === "/api/jobs/export" && r.method() === "POST");
    const download = page.waitForEvent("download", { timeout: 30000 });
    await dialog.getByRole("button", { name: "Export", exact: true }).click();
    const body = (await sent).postDataJSON();
    await download;
    await dialog.getByRole("heading", { name: "Export ready", exact: true }).waitFor();
    await dialog.getByRole("button", { name: "Done", exact: true }).click();
    await dialog.waitFor({ state: "detached" });
    assertEq(body.translation?.pages.map((p) => p.page).join(), "1,2,3,4,5", "every page goes translated");
    const parts = body.translation.pages[4].blocks.flatMap((b) => b.parts);
    assert(parts.some((p) => typeof p === "string" && p.startsWith("译:Farthest one says")), JSON.stringify(parts));
    assert(lookups.length - looked > 1, "the pages not seen looked up for the export");
    assert(lookups.slice(looked + 1).some((l) => l.texts.some((t) => t.startsWith("Farthest"))));
    assertEq(requests.length, asked, "still no request to translate");
    assertNoProblems(page);
  });

  await step("translation: a paper left in the original stays so after a reload, and nothing is looked up", async () => {
    await page.getByRole("button", { name: "Show original", exact: true }).click();
    await page.locator('[data-page="1"] .pdfTransLayer').waitFor({ state: "detached" });
    const looked = lookups.length;
    await keptPaper.open();
    await page.waitForTimeout(1500);
    assertEq(lookups.length, looked, "no lookup");
    assertEq(await page.locator(".pdfTransPara").count(), 0, "nothing shows");
    await page.getByRole("button", { name: "Translate", exact: true }).waitFor();
    assertNoProblems(page);
  });

  await step("translation: a paper another account of the workspace translated shows nothing until this browser asks", async () => {
    const looked = lookups.length;
    const asked = requests.length;
    try {
      const theirs = await openPaper("Theirs", "theirs.pdf", [longPage("Theirs"), secondPage()]);
      everyText = theirs.id;
      await page.waitForTimeout(1500);
      assertEq(lookups.length, looked, "no lookup for a paper never translated here");
      assertEq(await page.locator(".pdfTransPara").count(), 0, "their translation is not shown");
      await page.getByRole("button", { name: "Translate", exact: true }).click();
      await shownAgain("Theirs");
      assert(requests.length > asked, "asked for by this browser");
      await page.locator(".pdfTransBar").waitFor({ state: "detached" });
      // Asked for here, it is this browser's to come back to (and the page
      // beside the one translated is read from what is kept meanwhile).
      await page.waitForTimeout(800);
      const now = requests.length, before = lookups.length;
      await theirs.open();
      await shownAgain("Theirs");
      await page.waitForTimeout(800);
      assertEq(lookups.length - before, 1, "one lookup");
      assertEq(requests.length, now, "no request to translate");
      assertNoProblems(page);
    } finally {
      everyText = "";
    }
  });

  await step("translation: a viewer's translation, which the workspace does not keep, is not remembered: a reload looks nothing up", async () => {
    asViewer = true;
    try {
      const viewed = await openPaper("Viewed", "viewed.pdf", [longPage("Viewed"), secondPage()]);
      await page.getByRole("button", { name: "Translate", exact: true }).click();
      await shownAgain("Viewed");
      await page.locator(".pdfTransBar").waitFor({ state: "detached" });
      await page.waitForTimeout(800);
      const looked = lookups.length;
      await viewed.open();
      await page.waitForTimeout(1500);
      assertEq(lookups.length, looked, "no lookup");
      assertEq(await page.locator(".pdfTransPara").count(), 0, "it opens in the original");
      await page.getByRole("button", { name: "Translate", exact: true }).waitFor();
      assertNoProblems(page);
    } finally {
      asViewer = false;
    }
  });

  await glossaryScenarios({ server, browser, makePdf, step, until, assert, assertEq, assertNoProblems, openPage });
}

// Twelve short paragraphs, two chunks of ten and two, the eleventh alone
// naming the Rydberg blockade.
function glossaryPage() {
  const runs = [];
  const line = (x, y, text) => runs.push({ x, y, size: 10, font: "F2", text });
  COUNTED.forEach((name, k) => {
    line(82, 724 - 36 * k, name === "eleven" ? "Paragraph eleven says the Rydberg blockade holds the"
      : `Paragraph ${name} says how the atoms in the trap`);
    line(72, 712 - 36 * k, "move once the drive is switched on and off.");
  });
  return { runs };
}

// A chat model's API as the server reaches it (the OpenAI wire): a brief made
// of one name kept, and every segment of a translation call answered "译:" +
// its text. `calls` holds each translation call's user turn.
function standInModel() {
  const calls = [];
  const server = http.createServer((req, res) => {
    let raw = "";
    req.on("data", (chunk) => { raw += chunk; });
    req.on("end", () => {
      const json = (data) => { res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(data)); };
      if (req.method !== "POST") return json({ data: [{ id: "glossary-model" }] });
      const body = JSON.parse(raw || "{}");
      const said = (m) => (typeof m.content === "string" ? m.content : (m.content || []).map((p) => p.text || "").join(""));
      const system = said(body.messages.find((m) => m.role === "system") || {});
      const user = said(body.messages.filter((m) => m.role === "user").at(-1) || {});
      const reply = (content) => (body.stream ? (() => {
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content } }] })}\n\n`);
        res.write(`data: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`);
        res.end("data: [DONE]\n\n");
      })() : json({ choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }] }));
      if (system.startsWith("You prepare a translator's brief")) return reply(JSON.stringify({ field: "atomic physics", keep: ["RF"] }));
      calls.push(user);
      reply([...user.matchAll(/^<s(\d+)>(.*)<\/s\1>$/gm)].map(([, n, text]) => `<s${n}>译:${text}</s${n}>`).join("\n"));
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({
    calls, base: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((done) => server.close(done)),
  })));
}

// The workspace's glossary (docs/dev/translation.md "The glossary"): an
// account of its own whose translations go through the server to the
// stand-in, so its calls show the Terms line the server builds from the
// workspace's terms.
async function glossaryScenarios({ server, browser, makePdf, step, until, assert, assertEq, assertNoProblems, openPage }) {
  const model = await standInModel();
  let ctx = null, page = null, user = null, lang = "";
  let asViewer = false;
  const segmentsOf = (turn) => [...turn.matchAll(/^<s\d+>(.*)<\/s\d+>$/gm)].map((m) => m[1]);
  const termsOf = (turn) => turn.split("\n").find((l) => l.startsWith("Terms: ")) || "";
  const openGlossary = async () => {
    await page.locator('[data-guide="header.account"]').click();
    await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
    await page.getByRole("navigation", { name: "Settings categories" })
      .getByRole("button", { name: "Language and Translation", exact: true }).click();
    await page.locator('.settingsPane [data-setting="Glossary"]').first().waitFor();
  };
  const closeSettings = () => page.getByRole("button", { name: "Close settings", exact: true }).click();

  try {
    await step("translation: a glossary term added in Settings goes to the model with the paragraphs that use it, not with the others", async () => {
      await server.manage("create-user", "glossary-user", "glossary-pw");
      user = await new Account(server, "glossary-user", "glossary-pw").login();
      await user.api("/api/ai/providers", { method: "POST", body: {
        protocol: "openai", name: "Stand-in model", api_key: "e2e-glossary-only", base_url: model.base, models: "glossary-model",
      } });
      const up = await user.upload("/api/uploads", makePdf([glossaryPage()]), "glossary.pdf", "application/pdf");
      const created = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Glossary", source_url: up.source_url } });
      ctx = await user.context(browser);
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      // While `asViewer`, the session says the account only views its workspace.
      await ctx.route("**/api/session", async (route) => {
        if (!asViewer) return route.fallback();
        const response = await route.fetch();
        const json = await response.json();
        for (const w of json.workspaces || []) if (w.id === user.ws) w.role = "viewer";
        await route.fulfill({ response, json });
      });
      page = await openPage(ctx, `${server.base}/?page=${created.id}&ws=${user.ws}`);
      await waitForPdf(page, 1);

      await openGlossary();
      const pane = page.locator(".settingsPane");
      await pane.getByRole("button", { name: "Add term", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "Add term", exact: true });
      await dialog.getByRole("textbox").nth(0).fill("Rydberg blockade");
      await dialog.getByRole("textbox").nth(1).fill("里德堡阻塞");
      const added = page.waitForRequest((r) => new URL(r.url()).pathname === "/api/translate/terms" && r.method() === "POST");
      await dialog.getByRole("button", { name: "Save", exact: true }).click();
      lang = (await added).postDataJSON().lang;
      await dialog.waitFor({ state: "detached" });
      const row = pane.locator('.aiProvRow[data-term="Rydberg blockade"]');
      await row.waitFor();
      assert((await row.innerText()).includes("里德堡阻塞"), await row.innerText());
      await closeSettings();

      await page.getByRole("button", { name: "Translate", exact: true }).click();
      const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
      await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:Paragraph twelve")),
        { what: "the page translated through the server", timeout: 30000 });
      await page.locator(".pdfTransBar").waitFor({ state: "detached" });
      const using = model.calls.filter((turn) => segmentsOf(turn).some((s) => s.includes("Rydberg blockade")));
      const others = model.calls.filter((turn) => !using.includes(turn));
      assert(using.length && others.length, `calls with and without the term: ${JSON.stringify(model.calls)}`);
      for (const turn of using) assertEq(termsOf(turn), "Terms: Rydberg blockade → 里德堡阻塞", "the call with the term's paragraph");
      for (const turn of others) assertEq(termsOf(turn), "", "a call whose paragraphs use no term");
      assertNoProblems(page);
    });

    await step("translation: a paper left translated opens from what the workspace keeps with its AI connection removed, and once it is made again", async () => {
      const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
      const shown = (what) => until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:Paragraph twelve")),
        { what, timeout: 30000 });
      const looked = [];
      const onLookup = (r) => { if (new URL(r.url()).pathname === "/api/translations/lookup") looked.push(r.postDataJSON().model); };
      page.on("request", onLookup);
      const connection = { protocol: "openai", name: "Stand-in model", api_key: "e2e-glossary-only", base_url: model.base, models: "glossary-model" };
      const entryOf = async () => (await user.api("/api/ai/settings")).providers.find((p) => p.base_url === model.base)?.id;
      try {
        const before = await entryOf();
        await page.waitForTimeout(800);
        const calls = model.calls.length;
        await user.api(`/api/ai/providers/${before}`, { method: "DELETE" });
        await page.reload();
        await waitForPdf(page, 1);
        await shown("the kept translation, with no AI connection");
        assertEq(model.calls.length, calls, "no model call");
        assert(looked.length && looked.every((m) => m === `${before}:glossary-model`), `looked up under the model it was left under: ${looked}`);
        await page.getByRole("button", { name: "Show original", exact: true }).waitFor();

        // Made again, the connection has a new entry id: the same model.
        await user.api("/api/ai/providers", { method: "POST", body: connection });
        const after = await entryOf();
        assert(after && after !== before, "a new entry");
        looked.length = 0;
        await page.reload();
        await waitForPdf(page, 1);
        await shown("the kept translation, through the connection made again");
        assertEq(model.calls.length, calls, "still no model call");
        assert(looked.length && looked.every((m) => m === `${after}:glossary-model`), `looked up through the new entry: ${looked}`);
        assertNoProblems(page);
      } finally {
        page.off("request", onLookup);
        if (!(await entryOf())) await user.api("/api/ai/providers", { method: "POST", body: connection });
      }
    });

    await step("translation: Add to glossary from the selection's translation, prefilled and editable", async () => {
      await page.getByRole("button", { name: "Show original", exact: true }).click();
      await page.locator('[data-page="1"] .pdfTransLayer').waitFor({ state: "detached" });
      await dragSelectPdfText(page, 1, "switched");
      await page.getByRole("button", { name: "Translate selection", exact: true }).click();
      const tip = page.locator(".plainTip");
      await until(async () => (await tip.locator(".selTransBody").textContent()) === "译:switched", { what: "the selection's translation" });
      await tip.getByRole("button", { name: "Add to glossary", exact: true }).click();
      const term = tip.getByRole("textbox", { name: "Term", exact: true });
      const translation = tip.getByRole("textbox", { name: "Translation", exact: true });
      assertEq(await term.inputValue(), "switched", "the selection");
      assertEq(await translation.inputValue(), "译:switched", "its translation");
      // Typed key by key, the keys are the field's: no shortcut takes one.
      await term.fill("");
      await term.pressSequentially("switched");
      assertEq(await term.inputValue(), "switched", "typed into the field");
      await translation.fill("切换");
      await tip.getByRole("button", { name: "Add", exact: true }).click();
      await tip.getByText("Added to the glossary", { exact: true }).waitFor();
      const { terms } = await user.api(`/api/translate/terms?lang=${encodeURIComponent(lang)}`);
      assertEq(terms.map((t) => `${t.source}=${t.target}`).join(","), "Rydberg blockade=里德堡阻塞,switched=切换");
      // Added again, the refusal says so in the reader's words, not the server's.
      await tip.locator(".selTransTerm").waitFor({ state: "detached" });
      await tip.getByRole("button", { name: "Add to glossary", exact: true }).click();
      await tip.getByRole("button", { name: "Add", exact: true }).click();
      await tip.getByText("The glossary has “switched” already", { exact: true }).waitFor();
      assertNoProblems(page, [/^POST \/api\/translate\/terms -> 409$/]);
    });

    await step("translation: a reader sees the glossary without its edit controls", async () => {
      asViewer = true;
      await page.reload();
      await waitForPdf(page, 1);
      await openGlossary();
      const pane = page.locator(".settingsPane");
      await pane.locator('.aiProvRow[data-term="switched"]').waitFor();
      assertEq(await pane.locator(".aiProvRow[data-term]").count(), 2, "both terms");
      assertEq(await pane.getByRole("button", { name: "Add term", exact: true }).count(), 0, "no Add term");
      assertEq(await pane.locator(".aiProvRow[data-term]").getByRole("button").count(), 0, "no Edit or Delete");
      await closeSettings();
      assertNoProblems(page);
    });
  } finally {
    asViewer = false;
    await ctx?.close();
    await model.close();
  }
}

// The server's stream for the page's translate requests, line by line: each
// request waits for what the step says to it (window.transRequests[k].say and
// .end). Gives land(k, translations, failed), its final reply.
// Where `word` is drawn in the first element matching `sel` that holds it:
// {x, y, w, h} in the viewport, or null.
function wordBox(page, sel, word) {
  return page.evaluate(([sel, word]) => {
    for (const el of document.querySelectorAll(sel)) {
      const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let n = walk.nextNode(); n; n = walk.nextNode()) {
        const k = n.data.indexOf(word);
        if (k < 0) continue;
        const range = document.createRange();
        range.setStart(n, k);
        range.setEnd(n, k + word.length);
        const b = range.getBoundingClientRect();
        return { x: b.x, y: b.y, w: b.width, h: b.height };
      }
    }
    return null;
  }, [sel, word]);
}

// A mouse drag from the start of the word box `from` to the end of `to`.
async function drag(page, from, to) {
  await page.mouse.move(from.x + 1, from.y + from.h / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.w - 1, to.y + to.h / 2, { steps: 10 });
  await page.mouse.up();
}

async function holdRequests(page) {
  await page.evaluate(() => {
    if (window.transHeld) return;
    window.transHeld = true;
    const fetchFirst = window.fetch;
    window.transRequests = [];
    window.fetch = function (url, opts) {
      if (!String(url).endsWith("/api/ai/translate")) return fetchFirst.apply(this, arguments);
      const body = new ReadableStream({
        start(out) {
          window.transRequests.push({
            texts: JSON.parse(opts.body).texts,
            say: (line) => out.enqueue(new TextEncoder().encode(`${JSON.stringify(line)}\n`)),
            end: () => out.close(),
          });
        },
      });
      return Promise.resolve(new Response(body, { headers: { "Content-Type": "application/x-ndjson" } }));
    };
  });
  return (k, translations, failed = []) => page.evaluate(([n, ts, f]) => {
    window.transRequests[n].say({ translations: ts, model: "demo:model", cached: false, failed: f, brief: true });
    window.transRequests[n].end();
  }, [k, translations, failed]);
}
