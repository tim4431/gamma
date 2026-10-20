// Handwriting (docs/dev/handwriting.md): the tool strip and its presets,
// mouse strokes becoming an ink block with an .ink upload, persistence
// across a reload, the version 2 file a pressure stroke leaves and the
// highlighter band under the ink, the eraser, the lasso, the notes card's jump + flash,
// and a group's Transcribe with AI. /Ink in the annotated PDF is
// backend/tests/test_ink.py; the chat's view_ink is test_ai_tools_ink.py.
import { assertOpensInPlace, fakeAiModels } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";
import { decodeStroke } from "../../../src/ink/ink.js";

async function drawLine(page, from, to) {
  await page.mouse.move(from[0], from[1]);
  await page.mouse.down();
  await page.mouse.move(to[0], to[1], { steps: 12 });
  await page.mouse.up();
}

export async function inkScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage, flags }) {
  let ctx, page, pageId, box, inkBlock;
  const account = alice;
  const inkBlockOnServer = async () => {
    const d = await account.api(`/api/blocks/${pageId}/subtree`);
    return (d.block.children || []).find((b) => b.properties?.ink_url) || null;
  };

  await step("ink: the pen strip opens and two mouse strokes make a handwriting block with an .ink upload", async () => {
    const pdf = makePdf([["A page to write on", "with some text"]]);
    const up = await account.upload("/api/uploads", pdf, "ink.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Ink paper", source_url: up.source_url } });
    pageId = created.id;
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    assert(await page.$(".pdfInkBar .inkToolBtn.modeActive .inkToolInk"), "a pen preset is armed when the strip opens");
    assertEq((await page.$$(".pdfInkBar .inkToolInk")).length, 7, "the default presets: four pens, three highlighters");
    const buttons = await page.locator(".pdfInkRow button").evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")));
    assertEq(JSON.stringify(buttons.slice(-2)), JSON.stringify(["Undo ink", "Redo ink"]), "history controls are last");
    // Five groups split by rules: presets | eraser, lasso, text | hand | More, close | undo, redo.
    const groups = await page.locator(".pdfInkRow").evaluate((row) => [...row.children].map((el) =>
      el.classList.contains("pdfInkSep") ? "|" : el.classList.contains("pdfInkHistory") ? "history"
        : el.hasAttribute("data-preset") ? "preset" : (el.getAttribute("aria-label") || "").split(" ")[0]).join(" "));
    assertEq(groups, "preset preset preset preset preset preset preset | Eraser Lasso Text | Hand | More Close | history", "the row's five groups");
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 150], [box.x + 250, box.y + 170]);
    await drawLine(page, [box.x + 100, box.y + 250], [box.x + 250, box.y + 280]);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "two stroke paths" });
    await page.waitForSelector(".blockRow .inkMarker", { timeout: 5000 });
    await page.waitForSelector(".blockInkCard", { timeout: 5000 });
    inkBlock = await until(async () => {
      const b = await inkBlockOnServer();
      return b && b.properties.ink_url.endsWith(".ink") && b.properties.ink_strokes === 2 ? b : null;
    }, { what: "ink block with two uploaded strokes" });
    assert(!("pdf_page" in inkBlock.properties), "the page is the position's alone");
    const pos = inkBlock.properties.pdf_position;
    assertEq(JSON.stringify([pos.pageNumber, pos.width, pos.height]), JSON.stringify([1, 612, 792]), "pdf_position page and size");
    assertEq(JSON.stringify(Object.keys(pos.boundingRect)), JSON.stringify(["x1", "y1", "x2", "y2"]), "no size per rect");
    const ink = await account.api(inkBlock.properties.ink_url);
    assertEq(ink.format, "gamma-ink", "file format");
    assertEq(ink.strokes.length, 2, "strokes in the file");
    assert(ink.strokes.every((s) => s.pen === false && s.ch === "xyt"), "mouse strokes carry no pressure channel");
    await page.getByRole("button", { name: "Undo ink", exact: true }).click();
    await page.getByRole("button", { name: "Redo ink", exact: true }).click();
    await page.waitForSelector(".statusPill");
    const toolbar = await page.locator(".pdfInkBar").boundingBox();
    const message = await page.locator(".statusPill").boundingBox();
    assert(message.y >= toolbar.y + toolbar.height, "undo/redo feedback stays below the toolbar");
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-drawn.png` });
    assertNoProblems(page);
  });

  await step("ink: strokes survive a reload; the eraser removes one and the file follows", async () => {
    await page.reload();
    await waitForPdf(page, 1);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "two paths after reload", timeout: 15000 });
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    await page.keyboard.press("e");
    await page.waitForSelector(".pdfInkBar button[title^='Eraser'].modeActive", { timeout: 5000 });
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 175, box.y + 140], [box.x + 178, box.y + 185]);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 1, { what: "one path left" });
    await until(async () => (await inkBlockOnServer())?.properties.ink_strokes === 1, { what: "one stroke on the server" });
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".pdfInkBar")), { what: "strip closed by Escape" });
    assertNoProblems(page);
  });

  await step("ink: Ctrl+Z undoes the erasure and a stroke; Ctrl+Shift+Z redoes", async () => {
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    // The history is per visit: after the reload only the erasure is on it.
    await page.keyboard.press("Control+z");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "erased stroke back" });
    await page.keyboard.press("p");
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 400], [box.x + 250, box.y + 420]);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 3, { what: "a third stroke" });
    await page.keyboard.press("Control+z");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "third stroke undone" });
    await page.keyboard.press("Control+Shift+z");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 3, { what: "redone" });
    await page.keyboard.press("Control+z");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "undone again" });
    await until(async () => (await inkBlockOnServer())?.properties.ink_strokes === 2, { what: "two strokes on the server" });
    assert((await page.$$(".blockRow")).length >= 1, "the block is still there");
    assertNoProblems(page);
  });

  await step("ink: the partial eraser cuts a stroke in two; the lasso selects and moves them", async () => {
    await page.keyboard.press("e");
    await page.click(".pdfInkBar button[title^='Eraser']");          // the armed tool again: its options row
    await page.waitForSelector(".pdfInkSub[data-ink-options='eraser']");
    await page.click(".pdfInkBar button[aria-label='Partial']");
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-eraser.png` });
    box = await page.locator('[data-page="1"]').boundingBox();
    // straight down through the middle of the first stroke (y ≈ 160 at x = 175)
    await drawLine(page, [box.x + 175, box.y + 140], [box.x + 175, box.y + 185]);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 3, { what: "three pieces" });
    await until(async () => (await inkBlockOnServer())?.properties.ink_strokes === 3, { what: "three strokes on the server" });
    // lasso around the whole second stroke (100..250 × 250..280)
    await page.keyboard.press("l");
    await page.mouse.move(box.x + 80, box.y + 235);
    await page.mouse.down();
    for (const [x, y] of [[270, 235], [270, 300], [80, 300], [80, 235]]) await page.mouse.move(box.x + x, box.y + y, { steps: 4 });
    await page.mouse.up();
    await page.waitForSelector('[data-page="1"] .inkSelRect', { timeout: 5000 });
    const before = (await inkBlockOnServer()).properties.pdf_position.boundingRect;
    await drawLine(page, [box.x + 170, box.y + 265], [box.x + 170, box.y + 365]);   // drag the box 100 px down
    await until(async () => {
      const b = await inkBlockOnServer();
      return b && b.properties.pdf_position.boundingRect.y2 > before.y2 + 50 ? b : null;
    }, { what: "the group's box moved down" });
    await page.keyboard.press("Delete");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "selection deleted" });
    assertNoProblems(page);
  });

  await step("ink: a preset's options row sets its colour and width, duplicates and removes it; presets follow the account; the box lasso", async () => {
    try {
      await page.keyboard.press("1");
      await page.click(".pdfInkBar .inkToolBtn.modeActive");             // tap the armed preset again
      await page.waitForSelector(".pdfInkSub[data-ink-options='tool']");
      assertEq(JSON.stringify(await page.locator(".pdfInkSub .inkOptCaption").allTextContents()), JSON.stringify(["Style", "Color", "Width"]),
        "three captioned sub-rows");
      await page.click(".pdfInkSub button[aria-label='Red']");
      await page.click(".pdfInkSub button[aria-label='Width 4 pt']");
      assertEq(await page.locator(".pdfInkSub button[aria-label='Width 4 pt']").getAttribute("title"), "Width 4 pt · [ ]", "a width dot names the bracket keys");
      await page.getByRole("button", { name: "Ballpoint", exact: true }).click();
      assertEq(await page.getByRole("button", { name: "Ballpoint", exact: true }).getAttribute("aria-pressed"), "true");
      assertEq(await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label"),
        "Red ballpoint pen · 4 pt · key 1 — tap again for options", "a preset is named in words, not a hex code");
      await page.click(".pdfInkSub button[aria-label='Duplicate tool']");
      await until(async () => (await page.$$(".pdfInkBar .inkToolInk")).length === 8, { what: "a duplicated preset" });
      assert(await page.$(".pdfInkSub[data-ink-options='tool']"), "the copy stays open for editing");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-presets.png` });
      box = await page.locator('[data-page="1"]').boundingBox();
      await drawLine(page, [box.x + 100, box.y + 400], [box.x + 250, box.y + 420]);
      // The reload in an earlier step ended the group: this stroke is a new
      // block. Its file carries the copy's look.
      const red = await until(async () => {
        const d = await account.api(`/api/blocks/${pageId}/subtree`);
        for (const b of d.block.children || []) {
          if (!b.properties?.ink_url?.endsWith(".ink")) continue;
          const ink = await account.api(b.properties.ink_url);
          const hit = ink.strokes.find((st) => st.color === "#dc2626");
          if (hit) return hit;
        }
        return null;
      }, { what: "a stroke in the copy's colour on the server" });
      assertEq(red.size, 4, "the copy drew with its width");
      assertEq(red.brush, "monoline", "the duplicated Ballpoint preset saved the monoline brush");
      assertEq(red.tool, "pen", "…as a pen");
      await page.click(".pdfInkSub button[aria-label='Remove tool']");
      await until(async () => (await page.$$(".pdfInkBar .inkToolInk")).length === 7, { what: "the copy removed" });
      assert(await page.$(".pdfInkBar .inkToolBtn.modeActive"), "a neighbour is armed after the removal");
      // The presets are the account's: the edited list reaches the profile,
      // and a browser with no local copy takes it from there on load.
      const profileTools = async () => (await account.api("/api/prefs/profile")).value?.inkTools || [];
      await until(async () => {
        const tools = await profileTools();
        return tools.length === 7 && tools[0].color === "#dc2626" && tools[0].size === 4 && tools[0].brush === "monoline";
      }, { what: "the edited presets in the account's profile" });
      await page.evaluate(() => localStorage.removeItem("gamma-ink-tools"));
      await page.reload();
      await waitForPdf(page, 1);
      await page.click("button[aria-label='Handwriting tools']");
      await page.waitForSelector(".pdfInkBar");
      await until(async () => (await page.$eval(".pdfInkBar .inkToolInk", (el) => getComputedStyle(el).backgroundColor)) === "rgb(220, 38, 38)",
        { what: "the first preset's colour, back from the account" });
      await page.keyboard.press("1");
      await page.click(".pdfInkBar .inkToolBtn.modeActive");
      assertEq(await page.getByRole("button", { name: "Ballpoint", exact: true }).getAttribute("aria-pressed"), "true",
        "the first preset kept its brush across reload");
      await page.getByRole("button", { name: "Pen", exact: true }).click();
      assertEq(await page.getByRole("button", { name: "Pen", exact: true }).getAttribute("aria-pressed"), "true");
      // The lasso's box mode: a dragged rectangle selects what it covers.
      await page.keyboard.press("l");
      await page.click(".pdfInkBar button[title^='Lasso']");
      await page.waitForSelector(".pdfInkSub[data-ink-options='select']");
      await page.click(".pdfInkSub button[aria-label='Box']");
      box = await page.locator('[data-page="1"]').boundingBox();
      await drawLine(page, [box.x + 80, box.y + 380], [box.x + 270, box.y + 440]);
      await page.waitForSelector('[data-page="1"] .inkSelRect', { timeout: 5000 });
      assertEq(await page.locator(".pdfInkSub").count(), 0, "a selection closes the options row");
      await page.keyboard.press("Escape");                               // drops the selection
      await until(async () => !(await page.$('[data-page="1"] .inkSelRect')), { what: "selection dropped" });
      assert(await page.$(".pdfInkBar"), "the strip is still open");
      await page.keyboard.press("Escape");                               // closes the strip
      await until(async () => !(await page.$(".pdfInkBar")), { what: "strip closed" });
    } finally {
      // The presets back to the defaults — on the account, which every group
      // on this worker shares, and in this browser — for the steps that follow.
      // The browser's last change (Pen) pushes after a second: let it land
      // first, or it would put the edited list back over the reset.
      await until(async () => {
        const first = ((await account.api("/api/prefs/profile")).value?.inkTools || [])[0];
        return first && first.color === "#dc2626" && !first.brush;
      }, { what: "the last preset change pushed" }).catch(() => {});
      await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: null } } }).catch(() => {});
      await page.evaluate(() => localStorage.removeItem("gamma-ink-tools")).catch(() => {});
      await page.reload().catch(() => {});
      await waitForPdf(page, 1).catch(() => {});
    }
    assertNoProblems(page);
  });

  await step("ink: More (⋯) opens its menu on the press, Escape closes the menu alone, and Handwriting settings… lands on the section", async () => {
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    const more = page.locator(".pdfInkBar button[aria-label='More handwriting tools']");
    assertEq(await more.getAttribute("aria-haspopup"), "menu");
    assertEq(await more.getAttribute("data-guide"), "ink.more");
    await assertOpensInPlace(page, ".inkStripMenu", () => more.click(), { what: "the More menu" });
    assertEq(await more.getAttribute("aria-expanded"), "true");
    const menu = page.locator(".inkStripMenu");
    const paste = menu.locator("[data-command='ink.paste']");
    assert(await paste.isDisabled(), "nothing on the handwriting clipboard: Paste is greyed");
    assertEq(await paste.getAttribute("title"), "Copy or cut handwriting first");
    assertEq(await paste.textContent(), "Paste handwritingCtrl+V", "the command's label and its chord");
    assert(!(await menu.locator("[data-command='ink.selectAll']").isDisabled()), "Select all applies with a tool armed");
    assertEq(await menu.getByRole("button", { name: "Handwriting settings…", exact: true }).count(), 1);
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".inkStripMenu")), { what: "the menu closed by Escape" });
    assertEq(await page.locator(".pdfInkBar").count(), 1, "the strip stays open: Escape went to the menu");
    assertEq(await more.getAttribute("aria-expanded"), "false");
    await more.click();
    await menu.getByRole("button", { name: "Handwriting settings…", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
    await dialog.waitFor();
    await until(() => page.locator(".settingsPane [data-setting='Reset presets']").first().isVisible(), { what: "the Handwriting section in view" });
    await page.getByRole("button", { name: "Close settings", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    assertNoProblems(page);
  });

  await step("ink: Move right, Hide and Hidden tools in the Tool menu, and a mouse drag, reorder the presets; the number keys follow", async () => {
    const armed = () => page.locator(".pdfInkBar .inkToolBtn.modeActive");
    const name = async () => (await armed().getAttribute("aria-label")).split(" · ")[0];
    const presets = () => page.locator(".pdfInkRow [data-preset]");
    const presetName = async (i) => (await presets().nth(i).getAttribute("aria-label")).split(" · ")[0];
    const pillText = async () => (await page.locator(".statusPill .pillText").textContent()) || "";
    const toolMenu = async () => {
      await page.locator(".pdfInkSub button[aria-label='Tool menu']").click();
      await page.waitForSelector(".inkStripMenu");
      return page.locator(".inkStripMenu");
    };
    const ids = async () => page.evaluate(() => JSON.parse(localStorage.getItem("gamma-ink-tools")).map((t) => t.id).join());
    const order = await ids();
    await page.keyboard.press("1");
    const first = await name();                                          // the first default pen, by its colour
    await armed().click();                                               // its options row
    await page.waitForSelector(".pdfInkSub[data-ink-options='tool']");
    let menu = await toolMenu();
    assert(await menu.getByRole("button", { name: "Move left", exact: true }).isDisabled(), "nothing to the left of key 1");
    assert(await menu.getByRole("button", { name: "Hidden tools", exact: true }).isDisabled(), "no hidden tools yet");
    await menu.getByRole("button", { name: "Move right", exact: true }).click();
    await until(async () => (await pillText()).includes(`${first} is now key 2.`), { what: "the move's pill" });
    assertEq(await presetName(1), first, "key 2 is the moved preset");
    assert((await armed().getAttribute("aria-label")).includes("key 2"), "still armed, at key 2");
    await page.keyboard.press("1");
    assert((await name()) !== first, "key 1 arms the preset that moved up");
    await page.keyboard.press("2");
    assertEq(await name(), first, "key 2 arms the moved one");
    await armed().click();
    menu = await toolMenu();
    await menu.getByRole("button", { name: "Move left", exact: true }).click();
    await until(async () => (await pillText()).includes(`${first} is now key 1.`), { what: "moved back" });
    assertEq(await ids(), order, "the ids never change");
    // Hide: off the row and out of the keys, kept in the list; the pill offers Show; Hidden tools brings it back
    menu = await toolMenu();
    await menu.getByRole("button", { name: "Hide", exact: true }).click();
    await until(async () => (await presets().count()) === 6, { what: "six presets on the row" });
    assert((await pillText()).includes(`${first} hidden.`), "the pill names it");
    assertEq(await page.locator(".statusPill").getByRole("button", { name: "Show", exact: true }).count(), 1, "Show is on the pill");
    assert((await presetName(0)) !== first, "key 1 is the next preset");
    assert((await armed().getAttribute("aria-label")).includes("key 1"), "its neighbour is armed");
    assertEq(await page.evaluate(() => JSON.parse(localStorage.getItem("gamma-ink-tools")).filter((t) => t.hidden === true).length), 1, "kept in the list, hidden");
    await armed().click();
    menu = await toolMenu();
    await menu.getByRole("button", { name: "Hidden tools", exact: true }).click();
    await menu.getByRole("menu", { name: "Hidden tools" }).getByRole("button", { name: new RegExp(`^${first} · `) }).click();
    await until(async () => (await presets().count()) === 7, { what: "seven presets again" });
    assertEq(await presetName(0), first, "back where it was, at key 1");
    assertEq(await ids(), order);
    // A mouse drag: past 6 px the preset rides along as a ghost, a bar marks the slot, the drop writes the order
    const from = await presets().nth(0).boundingBox(), third = await presets().nth(2).boundingBox();
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(from.x + from.width / 2 + 12, from.y + from.height / 2, { steps: 3 });
    await page.waitForSelector(".inkDragGhost");
    assert(await page.$(".pdfInkRow .inkDropBar"), "the insertion bar shows");
    assert((await pillText()).includes("Drop to reorder."), "the pill says what the drag does");
    await page.mouse.move(third.x + third.width - 2, third.y + third.height / 2, { steps: 6 });   // past the third preset's centre: after it
    await page.mouse.up();
    await until(async () => (await pillText()).includes(`${first} is now key 3.`), { what: "the drop's pill" });
    await until(async () => !(await page.$(".inkDragGhost")), { what: "the ghost gone" });
    assertEq(await presetName(2), first, "the dragged preset at key 3");
    assert(await page.$(".pdfInkSub[data-ink-options='tool']"), "the drop was no tap: the options row is as it was");
    await page.keyboard.press("3");
    assertEq(await name(), first, "key 3 arms it");
    const back = await presets().nth(2).boundingBox(), front = await presets().nth(0).boundingBox();
    await page.mouse.move(back.x + back.width / 2, back.y + back.height / 2);
    await page.mouse.down();
    await page.mouse.move(front.x + 2, front.y + front.height / 2, { steps: 8 });
    await page.mouse.up();
    await until(async () => (await pillText()).includes(`${first} is now key 1.`), { what: "dragged back to the front" });
    assertEq(await ids(), order, "the original order, the same ids");
    assertNoProblems(page);
  });

  await step("ink: [ and ] step the armed preset's width with a pill; E held across an erasing pass returns to the tool before it, a lasso selection with it", async () => {
    const armed = () => page.locator(".pdfInkBar .inkToolBtn.modeActive");
    const pillText = async () => (await page.locator(".statusPill .pillText").textContent()) || "";
    await page.keyboard.press("1");
    const [first, width] = (await armed().getAttribute("aria-label")).split(" · ");
    assertEq(width, "2 pt", "the first default pen");
    await armed().click();
    await page.waitForSelector(".pdfInkSub[data-ink-options='tool']");
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".pdfInkSub")), { what: "Escape closed the options row" });
    assert(await page.$(".pdfInkBar"), "…and left the strip open");
    await page.keyboard.press("BracketRight");
    await until(async () => (await pillText()).includes(`${first} · 2.8 pt.`), { what: "the width pill" });
    assert((await armed().getAttribute("aria-label")).includes(" · 2.8 pt · "), "the preset is thicker, options row closed or not");
    await page.keyboard.press("BracketLeft");
    await until(async () => (await pillText()).includes(`${first} · 2 pt.`), { what: "thinner again" });
    // A tap on E arms the eraser and it stays; E held while a pass is made goes back to the tool before it.
    await page.keyboard.press("e");
    assert((await armed().getAttribute("aria-label")).startsWith("Eraser"), "a tap arms the eraser");
    await page.keyboard.press("1");
    box = await page.locator('[data-page="1"]').boundingBox();
    await page.keyboard.down("e");
    await until(async () => (await armed().getAttribute("aria-label")).startsWith("Eraser"), { what: "the eraser armed on the keydown" });
    await sleep(350);
    await drawLine(page, [box.x + 420, box.y + 700], [box.x + 440, box.y + 720]);   // a pass over nothing still counts
    await page.keyboard.up("e");
    await until(async () => (await armed().getAttribute("aria-label")).startsWith(first), { what: "back to the pen after the hold" });
    // With the lasso: its selection comes back with it.
    await page.keyboard.press("l");
    await drawLine(page, [box.x + 80, box.y + 380], [box.x + 270, box.y + 440]);   // the box lasso, from the step before
    await page.waitForSelector('[data-page="1"] .inkSelRect', { timeout: 5000 });
    await page.keyboard.down("e");
    await until(async () => !(await page.$('[data-page="1"] .inkSelRect')), { what: "the eraser drops the selection" });
    await sleep(350);
    await drawLine(page, [box.x + 420, box.y + 700], [box.x + 440, box.y + 720]);
    await page.keyboard.up("e");
    await page.waitForSelector('[data-page="1"] .inkSelRect', { timeout: 5000 });
    assert((await armed().getAttribute("aria-label")).startsWith("Lasso"), "the lasso is armed again, its selection restored");
    await page.keyboard.press("Escape");                               // drops the selection
    await until(async () => !(await page.$('[data-page="1"] .inkSelRect')), { what: "selection dropped" });
    await page.keyboard.press("Escape");                               // closes the strip
    await until(async () => !(await page.$(".pdfInkBar")), { what: "strip closed" });
    assertNoProblems(page);
  });

  await step("ink: the notes card jumps to the group and outlines it; its replay plays on the PDF page", async () => {
    await page.click(".blockInkCard");
    await page.waitForSelector('[data-page="1"] .inkFlash', { timeout: 5000 });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-notes.png` });
    const paths = '[data-page="1"] .inkLayer path';
    const whole = (await page.$$(paths)).length;
    await page.hover(".blockInkCardWrap");
    await page.click(".blockInkCardWrap .inkReplayBtn");
    await until(async () => (await page.$$(paths)).length < whole, { what: "the page draws the group stroke by stroke", every: 10 });
    await until(async () => (await page.$$(paths)).length === whole, { what: "then whole again" });
    assertNoProblems(page);
  });

  await step("ink: erasing the last stroke stays erased while block deletion is pending", async () => {
    // Reopening the page starts a fresh handwriting group.
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("p");
    const paths = '[data-page="1"] .inkLayer path';
    const count = await page.locator(paths).count();
    const before = await account.api(`/api/blocks/${pageId}/subtree`);
    const ids = new Set(before.block.children.map((b) => b.id));
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 320], [box.x + 250, box.y + 320]);
    const group = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.find((b) => !ids.has(b.id) && b.properties?.ink_url);
    }, { what: "new group saved before erasure" });
    await until(async () => await page.locator(paths).count() === count + 1);
    let release, deleting = false;
    const held = new Promise((resolve) => { release = resolve; });
    const url = `**/api/blocks/${group.id}`;
    await page.route(url, async (route) => {
      if (route.request().method() === "DELETE") { deleting = true; await held; }
      await route.continue();
    });
    try {
      await page.keyboard.press("e");
      await page.click(".pdfInkBar button[title^='Eraser']");
      await page.click(".pdfInkSub button[aria-label='Whole strokes']");
      box = await page.locator('[data-page="1"]').boundingBox();
      await drawLine(page, [box.x + 175, box.y + 310], [box.x + 175, box.y + 330]);
      await until(async () => await page.locator(paths).count() === count);
      await until(() => deleting, { what: "delete request held in flight" });
      const stayedErased = await page.evaluate(async ({ paths, count }) => {
        const end = performance.now() + 300;
        do {
          await new Promise(requestAnimationFrame);
          if (document.querySelectorAll(paths).length !== count) return false;
        } while (performance.now() < end);
        return true;
      }, { paths, count });
      assert(stayedErased, "saved strokes must not reappear while deletion is pending");
    } finally { release(); }
    await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return !d.block.children.some((b) => b.id === group.id);
    }, { what: "empty group deleted" });
    await page.unroute(url);
    assertEq(await page.locator(paths).count(), count);
    assertNoProblems(page);
  });

  await step("ink: Pencil touch gestures are claimed and finger gestures remain available", async () => {
    // Synthetic Safari event ordering exercises the handlers; a physical
    // iPad is still needed to verify native Pencil/scroll arbitration.
    await page.keyboard.press("Escape");
    if (await page.locator(".pdfInkBar").count()) await page.keyboard.press("Escape");
    const paths = '[data-page="1"] .inkLayer path';
    const count = await page.locator(paths).count();
    const result = await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const pointer = (type, pointerType, pointerId, x) => el.dispatchEvent(new PointerEvent(type, {
        bubbles: true, cancelable: true, pointerType, pointerId, button: 0,
        buttons: type === "pointerup" ? 0 : 1, pressure: 0.7,
        clientX: box.left + x, clientY: box.top + 350,
      }));
      const touch = (type, touchType) => {
        const event = new Event(type, { bubbles: true, cancelable: true });
        const contact = { identifier: 1, touchType, clientX: box.left + 100, clientY: box.top + 350 };
        Object.defineProperties(event, {
          changedTouches: { value: [contact] }, touches: { value: [contact] },
        });
        el.dispatchEvent(event);
        return event.defaultPrevented;
      };
      const fingerBefore = touch("touchstart", "direct");
      const pencilBefore = touch("touchstart", "stylus");
      pointer("pointerdown", "pen", 71, 100);
      const pencilStart = touch("touchstart", "stylus");
      const pencilMove = touch("touchmove", "stylus");
      const untypedPencil = touch("touchmove", undefined);
      const fingerDuring = touch("touchstart", "direct");
      pointer("pointerdown", "touch", 72, 120);
      pointer("pointerup", "touch", 72, 120);
      pointer("pointermove", "pen", 71, 220);
      pointer("pointerup", "pen", 71, 220);
      const fingerAfter = touch("touchstart", "direct");
      return { fingerBefore, pencilBefore, pencilStart, pencilMove, untypedPencil, fingerDuring, fingerAfter };
    });
    assert(result.pencilBefore && result.pencilStart && result.pencilMove && result.untypedPencil,
      "Pencil touch events prevent native panning, including before pointerdown");
    assert(!result.fingerBefore && result.fingerDuring && !result.fingerAfter,
      "palms are blocked during writing; fingers navigate before and after");
    await until(async () => await page.locator(paths).count() === count + 1, { what: "pen stroke survived a second contact" });
    assertNoProblems(page);
  });

  await step("ink: coalesced pen samples, prediction preview, lift endpoint and pressure persist accurately", async () => {
    const visibleCount = await page.locator('[data-page="1"] .inkLayer path').count();
    await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.reduce((n, b) => n + (b.properties?.ink_strokes || 0), 0) === visibleCount;
    }, { what: "previous Pencil stroke persisted before starting a separate group" });
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("p");
    const before = await account.api(`/api/blocks/${pageId}/subtree`);
    const ids = new Set(before.block.children.map((b) => b.id));
    const result = await page.locator('[data-page="1"]').evaluate(async (el) => {
      const box = el.getBoundingClientRect();
      const make = (type, x, time, pressure) => {
        const ev = new PointerEvent(type, { bubbles: true, cancelable: true,
          pointerType: "pen", pointerId: 81, button: 0, buttons: type === "pointerup" ? 0 : 1,
          clientX: box.left + x, clientY: box.top + 300, pressure });
        Object.defineProperty(ev, "timeStamp", { value: 1000 + time });
        return ev;
      };
      el.dispatchEvent(make("pointerdown", 100, 0, 0.2));
      const move = make("pointermove", 130, 12, 0.8);
      Object.defineProperties(move, {
        getCoalescedEvents: { value: () => [make("pointermove", 110, 4, 0.4),
          make("pointermove", 120, 8, 0.6), make("pointermove", 130, 12, 0.8)] },
        getPredictedEvents: { value: () => [make("pointermove", 138, 20, 0.8)] },
      });
      el.dispatchEvent(move);
      await new Promise(requestAnimationFrame);
      const canvas = el.querySelector(".inkCanvas");
      const painted = canvas.style.display === "block" && canvas.getContext("2d")
        .getImageData(0, 0, canvas.width, canvas.height).data.some((v, i) => i % 4 === 3 && v > 0);
      el.dispatchEvent(make("pointerup", 140, 24, 0));
      return { painted, hidden: canvas.style.display === "none",
        k: box.width / el.querySelector(".inkLayer").viewBox.baseVal.width };
    });
    assert(result.painted && result.hidden, "live canvas paints before lift and hands off to SVG");
    const block = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.find((b) => !ids.has(b.id) && b.properties?.ink_url);
    }, { what: "sampled pen stroke saved" });
    const ink = await account.api(block.properties.ink_url);
    const st = ink.strokes[0];
    assertEq(ink.version, 2);
    assertEq(st.ch, "xyptw");
    assertEq(st.pts.length, 25, "five real samples, no prediction saved");
    assertEq(JSON.stringify(st.pts.filter((_, i) => i % 5 === 2)), "[200,400,600,800,800]", "pressure including lift");
    assertEq(JSON.stringify(st.pts.filter((_, i) => i % 5 === 3)), "[0,4,4,4,12]", "hardware time deltas");
    assertEq(JSON.stringify(st.pts.filter((_, i) => i % 5 === 4)), "[700,200,200,200,0]", "the baked width, 1 + (p - 0.5), as deltas");
    const endpoint = st.pts.filter((_, i) => i % 5 === 0).reduce((a, b) => a + b, 0) / 100;
    assert(Math.abs(endpoint - 140 / result.k) < 0.02, "the final pointer-up position is saved");
    await page.reload();
    await waitForPdf(page, 1);
    await page.waitForSelector(`[data-page="1"] [data-ink-id="${block.id}"] path`);
    assertNoProblems(page);
  });

  await step("ink: a pressure stroke is stored as version 2 with its baked width; a highlighter draws under the ink", async () => {
    // The file a pen stroke leaves (docs/dev/handwriting.md "The stroke file"):
    // version 2, the drawn width baked into the w channel on the client
    // (finishStroke.js bakeWidths: w = 1 + (p - 0.5) for the default pen),
    // one stride per sample. Then a highlighter drawn over that stroke
    // after a reload, in a group of its own, is painted before the pen's
    // group: band 0 under every group's ink (InkLayer.jsx).
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("p");
    const before = await account.api(`/api/blocks/${pageId}/subtree`);
    const ids = new Set(before.block.children.map((b) => b.id));
    await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const send = (type, x, time, pressure) => {
        const ev = new PointerEvent(type, { bubbles: true, cancelable: true,
          pointerType: "pen", pointerId: 82, button: 0, buttons: type === "pointerup" ? 0 : 1,
          clientX: box.left + x, clientY: box.top + 360, pressure });
        Object.defineProperty(ev, "timeStamp", { value: 2000 + time });
        el.dispatchEvent(ev);
      };
      send("pointerdown", 90, 0, 0.3);
      send("pointermove", 110, 8, 0.5);
      send("pointermove", 130, 16, 0.7);
      send("pointerup", 150, 24, 0);
    });
    const penBlock = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.find((b) => !ids.has(b.id) && b.properties?.ink_url);
    }, { what: "pressure stroke saved" });
    await page.reload();
    await waitForPdf(page, 1);
    await page.waitForSelector(`[data-page="1"] [data-ink-id="${penBlock.id}"] path`);
    const ink = await account.api(penBlock.properties.ink_url);
    assertEq(ink.format, "gamma-ink");
    assertEq(ink.version, 2, "the client writes version 2");
    const st = ink.strokes[0];
    assert(/^xy[a-z]*$/.test(st.ch) && st.ch.includes("w"), `the baked width rides in ch (${st.ch})`);
    assertEq(st.pts.length % st.ch.length, 0, "whole strides");
    assertEq(st.pts.length / st.ch.length, 4, "one sample per pointer event, the lift included");
    const p0 = st.pts[st.ch.indexOf("p")] / 1000, w0 = st.pts[st.ch.indexOf("w")] / 1000;
    assertEq(p0, 0.3, "the first sample's pressure");
    assert(Math.abs(w0 - (1 + (p0 - 0.5))) < 0.0005, `the first sample's width multiplier is 1 + (p - 0.5) (${w0})`);
    assertEq(st.size * w0, st.size * 0.8, "the drawn width is size × w, no formula at render time");

    // a highlighter across the pen stroke, after the reload: its own group
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("h");
    assert((await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label")).includes("highlighter"), "a highlighter preset is armed");
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 80, box.y + 360], [box.x + 160, box.y + 360]);
    const hlBlock = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.find((b) => !ids.has(b.id) && b.id !== penBlock.id && b.properties?.ink_url);
    }, { what: "highlighter stroke saved in a group of its own" });
    const hlInk = await account.api(hlBlock.properties.ink_url);
    assertEq(hlInk.strokes[0].tool, "highlighter");
    assert(!hlInk.strokes[0].ch.includes("w") && hlInk.strokes[0].band === undefined, "a highlighter carries no width channel and its band is the tool's default");
    const order = await page.locator('[data-page="1"] .inkLayer').evaluate((svg, [penId, hlId]) => {
      const groups = [...svg.querySelectorAll("g[data-ink-id]")];
      const hl = groups.findIndex((g) => g.dataset.inkId === hlId);
      const pen = groups.findIndex((g) => g.dataset.inkId === penId);
      const hlPath = svg.querySelector(`g[data-ink-id="${hlId}"] path`);
      const penPath = svg.querySelector(`g[data-ink-id="${penId}"] path`);
      return { hl, pen, multiply: getComputedStyle(hlPath).mixBlendMode,
        pathOrder: !!(hlPath.compareDocumentPosition(penPath) & Node.DOCUMENT_POSITION_FOLLOWING) };
    }, [penBlock.id, hlBlock.id]);
    assert(order.hl >= 0 && order.pen >= 0 && order.hl < order.pen, `the highlighter's group is painted before the pen's (${order.hl} < ${order.pen})`);
    assert(order.pathOrder, "the highlighter path precedes the pen path");
    assertEq(order.multiply, "multiply", "the highlighter multiplies onto the page");
    assertNoProblems(page);
  });

  await step("ink: interrupted strokes clean up and a pen can take over a palm contact", async () => {
    // Enable finger writing to exercise the harder palm-first ordering.
    await page.evaluate(() => localStorage.setItem("gamma-ink-pen-only", "0"));
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    const paths = '[data-page="1"] .inkLayer path';
    const count = await page.locator(paths).count();
    const clean = await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const send = (type, id, pointerType = "pen", x = 100) => el.dispatchEvent(new PointerEvent(type, {
        bubbles: true, cancelable: true, pointerType, pointerId: id, button: 0,
        buttons: type === "pointerup" ? 0 : 1, pressure: 0.6,
        clientX: box.left + x, clientY: box.top + 330,
      }));
      send("pointerdown", 90);
      send("pointermove", 90, "pen", 150);
      send("pointercancel", 90);
      const cancelled = el.querySelector(".inkCanvas").style.display === "none";
      send("pointerdown", 91);
      send("lostpointercapture", 91);
      const lost = el.querySelector(".inkCanvas").style.display === "none";
      send("pointerdown", 92, "touch");
      send("pointermove", 92, "touch", 150);
      send("pointerdown", 93);
      send("pointerup", 92, "touch", 160);
      send("pointermove", 93, "pen", 180);
      send("pointerup", 93, "pen", 190);
      return cancelled && lost;
    });
    assert(clean, "cancellation and lost capture clear the live preview");
    await until(async () => await page.locator(paths).count() === count + 1,
      { what: "only the pen stroke survives palm takeover and interruptions" });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-touch-writing.png` });
    assertNoProblems(page);
  });

  await step("ink: native Chromium touch writes without scrolling and native pen pressure is saved", async () => {
    const paths = '[data-page="1"] .inkLayer path';
    const count = await page.locator(paths).count();
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    box = await page.locator('[data-page="1"]').boundingBox();
    const x = Math.round(box.x + 100), y = Math.round(box.y + 220);
    const scrollBefore = await page.locator(".pdfViewer").evaluate((el) => el.scrollTop);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    for (let i = 1; i <= 12; i++) {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x + i * 8, y: y + i * 3 }] });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await until(async () => await page.locator(paths).count() === count + 1, { what: "native touch stroke" });
    assertEq(await page.locator(".pdfViewer").evaluate((el) => el.scrollTop), scrollBefore, "finger drawing does not pan");
    await page.getByRole("button", { name: "Hand", exact: true }).click();
    await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y: y + 80,
      pointerType: "pen", button: "left", buttons: 1, clickCount: 1, force: 0.2 });
    for (let i = 1; i <= 12; i++) {
      await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x + i * 8, y: y + 80 + Math.sin(i / 2) * 15,
        pointerType: "pen", button: "left", buttons: 1, force: 0.2 + i * 0.05 });
    }
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x + 100, y: y + 76,
      pointerType: "pen", button: "left", buttons: 0, clickCount: 1 });
    await until(async () => await page.locator(paths).count() === count + 2, { what: "native pen stroke in Hand mode" });
    await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      for (const b of d.block.children) {
        if (!b.properties?.ink_url) continue;
        const ink = await account.api(b.properties.ink_url);
        if (ink.strokes.some((s) => s.pen && s.ch === "xyptw" && s.pts.length >= 60 && s.pts[2] === 200
          && s.pts.filter((_, i) => i % 5 === 2).includes(800))) return true;
      }
      return false;
    }, { what: "native pen pressure uploaded" });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-native-touch-pen.png` });
    await page.evaluate(() => localStorage.setItem("gamma-ink-pen-only", "1"));
    await page.reload();
    await waitForPdf(page, 1);
    await until(async () => await page.locator(paths).count() === count + 2, { what: "native strokes restored" });
    await page.click("button[aria-label='Handwriting tools']");
    const scrollStart = await page.locator(".pdfViewer").evaluate((el) => el.scrollTop);
    box = await page.locator('[data-page="1"]').boundingBox();
    const fingerX = Math.round(box.x + 300);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: fingerX, y: 500 }] });
    for (let i = 1; i <= 10; i++) {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: fingerX, y: 500 - i * 12 }] });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await until(async () => await page.locator(".pdfViewer").evaluate((el) => el.scrollTop) > scrollStart + 20,
      { what: "fingers scroll with a pen armed in pen-only mode" });
    assertEq(await page.locator(paths).count(), count + 2, "finger navigation adds no ink");
    await cdp.detach();
    assertNoProblems(page);
  });

  await step("ink: two tabs changing one group at once keep both changes (the save merges by stroke)", async () => {
    // Tab A draws two strokes into a fresh group; tab B erases one of them
    // while A draws a third. B's save is held until A's has landed, so it
    // arrives with a base the server has moved past: the server merges
    // (ops.py merge_ink) and both tabs end with A's new stroke and without
    // the erased one.
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("p");
    const paths = '[data-page="1"] .inkLayer path';
    const known = new Set((await account.api(`/api/blocks/${pageId}/subtree`)).block.children.map((b) => b.id));
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 600], [box.x + 250, box.y + 600]);
    await drawLine(page, [box.x + 100, box.y + 650], [box.x + 250, box.y + 650]);
    const group = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      const g = d.block.children.find((b) => !known.has(b.id) && b.properties?.ink_url);
      return g && g.properties.ink_strokes === 2 ? g : null;
    }, { what: "a new group with two strokes saved" });
    const firstY = (ink) => Math.min(...ink.strokes.map((st) => st.pts[1] / 100));
    const erasedY = firstY(await account.api(group.properties.ink_url)); // the upper stroke, which B erases
    const ctx2 = await account.context(browser);
    try {
      const other = await openPage(ctx2, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      await waitForPdf(other, 1);
      const countA = await page.locator(paths).count();
      await until(async () => await other.locator(paths).count() === countA, { what: "tab B shows the group" });
      let release;
      const held = new Promise((resolve) => { release = resolve; });
      let holding = false;
      const url = `**/api/blocks/${group.id}`;
      await other.route(url, async (route) => {
        if (route.request().method() === "PUT") { holding = true; await held; }
        await route.continue();
      });
      await other.click("button[aria-label='Handwriting tools']");
      await other.waitForSelector(".pdfInkBar");
      await other.keyboard.press("e");
      const b2 = await other.locator('[data-page="1"]').boundingBox();
      await drawLine(other, [b2.x + 175, b2.y + 590], [b2.x + 175, b2.y + 610]); // through the first stroke
      await until(async () => await other.locator(paths).count() === countA - 1, { what: "tab B erased a stroke" });
      await until(() => holding, { what: "tab B's save held in flight" });
      await drawLine(page, [box.x + 100, box.y + 700], [box.x + 250, box.y + 700]); // A's third stroke joins its group
      await until(async () => (await account.api(`/api/blocks/${group.id}`)).properties.ink_strokes === 3,
        { what: "tab A's save landed first" });
      release();
      await until(async () => {
        const block = await account.api(`/api/blocks/${group.id}`);
        if (block.properties.ink_strokes !== 2) return false;
        return firstY(await account.api(block.properties.ink_url)) > erasedY + 5;
      }, { what: "the merge kept A's new stroke and dropped the erased one" });
      await until(async () => await page.locator(paths).count() === countA, { what: "tab A shows the merge" });
      await until(async () => await other.locator(paths).count() === countA, { what: "tab B shows the merge" });
      await other.unroute(url);
      assertNoProblems(other);
    } finally { await ctx2.close(); }
    assertNoProblems(page);
  });

  // --- milestone 1: the pen feel (docs/dev/handwriting.md "What the user sees") ---
  const pathCount = () => page.locator('[data-page="1"] .inkLayer path').count();
  const pillText = async () => (await page.locator(".statusPill").allInnerTexts().catch(() => [])).join(" | ");
  // The strokes saved on the page, by id, over every group (a stroke joins
  // the current group, so a new one is found by its id, not its block).
  const savedStrokes = async () => {
    const d = await account.api(`/api/blocks/${pageId}/subtree`);
    const out = new Map();
    for (const b of d.block.children) {
      if (!b.properties?.ink_url?.endsWith(".ink")) continue;
      for (const st of (await account.api(b.properties.ink_url)).strokes) out.set(st.id, st);
    }
    return out;
  };
  const newStrokes = (before, n, what) => until(async () => {
    const fresh = [...(await savedStrokes()).values()].filter((st) => !before.has(st.id));
    return fresh.length >= n ? fresh : null;
  }, { what });
  // The armed preset's options row: a tap on the armed tool opens it.
  const openOptions = async () => {
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();
    await page.waitForSelector(".pdfInkSub");
  };
  const armedLabel = () => page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label");
  // The strip, open (the pen button toggles it, so only when it is closed).
  const openStrip = async () => {
    if (!(await page.locator(".pdfInkBar").count())) await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
  };
  // Settings → Reading & editing, its rows, and back.
  const openReadingSettings = async () => {
    await page.locator('[data-guide="header.account"]').click();
    await page.getByRole("button", { name: "Settings…", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
    await dialog.waitFor();
    await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Reading & editing", exact: true }).click();
    return dialog;
  };
  const settingRow = (name) => page.locator(`.settingsPane [data-setting="${name}"]`).first();
  const closeSettings = async (dialog) => {
    await page.getByRole("button", { name: "Close settings", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
  };
  // A pen stroke of synthetic pointer events on their own clock: points
  // [x, y, ms] from the page's corner, the lift at the last point. The
  // scribble and the smoothing read the samples' timing and spacing.
  const penStroke = (points) => page.locator('[data-page="1"]').evaluate((el, points) => {
    const box = el.getBoundingClientRect();
    const send = (type, x, y, time) => {
      const ev = new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 95, isPrimary: true,
        button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + x, clientY: box.top + y, pressure: type === "pointerup" ? 0 : 0.5 });
      Object.defineProperty(ev, "timeStamp", { value: 9000 + time });
      el.dispatchEvent(ev);
    };
    points.forEach(([x, y, time], i) => send(i ? "pointermove" : "pointerdown", x, y, time));
    const [x, y, time] = points[points.length - 1];
    send("pointerup", x, y, time + 8);
  }, points);
  // A saved stroke's spread across the line, over its middle (the start and
  // the lift are the pen's own points), in page points.
  const spread = (st) => {
    const a = decodeStroke(st);
    const ys = a.filter((q) => Math.min(Math.abs(q.x - a[0].x), Math.abs(q.x - a.at(-1).x)) > 30).map((q) => q.y);
    return Math.max(...ys) - Math.min(...ys);
  };

  await step("ink: Settings → Reading & editing has the account's Handwriting rows and this device's, and Undo with fingers is set to Double tap", async () => {
    const dialog = await openReadingSettings();
    const row = settingRow;
    for (const name of ["Handwriting", "Reset presets", "Eraser returns to the last tool", "Erases", "Draw and hold", "Scribble to erase",
      "This device", "Draws with", "Stylus draws right away", "Pressure-sensitive strokes", "Undo with fingers"]) {
      assert(await row(name).count(), `the row "${name}" is in Reading & editing`);
    }
    const scope = (title) => page.locator(`.settingsPane .setSection[data-setting="${title}"] .setScope`).getAttribute("data-scope");
    assertEq(await scope("Handwriting"), "account", "Handwriting follows the account");
    assertEq(await scope("This device"), "browser", "This device stays with the browser");
    assertEq(await row("Draw and hold").locator("input[type=checkbox]").isChecked(), true, "draw and hold is on by default");
    assertEq(await row("Scribble to erase").locator("input[type=checkbox]").isChecked(), false, "scribble to erase is off by default");
    assertEq(await row("Erases").getByRole("button", { name: "All", exact: true }).getAttribute("aria-pressed"), "true", "erases everything by default");
    // a mouse's browser: finger undo is off until asked for
    assertEq(await row("Undo with fingers").getByRole("button", { name: "Off", exact: true }).getAttribute("aria-pressed"), "true");
    await row("Undo with fingers").getByRole("button", { name: "Double tap", exact: true }).click();
    assertEq(await row("Undo with fingers").getByRole("button", { name: "Double tap", exact: true }).getAttribute("aria-pressed"), "true");
    assertEq(await page.evaluate(() => localStorage.getItem("gamma-ink-finger-undo")), "double", "a browser preference");
    // the search knows every row by its section
    await page.getByRole("searchbox", { name: "Search settings" }).fill("fingers undo");
    await page.locator(".settingsSearchResult").filter({ has: page.getByText("Undo with fingers", { exact: true }) }).first().waitFor();
    await closeSettings(dialog);
    assertNoProblems(page);
  });

  await step("ink: a pen held still at the end of a stroke snaps it to a straight line (pen: false, shape line) with Undo on the pill", async () => {
    await openStrip();
    await page.keyboard.press("1");
    const saved = await savedStrokes();
    const before = await pathCount();
    await page.locator('[data-page="1"]').evaluate(async (el) => {
      const box = el.getBoundingClientRect();
      const send = (type, x, y, time) => {
        const ev = new PointerEvent(type, { bubbles: true, cancelable: true,
          pointerType: "pen", pointerId: 91, button: 0, buttons: type === "pointerup" ? 0 : 1,
          clientX: box.left + x, clientY: box.top + y, pressure: type === "pointerup" ? 0 : 0.5 });
        Object.defineProperty(ev, "timeStamp", { value: 5000 + time });
        el.dispatchEvent(ev);
      };
      // a slightly wobbly 150 px line drawn over 300 ms, then the tip stays still
      send("pointerdown", 100, 450, 0);
      for (let i = 1; i <= 10; i++) send("pointermove", 100 + i * 15, 450 + (i % 2 ? 1.2 : -1.2), i * 30);
      await new Promise((r) => setTimeout(r, 900)); // past the 600 ms hold
      send("pointerup", 250, 450, 1200);
    });
    await until(async () => (await pathCount()) === before + 1, { what: "the snapped stroke drawn" });
    const [st] = await newStrokes(saved, 1, "the held stroke saved");
    assertEq(st.pen, false, "an even line");
    assertEq(st.ch, "xyt", "no pressure channel on a snapped stroke");
    assertEq(st.shape?.kind, "line", `the shape record (${JSON.stringify(st.shape)})`);
    assertEq(st.pts.length, 6, "two samples");
    assert(Math.abs(st.pts[4]) <= 1, `a line within 5° of horizontal is snapped level (dy ${st.pts[4]})`);
    await until(async () => (await pillText()).includes("Straightened."), { what: "the Straightened pill" });
    assert(await page.locator(".statusPill").getByRole("button", { name: "Undo", exact: true }).count(), "Undo is on the pill");
    await page.locator(".statusPill").getByRole("button", { name: "Undo", exact: true }).click();
    await until(async () => (await pathCount()) === before, { what: "the snap undone from the pill" });
    assertNoProblems(page);
  });

  await step("ink: a highlighter preset set to Straight: Always flattens a wavy highlight to its chord", async () => {
    await page.keyboard.press("h");
    await openOptions();
    const row = page.locator(".pdfInkSub");
    assertEq(await row.getByRole("button", { name: "Hold", exact: true }).getAttribute("aria-pressed"), "true", "Hold is the default");
    assertEq(await row.locator(".inkSmoothDot").count(), 5, "the Smoothing ladder: Off and four levels");
    await row.getByRole("button", { name: "Always", exact: true }).click();
    assertEq(await row.getByRole("button", { name: "Always", exact: true }).getAttribute("aria-pressed"), "true");
    assert(JSON.parse(await page.evaluate(() => localStorage.getItem("gamma-ink-tools"))).some((t) => t.straight === "always"), "the preset keeps it");
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click(); // closes the row
    const saved = await savedStrokes();
    const before = await pathCount();
    box = await page.locator('[data-page="1"]').boundingBox();
    await page.mouse.move(box.x + 100, box.y + 500);
    await page.mouse.down();
    for (let i = 1; i <= 20; i++) await page.mouse.move(box.x + 100 + i * 10, box.y + 500 + (i % 2 ? 3 : -3));
    await page.mouse.up();
    await until(async () => (await pathCount()) === before + 1, { what: "the highlight drawn" });
    const [st] = await newStrokes(saved, 1, "the straight highlight saved");
    assertEq(st.tool, "highlighter");
    assertEq(st.pen, false);
    assertEq(st.shape?.kind, "line", `the chord carries a line record (${JSON.stringify(st.shape)})`);
    assertEq(st.pts.length, 6, "two samples, xyt");
    assertEq(st.pts[4], 0, "snapped horizontal");
    await until(async () => (await pillText()).includes("Straightened."), { what: "the first Always straightening says so" });
    // back to Hold, so the presets are as they were
    await openOptions();
    await page.locator(".pdfInkSub").getByRole("button", { name: "Hold", exact: true }).click();
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();
    assertNoProblems(page);
  });

  await step("ink: the eraser set to Highlighters leaves the pen stroke under its pass intact; the hover ring carries the kind", async () => {
    const saved = await savedStrokes();
    const before = await pathCount();
    box = await page.locator('[data-page="1"]').boundingBox();
    await page.keyboard.press("1");
    await drawLine(page, [box.x + 100, box.y + 560], [box.x + 260, box.y + 560]);
    await page.keyboard.press("h");
    await drawLine(page, [box.x + 100, box.y + 560], [box.x + 260, box.y + 560]);
    await until(async () => (await pathCount()) === before + 2, { what: "a pen stroke and a highlight over it" });
    const drawn = await newStrokes(saved, 2, "both strokes saved");
    const penStroke = drawn.find((st) => st.tool === "pen"), highlight = drawn.find((st) => st.tool === "highlighter");
    assert(penStroke && highlight, "a pen stroke and a highlight");
    await page.keyboard.press("e");
    await openOptions();
    const row = page.locator(".pdfInkSub");
    assertEq(await row.getByRole("button", { name: "All", exact: true }).getAttribute("aria-pressed"), "true");
    await row.getByRole("button", { name: "Highlighters", exact: true }).click();
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkEraserFilter === "highlighter", { what: "the filter is the account's" });
    await page.mouse.move(box.x + 180, box.y + 540);
    await until(() => page.locator('[data-page="1"] .inkCursor[data-filter="highlighter"]').count(), { what: "the hover ring shows the kind" });
    await drawLine(page, [box.x + 180, box.y + 540], [box.x + 182, box.y + 580]);
    await until(async () => (await pathCount()) === before + 1, { what: "the highlight gone" });
    await until(async () => !(await savedStrokes()).has(highlight.id), { what: "the highlight gone from the file" });
    assert((await savedStrokes()).has(penStroke.id), "the pen stroke under the pass stays");
    await row.getByRole("button", { name: "All", exact: true }).click();
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkEraserFilter === "all", { what: "back to everything" });
    await page.mouse.move(box.x + 300, box.y + 540);
    await page.mouse.move(box.x + 302, box.y + 542);
    await until(async () => (await page.locator('[data-page="1"] .inkCursor').count()) === 1
      && (await page.locator('[data-page="1"] .inkCursor[data-filter]').count()) === 0, { what: "no glyph without a filter" });
    assertNoProblems(page);
  });

  await step("ink: with Back to last tool on, lifting the eraser re-arms the preset used before it, its row closed", async () => {
    await page.keyboard.press("2");
    assert((await armedLabel()).includes("key 2"), "preset 2 armed");
    await page.keyboard.press("e");
    await openOptions();
    const toggle = page.locator(".pdfInkSub").getByRole("button", { name: "Back to last tool", exact: true });
    assertEq(await toggle.getAttribute("aria-pressed"), "false", "off by default");
    await toggle.click();
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkEraserReturn === true, { what: "the return is the account's" });
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 400, box.y + 700], [box.x + 420, box.y + 720]); // over nothing: the return does not depend on erasing
    await until(async () => (await armedLabel() || "").includes("key 2"), { what: "preset 2 armed again after the eraser lifted" });
    assertEq(await page.locator(".pdfInkSub").count(), 0, "the options row is closed");
    await page.keyboard.press("e");
    await openOptions();
    await page.locator(".pdfInkSub").getByRole("button", { name: "Back to last tool", exact: true }).click();
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkEraserReturn === false, { what: "turned off again" });
    await page.keyboard.press("1");
    assertNoProblems(page);
  });

  await step("ink: a two-finger double tap undoes the last stroke and says so once, with Turn off", async () => {
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 620], [box.x + 240, box.y + 640]);
    const before = await pathCount();
    const chord = (ids, x, y) => page.locator('[data-page="1"]').evaluate(async (el, [ids, x, y]) => {
      const box = el.getBoundingClientRect();
      const send = (type, id, dx) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
        pointerType: "touch", pointerId: id, isPrimary: id === ids[0], button: 0, buttons: type === "pointerup" ? 0 : 1,
        clientX: box.left + x + dx, clientY: box.top + y }));
      ids.forEach((id, i) => send("pointerdown", id, i * 40));
      await new Promise((r) => setTimeout(r, 40));
      ids.forEach((id, i) => send("pointerup", id, i * 40));
    }, [ids, x, y]);
    await chord([201, 202], 300, 300);
    await new Promise((r) => setTimeout(r, 120));
    await chord([203, 204], 300, 300);
    await until(async () => (await pathCount()) === before - 1, { what: "the stroke undone by the chord" });
    await until(async () => (await pillText()).includes("Two fingers undid a stroke."), { what: "the first chord explains itself" });
    await page.locator(".statusPill").getByRole("button", { name: "Turn off", exact: true }).click();
    await until(async () => (await page.evaluate(() => localStorage.getItem("gamma-ink-finger-undo"))) === "off", { what: "Turn off sets the preference" });
    await page.getByRole("button", { name: "Redo ink", exact: true }).click();
    await until(async () => (await pathCount()) === before, { what: "redone from the strip" });
    await chord([205, 206], 300, 300);
    await new Promise((r) => setTimeout(r, 120));
    await chord([207, 208], 300, 300);
    await sleep(400);
    assertEq(await pathCount(), before, "off: a chord undoes nothing");
    assertNoProblems(page);
  });

  await step("ink: with Scribble to erase on, a fast back-and-forth with the pen erases the stroke under it as one undo step, and nothing is drawn", async () => {
    let dialog = await openReadingSettings();
    await settingRow("Scribble to erase").locator("input[type=checkbox]").check();
    await until(async () => (await pillText()).includes("Scribble to erase is on"), { what: "turning it on explains the gesture" });
    await closeSettings(dialog);
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkScribbleErase === true, { what: "the account's" });
    await page.keyboard.press("1");
    box = await page.locator('[data-page="1"]').boundingBox();
    const saved = await savedStrokes();
    const before = await pathCount();
    await drawLine(page, [box.x + 100, box.y + 450], [box.x + 160, box.y + 450]);
    await until(async () => (await pathCount()) === before + 1, { what: "a pen stroke to scratch out" });
    const [victim] = await newStrokes(saved, 1, "the stroke saved");
    // five passes over it, 70 px wide and 360 ms long: four sharp reversals, fast and dense
    const scribble = [];
    for (let pass = 0; pass < 5; pass++) for (let i = 0; i < 8; i++) {
      const u = i / 8;
      scribble.push([pass % 2 ? 165 - 70 * u : 95 + 70 * u, 450 + (pass % 2 ? 4 : -4) + (i % 2 ? 1 : -1), (pass * 8 + i) * 9]);
    }
    scribble.push([165, 454, 40 * 9]);
    await penStroke(scribble);
    await until(async () => (await pathCount()) === before, { what: "the stroke gone and the scribble not drawn" });
    await until(async () => !(await savedStrokes()).has(victim.id), { what: "the stroke gone from the file" });
    assertEq((await savedStrokes()).size, saved.size, "the scribble itself is not saved");
    await until(async () => (await pillText()).includes("Erased 1 stroke."), { what: "the pill counts the erasure" });
    await page.locator(".statusPill").getByRole("button", { name: "Undo", exact: true }).click();
    await until(async () => (await pathCount()) === before + 1, { what: "the stroke back from the pill's Undo" });
    await until(async () => (await savedStrokes()).has(victim.id), { what: "and back in the file" });
    dialog = await openReadingSettings();
    await settingRow("Scribble to erase").locator("input[type=checkbox]").uncheck();
    await closeSettings(dialog);
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkScribbleErase === false, { what: "off again" });
    assertNoProblems(page);
  });

  await step("ink: Smoothing 4 on a preset draws a shaky pen's zigzag as a calm line, where Smoothing off copies it", async () => {
    await page.keyboard.press("1");
    const zigzag = (y) => Array.from({ length: 61 }, (_, i) => [100 + i * 4, y + (i % 2 ? 5 : -5), i * 12]);
    let saved = await savedStrokes();
    let before = await pathCount();
    await penStroke(zigzag(400));
    await until(async () => (await pathCount()) === before + 1, { what: "the zigzag drawn with Smoothing off" });
    const [raw] = await newStrokes(saved, 1, "the raw zigzag saved");
    await openOptions();
    const row = page.locator(".pdfInkSub");
    assertEq(await row.locator(".inkSmoothing button").count(), 5, "Off and four levels");
    assertEq(await row.getByRole("button", { name: "Smoothing off", exact: true }).getAttribute("aria-pressed"), "true", "off by default");
    await row.getByRole("button", { name: "Smoothing 4 of 4", exact: true }).click();
    assertEq(await row.getByRole("button", { name: "Smoothing 4 of 4", exact: true }).getAttribute("aria-pressed"), "true");
    assertEq(JSON.parse(await page.evaluate(() => localStorage.getItem("gamma-ink-tools"))).find((t) => t.stabilize)?.stabilize, 4, "the preset keeps the level");
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click(); // closes the row
    saved = await savedStrokes();
    before = await pathCount();
    await penStroke(zigzag(420));
    await until(async () => (await pathCount()) === before + 1, { what: "the zigzag drawn with Smoothing 4" });
    const [smooth] = await newStrokes(saved, 1, "the smoothed stroke saved");
    assert(spread(raw) > 5, `off: the line copies the shake (spread ${spread(raw).toFixed(2)} pt)`);
    assert(spread(smooth) < 0.4 * spread(raw), `Smoothing 4: a calm line (spread ${spread(smooth).toFixed(2)} of ${spread(raw).toFixed(2)} pt)`);
    // both strokes start at the pen's own point (the first sample passes through), 20 px apart; the
    // smoothed one must end at the pen's lift, the same 20 px under the raw one's
    const first = (st) => decodeStroke(st)[0], end = (st) => decodeStroke(st).at(-1);
    const dy = first(smooth).y - first(raw).y;
    assert(Math.abs(end(smooth).x - end(raw).x) < 1 && Math.abs(end(smooth).y - end(raw).y - dy) < 1,
      `the smoothed line still reaches the lift point (ends ${JSON.stringify(end(smooth))} vs ${JSON.stringify(end(raw))}, starts ${dy.toFixed(2)} apart)`);
    await openOptions();
    await page.locator(".pdfInkSub").getByRole("button", { name: "Smoothing off", exact: true }).click();
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();
    assertNoProblems(page);
  });

  await step("ink: Transcribe with AI asks the chat about the group, with the group attached", async () => {
    const up = await account.api("/api/upload-ink", { method: "POST", body: {
      format: "gamma-ink", version: 2, space: { kind: "pdf-page", page: 1, width: 612, height: 792 },
      strokes: [{ id: "tr1", color: "#1f1f1f", size: 2, ch: "xy", pts: [10000, 60000, 3000, 400] }],
    } });
    const group = await account.api("/api/blocks", { method: "POST", body: {
      parent_id: pageId, content: "", properties: { ink_url: up.url, pdf_position: up.pdf_position, ink_strokes: 1 },
    } });
    const tctx = await account.context(browser);
    try {
      await fakeAiModels(tctx);
      await tctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const sent = [];
      await tctx.route("**/api/ai/chat", async (route) => {
        sent.push(route.request().postDataJSON());
        await route.fulfill({ contentType: "application/x-ndjson", body: '{"delta":"It says: hello."}\n' });
      });
      const tpage = await openPage(tctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      const wrap = tpage.locator(`.sortableBlockWrap[data-block-id="${group.id}"]`).first();
      await wrap.hover();
      await wrap.locator(".dragHandle").first().click();
      await tpage.locator(".ctxMenuItem", { hasText: "Transcribe with AI" }).click();
      await until(() => sent.length === 1, { what: "one chat request" });
      assertEq(sent[0].prompt, "Transcribe this handwriting into its caption.", "the request's text");
      assert(sent[0].context_blocks.includes(group.id), "the group rides with it as a chip");
      await tpage.locator(".chatBubble.ai", { hasText: "It says: hello." }).waitFor();
      await tpage.locator(".chatBubble.user", { hasText: "Transcribe this handwriting" }).waitFor();
      assertEq(sent.length, 1, "sent once");
      assertNoProblems(tpage);
    } finally { await tctx.close(); }
  });

  if (ctx) await ctx.close();
  return { inkPageId: pageId };
}
