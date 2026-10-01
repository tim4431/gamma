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

// Make every notes search (/api/block-search with a query) on `page` answer
// as if the server's scan ran out of time: the real hits plus
// `partial: true`. Returns the undo.
export async function partialBlockSearch(page) {
  const match = (url) => url.pathname.endsWith("/api/block-search") && !!url.searchParams.get("q");
  const handler = async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), partial: true } });
  };
  await page.route(match, handler);
  return () => page.unroute(match, handler);
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
    // Opening a page is a passive landing: no row is focused (so the chat
    // gets no Cursor block) until the reader clicks one.
    assertEq(await page.locator(".blockRow.focused").count(), 0, "opening a page focuses no row");
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

  await step("notes: the move-to-page dialog walks its pages by keys; page shortcuts wait behind it", async () => {
    // A page of its own in a second tab, so the tree the other steps check stays put.
    const src = await alice2.api("/api/pages", { method: "POST", body: { title: "Move source" } });
    await alice2.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "moveblk1", parent: src.id, position: "a0", content: "to be moved" }] } });
    const destA = await alice2.api("/api/pages", { method: "POST", body: { title: "Archive alpha" } });
    const destB = await alice2.api("/api/pages", { method: "POST", body: { title: "Archive beta" } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${src.id}`);
    try {
      await editRow(p2, "to be moved");
      await p2.keyboard.type(" now");
      await until(async () => (await tree(alice2, src.id))[0]?.content === "to be moved now", { what: "the edit saved" });
      await closeEditor(p2);
      const wrap = p2.locator(".sortableBlockWrap", { hasText: "to be moved" }).first();
      await wrap.hover();
      await wrap.locator(".dragHandle").click();
      await p2.locator(".ctxMenuItem", { hasText: "Move to page…" }).click();
      const box = p2.getByPlaceholder("Filter pages…");
      await box.waitFor();
      // Ctrl+Z and F2 belong to the page behind the dialog: neither acts.
      await p2.keyboard.press("Control+z");
      await p2.keyboard.press("F2");
      await sleep(500); // an undo would have saved by now
      assertEq(await p2.locator(".titleEdit").count(), 0, "F2 does not rename behind the dialog");
      assertEq((await tree(alice2, src.id))[0]?.content, "to be moved now", "Ctrl+Z does not undo behind the dialog");
      await box.fill("archive");
      const items = p2.locator(".moveBlockList .ctxMenuItem");
      await until(async () => (await items.count()) === 2);
      const focused = (i) => items.nth(i).evaluate((el) => el === document.activeElement);
      await p2.keyboard.press("ArrowDown");
      assertEq(await focused(0), true);
      await p2.keyboard.press("ArrowDown");
      assertEq(await focused(1), true);
      await p2.keyboard.press("ArrowDown");
      assertEq(await focused(1), true, "the last page holds the focus");
      await p2.keyboard.press("ArrowUp");
      await p2.keyboard.press("ArrowUp");
      await until(() => box.evaluate((el) => el === document.activeElement));
      await p2.keyboard.press("ArrowDown");
      await p2.keyboard.press("ArrowDown");
      const pick = (await items.nth(1).innerText()).includes("beta") ? destB : destA;
      await p2.keyboard.press("Enter");
      await until(async () => (await tree(alice2, pick.id)).some((b) => b.content === "to be moved now"), { what: "moved to the second match" });
      assertEq((await tree(alice2, src.id)).length, 0);
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
  });

  await step("notes: block commands from the palette (new above, move down) and the keys Ctrl+Shift+K, F2", async () => {
    // Unbound block commands run from Ctrl+Shift+P on the focused row.
    await editRow(page, "third");
    await page.keyboard.press("Control+Shift+p");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await palette.waitFor();
    await palette.getByRole("textbox").fill(">new block above");
    await until(async () => /New block above/.test(await palette.locator('[role="option"][aria-selected="true"]').textContent()));
    await page.keyboard.press("Enter");
    await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
    await page.keyboard.type("before");
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "before", children: [] }, { content: "third", children: [] }], "new block above");
    await page.keyboard.press("Control+Shift+p");
    await palette.waitFor();
    await palette.getByRole("textbox").fill(">move block down");
    await until(async () => /Move block down/.test(await palette.locator('[role="option"][aria-selected="true"]').textContent()));
    await page.keyboard.press("Enter");
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }, { content: "before", children: [] }], "moved down");
    // Ctrl+Shift+K deletes the (one-line) block; the caret lands at the end of the block above.
    await editRow(page, "before");
    await page.keyboard.press("Control+Shift+k");
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }], "line deleted");
    await until(() => page.evaluate(() => document.activeElement?.closest(".cm-content")?.textContent === "third"), { what: "the caret moves to the block above" });
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

  await step("notes: an embed card's checkbox merges into a source edited on its own page; reopening shows the source as it is", async () => {
    const before = "- [ ] task A\n- [ ] task B";
    const source = await alice2.api("/api/pages", { method: "POST", body: { title: "Embed source" } });
    const x = await alice2.api("/api/blocks", { method: "POST", body: { parent_id: source.id, content: before } });
    const host = await alice2.api("/api/pages", { method: "POST", body: { title: "Embed host" } });
    await alice2.api("/api/blocks", { method: "POST", body: { parent_id: host.id, content: `![[${x.id}]]` } });
    const ctx2 = await alice2.context(browser);
    const page2 = await openPage(ctx2, `${server.base}/?ws=${second.id}&page=${host.id}`);
    try {
      const card = page2.locator(".blockEmbedCard").first();
      await until(async () => (await card.innerText()).includes("task B"), { what: "the embed card rendered" });
      // Another tab adds to the source while this card shows its older copy.
      const elsewhere = `${before}\nWritten elsewhere.`;
      await alice2.api(`/api/blocks/${x.id}`, { method: "PUT", body: { content: elsewhere, base: before } });
      await card.locator("input.mdTaskCheckbox").first().click();
      const merged = "- [x] task A\n- [ ] task B\nWritten elsewhere.";
      await until(async () => (await alice2.api(`/api/blocks/${x.id}`)).content === merged, { what: "the tick merged, the addition kept" });
      await until(async () => (await card.innerText()).includes("Written elsewhere."), { what: "the card shows the stored text" });
      // Off to the source page and back: the card is fetched again on the way in.
      await card.locator(".blockEmbedSrc").click();
      await until(() => new URL(page2.url()).searchParams.get("block") === source.id, { what: "the source page opened" });
      await alice2.api(`/api/blocks/${x.id}`, { method: "PUT", body: { content: `${merged}\nAnd more.`, base: merged } });
      await page2.getByRole("button", { name: "Back", exact: true }).click();
      await until(async () => (await page2.locator(".blockEmbedCard").first().innerText()).includes("And more."),
        { what: "the reopened page shows the source's current text" });
      assertNoProblems(page2);
    } finally { await ctx2.close(); }
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

  await step("notes: in the editor an image stays a picture with the caret beside it; right-click shows the source", async () => {
    const up = await alice2.upload("/api/upload-image", PNG_1PX, "dot2.png", "image/png");
    await editRow(page, "buy milk");
    await page.keyboard.press("Enter"); // a line break; list continuation makes line 2 a new todo item
    await page.keyboard.type(`![](${up.url})`);
    const widgets = () => page.$$eval(".blockEditorCm .cmImgWidget img", (els) => els.map((e) => [e.getAttribute("src"), e.naturalWidth]));
    // The caret sits right after the image it just typed: the boundary keeps the picture.
    await until(async () => {
      const imgs = await widgets();
      return imgs.length === 1 && imgs[0][0].includes(`ws=${second.id}`) && imgs[0][1] === 1;
    }, { what: "image widget in the editor" });
    await page.keyboard.press("Control+Home");
    await page.keyboard.press("Control+End"); // back to the image's end: still a picture
    assertEq((await widgets()).length, 1, "the caret at the image's end keeps the picture");
    await page.locator(".blockEditorCm .cmImgWidget").click({ button: "right" });
    await until(async () => {
      const raw = await page.$eval(".blockEditorCm .cm-content", (el) => el.textContent);
      return (await widgets()).length === 0 && raw.includes("![](");
    }, { what: "right-click reveals the raw image source" });
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
    // The bare "/" list comes in groups, the footer hints at the colours;
    // a typed query is one ranked list.
    await page.keyboard.type("/");
    const groups = page.locator(".slashMenu .slashMenuTitle");
    await groups.first().waitFor();
    assertEq(JSON.stringify(await groups.allTextContents()), JSON.stringify(["Text", "Math", "Insert", "Link", "Style"]), "the / menu's groups");
    assertEq(await page.locator(".slashMenu .slashMenuItem.selected .slashMenuLabel").innerText(), "Heading 1", "Text comes first");
    assert((await page.locator(".slashMenuFooter").innerText()).includes("red"), "the footer names the colours");
    await page.keyboard.type("red");
    assertEq(await groups.count(), 0, "a typed query shows no groups");
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

  await step("notes: opening a block's editor keeps its height (paragraph, list, $$ math, code fence, callout, quote, table)", async () => {
    const src = await alice2.api("/api/pages", { method: "POST", body: { title: "Heights" } });
    const blocks = {
      hpara: "A paragraph of prose long enough to wrap onto a second line in the notes panel, so a trailing margin under it would show as a jump.",
      hlist: "- [ ] one\n- [x] two\n- three",
      hmath: "Energy:\n$$\nE = \\sum_i n_i\n$$",
      hcode: "Code:\n```python\nprint(1)\n```",
      hcallout: "> [!tip] A callout\n> with a body line",
      hboxed: "Before the box:\n> [!warning]+ Careful\n> - a listed point",
      hquote: "Quoted:\n> a quoted line",
      htable: "Table:\n| a | b |\n| --- | --- |\n| 1 | 2 |",
    };
    await alice2.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: Object.entries(blocks).map(([id, content], i) => (
      { op: "insert", id, parent: src.id, position: `a${i}`, content })) } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${src.id}`);
    try {
      for (const id of Object.keys(blocks)) {
        const sel = `.blockRowWrap[data-block-id="${id}"] > .blockRow`;
        await p2.waitForSelector(`${sel} .blockRendered`);
        await p2.locator(sel).scrollIntoViewIfNeeded();
        const height = () => p2.locator(sel).evaluate((el) => el.getBoundingClientRect().height);
        const before = await height();
        // Click the first character, so the formula, the fence and the table
        // below it stay rendered widgets in the editor.
        const pt = await p2.locator(`${sel} .blockRendered`).evaluate((el) => {
          const n = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, { acceptNode: (t) => (t.textContent.trim() ? 1 : 3) }).nextNode();
          const r = document.createRange(); r.setStart(n, 0); r.setEnd(n, 1);
          const b = r.getBoundingClientRect();
          return { x: b.left + 1, y: b.top + b.height / 2 };
        });
        await p2.mouse.click(pt.x, pt.y);
        await p2.waitForSelector(`${sel} .blockEditorCm .cm-content`, { timeout: 5000 });
        const during = await height();
        assert(Math.abs(during - before) <= 1, `${id}: ${before}px rendered, ${during}px while editing`);
        await closeEditor(p2);
      }
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
  });

  await step("notes: the [[ picker lists pages by title first (a block search cut short says so); a typed [[exact title]] links, an unknown one is an unlinked chip", async () => {
    const target = await alice2.api("/api/pages", { method: "POST", body: { title: "Quantum Linkable Target" } });
    const other = await alice2.api("/api/pages", { method: "POST", body: { title: "Quantum Scratch" } });
    const src = await alice2.api("/api/pages", { method: "POST", body: { title: "Picker source" } });
    await alice2.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "pkblock", parent: src.id, position: "a0", content: "start" }] } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${src.id}`);
    const unroute = await partialBlockSearch(p2);
    try {
      await editRow(p2, "start");
      await p2.keyboard.type(" [[linkable");
      const picker = p2.locator('.refPopup[data-guide="editor.refSearch"]');
      await picker.waitFor();
      assertEq(await picker.locator(".refPopupHead").first().textContent(), "Pages", "pages come first");
      assertEq(await picker.locator(".refPopupItem.selected .refPopupText").innerText(), "Quantum Linkable Target");
      assertEq(await picker.locator(".refPopupItem.selected .refPopupText mark.searchMark").innerText(), "Linkable", "the typed text is marked");
      await until(async () => (await picker.locator(".refPopupFooter").innerText()).startsWith("Block search stopped early"),
        { what: "the footer says the block search stopped early" });
      await unroute();
      await p2.keyboard.press("Enter");
      await until(async () => (await tree(alice2, src.id))[0]?.content === `start [[${target.id}]]`, { what: "Enter links the page" });
      // "]]" typed after the exact title of one page links it; an unknown title stays text.
      await p2.keyboard.type(" [[quantum scratch]] and [[Nothing By This Name]]");
      await until(async () => (await tree(alice2, src.id))[0]?.content === `start [[${target.id}]] [[${other.id}]] and [[Nothing By This Name]]`,
        { what: "the typed title resolved to its page" });
      await closeEditor(p2);
      const r = row(p2, "start");
      assertEq(JSON.stringify(await r.locator(".blockRefChip").allInnerTexts()), JSON.stringify(["Quantum Linkable Target", "Quantum Scratch"]));
      assertEq(await r.locator(".unlinkedRef").innerText(), "Nothing By This Name", "the unknown title is an unlinked chip");
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
  });

  // Ctrl+F on a page opens the compact find bar (Settings → Search). With no
  // PDF to step through it still counts what the details would list, and
  // says when the notes search stopped early.
  await step("notes: the compact find bar on a notes page counts the results and says when the search stopped early", async () => {
    const pg = await alice2.api("/api/pages", { method: "POST", body: { title: "Compact find" } });
    await alice2.api(`/api/pages/${pg.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "cfnote", parent: pg.id, position: "a0", content: "a quokkafind note here" }] } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${pg.id}`);
    const unroute = await partialBlockSearch(p2);
    try {
      await row(p2, "quokkafind").waitFor();
      await p2.click("button[aria-label='Search']");
      await p2.waitForSelector(".searchPopover .searchInput");
      const details = p2.locator("button[aria-label='Toggle result details']");
      if (/\bon\b/.test(await details.getAttribute("class"))) await details.click();
      await p2.fill(".searchPopover .searchInput", "quokkafind");
      const summary = p2.locator(".searchPopover .searchResult", { hasText: "1 result — show it" });
      await summary.waitFor();
      await p2.locator(".searchPopover .searchHint", { hasText: "Stopped early — refine the search to see more." }).waitFor();
      assertEq(await p2.locator(".searchPopover .searchSection").count(), 0, "compact: no result lists");
      await summary.click();
      await p2.locator(".searchPopover .searchSection", { hasText: "Notes on this page" }).waitFor();
      await p2.keyboard.press("Escape");
      assertNoProblems(p2);
    } finally {
      await unroute();
      await p2.close();
    }
  });

  // "Paste as → Blocks" splits an outline into blocks (POST
  // /api/markdown-blocks); pasted into an empty line, they take its place.
  await step("notes: Paste as Blocks counts every block it made and leaves no empty line behind", async () => {
    const pg = await alice2.api("/api/pages", { method: "POST", body: { title: "Paste blocks" } });
    await alice2.api(`/api/pages/${pg.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "pbfirst", parent: pg.id, position: "a0", content: "above the paste" }] } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${pg.id}`);
    try {
      await editRow(p2, "above the paste");
      await p2.keyboard.press("Shift+Enter");
      await p2.waitForFunction(() => {
        const ed = document.activeElement?.closest(".cm-content");
        return !!ed && (ed.querySelector(".cm-placeholder") != null || ed.textContent === "");
      }, null, { timeout: 5000 });
      await p2.evaluate((text) => {
        const dt = new DataTransfer();
        dt.setData("text/plain", text);
        document.activeElement.closest(".cm-content")
          .dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      }, "- one\n  - one a\n  - one b\n- two");
      await p2.locator(".slashMenu .slashMenuItem", { hasText: "Blocks" }).first().click();
      await p2.getByText("Pasted 4 blocks.").waitFor();
      await until(async () => same(await tree(alice2, pg.id), [
        { content: "above the paste", children: [] },
        { content: "one", children: [{ content: "one a", children: [] }, { content: "one b", children: [] }] },
        { content: "two", children: [] },
      ]), { what: "the outline took the empty line's place" });
      await until(async () => (await p2.locator(".blockEditorCm .cm-content").innerText()) === "two",
        { what: "the caret ends in the last pasted block" });
      await closeEditor(p2);
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
  });

  // Text over the server's limit (collabSession's MAX_CONTENT, 200,000
  // characters) is held back: the notice says why, and the editor stays open
  // on it (App's onStartEdit) until it is short enough to save.
  await step("notes: a note over the length limit says so, is not saved, and keeps its editor open until shortened", async () => {
    const pg = await alice2.api("/api/pages", { method: "POST", body: { title: "Long note" } });
    await alice2.api(`/api/pages/${pg.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "longnote", parent: pg.id, position: "a0", content: "short" }] } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${pg.id}`);
    try {
      await editRow(p2, "short");
      await p2.keyboard.insertText(" " + "word ".repeat(40001));
      const notice = p2.getByText(/^A note is too long to save \(at most 200,000 characters\)/);
      await notice.waitFor();
      // The whole sentence shows: the pill wraps rather than ending in "…".
      assert(await p2.locator(".statusPill.error .pillText").evaluate((el) => el.scrollWidth <= el.clientWidth + 1
        && getComputedStyle(el).whiteSpace === "normal"), "the notice wraps");
      await p2.evaluate(() => document.activeElement?.blur());
      await sleep(300);
      assertEq(await p2.locator(".blockEditorCm").count(), 1, "the editor stays open on the long text");
      assertEq((await tree(alice2, pg.id))[0].content, "short", "the long text is not saved");
      await p2.locator(".blockEditorCm .cm-content").click();
      await p2.keyboard.press("Control+a");
      await p2.keyboard.insertText("short again");
      await notice.waitFor({ state: "detached" });
      await until(async () => (await tree(alice2, pg.id))[0].content === "short again", { what: "the shortened text saved" });
      await closeEditor(p2);
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
  });

  await step("notes: 'Linked from' is off until the setting turns it on, then opens the linking block; folding it is remembered", async () => {
    const target = await alice2.api("/api/pages", { method: "POST", body: { title: "Link target" } });
    await alice2.api(`/api/pages/${target.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "blown", parent: target.id, position: "a0", content: "the target's own note" }] } });
    const source = await alice2.api("/api/pages", { method: "POST", body: { title: "Link source" } });
    await alice2.api(`/api/pages/${source.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "blfiller", parent: source.id, position: "a0", content: "filler" },
      { op: "insert", id: "blsource", parent: source.id, position: "a1", content: `See [[${target.id}]] for more` }] } });
    const url = `${server.base}/?ws=${second.id}&page=${target.id}`;
    const p2 = await openPage(ctx, url);
    try {
      const panel = p2.locator(".backlinksPanel");
      // Off by default (Settings → Reading & editing › Notes › Linked from).
      await until(async () => (await p2.locator(".blockRowWrap").count()) > 0, { what: "the notes render" });
      assertEq(await panel.count(), 0, "the section is off until the account turns it on");
      await alice2.api("/api/prefs/profile", { method: "PATCH", body: { set: { backlinksVisible: true } } });
      await p2.goto(url);
      await panel.waitFor();
      assert((await panel.locator(".backlinksHead").innerText()).includes("Linked from 1 page"), "the head counts pages");
      const [own, box] = [await row(p2, "the target's own note").boundingBox(), await panel.boundingBox()];
      assert(box.y > own.y + own.height - 1, "the section sits under the notes");
      assertEq(await panel.locator(".backlinkPageTitle").innerText(), "Link source");
      assertEq(await panel.locator(".backlinkSnippet .blockRefChip").innerText(), "Link target", "the [[ref]] renders as a chip");
      await panel.locator(".backlinkItem").click();
      await until(async () => (await p2.locator('.blockRowWrap[data-block-id="blsource"] > .blockRow.focused').count()) === 1,
        { what: "the linking page opens with its block focused" });
      // Folded here, still folded after a reload.
      await p2.goto(url);
      await panel.waitFor();
      await panel.locator(".backlinksHead").click();
      assertEq(await panel.locator(".backlinkItem").count(), 0, "folding hides the entries");
      await p2.reload();
      await panel.waitFor();
      assertEq(await panel.locator(".backlinksHead").getAttribute("aria-expanded"), "false", "the fold is remembered");
      await panel.locator(".backlinksHead").click();
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
  });

  await step("notes: Export… as an Obsidian vault downloads a zip", async () => {
    await page.click("button[aria-label='View']");
    await page.locator(".menuPopover .ctxMenuItem", { hasText: "Export…" }).click();
    const dialog = page.getByRole("dialog", { name: "Export", exact: true });
    await dialog.waitFor();
    await dialog.getByRole("button", { name: "Obsidian", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    const download = page.waitForEvent("download", { timeout: 15000 });
    await dialog.getByRole("button", { name: "Export", exact: true }).click();
    const file = await download;
    assert(/-obsidian\.zip$/.test(file.suggestedFilename()), `vault zip name: ${file.suggestedFilename()}`);
    await dialog.getByRole("heading", { name: "Export ready", exact: true }).waitFor();
    assert((await dialog.innerText()).includes("Unzip it into a vault"), "what to do with the vault");
    await dialog.getByRole("button", { name: "Done", exact: true }).click();
    await dialog.waitFor({ state: "detached" });
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
    const ctxD = await alice.context(browser);
    try {
      await alice.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), enterNewNote: true } } });
      await ctxD.addInitScript(() => { try { localStorage.setItem("gamma-enter-new-note", "1"); } catch {} });
      const p = await openPage(ctxD, `${server.base}/?ws=${alice.defaultWs}`);
      const id = await newPageViaUi(p, "Default ws page");
      await p.keyboard.type("hello");
      await p.keyboard.press("Enter");
      await p.keyboard.type("world");
      await closeEditor(p);
      await until(async () => same(await tree(alice, id), [{ content: "hello", children: [] }, { content: "world", children: [] }]), { what: "two blocks saved" });
      assertNoProblems(p);
    } finally {
      await ctxD.close();
      // Workers reuse Alice in later groups; Shift+Enter must keep its original meaning.
      await alice.api("/api/prefs/profile", { method: "PUT", body: { value: profile || {} } });
    }
  });

  await step("notes: images and tables are objects — a press selects, a drag lands between or inside blocks, right-click edits the source", async () => {
    const up = await alice2.upload("/api/upload-image", PNG_1PX, "dot4.png", "image/png");
    const table = "| a | b |\n|---|---|\n| 1 | 2 |";
    const withTable = await alice2.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: `intro\n\n${table}\n\nafter` } });
    const withImage = await alice2.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: `![|200](${up.url})` } });
    const landing = await alice2.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "para one\n\npara two" } });
    const ctxO = await alice2.context(browser);
    const p = await openPage(ctxO, `${server.base}/?ws=${second.id}&page=${pageId}`);
    const children = async () => (await alice2.api(`/api/blocks/${pageId}/subtree`)).block.children;
    const content = async (id) => (await children()).find((b) => b.id === id)?.content;
    const frameOf = (id, kind) => p.locator(`.blockRowWrap[data-block-id="${id}"] .mdObject-${kind}`);

    // A press on the frame's margin still opens the editor there (click to
    // source) — and in the editor the table stays a table.
    const tableFrame = frameOf(withTable.id, "table");
    await tableFrame.waitFor();
    await tableFrame.scrollIntoViewIfNeeded();
    const fb = await tableFrame.boundingBox();
    await p.mouse.click(fb.x + fb.width - 1, fb.y + fb.height - 1); // the margin, clear of the corner handle
    await p.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
    await p.locator(".blockEditorCm .cmTableWidget").waitFor();
    assertEq(await p.locator(".mdObjectSelected").count(), 0, "the margin press did not select the table");
    await closeEditor(p);
    // A press on the table's own body (a cell) edits the cell, not the source.
    await frameOf(withTable.id, "table").locator("td").first().click();
    await p.locator(".mdTableCellInput").waitFor();
    assertEq(await p.locator(".blockEditorCm").count(), 0, "a cell press never opens the raw editor");
    await p.keyboard.press("Escape");

    // One HTML5 drag of an object frame: dragstart on it, dragover on the
    // target row ("top": its top edge → a new block above; "gap": between the
    // row's two paragraphs → inside the block), then drop + dragend.
    const dragTo = async (srcSel, targetId, where) => {
      await p.evaluate(([srcSel, targetId, where]) => {
        const src = document.querySelector(srcSel);
        const wrap = document.querySelector(`.blockRowWrap[data-block-id="${targetId}"]`);
        const row = wrap.querySelector(".blockRow");
        const dt = new DataTransfer();
        const fire = (el, type, x, y) => el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt, clientX: x, clientY: y }));
        fire(src, "dragstart", 0, 0);
        const r = wrap.getBoundingClientRect();
        let y = r.top + 2;
        if (where === "gap") {
          const ps = wrap.querySelectorAll(".blockRendered > p");
          y = (ps[0].getBoundingClientRect().bottom + ps[1].getBoundingClientRect().top) / 2;
        }
        // Near the row's left edge: a sibling (further right nests under the row).
        fire(row, "dragover", r.left + 20, y);
        window.__e2eObj = { src, row, dt, x: r.left + 20, y };
      }, [srcSel, targetId, where]);
    };
    const release = () => p.evaluate(() => {
      const { src, row, dt, x, y } = window.__e2eObj;
      const fire = (el, type) => el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt, clientX: x, clientY: y }));
      fire(row, "drop");
      fire(src, "dragend");
    });

    // The table to the landing row's top edge: a block of its own, right above it.
    await dragTo(`.blockRowWrap[data-block-id="${withTable.id}"] .mdObject-table`, landing.id, "top");
    await p.locator(".dropIndicator").waitFor({ timeout: 3000 }); // the between-blocks line shows before the drop
    await release();
    await until(async () => (await content(withTable.id)) === "intro\n\nafter", { what: "the table left its block, the blank lines closed up" });
    // Closing the raw editor above pretty-printed the table (formatTables).
    const sameTable = (c) => (c || "").replace(/[ -]+/g, "") === table.replace(/[ -]+/g, "");
    const kids = await children();
    const tableBlock = kids.find((b) => sameTable(b.content));
    assert(tableBlock, "the table is a block of its own");
    assertEq(kids[kids.indexOf(tableBlock) + 1]?.id, landing.id, "the new block sits right above the landing row");
    assertEq(await p.locator(".dropIndicator").count(), 0, "the line is gone after the drop");

    // The image into the gap between the landing block's two paragraphs.
    await dragTo(`.blockRowWrap[data-block-id="${withImage.id}"] .mdObject-image`, landing.id, "gap");
    await p.locator(".dropIndicatorInside").waitFor({ timeout: 3000 }); // the inside-block line shows in the gap
    await release();
    await until(async () => (await content(landing.id)) === `para one\n\n![|200](${up.url})\n\npara two`, { what: "the image landed between the paragraphs" });
    assertEq(await content(withImage.id), "", "its old block is empty");

    // Out of the block being edited, with a real mouse drag of the editor's
    // picture widget: back to the (now empty) block it came from.
    await frameOf(landing.id, "image").click({ button: "right", position: { x: 2, y: 2 } });
    await p.getByRole("button", { name: "Edit markdown source" }).click();
    await p.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
    await p.keyboard.press("Control+End");
    const widget = p.locator(".blockEditorCm .cmImgWidget");
    await widget.waitFor();
    // First within the block being edited: onto the top of its first line —
    // the picture becomes the first paragraph (the editor's own drop, not
    // CodeMirror's paste-at-caret, so no copy is left behind).
    const imgMd = `![|200](${up.url})`;
    {
      const w0 = await widget.boundingBox();
      const l0 = await p.locator(".blockEditorCm .cm-line").first().boundingBox();
      await p.mouse.move(w0.x + w0.width / 2, w0.y + w0.height / 2);
      await p.mouse.down();
      await p.mouse.move(w0.x + w0.width / 2 + 6, w0.y + w0.height / 2 + 6);
      await p.mouse.move(l0.x + 30, l0.y + 2, { steps: 10 });
      await p.mouse.move(l0.x + 32, l0.y + 3);
      await p.locator(".dropIndicatorInside").waitFor({ timeout: 3000 });
      await p.mouse.up();
      await until(async () => (await content(landing.id)) === `${imgMd}\n\npara one\n\npara two`, { what: "the picture moved to the top of the block being edited, once" });
    }
    await p.keyboard.press("Control+End");
    await widget.waitFor();
    const wb = await widget.boundingBox();
    const tb = await p.locator(`.blockRowWrap[data-block-id="${withImage.id}"]`).boundingBox();
    await p.mouse.move(wb.x + wb.width / 2, wb.y + wb.height / 2);
    await p.mouse.down();
    await p.mouse.move(wb.x + wb.width / 2 + 6, wb.y + wb.height / 2 + 6);
    await p.mouse.move(tb.x + 20, tb.y + 3, { steps: 10 });
    await p.mouse.move(tb.x + 22, tb.y + 4); // the engine here sends no repeat dragover: nudge once
    await p.locator(".dropIndicator").waitFor({ timeout: 3000 });
    await p.mouse.up();
    await until(async () => (await content(landing.id)) === "para one\n\npara two", { what: "the picture dragged out of the block being edited" });
    const now = await children();
    const picBlock = now.find((b) => b.content === imgMd);
    assert(picBlock, "the picture is a block of its own");
    assertEq(now[now.indexOf(picBlock) + 1]?.id, withImage.id, "dropped on the row's top edge: right above it");
    await closeEditor(p).catch(() => {});

    // A press in the blank beside the centred picture opens the editor with
    // the caret right after it (the picture stays rendered there).
    const picFrame = frameOf(picBlock.id, "image");
    await picFrame.scrollIntoViewIfNeeded();
    const pb = await picFrame.boundingBox();
    await p.mouse.click(pb.x + pb.width + 60, pb.y + pb.height / 2);
    await p.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
    await p.locator(".blockEditorCm .cmImgWidget").waitFor();
    await p.keyboard.type("X");
    await closeEditor(p);
    await until(async () => (await content(picBlock.id)) === `${imgMd}X`, { what: "typed right after the picture" });

    // Double-clicking the picture zooms it (a click selects; the lightbox
    // closes on Escape); a press on it selects, and Delete removes it.
    const img = frameOf(picBlock.id, "image").locator("img.mdImg");
    await img.waitFor();
    await img.dblclick({ position: { x: 4, y: 190 } }); // the hover toolbar pill sits at the top
    await p.locator(".mdLightbox").waitFor();
    await p.keyboard.press("Escape");
    await p.locator(".mdLightbox").waitFor({ state: "detached" });
    await img.click({ position: { x: 4, y: 190 } });
    await p.waitForSelector(".mdObjectSelected");
    assertEq(await p.locator(".blockEditorCm").count(), 0, "pressing the picture selects it, no editor");
    await p.keyboard.press("Delete");
    await until(async () => (await content(picBlock.id)) === "X", { what: "Delete removed the selected image" });

    // Right-click → "Edit markdown source": the editor opens with the caret
    // inside the table, so its source shows; with the caret past its end the
    // table is a widget again, and that widget drags like the frame does.
    await frameOf(tableBlock.id, "table").click({ button: "right", position: { x: 2, y: 2 } });
    await p.getByRole("button", { name: "Edit markdown source" }).click();
    await p.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
    assertEq(await p.locator(".blockEditorCm .cmTableWidget").count(), 0, "the source shows with the caret inside the table");
    assert((await p.$eval(".blockEditorCm .cm-content", (el) => el.textContent)).includes("| a"), "raw table text in the editor");
    await p.keyboard.press("Control+End");
    await p.locator(".blockEditorCm .cmTableWidget").waitFor();
    await dragTo(".blockEditorCm .cmTableWidget", landing.id, "top");
    await p.locator(".dropIndicator").waitFor({ timeout: 3000 }); // a drag from the editor's widget shows the line too
    await release();
    await until(async () => (await content(tableBlock.id)) === "", { what: "the table left the block being edited" });
    const after = await children();
    const moved = after.find((b) => sameTable(b.content));
    assertEq(after[after.indexOf(moved) + 1]?.id, landing.id, "dragged out of the editor into a new block above the landing row");
    await closeEditor(p);
    assertNoProblems(p);
    await ctxO.close();
  });

  return { second, alice2, pageId };
}
