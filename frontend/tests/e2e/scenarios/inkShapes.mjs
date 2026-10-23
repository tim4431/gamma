// The handwriting strip's modifiers (docs/dev/handwriting.md "What the user
// sees"): the Shapes tool from the More menu, its row and a dragged
// rectangle stored with its shape record; the ruler (R), turned from the
// keyboard, a pen stroke begun by its edge stored as the straight line
// along it; Stamp line from More with Undo on the pill; Escape taking
// one layer at a time; the ruler going when the PDF closes; the
// calligraphy pen from the Style menu (its smoothing a default), its Nib
// angle and its stroke stored with its nib; a rectangle's corner handles,
// a drag of one stored as the rebuilt polyline; and a Fill from the
// selection's Style row. The rules themselves are node tests
// (tests/shapeTool.test.mjs, ruler.test.mjs, stripKeys.test.mjs); the iPad
// claim's `snap` is in ipad.mjs.
import { assertNear as near } from "../harness.mjs";
import { decodeStroke, encodeStroke, newInk } from "../../../src/ink/ink.js";
import { shapeSamples } from "../../../src/ink/shapes.js";
import { pickPenStyle } from "./ink.mjs";
import { waitForPdf } from "./pdf.mjs";

export async function inkShapesScenarios({ server, browser, alice, makePdf, step, until, assert, assertEq, assertNoProblems, openPage }) {
  let ctx, page, cdp, pageId;
  const paths = '[data-page="1"] .inkLayer path';
  const pdf = () => page.locator('[data-page="1"]');
  // The page's corner on the screen and its css px per pt.
  const geometry = () => pdf().evaluate((el) => {
    const box = el.getBoundingClientRect();
    return { left: box.left, top: box.top, k: box.width / el.querySelector(".inkLayer").viewBox.baseVal.width };
  });
  const point = async (x, y) => {
    const g = await geometry();
    return { x: Math.round(g.left + x * g.k), y: Math.round(g.top + y * g.k) };
  };
  // Every stroke saved on the page, over its groups.
  const savedStrokes = async () => {
    const out = [];
    for (const b of (await alice.api(`/api/blocks/${pageId}/subtree`)).block.children) {
      if (!b.properties?.ink_url?.endsWith(".ink")) continue;
      out.push(...(await alice.api(b.properties.ink_url)).strokes);
    }
    return out;
  };
  const newStroke = (before, what) => until(async () => (await savedStrokes()).find((s) => !before.has(s.id)) || null, { what });
  // The ruler as it lies: its middle, angle and length in page points.
  const rulerOf = () => page.locator('[data-page="1"] .inkRuler').evaluate((el) =>
    ({ x: Number(el.dataset.x), y: Number(el.dataset.y), angle: Number(el.dataset.angle), length: Number(el.dataset.length) }));
  const bar = () => page.locator(".pdfInkBar");
  const more = () => page.locator(".pdfInkBar button[aria-label='More handwriting tools']");
  const stripMenu = () => page.locator(".inkStripMenu");

  await step("ink shapes: More → Shapes arms the tool with its row; a dragged rectangle is saved with its shape record, even and pen:false, and stays after a reload", async () => {
    const up = await alice.upload("/api/uploads", makePdf([["Shapes and a ruler", "a page to draw on"]]), "shapes.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Shapes paper", source_url: up.source_url } });
    pageId = created.id;
    ctx = await alice.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
    cdp = await ctx.newCDPSession(page);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    assertEq(await bar().locator("button[aria-label='Shapes'], button[aria-label='Ruler']").count(), 0, "no modifier button while none is on");
    await more().click();
    await stripMenu().waitFor();
    const rulerRow = stripMenu().getByRole("menuitemcheckbox", { name: "Ruler" });
    const shapesRow = stripMenu().getByRole("menuitemcheckbox", { name: "Shapes" });
    assertEq(await rulerRow.getAttribute("aria-checked"), "false");
    assertEq(await shapesRow.getAttribute("aria-checked"), "false");
    assertEq(await rulerRow.locator(".ctxMenuKey").textContent(), "R", "the row names its key");
    const stamp = stripMenu().getByRole("menuitem", { name: "Stamp line", exact: true });
    assert(await stamp.isDisabled(), "Stamp line waits for the ruler");
    assertEq(await stamp.getAttribute("title"), "Show the ruler first");
    await shapesRow.click();
    const row = page.locator(".pdfInkSub[data-ink-options='shape']");
    await row.waitFor();
    const shapes = bar().locator("button[aria-label='Shapes']");
    assertEq(await shapes.getAttribute("aria-pressed"), "true", "a pressed Shapes button in the second group");
    assertEq(await shapes.getAttribute("data-guide"), "ink.shape");
    assertEq(await page.locator('.pdfPageWrap[data-page="1"]').evaluate((el) => getComputedStyle(el).cursor), "crosshair",
      "the crosshair over the page");
    assertEq(JSON.stringify(await row.locator(".inkShapeKinds button").evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")))),
      JSON.stringify(["Line", "Arrow", "Rectangle", "Ellipse"]));
    assertEq(await row.getByRole("button", { name: "Line", exact: true }).getAttribute("aria-pressed"), "true", "Line to start");
    const uses = await row.locator(".inkShapeUses").textContent();
    assert(/^Uses: .+ pen · [\d.]+ pt$/.test(uses), `what it draws with: ${uses}`);
    assertEq(await row.getByRole("button", { name: "Fill", exact: true }).getAttribute("aria-pressed"), "false", "no fill to start");
    await row.getByRole("button", { name: "Rectangle", exact: true }).click();
    await until(async () => (await page.evaluate(() => localStorage.getItem("gamma-ink-shape-kind"))) === "rect", { what: "the kind kept" });
    // a drag under 4 px draws nothing
    const tiny = await point(400, 600);
    await page.mouse.move(tiny.x, tiny.y);
    await page.mouse.down();
    await page.mouse.move(tiny.x + 2, tiny.y + 1, { steps: 2 });
    await page.mouse.up();
    // the rectangle, corner to corner
    const a = await point(100, 150), b = await point(250, 260);
    await page.mouse.move(a.x, a.y);
    await page.mouse.down();
    await page.mouse.move(b.x, b.y, { steps: 10 });
    await page.mouse.up();
    await until(async () => (await page.locator(paths).count()) === 1, { what: "one shape drawn" });
    const rect = await until(async () => (await savedStrokes()).find((s) => s.shape?.kind === "rect") || null, { what: "the rectangle saved" });
    assertEq((await savedStrokes()).length, 1, "the tiny drag saved nothing");
    assertEq(JSON.stringify([rect.pen, rect.ch, rect.brush]), JSON.stringify([false, "xyt", "monoline"]), "an even Ballpoint line, no pressure");
    const p = rect.shape.params;
    near(p.cx, 175, 1, "cx"); near(p.cy, 205, 1, "cy"); near(p.w, 150, 1.5, "w"); near(p.h, 110, 1.5, "h");
    assertEq(p.angle, 0, "axis-aligned");
    const samples = decodeStroke(rect);
    assertEq(samples.length, shapeSamples("rect", p).length, "the dense closed polyline of its params");
    assert(samples[0].x === samples.at(-1).x && samples[0].y === samples.at(-1).y, "closed");
    assert(!("fill" in rect), "Fill off: no fill");
    assert(Number.isFinite(rect.t0), "the drag's wall clock");
    await page.reload();
    await waitForPdf(page, 1);
    await until(async () => (await page.locator(paths).count()) === 1, { what: "the rectangle after a reload", timeout: 15000 });
    assertNoProblems(page);
  });

  await step("ink shapes: R lays the ruler with its row; turned from the keyboard, a pen stroke begun by its edge is saved as the straight line along it", async () => {
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar .inkToolBtn.modeActive .inkToolInk");
    await page.keyboard.press("r");
    await page.waitForSelector('[data-page="1"] .inkRuler .inkRulerBand');   // the band: the ruler's own box has no height
    await page.waitForSelector(".pdfInkSub[data-ink-options='ruler']");
    const rulerBtn = bar().locator("button[aria-label='Ruler']");
    assertEq(await rulerBtn.getAttribute("aria-pressed"), "true");
    assertEq(await rulerBtn.getAttribute("data-guide"), "ink.ruler");
    let r = await rulerOf();
    assertEq(r.angle, 0, "level to start");
    const { k } = await geometry();
    const band = await page.locator(".inkRulerBand").boundingBox();
    near(band.height, 36, 0.5, "the band is 36 css px on the screen");
    near(band.width, r.length * k, 1, "as long as the ruler");
    assertEq(await page.locator(".inkRulerAngle").textContent(), "Angle 0°");
    // the knob: Shift + ↑ to the next 15°, twice
    const knob = page.locator(".inkRulerKnob");
    assertEq(await knob.getAttribute("role"), "slider");
    await knob.focus();
    await page.keyboard.press("Shift+ArrowUp");
    await page.keyboard.press("Shift+ArrowUp");
    await until(async () => (await rulerOf()).angle === 30, { what: "the ruler at 30°" });
    assertEq(await knob.getAttribute("aria-valuenow"), "30");
    assertEq(await page.locator(".inkRulerAngle").textContent(), "Angle 30°");
    // the band's arrows move it
    await page.locator(".inkRulerBand").focus();
    await page.keyboard.press("Shift+ArrowDown");
    await until(async () => Math.abs((await rulerOf()).y - (r.y + 10)) < 0.01, { what: "the ruler 10 pt down" });
    r = await rulerOf();
    // the first preset, a pen; its stroke starts 6 px outside the counterclockwise edge and wanders along it
    await page.keyboard.press("1");
    const g = await geometry();
    const rad = (r.angle * Math.PI) / 180, d = { x: Math.cos(rad), y: -Math.sin(rad) }, n = { x: d.y, y: -d.x };
    const half = 18 / g.k;
    const at = (u, v) => ({ x: g.left + (r.x + d.x * u + n.x * v) * g.k, y: g.top + (r.y + d.y * u + n.y * v) * g.k });
    const before = new Set((await savedStrokes()).map((s) => s.id));
    const pts = Array.from({ length: 13 }, (_, i) => at(-r.length / 4 + (i * r.length) / 24, half + (i ? (i % 2 ? 9 : -3) : 6) / g.k));
    await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x: pts[0].x, y: pts[0].y, pointerType: "pen", button: "left", buttons: 1, clickCount: 1, force: 0.5 });
    for (const q of pts.slice(1)) {
      await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: q.x, y: q.y, pointerType: "pen", button: "left", buttons: 1, force: 0.5 });
    }
    const last = pts.at(-1);
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: last.x, y: last.y, pointerType: "pen", button: "left", buttons: 0, clickCount: 1 });
    const line = await newStroke(before, "the ruler line saved");
    assertEq(line.shape?.kind, "line", "a line shape record");
    assertEq(JSON.stringify([line.pen, line.ch]), JSON.stringify([false, "xyt"]), "even end to end, as a held line");
    const q = line.shape.params;
    const ang = (Math.atan2(-(q.y2 - q.y1), q.x2 - q.x1) * 180) / Math.PI;
    near(ang, 30, 0.1, "along the ruler's angle");
    // both ends on the edge: half the band out from the middle line
    for (const [x, y] of [[q.x1, q.y1], [q.x2, q.y2]]) near((x - r.x) * n.x + (y - r.y) * n.y, half, 0.03, "on the edge");
    const span = Math.hypot(q.x2 - q.x1, q.y2 - q.y1);
    near(span, r.length / 2, 1, "the farthest points the pen reached along it");
    const samples = decodeStroke(line);
    assertEq(samples.length, 2, "two samples");
    // its history entry is a straight line's: undone and redone by name (each step saved before the next)
    await page.keyboard.press("Control+z");
    await page.getByText("Undone: ink straight line (page 1).", { exact: true }).waitFor();
    await until(async () => !(await savedStrokes()).some((s) => s.id === line.id), { what: "the ruler line undone" });
    await page.keyboard.press("Control+Shift+z");
    await page.getByText("Redone: ink straight line (page 1).", { exact: true }).waitFor();
    await until(async () => (await savedStrokes()).some((s) => s.id === line.id), { what: "the ruler line back" });
    assertNoProblems(page);
  });

  await step("ink shapes: More → Stamp line draws the ruler's middle line end to end with the armed pen; the pill's Undo takes it back", async () => {
    await more().click();
    await stripMenu().waitFor();
    assertEq(await stripMenu().getByRole("menuitemcheckbox", { name: "Ruler" }).getAttribute("aria-checked"), "true", "ticked while it shows");
    const r = await rulerOf();
    const before = new Set((await savedStrokes()).map((s) => s.id));
    const armed = await bar().locator(".inkToolBtn.modeActive .inkToolInk").evaluate((el) => getComputedStyle(el).backgroundColor);
    await stripMenu().getByRole("menuitem", { name: "Stamp line", exact: true }).click();
    await page.locator(".statusPill").getByText("Line stamped along the ruler.").waitFor();
    const stamped = await newStroke(before, "the stamped line saved");
    assertEq(JSON.stringify([stamped.shape?.kind, stamped.ch, stamped.pen]), JSON.stringify(["line", "xyt", false]));
    const rad = (r.angle * Math.PI) / 180, d = { x: Math.cos(rad), y: -Math.sin(rad) };
    const p = stamped.shape.params;
    near(p.x1, r.x - (d.x * r.length) / 2, 0.02, "x1, the start end"); near(p.y1, r.y - (d.y * r.length) / 2, 0.02, "y1");
    near(p.x2, r.x + (d.x * r.length) / 2, 0.02, "x2, the other end"); near(p.y2, r.y + (d.y * r.length) / 2, 0.02, "y2");
    const color = await page.evaluate((hex) => { const el = document.createElement("span"); el.style.color = hex; document.body.append(el);
      const c = getComputedStyle(el).color; el.remove(); return c; }, stamped.color);
    assertEq(color, armed, "in the armed pen's colour");
    // the row has it too
    await bar().locator("button[aria-label='Ruler']").click();
    await page.locator(".pdfInkSub[data-ink-options='ruler']").getByRole("button", { name: "Stamp line", exact: true }).waitFor();
    await page.locator(".statusPill").getByRole("button", { name: "Undo", exact: true }).click();
    await page.getByText("Undone: ink straight line (page 1).", { exact: true }).waitFor();
    await until(async () => !(await savedStrokes()).some((s) => s.id === stamped.id), { what: "the stamped line undone" });
    assertNoProblems(page);
  });

  await step("ink shapes: Escape takes one layer at a time — the open row, the Shapes tool (the pen back), the ruler, then the strip", async () => {
    const armed = async () => (await bar().locator(".inkToolBtn.modeActive").first().getAttribute("aria-label")) || "";
    const pen = (await armed()).split(" · ")[0];
    await page.keyboard.press("s");
    await page.waitForSelector(".pdfInkSub[data-ink-options='shape']");
    await bar().locator("button[aria-label='Ruler']").click();
    await page.waitForSelector(".pdfInkSub[data-ink-options='ruler']");
    assertEq(await page.locator(".pdfInkSub[data-ink-options='shape']").count(), 0, "one row at a time");
    await page.keyboard.press("Escape");
    await until(async () => (await page.locator(".pdfInkSub").count()) === 0, { what: "the row closed" });
    assertEq(await bar().locator("button[aria-label='Shapes']").count(), 1, "the Shapes tool still armed");
    await page.keyboard.press("Escape");
    await until(async () => (await bar().locator("button[aria-label='Shapes']").count()) === 0, { what: "the Shapes tool off" });
    assertEq((await armed()).split(" · ")[0], pen, "the tool armed before it is back");
    assertEq(await page.locator('[data-page="1"] .inkRuler').count(), 1, "the ruler stays");
    await page.keyboard.press("Escape");
    await until(async () => (await page.locator('[data-page="1"] .inkRuler').count()) === 0, { what: "the ruler gone" });
    assertEq(await bar().count(), 1, "the strip stays open");
    await page.keyboard.press("Escape");
    await until(async () => (await bar().count()) === 0, { what: "the strip closed" });
    // closing the strip took the ruler with it: R after reopening lays a new one
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("r");
    await page.waitForSelector('[data-page="1"] .inkRuler .inkRulerBand');   // the band: the ruler's own box has no height
    await page.click("button[aria-label='Handwriting tools']");
    await until(async () => (await page.locator('[data-page="1"] .inkRuler').count()) === 0, { what: "the ruler gone with the strip" });
    assertNoProblems(page);
  });

  await step("ink shapes: closing the PDF takes the ruler with it: the PDF shown again has no ruler and no pressed Ruler button", async () => {
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("r");
    await page.waitForSelector('[data-page="1"] .inkRuler .inkRulerBand');
    assertEq(await bar().locator("button[aria-label='Ruler']").count(), 1, "the pressed Ruler while it shows");
    await page.getByRole("button", { name: "Close PDF", exact: true }).click();
    await until(async () => (await page.locator(".viewerWrap.pdfHidden").count()) === 1, { what: "the PDF closed" });
    // a jump to a group from the notes shows the PDF again
    await page.locator(".blockInkCard").first().click();
    await until(async () => (await page.locator(".viewerWrap.pdfHidden").count()) === 0, { what: "the PDF shown again" });
    await page.waitForSelector(".pdfInkBar");
    await page.waitForSelector('[data-page="1"] .inkLayer');
    assertEq(await page.locator('[data-page="1"] .inkRuler').count(), 0, "no ruler on the page");
    assertEq(await bar().locator("button[aria-label='Ruler']").count(), 0, "nor a pressed Ruler in the strip");
    assertEq(await page.locator(".pdfInkSub[data-ink-options='ruler']").count(), 0, "nor the ruler's row");
    assertNoProblems(page);
  });
  // Later steps and groups on this worker start from the defaults. The page
  // closes first: an account without a value is seeded from the browser
  // (docs/dev/settings.md), so its next load would push its own copy back.
  await ctx?.close().catch(() => {});
  await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkShapeKind: null, inkShapeFill: null } } }).catch(() => {});

  // --- the calligraphy pen, shape handles and fills -------------
  // The armed preset's options row.
  const openOptions = async () => {
    if (await page.locator(".pdfInkSub[data-ink-options='tool']").count()) return;
    await bar().locator(".inkToolBtn.modeActive").first().click();
    await page.waitForSelector(".pdfInkSub[data-ink-options='tool']");
  };
  const firstPreset = async () => ((await alice.api("/api/prefs/profile")).value?.inkTools || [])[0] || null;
  const editMenu = () => page.getByRole("toolbar", { name: "Edit handwriting", exact: true });

  await step("ink shapes: Calligraphy from the Style menu smooths at 2 and shows the Nib angle; its stroke is saved with its brush, its nib and the widths that nib gives, and drawn as the nib", async () => {
    try {
      // a page of its own, so no stroke of the steps before lies under these
      const up = await alice.upload("/api/uploads", makePdf([["The calligraphy pen", "shape handles and fills"]]), "nib.pdf", "application/pdf");
      pageId = (await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Nib paper", source_url: up.source_url } })).id;
      ctx = await alice.context(browser);
      page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
      await waitForPdf(page, 1);
      await page.click("button[aria-label='Handwriting tools']");
      await page.waitForSelector(".pdfInkBar .inkToolBtn.modeActive .inkToolInk");
      await page.keyboard.press("1");
      await openOptions();
      await pickPenStyle(page, "Calligraphy");
      const armed = await bar().locator(".inkToolBtn.modeActive").first().getAttribute("aria-label");
      assert(/ calligraphy pen · /.test(armed), `the preset is named a calligraphy pen (${armed})`);
      await until(async () => { const p = await firstPreset(); return p?.brush === "calligraphy" && !("stabilize" in p) && !("nib" in p); },
        { what: "the account's preset: calligraphy, its smoothing and its nib at their defaults (left out)" });
      // the Smoothing ladder shows the 2 a calligraphy pen smooths at without a level of its own
      assertEq(await page.locator(".pdfInkSub").getByRole("button", { name: "Smoothing 2 of 4", exact: true }).getAttribute("aria-pressed"), "true",
        "a calligraphy pen smooths at 2");
      // the Nib angle: − and + around the degrees for a mouse as for a finger, 45° to start, 15° a step; no Lock angle outside the iPad app
      const nib = page.locator(".pdfInkSub").getByRole("group", { name: "Nib angle", exact: true });
      await nib.waitFor();
      assertEq((await nib.locator(".inkNibReadout").textContent()).trim(), "45°");
      assertEq(await nib.locator(".inkNibPreview").count(), 1, "the nib drawn at its angle");
      assertEq(await nib.locator("input").count(), 0, "no slider: the same control set for every pointer");
      assertEq(JSON.stringify(await nib.locator("button").evaluateAll((els) => els.map((el) => [el.getAttribute("aria-label"), el.disabled]))),
        JSON.stringify([["Nib angle −15°", false], ["Nib angle +15°", false]]), "− and + around the readout, neither at its end");
      await nib.getByRole("button", { name: "Nib angle −15°", exact: true }).click();
      await until(async () => (await nib.locator(".inkNibReadout").textContent()).trim() === "30°", { what: "a step down to 30°" });
      await until(async () => (await firstPreset())?.nib === 30, { what: "the preset's nib on the account" });
      assertEq(await page.locator(".pdfInkSub button[aria-label='Lock angle']").count(), 0, "Lock angle only where the pen reports its roll");
      await bar().locator(".inkToolBtn.modeActive").first().click();            // the row closes
      // the hover footprint is the nib, turned to the angle
      const a = await point(100, 150), b = await point(220, 270);
      await page.mouse.move(a.x - 30, a.y);
      await page.mouse.move(a.x, a.y, { steps: 3 });
      await until(async () => page.locator('[data-page="1"] .inkCursor').evaluate((el) => el.dataset.brush === "calligraphy"
        && el.style.getPropertyValue("--nib") === "30"), { what: "the nib's footprint at 30°" });
      // a stroke down to the right, across a 30° nib (sin 75° of its length)
      const before = new Set((await savedStrokes()).map((s) => s.id));
      await page.mouse.down();
      await page.mouse.move(b.x, b.y, { steps: 16 });
      await page.mouse.up();
      const cal = await newStroke(before, "the calligraphy stroke saved");
      assertEq(JSON.stringify([cal.brush, cal.nib, cal.ch, cal.pen]), JSON.stringify(["calligraphy", 30, "xytw", false]),
        "a mouse's calligraphy stroke: its widths from the direction alone");
      const ws = decodeStroke(cal).map((q) => q.w), mid = ws[Math.floor(ws.length / 2)];
      near(mid, Math.max(0.12, Math.abs(Math.sin(Math.PI / 6) + Math.cos(Math.PI / 6)) / Math.SQRT2), 0.03, "w in the middle: the sine against the nib");
      const drawn = await until(async () => page.locator(`[data-page="1"] .inkLayer path`).evaluateAll((els) =>
        els.map((el) => el.getAttribute("d") || "").find((d) => (d.match(/Z/g) || []).length >= 3) || null), { what: "the stroke drawn as the nib's pieces" });
      assert(!/Q/.test(drawn), "the nib's straight-sided pieces, not perfect-freehand's curves");
      assertNoProblems(page);
    } finally {
      // preset 1 back to the defaults for the steps and scenarios after; the
      // page closes first, or its next load would push its calligraphy pen back
      await ctx?.close().catch(() => {});
      await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: null } } }).catch(() => {});
    }
  });

  await step("ink shapes: a rectangle alone in the selection shows its corner handles; a drag of one rebuilds the polyline from the new params, keeps the record and id, and undoes as one 'ink shape edit'", async () => {
    // a rectangle as the Shapes tool or a hold commits it, in a group of its own
    const params = { cx: 400, cy: 200, w: 120, h: 80, angle: 0 };
    const ink = { ...newInk(1, 612, 792), strokes: [encodeStroke({ id: "box", brush: "monoline", pen: false, color: "#1d4ed8", size: 2, ch: "xyt", t0: 1000,
      shape: { kind: "rect", params }, samples: shapeSamples("rect", params, { span: 400 }) })] };
    const file = await alice.api("/api/upload-ink", { method: "POST", body: ink });
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "A box",
      properties: { ink_url: file.url, pdf_position: file.pdf_position, ink_strokes: 1 } } });
    ctx = await alice.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    await page.keyboard.press("v");                                     // the hand: a click selects
    const edge = await point(400, 160);
    await until(async () => {
      await page.mouse.click(edge.x, edge.y);
      return (await page.locator('[data-page="1"] .inkShapeHandle').count()) === 4;
    }, { what: "the rectangle selected, its four corners shown" });
    const handles = page.locator('[data-page="1"] .inkShapeHandle');
    assertEq(JSON.stringify(await handles.evaluateAll((els) => els.map((el) => [el.dataset.handle, el.getAttribute("aria-label")]))),
      JSON.stringify([["c0", "Corner 1"], ["c1", "Corner 2"], ["c2", "Corner 3"], ["c3", "Corner 4"]]));
    for (const box of await handles.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON()))) {
      near(box.width, 28, 0.5, "a 28 px target"); near(box.height, 28, 0.5, "a 28 px target");
    }
    // the keyboard's focus shows one ring, the dot's: the 28 px button around it draws none
    await handles.first().focus();
    await page.keyboard.press("Tab");
    assertEq(JSON.stringify(await page.evaluate(() => {
      const el = document.activeElement, ring = (e) => getComputedStyle(e).outlineStyle;
      return [el.dataset.handle, el.matches(":focus-visible"), ring(el), ring(el.querySelector(".inkShapeDot"))];
    })), JSON.stringify(["c1", true, "none", "solid"]), "Tab to the next corner: its dot ringed, the button not");
    const corner = await point(460, 240);   // c2, the bottom-right corner, at its centre
    const c2 = await page.locator('[data-page="1"] .inkShapeHandle[data-handle="c2"]').boundingBox();
    near(c2.x + c2.width / 2, corner.x, 1.5, "c2 on its corner"); near(c2.y + c2.height / 2, corner.y, 1.5, "c2 on its corner");
    // drag it 40 pt right and 30 pt down: the opposite corner stays
    const to = await point(500, 270);
    await page.mouse.move(corner.x, corner.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: 8 });
    await until(async () => (await page.locator('[data-page="1"] .inkReshape').count()) === 1, { what: "the reshaped stroke previewed" });
    await page.mouse.up();
    const edited = await until(async () => {
      const s = (await savedStrokes()).find((st) => st.id === "box");
      return s && s.shape.params.w > 150 ? s : null;
    }, { what: "the reshaped rectangle saved" });
    const { k } = await geometry();
    const p = edited.shape.params;
    assertEq(edited.shape.kind, "rect");
    near(p.w, 160, 2 / k + 0.02, "w: from the fixed corner to the pointer"); near(p.h, 110, 2 / k + 0.02, "h");
    near(p.cx - p.w / 2, 340, 0.02, "the opposite corner's x stays"); near(p.cy - p.h / 2, 160, 0.02, "and its y");
    const samples = decodeStroke(edited), rebuilt = shapeSamples("rect", p, { span: 400 });
    assertEq(samples.length, rebuilt.length, "the dense polyline of the new params");
    assert(samples.every((q, i) => Math.abs(q.x - rebuilt[i].x) < 0.011 && Math.abs(q.y - rebuilt[i].y) < 0.011), "sample for sample");
    assertEq(JSON.stringify([edited.ch, edited.pen, edited.brush, edited.t0, samples.at(-1).t]), JSON.stringify(["xyt", false, "monoline", 1000, 400]),
      "its look and its timing kept");
    // one undo entry
    await page.keyboard.press("Control+z");
    await page.locator(".statusPill").getByText(/ink shape edit/).waitFor();
    await until(async () => (await savedStrokes()).find((st) => st.id === "box")?.shape.params.w === 120, { what: "the rectangle as it was" });
    assertNoProblems(page);
  });

  await step("ink shapes: Fill in the selection's Style row stores a fill on the closed shape and draws it under the outline; None clears it", async () => {
    const edge = await point(400, 160);
    if (!(await editMenu().count())) {
      await until(async () => { await page.mouse.click(edge.x, edge.y); return (await editMenu().count()) === 1; }, { what: "the rectangle selected" });
    }
    await editMenu().getByRole("button", { name: "Style", exact: true }).click();
    const fill = editMenu().getByRole("group", { name: "Fill", exact: true });
    await fill.waitFor();
    assertEq(await fill.getByRole("button", { name: "No fill", exact: true }).getAttribute("aria-pressed"), "true", "no fill to start");
    const swatch = fill.locator("button.inkSwatch").first();
    await swatch.click();
    const tinted = await until(async () => (await savedStrokes()).find((st) => st.id === "box" && st.fill) || null, { what: "the fill saved" });
    assert(/^#[0-9a-f]{6}$/.test(tinted.fill.color) && !("opacity" in tinted.fill), `a colour, the default opacity left out (${JSON.stringify(tinted.fill)})`);
    const painted = page.locator('[data-page="1"] .inkLayer path.inkFill');
    await until(async () => (await painted.count()) === 1, { what: "the fill drawn" });
    assertEq(JSON.stringify(await painted.evaluate((el) => [el.getAttribute("fill"), el.getAttribute("fill-opacity"), el.getAttribute("d").trim().endsWith("Z")])),
      JSON.stringify([tinted.fill.color, "0.25", true]), "a closed polygon in the fill's colour, a quarter opaque");
    assert(await painted.evaluate((el) => !!el.nextElementSibling && el.nextElementSibling.tagName === "path"), "under the outline");
    await until(async () => (await swatch.getAttribute("aria-pressed")) === "true", { what: "the swatch pressed" });
    assertEq(await fill.getByRole("button", { name: "No fill", exact: true }).getAttribute("aria-pressed"), "false");
    await fill.getByRole("button", { name: "No fill", exact: true }).click();
    await until(async () => { const s = (await savedStrokes()).find((st) => st.id === "box"); return s && !s.fill; }, { what: "the fill cleared" });
    await until(async () => (await painted.count()) === 0, { what: "and no longer drawn" });
    assertNoProblems(page);
  });
  await ctx?.close().catch(() => {});
}
