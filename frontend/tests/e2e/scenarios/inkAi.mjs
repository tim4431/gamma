// The selection menu's Convert menu (docs/dev/handwriting_ai.md): Convert,
// in the menu's first row, opens Transcribe in the chat, Redraw as shapes,
// Convert to text, Copy as SVG and Download SVG. Redraw as shapes sends the
// selected strokes and puts the AI's figures in their place — a rough box
// becomes a stored `rect` shape stroke, selected, which Ctrl+Z takes back to
// the drawn stroke; one AI request runs at a time, the strokes under a
// shimmering wash meanwhile, and its task row's Stop, or leaving the page,
// stops it with nothing changed and the wash gone. Convert to text takes
// the strokes away and puts a text box with the AI's text where they were,
// in their ink's colour, the wash fading as it lands, one Ctrl+Z bringing
// both back. Download SVG saves
// an SVG document of the selection's paths, and Copy as SVG puts a PNG and
// the markup on the clipboard. An edit share's view has no AI: Redraw and
// Convert to text say so and send nothing. In a 740 × 620 window, a Convert
// menu with no room under its button opens above it rather than over it; a
// scroll closes it, and so does More from the keyboard; for a
// highlighter alone Redraw and Convert to text are greyed, and where the
// clipboard takes no picture (no secure context) so is Copy as SVG, each
// title saying why. The AI is stubbed: the routes
// answer NDJSON as the server streams it (gamma/routers/ink_ai.py), whose
// side is backend/tests/test_ink_ai.py; the client's pure parts are
// tests/inkAi.test.mjs and tests/inkSvg.test.mjs.
import { readFile } from "node:fs/promises";
import { assertMenuFrom, assertMenuRoles, fakeAiModels, live } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";
import { decodeStroke, encodeStroke, newInk, strokeBounds } from "../../../src/ink/ink.js";

// A box drawn in one rough stroke, its top edge straight on y = 120 (where
// a click picks it), and a word-like zigzag in blue under it.
const BOX = [[100, 120], [140, 120], [180, 120], [220, 120], [262, 121], [261, 170], [259, 221], [180, 222], [101, 219],
  [99, 170], [101, 123]];
const WORD = [[120, 330], [135, 310], [150, 330], [165, 310], [180, 330], [195, 310], [210, 330], [225, 310], [240, 330]];
const RECT = { cx: 180, cy: 170, w: 160, h: 100, angle: 0 };

export async function inkAiScenarios({ server, browser, alice, bob, makePdf, step, until, assert, assertEq, assertNoProblems, openPage }) {
  let ctx, page, pageId, groupId;
  const sent = { shapes: [], text: [] };
  // While set ({promise}), the shapes route holds its answer until the step resolves it.
  let holdShapes = null;
  const menu = () => page.getByRole("toolbar", { name: "Edit handwriting", exact: true });
  // the open Convert menu (not one leaving)
  const convertMenu = () => page.locator(live(".inkConvertMenu"));
  const onPage = (x, y) => page.locator('.pdfViewer [data-page="1"]').evaluate((el, [x, y]) => {
    const r = el.getBoundingClientRect(), k = r.width / 612;
    return { x: r.left + x * k, y: r.top + y * k };
  }, [x, y]);
  // A click on the stroke at (x, y) with nothing armed selects it alone.
  const pick = async (x, y) => {
    const at = await onPage(x, y);
    await page.mouse.click(at.x, at.y);
    await menu().waitFor();
  };
  // Convert, and one row of its menu.
  const convert = async (row) => {
    if (!(await convertMenu().count())) await menu().getByRole("button", { name: "Convert", exact: true }).click();
    await convertMenu().getByRole("menuitem", { name: row, exact: true }).click();
  };
  const subtree = async () => (await alice.api(`/api/blocks/${pageId}/subtree`)).block.children;
  const saved = async () => {
    const group = (await subtree()).find((b) => b.id === groupId);
    return group?.properties?.ink_url ? (await alice.api(group.properties.ink_url)).strokes : [];
  };
  const ids = async () => (await saved()).map((s) => s.id).sort().join(",");
  const pill = (text) => page.locator(".statusPill", { hasText: text });

  await step("ink ai: Convert, in the selection menu's first row, opens Transcribe in the chat, Redraw as shapes, Convert to text, Copy as SVG and Download SVG", async () => {
    const up = await alice.upload("/api/uploads", makePdf([["Handwriting and AI", "a page to draw on"]]), "ink-ai.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Ink AI paper", source_url: up.source_url } });
    pageId = created.id;
    const stroke = (id, points, color) => encodeStroke({ id, color, size: 2, ch: "xyt", t0: 1000,
      samples: points.map(([x, y], i) => ({ x, y, t: i * 20 })) });
    const strokes = [stroke("box", BOX, "#1f1f1f"), stroke("word", WORD, "#2563eb")];
    const file = await alice.api("/api/upload-ink", { method: "POST", body: { ...newInk(1, 612, 792), strokes } });
    groupId = (await alice.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "",
      properties: { ink_url: file.url, pdf_position: file.pdf_position, ink_strokes: 2 } } })).id;
    ctx = await alice.context(browser, { permissions: ["clipboard-read", "clipboard-write"] });
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    await ctx.route("**/api/ai/ink/shapes", async (route) => {
      sent.shapes.push(route.request().postDataJSON());
      if (holdShapes) await holdShapes.promise;
      const body = `{"ping":1}\n${JSON.stringify({ shapes: [{ kind: "rect", params: RECT, strokes: [0] }] })}\n`;
      await route.fulfill({ contentType: "application/x-ndjson", body }).catch(() => {}); // a stopped request is gone
    });
    await ctx.route("**/api/ai/ink/text", async (route) => {
      sent.text.push(route.request().postDataJSON());
      await route.fulfill({ contentType: "application/x-ndjson", body: '{"ping":1}\n{"text":"Hello world"}\n' });
    });
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
    await until(async () => (await page.locator('.pdfViewer [data-page="1"] .inkLayer path').count()) === 2, { what: "the page's two strokes" });
    await pick(180, 120);
    assertEq(await menu().locator(".inkEditRow .ctlBtn").nth(3).getAttribute("aria-label"), "Convert", "Convert in the first row, after Copy");
    const button = menu().getByRole("button", { name: "Convert", exact: true });
    assertEq(await button.getAttribute("aria-haspopup"), "menu");
    assertEq(await button.getAttribute("title"), "Transcribe, redraw or convert the selected handwriting");
    await button.click();
    await convertMenu().waitFor();
    assertEq(await button.getAttribute("aria-expanded"), "true");
    // a menu of its own, named by its button, under it
    await assertMenuRoles(page.getByRole("menu", { name: "Convert", exact: true }), "the Convert menu");
    await assertMenuFrom(convertMenu(), button, "below", "the Convert menu");
    const rows = await convertMenu().getByRole("menuitem").evaluateAll((els) => els.map((el) => [el.textContent.trim(), el.title, el.disabled]));
    assertEq(JSON.stringify(rows), JSON.stringify([
      ["Transcribe in the chat", "Transcribe with AI: the selected handwriting as text in the chat", false],
      ["Redraw as shapes", "Redraw with AI: the selected drawing as clean lines, arrows and shapes", false],
      ["Convert to text", "Convert with AI: the selected handwriting becomes a text box in its place", false],
      ["Copy as SVG", "Copy the selected handwriting as an SVG picture", false],
      ["Download SVG", "Save the selected handwriting as an SVG file", false],
    ]), "the five rows, in order");
    assertEq(await convertMenu().getByRole("separator").count(), 1, "the AI's rows, then the SVG's");
    // a second press on Convert closes it
    await button.click();
    await until(async () => !(await page.locator(live(".inkConvertMenu")).count()), { what: "the menu closed" });
    assertNoProblems(page);
  });

  await step("ink ai: Redraw as shapes puts a rect shape stroke in the drawn box's place, selected; Ctrl+Z brings the drawn stroke back", async () => {
    await convert("Redraw as shapes");
    await until(async () => (await saved()).some((s) => s.shape?.kind === "rect"), { what: "the rect saved" });
    assertEq(sent.shapes.length, 1, "one request");
    const [body] = sent.shapes;
    assertEq(JSON.stringify(body.strokes.map((s) => s.id)), JSON.stringify(["box"]), "the selected stroke alone");
    assertEq(JSON.stringify([body.space.kind, body.space.page, body.space.width]), JSON.stringify(["pdf-page", 1, 612]), "in its group's space");
    assert(typeof body.model === "string", "the chat's model rides along");
    const strokes = await saved();
    assertEq(strokes.map((s) => s.id).includes("box"), false, "the drawn box is gone");
    assert(strokes.some((s) => s.id === "word"), "the word stays as drawn");
    const rect = strokes.find((s) => s.shape?.kind === "rect");
    // (the server writes a record's keys in order)
    const sorted = (o) => JSON.stringify(Object.fromEntries(Object.entries(o).sort(([a], [b]) => (a < b ? -1 : 1))));
    assertEq(sorted(rect.shape.params), sorted(RECT), "the AI's record");
    assertEq(JSON.stringify([rect.tool ?? "pen", rect.pen, rect.brush, rect.color, rect.size]), JSON.stringify(["pen", false, "monoline", "#1f1f1f", 2]),
      "a shape stroke in the drawn stroke's look");
    assertEq(rect.id, strokes[0].id, "in the drawn stroke's place");
    const samples = decodeStroke(rect);
    assert(samples.every((q) => q.x >= 99.99 && q.x <= 260.01 && q.y >= 119.99 && q.y <= 220.01), "drawn from its params");
    await pill("Redrawn as 1 shape.").waitFor();
    // the new stroke is the selection
    const [x0, y0, x1, y1] = strokeBounds(rect);
    await until(async () => {
      const box = await page.locator(".pdfViewer .inkSelRect").evaluate((el) => [el.x.baseVal.value, el.y.baseVal.value, el.width.baseVal.value, el.height.baseVal.value]);
      return Math.abs(box[0] + 4 - x0) < 0.6 && Math.abs(box[1] + 4 - y0) < 0.6 && Math.abs(box[2] - 8 - (x1 - x0)) < 0.6 && Math.abs(box[3] - 8 - (y1 - y0)) < 0.6;
    }, { what: "the rect selected" });
    await page.keyboard.press("ControlOrMeta+z");
    await pill("Undone: ink redraw as shapes (page 1).").waitFor();
    await until(async () => (await ids()) === "box,word", { what: "the drawn box saved back" });
    assertNoProblems(page);
  });

  await step("ink ai: one request at a time; the task row's Stop, or leaving the page, stops it and nothing is redrawn", async () => {
    let release;
    const hold = () => { holdShapes = { promise: new Promise((resolve) => { release = resolve; }) }; };
    hold();
    try {
      await pick(180, 120);
      await convert("Redraw as shapes");
      await pill("Redrawing the selection as shapes…").waitFor();
      await until(() => sent.shapes.length === 2, { what: "the request sent" });
      // the strokes sent under the translation's shimmering wash while the AI works
      const wash = page.locator('.pdfViewer [data-page="1"] .inkAiWork');
      await wash.waitFor();
      assertEq(await wash.getAttribute("class"), "inkAiWork", "working, not landed");
      assertEq(await wash.evaluate((el) => getComputedStyle(el).animationName), "pdfTransShimmer", "it shimmers as a paragraph being translated does");
      const [w, b0, b1] = [await wash.boundingBox(), await onPage(BOX[0][0], BOX[0][1]), await onPage(BOX[4][0], BOX[6][1])];
      assert(w.x < b0.x && w.y < b0.y && w.x + w.width > b1.x && w.y + w.height > b1.y, `over the box: ${JSON.stringify([w, b0, b1])}`);
      await convert("Redraw as shapes");
      await pill("The AI is still working on the last selection…").waitFor();
      assertEq(sent.shapes.length, 2, "no second request");
      await page.click("button[aria-label='Background tasks']");
      // (the first redraw's row stands done above it)
      const row = page.locator(live(".tasksPopover")).locator(live(".taskRow.running"), { hasText: "Redraw as shapes" });
      await row.waitFor();
      assert((await row.textContent()).includes("Redraw as shapes — Ink AI paper"), "the row names its page");
      assert((await row.getAttribute("title")).includes("asking"), await row.getAttribute("title"));
      await row.locator('button[aria-label^="Stop Redraw as shapes"]').click();
      await pill("Stopped: the handwriting stays as it was.").waitFor();
      await until(async () => !(await wash.count()), { what: "the wash gone with the request" });
      await until(async () => !(await page.locator(live(".tasksPopover .taskRow.running")).count()), { what: "the row stopped" });
      await page.keyboard.press("Escape");
      release();
      await page.waitForTimeout(1000);
      assertEq(await ids(), "box,word", "nothing redrawn");
      assertEq(await page.locator(".statusPill", { hasText: "Redrawn" }).count(), 0, "no pill claims a redraw");
      // asked again and the page left before the answer: it is stopped, and lands nowhere
      hold();
      await pick(180, 120);
      await convert("Redraw as shapes");
      await until(() => sent.shapes.length === 3, { what: "the request sent" });
      await page.locator('[data-guide="header.home"]').first().click();
      await pill("Stopped: the handwriting stays as it was.").waitFor();
      release();
      await page.waitForTimeout(1000);
      assertEq(await page.locator(".statusPill", { hasText: "Redrawn" }).count(), 0, "no pill claims a redraw");
    } finally {
      release();
      holdShapes = null;
    }
    await page.goto(`${server.base}/?page=${pageId}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
    await until(async () => (await page.locator('.pdfViewer [data-page="1"] .inkLayer path').count()) === 2, { what: "the page's two strokes" });
    assertEq(await ids(), "box,word", "nothing redrawn");
    assertNoProblems(page);
  });

  await step("ink ai: Convert to text puts a text box with the AI's text where the strokes were, in their colour, and takes them away; one Ctrl+Z restores both", async () => {
    await pick(127.5, 320);
    // the wash's classes as they change: working, then landed
    await page.evaluate(() => {
      window.aiWash = [];
      new MutationObserver(() => {
        const el = document.querySelector(".inkAiWork");
        if (el && window.aiWash.at(-1) !== el.className) window.aiWash.push(el.className);
      }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
    });
    await convert("Convert to text");
    await page.locator('.pdfViewer [data-page="1"] .textBox', { hasText: "Hello world" }).waitFor();
    await until(async () => JSON.stringify(await page.evaluate(() => window.aiWash)) === JSON.stringify(["inkAiWork", "inkAiWork landed"]),
      { what: "the wash shimmering, then landing" });
    await until(async () => !(await page.locator(".inkAiWork").count()), { what: "the landed wash gone" });
    assertEq(sent.text.length, 1, "one request");
    assertEq(JSON.stringify(sent.text[0].strokes.map((s) => s.id)), JSON.stringify(["word"]), "the selected stroke alone");
    await pill("Converted the handwriting to text.").waitFor();
    const box = await until(async () => (await subtree()).find((b) => b.properties?.text_box && b.content === "Hello world"), { what: "the box saved" });
    assertEq(box.properties.pdf_page, 1, "on the strokes' page");
    const [x0, y0, x1] = strokeBounds(encodeStroke({ id: "w", size: 2, ch: "xyt", samples: WORD.map(([x, y]) => ({ x, y, t: 0 })) }));
    const tb = box.properties.text_box;
    assert(Math.abs(tb.x - (x0 - 4)) < 0.02 && Math.abs(tb.y - (y0 - 4)) < 0.02, `the text where the strokes were: ${JSON.stringify(tb)} vs ${[x0, y0, x1]}`);
    assertEq(tb.color, "#2563eb", "in the ink's colour");
    assertEq(tb.auto, true);
    await until(async () => (await ids()) === "box", { what: "the converted stroke gone" });
    await page.keyboard.press("ControlOrMeta+z");
    await pill("Undone: ink conversion to text (page 1).").waitFor();
    await until(async () => !(await page.locator('.pdfViewer [data-page="1"] .textBox').count()), { what: "the box gone from the page" });
    await until(async () => !(await subtree()).some((b) => b.properties?.text_box), { what: "the box gone from the notes" });
    await until(async () => (await ids()) === "box,word", { what: "the stroke saved back" });
    assertNoProblems(page);
  });

  await step("ink ai: Download SVG saves an SVG of the selection's paths; Copy as SVG puts a PNG and the markup on the clipboard", async () => {
    await pick(180, 120);
    const download = page.waitForEvent("download");
    await convert("Download SVG");
    const file = await download;
    assertEq(file.suggestedFilename(), "handwriting.svg");
    const svg = await readFile(await file.path(), "utf8");
    assert(svg.startsWith("<svg") && svg.includes('xmlns="http://www.w3.org/2000/svg"') && svg.includes("<path"), svg.slice(0, 200));
    await convert("Copy as SVG");
    await pill("Copied the selected handwriting as SVG.").waitFor();
    const clip = await page.evaluate(async () => {
      const [item] = await navigator.clipboard.read();
      const text = item.types.includes("text/plain") ? await (await item.getType("text/plain")).text() : "";
      return { types: item.types, text };
    });
    assert(clip.types.includes("image/png") && clip.types.includes("text/plain"), JSON.stringify(clip.types));
    assertEq(clip.text.replace(/\r\n/g, "\n"), svg, "the markup is the downloaded document");
    assertNoProblems(page);
    await ctx.close();
  });

  await step("ink ai: an edit share's Redraw as shapes and Convert to text say a shared page cannot use the AI, and send nothing", async () => {
    await alice.api(`/api/share/${pageId}`, { method: "POST" });
    await alice.api(`/api/share-settings/${pageId}`, { method: "PUT", body: { audience: "users", role: "edit" } });
    const { token } = await alice.api(`/api/share-settings/${pageId}`);
    const editCtx = await bob.context(browser);
    try {
      const asked = [];
      await editCtx.route("**/api/ai/ink/**", (route) => { asked.push(route.request().url()); return route.abort(); });
      page = await openPage(editCtx, `${server.base}/?share=${token}`);
      await waitForPdf(page, 1);
      await until(async () => (await page.locator('.pdfViewer [data-page="1"] .inkLayer path').count()) === 2, { what: "the shared page's strokes" });
      await pick(180, 120);
      await convert("Redraw as shapes");
      await pill("Redrawing needs the AI, which a shared page cannot use.").waitFor();
      await convert("Convert to text");
      await pill("Converting handwriting needs the AI, which a shared page cannot use.").waitFor();
      assertEq(asked.length, 0, "nothing sent");
      assertEq(await ids(), "box,word", "nothing changed");
      assertNoProblems(page);
    } finally { await editCtx.close(); }
  });

  await step("ink ai: at 740 × 620 the Convert menu opens above a button low in the window, never over it; a scroll closes it, as More does; greyed rows say why", async () => {
    // a stroke near the page's foot and a highlighter over it, in a note of their own
    const low = encodeStroke({ id: "low", color: "#1f1f1f", size: 2, ch: "xyt", t0: 1000,
      samples: [[150, 700], [250, 702], [350, 700]].map(([x, y], i) => ({ x, y, t: i * 20 })) });
    const marker = encodeStroke({ id: "marker", tool: "highlighter", color: "#ffe28f", size: 10, opacity: 0.6, ch: "xy", pen: false,
      samples: [{ x: 150, y: 600 }, { x: 350, y: 600 }] });
    const file = await alice.api("/api/upload-ink", { method: "POST", body: { ...newInk(1, 612, 792), strokes: [low, marker] } });
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "",
      properties: { ink_url: file.url, pdf_position: file.pdf_position, ink_strokes: 2 } } });
    const small = await alice.context(browser, { viewport: { width: 740, height: 620 } });
    // a page served where the clipboard takes no picture (plain http, not localhost)
    await small.addInitScript(() => Object.defineProperty(window, "isSecureContext", { get: () => false }));
    try {
      page = await openPage(small, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
      await waitForPdf(page, 1);
      await until(async () => (await page.locator('.pdfViewer [data-page="1"] .inkLayer path').count()) === 4, { what: "the page's four strokes" });
      // scrolled so the stroke is 70 px over the window's foot: the selection
      // menu stands above it, and its Convert low in the window
      const at = await onPage(250, 702);
      await page.locator(".pdfViewer").evaluate((el, dy) => { el.scrollTop += dy; }, at.y - 550);
      await until(async () => Math.abs((await onPage(250, 702)).y - 550) < 2, { what: "the stroke scrolled near the window's foot" });
      await pick(250, 702);
      const more = menu().getByRole("button", { name: "More", exact: true });
      const button = menu().getByRole("button", { name: "Convert", exact: true });
      await button.click();
      await convertMenu().waitFor();
      await assertMenuFrom(convertMenu(), button, "above", "the Convert menu with no room under its button");
      const m = await convertMenu().boundingBox(), b = await button.boundingBox();
      assert(b.y + b.height + 4 + m.height > 620 - 8, `there is no room under the button: button ${JSON.stringify(b)}, menu ${JSON.stringify(m)}`);
      assert(m.x >= 0 && m.y >= 0 && m.x + m.width <= 740 && m.y + m.height <= 620, `the menu fits the window: ${JSON.stringify(m)}`);
      // a second press where the button is lands on it, and closes the menu
      await button.click();
      await until(async () => !(await convertMenu().count()), { what: "the menu closed by its button" });
      // a scroll moves the selection menu; the Convert menu closes rather than stay behind
      await button.click();
      await convertMenu().waitFor();
      await page.locator(".pdfViewer").evaluate((el) => { el.scrollTop -= 40; });
      await until(async () => !(await convertMenu().count()), { what: "the menu closed by the scroll" });
      await menu().waitFor();
      // from the keyboard: Convert's menu open, then More opened and closed — the
      // row moves the first one, and the menu closes rather than stay behind
      await button.focus();
      await page.keyboard.press("Enter");
      await convertMenu().waitFor();
      await more.focus();
      await page.keyboard.press("Enter");
      await until(async () => (await more.getAttribute("aria-expanded")) === "true", { what: "More opened" });
      await until(async () => !(await convertMenu().count()), { what: "the menu closed by the row" });
      await page.keyboard.press("Enter");
      await until(async () => (await more.getAttribute("aria-expanded")) === "false", { what: "More collapsed" });
      await page.waitForTimeout(300);
      assertEq(await convertMenu().count(), 0, "the Convert menu does not come back with the row");
      assertEq(await button.getAttribute("aria-expanded"), "false");
      // a highlighter alone, with no secure context: the rows that cannot act are greyed, saying why
      await page.keyboard.press("Escape");
      await until(async () => !(await menu().count()), { what: "the selection let go" });
      await pick(250, 600);
      await menu().getByRole("button", { name: "Convert", exact: true }).click();
      await convertMenu().waitFor();
      const rows = await convertMenu().getByRole("menuitem").evaluateAll((els) => els.map((el) => [el.textContent.trim(), el.title, el.disabled]));
      assertEq(JSON.stringify(rows), JSON.stringify([
        ["Transcribe in the chat", "Transcribe with AI: the selected handwriting as text in the chat", false],
        ["Redraw as shapes", "Redrawing takes pen strokes; highlighters are left as they are", true],
        ["Convert to text", "Converting takes pen strokes; highlighters are left as they are", true],
        ["Copy as SVG", "Copying an image needs a secure connection (HTTPS or localhost)", true],
        ["Download SVG", "Save the selected handwriting as an SVG file", false],
      ]), "the greyed rows and their reasons");
      assertNoProblems(page);
    } finally { await small.close(); }
  });
}
