// The zoom window and transcribing a selection (docs/dev/handwriting.md
// "Zoom window" and "Transcribe a selection"): More → Zoom window docks the
// panel under the pages with the window's box on the page; a stroke written
// in the panel, drawn live on a canvas of what the panel shows, is saved in
// the page's group at page coordinates (the window's offset and
// magnification applied) and is there after a reload; Previous, Next and
// New line move the window, a stroke reaching into its right fifth moves it
// on along the line, the box's band drags it (the panel following at the
// release) and a click on a blank spot of the page centres it there; Z
// closes it. The
// selection menu's Transcribe sends a selection that takes part of a group
// as that group's strokes (`ink_selections`), and a whole group as its
// block's chip, as Transcribe with AI does (the AI stubbed: no provider is
// called). The window's rules themselves are tests/zoomWindow.test.mjs; the
// server's side of a selection is backend/tests/test_ai_tools_ink.py.
import { assertNear as near, fakeAiModels } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";
import { decodeStroke, encodeStroke, newInk, strokeBounds } from "../../../src/ink/ink.js";
import { advance, moveTo, moveWindow, newLine, nextLine, prevLine } from "../../../src/ink/zoomWindow.js";

const PAGE = { width: 612, height: 792 };

export async function inkZoomScenarios({ server, browser, alice, makePdf, step, until, assert, assertEq, assertNoProblems, openPage }) {
  const account = alice;
  let ctx, page, pageId;
  // Every stroke the page's groups hold on the server.
  const savedStrokes = async () => {
    const out = [];
    for (const b of (await account.api(`/api/blocks/${pageId}/subtree`)).block.children || []) {
      if (!b.properties?.ink_url?.endsWith(".ink")) continue;
      for (const stroke of (await account.api(b.properties.ink_url)).strokes) out.push({ block: b.id, stroke });
    }
    return out;
  };
  const panel = () => page.getByRole("region", { name: "Zoom window", exact: true });
  const bar = () => page.getByRole("toolbar", { name: "Zoom window controls", exact: true });
  // The window as the page's box shows it, in page points; the window state
  // the zoomWindow.js rules take (the line height 24 and where the line began
  // are the test's own record of what it did).
  const box = () => page.locator(".pdfViewer .inkZoomRect").evaluate((el) =>
    ({ x: Number(el.dataset.x), y: Number(el.dataset.y), w: Number(el.dataset.w), h: Number(el.dataset.h) }));
  let x0 = 0;
  const state = async () => ({ page: 1, ...(await box()), mag: Number(await panel().getAttribute("data-mag")), line: 24, x0 });
  const sameBox = (a, b, what) => { for (const k of ["x", "y", "w", "h"]) near(a[k], b[k], 0.06, `${what} (${k})`); };
  // A page point where the panel shows it, and where the page itself does.
  const inPanel = async (x, y) => {
    const z = await state(), v = await page.locator(".inkZoomView").boundingBox();
    return { x: v.x + (x - z.x) * z.mag, y: v.y + (y - z.y) * z.mag };
  };
  const onPage = (x, y) => page.locator('.pdfViewer [data-page="1"]').evaluate((el, [x, y]) => {
    const r = el.getBoundingClientRect(), k = r.width / 612;
    return { x: r.left + x * k, y: r.top + y * k };
  }, [x, y]);
  const draw = async (from, to) => {
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: 12 });
    await page.mouse.up();
  };
  // The window once the panel has measured its view (w × mag and h × mag
  // its size): where its line begins is where it opened.
  const settled = async () => {
    await panel().waitFor();
    const view = await page.locator(".inkZoomView").boundingBox();
    const z = await until(async () => {
      const s = await state();
      return Math.abs(s.w * s.mag - view.width) <= 1 && Math.abs(s.h * s.mag - view.height) <= 1 ? s : null;
    }, { what: "w × mag and h × mag are the view's size" });
    x0 = z.x;
    return { ...z, x0 };
  };
  const openWindow = async () => {
    await page.locator(".pdfInkBar button[aria-label='More handwriting tools']").click();
    await page.locator(".inkStripMenu").getByRole("menuitemcheckbox", { name: "Zoom window" }).click();
    return settled();
  };

  await step("ink zoom: More → Zoom window docks the panel under the pages at 3×, its box on the page and the page's pixels behind it", async () => {
    const up = await account.upload("/api/uploads", makePdf([["The zoom window writes large", "on this page"]]), "zoom.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Zoom paper", source_url: up.source_url } });
    pageId = created.id;
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    assertEq(await page.locator(".pdfInkBar button[aria-label='Zoom window']").count(), 0, "no Zoom window button while it is closed");
    const z = await openWindow();
    assertEq(await panel().getAttribute("data-mag"), "3", "it opens at 3×");
    assertEq(await panel().getAttribute("data-page"), "1");
    assertEq(await panel().getAttribute("data-guide"), "ink.zoom");
    assertEq(await page.locator(".pdfInkBar button[aria-label='Zoom window']").getAttribute("aria-pressed"), "true", "the pressed toggle in the strip");
    assertEq(await page.locator(".inkStripMenu").count(), 0, "the menu closed");
    // docked under the scroller, which gives up its 200 px
    const scroller = await page.locator(".pdfViewer").boundingBox(), docked = await panel().boundingBox();
    near(docked.height, 200, 1, "the panel's height");
    near(scroller.y + scroller.height, docked.y, 1.5, "the panel starts where the scroller ends");
    // the window fills the view at 3× (openWindow waited for that) and lies on the page in view
    assert(z.x >= 0 && z.y >= 0 && z.x + z.w <= 612.01 && z.y + z.h <= 792.01, `the window on its page: ${JSON.stringify(z)}`);
    for (const name of ["Previous line", "Next line", "New line", "Smaller line height", "Larger line height", "Close the zoom window"]) {
      assertEq(await bar().getByRole("button", { name, exact: true }).count(), 1, `the bar's ${name}`);
    }
    assertEq(JSON.stringify(await bar().getByRole("group", { name: "Magnification" }).locator("button").allTextContents()), JSON.stringify(["2×", "3×", "4×"]));
    assertEq(await bar().locator(".inkZoomReadout").textContent(), "24 pt", "the line height to start");
    await until(() => page.locator(".inkZoomRaster").evaluateAll((els) => els.some((c) => c.style.display === "block" && c.width > 0)),
      { what: "the page's pixels drawn in the panel" });
    assertNoProblems(page);
  });

  let first;
  await step("ink zoom: a stroke written in the panel is saved in the page's group at page coordinates, and is there after a reload", async () => {
    const z = await state();
    const y = z.y + z.h / 2, from = { x: z.x + 0.15 * z.w, y }, to = { x: z.x + 0.45 * z.w, y };
    const a = await inPanel(from.x, from.y), b = await inPanel(to.x, to.y);
    await page.mouse.move(a.x, a.y);
    await page.mouse.down();
    await page.mouse.move((a.x + b.x) / 2, a.y, { steps: 6 });
    // the stroke in progress is drawn on a canvas of what the panel shows, at the device's ratio — not one
    // of the whole page at 3×, which the canvas caps would hold to a ratio near 1 on an iPad
    const live = await panel().locator(".inkCanvas").evaluate((c) => {
      const r = c.getBoundingClientRect(), v = c.closest(".inkZoomView").getBoundingClientRect();
      return { display: c.style.display, w: c.width, cw: r.width, ch: r.height, vw: v.width, vh: v.height, dpr: devicePixelRatio,
        painted: c.getContext("2d").getImageData(0, 0, c.width, c.height).data.some((x, i) => i % 4 === 3 && x > 0) };
    });
    assert(live.display === "block" && live.painted, `the live line drawn in the panel (${JSON.stringify(live)})`);
    assert(live.cw <= live.vw + 2 && live.ch <= live.vh + 2, `the live canvas covers the view, not the page (${JSON.stringify(live)})`);
    near(live.w / live.cw, live.dpr, 0.02, "the live canvas at the device's pixel ratio");
    await page.mouse.move(b.x, b.y, { steps: 6 });
    await page.mouse.up();
    const saved = await until(async () => { const all = await savedStrokes(); return all.length === 1 ? all[0] : null; }, { what: "one saved stroke" });
    const samples = decodeStroke(saved.stroke);
    // a css px in the panel is a third of a point: the samples land within a point of where the pointer went
    near(samples[0].x, from.x, 1, "the stroke starts where the pointer went down, in page points");
    near(samples.at(-1).x, to.x, 1, "and ends where it lifted");
    for (const s of samples) near(s.y, y, 1, "the line's height on the page");
    assertEq(await page.locator('.pdfViewer [data-page="1"] .inkLayer path').count(), 1, "the page draws it");
    assertEq(await panel().locator(".inkLayer path").count(), 1, "and the panel draws the same stroke");
    sameBox(await box(), z, "a stroke short of the right fifth leaves the window where it is");
    // the panel's layer has no chrome: no selection menu, handles or window box of its own
    assertEq(await panel().locator(".inkZoomRect, .inkTransformHandle").count(), 0);
    first = saved;
    await page.reload();
    await waitForPdf(page, 1);
    await until(async () => (await page.locator('.pdfViewer [data-page="1"] .inkLayer path').count()) === 1, { what: "the stroke after the reload" });
    assertEq(await page.locator(".inkZoomPanel").count(), 0, "the window is session state: closed after a reload");
    const after = await savedStrokes();
    assertEq(JSON.stringify(after.map((s) => s.stroke.pts)), JSON.stringify([first.stroke.pts]), "the stored stroke unchanged");
    assertNoProblems(page);
  });

  await step("ink zoom: Previous, Next and New line move the window; a stroke in its right fifth moves it on along the line; the band drags it; a click centres it; Z closes it", async () => {
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    await page.keyboard.press("z");
    const start = await settled();
    await bar().getByRole("button", { name: "Next line", exact: true }).click();
    await until(async () => Math.abs((await box()).y - nextLine(start, PAGE).y) < 0.06, { what: "one line down" });
    sameBox(await box(), nextLine(start, PAGE), "Next line keeps x");
    await bar().getByRole("button", { name: "Previous line", exact: true }).click();
    await until(async () => Math.abs((await box()).y - start.y) < 0.06, { what: "one line up again" });
    // a stroke reaching into the right fifth: on by three fifths of the width (or to the next line at the page's edge)
    const z = await state(), y = z.y + z.h / 2;
    await draw(await inPanel(z.x + 0.7 * z.w, y), await inPanel(z.x + 0.92 * z.w, y));
    const second = await until(async () => (await savedStrokes()).find((s) => s.stroke.id !== first.stroke.id) || null, { what: "the second stroke" });
    const want = advance(z, strokeBounds(second.stroke), PAGE);
    assert(want !== z, "the stroke reached past 80 % of the window");
    await until(async () => Math.abs((await box()).x - want.x) < 0.06 && Math.abs((await box()).y - want.y) < 0.06, { what: "the window moved on" });
    near(decodeStroke(second.stroke)[0].x, z.x + 0.7 * z.w, 1, "the second stroke where it was written");
    // New line: back to where the line began, one line down (Enter does the same with nothing focused)
    const moved = await state();
    await page.evaluate(() => document.activeElement?.blur());
    await page.keyboard.press("Enter");
    await until(async () => Math.abs((await box()).y - newLine(moved, PAGE).y) < 0.06, { what: "Enter: a new line" });
    sameBox(await box(), newLine(moved, PAGE), "New line goes back to the line's start");
    // the line height steps by 2 pt
    await bar().getByRole("button", { name: "Larger line height", exact: true }).click();
    assertEq(await bar().locator(".inkZoomReadout").textContent(), "26 pt");
    await bar().getByRole("button", { name: "Smaller line height", exact: true }).click();
    assertEq(await bar().locator(".inkZoomReadout").textContent(), "24 pt");
    // 2× doubles the window's size around the line's middle
    const before = await state();
    await bar().getByRole("button", { name: "2×", exact: true }).click();
    await until(async () => (await panel().getAttribute("data-mag")) === "2", { what: "2×" });
    near((await box()).w, before.w * 1.5, 0.06, "the window at 2× is half as wide again as at 3×");
    await bar().getByRole("button", { name: "3×", exact: true }).click();
    await until(async () => (await panel().getAttribute("data-mag")) === "3", { what: "3× again" });
    // the page's band drags the window; its line starts where it lands
    const held = await state(), band = await page.locator(".pdfViewer .inkZoomBand").boundingBox();
    const k = (await page.locator('.pdfViewer [data-page="1"]').boundingBox()).width / 612;
    const dragged = moveWindow(held, { x: held.x + 30 / k, y: held.y + 60 / k }, PAGE);
    const shownBefore = await page.locator(".inkZoomPage").evaluate((el) => el.style.transform);
    await page.mouse.move(band.x + band.width / 2, band.y + band.height / 2);
    await page.mouse.down();
    await page.mouse.move(band.x + band.width / 2 + 30, band.y + band.height / 2 + 60, { steps: 6 });
    // the box previews the drag; the window itself, which every page's layers read, moves once, at the release
    await until(async () => Math.abs((await box()).y - dragged.y) < 0.6, { what: "the box follows the band" });
    assertEq(await page.locator(".inkZoomPage").evaluate((el) => el.style.transform), shownBefore, "the panel waits for the release");
    await page.mouse.up();
    await until(async () => (await page.locator(".inkZoomPage").evaluate((el) => el.style.transform)) !== shownBefore,
      { what: "the panel moved at the release" });
    await until(async () => Math.abs((await box()).y - dragged.y) < 0.6, { what: "the band dragged the window" });
    near((await box()).x, dragged.x, 0.6, "the window followed the band");
    assertEq(await page.locator(".pdfViewer [data-page='1'] .inkLayer path").count(), 2, "the drag drew nothing");
    // with the hand, a click on a blank spot centres the window there (above the box: clear of its band and corner)
    await page.keyboard.press("v");
    const spot = { x: 500, y: Math.round((await box()).y - 60) };
    const at = await onPage(spot.x, spot.y);
    await page.mouse.click(at.x, at.y);
    const centred = moveTo(await state(), 1, spot, PAGE);
    await until(async () => { const b = await box(); return Math.abs(b.x - centred.x) < 0.6 && Math.abs(b.y - centred.y) < 0.6; },
      { what: "the window centred on the click" });
    await page.keyboard.press("z");
    await until(async () => !(await page.locator(".inkZoomPanel").count()), { what: "Z closes the window" });
    assertEq(await page.locator(".pdfViewer .inkZoomRect").count(), 0, "its box goes with it");
    assertEq(await page.locator(".pdfInkBar button[aria-label='Zoom window']").count(), 0, "and the strip's toggle");
    // Escape takes it away one layer at a time: the window before the strip
    await page.keyboard.press("z");
    await panel().waitFor();
    await page.keyboard.press("Escape");
    await until(async () => !(await page.locator(".inkZoomPanel").count()), { what: "Escape closes the window" });
    assertEq(await page.locator(".pdfInkBar").count(), 1, "the strip stays");
    assertNoProblems(page);
    await ctx.close();
  });

  await step("ink zoom: Transcribe on part of a group sends those strokes as ink_selections; on a whole group, its block as a chip", async () => {
    // right of what the zoom steps wrote, two lines apart
    const strokes = [["a", 220], ["b", 300]].map(([id, y]) => encodeStroke({ id, color: "#1f1f1f", size: 2, ch: "xyt", t0: 1000,
      samples: Array.from({ length: 11 }, (_, i) => ({ x: 400 + i * 10, y, t: i * 8 })) }));
    const file = await account.api("/api/upload-ink", { method: "POST", body: { ...newInk(1, 612, 792), strokes } });
    const group = await account.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "",
      properties: { ink_url: file.url, pdf_position: file.pdf_position, ink_strokes: 2 } } });
    const tctx = await account.context(browser);
    try {
      await fakeAiModels(tctx);
      await tctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const sent = [];
      await tctx.route("**/api/ai/chat", async (route) => {
        sent.push(route.request().postDataJSON());
        await route.fulfill({ contentType: "application/x-ndjson", body: '{"delta":"It says: hello."}\n' });
      });
      page = await openPage(tctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      await waitForPdf(page, 1);
      await until(async () => (await page.locator('.pdfViewer [data-page="1"] .inkLayer path').count()) === 4, { what: "the page's four strokes" });
      // a click on stroke "a" with nothing armed selects it alone: part of its group
      const at = await onPage(450, 220);
      await page.mouse.click(at.x, at.y);
      const menu = page.getByRole("toolbar", { name: "Edit handwriting", exact: true });
      await menu.waitFor();
      await menu.getByRole("button", { name: "More", exact: true }).click();
      const row = menu.locator(".inkEditMore .ctlBtn");
      assertEq(await row.count(), 7, "seven buttons in the second row");
      assertEq(await menu.getByRole("button", { name: "New note", exact: true }).count(), 1, "part of a group: New note, not Merge");
      const transcribe = menu.getByRole("button", { name: "Transcribe", exact: true });
      assertEq(await transcribe.getAttribute("title"), "Transcribe with AI: the selected handwriting as text in the chat");
      await transcribe.click();
      await until(() => sent.length === 1, { what: "one chat request" });
      assertEq(sent[0].prompt, "Transcribe the selected handwriting.", "the request's text");
      assertEq(sent[0].ink_selections.length, 1, "one group's selection");
      const [sel] = sent[0].ink_selections;
      assertEq(JSON.stringify([sel.page_id, sel.block_id, sel.strokes.map((s) => s.id)]), JSON.stringify([pageId, group.id, ["a"]]),
        "the selected stroke alone, with its group's block and page");
      assertEq(JSON.stringify([sel.space.kind, sel.space.page]), JSON.stringify(["pdf-page", 1]), "in its group's space");
      assert(!sent[0].context_blocks.includes(group.id), "no block chip for a part of a group");
      await page.locator(".chatBubble.ai", { hasText: "It says: hello." }).waitFor();
      assertEq(await page.locator(".chatSelChip.isInk").count(), 0, "the chip went with the message");
      // Select note takes the whole group: Transcribe then attaches its block, as Transcribe with AI does
      await menu.getByRole("button", { name: "Select note", exact: true }).click();
      await until(async () => Number(await page.locator(".pdfViewer .inkSelRect").getAttribute("height")) > 60, { what: "the whole group selected" });
      await menu.getByRole("button", { name: "Transcribe", exact: true }).click();
      await until(() => sent.length === 2, { what: "a second chat request" });
      assertEq(sent[1].prompt, "Transcribe this handwriting into its caption.");
      assert(sent[1].context_blocks.includes(group.id), "the group's block rides as a chip");
      assertEq(sent[1].ink_selections.length, 0, "and no strokes inline");
      assertNoProblems(page);
    } finally { await tctx.close(); }
  });
}
