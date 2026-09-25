// Note editing: page creation, the block editor's keys (Shift+Enter /
// Enter-as-new-block pref / Tab / Shift+Tab / Backspace), the op save path +
// reload, undo, the handle menu, markdown rendering (todos, images) and the
// workspace on browser-fetched upload URLs. Runs in a NON-default workspace:
// every request must name it explicitly, which is where plumbing bugs show.

export const PNG_1PX = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");

// The page's block tree as [{content, children}] from the API: what the
// server holds, i.e. what a reload would show.
export async function tree(account, pageId) {
  const data = await account.api(`/api/blocks/${pageId}/subtree`);
  const strip = (b) => ({ content: b.content, children: (b.children || []).map(strip) });
  return (data.block.children || []).map(strip);
}

export const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export function row(page, text) {
  return page.locator(".blockRow", { hasText: text }).first();
}

// Click a rendered block to open its editor, caret at the end.
export async function editRow(page, text) {
  await row(page, text).locator(".blockBody").click();
  await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
  await page.keyboard.press("End");
}

// Tab / Shift+Tab re-parent the block, which remounts its row and closes the
// editor; reopen it in place (same trick as the readme-media drivers).
export async function reopenFocused(page) {
  await page.waitForSelector(".blockRow.focused", { timeout: 5000 });
  await page.evaluate(() => {
    const r = document.querySelector(".blockRow.focused");
    const body = r.querySelector(".blockBody") || r;
    const b = body.getBoundingClientRect();
    r.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0, clientX: b.left + 40, clientY: b.top + b.height / 2 }));
  });
  await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
  await page.keyboard.press("End");
}

// Escape never closes the editor (it only dismisses popups); an in-page blur does.
export async function closeEditor(page) {
  await page.evaluate(() => document.activeElement?.blur());
  await page.waitForSelector(".blockEditorCm", { state: "detached", timeout: 5000 });
}

// One HTML5 drag, dispatched the way the browser would: dragstart on `from`
// (an element handle), then dragover on the row of the block `ontoText` near
// its wrap's top left (a sibling above it). `finish("drop")` drops on that row
// and fires dragend at the source; `finish("elsewhere")` only fires dragend at
// the body, a drag end the rows never see.
async function startDrag(page, from, ontoText) {
  const onto = await page.locator(".sortableBlockWrap", { hasText: ontoText }).first().elementHandle();
  await page.evaluate(([src, wrap]) => {
    const dt = new DataTransfer();
    window.__e2eDrag = { dt, src, row: wrap.querySelector(".blockRow") };
    src.dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: dt }));
    const r = wrap.getBoundingClientRect();
    window.__e2eDrag.row.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + 5, clientY: r.top + 4 }));
  }, [from, onto]);
  return {
    finish: (how) => page.evaluate((how) => {
      const { dt, src, row } = window.__e2eDrag;
      const fire = (el, type) => el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
      if (how === "drop") { fire(row, "drop"); fire(src, "dragend"); }
      else fire(document.body, "dragend");
    }, how),
  };
}

export async function newPageViaUi(page, title) {
  await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
  await page.click(".folderNewBtn");
  await page.waitForSelector(".titleEdit");
  await page.keyboard.type(title);
  await page.keyboard.press("Enter");
  await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
  return new URL(page.url()).searchParams.get("block");
}

export async function noteScenarios({ server, browser, alice, step, until, sleep, assert, assertEq, assertNoProblems, openPage }) {
  const second = await alice.api("/api/workspaces", { method: "POST", body: { name: "Second" } });
  const alice2 = Object.assign(Object.create(Object.getPrototypeOf(alice)), alice, { ws: second.id });
  let ctx, page, pageId;
  const saved = (want, what) => until(async () => same(await tree(alice2, pageId), want), { what: what || `tree ${JSON.stringify(want)}` });

  await step("notes: New page, title, first block, reload persists (non-default workspace)", async () => {
    ctx = await alice2.context(browser);
    page = await openPage(ctx, `${server.base}/?ws=${second.id}`);
    pageId = await newPageViaUi(page, "Reading list");
    assert(pageId, "URL names the page");
    await page.keyboard.type("first");
    await saved([{ content: "first", children: [] }], "the first block saved as an insert");
    assertNoProblems(page);
    await page.reload();
    await page.waitForSelector(".blockRow", { timeout: 15000 });
    await until(async () => (await page.textContent("body")).includes("first"), { what: "content after reload" });
    assert((await page.textContent("body")).includes("Reading list"), "title after reload");
    assertNoProblems(page);
  });

  await step("notes: Shift+Enter / Tab / Shift+Tab / Backspace shape the tree", async () => {
    await editRow(page, "first");
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.type("second");
    await page.keyboard.press("Tab");
    await reopenFocused(page);
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.type("third");
    await page.keyboard.press("Shift+Tab");
    await reopenFocused(page);
    await page.keyboard.press("Shift+Enter");
    // The new (empty) block's editor has the focus before Backspace removes it
    // (an empty CodeMirror doc shows its placeholder widget, so test for that).
    await page.waitForFunction(() => {
      const ed = document.activeElement?.closest(".cm-content");
      return !!ed && (ed.querySelector(".cm-placeholder") != null || ed.textContent === "");
    }, null, { timeout: 5000 });
    await page.keyboard.press("Backspace");
    await closeEditor(page);
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }]);
    assertNoProblems(page);
  });

  await step("notes: plain Enter is a line break; Ctrl+Z inside the editor restores the text", async () => {
    await editRow(page, "third");
    await page.keyboard.press("Enter");
    await page.keyboard.type("line two");
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes("third\\nline two"), { what: "line break saved" });
    await page.keyboard.press("Control+z");
    await page.getByText(/^Undone: note text edit:/).waitFor();
    await closeEditor(page);
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }], "undo saved");
    assertNoProblems(page);
  });

  await step("notes: the handle menu duplicates and deletes a block", async () => {
    const wrap = page.locator(".sortableBlockWrap", { hasText: "third" }).first();
    await wrap.hover();
    await wrap.locator(".dragHandle").click();
    await page.locator(".ctxMenuItem", { hasText: "Duplicate" }).click();
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }, { content: "third", children: [] }], "duplicate saved");
    const dup = page.locator(".sortableBlockWrap", { hasText: "third" }).nth(1);
    await dup.hover();
    await dup.locator(".dragHandle").click();
    await page.locator(".ctxMenuItem", { hasText: "Delete" }).click();
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }], "delete saved");
    assertNoProblems(page);
  });

  await step("notes: keyboard commands — Ctrl+Shift+Enter, Alt+↓, Ctrl+Shift+K, F2", async () => {
    await editRow(page, "third");
    await page.keyboard.press("Control+Shift+Enter");
    await page.keyboard.type("before");
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "before", children: [] }, { content: "third", children: [] }], "new block above");
    await page.keyboard.press("Alt+ArrowDown");
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }, { content: "before", children: [] }], "moved down");
    // The moved block keeps its editor and caret.
    await until(() => page.evaluate(() => document.activeElement?.closest(".cm-content")?.textContent === "before"), { what: "the moved block keeps its editor" });
    await page.keyboard.press("Control+Shift+k");
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }], "line deleted");
    await until(() => page.evaluate(() => document.activeElement?.closest(".cm-content")?.textContent === "third"), { what: "the caret moves to the block above" });
    await page.keyboard.press("Control+Enter");
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes("- [ ] third"), { what: "to-do toggled on" });
    await page.keyboard.press("Control+Enter");
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes("- [x] third"), { what: "to-do checked" });
    // Ctrl+L selects the block's text; typing over it restores the block.
    await page.keyboard.press("Control+l");
    await page.keyboard.type("third");
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }], "text restored over the selection");
    await closeEditor(page);
    await page.keyboard.press("F2");
    await page.locator(".titleEdit").waitFor();
    await page.keyboard.press("Escape");
    await until(async () => (await page.locator(".titleEdit").count()) === 0, { what: "rename cancelled" });
    assertNoProblems(page);
  });

  await step("notes: dragging a block's handle moves it; the drop line never outlives the drag (#88)", async () => {
    const handle = (text) => page.locator(".sortableBlockWrap", { hasText: text }).first().locator(".dragHandle").first().elementHandle();
    const line = page.locator(".dropIndicator");
    // A handle drag shows the line and the drop moves the block.
    let drag = await startDrag(page, await handle("third"), "first");
    await line.waitFor({ timeout: 5000 });
    await drag.finish("drop");
    await line.waitFor({ state: "detached", timeout: 5000 });
    await saved([{ content: "third", children: [] }, { content: "first", children: [{ content: "second", children: [] }] }], "moved above");
    // A drag whose end no row sees (a dragend elsewhere) still takes it away.
    drag = await startDrag(page, await handle("first"), "third");
    await line.waitFor({ timeout: 5000 });
    await drag.finish("elsewhere");
    await line.waitFor({ state: "detached", timeout: 5000 });
    // Anything else dragged over the notes (rendered text here) shows none.
    drag = await startDrag(page, await row(page, "third").locator(".blockRendered").first().elementHandle(), "first");
    await sleep(300);
    assertEq(await line.count(), 0, "no drop line for a non-block drag");
    await drag.finish("elsewhere");
    // Put the tree back for the steps below.
    drag = await startDrag(page, await handle("first"), "third");
    await line.waitFor({ timeout: 5000 });
    await drag.finish("drop");
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }], "moved back");
    assertEq(await line.count(), 0, "no drop line after the drop");
    assertNoProblems(page);
  });

  await step("notes: a todo renders as a checkbox and clicking it writes [x]", async () => {
    await editRow(page, "third");
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.type("- [ ] buy milk");
    await closeEditor(page);
    const box = row(page, "buy milk").locator("input.mdTaskCheckbox").first();
    await box.waitFor({ timeout: 5000 });
    await box.click();
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes("- [x] buy milk"), { what: "checkbox toggled in the source" });
    assertNoProblems(page);
  });

  await step("notes: an uploaded image renders (URL carries the workspace) and survives reload", async () => {
    const up = await alice2.upload("/api/upload-image", PNG_1PX, "dot.png", "image/png");
    await editRow(page, "buy milk");
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.type(`![](${up.url})`);
    await closeEditor(page);
    const check = async () => {
      const imgs = await page.$$eval("img.mdImg", (els) => els.map((e) => [e.getAttribute("src"), e.naturalWidth]));
      return imgs.length === 1 && imgs[0][0].includes(`ws=${second.id}`) && imgs[0][1] === 1 ? imgs[0][0] : null;
    };
    await until(check, { what: "rendered image with ?ws=" });
    await page.reload();
    await page.waitForSelector(".blockRow", { timeout: 15000 });
    const src = await until(check, { what: "rendered image after reload" });
    assertNoProblems(page);
    return src;
  });

  await step("notes: in the editor an untouched image shows the picture, the caret on it shows the source", async () => {
    const up = await alice2.upload("/api/upload-image", PNG_1PX, "dot2.png", "image/png");
    await editRow(page, "buy milk");
    await page.keyboard.press("Enter"); // a line break; list continuation makes line 2 a new todo item
    await page.keyboard.type(`![](${up.url})`);
    const widgets = () => page.$$eval(".blockEditorCm .cmImgWidget img", (els) => els.map((e) => [e.getAttribute("src"), e.naturalWidth]));
    // The caret sits at the end of the image it just typed: raw source.
    assertEq((await widgets()).length, 0, "typed image stays raw under the caret");
    await page.keyboard.press("Control+Home"); // line 1: the image is untouched now
    await until(async () => {
      const imgs = await widgets();
      return imgs.length === 1 && imgs[0][0].includes(`ws=${second.id}`) && imgs[0][1] === 1;
    }, { what: "image widget in the editor" });
    await page.keyboard.press("Control+End"); // back onto the image: raw again
    await until(async () => {
      const raw = await page.$eval(".blockEditorCm .cm-content", (el) => el.textContent);
      return (await widgets()).length === 0 && raw.includes("![](");
    }, { what: "raw image source under the caret" });
    await closeEditor(page);
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes(`- [ ] ![](${up.url})`), { what: "image line saved" });
    assertNoProblems(page);
  });

  await step("notes: one blank line is a paragraph break, a second one renders as an empty line", async () => {
    await editRow(page, "third");
    await page.keyboard.press("Enter");
    await page.keyboard.press("Enter");
    await page.keyboard.press("Enter");
    await page.keyboard.type("after gap");
    await closeEditor(page);
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes("third\\n\\n\\nafter gap"), { what: "blank lines saved" });
    const paras = await row(page, "after gap").locator(".blockRendered p").allTextContents();
    assertEq(JSON.stringify(paras), JSON.stringify(["third", "\u00a0", "after gap"]), "an empty paragraph between the two");
    assertNoProblems(page);
  });

  await step("notes: colored text via the / menu and a foldable [!note]- callout", async () => {
    await editRow(page, "after gap");
    await page.keyboard.press("Control+End");
    await page.keyboard.press("Enter");
    await page.keyboard.type("/red");
    await page.getByRole("button", { name: /Red text/ }).click();
    // The command lands through a React round trip that sets the content
    // first and the caret a beat later; type once the caret sits inside the
    // span (the mermaid step waits for its selection the same way).
    await page.waitForFunction(() => {
      const sel = document.getSelection();
      const ed = document.activeElement?.closest(".cm-content");
      if (!ed || !sel?.anchorNode || !ed.contains(sel.anchorNode)) return false;
      const r = document.createRange(); r.setStart(ed, 0); r.setEnd(sel.anchorNode, sel.anchorOffset);
      return r.toString().endsWith('#e5484d">');
    }, null, { timeout: 5000 });
    await page.keyboard.type("hot");
    await page.keyboard.press("End");
    await page.keyboard.press("Enter");
    await page.keyboard.type("> [!note]- Proof");
    await page.keyboard.press("Enter");
    await page.keyboard.type("hidden body");
    await closeEditor(page);
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes('<span style=\\"color:#e5484d\\">hot</span>'), { what: "the color span saved as inline HTML" });
    const colored = row(page, "hot").locator('.blockRendered span[style*="color"]');
    assertEq(await colored.innerText(), "hot", "the rendered view colors the run");
    const details = row(page, "Proof").locator("details.callout");
    assertEq(await details.count(), 1, "the fold flag renders a <details>");
    assertEq(await details.evaluate((el) => el.open), false, "'-' starts collapsed");
    await details.locator("summary").click();
    assertEq(await details.evaluate((el) => el.open), true, "the title toggles it");
    assertEq(await page.locator(".blockEditorCm").count(), 0, "toggling never opens the editor");
    assertNoProblems(page);
  });

  await step("notes: images centre with a grip each side; a click opens the editor on the clicked character", async () => {
    const up = await alice2.upload("/api/upload-image", PNG_1PX, "dot3.png", "image/png");
    await editRow(page, "second");
    await page.keyboard.press("Control+End");
    await page.keyboard.press("Shift+Enter");
    // Headings, a table and a list all render shorter than their source, so
    // the text below them sits lower in the editor than it did rendered.
    await page.keyboard.insertText(`![|200](${up.url})\n\n## Heading\n\n| col a | col b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n\n- item one\n- item two\n\nclick the **target** word`);
    await closeEditor(page);
    const frame = row(page, "target").locator(".mdImgFrame");
    await frame.waitFor();
    const [f, p] = [await frame.boundingBox(), await row(page, "target").locator(".blockRendered p").first().boundingBox()];
    assert(Math.abs((f.x + f.width / 2) - (p.x + p.width / 2)) < 2, `image centred (${f.x}+${f.width} in ${p.x}+${p.width})`);
    // The left grip dragged outward by 40px widens the centred image by 80.
    await frame.hover();
    const grip = await frame.locator(".mdResizeGrip.left").boundingBox();
    await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
    await page.mouse.down();
    await page.mouse.move(grip.x + grip.width / 2 - 40, grip.y + grip.height / 2, { steps: 4 });
    await page.mouse.up();
    await until(async () => /!\[\|28\d\]/.test(JSON.stringify(await tree(alice2, pageId))), { what: "left-grip width ≈ 280 saved" });
    assertEq(await page.locator(".blockEditorCm").count(), 0, "resizing never opens the editor");

    // Click just inside the "g" of the bold word: the caret opens before it.
    const pt = await page.evaluate(() => {
      const strong = [...document.querySelectorAll(".blockRendered strong")].find((s) => s.textContent === "target");
      const r = document.createRange();
      r.setStart(strong.firstChild, 3);
      r.setEnd(strong.firstChild, 4);
      const b = r.getBoundingClientRect();
      return { x: b.left + 1, y: b.top + b.height / 2 };
    });
    await page.mouse.click(pt.x, pt.y);
    await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
    await page.keyboard.type("X");
    await closeEditor(page);
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes("**tarXget**"), { what: "typed at the clicked character" });
    assertNoProblems(page);
  });

  await step("notes: the gap line between two adjacent $$ formulas opens the editor on a new line between them", async () => {
    await editRow(page, "second");
    await page.keyboard.press("Control+End");
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.insertText("$$x+1$$\n$$y+2$$");
    await closeEditor(page);
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes("$$x+1$$\\n$$y+2$$"), { what: "two formulas saved" });
    const mathRow = page.locator(".blockRow").filter({ has: page.locator(".katex-display") }).first();
    const gapY = await mathRow.evaluate((r) => {
      const [a, b] = [...r.querySelectorAll(".blockRendered > *")].map((el) => el.getBoundingClientRect());
      return (a.bottom + b.top) / 2;
    });
    const box = await mathRow.locator(".blockRendered").boundingBox();
    await page.mouse.move(box.x + box.width / 2, gapY);
    await mathRow.locator(".mdGapLine").waitFor({ timeout: 3000 });
    await page.mouse.click(box.x + box.width / 2, gapY);
    await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
    await page.keyboard.type("mid");
    await closeEditor(page);
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes("$$x+1$$\\nmid\\n$$y+2$$"), { what: "typed on the new line between the formulas" });
    assertNoProblems(page);
  });

  await step("notes: Export… as an Obsidian vault downloads a zip", async () => {
    await page.click("button[aria-label='View']");
    await page.locator(".popoverItem", { hasText: "Export…" }).click();
    const dialog = page.getByRole("dialog", { name: "Export", exact: true });
    await dialog.waitFor();
    await dialog.getByRole("button", { name: "Obsidian", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    const download = page.waitForEvent("download", { timeout: 15000 });
    await dialog.getByRole("button", { name: "Export", exact: true }).click();
    const file = await download;
    assert(/-obsidian\.zip$/.test(file.suggestedFilename()), `vault zip name: ${file.suggestedFilename()}`);
    await until(async () => (await page.textContent("body")).includes("Obsidian vault saved"), { what: "export status" });
    assertNoProblems(page);
  });

  await step("notes: the account menu lists both workspaces and switches", async () => {
    await page.click("button[aria-label='Account & settings']");
    await page.waitForSelector(".userPopover .wsItem");
    const names = await page.$$eval(".userPopover .wsItem .wsItemName", (els) => els.map((e) => e.textContent.trim()));
    assert(names.includes("Second") && names.includes("alice"), `workspaces listed: ${names.join(", ")}`);
    await page.locator(".userPopover .wsItem", { hasText: "alice" }).click();
    await page.waitForURL((u) => u.searchParams.get("ws") === alice.defaultWs, { timeout: 15000 });
    await page.waitForSelector(".folderNewBtn, .blockRow", { timeout: 15000 });
    assertNoProblems(page);
  });
  if (ctx) await ctx.close();

  await step("notes: Enter-as-new-block preference, in the default workspace", async () => {
    // The Enter key is an account preference: the profile's copy wins over this browser's.
    const { value: profile } = await alice.api("/api/prefs/profile");
    await alice.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), enterNewNote: true } } });
    const ctxD = await alice.context(browser);
    await ctxD.addInitScript(() => { try { localStorage.setItem("gamma-enter-new-note", "1"); } catch {} });
    const p = await openPage(ctxD, `${server.base}/?ws=${alice.defaultWs}`);
    const id = await newPageViaUi(p, "Default ws page");
    await p.keyboard.type("hello");
    await p.keyboard.press("Enter");
    await p.keyboard.type("world");
    await closeEditor(p);
    await until(async () => same(await tree(alice, id), [{ content: "hello", children: [] }, { content: "world", children: [] }]), { what: "two blocks saved" });
    assertNoProblems(p);
    await ctxD.close();
  });

  return { second, alice2, pageId };
}
