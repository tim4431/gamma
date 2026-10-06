// Text boxes (docs/dev/text_boxes.md): typed text placed on a PDF page or a
// sheet of paper. With the strip's Text tool a click makes a box and opens
// its editor (Enter a new line, the formatting keys work), a drag makes one
// of a fixed width, and a click outside commits it: the box is a block with
// `text_box` (plus `pdf_page` on a PDF page, under its sheet on a sheet)
// whose content is the text. It renders after a reload, its notes row's
// marker shows it on its page, a box left empty goes without an undo
// entry, and a box's editor and its row's are never open at once. Then the
// box as an object: a click selects it (frame, width handle, menu), a drag
// moves it, the arrows nudge it, the handle fixes its width, the menu
// restyles, duplicates and deletes it, and Ctrl+Z undoes each (the block
// history while the Text tool is armed or a box is selected, the strokes'
// with a pen); another client's edit shows in place, unclipped and never
// measured back; a finger taps and drags a selected box and scrolls over
// any other, and two fingers pinch over it without moving it. The Text
// tool tapped again while typing restyles that box; opening a box's editor
// scrolls nothing; a press that leaves a box early ends where it is let
// go; a stylus uses a box's handle, band and editor; a box over a PDF link
// takes its own clicks. The same on a sheet in the notebook view and in
// the notes view. Last, two people on one box: a move made while the other
// types survives the typing, and its undo. The rules behind it are
// frontend/tests/textBox.test.mjs, blockHistory.test.mjs,
// collabSession.test.mjs and backend/tests/test_text_box.py,
// test_text_box_merge.py.
import { normalizePaper } from "../../../src/notebook/notebook.js";
import { closeEditor } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";

const TEXT = "Hello **bold**\nsecond line";

// A one-page Letter PDF whose link annotation covers x 72..400, y 232..292
// pt from the top.
function linkPdf() {
  const objs = [];
  const add = (body) => { objs.push(body); return objs.length; };
  const catalog = add(""), pages = add("");
  const font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
  const content = "BT\n/F1 20 Tf\n72 720 Td\n(A page with a link) Tj\nET";
  const stream = add(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`);
  const annot = add("<< /Type /Annot /Subtype /Link /Rect [72 500 400 560] /Border [0 0 0] /A << /S /URI /URI (https://example.com/) >> >>");
  const pg = add(`<< /Type /Page /Parent ${pages} 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 ${font} 0 R >> >> /Contents ${stream} 0 R /Annots [${annot} 0 R] >>`);
  objs[catalog - 1] = `<< /Type /Catalog /Pages ${pages} 0 R >>`;
  objs[pages - 1] = `<< /Type /Pages /Kids [${pg} 0 R] /Count 1 >>`;
  let out = "%PDF-1.4\n";
  const offsets = [];
  objs.forEach((body, i) => { offsets.push(out.length); out += `${i + 1} 0 obj\n${body}\nendobj\n`; });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n` + offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  out += `trailer\n<< /Size ${objs.length + 1} /Root ${catalog} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

// A stylus on the page, through the DevTools protocol (Chromium's pen): a
// press at x0, y0 moved to x1, y1 and let go.
function stylus(cdp) {
  const send = (type, x, y, buttons) => cdp.send("Input.dispatchMouseEvent", {
    type, x, y, button: type === "mouseMoved" ? "none" : "left", buttons, clickCount: type === "mouseMoved" ? 0 : 1, pointerType: "pen",
  });
  return async (x0, y0, x1 = x0, y1 = y0) => {
    await send("mouseMoved", x0, y0, 0);
    await send("mousePressed", x0, y0, 1);
    for (let i = 1; i <= 8; i++) await send("mouseMoved", x0 + (x1 - x0) * i / 8, y0 + (y1 - y0) * i / 8, 1);
    await send("mouseReleased", x1, y1, 0);
  };
}

// Type TEXT into the open box: "Hello bold", the last word made bold with
// Ctrl+B, then a second line.
async function typeInBox(page) {
  await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
  await page.keyboard.type("Hello bold");
  for (let i = 0; i < 4; i++) await page.keyboard.press("Shift+ArrowLeft");
  await page.keyboard.press("Control+b");
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.type("second line");
}

export async function textBoxScenarios({ server, browser, alice, bob, makePdf, step, until, assert, assertEq, assertNoProblems, openPage, flags }) {
  const account = alice;
  let ctx, page, pageId, firstId, fixedId;
  const subtree = async (id) => (await account.api(`/api/blocks/${id}/subtree`)).block;
  const boxesIn = (list) => (list || []).flatMap((b) => [...(b.properties?.text_box ? [b] : []), ...boxesIn(b.children)]);
  // A box's block on the server, on the page `root`.
  const stored = async (id, root = pageId) => boxesIn((await subtree(root)).children).find((b) => b.id === id);
  const near = (a, b, what, within = 1) => assert(Math.abs(a - b) < within, `${what}: ${a}, not ${b}`);
  const boxEl = (id) => page.locator(`[data-box-id="${id}"]`);
  const menu = () => page.getByRole("toolbar", { name: "Edit text box", exact: true });
  const status = async () => (await page.locator(".statusPill").allInnerTexts().catch(() => [])).join(" | ");
  // A mouse drag from the middle of `locator` by dx, dy css px.
  const dragBy = async (locator, dx, dy) => {
    const r = await locator.boundingBox();
    await page.mouse.move(r.x + r.width / 2, r.y + r.height / 2);
    await page.mouse.down();
    await page.mouse.move(r.x + r.width / 2 + dx, r.y + r.height / 2 + dy, { steps: 8 });
    await page.mouse.up();
  };
  const shot = async (name) => { if (flags.keep) await page.screenshot({ path: `${server.dir}/${name}.png` }); };
  const armText = async (scope = ".pdfInkBar") => {
    await page.click(`${scope} button[title^='Text (T)']`);
    await page.waitForSelector(`${scope} button[title^='Text (T)'].modeActive`, { timeout: 5000 });
  };
  const hand = (scope = ".pdfInkBar") => page.click(`${scope} button[aria-label='Hand']`);

  await step("text box: on a PDF page the Text tool makes a box; its bold, two-line text is the block's content", async () => {
    const pdf = makePdf([["A page to type on", "with some text"]]);
    const up = await account.upload("/api/uploads", pdf, "boxes.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Box paper", source_url: up.source_url } });
    pageId = created.id;
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    await armText();
    assert(await page.$(".pdfViewer.textArmed"), "the page takes text");
    const b = await page.locator('[data-page="1"]').boundingBox();
    await page.mouse.click(b.x + 120, b.y + 300);
    await typeInBox(page);
    assertEq((await page.$$(".blockEditorCm")).length, 1, "one editor open: the box's");
    assert(await page.$('[data-page="1"] .textBox.editing .cmStrong'), "the bold word is bold while typing");
    await shot("textbox-editing");
    await hand(); // a click outside the box commits it
    await page.waitForSelector('[data-page="1"] .textBox:not(.editing) strong', { timeout: 5000 });
    assertEq(await page.locator('[data-page="1"] .textBox strong').innerText(), "bold", "the rendered text");
    const box = await until(async () => {
      const found = boxesIn((await subtree(pageId)).children).find((x) => x.content === TEXT);
      return found?.properties.text_box.h > 20 ? found : null;
    }, { what: "the box's block, text and measured size on the server" });
    firstId = box.id;
    assertEq(box.properties.pdf_page, 1, "on PDF page 1");
    assert(!("pdf_position" in box.properties), "no position: not a highlight");
    const tb = box.properties.text_box;
    assertEq(tb.auto, true, "a click makes an auto-width box");
    assertEq(tb.size, 12, "the default size");
    const k = b.width / 612; // css px per pt on the Letter page
    assert(Math.abs(tb.x + 4 - 120 / k) < 1, `the caret starts at the click (x ${tb.x})`);
    assert(tb.w > 40 && tb.w < 200, `the width follows the text (${tb.w})`);
    assert(tb.h > 12 * 1.25 * 2, `two lines tall (${tb.h})`);
    assertNoProblems(page);
  });

  await step("text box: it renders after a reload; the notes marker shows it on its page; its editor and its row's never both open", async () => {
    await page.reload();
    await waitForPdf(page, 1);
    await page.waitForSelector('[data-page="1"] .textBox strong', { timeout: 15000 });
    const row = page.locator(".blockRow", { has: page.locator(".textBoxMarker") }).first();
    await row.locator(".textBoxMarker").click();
    await page.waitForSelector('[data-page="1"] .textBoxFlash', { timeout: 5000 });
    await shot("textbox-pdf");
    // the row's editor, then the box's: opening one closes the other
    await page.click("button[aria-label='Handwriting tools']");
    await armText();
    await row.locator(".blockRendered").click();
    await page.waitForSelector(".blockRow .blockEditorCm", { timeout: 5000 });
    await page.locator('[data-page="1"] .textBox').click();
    await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
    await until(async () => (await page.$$(".blockEditorCm")).length === 1, { what: "the row's editor closed" });
    await row.locator(".blockRendered").click();
    await until(async () => !(await page.$(".textBox.editing")) && !!(await page.$(".blockRow .blockEditorCm")),
      { what: "the box's editor closed for the row's" });
    await closeEditor(page);
    assertEq(boxesIn((await subtree(pageId)).children).length, 1, "the box is still there, as it was");
    assertNoProblems(page);
  });

  await step("text box: a box left empty goes, with no undo entry, restyled first or not, the redo steps kept; its row clicked keeps it; a drag makes a fixed-width box", async () => {
    const b = await page.locator('[data-page="1"]').boundingBox();
    await page.keyboard.press("t"); // the strip's key
    await page.waitForSelector(".pdfInkBar button[title^='Text (T)'].modeActive", { timeout: 5000 });
    await page.mouse.click(b.x + 300, b.y + 500);
    await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
    assertEq((await page.$$('[data-page="1"] .textBox')).length, 2, "the new box");
    await page.keyboard.press("Escape");
    await until(async () => (await page.$$('[data-page="1"] .textBox')).length === 1, { what: "the empty box gone" });
    await page.keyboard.press("Escape"); // closes the strip: Ctrl+Z is the notes' again
    await until(async () => !(await page.$(".pdfInkBar")), { what: "the strip closed" });
    await page.keyboard.press("Control+z");
    await until(async () => (await page.locator(".statusPill").innerText().catch(() => "")).includes("Nothing to undo in notes"),
      { what: "nothing to undo: the abandoned box left no entry" });
    assertEq((await page.$$('[data-page="1"] .textBox')).length, 1, "the first box stays");
    // A redo step waiting (a nudge of the first box, undone), then a box
    // made, restyled from the Text options before any typing, and left
    // empty: nothing to undo for it, and the redo step still there.
    await boxEl(firstId).click();
    await page.waitForSelector(`[data-box-id="${firstId}"].selected`, { timeout: 5000 });
    const y0 = (await stored(firstId)).properties.text_box.y;
    await page.keyboard.press("ArrowDown");
    await until(async () => (await stored(firstId)).properties.text_box.y === y0 + 1, { what: "the nudge" });
    await page.keyboard.press("Control+z");
    await until(async () => (await stored(firstId)).properties.text_box.y === y0, { what: "the nudge undone" });
    await page.keyboard.press("Escape");
    await page.click("button[aria-label='Handwriting tools']");
    await armText();
    await page.mouse.click(b.x + 300, b.y + 500);
    await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
    const fresh = page.locator(".textBox.editing");
    await page.click(".pdfInkBar button[title^='Text (T)']"); // its options
    await page.click(".pdfInkBar [data-ink-options='text'] button[aria-label='Text size 36 pt']");
    await until(async () => (await fresh.evaluate((el) => el.style.getPropertyValue("--tb-size"))) === "36", { what: "the new box restyled" });
    await page.keyboard.press("Escape");
    await until(async () => (await page.$$('[data-page="1"] .textBox')).length === 1, { what: "the restyled empty box gone" });
    await page.keyboard.press("Control+Shift+z");
    await until(async () => (await stored(firstId)).properties.text_box.y === y0 + 1, { what: "the nudge redone: the redo step was kept" });
    assert((await status()).includes("Redone: text box move"), `the redo names the nudge (${await status()})`);
    await page.keyboard.press("Control+z");
    await until(async () => (await stored(firstId)).properties.text_box.y === y0, { what: "and undone again" });
    await page.click(".pdfInkBar [data-ink-options='text'] button[aria-label='Text size 12 pt']"); // new boxes at 12 pt again
    // A new box whose notes row is clicked before it is typed in: the row's
    // editor takes it over, and it stays.
    await page.mouse.click(b.x + 300, b.y + 500);
    await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
    const rowBoxId = await page.locator(".textBox.editing").getAttribute("data-box-id");
    await until(async () => !!(await stored(rowBoxId)), { what: "the new box on the server" });
    await page.locator(`.blockRowWrap[data-block-id="${rowBoxId}"] .blockRendered`).click(); // its "(empty)"
    await page.waitForSelector(`.blockRowWrap[data-block-id="${rowBoxId}"] .blockEditorCm`, { timeout: 5000 });
    assert(!(await page.$(".textBox.editing")), "the box's editor closed for its row's");
    await page.keyboard.type("typed in its row");
    await until(async () => (await stored(rowBoxId))?.content === "typed in its row", { what: "the row's text in the box" });
    await closeEditor(page);
    await account.api(`/api/blocks/${rowBoxId}`, { method: "DELETE" });
    await until(async () => !(await page.$(`[data-box-id="${rowBoxId}"]`)), { what: "that box removed again" });
    await page.mouse.move(b.x + 100, b.y + 600);
    await page.mouse.down();
    await page.mouse.move(b.x + 400, b.y + 610, { steps: 10 });
    await page.mouse.up();
    await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
    await page.keyboard.type("A box as wide as the drag");
    await hand();
    const fixed = await until(async () => boxesIn((await subtree(pageId)).children).find((x) => x.content === "A box as wide as the drag"),
      { what: "the dragged box on the server" });
    const k = b.width / 612;
    fixedId = fixed.id;
    assertEq(fixed.properties.text_box.auto, false, "a drag fixes the width");
    assert(Math.abs(fixed.properties.text_box.w - 300 / k) < 1, `as wide as the drag (${fixed.properties.text_box.w})`);
    await until(async () => boxesIn((await subtree(pageId)).children).length === 2, { what: "no empty box left on the server" });
    assertNoProblems(page);
  });

  await step("text box: with the hand a click selects a box; a drag moves it, the arrows nudge it, Ctrl+Z undoes a run of nudges", async () => {
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    await boxEl(firstId).click();
    await page.waitForSelector(`[data-box-id="${firstId}"].selected .textBoxWidth`, { timeout: 5000 });
    await menu().waitFor({ timeout: 5000 });
    for (const name of ["Size", "Color", "Background", "Duplicate", "Show note", "Delete"]) {
      assert(await menu().getByRole("button", { name, exact: true }).isVisible(), `the menu's ${name}`);
    }
    assert(await page.$(".pdfInkBar button[aria-label='Undo']") && !(await page.$(".pdfInkBar button[aria-label='Undo ink']")),
      "with a box selected the strip's Undo is the block history's");
    await shot("textbox-selected");
    const before = (await stored(firstId)).properties.text_box;
    await dragBy(boxEl(firstId), 60, 40);
    const moved = await until(async () => {
      const tb = (await stored(firstId)).properties.text_box;
      return tb.x !== before.x ? tb : null;
    }, { what: "the moved box on the server" });
    near(moved.x, before.x + 60 / k, "moved right as far as the drag");
    near(moved.y, before.y + 40 / k, "moved down as far as the drag");
    assert(await page.$(`[data-box-id="${firstId}"].selected`), "still selected after the drag");
    await page.waitForTimeout(1100); // a pause: the nudges are a run of their own
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Shift+ArrowDown");
    const round = (v) => Math.round(v * 100) / 100;
    await until(async () => {
      const tb = (await stored(firstId)).properties.text_box;
      return tb.x === round(moved.x + 1) && tb.y === round(moved.y + 10);
    }, { what: "nudged 1 pt right and 10 pt down" });
    await page.keyboard.press("Control+z"); // a run of nudges (a held arrow key) undoes as one step
    await until(async () => (await status()).includes("Undone: text box move."), { what: "the undo names the move" });
    await until(async () => {
      const tb = (await stored(firstId)).properties.text_box;
      return tb.x === moved.x && tb.y === moved.y;
    }, { what: "both nudges undone" });
    await page.keyboard.press("ArrowRight");
    await until(async () => (await stored(firstId)).properties.text_box.x === round(moved.x + 1), { what: "a nudge again" });
    await page.keyboard.press("Enter"); // edits the selected box, and types no new line
    await page.waitForSelector(`[data-box-id="${firstId}"].editing .cm-content`, { timeout: 5000 });
    // While it is edited, the band just outside its frame moves it.
    const nudged = (await stored(firstId)).properties.text_box;
    const r = await boxEl(firstId).boundingBox();
    const below = r.y + r.height + 3; // the band under the box: the tool strip floats over the one above it
    await page.mouse.move(r.x + r.width / 2, below);
    await page.mouse.down();
    await page.mouse.move(r.x + r.width / 2 + 30, below + 20, { steps: 8 });
    await page.mouse.up();
    await until(async () => (await stored(firstId)).properties.text_box.x !== nudged.x, { what: "the band's move on the server" });
    near((await stored(firstId)).properties.text_box.y, nudged.y + 20 / k, "the band moved it down");
    assert(await page.$(`[data-box-id="${firstId}"].editing .cm-focused`), "and left its editor open and focused");
    await page.keyboard.press("Escape"); // commits, and leaves it selected
    await page.waitForSelector(`[data-box-id="${firstId}"].selected:not(.editing)`, { timeout: 5000 });
    assertEq((await stored(firstId)).content, TEXT, "the text as it was");
    assertNoProblems(page);
  });

  await step("text box: the width handle fixes the width; the menu restyles the box (size, colour, background)", async () => {
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const shownW = (await boxEl(firstId).boundingBox()).width / k;
    await page.keyboard.press("F2"); // the handle works while the box is edited too
    await page.waitForSelector(`[data-box-id="${firstId}"].editing .textBoxWidth`, { timeout: 5000 });
    await dragBy(boxEl(firstId).locator(".textBoxWidth"), 80, 0);
    const sized = await until(async () => {
      const tb = (await stored(firstId)).properties.text_box;
      return tb.auto === false ? tb : null;
    }, { what: "the handle's width on the server" });
    near(sized.w, shownW + 80 / k, "as wide as it was plus the drag", 1.5);
    assert(await page.$(`[data-box-id="${firstId}"].editing .cm-focused`), "the editor stays open and focused");
    await page.keyboard.press("Escape");
    await page.waitForSelector(`[data-box-id="${firstId}"].selected:not(.editing)`, { timeout: 5000 });
    await boxEl(firstId).locator(".textBoxWidth").focus(); // and it takes the arrow keys
    await page.keyboard.press("Shift+ArrowLeft");
    await until(async () => Math.abs((await stored(firstId)).properties.text_box.w - (sized.w - 10)) < 0.01, { what: "10 pt narrower" });
    await menu().getByRole("button", { name: "Size", exact: true }).click();
    await menu().getByRole("button", { name: "Text size 24 pt", exact: true }).click();
    await menu().getByRole("button", { name: "Color", exact: true }).click();
    await menu().getByRole("button", { name: "Red", exact: true }).click();
    await menu().getByRole("button", { name: "Background", exact: true }).click();
    await menu().getByRole("button", { name: "Yellow background", exact: true }).click();
    const styled = await until(async () => {
      const tb = (await stored(firstId)).properties.text_box;
      return tb.size === 24 && tb.color === "#dc2626" && tb.bg === "#fff4b8" ? tb : null;
    }, { what: "the restyled box on the server" });
    assert(styled.h > sized.h * 1.5, `it measured taller in the larger type (${sized.h} → ${styled.h})`);
    assertEq(await boxEl(firstId).locator(".textBoxBody").evaluate((el) => getComputedStyle(el).backgroundColor), "rgb(255, 244, 184)");
    assert(await page.$(`[data-box-id="${firstId}"].selected`), "the menu keeps the box selected");
    await shot("textbox-menu-pdf");
    // The Text tool's options row (tap the armed tool again) shows the
    // selected box's style, restyles it, and sets the style of new boxes.
    await armText();
    await page.click(".pdfInkBar button[title^='Text (T)']");
    await page.waitForSelector(".pdfInkBar [data-ink-options='text'] button[aria-label='Text size 24 pt'][aria-pressed='true']", { timeout: 5000 });
    await page.click(".pdfInkBar [data-ink-options='text'] button[aria-label='Text size 16 pt']");
    await until(async () => (await stored(firstId)).properties.text_box.size === 16, { what: "the strip's size on the box" });
    assertEq(JSON.parse(await page.evaluate(() => localStorage.getItem("gamma-text-box-style"))).size, 16, "and on new boxes");
    assert(await page.$(`[data-box-id="${firstId}"].selected`), "the strip keeps the box selected");
    await hand();
    assertNoProblems(page);
  });

  await step("text box: another client's edit shows in place, selected or edited, never clipped and never measured back", async () => {
    // the size it already has, chosen again: no change, so nothing to measure after it
    await menu().getByRole("button", { name: "Size", exact: true }).click();
    await menu().getByRole("button", { name: "Text size 16 pt", exact: true }).click();
    const before = (await stored(firstId)).properties.text_box;
    const long = "Hello **bold**\nsecond line\nthird line from elsewhere\nand a fourth";
    await account.api(`/api/blocks/${firstId}`, { method: "PUT", body: { content: long } });
    await until(async () => (await boxEl(firstId).innerText()).includes("and a fourth"), { what: "the remote text in the box" });
    assert(await page.$(`[data-box-id="${firstId}"].selected`), "still selected");
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const drawn = (await boxEl(firstId).boundingBox()).height / k;
    assert(drawn > before.h + 40, `drawn as tall as its text (${drawn} pt), not its stored height (${before.h} pt)`);
    await page.waitForTimeout(1500); // a negative check: nothing to wait for
    assertEq((await stored(firstId)).properties.text_box.h, before.h, "a passive render writes no size");
    // F2 opens the selected box's editor (so does Enter, but the menu's focused button keeps its own)
    await page.keyboard.press("F2");
    await page.waitForSelector(`[data-box-id="${firstId}"].editing .cm-content`, { timeout: 5000 });
    await shot("textbox-editing-styled");
    await account.api(`/api/blocks/${firstId}`, { method: "PUT", body: { content: TEXT } });
    await until(async () => !(await boxEl(firstId).locator(".cm-content").innerText()).includes("fourth"),
      { what: "the open editor takes the remote text" });
    await page.keyboard.press("Escape");
    await page.waitForSelector(`[data-box-id="${firstId}"].selected:not(.editing)`, { timeout: 5000 });
    assertNoProblems(page);
  });

  await step("text box: Duplicate makes a selected copy beside it; Delete removes it; Ctrl+Z with the Text tool brings it back", async () => {
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const orig = (await stored(firstId)).properties.text_box;
    await menu().getByRole("button", { name: "Duplicate", exact: true }).click();
    const copy = await until(async () => boxesIn((await subtree(pageId)).children).find((b) => ![firstId, fixedId].includes(b.id)),
      { what: "the copy on the server" });
    assertEq(copy.content, TEXT, "the same text");
    assertEq(copy.properties.pdf_page, 1, "on the same page");
    near(copy.properties.text_box.x, orig.x + 12 / k, "12 screen pixels to the right");
    near(copy.properties.text_box.y, orig.y + 12 / k, "and down");
    assertEq(JSON.stringify({ ...copy.properties.text_box, x: 0, y: 0 }), JSON.stringify({ ...orig, x: 0, y: 0 }), "in the same style and size");
    await page.waitForSelector(`[data-box-id="${copy.id}"].selected`, { timeout: 5000 });
    assert(!(await page.$(`[data-box-id="${firstId}"].selected`)), "the copy is selected, not the original");
    await page.keyboard.press("Delete");
    await until(async () => !(await stored(copy.id)), { what: "the copy deleted on the server" });
    assert(!(await page.$(".textBox.selected")), "nothing selected");
    await page.keyboard.press("t"); // the Text tool: Ctrl+Z is the block history's
    await page.waitForSelector(".pdfInkBar button[title^='Text (T)'].modeActive", { timeout: 5000 });
    await page.keyboard.press("Control+z");
    await until(async () => !!(await stored(copy.id)), { what: "the copy back on the server" });
    assert((await status()).includes("Undone: text box deletion"), `the undo names the deletion (${await status()})`);
    await page.locator(".pdfInkBar button[aria-label='Undo']").click(); // the strip's Undo, the same history
    await until(async () => !(await stored(copy.id)), { what: "the duplicate undone" });
    assertNoProblems(page);
  });

  await step("text box: the Text tool tapped again while typing opens its options on that box; opening a box's editor scrolls nothing", async () => {
    const b = await page.locator('[data-page="1"]').boundingBox();
    await page.mouse.click(b.x + 380, b.y + 420); // the Text tool is armed
    await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
    const id = await page.locator(".textBox.editing").getAttribute("data-box-id");
    await page.keyboard.type("restyle me");
    await page.click(".pdfInkBar button[title^='Text (T)']"); // its options
    await page.waitForSelector(".pdfInkBar [data-ink-options='text']", { timeout: 5000 });
    assert(await page.$(`[data-box-id="${id}"].editing .cm-focused`), "the box is still being typed in");
    await page.click(".pdfInkBar [data-ink-options='text'] button[aria-label='Text size 36 pt']");
    await until(async () => (await boxEl(id).evaluate((el) => el.style.getPropertyValue("--tb-size"))) === "36", { what: "that box restyled" });
    await page.keyboard.type(" more");
    await hand();
    await until(async () => {
      const x = await stored(id);
      return x?.content === "restyle me more" && x.properties.text_box.size === 36;
    }, { what: "its text and size on the server" });
    // Opening its editor by a click near the top of its big line scrolls
    // neither the viewer nor anything else.
    await boxEl(id).click();
    await page.waitForSelector(`[data-box-id="${id}"].selected`, { timeout: 5000 });
    await page.locator(".pdfViewer").evaluate((v) => { v.scrollTop += 40; });
    await page.waitForTimeout(300);
    const scrolled = () => page.evaluate(() => JSON.stringify([...document.querySelectorAll("*")]
      .filter((el) => el.scrollTop > 0).map((el) => [el.className, el.scrollTop])));
    const r = await boxEl(id).boundingBox();
    const at = await scrolled();
    await page.mouse.click(r.x + 12, r.y + 6);
    await page.waitForSelector(`[data-box-id="${id}"].editing .cm-focused`, { timeout: 5000 });
    await page.waitForTimeout(300); // a negative check: nothing to wait for
    assertEq(await scrolled(), at, "nothing scrolled");
    await page.keyboard.press("Escape");
    await page.keyboard.press("Delete");
    await until(async () => !(await stored(id)), { what: "that box deleted" });
    await armText();
    await page.click(".pdfInkBar button[title^='Text (T)']");
    await page.click(".pdfInkBar [data-ink-options='text'] button[aria-label='Text size 12 pt']"); // new boxes at 12 pt again
    await hand();
    assertNoProblems(page);
  });

  await step("text box: a press that leaves a box before it moves far enough ends where it is let go; a hover after it moves nothing", async () => {
    const el = boxEl(fixedId); // nothing is selected, the hand is armed
    const before = (await stored(fixedId)).properties.text_box;
    const b0 = await el.boundingBox();
    const y = b0.y + b0.height / 2;
    await page.mouse.move(b0.x + b0.width - 2, y);
    await page.mouse.down();
    await page.mouse.move(b0.x + b0.width + 40, y, { steps: 2 }); // out of the box within 6 px
    await page.mouse.up();
    await page.mouse.move(b0.x + b0.width / 2, y);
    await page.mouse.move(b0.x + b0.width / 2 + 60, y + 30, { steps: 12 }); // a plain hover
    await page.waitForTimeout(300);
    const b1 = await el.boundingBox();
    near(b1.x, b0.x, "the hover left it where it was", 0.5);
    near(b1.y, b0.y, "down too", 0.5);
    // nor does a press elsewhere, let go over the box, move it
    const pg = await page.locator('[data-page="1"]').boundingBox();
    await page.mouse.move(pg.x + 500, pg.y + 150);
    await page.mouse.down();
    await page.mouse.move(b1.x + b1.width / 2, b1.y + b1.height / 2, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(1200); // a negative check: nothing to wait for
    assertEq(JSON.stringify((await stored(fixedId)).properties.text_box), JSON.stringify(before), "nothing written");
    assert(!(await page.$(".textBox.selected")), "nor selected");
    assertNoProblems(page);
  });

  await step("text box: a stylus in hand mode sets a box's width by its handle, places the caret in its editor and moves it by its band, writing no ink", async () => {
    const cdp = await ctx.newCDPSession(page);
    const pen = stylus(cdp);
    const paths = '[data-page="1"] .inkLayer path';
    const groups = async () => (await subtree(pageId)).children.filter((c) => c.properties?.ink_url !== undefined).length;
    const groups0 = await groups();
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    await boxEl(fixedId).click(); // the hand is armed: a stylus writes right away elsewhere
    await page.waitForSelector(`[data-box-id="${fixedId}"].selected .textBoxWidth`, { timeout: 5000 });
    const w0 = (await stored(fixedId)).properties.text_box.w;
    const h = await boxEl(fixedId).locator(".textBoxWidth").boundingBox();
    await pen(h.x + h.width / 2, h.y + h.height / 2, h.x + h.width / 2 + 60, h.y + h.height / 2);
    const w1 = await until(async () => {
      const w = (await stored(fixedId)).properties.text_box.w;
      return w !== w0 ? w : null;
    }, { what: "the stylus's width on the server" });
    near(w1, w0 + 60 / k, "as wide as it was plus the stylus's drag", 1.5);
    await page.keyboard.press("F2");
    await page.waitForSelector(`[data-box-id="${fixedId}"].editing .cm-content`, { timeout: 5000 });
    const r = await boxEl(fixedId).boundingBox();
    await pen(r.x + 8, r.y + r.height / 2); // a tap in its text
    await page.waitForTimeout(300);
    assert(await page.$(`[data-box-id="${fixedId}"].editing .cm-focused`), "the editor stays open, focused");
    const before = (await stored(fixedId)).properties.text_box;
    await pen(r.x + r.width / 2, r.y - 3, r.x + r.width / 2 + 30, r.y - 3 + 20); // the band
    await until(async () => (await stored(fixedId)).properties.text_box.x !== before.x, { what: "the stylus's move on the server" });
    near((await stored(fixedId)).properties.text_box.y, before.y + 20 / k, "moved down by the band", 1.5);
    assertEq(await page.locator(paths).count(), 0, "no stroke drawn");
    await page.keyboard.press("Escape");
    await page.keyboard.press("Escape");
    assertEq(await groups(), groups0, "no ink group made");
    await cdp.detach();
    await page.waitForTimeout(1100); // the next step's nudge is a step of its own
    assertNoProblems(page);
  });

  await step("text box: Ctrl+Z follows the armed tool: a pen's undoes a stroke, the Text tool's a box's move", async () => {
    const b = await boxEl(fixedId).boundingBox(); // a stroke on the empty page below it
    const paths = '[data-page="1"] .inkLayer path';
    await page.keyboard.press("1"); // the first pen
    await page.mouse.move(b.x + 20, b.y + b.height + 60);
    await page.mouse.down();
    await page.mouse.move(b.x + 120, b.y + b.height + 80, { steps: 8 });
    await page.mouse.up();
    await until(async () => (await page.locator(paths).count()) === 1, { what: "the stroke" });
    await until(async () => (await subtree(pageId)).children.some((c) => c.properties?.ink_url), { what: "the ink group on the server" });
    await page.keyboard.press("v"); // the hand
    await boxEl(fixedId).click();
    await page.waitForSelector(`[data-box-id="${fixedId}"].selected`, { timeout: 5000 });
    const before = (await stored(fixedId)).properties.text_box;
    await page.keyboard.press("ArrowDown");
    await until(async () => (await stored(fixedId)).properties.text_box.y !== before.y, { what: "the box nudged" });
    await page.keyboard.press("1"); // a pen again: the box lets go, Ctrl+Z is the strokes'
    await until(async () => !(await page.$(".textBox.selected")), { what: "arming a pen lets go of the box" });
    assert(await page.$(".pdfInkBar button[aria-label='Undo ink']"), "the strip's Undo is the strokes'");
    await page.keyboard.press("Control+z");
    await until(async () => (await page.locator(paths).count()) === 0, { what: "the stroke undone" });
    assert((await status()).includes("Undone: ink stroke"), `the stroke history's undo (${await status()})`);
    assert((await stored(fixedId)).properties.text_box.y !== before.y, "the box stays where it was nudged");
    await page.keyboard.press("t");
    await page.waitForSelector(".pdfInkBar button[aria-label='Undo']", { timeout: 5000 });
    await page.keyboard.press("Control+z");
    await until(async () => (await stored(fixedId)).properties.text_box.y === before.y, { what: "the nudge undone" });
    assertEq(await page.locator(paths).count(), 0, "the stroke stays undone");
    await until(async () => !(await subtree(pageId)).children.some((c) => c.properties?.ink_url !== undefined),
      { what: "the emptied ink group gone from the server" });
    assertNoProblems(page);
    await ctx.close();
  });

  // (Chromium's emulation delivers no click for a tap after a touch
  // scroll, so the tap comes first.)
  await step("text box: a finger tap selects a box and a drag moves it; over a box not selected the finger scrolls; a remote delete lets go", async () => {
    ctx = await account.context(browser, { hasTouch: true });
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.waitForSelector(`[data-box-id="${firstId}"]`, { timeout: 15000 });
    const cdp = await ctx.newCDPSession(page);
    const drag = async (from, dx, dy) => {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [from] });
      for (let i = 1; i <= 12; i++) await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove", touchPoints: [{ x: from.x + dx * i / 12, y: from.y + dy * i / 12 }] });
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    };
    const middle = async (id) => {
      const r = await boxEl(id).boundingBox();
      return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
    };
    const scrollTop = () => page.locator(".pdfViewer").evaluate((el) => el.scrollTop);
    const k = (await page.locator('[data-page="1"]').boundingBox()).width / 612;
    const p = await middle(firstId);
    await page.touchscreen.tap(p.x, p.y);
    await page.waitForSelector(`[data-box-id="${firstId}"].selected`, { timeout: 5000 });
    const before = (await stored(firstId)).properties.text_box;
    const scrollAt = await scrollTop();
    await drag(await middle(firstId), 50, 30);
    const moved = await until(async () => {
      const tb = (await stored(firstId)).properties.text_box;
      return tb.x !== before.x ? tb : null;
    }, { what: "the finger-moved box on the server" });
    near(moved.x, before.x + 50 / k, "moved as far as the finger", 1.5);
    near(moved.y, before.y + 30 / k, "down too", 1.5);
    assertEq(await scrollTop(), scrollAt, "a finger on the selected box does not scroll");
    // Two fingers over it pinch: the box stays where it is.
    const c = await middle(firstId);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: c.x - 5, y: c.y, id: 1 }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: c.x - 5, y: c.y, id: 1 }, { x: c.x + 5, y: c.y, id: 2 }] });
    for (let i = 1; i <= 10; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove",
      touchPoints: [{ x: c.x - 5 - 6 * i, y: c.y - 2 * i, id: 1 }, { x: c.x + 5 + 6 * i, y: c.y + 2 * i, id: 2 }] });
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await page.waitForTimeout(1200); // a negative check: nothing to wait for
    assertEq(JSON.stringify((await stored(firstId)).properties.text_box), JSON.stringify(moved), "a pinch moves no box");
    // A selected box another client deletes lets go of its selection and menu.
    await menu().waitFor({ timeout: 5000 });
    await account.api(`/api/blocks/${firstId}`, { method: "DELETE" });
    await until(async () => !(await page.$(`[data-box-id="${firstId}"]`)) && !(await menu().count()), { what: "the box and its menu gone" });
    const fixedBefore = (await stored(fixedId)).properties.text_box;
    await boxEl(fixedId).scrollIntoViewIfNeeded();
    const scrollBefore = await scrollTop();
    const room = await page.locator(".pdfViewer").evaluate((el) => el.scrollHeight - el.clientHeight);
    await drag(await middle(fixedId), 0, scrollBefore > room / 2 ? 120 : -120); // towards the side it can scroll to
    await until(async () => Math.abs(await scrollTop() - scrollBefore) > 20, { what: "a finger over a box not selected scrolls the page" });
    assertEq(JSON.stringify((await stored(fixedId)).properties.text_box), JSON.stringify(fixedBefore), "and leaves the box");
    await cdp.detach();
    assertNoProblems(page);
    await ctx.close();
  });

  // --- on a sheet -------------------------------------------------------------------
  let nbId;
  const sheetBoxes = async () => boxesIn((await subtree(nbId)).children);

  await step("text box: on a sheet in the notebook view, a box under the sheet; it renders after a reload; the marker shows it", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${account.ws}`);
    await page.click("button[aria-label='Add']");
    await page.getByRole("menuitem", { name: "New notebook" }).or(page.locator("button", { hasText: "New notebook" })).first().click();
    await page.waitForSelector(".nbViewer .nbSheet", { timeout: 15000 });
    await page.keyboard.type("Box notebook");
    await page.keyboard.press("Enter");
    nbId = await until(async () => ((await account.api("/api/blocks/root/children")).children || [])
      .find((b) => b.content === "Box notebook")?.id, { what: "the notebook in the library" });
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    await armText();
    const s = await page.locator(".nbSheet").first().boundingBox();
    await page.mouse.click(s.x + 80, s.y + 120);
    await typeInBox(page);
    await hand();
    await page.waitForSelector(".nbSheet .textBox:not(.editing) strong", { timeout: 5000 });
    const [box] = await until(async () => {
      const found = await sheetBoxes();
      return found.length === 1 && found[0].content === TEXT ? found : null;
    }, { what: "the box under the sheet on the server" });
    const [sheet] = (await subtree(nbId)).children.filter((c) => c.properties?.sheet);
    assert(sheet.children.some((c) => c.id === box.id), "the sheet's child");
    assert(!("pdf_page" in box.properties), "no PDF page");
    // Abandoned: a click, then Escape.
    await armText();
    await page.mouse.click(s.x + 200, s.y + 400);
    await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
    await page.keyboard.press("Escape");
    await until(async () => (await page.$$(".nbSheet .textBox")).length === 1, { what: "the empty box gone from the sheet" });
    await page.reload();
    await page.waitForSelector(".nbViewer .nbSheet .textBox strong", { timeout: 15000 });
    const sheetRow = page.locator(".sortableBlockWrap", { has: page.locator(".blockSheetLabel", { hasText: "Page 1" }) }).first();
    await sheetRow.locator(".collapseBtn").first().click(); // a new sheet starts folded over what is on it
    await page.locator(".textBoxMarker").first().click();
    await page.waitForSelector(".nbViewer .textBoxFlash", { timeout: 5000 });
    assertEq((await sheetBoxes()).length, 1, "one box on the server");
    // A box under the sheet that still names a PDF page (moved there in
    // the notes) is on the sheet, and its row names no page.
    await account.api(`/api/pages/${nbId}/ops`, { method: "POST", body: { client: "e2e", ops: [{ op: "insert", id: "tbStalePage", parent: sheet.id,
      content: "on the sheet", props: { text_box: { x: 40, y: 400 }, pdf_page: 3 } }] } });
    await page.waitForSelector('.nbSheet [data-box-id="tbStalePage"]', { timeout: 5000 });
    const staleRow = page.locator('.blockRowWrap[data-block-id="tbStalePage"]');
    await staleRow.waitFor({ timeout: 5000 });
    assertEq(await staleRow.locator(".blockMeta").first().innerText(), "note", "no page named");
    await account.api("/api/blocks/tbStalePage", { method: "DELETE" });
    await until(async () => !(await page.$('[data-box-id="tbStalePage"]')), { what: "that box removed again" });
    assertNoProblems(page);
  });

  await step("text box: on a sheet a box is selected, moved, sized, restyled and deleted, and Ctrl+Z brings it back", async () => {
    const [{ id }] = await sheetBoxes();
    const [sheet] = (await subtree(nbId)).children.filter((c) => c.properties?.sheet);
    const k = (await page.locator(".nbSheet").first().boundingBox()).width / normalizePaper(sheet.properties.sheet).width;
    await boxEl(id).click(); // the strip is closed: a click selects
    await menu().waitFor({ timeout: 5000 });
    const before = (await stored(id, nbId)).properties.text_box;
    await dragBy(boxEl(id), 40, 30);
    const moved = await until(async () => {
      const tb = (await stored(id, nbId)).properties.text_box;
      return tb.x !== before.x ? tb : null;
    }, { what: "the moved box on the server" });
    near(moved.x, before.x + 40 / k, "moved with the drag");
    near(moved.y, before.y + 30 / k, "down too");
    await dragBy(boxEl(id).locator(".textBoxWidth"), 60, 0);
    await until(async () => (await stored(id, nbId)).properties.text_box.auto === false, { what: "a fixed width" });
    await menu().getByRole("button", { name: "Background", exact: true }).click();
    await menu().getByRole("button", { name: "Light blue background", exact: true }).click();
    await until(async () => (await stored(id, nbId)).properties.text_box.bg === "#dcecff", { what: "the background on the server" });
    await shot("textbox-menu-sheet");
    await menu().getByRole("button", { name: "Show note", exact: true }).click(); // lets go of the box for its row
    await until(async () => !(await menu().count()) && !(await page.$(".textBox.selected")), { what: "the box let go" });
    await boxEl(id).click();
    await menu().waitFor({ timeout: 5000 });
    await menu().getByRole("button", { name: "Delete", exact: true }).click();
    await until(async () => !(await stored(id, nbId)), { what: "the box deleted" });
    await page.keyboard.press("Control+z"); // no strip: the notes' undo, as ever
    await until(async () => (await stored(id, nbId))?.properties.text_box.bg === "#dcecff", { what: "the box back, as it was" });
    await page.waitForSelector(`.nbSheet [data-box-id="${id}"]`, { timeout: 5000 });
    assertNoProblems(page);
  });

  await step("text box: on a sheet in the notes view, the same", async () => {
    await page.click(".pdfZoomOverlay button[aria-label='Notes view']");
    await page.waitForSelector(".noteSheet .nbSheet .textBox strong", { timeout: 5000 });
    await page.click("button[aria-label='Write on this page']");
    await page.waitForSelector(".notesInkDock .pdfInkBar", { timeout: 5000 });
    await armText(".notesInkDock .pdfInkBar");
    const sheet = page.locator(".noteSheet .nbSheet").first();
    await sheet.evaluate((n) => n.scrollIntoView({ block: "start" }));
    const s = await sheet.boundingBox();
    await page.mouse.click(s.x + 60, s.y + 260);
    await page.waitForSelector(".noteSheet .textBox.editing .cm-content", { timeout: 5000 });
    await page.keyboard.type("Typed among the notes");
    await hand(".notesInkDock .pdfInkBar");
    await until(async () => (await sheetBoxes()).some((b) => b.content === "Typed among the notes"), { what: "the second box on the server" });
    await armText(".notesInkDock .pdfInkBar");
    await page.mouse.click(s.x + 300, s.y + 420);
    await page.waitForSelector(".noteSheet .textBox.editing .cm-content", { timeout: 5000 });
    await page.keyboard.press("Escape");
    await until(async () => (await page.$$(".noteSheet .textBox")).length === 2, { what: "the empty box gone from the sheet in the notes" });
    await page.reload();
    await until(async () => (await page.$$(".noteSheet .nbSheet .textBox")).length === 2, { what: "both boxes after the reload", timeout: 15000 });
    const marker = page.locator(".blockRow", { has: page.locator(".blockRendered", { hasText: "Typed among the notes" }) })
      .locator(".textBoxMarker");
    await marker.click();
    await page.waitForSelector(".noteSheet .textBoxFlash", { timeout: 5000 });
    await shot("textbox-notes-sheet");
    assertEq((await sheetBoxes()).length, 2, "two boxes on the server");
    assertNoProblems(page);
    await ctx.close();
  });

  // --- over a PDF link, and two people on one box ---------------------------------

  await step("text box: a box over a PDF link takes its own clicks, the Text tool's too; the link answers beside it; ink is drawn over the boxes", async () => {
    ctx = await account.context(browser);
    const up = await account.upload("/api/uploads", linkPdf(), "link.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Link paper", source_url: up.source_url } });
    pageId = created.id;
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await page.waitForSelector('[data-page="1"] .pdfLinkBox', { timeout: 30000 });
    const link = await page.locator('[data-page="1"] .pdfLinkBox').first().boundingBox();
    const prompt = () => page.$(".linkPromptModal");
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    await armText();
    await page.mouse.click(link.x + 40, link.y + link.height / 2); // with the Text tool a tap on the link makes a box there
    await page.waitForSelector(".textBox.editing .cm-content", { timeout: 5000 });
    assert(!(await prompt()), "the link did not open");
    const id = await page.locator(".textBox.editing").getAttribute("data-box-id");
    await page.keyboard.type("Over link");
    await page.keyboard.press("Escape");
    await hand();
    await page.keyboard.press("Escape");
    await until(async () => (await stored(id))?.content === "Over link", { what: "the box on the server" });
    const r = await boxEl(id).boundingBox();
    const mid = [r.x + r.width / 2, r.y + r.height / 2];
    assert(await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest(".textBox"), mid), "the box is on top of the link");
    await page.mouse.click(...mid);
    await page.waitForSelector(`[data-box-id="${id}"].selected`, { timeout: 5000 });
    await page.waitForTimeout(300); // a negative check: nothing to wait for
    assert(!(await prompt()), "a click on the box is the box's, not the link's");
    await page.keyboard.press("Escape");
    await armText();
    await page.mouse.click(...mid); // the Text tool opens the box rather than making one over it
    await page.waitForSelector(`[data-box-id="${id}"].editing .cm-content`, { timeout: 5000 });
    assertEq(await page.locator('[data-page="1"] .textBox').count(), 1, "no second box");
    await page.keyboard.press("Escape");
    await hand();
    await page.keyboard.press("Escape");
    await page.mouse.click(link.x + link.width - 10, link.y + link.height / 2); // the link beside the box
    await until(prompt, { what: "the link's prompt" });
    await page.locator(".linkPromptModal").getByRole("button", { name: "Cancel", exact: true }).click();
    await until(async () => !(await prompt()), { what: "the prompt closed" });
    const order = await page.evaluate(() => {
      const z = (el) => Number(getComputedStyle(el).zIndex);
      const boxes = document.querySelector('[data-page="1"] .textBoxLayer'), ink = document.querySelector('[data-page="1"] .inkLayer');
      return { boxes: z(boxes), ink: z(ink), link: z(document.querySelector('[data-page="1"] .pdfLinkBox')),
        inkAfter: !!(boxes.compareDocumentPosition(ink) & Node.DOCUMENT_POSITION_FOLLOWING) };
    });
    assert(order.boxes > order.link && order.ink === order.boxes && order.inkAfter, `boxes over links, ink over boxes: ${JSON.stringify(order)}`);
    assertNoProblems(page);
    await ctx.close();
  });

  await step("text box: two people on one box: a move made while the other types keeps both, on the server and both screens; its undo keeps the other's typing", async () => {
    // A shared workspace: alice owns it, bob edits (manage.py: admin-only setup).
    const out = server.manage("create-workspace", "Box team", "alice", "shared");
    const teamId = (out.match(/workspace (\S+)/) || [])[1];
    assert(teamId, `shared workspace id from: ${out}`);
    server.manage("set-member", teamId, "bob", "editor");
    const aliceT = Object.assign(Object.create(Object.getPrototypeOf(alice)), alice, { ws: teamId });
    const bobT = Object.assign(Object.create(Object.getPrototypeOf(bob)), bob, { ws: teamId });
    const up = await aliceT.upload("/api/uploads", makePdf([["A page for two", "with some text"]]), "two.pdf", "application/pdf");
    const paper = await aliceT.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Box for two", source_url: up.source_url } });
    const box0 = { x: 100, y: 200, w: 60, h: 23, auto: true, size: 12, color: "#1f1f1f", bg: null };
    await aliceT.api(`/api/pages/${paper.id}/ops`, { method: "POST", body: { client: "e2e", ops: [{ op: "insert", id: "tbTwo", parent: paper.id,
      content: "Hello", props: { text_box: box0, pdf_page: 1 } }] } });
    const shared = async () => boxesIn((await aliceT.api(`/api/blocks/${paper.id}/subtree`)).block.children).find((b) => b.id === "tbTwo");
    const ctxA = await aliceT.context(browser), ctxB = await bobT.context(browser);
    const url = `${server.base}/?page=${paper.id}&ws=${teamId}`;
    const A = await openPage(ctxA, url), B = await openPage(ctxB, url);
    const box = (p) => p.locator('[data-box-id="tbTwo"]');
    await box(A).waitFor({ timeout: 30000 });
    await box(B).waitFor({ timeout: 30000 });
    // bob opens the box and types while offline, so his keystrokes (and the
    // size his box measures at) wait while alice moves it
    await box(B).click();
    await box(B).click();
    await B.waitForSelector('[data-box-id="tbTwo"].editing .cm-content', { timeout: 5000 });
    await B.keyboard.press("End");
    await ctxB.setOffline(true);
    await B.keyboard.type(" world, typed meanwhile");
    await B.waitForEvent("requestfailed", { predicate: (r) => r.url().includes("/ops"), timeout: 8000 });
    const k = (await A.locator('[data-page="1"]').boundingBox()).width / 612;
    const ra = await box(A).boundingBox();
    await A.mouse.move(ra.x + ra.width / 2, ra.y + ra.height / 2);
    await A.mouse.down();
    await A.mouse.move(ra.x + ra.width / 2 + 120, ra.y + ra.height / 2 + 60, { steps: 8 });
    await A.mouse.up();
    const moved = await until(async () => {
      const tb = (await shared()).properties.text_box;
      return tb.x !== box0.x ? tb : null;
    }, { what: "alice's move on the server" });
    near(moved.x, box0.x + 120 / k, "moved as far as the drag", 1.5);
    await ctxB.setOffline(false);
    const both = await until(async () => {
      const b = await shared();
      return b.content === "Hello world, typed meanwhile" ? b : null;
    }, { what: "bob's typing on the server", timeout: 15000 });
    assertEq(both.properties.text_box.x, moved.x, "the move survives bob's keystrokes");
    assertEq(both.properties.text_box.y, moved.y, "down too");
    assert(both.properties.text_box.w > box0.w, `bob's box measured wider as he typed (${both.properties.text_box.w})`);
    // both screens show it where alice put it
    for (const [p, who] of [[A, "alice"], [B, "bob"]]) {
      await until(async () => {
        const pg = await p.locator('[data-page="1"]').boundingBox(), rb = await box(p).boundingBox();
        return Math.abs((rb.x - pg.x) / (pg.width / 612) - moved.x) < 1 && (await box(p).innerText()).includes("typed meanwhile");
      }, { what: `the box where alice put it, with bob's text, on ${who}'s screen` });
    }
    // alice's Ctrl+Z (her box is selected) takes her move back, not bob's typing
    await A.keyboard.press("Control+z");
    await until(async () => (await shared()).properties.text_box.x === box0.x, { what: "alice's move undone" });
    const after = await shared();
    assertEq(after.content, "Hello world, typed meanwhile", "bob's text stays");
    assertEq(after.properties.text_box.w, both.properties.text_box.w, "and the size his box measured at");
    assert((await A.locator(".statusPill").allInnerTexts()).join(" | ").includes("Undone: text box move"), "the undo names the move");
    await B.keyboard.press("Escape");
    assertNoProblems(A);
    // The failed POST and the dropped socket are expected while offline.
    assertNoProblems(B, [/ERR_INTERNET_DISCONNECTED|Failed to fetch|WebSocket|net::ERR/]);
    await ctxA.close();
    await ctxB.close();
  });
}
