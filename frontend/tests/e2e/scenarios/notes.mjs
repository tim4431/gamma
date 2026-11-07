// Note editing: page creation, the block editor's keys (Enter / Shift+Enter
// and the preference that swaps them / Tab / Shift+Tab / Backspace), the op save path +
// reload, undo, the handle menu, the selection toolbar, markdown rendering
// (todos, images) and the workspace on browser-fetched upload URLs. Runs in
// a NON-default workspace: every request must name it explicitly, which is
// where plumbing bugs show.
import { assertOpensInPlace } from "../harness.mjs";

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

// Tab / Shift+Tab re-parent the block, which remounts its row: its editor
// stays open with the focus, the caret where it was (App's keepEditorThrough).
async function stillEditing(page, text) {
  await page.waitForFunction((text) => {
    const ed = document.activeElement?.closest(".blockEditorCm");
    return !!ed && ed.closest(".blockRow")?.textContent.includes(text);
  }, text, { timeout: 5000 });
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

  await step("notes: Enter / Tab / Shift+Tab / Backspace shape the tree", async () => {
    await editRow(page, "first");
    await page.keyboard.press("Enter");
    await page.keyboard.type("second");
    await page.keyboard.press("Tab");
    await stillEditing(page, "second");
    await page.keyboard.press("Enter");
    await page.keyboard.type("third");
    await page.keyboard.press("Shift+Tab");
    await stillEditing(page, "third");
    await page.keyboard.press("Enter");
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

  await step("notes: Shift+Enter is a line break; Ctrl+Z inside the editor restores the text", async () => {
    await editRow(page, "third");
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.type("line two");
    await until(async () => JSON.stringify(await tree(alice2, pageId)).includes("third\\nline two"), { what: "line break saved" });
    await page.keyboard.press("Control+z");
    await page.getByText(/^Undone: note text edit:/).waitFor();
    await closeEditor(page);
    await saved([{ content: "first", children: [{ content: "second", children: [] }] }, { content: "third", children: [] }], "undo saved");
    assertNoProblems(page);
  });

  // The editor shows a numbered marker as text in tabular figures
  // (BlockCmEditor's cmListNum): Inter's own "1" is narrower than "2", which
  // set the first item's text left of the others'.
  await step("notes: Shift+Enter continues a numbered list in the block, and its items' text lines up in the editor", async () => {
    const src = await alice2.api("/api/pages", { method: "POST", body: { title: "Numbered" } });
    await alice2.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "numblk01", parent: src.id, position: "a0", content: "1. one" }] } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${src.id}`);
    try {
      await editRow(p2, "one");
      for (const word of ["two", "three", "four", "seven"]) { await p2.keyboard.press("Shift+Enter"); await p2.keyboard.type(word); }
      await until(async () => (await tree(alice2, src.id))[0]?.content === "1. one\n2. two\n3. three\n4. four\n5. seven", { what: "the list saved" });
      // Where each item's text starts: the first letter after "N. ".
      const starts = await p2.evaluate(() => [...document.querySelectorAll(".blockEditorCm .cm-line")].map((line) => {
        const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
        let at = line.textContent.indexOf(" ") + 1, node;
        while ((node = walker.nextNode()) && at >= node.data.length) at -= node.data.length;
        const range = document.createRange();
        range.setStart(node, at); range.setEnd(node, at + 1);
        return range.getBoundingClientRect().left;
      }));
      assertEq(starts.length, 5, "five lines");
      assert(Math.max(...starts) - Math.min(...starts) < 0.5, `the items' text lines up: ${starts.join(", ")}`);
      assertNoProblems(p2);
    } finally { await p2.close(); }
  });

  // A quote or callout spans lines (editor/mdLines.js): the line-break Enter
  // carries its marks and a list inside them, pasted lines land at the
  // quote's level (or as copied: Paste as → As copied), lines copied out of
  // a quote join it instead of nesting, and Ctrl+Shift+. quotes or unquotes
  // the selected lines. It renders as one box.
  await step("notes: a callout keeps its lines together: Shift+Enter continues its list, a paste stays in it, Ctrl+Shift+. quotes lines", async () => {
    const src = await alice2.api("/api/pages", { method: "POST", body: { title: "Quotes" } });
    await alice2.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "quoteblk1", parent: src.id, position: "a0", content: "> [!tip] Steps\n> - one" },
      { op: "insert", id: "quoteblk2", parent: src.id, position: "a1", content: "alpha\n\nbeta" }] } });
    const content = async (i) => (await tree(alice2, src.id))[i]?.content;
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${src.id}`);
    try {
      await editRow(p2, "Steps");
      await p2.keyboard.press("Control+End");
      await p2.keyboard.press("Shift+Enter");
      await p2.keyboard.type("two");
      await p2.keyboard.press("Shift+Enter");
      await p2.keyboard.press("Shift+Enter");
      await p2.keyboard.type("after the list");
      const listed = "> [!tip] Steps\n> - one\n> - two\n> after the list";
      await until(async () => (await content(0)) === listed, { what: "the list in the callout saved" });
      assertEq(await p2.locator(".cm-line.cmCalloutLine .cmBulletDot").count(), 2, "the editor draws both items' bullets in the box");

      const pasteText = (text) => p2.evaluate((text) => {
        const dt = new DataTransfer();
        dt.setData("text/plain", text);
        document.activeElement.closest(".cm-content")
          .dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      }, text);
      await p2.keyboard.press("Shift+Enter");
      await pasteText("first\nsecond\n\nthird");
      const quoted = `${listed}\n> first\n> second\n>\n> third`;
      await until(async () => (await content(0)) === quoted, { what: "every pasted line in the quote" });
      const items = p2.locator(".slashMenu .slashMenuItem");
      await until(async () => /At this level.*Nested.*As copied.*Blocks/.test((await items.allTextContents()).join("|")), { what: "the chooser offers the other placements and the text as copied" });
      await items.filter({ hasText: "As copied" }).first().click();
      await until(async () => (await content(0)) === `${listed}\n> first\nsecond\n\nthird`, { what: "As copied leaves the lines as they came" });
      // From the end of "third" up into "second": three lines selected.
      await p2.keyboard.press("Shift+ArrowUp");
      await p2.keyboard.press("Shift+ArrowUp");
      await p2.keyboard.press("Control+Shift+Period");
      await until(async () => (await content(0)) === quoted, { what: "Ctrl+Shift+. quoted the selected lines, the blank one with a bare >" });
      // Lines copied out of a quote keep their marks; pasted into this one
      // they join it rather than make a quote inside it.
      await p2.keyboard.press("Control+End");
      await p2.keyboard.press("Shift+Enter");
      await pasteText("> 1. this\n> 2. second");
      await until(async () => (await content(0)) === `${quoted}\n> 1. this\n> 2. second`, { what: "the copied quote's lines at the box's level" });
      await p2.keyboard.press("Escape");
      await closeEditor(p2);
      const box = row(p2, "Steps").locator("blockquote.callout");
      assertEq(await box.count(), 1, "one callout box");
      assertEq(await row(p2, "Steps").locator("blockquote").count(), 1, "and no quote inside it");
      assertEq(await box.locator("ul > li").count(), 2, "its list");
      assertEq(await box.locator("ol > li").count(), 2, "and the pasted numbered one");
      const boxText = await box.innerText();
      for (const word of ["after the list", "first", "second", "third", "this"]) assert(boxText.includes(word), `"${word}" is in the box`);

      await editRow(p2, "alpha");
      await p2.keyboard.press("Control+End");
      await p2.keyboard.press("Control+Shift+Home");
      await p2.keyboard.press("Control+Shift+Period");
      await until(async () => (await content(1)) === "> alpha\n>\n> beta", { what: "the whole block quoted" });
      await p2.keyboard.press("Control+Shift+Period");
      await until(async () => (await content(1)) === "alpha\n\nbeta", { what: "and unquoted again" });
      await closeEditor(p2);
      assertNoProblems(p2);
    } finally { await p2.close(); }
  });

  // Math pasted as \( \) and \[ \] (ChatGPT, LaTeX) comes in as the $ and
  // $$ a note renders (editor/pasteMath.js); Paste as → As copied puts the
  // clipboard's own back, and an escaped bracket is no formula.
  await step("notes: pasted \\( \\) and \\[ \\] math comes in as $ and $$ and renders; As copied keeps the clipboard's", async () => {
    const src = await alice2.api("/api/pages", { method: "POST", body: { title: "Pasted math" } });
    await alice2.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "mathpaste1", parent: src.id, position: "a0", content: "Inline" },
      { op: "insert", id: "mathpaste2", parent: src.id, position: "a1", content: "Energy:\n" },
      { op: "insert", id: "mathpaste3", parent: src.id, position: "a2", content: "Cited" },
      { op: "insert", id: "mathpaste4", parent: src.id, position: "a3", content: "> Key result:\n> " },
      { op: "insert", id: "mathpaste5", parent: src.id, position: "a4", content: "Kept \\[ x \\]" }] } });
    const content = async (i) => (await tree(alice2, src.id))[i]?.content;
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${src.id}`);
    try {
      const pasteText = (text) => p2.evaluate((text) => {
        const dt = new DataTransfer();
        dt.setData("text/plain", text);
        document.activeElement.closest(".cm-content")
          .dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      }, text);
      const items = p2.locator(".slashMenu .slashMenuItem");
      const rows = async () => (await items.allTextContents()).join("|");

      await editRow(p2, "Inline");
      await p2.keyboard.press("Control+End");
      await pasteText(" where \\( x^2 \\) holds");
      await until(async () => (await content(0)) === "Inline where $x^2$ holds", { what: "the inline formula stored with dollars" });
      await until(async () => /^Math.*\|As copied/.test(await rows()), { what: "the chooser offers the text as copied" });
      await items.filter({ hasText: "As copied" }).first().click();
      await until(async () => (await content(0)) === "Inline where \\( x^2 \\) holds", { what: "As copied puts the clipboard's text back" });
      await closeEditor(p2);

      await editRow(p2, "Energy");
      await p2.keyboard.press("Control+End");
      await pasteText("\\[\nE = mc^2\n\\]\nwhere \\( m \\) is the mass");
      await until(async () => (await content(1)) === "Energy:\n$$\nE = mc^2\n$$\nwhere $m$ is the mass", { what: "the display formula on lines of its own" });
      await until(async () => /^Text.*\|As copied.*\|Blocks/.test(await rows()), { what: "the placements, the text as copied, and Blocks" });
      await p2.keyboard.press("Escape");
      await closeEditor(p2);
      const energy = row(p2, "Energy");
      await energy.locator(".katex-display").waitFor();
      assertEq(await energy.locator(".katex").count(), 2, "the display formula and the inline one render");

      await editRow(p2, "Cited");
      await p2.keyboard.press("Control+End");
      await pasteText(" as shown \\[1\\]");
      await until(async () => (await content(2)) === "Cited as shown \\[1\\]", { what: "an escaped bracket pastes as it is" });
      await sleep(300);
      assertEq(await items.count(), 0, "and offers no chooser");
      await closeEditor(p2);
      assertEq(await row(p2, "Cited").locator(".katex").count(), 0, "no formula");

      // In a quote the formula keeps the quote's marks and renders as display math in it.
      await editRow(p2, "Key result");
      await p2.keyboard.press("Control+End");
      await pasteText("\\[\nE = mc^2\n\\]");
      await until(async () => (await content(3)) === "> Key result:\n> $$\n> E = mc^2\n> $$", { what: "the display formula on the quote's lines" });
      await p2.keyboard.press("Escape");
      await closeEditor(p2);
      const quoted = row(p2, "Key result").locator("blockquote .katex-display");
      await quoted.waitFor();
      assert(!(await quoted.innerText()).includes(">"), "no quote mark in the formula");

      // The note's own source copied in Gamma and pasted back stays as it
      // is: the padded "\[ x \]" an older note holds would read as a formula
      // from anywhere else (editor/sourceCopy.js).
      await editRow(p2, "Kept");
      await p2.evaluate(() => {
        const content = document.activeElement.closest(".cm-content");
        const view = content.cmTile.root.view;
        view.dispatch({ selection: { anchor: 5, head: view.state.doc.length } });
        content.dispatchEvent(new ClipboardEvent("copy", { clipboardData: new DataTransfer(), bubbles: true, cancelable: true }));
      });
      await p2.keyboard.press("End");
      await pasteText("\\[ x \\]");
      await until(async () => (await content(4)) === "Kept \\[ x \\]\\[ x \\]", { what: "the copied source pasted as it was" });
      await sleep(300);
      assertEq(await items.count(), 0, "with no chooser");
      await closeEditor(p2);
      assertNoProblems(p2);
    } finally { await p2.close(); }
  });

  await step("notes: a two-backtick code span is one inline code in the editor, as in the rendered view", async () => {
    const src = await alice2.api("/api/pages", { method: "POST", body: { title: "Code spans" } });
    await alice2.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "codespan1", parent: src.id, position: "a0", content: "Run ``a`**b**`` now" }] } });
    const ctxC = await alice2.context(browser);
    const p2 = await openPage(ctxC, `${server.base}/?ws=${second.id}&page=${src.id}`);
    try {
      const rendered = row(p2, "Run").locator(".blockRendered code");
      await rendered.waitFor();
      assertEq(await rendered.innerText(), "a`**b**", "the rendered view's code span");
      await editRow(p2, "Run");
      const code = p2.locator(".blockEditorCm .cmInlineCode");
      await code.first().waitFor();
      assertEq(JSON.stringify(await code.allInnerTexts()), JSON.stringify(["a`**b**"]), "the editor shows the same span, its backticks hidden");
      assertEq(await p2.locator(".blockEditorCm .cmStrong").count(), 0, "and nothing bold inside it");
      await closeEditor(p2);
      assertNoProblems(p2);
    } finally { await ctxC.close(); }
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
    // The source page's socket, its messages to the tab held back while `held`
    // is set: the move's response then always lands before the room's delete.
    let socket = null, held = null;
    await ctx.routeWebSocket((url) => url.pathname === `/api/ws/page/${src.id}`, (ws) => {
      socket = ws;
      const server = ws.connectToServer();
      ws.onMessage((m) => server.send(m));
      server.onMessage((m) => (held ? held.push(m) : ws.send(m)));
    });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${src.id}`);
    const sent = []; // the batches the tab sends for the source page (from the move on)
    p2.on("request", (r) => {
      if (r.method() === "POST" && new URL(r.url()).pathname === `/api/pages/${src.id}/ops`) sent.push(r.postDataJSON());
    });
    try {
      await editRow(p2, "to be moved");
      await p2.keyboard.type(" now");
      await until(async () => (await tree(alice2, src.id))[0]?.content === "to be moved now", { what: "the edit saved" });
      await closeEditor(p2);
      const wrap = p2.locator(".sortableBlockWrap", { hasText: "to be moved" }).first();
      await wrap.hover();
      await wrap.locator(".dragHandle").click();
      await p2.locator(".ctxMenuItem", { hasText: "Move to page…" }).click();
      const dialog = p2.getByRole("dialog", { name: "Move block to page" });
      const box = dialog.getByRole("textbox", { name: "Move block to page…" });
      await box.waitFor();
      // Ctrl+Z and F2 belong to the page behind the dialog: neither acts.
      await p2.keyboard.press("Control+z");
      await p2.keyboard.press("F2");
      await sleep(500); // an undo would have saved by now
      assertEq(await p2.locator(".titleEdit").count(), 0, "F2 does not rename behind the dialog");
      assertEq((await tree(alice2, src.id))[0]?.content, "to be moved now", "Ctrl+Z does not undo behind the dialog");
      // The palette's keys (shared/ui/Palette.jsx): the field keeps the
      // focus, ↑↓ move the selection round the matches.
      await box.fill("archive");
      const items = dialog.getByRole("option");
      await until(async () => (await items.count()) === 2);
      const selected = async () => (await items.evaluateAll((els) => els.map((el) => el.getAttribute("aria-selected")))).indexOf("true");
      assertEq(await selected(), 0, "the best match is selected");
      await p2.keyboard.press("ArrowDown");
      assertEq(await selected(), 1);
      await p2.keyboard.press("ArrowDown");
      assertEq(await selected(), 0, "the selection goes round");
      await p2.keyboard.press("ArrowUp");
      assertEq(await selected(), 1);
      assert(await box.evaluate((el) => el === document.activeElement), "the field keeps the focus");
      const pick = (await items.nth(1).innerText()).includes("beta") ? destB : destA;
      held = [];
      sent.length = 0;
      await p2.keyboard.press("Enter");
      await until(async () => (await tree(alice2, pick.id)).some((b) => b.content === "to be moved now"), { what: "moved to the second match" });
      assertEq((await tree(alice2, src.id)).length, 0);
      await sleep(500); // a delete of its own would have gone out by now
      assert(!sent.some((b) => b.ops.some((op) => op.id === "moveblk1")), "the tab sends no op for the moved block");
      // The block leaves the page through the room's delete.
      const release = held;
      held = null;
      release.forEach((m) => socket.send(m));
      await until(async () => (await p2.locator(".sortableBlockWrap", { hasText: "to be moved" }).count()) === 0, { what: "the room's delete took the block off" });
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
    // The new block's editor has the focus, the caret at its start (the
    // empty doc shows its placeholder widget after the caret).
    await until(() => page.evaluate(() => {
      const ed = document.activeElement?.closest(".blockEditorCm .cm-content");
      const sel = document.getSelection();
      if (!ed || !(ed.querySelector(".cm-placeholder") || ed.textContent === "") || !sel?.anchorNode || !ed.contains(sel.anchorNode)) return false;
      const r = document.createRange(); r.setStart(ed, 0); r.setEnd(sel.anchorNode, sel.anchorOffset);
      return r.toString() === "";
    }), { what: "the new block's editor focused, caret at its start" });
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

  await step("notes: dragging a block's handle moves it; the drop line steps in where a drop nests, in step with the row's text, and never outlives the drag (#88)", async () => {
    const handle = (text) => page.locator(".sortableBlockWrap", { hasText: text }).first().locator(".dragHandle").first().elementHandle();
    const line = page.locator(".dropIndicator");
    // A drop nests once the pointer passes the start of the row's text, and the
    // line says so: it steps in by one indent, clear of the handles' gutter.
    let drag = await startDrag(page, await handle("third"), "first");
    await line.waitFor({ timeout: 5000 });
    const dragOverText = (text, dx) => page.locator(".sortableBlockWrap", { hasText: text }).last().evaluate((wrap, dx) => {
      const textLeft = wrap.querySelector(".blockRendered").getBoundingClientRect().left;
      const row = wrap.querySelector(".blockRow");
      row.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: window.__e2eDrag.dt, clientX: textLeft + dx, clientY: row.getBoundingClientRect().top + 4 }));
      return { textLeft, rowLeft: row.getBoundingClientRect().left };
    }, dx);
    for (const [text, sibling, nested] of [["first", -22, -8], ["second", -8, 6]]) {
      for (const [dx, want, what] of [[-2, sibling, "a sibling"], [2, nested, "nested"]]) {
        const { textLeft, rowLeft } = await dragOverText(text, dx);
        await until(async () => Math.abs((await line.boundingBox()).x - (textLeft + want)) < 1, { what: `the line for ${what} on "${text}", ${want}px from its text` });
        assert((await line.boundingBox()).x >= rowLeft, `the line for ${what} on "${text}" starts in the row, not over the handles`);
      }
    }
    await drag.finish("elsewhere");
    await line.waitFor({ state: "detached", timeout: 5000 });
    // A handle drag shows the line and the drop moves the block.
    drag = await startDrag(page, await handle("third"), "first");
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
    await page.keyboard.press("Enter");
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
      // A copy in the card's own editor puts its source on the clipboard; the
      // rendered view's copy leaves a copy already made alone.
      await page2.locator(".blockEmbedCard").first().getByText("And more.").click();
      const editor = page2.locator(".blockEmbedCard .cm-content").first();
      await editor.waitFor();
      const [copied, held] = await editor.evaluate((el) => {
        const view = el.cmTile.root.view;
        view.dispatch({ selection: { anchor: 0, head: view.state.doc.length } });
        const dt = new DataTransfer();
        el.dispatchEvent(new ClipboardEvent("copy", { clipboardData: dt, bubbles: true, cancelable: true }));
        return [dt.getData("text/plain"), view.state.doc.toString()];
      });
      assertEq(copied, held, "the clipboard holds the card's source");
      await page2.keyboard.press("Escape");
      assertNoProblems(page2);
    } finally { await ctx2.close(); }
  });

  await step("notes: an uploaded image renders (URL carries the workspace) and survives reload", async () => {
    const up = await alice2.upload("/api/upload-image", PNG_1PX, "dot.png", "image/png");
    await editRow(page, "buy milk");
    await page.keyboard.press("Enter");
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
    await page.keyboard.press("Shift+Enter"); // a line break; list continuation makes line 2 a new todo item
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
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.press("Shift+Enter");
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
    await page.keyboard.press("Shift+Enter");
    // The bare "/" list comes in groups, the footer hints at the colours;
    // a typed query is one ranked list, fuzzy, its letters marked where
    // they matched; each row names the command to type.
    await page.keyboard.type("/");
    const groups = page.locator(".slashMenu .slashMenuTitle");
    const chosen = page.locator(".slashMenu .slashMenuItem.selected");
    await groups.first().waitFor();
    assertEq(JSON.stringify(await groups.allTextContents()), JSON.stringify(["Text", "Math", "Insert", "Link", "Style"]), "the / menu's groups");
    assertEq(await chosen.locator(".slashMenuLabel").innerText(), "Heading 1", "Text comes first");
    assertEq(await chosen.locator(".slashMenuName").innerText(), "/h1", "the row names its command");
    assert((await page.locator(".slashMenuFooter").innerText()).includes("red"), "the footer names the colours");
    await page.keyboard.type("cb");
    await until(async () => await chosen.locator(".slashMenuLabel").innerText() === "Code block", { what: "/cb is the Code block" });
    assertEq(JSON.stringify(await chosen.locator(".slashMenuLabel mark").allTextContents()), JSON.stringify(["C", "b"]), "the fuzzy letters marked in the label");
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");
    await page.keyboard.type("red");
    assertEq(await groups.count(), 0, "a typed query shows no groups");
    assertEq(await chosen.locator(".slashMenuName mark").innerText(), "red", "the typed name marked");
    await page.getByRole("button", { name: /Red text/ }).click();
    // The command lands through a React round trip that sets the content
    // first and the caret a beat later; type once the caret sits inside the
    // span (the diagrams step waits for its selection the same way).
    await page.waitForFunction(() => {
      const sel = document.getSelection();
      const ed = document.activeElement?.closest(".cm-content");
      if (!ed || !sel?.anchorNode || !ed.contains(sel.anchorNode)) return false;
      const r = document.createRange(); r.setStart(ed, 0); r.setEnd(sel.anchorNode, sel.anchorOffset);
      return r.toString().endsWith('#e5484d">');
    }, null, { timeout: 5000 });
    await page.keyboard.type("hot");
    await page.keyboard.press("End");
    await page.keyboard.press("Shift+Enter");
    await page.keyboard.type("> [!note]- Proof");
    await page.keyboard.press("Shift+Enter");
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

  await step("notes: the selection toolbar formats selected text — bold, underline, a color — and waits for a mouse selection's release", async () => {
    await editRow(page, "Proof");
    await page.keyboard.press("Control+End");
    await page.keyboard.press("Enter");
    await page.keyboard.type("make this bold now");
    // A keyboard selection shows the bar at once, above the selected line.
    for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowLeft");
    for (let i = 0; i < 4; i++) await page.keyboard.press("Shift+ArrowLeft");
    const bar = page.locator(".selTools");
    const bold = bar.locator('[data-command="block.bold"]');
    await bar.waitFor();
    assertEq(await bold.getAttribute("aria-pressed"), "false", "Bold starts unpressed");
    assertEq(await bold.getAttribute("title"), "Bold (Ctrl+B)", "the title names the chord");
    const barBox = await bar.boundingBox();
    const selTop = await page.evaluate(() => getSelection().getRangeAt(0).getBoundingClientRect().top);
    assert(barBox.y + barBox.height <= selTop + 1, `the bar (bottom ${barBox.y + barBox.height}) sits above the selection (top ${selTop})`);
    // Each tool keeps the text selected, so the next one applies to it too.
    await bold.click();
    await until(async () => await bold.getAttribute("aria-pressed") === "true", { what: "Bold pressed once applied" });
    await bar.locator('[data-command="block.underline"]').click();
    await until(async () => await bar.locator('[data-command="block.underline"]').getAttribute("aria-pressed") === "true", { what: "Underline pressed" });
    await bar.getByRole("button", { name: "Color", exact: true }).click();
    await bar.getByRole("button", { name: "Red text" }).click();
    await bar.getByRole("button", { name: "Color", exact: true }).click();
    await until(async () => await bar.getByRole("button", { name: "Red text" }).getAttribute("aria-pressed") === "true", { what: "the run's color marked" });
    // Escape puts the bar away; the selection stays.
    await page.keyboard.press("Escape");
    await bar.waitFor({ state: "detached" });
    assertEq(await page.evaluate(() => getSelection().toString()), "bold", "Escape keeps the selection");
    // A mouse drag: no bar while the button is down, one in place after.
    const word = await page.evaluate(() => {
      const walker = document.createTreeWalker(document.querySelector(".blockEditorCm .cm-content"), NodeFilter.SHOW_TEXT);
      for (let n; (n = walker.nextNode());) {
        const i = n.data.indexOf("make this");
        if (i < 0) continue;
        const r = document.createRange();
        r.setStart(n, i);
        r.setEnd(n, i + 4);
        const b = r.getBoundingClientRect();
        return { x1: b.left + 1, x2: b.right - 1, y: b.top + b.height / 2 };
      }
      return null;
    });
    assert(word, "the typed line is in the editor");
    await page.mouse.move(word.x1, word.y);
    await page.mouse.down();
    await page.mouse.move(word.x2, word.y, { steps: 4 });
    await sleep(150);
    assertEq(await bar.count(), 0, "no bar while the button is held");
    await assertOpensInPlace(page, ".selTools", () => page.mouse.up(), { what: "the selection toolbar" });
    await bar.waitFor();
    assertEq(await page.evaluate(() => getSelection().toString()), "make", "the drag selected the word");
    // Ctrl+U is the toolbar's Underline from the keyboard.
    await page.keyboard.press("End");
    for (let i = 0; i < 3; i++) await page.keyboard.press("Shift+ArrowLeft");
    await page.keyboard.press("Control+u");
    await closeEditor(page);
    const want = 'make this **<u><span style="color:#e5484d">bold</span></u>** <u>now</u>';
    const contents = (nodes) => nodes.flatMap((n) => [n.content, ...contents(n.children)]);
    await until(async () => contents(await tree(alice2, pageId)).includes(want), { what: `saved as ${want}` });
    const red = row(page, "make this").locator(".blockRendered strong u span[style*='color']");
    assertEq(await red.innerText(), "bold", "rendered bold, underlined and red");
    assertEq(await row(page, "make this").locator(".blockRendered u").last().innerText(), "now", "Ctrl+U underlined");
    assertNoProblems(page);
  });

  await step("notes: images centre with a grip each side; a click opens the editor on the clicked character", async () => {
    const up = await alice2.upload("/api/upload-image", PNG_1PX, "dot3.png", "image/png");
    await editRow(page, "second");
    await page.keyboard.press("Control+End");
    await page.keyboard.press("Enter");
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
    await page.keyboard.press("Enter");
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

  await step("notes: a plain row draws neither dot nor label; a placed row keeps p.N and a mark while edited; an empty row is blank until hovered or focused, and on a view-only page for good, and never copied; the + and the ⋮⋮ sit side by side on a row's first line", async () => {
    const src = await alice2.api("/api/pages", { method: "POST", body: { title: "Rows" } });
    const place = { pageNumber: 3, width: 600, height: 800, boundingRect: { x1: 10, y1: 10, x2: 90, y2: 30 }, rects: [{ x1: 10, y1: 10, x2: 90, y2: 30 }] };
    const blocks = [
      { id: "rplain", content: "A paragraph" },
      { id: "rempty", content: "" },
      { id: "rnext", content: "One line" },
      { id: "rlast", content: "Another line" },
      { id: "rhead", content: "# A heading" },
      { id: "rhigh", content: "On a passage", props: { pdf_position: place, quote: "a quoted passage" } },
      { id: "rbox", content: "", props: { text_box: { x: 40, y: 40 }, pdf_page: 2 } },
    ];
    await alice2.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: blocks.map(({ id, content, props }, i) => (
      { op: "insert", id, parent: src.id, position: `a${i}`, content, props })).concat(
      { op: "insert", id: "rdeep", parent: "rlast", position: "a0", content: "Under a line" }) } });
    const shown = (p, id, what) => p.locator(`.blockRowWrap[data-block-id="${id}"] > .blockRow ${what}`);
    const wordShown = (p, id) => shown(p, id, ".blockPlaceholder").evaluate((el) => getComputedStyle(el).visibility === "visible");
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${src.id}`);
    try {
      await p2.waitForSelector('.blockRowWrap[data-block-id="rhigh"] .blockRendered');
      assertEq(await p2.locator(".blockRow .noteBulletDot").count(), 0, "no dot on a row at rest");
      assertEq(await shown(p2, "rplain", ".blockMeta").count(), 0, "no label on a plain row");
      assertEq(await shown(p2, "rplain", ".dotSlot").count(), 1, "the slot keeps the left edge");
      assertEq(await shown(p2, "rhigh", ".blockMeta").innerText(), "p.3", "a placed row names its page");
      assertEq(await shown(p2, "rhigh", ".highlightDot").count(), 1, "a highlight keeps its dot");

      // The row's editor stands in for a highlight's dot; it keeps a quiet one.
      await shown(p2, "rhigh", ".blockRendered").click();
      await p2.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
      assertEq(await shown(p2, "rhigh", ".noteBulletDot").count(), 1, "a highlight keeps a mark while edited");
      await closeEditor(p2);

      // An empty note's word comes with the row, in the row's own height.
      await p2.mouse.move(0, 0);
      await until(async () => !(await wordShown(p2, "rempty")), { what: "an empty row blank at rest" });
      assert(await wordShown(p2, "rbox"), "any other empty row keeps its word");
      const height = () => shown(p2, "rempty", ".blockRendered").evaluate((el) => el.getBoundingClientRect().height);
      const line = await shown(p2, "rnext", ".blockRendered").evaluate((el) => el.getBoundingClientRect().height);
      assert(Math.abs((await height()) - line) <= 1, `an empty row keeps a line's height: ${await height()}px, a line ${line}px`);
      await p2.locator('.blockRowWrap[data-block-id="rempty"] > .blockRow').hover();
      await until(() => wordShown(p2, "rempty"), { what: "the word on hover" });
      await shown(p2, "rempty", ".blockRendered").click();
      await p2.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
      await closeEditor(p2);
      await p2.mouse.move(0, 0);
      assertEq(await p2.locator('.blockRowWrap[data-block-id="rempty"] > .blockRow.focused').count(), 1, "the row keeps the focus");
      assert(await wordShown(p2, "rempty"), "the word on the focused row");

      // On a row's first line, level with its text: the "+", then the ⋮⋮, then
      // the row. They stay in the row's height, so over neither the next row
      // nor their own text, and the row is no scroller to CodeMirror. A press
      // reaches all of the "+", clear of the sash's margin at the pane's edge
      // and of the guide line of the row above.
      const handlesOf = (id) => p2.evaluate((id) => {
        const wrap = document.querySelector(`.sortableBlockWrap[data-block-id="${id}"]`);
        const box = (selector) => { const r = wrap.querySelector(selector).getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; };
        const add = wrap.querySelector(".addHandle");
        const hit = box(".addHandle");
        const reached = [hit.left + 2, hit.right - 2].every((x) => document.elementFromPoint(x, (hit.top + hit.bottom) / 2)?.closest(".addHandle") === add);
        return { add: hit, drag: box(".dragHandle"), row: box(".blockRow"), text: box(".blockRendered > :first-child"), reached,
          scroller: wrap.scrollHeight > wrap.clientHeight || wrap.scrollWidth > wrap.clientWidth,
          pane: document.querySelector(".sidebar").getBoundingClientRect().left,
          guide: wrap.closest(".blockChildren")?.getBoundingClientRect().left ?? null };
      }, id);
      for (const id of ["rnext", "rhead", "rdeep"]) {
        await p2.locator(`.blockRowWrap[data-block-id="${id}"] > .blockRow`).hover();
        const at = await handlesOf(id);
        assert(at.add.right <= at.drag.left + 0.5 && at.drag.right <= at.row.left + 0.5, `${id}: the +, the ⋮⋮, the row: ${JSON.stringify(at)}`);
        assert(Math.abs(at.add.top - at.drag.top) < 0.5 && at.add.bottom <= at.row.bottom, `${id}: one line, in the row: ${JSON.stringify(at)}`);
        assert(Math.abs((at.drag.top + at.drag.bottom) / 2 - (at.text.top + at.text.bottom) / 2) <= 1.5, `${id}: level with the text: ${JSON.stringify(at)}`);
        assert(!at.scroller, `${id}: the row has nothing to scroll`);
        assert(at.reached, `${id}: a press reaches the + from edge to edge: ${JSON.stringify(at)}`);
        assert(at.add.left >= at.pane + 6, `${id}: clear of the sash's margin: ${JSON.stringify(at)}`);
        if (at.guide != null) assert(at.add.left > at.guide + 1, `${id}: clear of the guide line: ${JSON.stringify(at)}`);
      }
      // Both handles are icons, 18 px in any font: two ⋮ glyphs were 34 px
      // wide on Linux, and the ⋮⋮ grew and pushed the + out of the pane.
      const before = await handlesOf("rnext");
      const wide = await p2.addStyleTag({ content: ".rowHandles { font: 600 40px monospace; letter-spacing: 12px; }" });
      const after = await handlesOf("rnext");
      await wide.evaluate((el) => el.remove());
      assert(Math.abs(after.drag.right - after.drag.left - 18) < 0.5 && Math.abs(after.add.left - before.add.left) < 0.5,
        `the handles keep their width in any font: ${JSON.stringify({ before, after })}`);
      const order = async () => (await alice2.api(`/api/blocks/${src.id}/subtree`)).block.children.map((b) => b.id);
      const wrap = p2.locator('.sortableBlockWrap[data-block-id="rnext"]');
      const add = wrap.locator(".addHandle");
      for (const [modifiers, past, what] of [[[], 1, "below"], [["Alt"], -1, "above"]]) {
        await p2.locator('.blockRowWrap[data-block-id="rnext"] > .blockRow').hover();
        await add.click({ modifiers });
        await until(async () => (await order()).length === blocks.length + 1, { what: `the + adds a block ${what}` });
        const ids = await order();
        assert(!blocks.some((b) => b.id === ids[ids.indexOf("rnext") + past]), `it lands ${what} the row: ${ids}`);
        await p2.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
        await p2.keyboard.press("Backspace");
        await until(async () => (await order()).length === blocks.length, { what: "that block gone again" });
      }
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
    await alice2.api(`/api/share/${src.id}`, { method: "POST" });
    await alice2.api(`/api/share-settings/${src.id}`, { method: "PUT", body: { audience: "anyone", role: "view" } });
    const { token } = await alice2.api(`/api/share-settings/${src.id}`);
    const shareCtx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const viewer = await openPage(shareCtx, `${server.base}/?share=${token}`);
    await viewer.waitForSelector('.blockRowWrap[data-block-id="rempty"] .blockRendered', { timeout: 15000 });
    await viewer.locator('.blockRowWrap[data-block-id="rempty"] > .blockRow').hover();
    assert(!(await wordShown(viewer, "rempty")), "a viewer never sees the word");
    assertEq(await viewer.locator(".rowHandles").count(), 0, "a viewer has no handles");
    // Copying across the gap leaves out the word that was never on screen.
    const copied = await shown(viewer, "rplain", ".blockRendered").evaluate((el) => {
      const last = document.querySelector('.blockRowWrap[data-block-id="rnext"] > .blockRow .blockRendered');
      const range = document.createRange();
      range.selectNodeContents(el);
      range.setEnd(last, last.childNodes.length);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      const clipboardData = new DataTransfer();
      el.dispatchEvent(new ClipboardEvent("copy", { bubbles: true, cancelable: true, clipboardData }));
      selection.removeAllRanges();
      return { plain: clipboardData.getData("text/plain"), html: clipboardData.getData("text/html") };
    });
    assertEq(copied.plain, "A paragraph\n\nOne line", "a gap copies as nothing");
    assert(copied.html.includes("One line") && !copied.html.includes("(empty)"), `the rich copy leaves the word out too: ${copied.html}`);
    assertNoProblems(viewer);
    await shareCtx.close();
  });

  // A new page of [id, content, children?, props?] rows, open in its own tab,
  // with what the keys below need on it.
  const keysPage = async (title, rows) => {
    const made = await alice2.api("/api/pages", { method: "POST", body: { title } });
    const ops = [];
    const add = (list, parent) => list.forEach(([id, content, kids = [], props = {}], i) => {
      ops.push({ op: "insert", id, parent, position: `a${i.toString(20).toUpperCase()}`, content, props });
      add(kids, id);
    });
    add(rows, made.id);
    await alice2.api(`/api/pages/${made.id}/ops`, { method: "POST", body: { client: "e2e", ops } });
    const p = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${made.id}`);
    await p.waitForSelector(".blockRow .blockRendered", { timeout: 15000 });
    // The tree as nested [content, ...children], the server's.
    const shape = async () => (await tree(alice2, made.id)).map(function line(b) { return [b.content, ...b.children.map(line)]; });
    const held = (want, what) => until(async () => same(await shape(), want), { what: what || `tree ${JSON.stringify(want)}` });
    const edit = async (id) => {
      await p.locator(`.blockRowWrap[data-block-id="${id}"] > .blockRow .blockBody`).click();
      await p.waitForFunction((id) => document.activeElement?.closest(".blockRowWrap")?.dataset.blockId === id, id, { timeout: 5000 });
      await p.keyboard.press("Control+End");
    };
    const left = async (n) => { for (let i = 0; i < n; i++) await p.keyboard.press("ArrowLeft"); };
    const editing = () => p.evaluate(() => document.activeElement?.closest(".blockRowWrap")?.dataset.blockId || null);
    // The new-block key, with the caret across in the block it made.
    const newBlock = async () => {
      const was = await editing();
      await p.keyboard.press("Enter");
      await p.waitForFunction((was) => {
        const id = document.activeElement?.closest(".blockRowWrap")?.dataset.blockId;
        return !!id && id !== was;
      }, was, { timeout: 5000 });
    };
    return { id: made.id, p, held, edit, left, editing, newBlock };
  };

  await step("notes: the new-block key splits at the caret, one undo step with the caret back; before the text it makes a block above and stays; open children take the new block first, folded ones keep theirs", async () => {
    const { p, held, edit, left, editing, newBlock } = await keysPage("Split", [
      ["ks1", "abcd"], ["ks2", "parent", [["ks3", "kid"]]], ["ks4", "folded", [["ks5", "hidden"]], { collapsed: true }],
    ]);
    const others = [["parent", ["kid"]], ["folded", ["hidden"]]];
    try {
      await edit("ks1");
      // The caret moves after the last commit: undo must still find it.
      await left(2);
      await newBlock();
      await p.keyboard.type("X");
      await held([["ab"], ["Xcd"], ...others], "split at the caret, the caret at the new block's start");
      await p.keyboard.press("Control+z");
      await held([["ab"], ["cd"], ...others], "undo takes the typing back");
      await p.keyboard.press("Control+z");
      await held([["abcd"], ...others], "one more takes the split back, whole");
      await until(async () => (await editing()) === "ks1", { what: "the first block's editor back" });
      await p.keyboard.type("Y");
      await held([["abYcd"], ...others], "the caret back where the split was");

      // At the start of the text: the new block is above and the caret stays.
      await p.keyboard.press("Control+Home");
      await p.keyboard.press("Enter");
      await p.keyboard.type("Z");
      await held([[""], ["ZabYcd"], ...others], "a block above, no focus change");
      await p.keyboard.press("Control+z");
      await p.keyboard.press("Control+z");
      await held([["abYcd"], ...others], "that is one step too");

      await edit("ks2");
      await newBlock();
      await p.keyboard.type("first");
      await held([["abYcd"], ["parent", ["first"], ["kid"]], ["folded", ["hidden"]]], "first under open children");
      await edit("ks4");
      await newBlock();
      await p.keyboard.type("past");
      await held([["abYcd"], ["parent", ["first"], ["kid"]], ["folded", ["hidden"]], ["past"]], "after the subtree of a folded block");
      assertNoProblems(p);
    } finally {
      await p.close();
    }
  });

  await step("notes: the new-block key goes on with a list, a count and an open box, the text after the caret taking the marker; an empty item ends the list, alone in place, at the end of several lines in a new block", async () => {
    const { p, held, edit, left, editing, newBlock } = await keysPage("Lists", [
      ["kl1", "- ab"], ["kl2", "3. three"], ["kl3", "- [x] done"], ["kl4", "- cd"], ["kl5", "- a\n- b"], ["kl6", "- a\n- b"],
    ]);
    const later = [["3. three"], ["- [x] done"], ["- cd"], ["- a\n- b"], ["- a\n- b"]];
    try {
      await edit("kl1");
      await newBlock();
      await p.keyboard.type("next");
      await held([["- ab"], ["- next"], ...later], "the next item");
      // An empty item that is the whole block ends: no block, the caret stays.
      await newBlock();
      await held([["- ab"], ["- next"], ["- "], ...later], "another item");
      const item = await editing();
      await p.keyboard.press("Enter");
      await held([["- ab"], ["- next"], [""], ...later], "the empty item ends the list");
      assertEq(await editing(), item, "no block made, the caret stays");
      await p.keyboard.type("para");
      await held([["- ab"], ["- next"], ["para"], ...later], "back to a paragraph");

      await edit("kl2");
      await newBlock();
      await p.keyboard.type("four");
      await held([["- ab"], ["- next"], ["para"], ["3. three"], ["4. four"], ["- [x] done"], ["- cd"], ["- a\n- b"], ["- a\n- b"]], "the count goes on");
      await edit("kl3");
      await newBlock();
      await p.keyboard.type("open");
      await held([["- ab"], ["- next"], ["para"], ["3. three"], ["4. four"], ["- [x] done"], ["- [ ] open"], ["- cd"], ["- a\n- b"], ["- a\n- b"]], "a new to-do starts unchecked");
      await edit("kl4");
      await left(1);
      await newBlock();
      await held([["- ab"], ["- next"], ["para"], ["3. three"], ["4. four"], ["- [x] done"], ["- [ ] open"], ["- c"], ["- d"], ["- a\n- b"], ["- a\n- b"]], "the text after the caret takes the marker");

      // The last empty item of several lines goes, and a paragraph follows.
      await edit("kl5");
      await p.keyboard.press("Shift+Enter");
      await newBlock();
      await p.keyboard.type("after");
      await held([["- ab"], ["- next"], ["para"], ["3. three"], ["4. four"], ["- [x] done"], ["- [ ] open"], ["- c"], ["- d"], ["- a\n- b"], ["after"], ["- a\n- b"]], "the list ends in a new paragraph");
      await p.keyboard.press("Control+z");
      await p.keyboard.press("Control+z");
      await held([["- ab"], ["- next"], ["para"], ["3. three"], ["4. four"], ["- [x] done"], ["- [ ] open"], ["- c"], ["- d"], ["- a\n- b\n- "], ["- a\n- b"]], "one undo step brings the empty item back");

      // Between two lines: each item keeps its own marker.
      await edit("kl6");
      await p.keyboard.press("ArrowUp");
      await p.keyboard.press("End");
      await newBlock();
      await held([["- ab"], ["- next"], ["para"], ["3. three"], ["4. four"], ["- [x] done"], ["- [ ] open"], ["- c"], ["- d"], ["- a\n- b\n- "], ["- a"], ["- b"]], "the lines after the caret go along");
      assertNoProblems(p);
    } finally {
      await p.close();
    }
  });

  await step("notes: Backspace right after a list marker takes it in one press, on any line; a heading loses one character; an empty note goes, its children staying", async () => {
    const { p, held, edit, left, editing } = await keysPage("Markers", [
      ["kb1", "- item"], ["kb2", "1. num"], ["kb3", "- [ ] task"], ["kb4", "plain\n- inner"], ["kb5", "# head"], ["kb6", "> - quoted"],
      ["kb7", "", [["kb8", "kid"]]], ["kb9", "after"],
    ]);
    try {
      await edit("kb1");
      await left(4);
      await p.keyboard.press("Backspace");
      await held([["item"], ["1. num"], ["- [ ] task"], ["plain\n- inner"], ["# head"], ["> - quoted"], ["", ["kid"]], ["after"]], "a bullet's marker");
      await edit("kb2");
      await left(3);
      await p.keyboard.press("Backspace");
      await edit("kb3");
      await left(4);
      await p.keyboard.press("Backspace");
      await edit("kb4");
      await left(5);
      await p.keyboard.press("Backspace");
      await edit("kb5");
      await left(4);
      await p.keyboard.press("Backspace");
      await edit("kb6");
      await left(6);
      await p.keyboard.press("Backspace");
      await held([["item"], ["num"], ["task"], ["plain\ninner"], ["#head"], ["> quoted"], ["", ["kid"]], ["after"]], "each marker in one press, the heading's character, the quote's marks kept");

      await edit("kb7");
      await p.keyboard.press("Backspace");
      await held([["item"], ["num"], ["task"], ["plain\ninner"], ["#head"], ["> quoted"], ["kid"], ["after"]], "the empty parent goes, its child takes its place");
      assertEq(await editing(), "kb6", "the caret goes to the block shown above");
      assertNoProblems(p);
    } finally {
      await p.close();
    }
  });

  await step("notes: Backspace at a note's start merges it into the block above, marker gone, children in order, one undo step; an empty block above goes instead; a thread, code or a fold above refuses", async () => {
    const { id, p, held, edit, editing } = await keysPage("Merges", [
      ["km1", "one"], ["km2", "- two\n- rest"],
      ["kp1", "parent", [["kp2", "child", [["kp3", "grand"]]], ["kp4", "next"]]],
      ["ke1", ""], ["ke2", "- stays"],
      ["kc0", "before"], ["kc1", "open thread"], ["kc2", "resolved thread"],
      ["kf0", "```\ncode\n```"], ["kf1", "under code"],
      ["kt0", "text"], ["kt1", "> quoted"],
      ["kd0", "folded", [["kd1", "hidden"]], { collapsed: true }], ["kd2", "under the fold"],
    ]);
    const tail = [
      ["- stays"], ["before"], ["open thread"], ["resolved thread"], ["```\ncode\n```"], ["under code"],
      ["text"], ["> quoted"], ["folded", ["hidden"]], ["under the fold"],
    ];
    const start = async (rowId) => { await edit(rowId); await p.keyboard.press("Control+Home"); };
    try {
      await alice2.api(`/api/pages/${id}/comments`, { method: "POST", body: { block_id: "kc1", body: "A question" } });
      const shut = await alice2.api(`/api/pages/${id}/comments`, { method: "POST", body: { block_id: "kc2", body: "An answer" } });
      await alice2.api(`/api/comments/${shut.comment.id}/resolve`, { method: "POST", body: { resolved: true } });
      await p.reload();
      await p.waitForSelector('.blockRowWrap[data-block-id="kc1"] .commentCtl', { state: "attached", timeout: 15000 });

      await start("km2");
      await p.keyboard.press("Backspace");
      await held([["onetwo\n- rest"], ["parent", ["child", ["grand"]], ["next"]], [""], ...tail], "the first line's marker goes");
      await until(async () => (await editing()) === "km1", { what: "the editor on the block above" });
      await p.keyboard.type("X");
      await held([["oneXtwo\n- rest"], ["parent", ["child", ["grand"]], ["next"]], [""], ...tail], "the caret at the seam");
      await p.keyboard.press("Control+z");
      await p.keyboard.press("Control+z");
      await held([["one"], ["- two\n- rest"], ["parent", ["child", ["grand"]], ["next"]], [""], ...tail], "one undo step");
      await until(async () => (await editing()) === "km2", { what: "the editor back on the merged block" });
      await p.keyboard.type("Y");
      await held([["one"], ["Y- two\n- rest"], ["parent", ["child", ["grand"]], ["next"]], [""], ...tail], "the caret back at its start");
      await p.keyboard.press("Backspace");
      await held([["one"], ["- two\n- rest"], ["parent", ["child", ["grand"]], ["next"]], [""], ...tail], "the typing gone");

      await start("kp2");
      await p.keyboard.press("Backspace");
      await held([["one"], ["- two\n- rest"], ["parentchild", ["grand"], ["next"]], [""], ...tail], "into its parent, its children at its place");

      await start("ke2");
      await p.keyboard.press("Backspace");
      await held([["one"], ["- two\n- rest"], ["parentchild", ["grand"], ["next"]], ...tail], "the empty block above goes");
      assertEq(await editing(), "ke2", "and this one's editor stays");
      await p.keyboard.type("Q");
      await held([["one"], ["- two\n- rest"], ["parentchild", ["grand"], ["next"]], ["Q- stays"], ...tail.slice(1)], "with its caret at the start");
      await p.keyboard.press("Backspace");

      // None of these has anywhere to go: nothing moves, the editor stays.
      for (const refused of ["kc1", "kc2", "kf1", "kt1", "kd2"]) {
        await start(refused);
        await p.keyboard.press("Backspace");
        await sleep(150);
        assertEq(await editing(), refused, `${refused} keeps its editor`);
      }
      await held([["one"], ["- two\n- rest"], ["parentchild", ["grand"], ["next"]], ...tail], "nothing merged");
      assertNoProblems(p);
    } finally {
      await p.close();
    }
  });

  await step("notes: the new-block key never cuts a formula, typed with its auto-paired closer, with the completion popup open or not; Tab still takes a completion", async () => {
    const { id, p, held, edit, left, newBlock } = await keysPage("Formulas", [
      ["fm1", "see"], ["fm2", "next"], ["fm3", "see $ab$ here"], ["fm4", "- item"], ["fm5", "tab"],
    ]);
    const popup = p.locator(".latexAcPopup");
    try {
      await edit("fm1");
      await p.keyboard.type(" $x^2");
      await newBlock();
      await p.keyboard.type("A");
      const typed = [["see $x^2$"], ["A"]];
      await held([...typed, ["next"], ["see $ab$ here"], ["- item"], ["tab"]], "the closer stays with its formula, the caret in the new block");

      // The popup opens unasked: Enter closes it and goes on to the same split.
      await edit("fm2");
      await p.keyboard.type(" $\\fr");
      await popup.waitFor();
      await newBlock();
      await popup.waitFor({ state: "hidden" });
      await p.keyboard.type("B");
      typed.push(["next $\\fr$"], ["B"]);
      await held([...typed, ["see $ab$ here"], ["- item"], ["tab"]], "the same with the popup open");

      await edit("fm3");
      await left(7);
      await newBlock();
      await p.keyboard.type("C");
      typed.push(["see $ab$"], ["Chere"]);
      await held([...typed, ["- item"], ["tab"]], "the text after the formula goes on, without its space");

      await edit("fm4");
      await p.keyboard.type(" $y");
      await newBlock();
      await p.keyboard.type("D");
      typed.push(["- item $y$"], ["- D"]);
      await held([...typed, ["tab"]], "a list goes on past it");

      await edit("fm5");
      await p.keyboard.type(" $\\fr");
      await popup.waitFor();
      await p.keyboard.press("Tab");
      await popup.waitFor({ state: "hidden" });
      await until(async () => (await tree(alice2, id)).at(-1)?.content.startsWith("tab $\\frac"), { what: "Tab takes the completion, no block made" });
      assertNoProblems(p);
    } finally {
      await p.close();
    }
  });

  await step("notes: a stray $ on the line above and a backslash before the auto-paired closer cut no formula", async () => {
    const { p, held, edit, newBlock } = await keysPage("Stray dollars", [["sd1", "costs $5\nsee"], ["sd2", "next"]]);
    try {
      await edit("sd1");
      await p.keyboard.type(" $x^2");
      await newBlock();
      await p.keyboard.type("A");
      const typed = [["costs $5\nsee $x^2$"], ["A"]];
      await held([...typed, ["next"]], "the closer stays with its formula, whatever the line above holds");

      await edit("sd2");
      await p.keyboard.type(" $\\");
      await newBlock();
      await p.keyboard.type("B");
      await held([...typed, ["next $\\$"], ["B"]], "a backslash waiting for its command stays in the formula too");
      assertNoProblems(p);
    } finally {
      await p.close();
    }
  });

  await step("notes: in code and in $$ either Enter stays a line break; a highlight's row and a text box's do not split; /text drops a marker; a key that confirms a composed word makes no block", async () => {
    const place = { pageNumber: 3, width: 600, height: 800, boundingRect: { x1: 10, y1: 10, x2: 90, y2: 30 }, rects: [{ x1: 10, y1: 10, x2: 90, y2: 30 }] };
    const { p, held, edit, left, editing, newBlock } = await keysPage("Others", [
      ["ko1", "```\ncode\n```"], ["ko2", "$$\nx\n$$"],
      ["ko3", "on a passage", [], { pdf_position: place, quote: "a quoted passage" }],
      ["ko4", "boxed", [], { text_box: { x: 40, y: 40 }, pdf_page: 2 }],
      ["ko5", "- item"], ["ko6", "word"],
    ]);
    try {
      await edit("ko1");
      await p.keyboard.press("ArrowUp");
      await p.keyboard.press("End");
      await p.keyboard.press("Enter");
      await p.keyboard.press("Shift+Enter");
      await edit("ko2");
      await p.keyboard.press("ArrowUp");
      await p.keyboard.press("End");
      await p.keyboard.press("Enter");
      await p.keyboard.press("Shift+Enter");
      await held([["```\ncode\n\n\n```"], ["$$\nx\n\n\n$$"], ["on a passage"], ["boxed"], ["- item"], ["word"]], "a line break in each");

      await edit("ko3");
      await left(3);
      await newBlock();
      await edit("ko4");
      await left(2);
      await newBlock();
      await held([["```\ncode\n\n\n```"], ["$$\nx\n\n\n$$"], ["on a passage"], [""], ["boxed"], [""], ["- item"], ["word"]], "an empty sibling, the text whole");

      await edit("ko5");
      await p.keyboard.type(" /text");
      await p.locator(".slashMenu .slashMenuItem.selected .slashMenuName", { hasText: "/text" }).waitFor();
      await p.keyboard.press("Enter");
      await held([["```\ncode\n\n\n```"], ["$$\nx\n\n\n$$"], ["on a passage"], [""], ["boxed"], [""], ["item "], ["word"]], "the marker gone");

      // An Enter an input method takes for itself, as each browser marks it.
      // The same event unmarked does make a block.
      await edit("ko6");
      const press = (init) => p.evaluate((init) => {
        document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true, ...init }));
      }, init);
      await press({ isComposing: true });
      await press({ keyCode: 229 });
      await sleep(200);
      assertEq(await editing(), "ko6", "the caret stays");
      assertEq(await p.locator(".blockRow").count(), 8, "no block made");
      await press({});
      await until(async () => (await p.locator(".blockRow").count()) === 9, { what: "the key itself makes one" });
      assertNoProblems(p);
    } finally {
      await p.close();
    }
  });

  await step("notes: code in a list stays whole: Enter and a paste keep the item's indent, Tab indents, a $ stays one; dollars in inline code neither move a split nor stop one", async () => {
    const { p, held, edit, left } = await keysPage("Listed code", [
      ["lc1", "- ```\n  one\n  two\n  ```"], ["lc2", "  ```\n  one\n  ```"], ["lc3", "1. ```\n   one\n   ```"],
      ["lc4", "`$5` and `$6` tail"], ["lc5", "use `$$` for display"], ["lc6", "- item\n- $$\n  x\n  $$"],
    ]);
    const caret = (at) => p.evaluate((at) => {
      const view = document.querySelector(".blockEditorCm .cm-content").cmTile.root.view;
      view.dispatch({ selection: { anchor: at < 0 ? view.state.doc.length + at : at } });
    }, at);
    const paste = (text) => p.evaluate((text) => {
      const data = new DataTransfer();
      data.setData("text/plain", text);
      document.querySelector(".blockEditorCm .cm-content").dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    }, text);
    const rest = [["  ```\n  one\n  ```"], ["1. ```\n   one\n   ```"], ["`$5` and `$6` tail"], ["use `$$` for display"], ["- item\n- $$\n  x\n  $$"]];
    try {
      await edit("lc1");
      await caret("- ```\n  one".length);
      await p.keyboard.press("Enter");
      await p.keyboard.type("new");
      await held([["- ```\n  one\n  new\n  two\n  ```"], ...rest], "a code line in the list item, no block");
      await p.keyboard.press("Shift+Enter");
      await p.keyboard.press("Tab");
      await p.keyboard.type("$");
      await held([["- ```\n  one\n  new\n    $\n  two\n  ```"], ...rest], "the other Enter too; Tab indents and a $ stays one");
      await p.keyboard.press("Backspace");
      await paste("aa\n  bb");
      await held([["- ```\n  one\n  new\n    aa\n    bb\n  two\n  ```"], ...rest], "a paste keeps its lines in the item");
      await paste("cc\n");
      await held([["- ```\n  one\n  new\n    aa\n    bbcc\n  \n  two\n  ```"], ...rest], "and the line break it ends with");
      await edit("lc2");
      await caret(-"\n  ```".length);
      await p.keyboard.press("Enter");
      await edit("lc3");
      await caret(-"\n   ```".length);
      await p.keyboard.press("Enter");
      await edit("lc6");
      await caret(-"\n  $$".length);
      await p.keyboard.press("Enter");
      const whole = [["- ```\n  one\n  new\n    aa\n    bbcc\n  \n  two\n  ```"], ["  ```\n  one\n  \n  ```"], ["1. ```\n   one\n   \n   ```"],
        ["`$5` and `$6` tail"], ["use `$$` for display"], ["- item\n- $$\n  x\n  \n  $$"]];
      await held(whole, "an indented fence, a numbered one and math in a list");

      await edit("lc4");
      await left(" `$6` tail".length + 1);
      await p.keyboard.press("Enter");
      await held([...whole.slice(0, 3), ["`$5` an"], ["d `$6` tail"], ...whole.slice(4)], "the split at the caret, the code span whole");
      await edit("lc5");
      await left(3);
      await p.keyboard.press("Enter");
      await held([...whole.slice(0, 3), ["`$5` an"], ["d `$6` tail"], ["use `$$` for disp"], ["lay"], whole[5]], "a $$ in code stops no split");
      assertNoProblems(p);
    } finally {
      await p.close();
    }
  });

  await step("notes: a fence in a quote or a callout and a ~~~ one stay whole under either Enter, the quote's marks going on; a typed ~~~ fence is one block", async () => {
    const { p, held, edit, left, newBlock } = await keysPage("Fenced", [
      ["qf1", "> ```py\n> abcd\n> ```"], ["qf2", "> [!note] t\n> ```py\n> abcd\n> ```"], ["qf3", "~~~py\ncode1\ncode2\n~~~"], ["qf4", "word"],
    ]);
    const pres = (id) => p.locator(`.blockRowWrap[data-block-id="${id}"] > .blockRow .blockRendered pre`).count();
    try {
      for (const id of ["qf1", "qf2", "qf3"]) assertEq(await pres(id), 1, `${id} renders one code block`);
      for (const [id, up] of [["qf1", 1], ["qf2", 1], ["qf3", 2]]) {
        await edit(id);
        for (let i = 0; i < up; i++) await p.keyboard.press("ArrowUp");
        await p.keyboard.press("End");
        await left(2);
        await p.keyboard.press("Enter");
        await p.keyboard.press("Shift+Enter");
      }
      await held([
        ["> ```py\n> ab\n> \n> cd\n> ```"], ["> [!note] t\n> ```py\n> ab\n> \n> cd\n> ```"], ["~~~py\ncod\n\ne1\ncode2\n~~~"], ["word"],
      ], "a line break in each, no block made");
      await edit("qf4");
      await newBlock();
      await p.keyboard.type("~~~");
      await p.keyboard.press("Enter");
      await p.keyboard.type("x");
      await p.keyboard.press("Enter");
      await p.keyboard.type("~~~");
      await newBlock();
      await p.keyboard.type("next");
      await held([
        ["> ```py\n> ab\n> \n> cd\n> ```"], ["> [!note] t\n> ```py\n> ab\n> \n> cd\n> ```"], ["~~~py\ncod\n\ne1\ncode2\n~~~"], ["word"], ["~~~\nx\n~~~"], ["next"],
      ], "the typed fence is one block, and the key leaves it from its closing line");
      await p.evaluate(() => document.activeElement?.blur());
      await p.waitForSelector(".blockEditorCm", { state: "detached", timeout: 5000 });
      for (const id of ["qf1", "qf2", "qf3"]) assertEq(await pres(id), 1, `${id} still renders one code block`);
      assertNoProblems(p);
    } finally {
      await p.close();
    }
  });

  await step("notes: the new-block key on a hand-typed table is one undo step, the close's pretty-print with it", async () => {
    const raw = "| a | b |\n|---|---|\n| 1 | 22 |";
    const { id, p, held, edit, newBlock } = await keysPage("Table undo", [["ku1", raw]]);
    try {
      await edit("ku1");
      await newBlock();
      await until(async () => {
        const rows = await tree(alice2, id);
        return rows.length === 2 && rows[0].content !== raw;
      }, { what: "the table pretty-printed and the new block saved" });
      await p.keyboard.press("Control+z");
      await held([[raw]], "one undo takes back the block and the print together");
      assertNoProblems(p);
    } finally {
      await p.close();
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
      // Tab picks too, like every completion popup.
      await p2.keyboard.type(" [[quantum scr");
      await picker.locator(".refPopupItem.selected", { hasText: "Quantum Scratch" }).waitFor();
      await p2.keyboard.press("Tab");
      await until(async () => (await tree(alice2, src.id))[0]?.content === `start [[${target.id}]] [[${other.id}]]`, { what: "Tab links the page" });
      // "]]" typed after the exact title of one page links it; an unknown title stays text.
      await p2.keyboard.type(" [[quantum linkable target]] and [[Nothing By This Name]]");
      await until(async () => (await tree(alice2, src.id))[0]?.content === `start [[${target.id}]] [[${other.id}]] [[${target.id}]] and [[Nothing By This Name]]`,
        { what: "the typed title resolved to its page" });
      await closeEditor(p2);
      const r = row(p2, "start");
      assertEq(JSON.stringify(await r.locator(".blockRefChip").allInnerTexts()),
        JSON.stringify(["Quantum Linkable Target", "Quantum Scratch", "Quantum Linkable Target"]));
      assertEq(await r.locator(".unlinkedRef").innerText(), "Nothing By This Name", "the unknown title is an unlinked chip");
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
  });

  // Notion's /page: a new page inside this one (a sub-page, with no folders
  // of its own: it is filed where its top is), linked where the command was
  // typed and opened with its title ready.
  await step("notes: /page makes a page inside this one, links it where it was typed and opens it; Back shows the link under its title", async () => {
    const src = await alice2.api("/api/pages", { method: "POST", body: { title: "Subpage source" } });
    await alice2.file(src.id, { folders: ["Projects"] });
    const folder = await alice2.folder("Projects");
    await alice2.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "spblock", parent: src.id, position: "a0", content: "See" }] } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${src.id}`);
    try {
      await editRow(p2, "See");
      await p2.keyboard.type(" /page");
      await p2.waitForSelector(".slashMenu");
      assertEq(await p2.locator(".slashMenu .slashMenuItem.selected .slashMenuLabel").innerText(), "New page", "/page is the new page");
      await p2.keyboard.press("Enter");
      await p2.waitForSelector(".titleEdit", { timeout: 5000 });
      const made = await until(() => { const id = new URL(p2.url()).searchParams.get("block"); return id !== src.id && id; }, { what: "the new page opens" });
      await until(async () => (await tree(alice2, src.id))[0]?.content === `See [[${made}]]`, { what: "its link replaced the command" });
      const madeProps = (await alice2.api(`/api/blocks/${made}/subtree`)).block.properties;
      assertEq(madeProps.parent_page, src.id, "inside the source page");
      assertEq(madeProps.folders, undefined, "with no folders of its own");
      assertEq(JSON.stringify((await alice2.api(`/api/blocks/${src.id}/subtree`)).block.properties.folders), JSON.stringify([folder]),
        "the source page stays in its folder");
      await p2.keyboard.type("Meeting notes");
      await p2.keyboard.press("Enter");
      await until(async () => (await alice2.api(`/api/blocks/${made}/subtree`)).block.content === "Meeting notes", { what: "the title saved" });
      await p2.getByRole("button", { name: "Back", exact: true }).click();
      await until(() => new URL(p2.url()).searchParams.get("block") === src.id, { what: "Back returns to the source page" });
      await until(async () => (await row(p2, "See").locator(".blockRefChip").innerText()) === "Meeting notes", { what: "the link shows the title" });
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
      await p2.keyboard.press("Enter");
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
        { content: "- one", children: [{ content: "- one a", children: [] }, { content: "- one b", children: [] }] },
        { content: "- two", children: [] },
      ]), { what: "the list took the empty line's place, its markers kept" });
      await until(async () => (await p2.locator(".blockEditorCm .cm-content").innerText()).endsWith("two"),
        { what: "the caret ends in the last pasted block" });
      await closeEditor(p2);
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
  });

  // Copy as markdown writes a subtree as an outline, each block one "- "
  // (editor/sourceCopy.js): Paste as Blocks reads that text back as the blocks
  // it was, a list block among them, where the same lines from anywhere else,
  // or one block's own list, are a list.
  await step("notes: Copy as markdown of a subtree, then Paste as Blocks, gives the blocks back as they were", async () => {
    const pg = await alice2.api("/api/pages", { method: "POST", body: { title: "Copy blocks" } });
    await alice2.api(`/api/pages/${pg.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "cbtop", parent: pg.id, position: "a0", content: "plain top" },
      { op: "insert", id: "cblist", parent: "cbtop", position: "a0", content: "- a list block" },
      { op: "insert", id: "cbnum", parent: "cbtop", position: "a1", content: "1. a number" },
      { op: "insert", id: "cbown", parent: pg.id, position: "a1", content: "- its own\n- list" },
      { op: "insert", id: "cbend", parent: pg.id, position: "a2", content: "target" }] } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${pg.id}`);
    const copied = async (id) => {
      const wrap = p2.locator(`.sortableBlockWrap[data-block-id="${id}"]`).first();
      await wrap.hover();
      await wrap.locator(".dragHandle").first().click();
      await p2.getByRole("menuitem", { name: "Copy as markdown", exact: true }).click();
      await p2.getByText("Copied block as markdown").first().waitFor();
    };
    const pasteAsBlocks = async (text) => {
      await p2.evaluate((clip) => {
        const dt = new DataTransfer();
        dt.setData("text/plain", clip);
        document.activeElement.closest(".cm-content")
          .dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      }, text);
      await p2.locator(".slashMenu .slashMenuItem", { hasText: "Blocks" }).first().click();
    };
    const emptyLine = async (text) => {
      await editRow(p2, text);
      await p2.keyboard.press("End");
      await p2.keyboard.press("Enter");
      await p2.waitForFunction(() => {
        const ed = document.activeElement?.closest(".cm-content");
        return !!ed && (ed.querySelector(".cm-placeholder") != null || ed.textContent === "");
      }, null, { timeout: 5000 });
    };
    const subtree = { content: "plain top", children: [
      { content: "- a list block", children: [] }, { content: "1. a number", children: [] }] };
    const own = { content: "- its own\n- list", children: [] };
    try {
      await copied("cbtop");
      await emptyLine("target");
      await pasteAsBlocks("- plain top\n  - - a list block\n  - 1. a number");
      await p2.getByText("Pasted 3 blocks.").waitFor();
      await until(async () => same(await tree(alice2, pg.id), [subtree, own, { content: "target", children: [] }, subtree]),
        { what: "the subtree came back as the blocks it was" });
      await closeEditor(p2);

      await copied("cbown");
      await emptyLine("target");
      await pasteAsBlocks("- its own\n- list");
      await p2.getByText("Pasted 2 blocks.").waitFor();
      await until(async () => same((await tree(alice2, pg.id)).slice(3, 5).map((b) => b.content), ["- its own", "- list"]),
        { what: "one block's own list stayed a list" });
      await closeEditor(p2);
      assertNoProblems(p2);
    } finally {
      await p2.close();
    }
  });

  // A paste the server will not split (past 5 MB or MAX_BLOCKS blocks: a
  // 413) stays in its block as text, and the status line says so.
  await step("notes: Paste as Blocks the server refuses stays as text and says so", async () => {
    const pg = await alice2.api("/api/pages", { method: "POST", body: { title: "Paste refused" } });
    await alice2.api(`/api/pages/${pg.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "prfirst", parent: pg.id, position: "a0", content: "above the refused paste" }] } });
    const p2 = await openPage(ctx, `${server.base}/?ws=${second.id}&page=${pg.id}`);
    try {
      await p2.route("**/api/markdown-blocks*", (route) => route.fulfill({
        status: 413, contentType: "application/json", body: JSON.stringify({ detail: "text exceeds 5 MB" }) }));
      await editRow(p2, "above the refused paste");
      await p2.keyboard.press("Enter");
      await p2.waitForFunction(() => {
        const ed = document.activeElement?.closest(".cm-content");
        return !!ed && (ed.querySelector(".cm-placeholder") != null || ed.textContent === "");
      }, null, { timeout: 5000 });
      await p2.evaluate((text) => {
        const dt = new DataTransfer();
        dt.setData("text/plain", text);
        document.activeElement.closest(".cm-content")
          .dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
      }, "- kept one\n- kept two");
      await p2.locator(".slashMenu .slashMenuItem", { hasText: "Blocks" }).first().click();
      await p2.getByText("The paste is too large to split into blocks; it stays as text.").waitFor();
      await closeEditor(p2);
      await until(async () => {
        const blocks = await tree(alice2, pg.id);
        return blocks.length === 2 && /kept one[\s\S]*kept two/.test(blocks[1].content);
      }, { what: "the paste stayed in its block as text" });
      assertNoProblems(p2, [/markdown-blocks.* -> 413$/]);
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
      // the entries fold away (a closing body stays, inert, until it has folded)
      await until(async () => (await panel.locator(".backlinkItem").count()) === 0, { what: "folding hides the entries" });
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

  await step("notes: Enter-as-a-line-break preference, in the default workspace: the two keys swap, and a list goes on on either", async () => {
    // The Enter key is an account preference: the profile's copy wins over this browser's.
    const { value: profile } = await alice.api("/api/prefs/profile");
    const ctxD = await alice.context(browser);
    try {
      await alice.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), enterNewBlock: false } } });
      await ctxD.addInitScript(() => { try { localStorage.setItem("gamma-enter-new-block", "0"); } catch {} });
      const p = await openPage(ctxD, `${server.base}/?ws=${alice.defaultWs}`);
      const id = await newPageViaUi(p, "Default ws page");
      await p.keyboard.type("hello");
      await p.keyboard.press("Enter");
      await p.keyboard.type("there");
      await p.keyboard.press("Shift+Enter");
      await p.keyboard.type("- world");
      await p.keyboard.press("Enter");
      await p.keyboard.type("two");
      await p.keyboard.press("Shift+Enter");
      await p.keyboard.type("three");
      await closeEditor(p);
      await until(async () => same(await tree(alice, id), [
        { content: "hello\nthere", children: [] }, { content: "- world\n- two", children: [] }, { content: "- three", children: [] },
      ]), { what: "the line breaks and the blocks saved" });
      assertNoProblems(p);
    } finally {
      await ctxD.close();
      // Workers reuse Alice in later groups; Enter must keep its default meaning.
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
    await p.getByRole("menuitem", { name: "Edit markdown source" }).click();
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
    await p.locator(".lightbox").waitFor();
    await p.keyboard.press("Escape");
    await p.locator(".lightbox").waitFor({ state: "detached" });
    await img.click({ position: { x: 4, y: 190 } });
    await p.waitForSelector(".mdObjectSelected");
    assertEq(await p.locator(".blockEditorCm").count(), 0, "pressing the picture selects it, no editor");
    await p.keyboard.press("Delete");
    await until(async () => (await content(picBlock.id)) === "X", { what: "Delete removed the selected image" });

    // Right-click → "Edit markdown source": the editor opens with the caret
    // inside the table, so its source shows; with the caret past its end the
    // table is a widget again, and that widget drags like the frame does.
    await frameOf(tableBlock.id, "table").click({ button: "right", position: { x: 2, y: 2 } });
    await p.getByRole("menuitem", { name: "Edit markdown source" }).click();
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

  await step("notes: the object menu's Copy image — a PNG as it is, a JPEG as a PNG, an SVG as itself plus a PNG twice its size", async () => {
    const ctxC = await alice2.context(browser, { permissions: ["clipboard-read", "clipboard-write"] });
    const p = await openPage(ctxC, `${server.base}/?ws=${second.id}`);
    try {
      // A 40×30 JPEG, which the clipboard refuses as such.
      const jpeg = Buffer.from(await p.evaluate(async () => {
        const canvas = Object.assign(document.createElement("canvas"), { width: 40, height: 30 });
        canvas.getContext("2d").fillRect(0, 0, 40, 30);
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg"));
        return btoa(String.fromCharCode(...new Uint8Array(await blob.arrayBuffer())));
      }), "base64");
      const png = await alice2.upload("/api/upload-image", PNG_1PX, "copy.png", "image/png");
      const jpg = await alice2.upload("/api/upload-image", jpeg, "copy.jpg", "image/jpeg");
      const svg = await alice2.upload("/api/upload-image", Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="60" height="40"><rect width="60" height="40" fill="rgb(50, 100, 200)"/></svg>'), "copy.svg", "image/svg+xml");
      const block = await alice2.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: `![|100](${png.url})\n\n![|120](${jpg.url})\n\n![|120](${svg.url})` } });
      const withTable = await alice2.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "| a |\n|---|\n| 1 |" } });
      await p.goto(`${server.base}/?ws=${second.id}&page=${pageId}`);
      const imgs = p.locator(`.blockRowWrap[data-block-id="${block.id}"] img.mdImg`);
      await until(async () => (await imgs.evaluateAll((els) => els.filter((el) => el.complete && el.naturalWidth > 0).length)) === 3, { what: "the three pictures load" });
      // The clipboard's one item: each type, an image as its pixel size.
      const clipboard = () => p.evaluate(async () => {
        const [item] = await navigator.clipboard.read();
        const out = {};
        for (const type of item.types) {
          const blob = await item.getType(type);
          out[type] = type === "image/png" ? await createImageBitmap(blob).then((b) => `${b.width}x${b.height}`) : await blob.text();
        }
        return out;
      });
      const copyNth = async (n) => {
        await p.evaluate(() => navigator.clipboard.writeText("before"));
        const box = await imgs.nth(n).boundingBox();
        await imgs.nth(n).click({ button: "right", position: { x: box.width / 2, y: box.height - 4 } }); // clear of the toolbar and the grips
        await p.getByRole("menuitem", { name: "Copy image", exact: true }).click();
        await until(async () => !(await clipboard())["text/plain"], { what: `picture ${n} reached the clipboard` });
        return clipboard();
      };
      assertEq(JSON.stringify(await copyNth(0)), JSON.stringify({ "image/png": "1x1" }), "a PNG keeps its own pixels, not its shown size");
      await p.getByText("Copied image", { exact: true }).waitFor();
      assertEq(JSON.stringify(await copyNth(1)), JSON.stringify({ "image/png": "40x30" }), "a JPEG becomes a PNG of the same size");
      const vector = await copyNth(2);
      assertEq(vector["image/png"], "240x160", "an SVG's PNG is twice its shown size");
      assert(/<svg[^>]* width="120" height="80"/.test(vector["image/svg+xml"]) && vector["image/svg+xml"].includes('viewBox="0 0 60 40"'),
        `the SVG at its shown size, scaled: ${vector["image/svg+xml"]}`);
      // A table has no picture to copy.
      await p.locator(`.blockRowWrap[data-block-id="${withTable.id}"] .mdObject-table`).click({ button: "right", position: { x: 2, y: 2 } });
      await p.getByRole("menuitem", { name: "Copy as markdown", exact: true }).waitFor();
      assertEq(await p.getByRole("menuitem", { name: "Copy image", exact: true }).count(), 0, "no Copy image on a table");
      await p.keyboard.press("Escape");
      assertNoProblems(p);
    } finally { await ctxC.close(); }
  });

  return { second, alice2, pageId };
}
