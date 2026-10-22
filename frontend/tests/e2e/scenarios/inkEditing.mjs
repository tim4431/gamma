import { encodeStroke, newInk, decodeStroke, strokeBounds } from "../../../src/ink/ink.js";
import { waitForPdf } from "./pdf.mjs";
import { assertNear as near, assertMenuFrom, assertOpensInPlace } from "../harness.mjs";

export async function inkEditingScenarios({ server, browser, alice, bob, makePdf, step, until, assert, assertEq, assertNoProblems, openPage, flags }) {
  let ctx, page, cdp, pageId, noteId, secondId;
  const paths = '[data-page="1"] .inkLayer path';
  const menu = () => page.getByRole("toolbar", { name: "Edit handwriting", exact: true });
  const stored = async (id = noteId) => {
    const d = await alice.api(`/api/blocks/${pageId}/subtree`);
    const block = d.block.children.find((b) => b.id === id);
    return block?.properties?.ink_url ? alice.api(block.properties.ink_url) : null;
  };
  // Every ink block on the page with its file, in tree order (a paste or a
  // split adds one, a merge or a cut emptying one removes it).
  const inkBlocks = async () => {
    const d = await alice.api(`/api/blocks/${pageId}/subtree`);
    const out = [];
    for (const b of d.block.children) if (b.properties?.ink_url) out.push({ id: b.id, ink: await alice.api(b.properties.ink_url) });
    return out;
  };
  const moreRow = async () => {
    const more = menu().getByRole("button", { name: "More", exact: true });
    if (await more.getAttribute("aria-expanded") !== "true") await more.tap();
    await menu().getByRole("button", { name: "Cut", exact: true }).waitFor();
  };
  const point = async (x, y) => page.locator('[data-page="1"]').evaluate((el, [x, y]) => {
    const rect = el.getBoundingClientRect(), k = rect.width / 612;
    return { x: Math.round(rect.left + x * k), y: Math.round(rect.top + y * k) };
  }, [x, y]);
  // A tap that selects nothing has failed only on a loaded 4-core machine,
  // with the tap on the stroke; the error says what was under it.
  const tapInk = async (x = 130, y = 220) => {
    const p = await point(x, y);
    await page.touchscreen.tap(p.x, p.y);
    try {
      await menu().waitFor({ timeout: 10000 });
    } catch (e) {
      const seen = await page.evaluate(([x, y, paths]) => {
        const el = document.elementFromPoint(x, y);
        return { at: [x, y], under: el ? `${el.tagName.toLowerCase()}.${[...el.classList].join(".")}` : null,
          strokes: [...document.querySelectorAll(paths)].map((s) => { const r = s.getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round); }),
          selection: !!document.querySelector(".inkSelectionHit"), menus: document.querySelectorAll(".inkEditMenu").length };
      }, [p.x, p.y, paths]);
      throw new Error(`the tap selected no ink: ${JSON.stringify(seen)}`);
    }
  };
  const assertHandlesFollowSelection = async () => {
    await until(async () => page.locator('[data-page="1"]').evaluate((el) => {
      const selection = el.querySelector(".inkSelRect"), matrix = selection?.getScreenCTM();
      if (!matrix) return false;
      return ["resize", "rotate"].every((mode) => {
        const widget = el.querySelector(`.inkTransform-${mode}`).getBoundingClientRect();
        const x = selection.x.baseVal.value + selection.width.baseVal.value - 4;
        const y = mode === "resize" ? selection.y.baseVal.value + selection.height.baseVal.value - 4 : selection.y.baseVal.value + 4;
        const corner = new DOMPoint(x, y).matrixTransform(matrix);
        return Math.abs(widget.x - corner.x) < 2 && Math.abs((mode === "resize" ? widget.y : widget.y + widget.height) - corner.y) < 2
          && Math.abs(widget.width - 28) < 1 && Math.abs(widget.height - 28) < 1;
      });
    }), { what: "widgets follow preview corners at a constant screen size" });
  };
  const dragTouch = async (from, to, beforeLift) => {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [from] });
    for (let i = 1; i <= 12; i++) await cdp.send("Input.dispatchTouchEvent", {
      type: "touchMove", touchPoints: [{ x: from.x + (to.x - from.x) * i / 12, y: from.y + (to.y - from.y) * i / 12 }],
    });
    await beforeLift?.();
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  };

  await step("ink edit: finger tap selects thin ink and exposes edits without changing the pen", async () => {
    const up = await alice.upload("/api/uploads", makePdf([["Touch editing"]]), "touch-edit.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Touch ink", source_url: up.source_url } });
    pageId = created.id;
    const addInk = async (id, y, color, size) => {
      const ink = { ...newInk(1, 612, 792), strokes: [encodeStroke({ id, color, size, ch: "xypt", t0: 1000,
        samples: Array.from({ length: 13 }, (_, i) => ({ x: 80 + i * 10, y, p: 0.2 + i * 0.05, t: i * 8 })) })] };
      const file = await alice.api("/api/upload-ink", { method: "POST", body: ink });
      return (await alice.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: `Ink ${id}`,
        properties: { ink_url: file.url, pdf_position: file.pdf_position, ink_strokes: 1 } } })).id;
    };
    noteId = await addInk("thin", 220, "#1f1f1f", 0.6);
    secondId = await addInk("other", 270, "#1d4ed8", 2);
    ctx = await alice.context(browser, { hasTouch: true });
    await ctx.addInitScript(() => localStorage.setItem("gamma-ink-pen-only", "1"));
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
    cdp = await ctx.newCDPSession(page);
    await waitForPdf(page, 1);
    await until(async () => await page.locator(paths).count() === 2);
    await page.getByRole("button", { name: "Handwriting tools", exact: true }).tap();
    const pen = await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label");
    // Near a sub-pixel line: hit tolerance is in screen pixels. The menu
    // knows its height before it is painted, so it opens on its side.
    await assertOpensInPlace(page, ".inkEditMenu", () => tapInk(130, 223), { what: "the selection menu" });
    assertEq(await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label"), pen);
    assertEq(await page.locator(paths).count(), 2);
    assert(await page.locator('[aria-label="Undo ink"]').isDisabled(), "selection is not an edit");
    await page.keyboard.press("Control+z");
    await page.getByText("Nothing to undo in handwriting.", { exact: true }).waitFor();
    assertEq(await page.locator(paths).count(), 2, "empty ink history does not fall through to note undo");
    // Row one is fixed; More adds the second row, every button a 36 px target.
    for (const name of ["Color", "Width", "Style", "Copy", "Duplicate", "More", "Delete"]) {
      const box = await menu().getByRole("button", { name, exact: true }).boundingBox();
      assert(box.width >= 36 && box.height >= 36, `${name} retains a touch target`);
    }
    assertEq(await menu().getByRole("button", { name: "Style", exact: true }).getAttribute("aria-expanded"), "false", "the Style row is closed until asked for");
    assertEq(await menu().getByRole("button", { name: "More", exact: true }).getAttribute("aria-expanded"), "false");
    await menu().getByRole("button", { name: "More", exact: true }).tap();
    assertEq(await menu().getByRole("button", { name: "More", exact: true }).getAttribute("aria-expanded"), "true");
    // (Merge into note in New note's place: the selection is this note's whole drawing and the page has another)
    for (const name of ["Cut", "Flip horizontal", "Flip vertical", "Merge into note", "Select note", "Show note", "Transcribe"]) {
      const box = await menu().getByRole("button", { name, exact: true }).boundingBox();
      assert(box.width >= 36 && box.height >= 36, `${name} retains a touch target`);
    }
    assertEq(await menu().getByRole("button", { name: "New note", exact: true }).count(), 0, "New note gives Merge into note its place");
    assertEq(await menu().locator(".inkEditMore .ctlBtn").count(), 7, "at most seven buttons in the second row");
    await menu().getByRole("button", { name: "Duplicate", exact: true }).hover();
    await page.getByRole("tooltip").filter({ hasText: "Duplicate" }).waitFor();
    assertNoProblems(page);
  });

  await step("ink edit: color and width affect only selected ink, persist samples, and use visible undo/redo", async () => {
    const before = await stored();
    // The menu grows and moves up to fit in one frame, never grown in place first.
    await assertOpensInPlace(page, ".inkEditMenu", () => menu().getByRole("button", { name: "Color", exact: true }).tap(), { what: "the menu opening its colours" });
    await menu().getByRole("button", { name: "Red", exact: true }).tap();
    await menu().getByRole("button", { name: "Width", exact: true }).tap();
    await menu().getByRole("button", { name: "Pen width 4 pt", exact: true }).tap();
    await until(async () => (await stored())?.strokes[0].size === 4 && (await stored()).strokes[0].color === "#dc2626");
    assertEq(JSON.stringify((await stored()).strokes[0].pts), JSON.stringify(before.strokes[0].pts), "pressure/time unchanged");
    assertEq((await stored(secondId)).strokes[0].color, "#1d4ed8");
    assertEq(await page.locator(paths).count(), 2, "menu touches add no ink");
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await page.getByText("Undone: ink width change (page 1).", { exact: true }).waitFor();
    await until(async () => (await stored()).strokes[0].size === 0.6);
    assertEq((await stored()).strokes[0].color, "#dc2626", "one edit per undo");
    await page.getByRole("button", { name: "Redo ink", exact: true }).tap();
    await page.getByText("Redone: ink width change (page 1).", { exact: true }).waitFor();
    await until(async () => (await stored()).strokes[0].size === 4);
    await page.reload();
    await waitForPdf(page, 1);
    await until(async () => await page.locator(paths).count() === 2);
    await tapInk();
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-edit-menu.png` });
    assertNoProblems(page);
  });

  await step("ink edit: duplicate selects its new IDs; delete and undo restore the right strokes", async () => {
    await menu().getByRole("button", { name: "Duplicate", exact: true }).tap();
    await until(async () => (await stored())?.strokes.length === 2);
    const copies = (await stored()).strokes;
    assertEq(copies[0].id, "thin");
    assert(copies[1].id !== "thin", "fresh copy id");
    assert(decodeStroke(copies[1])[0].x > decodeStroke(copies[0])[0].x, "copy offset");
    await menu().getByRole("button", { name: "Delete", exact: true }).tap();
    await until(async () => (await stored())?.strokes.length === 1);
    assertEq((await stored()).strokes[0].id, "thin", "delete removes the copy");
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await until(async () => (await stored())?.strokes.length === 2);
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await until(async () => (await stored())?.strokes.length === 1);
    assertEq(await page.locator(paths).count(), 2);
    assertNoProblems(page);
  });

  await step("ink edit: Ctrl+C and Ctrl+V paste fresh ids at the page centre with the lasso armed; Ctrl+X cuts and pastes back", async () => {
    // After the reload above the strip stands with the hand (session state);
    // Ctrl+C needs only a selection, Ctrl+V the strip open with a tool armed.
    if (!await page.locator(".pdfInkBar").count()) await page.getByRole("button", { name: "Handwriting tools", exact: true }).tap();
    if (await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label") === "Hand") {
      await page.getByRole("button", { name: / pen · .* · key 1\b/ }).tap();
    }
    await tapInk();
    const original = (await stored()).strokes[0];
    await page.keyboard.press("Control+c");
    await page.getByText("Copied 1 stroke.", { exact: true }).waitFor();
    assertEq((await inkBlocks()).length, 2, "copy adds nothing");
    await page.keyboard.press("Control+v");
    // No group is current on the page (nothing was drawn here), so the paste
    // starts one: a third ink block holding the copy.
    await until(async () => (await inkBlocks()).length === 3, { what: "the paste lands in a group of its own" });
    const added = (await inkBlocks()).find((b) => ![noteId, secondId].includes(b.id));
    assertEq(added.ink.strokes.length, 1);
    const pasted = added.ink.strokes[0];
    assert(pasted.id !== "thin", "a pasted stroke gets a fresh id");
    assertEq(JSON.stringify(decodeStroke(pasted).map((q) => [q.p, q.t])), JSON.stringify(decodeStroke(original).map((q) => [q.p, q.t])), "pressure and timing kept");
    const b = strokeBounds(pasted);
    assert(Math.abs((b[0] + b[2]) / 2 - 306) < 1 && Math.abs((b[1] + b[3]) / 2 - 396) < 1, `the copied box is centred on the page: ${b}`);
    await menu().waitFor();
    assert(/^Lasso/.test(await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label")), "the pasted strokes are selected with the lasso");
    await page.keyboard.press("Control+x");
    await page.getByText("Cut 1 stroke.", { exact: true }).waitFor();
    await until(async () => (await inkBlocks()).length === 2, { what: "the cut empties its group, whose block goes" });
    await page.keyboard.press("Control+v");
    await until(async () => (await inkBlocks()).length === 3, { what: "the cut strokes paste back" });
    const again = (await inkBlocks()).find((b) => ![noteId, secondId].includes(b.id));
    assert(again.ink.strokes[0].id !== pasted.id && again.ink.strokes[0].id !== "thin", "another fresh id");
    await menu().waitFor();
    await menu().getByRole("button", { name: "Delete", exact: true }).tap();
    await until(async () => (await inkBlocks()).length === 2);
    assertEq(await page.locator(paths).count(), 2);
    assertNoProblems(page);
  });

  await step("ink edit: flip horizontal mirrors the samples about the centre; a second flip restores them", async () => {
    await tapInk();
    const before = (await stored()).strokes[0], box = strokeBounds(before), cx = (box[0] + box[2]) / 2;
    await moreRow();
    await menu().getByRole("button", { name: "Flip horizontal", exact: true }).tap();
    await until(async () => JSON.stringify((await stored()).strokes[0].pts) !== JSON.stringify(before.pts), { what: "the flip is saved" });
    const a = decodeStroke(before), f = decodeStroke((await stored()).strokes[0]);
    assertEq(f.length, a.length);
    assert(f.every((q, i) => Math.abs(q.x - (2 * cx - a[i].x)) < 0.02), "x mirrored about the box centre");
    assert(f.every((q, i) => q.y === a[i].y && q.p === a[i].p && q.t === a[i].t), "y, pressure and timing untouched");
    assertEq(await menu().count(), 1, "the selection stays");
    await menu().getByRole("button", { name: "Flip horizontal", exact: true }).tap();
    await until(async () => JSON.stringify((await stored()).strokes[0].pts) === JSON.stringify(before.pts), { what: "two flips are the identity" });
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await page.getByText("Undone: ink flip (page 1).", { exact: true }).waitFor();
    await until(async () => JSON.stringify((await stored()).strokes[0].pts) !== JSON.stringify(before.pts));
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await until(async () => JSON.stringify((await stored()).strokes[0].pts) === JSON.stringify(before.pts));
    assertNoProblems(page);
  });

  await step("ink edit: New note moves the selection into a block of its own, Merge into note folds it into the previous one, and undo restores both", async () => {
    await tapInk();
    await menu().getByRole("button", { name: "Duplicate", exact: true }).tap();
    await until(async () => (await stored())?.strokes.length === 2);
    const copyId = (await stored()).strokes[1].id;
    await moreRow();
    await menu().getByRole("button", { name: "New note", exact: true }).tap();
    await until(async () => (await inkBlocks()).length === 3 && (await stored()).strokes.length === 1, { what: "a new ink block takes the copy" });
    const fresh = (await inkBlocks()).find((b) => ![noteId, secondId].includes(b.id));
    assertEq(fresh.ink.strokes.map((s) => s.id).join(), copyId, "the moved stroke keeps its id");
    assertEq((await stored()).strokes[0].id, "thin", "the source keeps the rest");
    // The new note is selected whole and the page has other notes.
    await menu().waitFor();
    await menu().getByRole("button", { name: "Merge into note", exact: true }).tap();
    await until(async () => (await inkBlocks()).length === 2 && (await stored(secondId)).strokes.length === 2,
      { what: "the strokes join the previous note and the emptied block goes" });
    assertEq((await stored(secondId)).strokes[1].id, copyId);
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await page.getByText("Undone: ink merge (page 1).", { exact: true }).waitFor();
    await until(async () => (await inkBlocks()).length === 3 && (await stored(secondId)).strokes.length === 1,
      { what: "undo brings the block and both groups back together" });
    assertEq((await inkBlocks()).find((b) => b.id === fresh.id)?.ink.strokes[0].id, copyId);
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await page.getByText("Undone: ink split (page 1).", { exact: true }).waitFor();
    await until(async () => (await inkBlocks()).length === 2 && (await stored()).strokes.length === 2, { what: "undo of the split returns the copy" });
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await until(async () => (await stored()).strokes.length === 1);
    assertEq(await page.locator(paths).count(), 2);
    assertNoProblems(page);
  });

  await step("ink edit: a right-click on blank page with a tool armed offers Paste here, which pastes at the point", async () => {
    await page.getByRole("button", { name: / pen · .* · key 1\b/ }).tap();
    await tapInk();
    await page.keyboard.press("Control+c");
    await page.getByText("Copied 1 stroke.", { exact: true }).waitFor();
    const blank = await point(300, 500);
    await page.mouse.click(blank.x, blank.y, { button: "right" });
    await page.getByRole("button", { name: "Paste here", exact: true }).click();
    await until(async () => (await inkBlocks()).length === 3, { what: "Paste here lands a group" });
    const added = (await inkBlocks()).find((b) => ![noteId, secondId].includes(b.id));
    const b = strokeBounds(added.ink.strokes[0]);
    assert(Math.abs(b[0] - 300) < 1 && Math.abs(b[1] - 500) < 1, `the copied box's top-left lands at the point: ${b}`);
    await menu().waitFor();
    await menu().getByRole("button", { name: "Delete", exact: true }).tap();
    await until(async () => (await inkBlocks()).length === 2);
    // Over ink, or with the hand, the page keeps its own menu: no Paste here.
    await page.getByRole("button", { name: "Hand", exact: true }).tap();
    await page.mouse.click(blank.x, blank.y, { button: "right" });
    assertEq(await page.getByRole("button", { name: "Paste here", exact: true }).count(), 0, "the hand offers no paste");
    // (Escape with no menu open would close the strip, which the next steps use.)
    if (await page.locator(".ctxMenu").count()) await page.keyboard.press("Escape");
    assertNoProblems(page);
  });

  await step("ink edit: lasso spans notes, finger drag moves both, and one undo restores both", async () => {
    await page.getByRole("button", { name: /^Lasso \(L\)/ }).tap();
    const from = await point(65, 205);
    await page.mouse.move(from.x, from.y); await page.mouse.down();
    for (const [x, y] of [[215, 205], [215, 285], [65, 285], [65, 205]]) {
      const p = await point(x, y); await page.mouse.move(p.x, p.y, { steps: 5 });
    }
    await page.mouse.up();
    await menu().waitFor();
    const beforeA = decodeStroke((await stored()).strokes[0])[0];
    const beforeB = decodeStroke((await stored(secondId)).strokes[0])[0];
    await dragTouch(await point(140, 245), await point(170, 280), assertHandlesFollowSelection);
    // The groups commit one after another: wait for both (as the resize step does).
    await until(async () => decodeStroke((await stored()).strokes[0])[0].y > beforeA.y + 30
      && decodeStroke((await stored(secondId)).strokes[0])[0].y > beforeB.y + 30, { what: "both groups moved" });
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await until(async () => decodeStroke((await stored()).strokes[0])[0].y === beforeA.y
      && decodeStroke((await stored(secondId)).strokes[0])[0].y === beforeB.y);
    assertNoProblems(page);
  });

  await step("ink edit: selection handles resize and rotate across notes, preview, cancel and undo", async () => {
    const selectBoth = async () => {
      const start = await point(65, 205);
      await page.mouse.move(start.x, start.y); await page.mouse.down();
      for (const [x, y] of [[215, 205], [215, 285], [65, 285], [65, 205]]) {
        const p = await point(x, y); await page.mouse.move(p.x, p.y, { steps: 3 });
      }
      await page.mouse.up(); await menu().waitFor();
    };
    await selectBoth();
    const beforeA = await stored(), beforeB = await stored(secondId);
    const resize = page.getByRole("button", { name: "Resize selected handwriting", exact: true });
    const rotate = page.getByRole("button", { name: "Rotate selected handwriting", exact: true });
    await resize.waitFor();
    const b = await resize.boundingBox();
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2 + 45, b.y + b.height / 2 + 25, { steps: 6 });
    assertEq(await menu().count(), 0, "menu hides during transform preview");
    await assertHandlesFollowSelection();
    await page.mouse.up();
    // The groups commit one after another (flushInk uploads each in turn):
    // wait for both, never assert the second right after the first landed.
    await until(async () => (await stored()).strokes[0].size > beforeA.strokes[0].size
      && (await stored(secondId)).strokes[0].size > beforeB.strokes[0].size, { what: "both notes resized" });
    assertEq(decodeStroke((await stored()).strokes[0])[4].p, decodeStroke(beforeA.strokes[0])[4].p);
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await until(async () => JSON.stringify((await stored()).strokes) === JSON.stringify(beforeA.strokes)
      && JSON.stringify((await stored(secondId)).strokes) === JSON.stringify(beforeB.strokes), { what: "undo restores both notes" });
    // Toolbar undo dismisses selection; select both notes again.
    await selectBoth();
    await rotate.focus(); await page.keyboard.press("ArrowRight");
    await until(async () => decodeStroke((await stored()).strokes[0])[0].y !== decodeStroke(beforeA.strokes[0])[0].y);
    const rotated = await stored();
    assertEq(rotated.strokes[0].size, beforeA.strokes[0].size, "rotation preserves width");
    const r = await rotate.boundingBox();
    await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x: r.x + 14, y: r.y + 14, pointerType: "pen", button: "left", buttons: 1, force: 0.5 });
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: r.x + 40, y: r.y + 55, pointerType: "pen", button: "left", buttons: 1, force: 0.5 });
    await assertHandlesFollowSelection();
    await page.keyboard.press("Escape");
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: r.x + 40, y: r.y + 55, pointerType: "pen", button: "left", buttons: 0 });
    assertEq(JSON.stringify((await stored()).strokes), JSON.stringify(rotated.strokes), "cancel discards preview");
    assertEq(await page.locator('[data-page="1"] .inkLayer > g[transform]').count(), 0, "Escape clears the transform preview");
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await until(async () => JSON.stringify((await stored()).strokes) === JSON.stringify(beforeA.strokes));
    assertNoProblems(page);
  });

  await step("ink edit: mouse and pen hover show tool footprint without drawing", async () => {
    await page.getByRole("button", { name: / pen · .* · key 1\b/ }).tap();
    const p = await point(300, 330), cursor = page.locator('[data-page="1"] .inkCursor');
    await page.mouse.move(p.x, p.y); await cursor.waitFor({ state: "visible" });
    const penSize = await cursor.evaluate((el) => el.getBoundingClientRect().width);
    assert(penSize > 0, "pen footprint visible");
    await page.getByRole("button", { name: /^Eraser \(E\)/ }).tap();
    await page.mouse.move(p.x, p.y);
    assertEq(await cursor.getAttribute("data-tool"), "eraser");
    assert((await cursor.boundingBox()).width > penSize, "the eraser shows its own, larger footprint");
    await page.getByRole("button", { name: "Hand", exact: true }).tap();
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", ...p, pointerType: "pen", buttons: 0 });
    await cursor.waitFor({ state: "visible" });
    assertEq(await cursor.getAttribute("data-tool"), "pen", "stylus still previews in hand mode");
    assertEq(await page.locator(paths).count(), 2, "hover adds no ink");
    await page.mouse.move(5, 5); await cursor.waitFor({ state: "hidden" });
    assertNoProblems(page);
  });

  await step("ink edit: swipes over ink scroll, blank taps dismiss, and holds cancel on a second contact", async () => {
    await page.getByRole("button", { name: "Hand", exact: true }).tap();
    const scrollBefore = await page.locator(".pdfViewer").evaluate((el) => el.scrollTop);
    const from = await point(130, 220);
    await dragTouch(from, { x: from.x, y: from.y - 110 });
    const scrollTop = () => page.locator(".pdfViewer").evaluate((el) => el.scrollTop);
    await until(async () => await scrollTop() > scrollBefore + 20);
    assertEq(await menu().count(), 0, "swipe did not select");
    // Let the fling's momentum finish before resetting: a still-moving scroller
    // would carry the ink out of view and the menu hides off-screen selections.
    await until(async () => { const a = await scrollTop(); await page.waitForTimeout(250); return a === await scrollTop(); }, { what: "swipe momentum settled" });
    await page.locator(".pdfViewer").evaluate((el) => { el.scrollTop = 0; });
    await until(async () => await scrollTop() === 0, { what: "scrolled back to the top" });
    await tapInk();
    const blank = await point(300, 330);
    await page.touchscreen.tap(blank.x, blank.y);
    await until(async () => await menu().count() === 0);
    const hold = await point(130, 220);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [hold] });
    await menu().waitFor(); // The hold timer must open it before touchEnd.
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [
      { ...hold, id: 0 }, { x: hold.x + 60, y: hold.y + 40, id: 1 },
    ] });
    await until(async () => await menu().count() === 0, { what: "second contact cancels held selection" });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    assertEq(await page.locator(paths).count(), 2);
    assertNoProblems(page);
  });

  await step("ink edit: pen resumes writing after selection and menu stays inside a small viewport", async () => {
    await tapInk();
    const p = await point(140, 220);
    await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", ...p, pointerType: "pen", button: "left", buttons: 1, force: 0.5 });
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x + 50, y: p.y + 30, pointerType: "pen", button: "left", buttons: 1, force: 0.7 });
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: p.x + 50, y: p.y + 30, pointerType: "pen", button: "left", buttons: 0 });
    await until(async () => await page.locator(paths).count() === 3);
    assertEq(await menu().count(), 0, "pen writes instead of moving selected ink");
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await until(async () => await page.locator(paths).count() === 2);
    await page.setViewportSize({ width: 740, height: 620 });
    await page.locator(".pdfViewer").evaluate((el) => { el.scrollTop = 0; });
    await tapInk();
    // The tallest state: both rows and the colour row.
    await moreRow();
    await menu().getByRole("button", { name: "Color", exact: true }).tap();
    await menu().getByRole("button", { name: "Red", exact: true }).waitFor();
    await until(async () => {
      const m = await page.locator(".inkEditMenu").boundingBox();
      const s = await page.locator(".inkSelectionHit").boundingBox();
      return m && s && (m.y + m.height <= s.y || m.y >= s.y + s.height);
    }, { what: "expanded menu avoids covering selected ink" });
    const bounds = await page.locator(".inkEditMenu").boundingBox();
    assert(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 740 && bounds.y + bounds.height <= 620,
      "expanded menu fits the viewport");
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-edit-small.png` });
    await menu().getByRole("button", { name: "Show note", exact: true }).tap();
    await until(async () => await menu().count() === 0);
    assertNoProblems(page);
  });

  await step("ink edit: view shares hide editing; an edit share can restyle saved ink", async () => {
    await alice.api(`/api/share/${pageId}`, { method: "POST" });
    await alice.api(`/api/share-settings/${pageId}`, { method: "PUT", body: { audience: "anyone", role: "view" } });
    const { token } = await alice.api(`/api/share-settings/${pageId}`);
    assert(token, "share token");
    const viewCtx = await browser.newContext({ hasTouch: true, viewport: { width: 1280, height: 860 } });
    const view = await openPage(viewCtx, `${server.base}/?share=${token}`);
    await waitForPdf(view, 1);
    await view.waitForSelector(paths);
    // (a read-only page's strokes take no pointer themselves: the ink
    // layer answers a tap near one, below the page's own marks)
    const stroke = await view.locator(paths).first().boundingBox();
    await view.touchscreen.tap(stroke.x + stroke.width / 2, stroke.y + stroke.height / 2);
    assertEq(await view.locator(".inkEditMenu").count(), 0);
    assertEq(await view.getByRole("button", { name: "Handwriting tools", exact: true }).count(), 0);
    assertNoProblems(view);
    await viewCtx.close();
    await alice.api(`/api/share-settings/${pageId}`, { method: "PUT", body: { audience: "users", role: "edit" } });
    const editCtx = await bob.context(browser, { hasTouch: true });
    await editCtx.addInitScript(() => localStorage.setItem("gamma-ink-pen-only", "1"));
    const edit = await openPage(editCtx, `${server.base}/?share=${token}`);
    await waitForPdf(edit, 1);
    await edit.waitForSelector(paths);
    await edit.locator(paths).first().tap();
    const editMenu = edit.getByRole("toolbar", { name: "Edit handwriting", exact: true });
    await editMenu.waitFor();
    await editMenu.getByRole("button", { name: "Color", exact: true }).tap();
    await editMenu.getByRole("button", { name: "Green", exact: true }).tap();
    await until(async () => (await stored()).strokes[0].color === "#15803d");
    assertNoProblems(edit);
    await editCtx.close();
  });

  await step("ink edit: Chrome double taps do not zoom on tablet; PDF pinch still zooms", async () => {
    const tabletCtx = await alice.context(browser, { hasTouch: true, isMobile: true, viewport: { width: 1280, height: 860 } });
    const tablet = await openPage(tabletCtx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
    await waitForPdf(tablet, 1);
    assertEq(await tablet.locator(".app.phoneUI").count(), 0, "covers layouts beyond phone");
    assertEq(await tablet.evaluate(() => getComputedStyle(document.documentElement).touchAction), "manipulation");
    const session = await tabletCtx.newCDPSession(tablet);
    const pdf = tablet.locator('[data-page="1"]');
    const before = await pdf.boundingBox();
    const scale = await tablet.evaluate(() => visualViewport.scale);
    const x = Math.round(before.x + before.width * 0.65), y = Math.round(before.y + 280);
    await session.send("Input.synthesizeTapGesture", { x, y, tapCount: 2, gestureSourceType: "touch" });
    // Sample through Chrome's double-tap animation window, rather than checking
    // only before a delayed zoom could start.
    const scales = await tablet.evaluate(() => new Promise((resolve) => {
      const values = [], start = performance.now();
      const tick = () => { values.push(visualViewport.scale); if (performance.now() - start < 600) requestAnimationFrame(tick); else resolve(values); };
      requestAnimationFrame(tick);
    }));
    assert(scales.every((s) => Math.abs(s - scale) < 0.01), "native double tap keeps browser scale");
    assertEq((await pdf.boundingBox()).width, before.width, "double tap keeps PDF scale");
    await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: x - 40, y, id: 0 }, { x: x + 40, y, id: 1 }] });
    for (let i = 1; i <= 8; i++) await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [
      { x: x - 40 - i * 5, y, id: 0 }, { x: x + 40 + i * 5, y, id: 1 },
    ] });
    await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await until(async () => (await pdf.boundingBox()).width > before.width * 1.5, { what: "PDF pinch increases page size" });
    assert(Math.abs(await tablet.evaluate(() => visualViewport.scale) - scale) < 0.01, "PDF pinch leaves browser chrome at its original scale");
    assertNoProblems(tablet);
    await tabletCtx.close();
  });

  await step("ink edit: the Style row restyles a selected pen to Brush (its widths baked again) and Dotted; a highlighter takes a line, never a brush", async () => {
    // (the last step on this page: the highlighter it adds stays)
    if (!await page.locator(".pdfInkBar").count()) await page.getByRole("button", { name: "Handwriting tools", exact: true }).tap();
    await page.getByRole("button", { name: / pen · .* · key 1\b/ }).tap();
    await page.locator(".pdfViewer").evaluate((el) => { el.scrollTop = 0; });
    await tapInk();
    const before = (await stored()).strokes[0];
    assert(!before.brush && !before.ch.includes("w"), `a plain constant pen to start from (${before.ch})`);
    const styleBtn = menu().getByRole("button", { name: "Style", exact: true });
    await styleBtn.tap();
    assertEq(await styleBtn.getAttribute("aria-expanded"), "true");
    const row = menu().locator(".inkEditOptions", { has: page.locator(".inkLine") });
    assertEq(await row.getAttribute("aria-label"), "Selected ink style");
    for (const name of ["Style: Pen", "Solid", "Dashed", "Dotted"]) {
      const box = await row.getByRole("button", { name, exact: true }).boundingBox();
      assert(box.width >= 32 && box.height >= 32, `${name} keeps a 32 px target`);
    }
    assertEq(await row.getByRole("button", { name: "Solid", exact: true }).getAttribute("aria-pressed"), "true", "the stroke is solid");
    // the picker's menu opens on the press, outside the menu's tooltip host, and a row of it keeps the selection
    await row.getByRole("button", { name: "Style: Pen", exact: true }).click();
    const styles = page.getByRole("menu", { name: "Pen style" });
    assertEq(await styles.getByRole("menuitemradio", { name: "Pen", exact: true }).getAttribute("aria-checked"), "true");
    await assertMenuFrom(page.locator(".inkStyleMenu"), row.getByRole("button", { name: "Style: Pen", exact: true }), "below", "the Pen style menu");
    await styles.getByRole("menuitemradio", { name: "Brush", exact: true }).click();
    await until(async () => (await stored()).strokes[0].brush === "brush", { what: "the stroke restyled to Brush" });
    const brushed = (await stored()).strokes[0];
    assertEq(brushed.ch, "xyptw", "the Brush writes its baked widths");
    const q = decodeStroke(brushed), mid = Math.floor(q.length / 2);
    assert(q[0].w < q[mid].w && q.at(-1).w < q[mid].w, `tapered at both ends (${q[0].w}, ${q[mid].w}, ${q.at(-1).w})`);
    assert(Math.abs(q[mid].w - 2 * q[mid].p) < 0.002, `2p in the middle, from the stored pressure (${q[mid].w} at p ${q[mid].p})`);
    assertEq(JSON.stringify(q.map((s) => [s.x, s.y, s.p, s.t])), JSON.stringify(decodeStroke(before).map((s) => [s.x, s.y, s.p, s.t])),
      "pressure, time and place untouched");
    await menu().waitFor();
    await until(async () => (await menu().getByRole("button", { name: "Style: Brush", exact: true }).count()) === 1, { what: "the picker names the Brush" });
    // From the keyboard: Enter on the focused picker opens its menu, and
    // Escape closes that menu alone (a menu of the selection menu takes
    // Escape first), the selection menu and the focus staying.
    const picker = menu().getByRole("button", { name: "Style: Brush", exact: true });
    await picker.focus();
    await page.keyboard.press("Enter");
    await styles.getByRole("menuitemradio", { name: "Brush", exact: true }).waitFor();
    assertEq(await picker.getAttribute("aria-expanded"), "true", "opened from the keyboard");
    await page.keyboard.press("Escape");
    await styles.waitFor({ state: "detached" });
    assertEq(await menu().count(), 1, "the selection menu stays: Escape went to its Style menu");
    assert(await picker.evaluate((el) => el === document.activeElement), "the focus stays on the picker");
    // A finger: the tap's press opens the menu and the click the tap ends
    // with leaves it open; a second tap closes it.
    await picker.evaluate((el) => el.addEventListener("click", () => { window.__pickerClicked = true; }, { once: true }));
    await picker.tap();
    await until(() => page.evaluate(() => window.__pickerClicked === true), { what: "the tap's click" });
    assertEq(await picker.getAttribute("aria-expanded"), "true", "the tap opened the menu, and its click left it open");
    assertEq(await styles.getByRole("menuitemradio").count(), 5, "the five brushes");
    await picker.tap();
    await styles.waitFor({ state: "detached" });
    await menu().getByRole("button", { name: "Dotted", exact: true }).tap();
    await until(async () => (await stored()).strokes[0].dash === "dotted", { what: "the stroke dotted" });
    assertEq((await stored()).strokes[0].brush, "brush", "the line leaves the brush");
    assertEq(await menu().getByRole("button", { name: "Dotted", exact: true }).getAttribute("aria-pressed"), "true");
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await page.getByText("Undone: ink line change (page 1).", { exact: true }).waitFor();
    await until(async () => !("dash" in (await stored()).strokes[0]), { what: "the line undone" });
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await page.getByText("Undone: ink style change (page 1).", { exact: true }).waitFor();
    await until(async () => JSON.stringify((await stored()).strokes[0]) === JSON.stringify(before), { what: "the brush undone, the samples as they were" });
    // a highlighter: its Style row has the Line trio and no Style picker
    const hl = { ...newInk(1, 612, 792), strokes: [encodeStroke({ id: "hl", tool: "highlighter", color: "#fde047", size: 14, opacity: 0.6, ch: "xypt",
      samples: Array.from({ length: 9 }, (_, i) => ({ x: 80 + i * 15, y: 330, p: 0.5, t: i * 8 })) })] };
    const file = await alice.api("/api/upload-ink", { method: "POST", body: hl });
    const hlId = (await alice.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "Ink hl",
      properties: { ink_url: file.url, pdf_position: file.pdf_position, ink_strokes: 1 } } })).id;
    await until(async () => (await page.locator(paths).count()) === 3, { what: "the highlighter drawn" });
    await tapInk(140, 330);
    await menu().getByRole("button", { name: "Style", exact: true }).tap();
    assertEq(await menu().locator(".inkStyleBtn").count(), 0, "a highlighter takes no brush");
    await menu().getByRole("button", { name: "Dashed", exact: true }).tap();
    await until(async () => (await stored(hlId))?.strokes[0].dash === "dashed", { what: "the highlighter dashed" });
    await until(async () => (await page.locator(`${paths}[stroke-dasharray="28 28"]`).count()) === 1, { what: "drawn with its 2-width dashes" });
    assertNoProblems(page);
  });

  // Escape drops the selection, and its menu with it (a blank tap could land under the menu).
  const dropSelection = async () => {
    await page.keyboard.press("Escape");
    await until(async () => (await menu().count()) === 0, { what: "the selection dropped" });
  };

  await step("ink edit: the Color row marks the colour every selected stroke shares and none for a mix; a custom colour recolours, keeps a highlighter's opacity and shows on the custom swatch; a pen swatch on pens and a highlighter keeps each opacity", async () => {
    // (after the Style step: "thin" green, "other" blue, and the dashed highlighter "hl", selected)
    const colors = () => menu().locator(".inkEditOptions[aria-label='Selected ink color']");
    const openColors = async () => {
      const button = menu().getByRole("button", { name: "Color", exact: true });
      if (await button.getAttribute("aria-expanded") !== "true") await button.tap();
      await colors().waitFor();
    };
    // the swatches with the ring (.colorBtn.selected): a palette colour's name, or the custom swatch's title
    const marked = () => colors().locator(".colorBtn.selected").evaluateAll((els) => els.map((el) => el.getAttribute("aria-label") || el.title));
    const custom = () => colors().locator(".inkCustomColor");
    const hlBlock = async () => (await inkBlocks()).find((b) => b.ink.strokes.some((s) => s.id === "hl"));
    // a lasso through these page points
    const lassoRound = async (points) => {
      const start = await point(...points[0]);
      await page.mouse.move(start.x, start.y); await page.mouse.down();
      for (const [x, y] of [...points.slice(1), points[0]]) {
        const p = await point(x, y); await page.mouse.move(p.x, p.y, { steps: 3 });
      }
      await page.mouse.up();
    };
    await dropSelection();
    // a mix: the lasso round both pens
    await page.getByRole("button", { name: /^Lasso \(L\)/ }).tap();
    await lassoRound([[65, 205], [215, 205], [215, 285], [65, 285]]);
    await menu().waitFor();
    await openColors();
    assertEq(JSON.stringify(await marked()), "[]", "green and blue: no swatch is marked");
    assertEq(await colors().locator("[aria-pressed='true']").count(), 0);
    // one pen: its colour's swatch carries the ring
    await dropSelection();
    await tapInk();
    await openColors();
    assertEq(JSON.stringify(await marked()), JSON.stringify(["Green"]), "the colour it has is marked");
    assertEq(await colors().getByRole("button", { name: "Green", exact: true }).getAttribute("aria-pressed"), "true");
    // the highlighter: its yellow is none of the highlighter swatches, so the custom swatch shows it
    await dropSelection();
    await tapInk(140, 330);
    await openColors();
    assertEq(JSON.stringify(await marked()), JSON.stringify(["Custom color"]), "a colour off the palette marks the custom swatch");
    assertEq(await custom().evaluate((el) => el.style.getPropertyValue("--ink-custom")), "#fde047", "in the stroke's colour");
    const input = custom().locator("input[type=color]");
    assertEq(await input.getAttribute("aria-label"), "Custom color", "the custom input is named as every colour picker's");
    // keyboard focus on the picker, which is invisible, rings its swatch as on every swatch
    await colors().locator("button.colorBtn").last().focus();
    await page.keyboard.press("Tab");
    assert(await input.evaluate((el) => el.matches(":focus-visible")), "Tab reaches the custom colour");
    assertEq(await custom().evaluate((el) => getComputedStyle(el).outlineStyle), "solid", "its swatch carries the focus ring");
    // a colour picked in the native picker (set as the browser sets it, past React's value tracker)
    await input.evaluate((el, value) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }, "#22aa88");
    await until(async () => (await hlBlock())?.ink.strokes[0].color === "#22aa88", { what: "the custom colour saved" });
    const recoloured = (await hlBlock()).ink.strokes[0];
    assertEq(recoloured.opacity, 0.6, "the highlighter keeps its opacity");
    assertEq(recoloured.dash, "dashed", "and its line");
    await until(async () => (await custom().evaluate((el) => el.style.getPropertyValue("--ink-custom"))) === "#22aa88",
      { what: "the custom swatch shows the picked colour" });
    assertEq(JSON.stringify(await marked()), JSON.stringify(["Custom color"]));
    const drawn = page.locator(`${paths}[stroke-dasharray]`);
    await until(async () => (await drawn.getAttribute("stroke")) === "#22aa88", { what: "drawn in the picked colour" });
    assertEq(await drawn.getAttribute("stroke-opacity"), "0.6", "at the highlighter's opacity");
    // one undo entry, as every recolour
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await page.getByText("Undone: ink color change (page 1).", { exact: true }).waitFor();
    await until(async () => (await hlBlock()).ink.strokes[0].color === "#fde047", { what: "the colour undone" });
    // the pens and the highlighter: a pen swatch recolours all three, each keeping its opacity (the
    // swatch's alpha is a pen's, and would turn the highlighter opaque)
    if (await menu().count()) await dropSelection();   // (Escape with nothing selected would close the strip)
    const lasso = page.getByRole("button", { name: /^Lasso \(L\)/ });
    if ((await lasso.getAttribute("aria-pressed")) !== "true") await lasso.tap();
    await lassoRound([[65, 205], [215, 205], [215, 345], [65, 345]]);
    await menu().waitFor();
    await openColors();
    await colors().getByRole("button", { name: "Violet", exact: true }).tap();
    const looks = async () => (await inkBlocks()).flatMap((b) => b.ink.strokes).map((s) => `${s.id} ${s.color} ${s.opacity ?? 1}`).sort();
    await until(async () => (await looks()).every((look) => look.includes("#7c3aed")), { what: "all three recoloured" });
    assertEq(JSON.stringify(await looks()), JSON.stringify(["hl #7c3aed 0.6", "other #7c3aed 1", "thin #7c3aed 1"]), "each its own opacity");
    await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
    await until(async () => (await hlBlock()).ink.strokes[0].color === "#fde047", { what: "the recolour undone" });
    assertNoProblems(page);
  });

  await step("ink edit: Ctrl+D and the menu's Duplicate set the copy off by the same 12 css px, in the page's points, at two zooms", async () => {
    // css px per point on page 1 (612 pt wide)
    const scale = () => page.locator('[data-page="1"]').evaluate((el) => el.getBoundingClientRect().width / 612);
    // the copy's offset in points: its first sample against the original's
    const offset = async () => {
      const [a, b] = (await stored()).strokes.map((s) => decodeStroke(s)[0]);
      return { dx: b.x - a.x, dy: b.y - a.y };
    };
    const undoCopy = async () => {
      await page.getByRole("button", { name: "Undo ink", exact: true }).tap();
      await until(async () => (await stored())?.strokes.length === 1, { what: "the copy undone" });
    };
    const duplicates = async () => {
      if (await menu().count()) await dropSelection();
      await tapInk();
      await page.keyboard.press("Control+d");
      await until(async () => (await stored())?.strokes.length === 2, { what: "Ctrl+D's copy" });
      const key = await offset();
      await undoCopy();
      await tapInk();
      await menu().getByRole("button", { name: "Duplicate", exact: true }).tap();
      await until(async () => (await stored())?.strokes.length === 2, { what: "the menu's copy" });
      const button = await offset();
      await undoCopy();
      return { key, button };
    };
    const check = ({ key, button }, k, what) => {
      for (const [name, d] of [["Ctrl+D", key], ["the menu's Duplicate", button]]) {
        near(d.dx, 12 / k, 0.02, `${what}: ${name} right by 12 css px`);
        near(d.dy, 12 / k, 0.02, `${what}: ${name} down by 12 css px`);
      }
      near(key.dx, button.dx, 0.011, `${what}: the same offset both ways`);
    };
    const k1 = await scale();
    const first = await duplicates();
    check(first, k1, "at the first zoom");
    // zoomed in by a third or more: the same 12 px is fewer points
    for (let i = 0; i < 4 && (await scale()) < k1 * 1.3; i++) {
      const was = await scale();
      await page.getByRole("button", { name: "Zoom in", exact: true }).tap();
      await until(async () => Math.abs((await scale()) - was) > 0.01, { what: "the page zoomed in" });
    }
    await page.locator(".pdfViewer").evaluate((el) => { el.scrollTop = 0; el.scrollLeft = 0; });
    const k2 = await scale();
    assert(k2 >= k1 * 1.3, `zoomed in (${k1} to ${k2} css px a point)`);
    const second = await duplicates();
    check(second, k2, "zoomed in");
    assert(second.key.dx < first.key.dx - 0.5, `fewer points zoomed in (${first.key.dx}, then ${second.key.dx})`);
    assertNoProblems(page);
  });

  if (ctx) await ctx.close();
}
