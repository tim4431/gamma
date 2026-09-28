// Two people on one page of a shared workspace: presence (header stack, row
// chips), each other's ops arriving live, concurrent edits to different
// blocks converging, same-block typing merging both people's text, one
// person's undo leaving the other's edit alone, a rename reaching the other
// tab, edits made offline landing once the network is back (and the notice
// going with them), a note moved in with a reload surviving Ctrl+Z, a failed
// refresh keeping the page, undo never deleting what the other wrote in our
// note, an indent around an open editor keeping it open, typing at one caret
// keeping both people's keystrokes, a two-batch paste leaving the idle tab
// silent, typing kept through Recently deleted and a restore, and a
// highlight one person makes on the PDF showing up for the other.
import { tree, same, editRow, closeEditor } from "./notes.mjs";
import { waitForPdf, selectPdfText } from "./pdf.mjs";

export async function collabScenarios({ server, browser, alice, bob, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage }) {
  // A shared workspace: alice owns it, bob edits. (manage.py: admin-only setup.)
  const out = server.manage("create-workspace", "Team", "alice", "shared");
  const teamId = (out.match(/workspace (\S+)/) || [])[1];
  assert(teamId, `shared workspace id from: ${out}`);
  server.manage("set-member", teamId, "bob", "editor");
  const aliceT = Object.assign(Object.create(Object.getPrototypeOf(alice)), alice, { ws: teamId });
  const bobT = Object.assign(Object.create(Object.getPrototypeOf(bob)), bob, { ws: teamId });

  const created = await aliceT.api("/api/pages", { method: "POST", body: { title: "Team notes" } });
  const pageId = created.id;
  await aliceT.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "alpha" } });
  await aliceT.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "beta" } });

  const ctxA = await aliceT.context(browser);
  const ctxB = await bobT.context(browser);
  const url = `${server.base}/?page=${pageId}&ws=${teamId}`;
  const A = await openPage(ctxA, url);
  const B = await openPage(ctxB, url);
  const bodyHas = (p, s) => until(async () => (await p.textContent("body")).includes(s), { what: `"${s}" visible`, timeout: 10000 });
  const serverHas = (s) => until(async () => JSON.stringify(await tree(aliceT, pageId)).includes(s), { what: `server has "${s}"` });
  const pill = async (p) => (await p.$$eval(".statusPill", (els) => els.map((e) => e.textContent).join(" | "))) || "";
  // A page of its own for a step, open in both tabs: its id and a tree reader.
  const scratchPage = async (title, contents, tabs = [A, B]) => {
    const pg = await aliceT.api("/api/pages", { method: "POST", body: { title } });
    for (const content of contents) await aliceT.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content } });
    for (const p of tabs) {
      await p.goto(`${server.base}/?page=${pg.id}&ws=${teamId}`);
      if (contents.length) await bodyHas(p, contents[contents.length - 1]);
    }
    return { id: pg.id, tree: () => tree(aliceT, pg.id) };
  };

  await step("collab: both see each other in the presence stack", async () => {
    await bodyHas(A, "beta"); await bodyHas(B, "beta");
    await until(async () => (await A.$$(".presenceBar .peerAvatar")).length >= 1, { what: "alice sees a peer" });
    await until(async () => (await B.$$(".presenceBar .peerAvatar")).length >= 1, { what: "bob sees a peer" });
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: bob's typing shows up live for alice, with a chip on the row", async () => {
    await editRow(B, "beta");
    await until(async () => (await A.$$(".blockRowWrap .peerChips .peerAvatar")).length >= 1, { what: "row chip on alice's side" });
    await B.keyboard.type(" from bob");
    await bodyHas(A, "beta from bob");
    // Alice isn't editing: bob's caret is drawn over her rendered row, right
    // after what he typed.
    await until(() => A.evaluate(() => {
      const caret = document.querySelector(".blockRendered .peerCaret");
      if (!caret) return false;
      const walker = document.createTreeWalker(caret.parentElement, NodeFilter.SHOW_TEXT);
      let n = walker.nextNode();
      while (n && !n.data.includes("from bob")) n = walker.nextNode();
      if (!n) return false;
      const r = document.createRange(); r.selectNodeContents(n);
      return Math.abs(caret.getBoundingClientRect().left - r.getBoundingClientRect().right) < 4;
    }), { what: "bob's caret at the end of his text on alice's rendered row" });
    await closeEditor(B);
    await until(async () => !(await A.$(".blockRendered .peerCaret")), { what: "bob's caret gone once he stops editing" });
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: concurrent edits to different blocks converge on both screens and the server", async () => {
    await editRow(A, "alpha");
    await editRow(B, "beta from bob");
    await A.keyboard.type(" (alice)");
    await B.keyboard.type(" (bob)");
    await closeEditor(A);
    await closeEditor(B);
    const want = [{ content: "alpha (alice)", children: [] }, { content: "beta from bob (bob)", children: [] }];
    await until(async () => same(await tree(aliceT, pageId), want), { what: `server tree ${JSON.stringify(want)}` });
    await bodyHas(A, "beta from bob (bob)"); await bodyHas(A, "alpha (alice)");
    await bodyHas(B, "alpha (alice)"); await bodyHas(B, "beta from bob (bob)");
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: same-block typing keeps both people's text (three-way merge)", async () => {
    await editRow(A, "alpha (alice)");
    await editRow(B, "alpha (alice)");
    const saved = (page, content) => page.waitForResponse((response) =>
      response.url().includes(`/api/pages/${pageId}/ops`) && response.request().method() === "POST"
      && response.request().postDataJSON()?.ops?.some((op) => op.content === content));
    const saves = [saved(A, "alpha (alice) A1"), saved(B, "alpha (alice) B1")];
    await A.keyboard.type(" A1");
    await B.keyboard.type(" B1");
    await closeEditor(A);
    await closeEditor(B);
    for (const response of await Promise.all(saves)) assert(response.ok(), "both concurrent edits saved");
    // Both edits were made from the same base: the second is merged in, not
    // dropped — whichever order the server took them.
    const final = await until(async () => {
      const t = await tree(aliceT, pageId);
      const c = t[0]?.content || "";
      if (!/^alpha \(alice\)( A1 B1| B1 A1)$/.test(c)) return null;
      const bodies = await Promise.all([A.textContent("body"), B.textContent("body")]);
      return bodies.every((body) => body.includes(c)) ? c : null;
    }, { what: "both screens and the server hold both edits" });
    assertNoProblems(A); assertNoProblems(B);
    return final;
  });

  await step("collab: simultaneous edits at both ends of one long block both survive", async () => {
    const blk = await aliceT.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: ["head line", "middle line", "tail line"].join("\n") } });
    await bodyHas(A, "tail line"); await bodyHas(B, "tail line");
    await editRow(A, "tail line");
    await A.keyboard.press("Control+Home");
    await editRow(B, "tail line");
    await B.keyboard.press("Control+End");
    await A.keyboard.type("AA ", { delay: 30 });
    await B.keyboard.type(" BB", { delay: 30 });
    const want = ["AA head line", "middle line", "tail line BB"].join("\n");
    await until(async () => (await tree(aliceT, pageId)).some((b) => b.content === want), { what: "server holds both edits" });
    await bodyHas(A, "tail line BB"); await bodyHas(A, "AA head line");
    await bodyHas(B, "AA head line"); await bodyHas(B, "tail line BB");
    // Each editor still holds the merged text with the caret where its owner typed.
    await A.keyboard.type("!");
    await B.keyboard.type("?");
    const want2 = ["AA !head line", "middle line", "tail line BB?"].join("\n");
    await until(async () => (await tree(aliceT, pageId)).some((b) => b.content === want2), { what: "carets stayed put through the merge" });
    await closeEditor(A);
    await closeEditor(B);
    await aliceT.api(`/api/blocks/${blk.id}`, { method: "DELETE" });
    await until(async () => !(await B.textContent("body")).includes("tail line"), { what: "the scratch block gone" });
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: a peer's caret lands where they typed, mid-block, and the other caret shifts along", async () => {
    // Where an editor draws the remote caret: (line, column) of the widget.
    const remoteCaret = (p) => p.evaluate(() => {
      const ed = document.querySelector(".blockEditorCm .cm-content");
      const w = ed?.querySelector(".cmRemoteCaret");
      if (!w) return null;
      const lines = [...ed.querySelectorAll(".cm-line")];
      const i = lines.findIndex((l) => l.contains(w));
      const r = document.createRange(); r.setStart(lines[i], 0); r.setEndBefore(w);
      return { line: i + 1, col: r.toString().length };
    });
    const multi = await aliceT.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: ["first line here", "second line", "", "last line"].join("\n") } });
    await bodyHas(A, "last line"); await bodyHas(B, "last line");
    // Alice keeps the editor open at the very end; bob types at the end of line 1.
    await editRow(A, "last line");
    await A.keyboard.press("Control+End");
    await editRow(B, "last line");
    await B.keyboard.press("Control+Home");
    await B.keyboard.press("End");
    await until(async () => JSON.stringify(await remoteCaret(A)) === JSON.stringify({ line: 1, col: 15 }), { what: "bob's caret at the end of line 1 on alice's side" });
    await B.keyboard.type("xyz", { delay: 40 });
    await bodyHas(A, "first line herexyz");
    const caretAt = (p, want) => async () => JSON.stringify(await remoteCaret(p)) === JSON.stringify(want);
    // Offsets past the typed text used to land it on line 2 and stay there.
    // (The caret rides on the batch that carried the text, so once the text
    // is there the placement is final — nothing later moves it.)
    await until(caretAt(A, { line: 1, col: 18 }), { what: "bob's caret after typing, on alice's side" });
    // Alice's caret, after the insertion, moved along with the text on bob's side.
    await until(caretAt(B, { line: 4, col: 9 }), { what: "alice's caret on bob's side" });
    await closeEditor(A);
    await closeEditor(B);
    await aliceT.api(`/api/blocks/${multi.id}`, { method: "DELETE" });
    await until(async () => !(await B.textContent("body")).includes("last line"), { what: "the scratch block gone" });
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: alice's undo reverts only her own edit", async () => {
    const alpha = (await tree(aliceT, pageId))[0].content;
    await editRow(A, alpha);
    await A.keyboard.type(" again");
    await serverHas(`${alpha} again`);
    await editRow(B, "beta from bob (bob)");
    await B.keyboard.type(" later");
    await closeEditor(B);
    await serverHas("(bob) later");
    await bodyHas(A, "(bob) later");
    await A.keyboard.press("Control+z");
    await closeEditor(A);
    const want = [{ content: alpha, children: [] }, { content: "beta from bob (bob) later", children: [] }];
    await until(async () => same(await tree(aliceT, pageId), want), { what: `after undo ${JSON.stringify(want)}` });
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: renaming the page in one tab updates the other's title and tab", async () => {
    await A.click("h3.titleText");
    await A.waitForSelector(".titleEdit");
    await A.keyboard.press("Control+a");
    await A.keyboard.type("Team notes renamed");
    await A.keyboard.press("Enter");
    await until(async () => (await B.textContent("h3.titleText")) === "Team notes renamed", { what: "bob's title" });
    await until(async () => (await B.textContent(".tabStrip")).includes("Team notes renamed"), { what: "bob's tab label" });
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: an edit made offline is kept and lands once the network is back", async () => {
    await ctxA.setOffline(true);
    await editRow(A, "(bob) later");
    await A.keyboard.type(" offline");
    // Closing the editor flushes at once; offline, that POST fails in the browser.
    const attempted = A.waitForEvent("requestfailed", { predicate: (r) => r.url().includes("/ops"), timeout: 8000 });
    await closeEditor(A);
    await attempted;
    assert(!JSON.stringify(await tree(aliceT, pageId)).includes("offline"), "nothing reached the server while offline");
    // the wait is told in words (not the browser's "Failed to fetch") …
    await until(async () => (await pill(A)).includes("Not saved yet — the server can't be reached"), { what: "the waiting notice" });
    await ctxA.setOffline(false);
    await serverHas("(bob) later offline");
    await bodyHas(B, "(bob) later offline");
    // … and goes with the save
    await until(async () => !(await pill(A)).includes("Not saved"), { what: "no save notice once saved", timeout: 3000 });
    // The failed POSTs and the dropped socket are expected while offline.
    assertNoProblems(A, [/ERR_INTERNET_DISCONNECTED|Failed to fetch|WebSocket|net::ERR/]);
    assertNoProblems(B);
  });

  await step("collab: a block bob deletes disappears for alice", async () => {
    const wrap = B.locator(".sortableBlockWrap", { hasText: "alpha" }).first();
    await wrap.hover();
    await wrap.locator(".dragHandle").click();
    await B.locator(".ctxMenuItem", { hasText: "Delete" }).click();
    await until(async () => !(await A.textContent("body")).includes("alpha"), { what: "row gone on alice's side" });
    await until(async () => same(await tree(aliceT, pageId), [{ content: "beta from bob (bob) later offline", children: [] }]), { what: "server tree" });
    assertNoProblems(A); assertNoProblems(B);
  });

  // A note moved here from another page (an import, the AI's move_block)
  // arrives as a reload; the undo stack from before it must not delete it.
  const moveIn = async (content) => {
    const elsewhere = await aliceT.api("/api/pages", { method: "POST", body: { title: `Elsewhere: ${content}` } });
    const note = await aliceT.api("/api/blocks", { method: "POST", body: { parent_id: elsewhere.id, content } });
    await aliceT.api(`/api/blocks/${note.id}/reorder`, { method: "POST", body: { parent_id: pageId, before: null, after: null } });
  };

  await step("collab: Ctrl+Z after a note arrived with a reload never deletes it", async () => {
    const text = (await tree(aliceT, pageId))[0].content;
    await editRow(A, text);
    await A.keyboard.type(" undoable");
    await closeEditor(A);
    await serverHas(`${text} undoable`);
    await moveIn("a note moved in");
    await bodyHas(A, "a note moved in");
    await A.keyboard.press("Control+z");
    await sleep(800);
    assert(JSON.stringify(await tree(aliceT, pageId)).includes("a note moved in"), "the moved note survives the undo");
    await bodyHas(A, "a note moved in");
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: a refresh that fails keeps the page on screen and tries again", async () => {
    let aborted = 0;
    await A.route("**/subtree*", (route) => (aborted++ === 0 ? route.abort() : route.continue()));
    await moveIn("arrives after a retry");
    await until(async () => aborted >= 1, { what: "the refresh was cut off" });
    // the page stays (never an empty page), with the notice
    assert((await A.textContent("body")).includes("a note moved in"), "the notes stay on screen");
    await until(async () => (await A.textContent(".statusPill").catch(() => "") || "").includes("Couldn't refresh"), { what: "the refresh notice" });
    await bodyHas(A, "arrives after a retry"); // the retry
    await A.unroute("**/subtree*");
    assertNoProblems(A, [/net::ERR|Failed to fetch|subtree/]); assertNoProblems(B);
  });

  await step("collab: undo takes back only alice's typing, never the text bob wrote in her note", async () => {
    const pg = await scratchPage("Undo with others", ["existing block"]);
    await editRow(A, "existing block");
    await A.keyboard.press("Shift+Enter"); // a new note …
    await A.keyboard.type("alice made this"); // … and typing in it
    await closeEditor(A);
    await bodyHas(B, "alice made this");
    await editRow(B, "alice made this");
    await B.keyboard.type(" and bob wrote this");
    await closeEditor(B);
    await bodyHas(A, "alice made this and bob wrote this");
    const texts = async () => (await pg.tree()).map((b) => b.content);
    await A.keyboard.press("Control+z");
    await until(async () => same(await texts(), ["existing block", " and bob wrote this"]), { what: "alice's typing taken out, bob's kept" });
    await A.keyboard.press("Control+z"); // would delete the note bob wrote in
    await until(async () => (await pill(A)).includes("Can't undo: someone else changed this since"), { what: "the undo says why it stops" });
    await sleep(500);
    assert(same(await texts(), ["existing block", " and bob wrote this"]), "bob's text is never deleted");
    await until(async () => !(await B.textContent("body")).includes("alice made this"), { what: "bob's screen shows the undo" });
    await bodyHas(B, "and bob wrote this");
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: bob indenting the note around alice's keeps her editor open, typing and all", async () => {
    const pg = await scratchPage("Moves while typing", ["sibling above", "parent node"]);
    const parentId = (await aliceT.api(`/api/blocks/${pg.id}/subtree`)).block.children[1].id;
    await aliceT.api("/api/blocks", { method: "POST", body: { parent_id: parentId, content: "child one" } });
    await bodyHas(A, "child one"); await bodyHas(B, "child one");
    await editRow(A, "child one");
    await A.keyboard.type(" typing");
    await editRow(B, "parent node");
    await B.keyboard.press("Home");
    await B.keyboard.press("Tab"); // the parent (and alice's note in it) moves under "sibling above"
    await B.evaluate(() => document.activeElement?.blur());
    await until(async () => (await pg.tree())[0]?.children?.[0]?.content === "parent node", { what: "bob's indent on the server" });
    await until(async () => (await A.textContent(".blockEditorCm .cm-content").catch(() => "")) === "child one typing"
      && (await A.evaluate(() => !!document.activeElement?.closest(".blockEditorCm"))), { what: "alice's editor open and focused after the move" });
    await A.keyboard.type(" more");
    await closeEditor(A);
    const want = [{ content: "sibling above", children: [{ content: "parent node", children: [{ content: "child one typing more", children: [] }] }] }];
    await until(async () => same(await pg.tree(), want), { what: `server tree ${JSON.stringify(want)}` });
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: typing at one caret keeps both people's keystrokes", async () => {
    const pg = await scratchPage("One caret", ["tag"]);
    // bob's saves reach the server a little later: alice's always land first
    await B.route("**/api/pages/*/ops", async (route) => { await sleep(150); await route.continue(); });
    await editRow(A, "tag");
    await editRow(B, "tag");
    await A.keyboard.type("x");
    await B.keyboard.type("Y");
    await sleep(700);
    await A.keyboard.type("xxxx");
    await B.keyboard.type("YYYY");
    await closeEditor(A);
    await closeEditor(B);
    await B.unroute("**/api/pages/*/ops");
    const final = await until(async () => {
      const c = (await pg.tree())[0]?.content || "";
      return (c.match(/x/g) || []).length === 5 && (c.match(/Y/g) || []).length === 5 ? c : null;
    }, { what: "five x's and five Y's on the server" });
    await bodyHas(A, final); await bodyHas(B, final);
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: a long paste arriving in two batches makes the idle tab send nothing", async () => {
    await scratchPage("Two-batch paste", ["paste here"]);
    const posted = [];
    const onRequest = (r) => { if (r.url().includes("/ops") && r.method() === "POST") posted.push(r.postDataJSON().ops.length); };
    B.on("request", onRequest);
    const lines = Array.from({ length: 520 }, (_, i) => `- line ${i}`);
    await editRow(A, "paste here");
    await A.keyboard.press("Shift+Enter");
    await A.evaluate((text) => {
      const dt = new DataTransfer();
      dt.setData("text/plain", text);
      document.querySelector(".blockEditorCm .cm-content")
        .dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    }, lines.join("\n"));
    await A.locator(".slashMenu .slashMenuItem", { hasText: "Blocks" }).first().click({ timeout: 5000 });
    await until(async () => (await B.$$(".blockRowWrap")).length >= 521, { what: "the whole paste on bob's screen", timeout: 20000 });
    await sleep(1500);
    B.off("request", onRequest);
    assert(!posted.length, `bob's idle tab posted ops: ${JSON.stringify(posted)}`);
    await closeEditor(A).catch(() => {});
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("collab: typing in a page moved to Recently deleted is kept, and saved once it is restored", async () => {
    const pg = await scratchPage("Trashed while typing", ["bob types here"], [B]);
    await editRow(B, "bob types here");
    await B.keyboard.type(" before");
    await until(async () => (await pg.tree())[0]?.content === "bob types here before", { what: "saved before the trash" });
    await aliceT.api(`/api/blocks/${pg.id}`, { method: "DELETE" }); // to Recently deleted
    await until(async () => !!(await B.$(".pageTrashedNotice")), { what: "bob sees the page went to Recently deleted" });
    await B.keyboard.type(" after");
    await closeEditor(B);
    await bodyHas(B, "bob types here before after"); // kept on screen, waiting
    await aliceT.api(`/api/trash/${pg.id}/restore`, { method: "POST" });
    await until(async () => (await pg.tree())[0]?.content === "bob types here before after", { what: "the waiting edit saved after the restore" });
    await until(async () => !(await B.$(".pageTrashedNotice")), { what: "the notice gone" });
    assertNoProblems(B);
  });

  await step("collab: a highlight bob makes on the PDF appears on alice's page", async () => {
    const pdf = makePdf([["Shared paper about tensor networks", "and matrix product states"]]);
    const up = await aliceT.upload("/api/uploads", pdf, "shared.pdf", "application/pdf");
    const paper = await aliceT.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Shared paper", source_url: up.source_url } });
    const purl = `${server.base}/?page=${paper.id}&ws=${teamId}`;
    await A.goto(purl); await B.goto(purl);
    await waitForPdf(A, 1); await waitForPdf(B, 1);
    await selectPdfText(B, 1, "tensor networks");
    await B.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
    await B.locator(".plainTip .colorBtn").first().click();
    await A.waitForSelector('[data-page="1"] [data-hl-id]', { timeout: 10000 });
    await until(async () => ((await A.textContent(".blockRow .blockQuote").catch(() => "")) || "").includes("tensor networks"), { what: "quote row on alice's side" });
    assertNoProblems(A); assertNoProblems(B);
  });

  await ctxA.close();
  await ctxB.close();
  return { teamId, teamPageId: pageId };
}
