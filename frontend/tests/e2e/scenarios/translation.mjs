// The PDF translated view on a two-column page (docs/dev/translation.md):
// a paragraph that the column break cuts in two goes to the model as one
// text with {br} where the cut was, and comes back split into both columns'
// boxes; its formulas (a Greek letter in the Symbol font, an italic
// variable) and its citation marker go as {v1}, {v2}… tokens, and the
// formula comes back as a crop of the page inside the translation; the
// request names the page (for the paper's brief), the section heading and
// the text around its texts. "Translate whole document" then does the two
// pages after it, each request with the text before and after it across
// the page breaks. The translate endpoint is answered locally.
import { fakeAiModels, wanted } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";

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

function thirdPage() {
  const runs = [];
  const line = (x, y, text) => runs.push({ x, y, size: 10, font: "F2", text });
  line(82, 724, "The interaction falls off with the sixth");
  line(72, 712, "power of the distance between two atoms.");
  return { runs };
}

export async function translationScenarios({ server, browser, alice, makePdf, step, until, assert, assertEq, assertNoProblems, openPage }) {
  if (!wanted("translation")) return;
  let page, pageId;
  const requests = [];

  await step("translation: setup, a paper whose first page's paragraph runs over the column break", async () => {
    const pdf = makePdf([twoColumnPage(), secondPage(), thirdPage()]);
    const up = await alice.upload("/api/uploads", pdf, "columns.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Two columns", source_url: up.source_url } });
    pageId = created.id;
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    // Every text comes back as "译:" + each of its pieces, tokens and
    // breaks kept: what a model following the prompt returns.
    await ctx.route("**/api/ai/translate", async (route) => {
      const body = route.request().postDataJSON();
      requests.push(body);
      const translations = body.texts.map((t) => `译:${t.split("{br}").join("{br}译:")}`);
      await route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify({ translations, model: "demo:model", cached: false })}\n` });
    });
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
  });

  await step("translation: while the server reads the paper for its brief, the pill says so; leaving the PDF takes it down", async () => {
    // A held request answers as the server does while it makes the paper's
    // brief: a "brief" stage line, then nothing until window.__makeBrief().
    // A route can only answer whole, so the page's fetch is wrapped:
    // released, the request goes on to the route above. window.__hold arms
    // it for the next translate request.
    await page.evaluate(() => {
      const fetchFirst = window.fetch;
      window.fetch = function (url, opts) {
        if (!window.__hold || !String(url).includes("/ai/translate")) return fetchFirst.apply(this, arguments);
        window.__hold = false;
        const made = new Promise((resolve) => { window.__makeBrief = resolve; });
        const line = (s) => new TextEncoder().encode(`${s}\n`);
        const body = new ReadableStream({
          async start(out) {
            opts.signal?.addEventListener("abort", () => out.error(new DOMException("Aborted", "AbortError")));
            out.enqueue(line('{"stage": "brief"}'));
            await made;
            out.enqueue(line('{"stage": "translate"}'));
            const res = await fetchFirst.call(window, url, opts);
            out.enqueue(new Uint8Array(await res.arrayBuffer()));
            out.close();
          },
        });
        return Promise.resolve(new Response(body, { headers: { "Content-Type": "application/x-ndjson" } }));
      };
      window.__hold = true;
    });
    const pill = page.locator(".statusPill .pillText", { hasText: "Reading the paper first" });
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    await pill.waitFor({ timeout: 5000 });
    // Home: the viewer goes, its job stops, and the pill with it.
    await page.locator('[data-guide="header.home"]').first().click();
    await pill.waitFor({ state: "detached", timeout: 5000 });
    // Back on the paper, a new job reads it again; made, the pill goes.
    await page.locator(".tabStrip .tab", { hasText: "Two columns" }).first().click();
    await waitForPdf(page, 1);
    await page.evaluate(() => { window.__hold = true; });
    await page.getByRole("button", { name: "Translate", exact: true }).click();
    await pill.waitFor({ timeout: 5000 });
    await page.evaluate(() => window.__makeBrief());
    await pill.waitFor({ state: "detached", timeout: 5000 });
    assertNoProblems(page);
  });

  await step("translation: the cut paragraph goes as one text with its formulas as tokens, and fills both columns", async () => {
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
    // The section heading rides along: as the request's section, or as one
    // of its texts when the request starts with it.
    assert(withJoin.section === "1 Introduction" || withJoin.texts.includes("1 Introduction"), JSON.stringify(withJoin));
    const texts = await boxes.allTextContents();
    assert(texts.some((t) => t.startsWith("译:The collective Rabi frequency")), JSON.stringify(texts));
    assert(texts.some((t) => t.includes("blockade forms [3].")), "the citation marker is back as text");
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

  await step("translation: the whole document goes on with the pages after it, each request with the text around it", async () => {
    const start = requests.length;
    await page.getByRole("button", { name: "Hide translation", exact: true }).click({ button: "right" });
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
    await page.locator('[data-page="2"]').scrollIntoViewIfNeeded();
    const boxes = page.locator('[data-page="2"] .pdfTransPara .pdfTransText');
    await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:Here the atoms sit")),
      { what: "the second page's translation" });
    assertNoProblems(page);
  });
}
