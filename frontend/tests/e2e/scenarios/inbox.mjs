// The Inbox (docs/dev/comments.md "In the app" › "The Inbox"): an owner and
// an editor in a shared workspace, and an account outside it invited to a
// page share. The editor's invitation opens its workspace; a mention typed
// in a note through the @ menu reaches the bell of a tab that has a page
// open, through the socket's nudge; the home library catches up on focus;
// an item of another workspace opens there by navigation, and one of this
// workspace lands on its block with the thread open, in place; the share's
// invitee hears of the share, a mention and a reply, and opens the reply
// through the share; Mark all read; Archive and back; an item whose page
// went to Recently deleted settles into Archived; with Inbox notifications
// on, a new item rings once per browser.
import { Account, wanted } from "../harness.mjs";
import { closeEditor, editRow, tree } from "./notes.mjs";

export async function inboxScenarios({ server, browser, step, until, assert, assertEq, assertNoProblems, openPage }) {
  if (!wanted("inbox")) return;
  for (const name of ["ib-ann", "ib-ben", "ib-cal"]) await server.manage("create-user", name, `${name}-pw`);
  const out = await server.manage("create-workspace", "Inbox lab", "ib-ann", "shared");
  const wsId = (out.match(/workspace (\S+)/) || [])[1];
  assert(wsId, `shared workspace id from: ${out}`);
  await server.manage("set-member", wsId, "ib-ben", "editor");
  const login = async (name, ws = wsId) => {
    const a = await new Account(server, name, `${name}-pw`).login();
    if (ws) a.ws = ws;
    return a;
  };
  const ann = await login("ib-ann");
  const ben = await login("ib-ben");
  const cal = await login("ib-cal", "");

  const pg = await ann.api("/api/pages", { method: "POST", body: { title: "Review draft" } });
  const other = await ann.api("/api/pages", { method: "POST", body: { title: "Other page" } });
  await ann.api(`/api/pages/${pg.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
    { op: "insert", id: "ibnote1", parent: pg.id, position: "a0", content: "The first claim" },
    { op: "insert", id: "ibnote2", parent: pg.id, position: "a1", content: "The second claim" },
  ] } });
  await ann.api(`/api/pages/${other.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
    { op: "insert", id: "ibnote9", parent: other.id, position: "a0", content: "Elsewhere" },
  ] } });
  const items = async (who, filter = "all") => (await who.api(`/api/inbox?filter=${filter}`)).items;
  // Ben was added to the workspace: that invitation is his first item.
  assertEq((await items(ben, "unread")).map((i) => `${i.kind} ${i.snippet}`).join("|"), "invited Inbox lab", "the invitation (manage.py names no inviter)");

  const bell = (page) => page.locator('[data-guide="header.inbox"]');
  // the bell's count, "0" without a badge (asked at once, never waited for)
  const badge = (page) => bell(page).evaluate((el) => el.querySelector(".inboxBadge")?.textContent || "0").catch(() => "");
  const panel = (page) => page.locator(".inboxPopover");
  const row = (page, text) => panel(page).locator(".inboxRow", { hasText: text }).first();
  const openPanel = async (page) => {
    if (!(await panel(page).isVisible().catch(() => false))) await bell(page).click();
    await panel(page).waitFor();
    await until(async () => !(await panel(page).locator(".inboxLoading").count()), { what: "the panel's listing is in" });
  };
  const tab = async (page, name) => {
    await panel(page).locator(".segGroup button", { hasText: name }).click();
    await until(async () => !(await panel(page).locator(".inboxLoading").count()), { what: `the ${name} tab is in` });
  };
  const focus = (page) => page.evaluate(() => window.dispatchEvent(new Event("focus")));

  const ctxB = await ben.context(browser);
  const ctxCal = await cal.context(browser);
  const ctxA = await ann.context(browser);
  // Ben's home library, in his own workspace: no page, so no socket.
  const home = `${server.base}/?ws=${ben.defaultWs}`;
  const H = await openPage(ctxB, home);
  await bell(H).waitFor();

  await step("inbox: an invitation opens its workspace and is read", async () => {
    await until(async () => (await badge(H)) === "1", { what: "the invitation counts" });
    await openPanel(H);
    const invited = row(H, "added you to Inbox lab");
    await Promise.all([H.waitForURL((u) => u.searchParams.get("ws") === wsId), invited.locator(".inboxMain").click()]);
    await until(async () => (await items(ben, "unread")).length === 0, { what: "the click marked it read" });
    await until(async () => (await badge(H)) === "0", { what: "the workspace's bell counts none" });
    assertNoProblems(H);
    await H.goto(home);
    await bell(H).waitFor();
  });

  // Ben's tab with a page of the shared workspace open: its socket hears the nudge.
  const B = await openPage(ctxB, `${server.base}/?ws=${wsId}&page=${other.id}`);
  await B.locator(".blockRow", { hasText: "Elsewhere" }).first().waitFor();

  await step("inbox: a mention typed in a note reaches the bell of a tab with a page open within 3 s", async () => {
    assertEq(await badge(B), "0", "nothing yet");
    const A = await openPage(ctxA, `${server.base}/?ws=${wsId}&page=${pg.id}`);
    const menu = A.locator(".mentionMenu");
    await editRow(A, "The first claim");
    await A.keyboard.type(", @ib-ben");
    await until(async () => (await menu.locator(".refPopupItem.selected .refPopupText").innerText().catch(() => "")) === "ib-ben",
      { what: "the @ menu offers Ben" });
    await A.keyboard.press("Enter");
    await menu.waitFor({ state: "detached" });
    await A.keyboard.type("please check");
    await closeEditor(A);
    await until(async () => (await tree(ann, pg.id))[0]?.content === "The first claim, @ib-ben please check",
      { what: "the note is saved with the mention" });
    await until(async () => (await badge(B)) === "1", { what: "the nudge brings the count", timeout: 3000 });
    assertNoProblems(A);
    assertNoProblems(B);
    await A.close();
  });

  await step("inbox: the home library catches up on focus", async () => {
    await ann.api(`/api/pages/${pg.id}/comments`, { method: "POST", body: { block_id: "ibnote2", body: "@ib-ben is this right?" } });
    await until(async () => (await badge(B)) === "2", { what: "the page tab hears of the comment", timeout: 3000 });
    assertEq(await badge(H), "0", "the home tab, without a socket, still shows its boot count");
    await focus(H);
    await until(async () => (await badge(H)) === "2", { what: "focus asks again", timeout: 3000 });
    assertNoProblems(H);
  });

  await step("inbox: an item of another workspace opens there, at its block", async () => {
    await openPanel(H);
    const mention = row(H, "ib-ann mentioned you in Review draft");
    await mention.waitFor();
    assert((await mention.innerText()).includes("Inbox lab"), "the row names the other workspace");
    assert((await row(H, "mentioned you in a comment on Review draft").innerText()).includes("is this right?"), "the comment's words");
    assertEq(await panel(H).locator(".popoverSection").first().innerText(), "Today", "grouped by day");
    await Promise.all([H.waitForURL((u) => u.searchParams.get("ws") === wsId && u.searchParams.get("block") === "ibnote1"), mention.locator(".inboxMain").click()]);
    await H.locator(".blockRow", { hasText: "The first claim" }).first().waitFor();
    await until(async () => (await items(ben, "unread")).length === 1, { what: "the click marked it read" });
    await until(async () => (await badge(H)) === "1", { what: "the bell counts one" });
    assertNoProblems(H);
  });

  await step("inbox: an item of this workspace lands on its block with the thread open, in place", async () => {
    const thread = (await items(ben, "unread")).find((i) => i.kind === "comment_mention").thread_id;
    await openPanel(B);
    await row(B, "mentioned you in a comment").locator(".inboxMain").click();
    await panel(B).waitFor({ state: "detached" });
    await B.locator(`.commentPopover .commentThread[data-thread-id="${thread}"]`).waitFor({ timeout: 10000 });
    assert(B.url().includes(`ws=${wsId}`), "still this workspace's tab");
    await B.locator(".blockRow", { hasText: "The second claim" }).first().waitFor();
    await until(async () => (await badge(B)) === "0", { what: "read: no count" });
    await B.locator(".commentPopover .commentPopHead .uiClose").click();
    assertNoProblems(B);
  });

  let token = "";
  await step("inbox: an account invited to a share hears of the share, a mention and a reply, and opens the reply through the share", async () => {
    ({ token } = await ann.api(`/api/share/${pg.id}`, { method: "POST", body: { audience: "list", users: [{ name: "ib-cal", role: "view" }] } }));
    const root = await ann.api(`/api/pages/${pg.id}/comments`, { method: "POST", body: { block_id: "ibnote1", body: "@ib-cal what do you think?" } });
    await ben.api(`/api/pages/${pg.id}/comments`, { method: "POST", body: { block_id: "ibnote1", thread_id: root.thread.id, body: "I agree with the claim" } });
    await until(async () => (await items(cal)).length === 3, { what: "three items for the invitee" });
    assert((await items(cal)).every((i) => i.share_token === token), "each carries the share's token");
    const C = await openPage(ctxCal, `${server.base}/`);
    try {
      await until(async () => (await badge(C)) === "3", { what: "the invitee's bell" });
      await openPanel(C);
      await row(C, "ib-ann shared Review draft with you").waitFor();
      await row(C, "ib-ann mentioned you in a comment on Review draft").waitFor();
      const reply = row(C, "ib-ben replied to a comment on Review draft");
      assert((await reply.innerText()).includes("I agree with the claim"), "the reply's words");
      await Promise.all([C.waitForURL((u) => u.searchParams.get("share") === token && u.searchParams.get("thread") === root.thread.id),
        reply.locator(".inboxMain").click()]);
      await C.locator(`.commentPopover .commentThread[data-thread-id="${root.thread.id}"]`).waitFor({ timeout: 10000 });
      assert((await C.locator(".commentPopover").innerText()).includes("I agree with the claim"), "the thread's reply is there");
      assertEq(await bell(C).count(), 0, "no bell in a share view");
      assertNoProblems(C);
    } finally {
      await C.close();
    }
  });

  await step("inbox: Mark all read empties Unread; an item archived moves to Archived and back", async () => {
    const C = await openPage(ctxCal, `${server.base}/`);
    try {
      await until(async () => (await badge(C)) === "2", { what: "two left unread" });
      await openPanel(C);
      assertEq(await panel(C).locator(".inboxRow.unread").count(), 2, "the Unread tab");
      await panel(C).getByRole("button", { name: "Mark all read", exact: true }).click();
      await panel(C).locator(".setEmpty", { hasText: "You're all caught up." }).waitFor();
      await until(async () => (await badge(C)) === "0", { what: "the count goes" });
      assertEq((await items(cal, "unread")).length, 0, "read on the server");
      await tab(C, "All");
      assertEq(await panel(C).locator(".inboxRow").count(), 3, "All holds the read items");
      const shared = row(C, "shared Review draft with you");
      await shared.hover();
      await shared.getByRole("button", { name: "Archive this item" }).click();
      await until(async () => (await panel(C).locator(".inboxRow").count()) === 2, { what: "it leaves All" });
      await until(async () => (await items(cal, "archived")).length === 1, { what: "archived on the server" });
      await tab(C, "Archived");
      const archived = row(C, "shared Review draft with you");
      await archived.hover();
      await archived.getByRole("button", { name: "Move back to the inbox" }).click();
      await panel(C).locator(".setEmpty", { hasText: "Nothing archived." }).waitFor();
      await until(async () => (await items(cal, "archived")).length === 0, { what: "back on the server" });
      assertNoProblems(C);
    } finally {
      await C.close();
    }
  });

  await step("inbox: an item whose page went to Recently deleted settles into Archived", async () => {
    const doomed = await ann.api("/api/pages", { method: "POST", body: { title: "Doomed page" } });
    await ann.api(`/api/pages/${doomed.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "ibnote5", parent: doomed.id, position: "a0", content: "@ib-ben see this" },
    ] } });
    await until(async () => (await badge(B)) === "1", { what: "the mention arrives", timeout: 3000 });
    await ann.api(`/api/blocks/${doomed.id}`, { method: "DELETE" });
    await openPanel(B);
    await panel(B).locator(".setEmpty", { hasText: "You're all caught up." }).waitFor();
    await until(async () => (await badge(B)) === "0", { what: "the listing's count replaces the bell's" });
    await tab(B, "Archived");
    await row(B, "mentioned you in Doomed page").waitFor();
    await B.keyboard.press("Escape");
    await panel(B).waitFor({ state: "detached" });
    assertNoProblems(B);
  });

  await step("inbox: with Inbox notifications on, a new item rings once per browser", async () => {
    // A stand-in for the browser's notifications: its permission kept across
    // reloads, each notification made kept in window.__rung.
    const ctxN = await ben.context(browser);
    await ctxN.addInitScript(() => {
      window.__rung = [];
      class FakeNotification {
        static get permission() { return localStorage.getItem("e2e-notify-permission") || "default"; }
        static requestPermission() {
          localStorage.setItem("e2e-notify-permission", "granted");
          return Promise.resolve("granted");
        }
        constructor(title, options = {}) { window.__rung.push({ title, body: options.body || "" }); }
        close() {}
      }
      window.Notification = FakeNotification;
    });
    const N = await openPage(ctxN, `${server.base}/?ws=${wsId}&page=${other.id}`);
    try {
      await N.locator(".blockRow", { hasText: "Elsewhere" }).first().waitFor();
      await N.locator('[data-guide="header.account"]').click();
      await N.getByRole("button", { name: "Settings…", exact: true }).click();
      const dialog = N.getByRole("dialog", { name: "Settings", exact: true });
      await dialog.waitFor();
      await N.getByRole("searchbox", { name: "Search settings" }).fill("Inbox notifications");
      await N.locator(".settingsSearchResult").filter({ has: N.getByText("Inbox notifications", { exact: true }) }).click();
      const toggle = N.getByRole("checkbox", { name: "Inbox notifications", exact: true });
      await toggle.check();
      await until(async () => (await N.evaluate(() => Notification.permission)) === "granted", { what: "the permission asked inside the click" });
      await N.keyboard.press("Escape");
      await dialog.waitFor({ state: "detached" });
      const rung = () => N.evaluate(() => window.__rung);
      assertEq((await rung()).length, 0, "nothing new yet");

      await ann.api(`/api/pages/${pg.id}/comments`, { method: "POST", body: { block_id: "ibnote2", body: "@ib-ben a ring for you" } });
      await until(async () => (await rung()).length === 1, { what: "the new item rings", timeout: 5000 });
      const [note] = await rung();
      assertEq(note.title, "ib-ann mentioned you in a comment on Review draft", "the notification's sentence");
      assert(note.body.includes("a ring for you"), `the comment's words: ${note.body}`);
      await until(async () => (await badge(N)) === "1", { what: "the bell counts it" });

      // A reload asks again and rings nothing: the item has rung in this browser.
      const asked = N.waitForResponse((r) => r.url().includes("/api/inbox?filter=unread&limit=10"));
      await N.reload();
      await (await asked).finished(); // its whole body, not just the headers
      await until(async () => (await badge(N)) === "1", { what: "the reloaded bell" });
      // the page has read that body (a frame after it), and a little longer for a late ring
      await N.evaluate(() => new Promise((done) => requestAnimationFrame(() => setTimeout(done, 300))));
      assertEq((await rung()).length, 0, "rung once per browser");
      assertNoProblems(N);
    } finally {
      await ctxN.close();
    }
  });

  await ctxA.close();
  await ctxB.close();
  await ctxCal.close();
}
