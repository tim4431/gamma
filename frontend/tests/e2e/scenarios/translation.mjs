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
// translations and clears them, the viewer's included; "Forget this
// translation" forgets one paper's. A viewer of the workspace is offered neither. The translate
// endpoints and the kept translations' are answered locally.
import { FAKE_AI_MODELS, fakeAiModels, wanted } from "../harness.mjs";
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

// Twelve short paragraphs: two chunks, of ten and of two.
const COUNTED = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];
function longPage() {
  const runs = [];
  const line = (x, y, text) => runs.push({ x, y, size: 10, font: "F2", text });
  COUNTED.forEach((name, k) => {
    line(82, 724 - 36 * k, `Paragraph ${name} says how the atoms in the lattice`);
    line(72, 712 - 36 * k, "move once the drive is switched on and off.");
  });
  return { runs };
}

export async function translationScenarios({ server, browser, alice, makePdf, step, until, assert, assertEq, assertNoProblems, openPage }) {
  if (!wanted("translation")) return;
  let page, pageId, stubbornDoc;
  const requests = [];
  // A step's own answer for the translate endpoint (route, body), when set.
  let respond = null;
  // The brief's requests, and the kept translations' DELETEs (their paths);
  // what GET /api/translations/stats answers.
  const briefs = [];
  const forgotten = [];
  let kept = { documents: 2, paragraphs: 42, bytes: 37 * 1024 };
  let asViewer = false;

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
    assertEq((await menu.getByRole("menuitemradio").allTextContents()).join(","), "Default,flex,fast");
    await menu.getByRole("menuitemradio", { name: "fast", exact: true }).click();
    await until(async () => (await alice.api("/api/prefs/profile")).value?.translateSpeed === "fast");
    assertEq((await alice.api("/api/prefs/profile")).value?.chatSpeed || "", chatSpeed, "the chat's speed is left as it was");
    await page.reload();
    await waitForPdf(page, 1);
    await openTranslation();
    assertEq(await speed.innerText(), "fast");
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
    // A new job asks for the brief again, and only its first chunk goes
    // without waiting for it.
    assertEq(briefs.length, 2, "each job asks for the paper's brief as it starts");
    assertEq(JSON.stringify(later().map((r) => r.wait_brief)), JSON.stringify(later().map((_, n) => n > 0)));
    await page.locator('[data-page="2"]').scrollIntoViewIfNeeded();
    const boxes = page.locator('[data-page="2"] .pdfTransPara .pdfTransText');
    await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:Here the atoms sit")),
      { what: "the second page's translation" });
    assertNoProblems(page);
  });

  await step("translation: the first chunk lands while the paper's brief is made; the job runs on after leaving the PDF, its row reaching 100%, and asks again for the chunk that went without the brief", async () => {
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
    const row = page.locator(".taskRow", { hasText: "Translate p.1" });
    await row.waitFor();
    assert(!(await row.evaluate((el) => el.classList.contains("done"))), "the job still runs");
    assert((await row.innerText()).includes("reading the paper"), await row.innerText());
    // Made: the waiting chunk translates with the brief, and the first is
    // asked again, with it.
    briefMade = true;
    await page.evaluate(() => window.__makeBrief());
    await until(() => row.evaluate((el) => el.classList.contains("done")), { what: "the job's row done" });
    assert((await row.innerText()).includes("100%"), await row.innerText());
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
    await page.getByRole("button", { name: "Hide translation", exact: true }).waitFor();
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
    // Try again asks for that paragraph alone.
    respond = null;
    await pill.locator(".pillRetryBtn", { hasText: "Try again" }).click();
    await until(async () => (await boxes.allTextContents()).some((t) => t.startsWith("译:A stubborn")),
      { what: "the stubborn paragraph, translated on the second try" });
    const again = requests.slice(start + 2);
    assertEq(again.length, 1, "one request");
    assertEq(JSON.stringify(again[0].texts.map((t) => t.slice(0, 11))), JSON.stringify(["A stubborn "]), "for the stubborn paragraph alone");
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

  await step("translation: Forget this translation asks the server to forget the paper, and the next click translates it afresh", async () => {
    const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
    assert((await boxes.count()) > 0, "the stubborn paper is translated");
    const start = requests.length;
    try {
      await page.getByRole("button", { name: "Hide translation", exact: true }).click({ button: "right" });
      await page.getByRole("menuitem", { name: "Forget this translation" }).click();
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

  await step("translation: a viewer of the workspace translates, but is offered neither Forget this translation nor Clear", async () => {
    kept = { documents: 1, paragraphs: 3, bytes: 2048 };
    asViewer = true;
    try {
      await page.reload();
      await waitForPdf(page, 1);
      const boxes = page.locator('[data-page="1"] .pdfTransPara .pdfTransText');
      await page.getByRole("button", { name: "Translate", exact: true }).click();
      await until(async () => (await boxes.count()) > 0, { what: "the paper translated for a viewer" });
      await page.getByRole("button", { name: "Hide translation", exact: true }).click({ button: "right" });
      const menu = page.getByRole("menu", { name: "Translate" });
      await menu.getByRole("menuitem", { name: "Show original" }).waitFor();
      assertEq(await menu.getByRole("menuitem", { name: "Forget this translation" }).count(), 0, "no Forget for a viewer");
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
}
