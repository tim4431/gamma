// Comments (docs/dev/comments.md "In the app"): two editors and a viewer in
// a shared workspace, and a visitor through a share link with no account.
// A thread on a note from its row control (the popover opening in place,
// the @ menu of people), the reply showing live in the other tab; resolve,
// and the Comments window's Resolved tab bringing it back to reopen; the
// page's own thread and a highlight's, with the PDF badge; edit and delete
// one's own; Ctrl+Shift+M from an open editor and Escape back to the
// control; a viewer commenting from a read-only row; the link visitor
// reading only; ?thread= opening the thread, and a note thread's copied
// link opening it through a folder share; a reply left unsent kept in its
// field; the field as a note's editor ([[ links, @ people, $…$ with its
// preview, Enter and Shift+Enter, the "/" menu); on a phone, a note's thread link
// bringing the notes over the PDF; the selection popup opening at the
// pointer, its Comment making a highlight drawn as a commented passage, and
// an abandoned one leaving none; the PDF margin's cards where the page has
// room (level with their highlights, apart, a press opening the thread, a
// reply, Resolve, the selection's Comment as a card), and the badge and
// popover without room; the pages' margins as one column (a card grown at
// a page's foot pushing the next page's down), and a card pressed with a
// pen or the Text tool armed opening and drawing nothing.
import { editRow } from "./notes.mjs";
import { dragSelectPdfText, selectPdfText, waitForPdf } from "./pdf.mjs";
import { Account, assertOpensInPlace, sleep, wanted } from "../harness.mjs";

export async function commentScenarios({ server, browser, makePdf, step, until, assert, assertEq, assertNoProblems, openPage, flags }) {
  if (!wanted("comments")) return;
  for (const [name, pw] of [["cm-ann", "cm-ann-pw"], ["cm-ben", "cm-ben-pw"], ["cm-vic", "cm-vic-pw"]]) {
    await server.manage("create-user", name, pw);
  }
  const out = await server.manage("create-workspace", "Comment lab", "cm-ann", "shared");
  const wsId = (out.match(/workspace (\S+)/) || [])[1];
  assert(wsId, `shared workspace id from: ${out}`);
  await server.manage("set-member", wsId, "cm-ben", "editor");
  await server.manage("set-member", wsId, "cm-vic", "viewer");
  const login = async (name) => {
    const a = await new Account(server, name, `${name}-pw`).login();
    a.ws = wsId;
    return a;
  };
  const ann = await login("cm-ann");
  const ben = await login("cm-ben");
  const vic = await login("cm-vic");

  const pg = await ann.api("/api/pages", { method: "POST", body: { title: "Review draft" } });
  const appendix = await ann.api("/api/pages", { method: "POST", body: { title: "Appendix tables" } });
  await ann.api(`/api/pages/${pg.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
    { op: "insert", id: "cmnote1", parent: pg.id, position: "a0", content: "The first claim" },
    { op: "insert", id: "cmnote2", parent: pg.id, position: "a1", content: "The second claim" },
  ] } });
  const url = `${server.base}/?ws=${wsId}&page=${pg.id}`;
  const threads = async () => (await ann.api(`/api/pages/${pg.id}/comments?state=all`)).threads;
  const rowOf = (page, text) => page.locator(".blockRow", { hasText: text }).first();
  const control = (page, text) => rowOf(page, text).locator(".commentCtl");
  const popover = (page) => page.locator(".commentPopover");
  const openControl = async (page, text) => {
    await rowOf(page, text).hover();
    await control(page, text).click();
    await popover(page).waitFor();
  };
  // A comment's field is the note editor: its text box is the .cm-content.
  const field = (scope) => scope.locator(".commentField .cm-content");
  // its text (its empty line shows the placeholder)
  const fieldText = (f) => f.evaluate((el) => (el.querySelector(".cm-placeholder") ? "" : el.innerText.trim()));
  // The composer takes the focus a frame after the popover is placed.
  const focused = (f, what) => until(async () => f.evaluate((el) => el === document.activeElement).catch(() => false), { what });
  // A thread's reply field shows once the thread is pressed (as in Notion):
  // a press on its first comment's text, then its field.
  const openReply = async (scope) => {
    await scope.locator(".commentThread .commentBody").first().click();
    const f = field(scope.locator(".commentThread"));
    await f.first().waitFor();
    return f;
  };
  // --keep: the field's widgets, for the record
  const shot = async (page, name) => {
    if (flags?.keep) await page.screenshot({ path: `${server.dir}/${name}.png`, animations: "disabled" });
  };
  const closePopover = async (page) => {
    await popover(page).locator(".commentPopHead .uiClose").click();
    await popover(page).waitFor({ state: "detached" });
  };

  const ctxA = await ann.context(browser);
  const ctxB = await ben.context(browser);
  const A = await openPage(ctxA, url);
  const B = await openPage(ctxB, url);
  await rowOf(A, "The first claim").waitFor();
  await rowOf(B, "The first claim").waitFor();

  await step("comments: a note's row control opens its popover in place; a thread with an @mention; the reply shows live in the other tab", async () => {
    await rowOf(A, "The first claim").hover();
    await assertOpensInPlace(A, ".commentPopover", () => control(A, "The first claim").click(), { what: "the thread popover" });
    await focused(field(popover(A).locator(".commentNew")), "the new comment's field takes the focus");
    await A.keyboard.type("Is this sourced? @cm-b");
    const selected = A.locator(".mentionMenu .refPopupItem.selected .refPopupText");
    await until(async () => (await selected.innerText().catch(() => "")) === "cm-ben", { what: "the @ menu offers the person" });
    assertEq(await A.locator(".mentionMenu .refPopupHead").allInnerTexts().then((h) => h.join("|")), "People", "no date or page answers the name");
    await A.keyboard.press("Enter");
    await A.keyboard.type("please check");
    await A.keyboard.press("Shift+Enter");
    await until(async () => (await threads()).some((th) => th.block_id === "cmnote1"
      && th.comments[0].body === "Is this sourced? @cm-ben please check" && th.comments[0].author === "cm-ann"), { what: "the thread is stored" });
    await until(async () => (await control(A, "The first claim").innerText()) === "1", { what: "the row counts its open thread" });
    assertEq(await popover(A).locator(".commentBody .mentionChip").innerText(), "@cm-ben", "the mention renders as a chip");

    // Ben's tab hears of it from the page's room.
    await until(async () => (await control(B, "The first claim").innerText().catch(() => "")) === "1", { what: "the count reaches the other tab", timeout: 10000 });
    await openControl(B, "The first claim");
    assertEq(await field(popover(B).locator(".commentThread")).count(), 0, "no reply field until the thread is pressed");
    const replyB = await openReply(popover(B));
    await focused(replyB, "the pressed thread's reply field takes the focus");
    // a word typed over, apart from the typing before it: an edit whose
    // undo would put text back
    await B.keyboard.type("Yes, see the annex");
    await sleep(700);
    for (let i = 0; i < 5; i++) await B.keyboard.press("Shift+ArrowLeft");
    await B.keyboard.type("appendix");
    await B.keyboard.press("Shift+Enter");
    await until(async () => (await popover(A).innerText()).includes("Yes, see the appendix"), { what: "the reply shows live in the open popover", timeout: 10000 });
    // The emptied field's history starts afresh: an undo brings back none of the sent text.
    await until(async () => (await fieldText(replyB)) === "", { what: "the reply field empties" });
    await B.keyboard.press("Control+Z");
    assertEq(await fieldText(replyB), "", "an undo after sending restores nothing");
    await closePopover(B);
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("comments: Resolve takes the thread off the row; the window's Resolved tab holds it, and Reopen brings it back", async () => {
    await popover(A).locator('.commentResolve[aria-label="Resolve"]').click();
    await until(async () => (await threads()).find((th) => th.block_id === "cmnote1")?.resolved_by === "cm-ann", { what: "resolved on the server" });
    await until(async () => !(await control(A, "The first claim").evaluate((el) => el.classList.contains("has"))), { what: "the row's count goes" });
    await closePopover(A);

    await A.getByRole("button", { name: "View", exact: true }).click();
    await A.locator(".menuPopover .ctxMenuItem", { hasText: "Comments" }).click();
    await A.keyboard.press("Escape"); // the View menu closes
    const panel = A.locator(".commentsPanel");
    await panel.waitFor();
    await panel.locator(".setEmpty", { hasText: "No open comments on this page." }).waitFor();
    await A.locator(".dockWindow:has(.commentsPanel) .segGroup button", { hasText: "Resolved (1)" }).click();
    const item = panel.locator(".commentsPanelRow", { hasText: "The first claim" });
    await item.waitFor();
    assert((await item.innerText()).includes("1 reply"), "the row counts the reply");
    await item.click();
    await popover(A).locator(".commentThread.resolved", { hasText: "Resolved by cm-ann" }).waitFor();
    await popover(A).locator('.commentResolve[aria-label="Reopen"]').click();
    await until(async () => (await control(A, "The first claim").innerText()) === "1", { what: "reopened: the count is back" });
    await closePopover(A);
    await A.locator(".dockWindow:has(.commentsPanel) .segGroup button", { hasText: "Open (1)" }).click();
    await panel.locator(".commentsPanelRow", { hasText: "The first claim" }).waitFor();
    assertNoProblems(A);
  });

  await step("comments: Ctrl+Shift+M from an open editor starts a thread; Escape closes it and gives the control the focus", async () => {
    await editRow(A, "The second claim");
    await A.keyboard.press("Control+Shift+M");
    await focused(field(popover(A).locator(".commentNew")), "the field takes the focus");
    await A.keyboard.press("Escape");
    await popover(A).waitFor({ state: "detached" });
    assertEq(await A.evaluate(() => document.activeElement?.dataset.commentFor || ""), "cmnote2", "the focus is on the row's control");
    assertNoProblems(A);
  });

  await step("comments: the page's own thread, from the title row's control", async () => {
    await assertOpensInPlace(A, ".commentPopover", () => A.locator(".pageCommentBtn").click(), { what: "the page's thread popover" });
    assertEq(await popover(A).locator(".commentPopTitle").innerText(), "Comments on this page");
    await focused(field(popover(A).locator(".commentNew")), "ready to write");
    await A.keyboard.type("Overall: good");
    await A.keyboard.press("Shift+Enter");
    await until(async () => (await threads()).some((th) => th.block_id === pg.id && th.comments[0].body === "Overall: good"), { what: "stored on the page's root" });
    await until(async () => (await A.locator(".pageCommentBtn").innerText()) === "1", { what: "the title control counts it" });
    await closePopover(A);
    await A.locator(".commentsPanelRow", { hasText: "This page" }).waitFor();
    assertNoProblems(A);
  });

  await step("comments: edit and delete one's own; another person's comment offers neither", async () => {
    await ann.api(`/api/pages/${pg.id}/comments`, { method: "POST", body: { block_id: "cmnote2", body: "Ann's remark, with a tpyo" } });
    await ben.api(`/api/pages/${pg.id}/comments`, { method: "POST", body: { block_id: "cmnote2", body: "Ben's remark" } });
    await until(async () => (await control(A, "The second claim").innerText().catch(() => "")) === "2", { what: "both threads counted", timeout: 10000 });
    // Ben sees Copy link only on Ann's comment.
    await until(async () => (await control(B, "The second claim").innerText().catch(() => "")) === "2", { what: "ben sees both", timeout: 10000 });
    await openControl(B, "The second claim");
    const annsInB = popover(B).locator(".commentItem", { hasText: "Ann's remark" });
    await annsInB.hover();
    await annsInB.locator(".rowMenuBtn").click();
    assertEq((await B.locator(".ctxMenu .ctxMenuItem").allInnerTexts()).map((s) => s.trim()).join("|"), "Copy link", "no Edit or Delete on another's");
    await B.keyboard.press("Escape");
    await closePopover(B);

    await openControl(A, "The second claim");
    const mine = popover(A).locator(".commentItem", { hasText: "Ann's remark" });
    await mine.hover();
    await mine.locator(".rowMenuBtn").click();
    await A.locator(".ctxMenu .ctxMenuItem", { hasText: "Edit" }).click();
    const edit = field(mine);
    await edit.waitFor();
    await focused(edit, "the edit field takes the focus");
    assertEq(await fieldText(edit), "Ann's remark, with a tpyo", "the field holds the comment's text");
    await A.keyboard.press("Control+A");
    await A.keyboard.type("Ann's remark, with a typo fixed");
    await mine.getByRole("button", { name: "Save", exact: true }).click();
    await until(async () => (await threads()).some((th) => th.comments.some((c) => c.body === "Ann's remark, with a typo fixed")), { what: "the edit is stored" });
    await mine.locator(".commentEdited").waitFor();
    await mine.hover();
    await mine.locator(".rowMenuBtn").click();
    await A.locator(".ctxMenu .ctxMenuItem", { hasText: "Delete" }).click();
    await until(async () => !(await threads()).some((th) => th.comments.some((c) => c.body.startsWith("Ann's remark"))), { what: "the delete is stored" });
    await until(async () => (await control(A, "The second claim").innerText()) === "1", { what: "one thread left" });
    await closePopover(A);
    assertNoProblems(A); assertNoProblems(B);
  });

  await step("comments: a reply left unsent is still in its field when the popover opens again", async () => {
    await openControl(A, "The first claim");
    const reply = await openReply(popover(A));
    await reply.click();
    await A.keyboard.type("Half a thought");
    await A.keyboard.press("Escape");
    await popover(A).waitFor({ state: "detached" });
    await openControl(A, "The first claim");
    // a thread holding a draft shows its field without a press
    assertEq(await fieldText(reply), "Half a thought", "the draft is back");
    await reply.click();
    await A.keyboard.press("Control+A");
    await A.keyboard.press("Backspace");
    await closePopover(A);
    await openControl(A, "The first claim");
    assertEq(await reply.count(), 0, "an emptied field keeps no draft, so the thread shows no field until pressed");
    await closePopover(A);
    assertNoProblems(A);
  });

  await step("comments: a viewer comments from a read-only row", async () => {
    const ctx = await vic.context(browser);
    const V = await openPage(ctx, url);
    try {
      await rowOf(V, "The second claim").waitFor();
      assertEq(await V.locator(".dragHandle").count(), 0, "the rows are read-only");
      await openControl(V, "The second claim");
      await popover(V).locator(".commentNewBtn").click();
      await focused(field(popover(V).locator(".commentNew")), "the viewer's field takes the focus");
      await V.keyboard.type("A viewer's question");
      await V.keyboard.press("Shift+Enter");
      await until(async () => (await threads()).some((th) => th.comments[0].author === "cm-vic" && th.comments[0].body === "A viewer's question"), { what: "the viewer's thread is stored" });
      assertNoProblems(V);
    } finally {
      await ctx.close();
    }
  });

  await step("comments: a visitor through a link with no account reads the threads and is asked to sign in", async () => {
    const { token } = await ann.api(`/api/share/${pg.id}`, { method: "POST" });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const S = await openPage(ctx, `${server.base}/?share=${token}`);
    try {
      await until(async () => (await control(S, "The first claim").innerText().catch(() => "")) === "1", { what: "the count shows in the share view" });
      await openControl(S, "The first claim");
      await popover(S).locator(".commentBody", { hasText: "Yes, see the appendix" }).waitFor();
      assertEq(await popover(S).locator(".commentField").count(), 0, "no field to write in");
      assertEq(await popover(S).locator(".commentResolve").count(), 0, "no Resolve");
      await popover(S).getByRole("button", { name: "Sign in to comment" }).waitFor();
      assertNoProblems(S);
    } finally {
      await ctx.close();
    }
  });

  await step("comments: ?thread= opens the page at the thread", async () => {
    const thread = (await threads()).find((th) => th.block_id === "cmnote1");
    const ctx = await ben.context(browser);
    const D = await openPage(ctx, `${server.base}/?ws=${wsId}&page=${pg.id}&block=cmnote1&thread=${thread.id}`);
    try {
      await popover(D).locator(`.commentThread[data-thread-id="${thread.id}"]`).waitFor({ timeout: 10000 });
      assert((await popover(D).innerText()).includes("Yes, see the appendix"), "the thread's reply is there");
      assertNoProblems(D);
    } finally {
      await ctx.close();
    }
  });

  await step("comments: a formula draws whole: held hidden while its faces load in a fresh browser, shown once they are in", async () => {
    // Faces outside the warmed set (shared/lib/katexFonts.js FACES), so even
    // a browser that has the common ones must fetch these first.
    const root = await ann.api(`/api/pages/${pg.id}/comments`, { method: "POST",
      body: { block_id: "cmnote2", body: "The space $\\mathcal{H}$ and the algebra $\\mathfrak{g}$ over $\\mathbb{R}$" } });
    const ctx = await ben.context(browser);
    // Every mark put on a formula and taken off again, from the first script on.
    await ctx.addInitScript(() => {
      window.__mathHold = { held: 0, released: 0 };
      new MutationObserver((records) => {
        for (const r of records) {
          if (r.attributeName !== "data-fonts") continue;
          if (r.target.getAttribute("data-fonts") === "loading") window.__mathHold.held++;
          else window.__mathHold.released++;
        }
      }).observe(document, { attributes: true, attributeFilter: ["data-fonts"], subtree: true });
    });
    const E = await openPage(ctx, `${server.base}/?ws=${wsId}&page=${pg.id}&block=cmnote2&thread=${root.thread.id}`);
    try {
      const formula = popover(E).locator(`.commentThread[data-thread-id="${root.thread.id}"] .commentBody .katex`);
      await formula.first().waitFor({ timeout: 10000 });
      await until(async () => (await E.locator(".katex[data-fonts]").count()) === 0, { what: "the formulas are shown again", timeout: 5000 });
      const hold = await E.evaluate(() => window.__mathHold);
      assert(hold.held >= 1, `the first formula found its faces loading and was held: ${JSON.stringify(hold)}`);
      assert(hold.released >= 1, `a held formula was released: ${JSON.stringify(hold)}`);
      const faces = await E.evaluate(() => {
        const out = {};
        for (const f of document.fonts) if (/^KaTeX_(Caligraphic|Fraktur|AMS)$/.test(f.family) && f.weight === "400") out[f.family] = f.status;
        return out;
      });
      assertEq(faces.KaTeX_Caligraphic, "loaded", "the calligraphic face is in");
      assertEq(faces.KaTeX_Fraktur, "loaded", "the fraktur face is in");
      assertNoProblems(E);
    } finally {
      await ctx.close();
    }
  });

  await step("comments: a note thread's Copy link in a folder share opens that page at the thread", async () => {
    await ann.file(pg.id, { folders: ["Review folder"] });
    const { token } = await ann.api(`/api/share/folder/${await ann.folder("Review folder")}`, { method: "POST" });
    const thread = (await threads()).find((th) => th.block_id === "cmnote1");
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, permissions: ["clipboard-read", "clipboard-write"] });
    try {
      const S = await openPage(ctx, `${server.base}/?share=${token}&page=${pg.id}`);
      await openControl(S, "The first claim");
      const first = popover(S).locator(".commentItem", { hasText: "Is this sourced?" });
      await first.hover();
      await first.locator(".rowMenuBtn").click();
      await S.locator(".ctxMenu .ctxMenuItem", { hasText: "Copy link" }).click();
      let link = "";
      await until(async () => (link = await S.evaluate(() => navigator.clipboard.readText()).catch(() => "")).includes("thread="), { what: "the link is copied" });
      const q = new URL(link).searchParams;
      assertEq([q.get("share"), q.get("page"), q.get("block"), q.get("thread")].join("|"), [token, pg.id, "cmnote1", thread.id].join("|"), "the share's link to the note's thread");
      const L = await openPage(ctx, link);
      await popover(L).locator(`.commentThread[data-thread-id="${thread.id}"]`).waitFor({ timeout: 10000 });
      assert((await popover(L).innerText()).includes("Yes, see the appendix"), "the thread's reply is there");
      assertNoProblems(S); assertNoProblems(L);
    } finally {
      await ctx.close();
    }
  });

  await step("comments: the field is a note's editor: [[ links a page, Escape closes only the picker, @ names a person, $…$ previews, Enter breaks the line and Shift+Enter sends", async () => {
    await openControl(A, "The second claim");
    await popover(A).locator(".commentNewBtn").click();
    const box = field(popover(A).locator(".commentNew"));
    await focused(box, "the new field takes the focus");
    // its own undo: Ctrl+Z takes back the field's typing, not a note's
    await A.keyboard.type("Oops");
    await A.keyboard.press("Control+Z");
    assertEq(await fieldText(box), "", "the typing is undone");
    assertEq(await rowOf(A, "The second claim").count(), 1, "the notes are as they were");
    // [[ and part of a title: the note's link picker, at the "[["
    await A.keyboard.type("See [[Appen");
    const picker = A.locator(".refPopup:not(.mentionMenu)");
    const pickedRow = picker.locator(".refPopupItem.selected .refPopupText");
    await until(async () => (await pickedRow.innerText().catch(() => "")) === "Appendix tables", { what: "the link picker offers the page" });
    await shot(A, "w6-link-picker");
    await A.keyboard.press("Escape");
    await picker.waitFor({ state: "detached" });
    assert(await popover(A).isVisible(), "Escape closed the picker, not the popover");
    assertEq(await fieldText(box), "See [[Appen", "the typed text stays");
    await A.keyboard.type("d"); // typing on opens it again
    await until(async () => (await pickedRow.innerText().catch(() => "")) === "Appendix tables", { what: "the picker is back" });
    await A.keyboard.press("Enter");
    // @ and a name: the note's @ menu at the caret, without reminders
    await A.keyboard.type(" for @cm-b");
    // the link, once the caret has left it, is its page's chip
    await box.locator(".cmRefChip", { hasText: "Appendix tables" }).waitFor();
    const menu = A.locator(".mentionMenu");
    await until(async () => (await menu.locator(".refPopupItem.selected .refPopupText").innerText().catch(() => "")) === "cm-ben", { what: "the @ menu offers the person" });
    await shot(A, "w6-mention-menu");
    await A.keyboard.press("Enter");
    await menu.waitFor({ state: "detached" });
    await box.locator(".cmMentionChip", { hasText: "@cm-ben" }).waitFor();
    // Enter breaks the line; inside $…$ the live preview shows
    await A.keyboard.press("Enter");
    await popover(A).waitFor(); // not sent
    await A.keyboard.type("Bound: $\\sqrt{x");
    await A.locator(".mathPreviewTip .katex").waitFor();
    await shot(A, "w6-latex-preview");
    await A.keyboard.type("}$"); // over the closers the field put in
    await A.locator(".mathPreviewTip").waitFor({ state: "detached" });
    await A.keyboard.press("Shift+Enter");
    let body = "";
    await until(async () => {
      const th = (await threads()).find((x) => x.block_id === "cmnote2" && x.comments[0].body.startsWith("See "));
      body = th?.comments[0].body || "";
      return !!body;
    }, { what: "the comment is stored" });
    assertEq(body, `See [[${appendix.id}]] for @cm-ben \nBound: $\\sqrt{x}$`, "the link, the mention, the line break and the math");
    // the new comment's field closes and the button to start one is back
    await box.waitFor({ state: "detached" });
    await popover(A).locator(".commentNewBtn").waitFor();
    const sent = popover(A).locator(".commentBody", { hasText: "Bound:" });
    await sent.locator(".katex").waitFor();
    assertEq(await sent.locator(".blockRefChip").innerText(), "Appendix tables", "the link shows the page's title");
    assertEq(await sent.locator(".mentionChip").innerText(), "@cm-ben");
    await sent.scrollIntoViewIfNeeded();
    await shot(A, "w6-sent-comment");
    await closePopover(A);
    assertNoProblems(A);
  });

  await step("comments: the field's \"/\" menu writes text only: /h2 makes a heading, a list continues, the Equation block previews; the small send button's title names Shift+Enter", async () => {
    await openControl(A, "The first claim");
    await popover(A).locator(".commentNewBtn").click();
    const box = field(popover(A).locator(".commentNew"));
    await focused(box, "the new field takes the focus");
    const send = popover(A).locator(".commentNew .commentSend");
    assertEq(await send.getAttribute("title"), "Comment (Shift+Enter)", "the title names the key");
    assertEq(await send.getAttribute("aria-label"), "Comment", "the name is the plain action");
    const size = await send.evaluate((el) => [el.getBoundingClientRect().width, el.getBoundingClientRect().height, el.querySelector("svg").getBoundingClientRect().width]);
    assertEq(size.join(), "24,24,12", "a 24px circle with a 12px arrow");
    // the bare "/": the commands that write text, none that make a page, a sheet, an upload or an embed
    await A.keyboard.type("/");
    const menu = A.locator(".slashMenu");
    await menu.waitFor();
    const labels = (await menu.locator(".slashMenuLabel").allInnerTexts()).map((s) => s.trim());
    for (const gone of ["New page", "Handwritten note", "Image", "Embed note"]) assert(!labels.includes(gone), `no ${gone}: ${labels.join("|")}`);
    for (const kept of ["Heading 1", "Heading 2", "Heading 3", "To-do", "Bulleted list", "Numbered list", "Quote", "Callout", "Divider",
      "Inline equation", "Equation block", "Table", "Code block", "Mermaid diagram", "Today's date", "Link to note", "Highlight text"]) {
      assert(labels.includes(kept), `${kept} is offered: ${labels.join("|")}`);
    }
    await shot(A, "w9-slash-menu");
    await A.keyboard.press("Escape");
    await menu.waitFor({ state: "detached" });
    assert(await popover(A).isVisible(), "Escape closed the menu, not the popover");
    await A.keyboard.press("Backspace");
    // /h2 and Enter: the line is a heading
    await A.keyboard.type("/h2");
    await until(async () => (await menu.locator(".slashMenuItem.selected .slashMenuLabel").innerText().catch(() => "")) === "Heading 2", { what: "/h2 picks Heading 2" });
    await A.keyboard.press("Enter");
    await menu.waitFor({ state: "detached" });
    await A.keyboard.type("Findings");
    // one line: the button stands on it
    const line = await box.locator(".cm-line").first().evaluate((el) => el.getBoundingClientRect().top + el.getBoundingClientRect().height / 2);
    const mid = await send.evaluate((el) => el.getBoundingClientRect().top + el.getBoundingClientRect().height / 2);
    assert(Math.abs(line - mid) <= 4, `the button on the text's line: ${mid} by ${line}`);
    if (flags?.keep) {
      // a screenshot draws no native tooltip: the title, drawn where it shows
      await send.hover();
      await send.evaluate((b) => {
        const r = b.getBoundingClientRect(), tip = document.createElement("div");
        tip.id = "e2eTitle";
        tip.textContent = b.title;
        Object.assign(tip.style, { position: "fixed", left: `${r.left - 60}px`, top: `${r.bottom + 8}px`, zIndex: 99999, font: "12px system-ui",
          padding: "2px 6px", background: "#fff", color: "#000", border: "1px solid #767676", boxShadow: "1px 1px 2px rgba(0,0,0,.2)" });
        document.body.append(tip);
      });
      await shot(A, "w9-send-button");
      await A.evaluate(() => document.getElementById("e2eTitle")?.remove());
      await box.click();
      await A.keyboard.press("Control+End");
    }
    // Enter breaks the line; a list continues, an empty item ends it
    await A.keyboard.press("Enter");
    await A.keyboard.type("/bullet");
    await until(async () => (await menu.locator(".slashMenuItem.selected .slashMenuLabel").innerText().catch(() => "")) === "Bulleted list", { what: "/bullet" });
    await A.keyboard.press("Enter");
    await A.keyboard.type("first point");
    await A.keyboard.press("Enter");
    await A.keyboard.type("second point");
    await A.keyboard.press("Enter");
    await A.keyboard.press("Enter");
    await A.keyboard.press("Enter");
    assert(await box.isVisible(), "Enter sent nothing");
    // the Equation block: $$x$$ with the x chosen, typed over with the live preview
    await A.keyboard.type("/equation");
    await until(async () => (await menu.locator(".slashMenuItem.selected .slashMenuLabel").innerText().catch(() => "")) === "Equation block", { what: "/equation" });
    await A.keyboard.press("Enter");
    await A.keyboard.type("E = mc^2");
    await A.locator(".mathPreviewTip .katex").waitFor();
    await shot(A, "w9-equation-preview");
    await A.keyboard.press("Shift+Enter");
    let body = "";
    await until(async () => {
      const th = (await threads()).find((x) => x.block_id === "cmnote1" && x.comments[0].body.startsWith("## "));
      body = th?.comments[0].body || "";
      return !!body;
    }, { what: "the comment is stored" });
    assertEq(body, "## Findings\n- first point\n- second point\n\n$$E = mc^2$$", "the heading, the list and the equation");
    const sent = popover(A).locator(".commentBody", { hasText: "Findings" });
    await sent.locator("h2", { hasText: "Findings" }).waitFor();
    assertEq(await sent.locator("ul > li").count(), 2, "the list's two items");
    await sent.locator(".katex").waitFor();
    await sent.scrollIntoViewIfNeeded();
    await shot(A, "w9-sent-comment");
    // a reply's button names its key too, once its thread is pressed
    await openReply(popover(A));
    const reply = popover(A).locator(".commentThread .commentSend").first();
    assertEq(await reply.getAttribute("title"), "Send reply (Shift+Enter)");
    await closePopover(A);
    assertNoProblems(A);
  });

  let reviewed = ""; // the paper the highlight steps comment on
  await step("comments: a highlight's thread, from its menu; the PDF badge shows it and opens it", async () => {
    const pdf = makePdf([["A paper about tensor networks", "and matrix product states"]]);
    const up = await ann.upload("/api/uploads", pdf, "review.pdf", "application/pdf");
    const paper = await ann.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Reviewed paper", source_url: up.source_url } });
    reviewed = paper.id;
    await A.goto(`${server.base}/?ws=${wsId}&page=${paper.id}`);
    await waitForPdf(A, 1);
    await selectPdfText(A, 1, "tensor networks");
    await A.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
    await A.locator(".plainTip .colorBtn").first().click();
    const mark = A.locator('[data-page="1"] [data-hl-id]').first();
    await mark.waitFor({ timeout: 10000 });
    await mark.click({ button: "right" });
    await A.locator(".ctxMenu").getByRole("button", { name: "Comment", exact: true }).click();
    await popover(A).waitFor();
    assertEq(await popover(A).locator(".commentPopTitle").innerText(), "Comments on this highlight");
    await focused(field(popover(A).locator(".commentNew")), "ready to write");
    // (the highlight was made a moment ago: the comment waits for its block to be saved)
    await A.keyboard.type("Which paper is this?");
    await A.keyboard.press("Shift+Enter");
    const badge = A.locator('[data-page="1"] .pdfNoteBadge.hasThread');
    await badge.waitFor({ timeout: 10000 });
    await closePopover(A);
    await badge.click();
    await popover(A).locator(".commentBody", { hasText: "Which paper is this?" }).waitFor();
    assertNoProblems(A);

    // On a phone the page opens on its PDF: a note's thread link brings the notes up under it.
    await ann.api(`/api/pages/${paper.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "cmnote3", parent: paper.id, content: "A note on the paper" }] } });
    const { thread } = await ann.api(`/api/pages/${paper.id}/comments`, { method: "POST", body: { block_id: "cmnote3", body: "Seen on a phone" } });
    const ctx = await ben.context(browser, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    try {
      const P = await openPage(ctx, `${server.base}/?ws=${wsId}&page=${paper.id}&block=cmnote3&thread=${thread.id}`);
      await popover(P).locator(".commentBody", { hasText: "Seen on a phone" }).waitFor({ timeout: 10000 });
      await P.locator(".phonePanel:not(.phonePanelHidden) .blockRow", { hasText: "A note on the paper" }).waitFor();
      assert(await P.locator(".phonePanel:not(.phonePanelHidden) .blockRow", { hasText: "A note on the paper" }).isVisible(), "the note shows under its thread");
      assertNoProblems(P);
    } finally {
      await ctx.close();
    }
  });

  await step("comments: the selection popup opens at the pointer; its Comment makes a commented highlight, and an abandoned one leaves none", async () => {
    // (A is on the reviewed paper from the step above, its badge's popover open)
    if (await popover(A).count()) await closePopover(A);
    const tip = A.locator(".plainTip");
    const marks = () => A.evaluate(() => [...new Set([...document.querySelectorAll('[data-page="1"] [data-hl-id]')].map((el) => el.dataset.hlId))]);
    const before = await marks();
    let release = null;
    await assertOpensInPlace(A, ".plainTip", async () => { release = await dragSelectPdfText(A, 1, "matrix product"); }, { what: "the selection popup" });
    const r = await tip.evaluate((el) => { const b = el.getBoundingClientRect(); return { left: b.left, top: b.top }; });
    assert(Math.hypot(r.left - release.x, r.top - release.y) < 40,
      `the popup opens by the pointer: its corner (${r.left}, ${r.top}), the release (${release.x}, ${release.y})`);
    // the colours, the line, then the tools: Comment, Link
    const row = await tip.evaluate((el) => [...el.querySelector(".colorRow").children].map((c) => ({
      kind: c.classList.contains("tipDivider") ? "line" : c.classList.contains("linkTipBtn") ? "tool" : "colour",
      label: c.getAttribute("aria-label") || c.getAttribute("title") || "", left: c.getBoundingClientRect().left, right: c.getBoundingClientRect().right,
    })));
    const line = row.findIndex((c) => c.kind === "line");
    assert(line > 0 && row.slice(0, line).every((c) => c.kind === "colour") && row.slice(line + 1).every((c) => c.kind === "tool"),
      `colours | tools: ${row.map((c) => c.kind).join(" ")}`);
    assert(row[line - 1].right < row[line].left && row[line].right < row[line + 1].left, "the line sits between them");
    assertEq(row[line + 1].label, "Comment on this passage", "Comment is the first tool");
    assert(row[line + 2].label.startsWith("Link this reference"), `then Link: ${row[line + 2].label}`);
    await shot(A, "w7-popup-at-pointer");

    // Comment: the highlight, its popover with the new comment's field focused
    await tip.getByRole("button", { name: "Comment on this passage" }).click();
    await popover(A).waitFor();
    assertEq(await popover(A).locator(".commentPopTitle").innerText(), "Comments on this highlight");
    await focused(field(popover(A).locator(".commentNew")), "the new comment's field takes the focus");
    const made = (await marks()).filter((id) => !before.includes(id));
    assertEq(made.length, 1, "one new highlight");
    const hl = made[0];
    await A.keyboard.type("Cite the original MPS paper");
    await shot(A, "w7-composer-on-fresh-highlight");
    await A.keyboard.press("Shift+Enter");
    await until(async () => (await ann.api(`/api/pages/${reviewed}/comments?state=all`)).threads
      .some((x) => x.block_id === hl && x.comments[0].body === "Cite the original MPS paper"), { what: "the comment is stored on the new highlight" });
    await closePopover(A);
    // a highlight with comments and no note: a lighter wash over a 2px line of its colour
    const look = await A.locator(`[data-hl-id="${hl}"]`).first().evaluate((el) => {
      const cs = getComputedStyle(el);
      return { width: cs.borderBottomWidth, style: cs.borderBottomStyle, line: cs.borderBottomColor };
    });
    assertEq(look.width, "2px", "the underline");
    assertEq(look.style, "solid");
    assert(!/70, 130, 255/.test(look.line), `the highlight's own colour, not the link's blue: ${look.line}`);
    assert(await A.locator('[data-page="1"] .pdfNoteBadge.hasThread').count() >= 2, "its badge shows the thread");
    await A.locator(`[data-hl-id="${hl}"]`).first().scrollIntoViewIfNeeded();
    await shot(A, "w7-commented-highlight");

    // A second Comment left with nothing typed (Escape): no highlight stays.
    const kept = await marks();
    await dragSelectPdfText(A, 1, "A paper about");
    await tip.waitFor();
    await tip.getByRole("button", { name: "Comment on this passage" }).click();
    await popover(A).waitFor();
    await focused(field(popover(A).locator(".commentNew")), "ready to write");
    await until(async () => (await marks()).length === kept.length + 1, { what: "the second highlight drawn" });
    await A.keyboard.press("Escape");
    await popover(A).waitFor({ state: "detached" });
    await until(async () => (await marks()).length === kept.length, { what: "the abandoned highlight is taken away" });
    assertEq((await marks()).sort().join(), kept.sort().join(), "the same highlights as before");
    // Nor any undo step: the first Ctrl+Z takes back the commented
    // highlight made before it, not an empty one; redo brings that back.
    await A.keyboard.press("Control+z");
    await until(async () => !(await marks()).includes(hl), { what: "the undo takes back the commented highlight" });
    assertEq((await marks()).length, kept.length - 1, "no empty highlight comes back");
    await A.keyboard.press("Control+Shift+z");
    await until(async () => (await marks()).includes(hl), { what: "the redo brings it back" });
    assertEq((await marks()).sort().join(), kept.sort().join(), "the highlights as before the undo");
    assertNoProblems(A);
  });

  await step("comments: with room beside the page the threads are cards in the margin, level with their highlights; a press opens one, a reply posts from it, Resolve takes it away; the selection popup's Comment opens a card; narrow, the badge and the popover as before", async () => {
    // (A is on the reviewed paper: "tensor networks" and "matrix product",
    // on the page's two nearby lines, each with a thread)
    if (await popover(A).count()) await closePopover(A);
    const all = await ann.api(`/api/pages/${reviewed}/comments?state=all`);
    const onPaper = (body) => all.threads.find((x) => x.comments[0].body === body);
    const first = onPaper("Which paper is this?");
    const second = onPaper("Cite the original MPS paper");
    assert(first && second, "the two highlights' threads");
    await A.setViewportSize({ width: 1600, height: 900 });
    const margin = A.locator('[data-page="1"] .commentMargin');
    const zoomOut = A.getByRole("button", { name: "Zoom out", exact: true });
    for (let i = 0; i < 12 && !(await margin.count()); i++) {
      await zoomOut.click();
      await sleep(200);
    }
    await margin.waitFor({ state: "attached", timeout: 5000 }); // (a column of no height: the cards are placed in it)
    const cards = margin.locator(".commentCard");
    const card = (body) => cards.filter({ hasText: body });
    await card("Which paper is this?").waitFor();
    await card("Cite the original MPS paper").waitFor();
    assertEq(await A.locator('[data-page="1"] .pdfNoteBadge.hasThread').count(), 0, "the threads leave the badges for the cards");
    // level with the highlight's first line, beside the page
    const geometry = () => A.evaluate(() => {
      const page = document.querySelector('[data-page="1"]').getBoundingClientRect();
      return [...document.querySelectorAll('[data-page="1"] .commentCard')].map((el) => {
        const r = el.getBoundingClientRect();
        const hl = [...document.querySelectorAll(`[data-page="1"] [data-hl-id="${el.dataset.cardFor}"]`)]
          .map((m) => m.getBoundingClientRect().top);
        return { top: r.top, bottom: r.bottom, left: r.left, hl: Math.min(...hl), pageRight: page.right, active: el.classList.contains("active") };
      });
    });
    // (the cards settle a frame after they are measured, and move with a short transition)
    const settled = async (what) => until(async () => apart(await geometry()), { what });
    const apart = (list) => [...list].sort((a, b) => a.top - b.top).every((b, i, s) => i === 0 || b.top >= s[i - 1].bottom);
    await settled("two highlights on nearby lines: cards that do not overlap");
    let boxes = await geometry();
    const top = boxes.reduce((a, b) => (a.top < b.top ? a : b));
    assert(Math.abs(top.top - top.hl) <= 30, `the first card is level with its highlight: card ${top.top}, highlight ${top.hl}`);
    assert(boxes.every((b) => b.left > b.pageRight), "the cards stand right of the page");
    await shot(A, "w8-margin-two-cards");

    // A press on a card: its whole thread, with its reply field focused, and its highlight drawn stronger.
    const hlFirst = A.locator(`[data-hl-id="${first.block_id}"]`).first();
    const restWash = await hlFirst.evaluate((el) => getComputedStyle(el).backgroundColor);
    await card("Which paper is this?").locator(".commentBody").first().click();
    const active = margin.locator(".commentCard.active");
    await active.waitFor();
    assert((await active.innerText()).includes("Which paper is this?"), "the pressed card is the active one");
    await focused(field(active), "the reply field takes the focus");
    assert((await hlFirst.evaluate((el) => getComputedStyle(el).backgroundColor)) !== restWash, "the active card's highlight is drawn stronger");
    await A.keyboard.type("Found it: the 2004 review");
    await A.keyboard.press("Shift+Enter");
    await until(async () => (await ann.api(`/api/pages/${reviewed}/comments?state=all`)).threads
      .find((x) => x.id === first.id)?.comments.some((x) => x.body === "Found it: the 2004 review"), { what: "the reply is stored" });
    await active.locator(".commentBody", { hasText: "Found it: the 2004 review" }).waitFor();
    await settled("the grown active card pushes the one under it down");
    boxes = await geometry();
    const activeBox = boxes.find((b) => b.active);
    assert(Math.abs(activeBox.top - activeBox.hl) <= 30, `the active card stays level with its highlight: ${activeBox.top}, ${activeBox.hl}`);
    await shot(A, "w8-active-card");
    // Escape: no card is active, and the card at rest counts the reply
    await A.keyboard.press("Escape");
    await active.waitFor({ state: "detached" });
    assertEq(await card("Which paper is this?").locator(".commentReplies").innerText(), "1 reply");
    assertEq(await card("Which paper is this?").locator(".commentField").count(), 0, "at rest: no field");
    // a press on the highlight in the page makes its card the active one; a press outside, none
    await A.locator(`[data-hl-id="${second.block_id}"]`).first().click();
    await active.waitFor();
    assert((await active.innerText()).includes("Cite the original MPS paper"), "the highlight's card");
    await A.locator(".blockRow").first().click();
    await active.waitFor({ state: "detached" });

    // Resolve, from the active card: the card leaves the margin
    await card("Cite the original MPS paper").locator(".commentBody").first().click();
    await active.getByRole("button", { name: "Resolve", exact: true }).click();
    await card("Cite the original MPS paper").waitFor({ state: "detached" });
    await until(async () => !!(await ann.api(`/api/pages/${reviewed}/comments?state=all`)).threads
      .find((x) => x.id === second.id)?.resolved_at, { what: "the thread is resolved" });
    assertEq(await cards.count(), 1, "one card left");

    // The selection popup's Comment: a card of its own, its field focused
    const tip = A.locator(".plainTip");
    await dragSelectPdfText(A, 1, "A paper about");
    await tip.waitFor();
    await tip.getByRole("button", { name: "Comment on this passage" }).click();
    const fresh = margin.locator(".commentCard.active .commentNew");
    await fresh.waitFor();
    await focused(field(fresh), "the new comment's field takes the focus");
    assertEq(await popover(A).count(), 0, "no popover: the card is the thread");
    await A.keyboard.type("Say which review");
    await shot(A, "w8-new-comment-card");
    await A.keyboard.press("Shift+Enter");
    await card("Say which review").waitFor();
    await until(async () => (await ann.api(`/api/pages/${reviewed}/comments?state=all`)).threads
      .some((x) => x.comments[0].body === "Say which review"), { what: "the new comment is stored" });
    await active.locator(".commentField").waitFor(); // stored, it is the active card with its reply field
    await settled("the new card does not overlap the others");
    // one left with nothing typed leaves no highlight, as with the popover
    const marks = () => A.evaluate(() => new Set([...document.querySelectorAll('[data-page="1"] [data-hl-id]')].map((el) => el.dataset.hlId)).size);
    const kept = await marks();
    await dragSelectPdfText(A, 1, "states");
    await tip.waitFor();
    await tip.getByRole("button", { name: "Comment on this passage" }).click();
    await fresh.waitFor();
    await until(async () => (await marks()) === kept + 1, { what: "the highlight drawn" });
    await A.keyboard.press("Escape");
    await fresh.waitFor({ state: "detached" });
    await until(async () => (await marks()) === kept, { what: "the abandoned highlight is taken away" });
    assertNoProblems(A);

    // Narrow, at fit-width: no margin; the badge and the popover as before.
    await A.setViewportSize({ width: 1280, height: 860 });
    await A.getByRole("button", { name: "Fit to width", exact: true }).click();
    await margin.waitFor({ state: "detached" });
    const badge = A.locator(`[data-page="1"] .pdfNoteBadge.hasThread[data-hl-id="${first.block_id}"]`);
    await badge.waitFor();
    await badge.click();
    await popover(A).locator(".commentBody", { hasText: "Found it: the 2004 review" }).waitFor();
    await shot(A, "w8-narrow-fallback");
    await closePopover(A);
    assertNoProblems(A);
  });

  await step("comments: the pages' margins are one column: a card grown at a page's foot pushes the next page's cards down; with a pen or the Text tool armed, a press on a card opens it and draws nothing", async () => {
    // a highlight on page 1's last line, another on page 2's first
    const pdf = makePdf([[...Array(25).fill(""), "Bottom of page one"], ["Top of page two", "and more of page two"]]);
    const up = await ann.upload("/api/uploads", pdf, "two-pages.pdf", "application/pdf");
    const paper = await ann.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Two pages", source_url: up.source_url } });
    await A.setViewportSize({ width: 1600, height: 900 });
    await A.goto(`${server.base}/?ws=${wsId}&page=${paper.id}`);
    await waitForPdf(A, 1);
    const highlight = async (pageNo, text) => {
      await A.locator(`[data-page="${pageNo}"]`).scrollIntoViewIfNeeded();
      await waitForPdf(A, pageNo);
      await selectPdfText(A, pageNo, text);
      await A.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
      await A.locator(".plainTip .colorBtn").first().click();
      const mark = A.locator(`[data-page="${pageNo}"] [data-hl-id]`).first();
      await mark.waitFor({ timeout: 10000 });
      return mark.getAttribute("data-hl-id");
    };
    const foot = await highlight(1, "Bottom of page one");
    const head = await highlight(2, "Top of page two");
    // (the highlights are saved before their threads can be stored on them)
    const post = (block_id, body, thread_id) => until(async () => (await ann.api(`/api/pages/${paper.id}/comments`, { method: "POST", body: { block_id, body, thread_id } }).catch(() => null))?.thread, { what: `"${body}" stored` });
    const footThread = await post(foot, "Bottom of page one, with a long thread");
    for (let i = 1; i <= 4; i++) await post(foot, `Reply ${i}: a reply long enough to take two lines of the card's column, so the card grows`, footThread.id);
    await post(head, "Top of page two");
    const zoomOut = A.getByRole("button", { name: "Zoom out", exact: true });
    const margins = A.locator(".commentMargin");
    for (let i = 0; i < 12 && (await margins.count()) < 2; i++) {
      await zoomOut.click();
      await sleep(200);
    }
    const card = (body) => A.locator(".commentCard").filter({ hasText: body });
    const footCard = card("Bottom of page one, with a long thread");
    const headCard = card("Top of page two");
    await footCard.waitFor();
    await headCard.waitFor();
    await footCard.scrollIntoViewIfNeeded();
    await footCard.locator(".commentBody").first().click();
    const active = A.locator(".commentCard.active");
    await active.locator(".commentBody", { hasText: "Reply 4" }).waitFor();
    // every card on the two pages, in the viewport's frame
    const geometry = () => A.evaluate(() => [...document.querySelectorAll(".commentCard")].map((el) => {
      const r = el.getBoundingClientRect();
      const hl = [...document.querySelectorAll(`[data-hl-id="${el.dataset.cardFor}"]`)].map((m) => m.getBoundingClientRect().top);
      const x = r.left + r.width / 2, y = r.bottom - 12;
      return { top: r.top, bottom: r.bottom, hl: Math.min(...hl), active: el.classList.contains("active"), page: el.closest("[data-page]").dataset.page,
        // what shows at the card's foot (when in view): the card itself, not another page's
        own: y < 0 || y > innerHeight || el.contains(document.elementFromPoint(x, y)) };
    }));
    const apart = (list) => [...list].sort((a, b) => a.top - b.top).every((b, i, s) => i === 0 || b.top >= s[i - 1].bottom);
    const boxes = await until(async () => { const g = await geometry(); return g.length === 2 && apart(g) ? g : null; },
      { what: "the grown card at page 1's foot and page 2's first card do not overlap" });
    const grown = boxes.find((b) => b.active), next = boxes.find((b) => !b.active);
    assertEq(`${grown.page} ${next.page}`, "1 2", "a card on each page");
    assert(grown.bottom > next.hl, `the grown card runs past the next page's highlight: card bottom ${grown.bottom}, highlight ${next.hl}`);
    assert(next.top >= grown.bottom, `the next page's card goes under it: ${next.top}, ${grown.bottom}`);
    assert(Math.abs(grown.top - grown.hl) <= 30, `the active card stays level with its highlight: ${grown.top}, ${grown.hl}`);
    assert(boxes.every((b) => b.own), "no card is drawn over another");
    if (flags?.keep) { // both cards in view, for the record
      await A.locator(".pdfViewer").evaluate((el, dy) => el.scrollBy(0, dy), grown.top - 160);
      await sleep(200);
      await shot(A, "w8-page-boundary");
    }
    await A.keyboard.press("Escape");
    await active.waitFor({ state: "detached" });

    // The pen strip opens with a pen armed: a drag over a card draws
    // nothing, and a press on it makes it the active card.
    const extras = async () => ((await ann.api(`/api/blocks/${paper.id}/subtree`)).block.children || [])
      .filter((b) => b.properties?.ink_url || b.properties?.text_box).length;
    const drawn = () => A.locator(".inkLayer path, .textBox").count();
    await A.click("button[aria-label='Handwriting tools']");
    await A.waitForSelector(".pdfInkBar .inkToolBtn.modeActive .inkToolInk");
    await headCard.scrollIntoViewIfNeeded();
    const b = await headCard.boundingBox();
    await A.mouse.move(b.x + 20, b.y + b.height / 2);
    await A.mouse.down();
    await A.mouse.move(b.x + b.width - 20, b.y + b.height / 2, { steps: 12 });
    await A.mouse.up();
    assertEq(await drawn(), 0, "no stroke under the drag");
    // (the drag began and ended on the card: a press on it)
    if (await active.count()) {
      // (an Escape before the card's field has the focus closes the pen strip too)
      await focused(field(active), "the pressed card's reply field takes the focus");
      await A.keyboard.press("Escape");
      await active.waitFor({ state: "detached" });
    }
    await headCard.locator(".commentBody").first().click();
    await active.waitFor();
    assert((await active.innerText()).includes("Top of page two"), "the pressed card is the active one");
    await focused(field(active), "its reply field takes the focus");
    await A.keyboard.press("Escape");
    await active.waitFor({ state: "detached" });
    // and the Text tool makes no box there
    await A.click(".pdfInkBar button[title^='Text (T)']");
    await A.waitForSelector(".pdfInkBar button[title^='Text (T)'].modeActive", { timeout: 5000 });
    await headCard.locator(".commentBody").first().click();
    await active.waitFor();
    await A.keyboard.press("Escape");
    await active.waitFor({ state: "detached" });
    await sleep(500); // (a stroke or a box would be saved by now)
    assertEq(await drawn(), 0, "no stroke and no text box on the page");
    assertEq(await extras(), 0, "none stored");
    await A.click("button[aria-label='Handwriting tools']");
    assertNoProblems(A);
  });

  await ctxA.close();
  await ctxB.close();
}
