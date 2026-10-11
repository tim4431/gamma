// The note editor's "@" menu (docs/dev/mentions.md): its sections, each
// pick's text and chip, a name no one has staying text, Invite opening the
// Share popover; and a reminder due in another workspace as a card that
// opens its note there and is dismissed for the account.
import { Account, wanted } from "../harness.mjs";
import { closeEditor, editRow, row, tree } from "./notes.mjs";

export async function noteMentionScenarios({ server, browser, step, until, assert, assertEq, assertNoProblems, openPage }) {
  if (!wanted("note mentions")) return;
  for (const [name, pw] of [["nm-admin", "nm-admin-pw"], ["nm-ann", "nm-ann-pw"], ["nm-ben", "nm-ben-pw"]]) {
    server.manage("create-user", name, pw);
  }
  server.manage("set-admin", "nm-admin", "on");
  const admin = await new Account(server, "nm-admin", "nm-admin-pw").login();
  const ann = await new Account(server, "nm-ann", "nm-ann-pw").login();
  const personal = ann.ws;
  // A shared workspace: ann owns it, ben edits. The admin is no member.
  const lab = await admin.api("/api/workspaces", { method: "POST", body: { name: "Mention lab", kind: "shared", owner: "nm-ann" } });
  server.manage("set-admin", "nm-admin", "off");
  ann.ws = lab.id;
  await ann.api(`/api/workspaces/${lab.id}/members/nm-ben`, { method: "PUT", body: { role: "editor" } });
  const target = await ann.api("/api/pages", { method: "POST", body: { title: "Powerloss" } });
  await ann.file(target.id, { folders: ["General/Docs"] });
  const src = await ann.api("/api/pages", { method: "POST", body: { title: "Mention source" } });
  await ann.api(`/api/pages/${src.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
    { op: "insert", id: "nmblock", parent: src.id, position: "a0", content: "start" }] } });
  const content = async () => (await tree(ann, src.id))[0]?.content;

  await step("note mentions: a bare @ offers the date, a reminder, the people and Invite, the recent pages; each pick writes its mention and renders as a chip", async () => {
    const ctx = await ann.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${lab.id}&page=${src.id}`);
    try {
      const day = (n) => page.evaluate((n) => {
        const d = new Date(); d.setDate(d.getDate() + n);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      }, n);
      const [today, tomorrow] = [await day(0), await day(1)];
      const menu = page.locator(".mentionMenu");
      const selected = menu.locator(".refPopupItem.selected .refPopupText");
      await editRow(page, "start");
      await page.keyboard.type(" @");
      await menu.waitFor();
      assertEq(JSON.stringify(await menu.locator(".refPopupHead").allInnerTexts()), JSON.stringify(["Date", "People", "Link to page"]));
      const rows = await menu.locator(".refPopupItem .refPopupBody").allInnerTexts();
      for (const want of ["Today", "Remind me— Tomorrow 9:00 AM", "nm-ann(You)", "nm-ben", "Invite…", "Powerloss\nGeneral / Docs"]) {
        assert(rows.some((r) => r.replace(/\s+\n/g, "\n").trim() === want), `a row reads ${JSON.stringify(want)}: ${JSON.stringify(rows)}`);
      }
      assertEq(await selected.innerText(), "Today", "the first row is chosen");
      await page.keyboard.press("Enter");
      await until(async () => (await content()) === `start @${today} `, { what: "Enter writes the date" });

      await page.keyboard.type("@ben");
      await until(async () => (await selected.innerText()) === "nm-ben", { what: "a part of a name finds the person" });
      await page.keyboard.press("Tab");
      await page.keyboard.type("@powerl");
      await until(async () => (await selected.innerText()) === "Powerloss", { what: "the page by its title" });
      await page.keyboard.press("Enter");
      await page.keyboard.type("@tomorrow 3pm");
      await until(async () => (await selected.innerText()) === "Tomorrow 3:00 PM", { what: "the query's date, spaces and all" });
      await page.keyboard.press("ArrowDown");
      assert((await selected.innerText()).startsWith("Remind me"), "then the reminder at that time");
      await page.keyboard.press("Enter");
      // A name no one has stays text; Escape closes the menu and keeps what was typed.
      await page.keyboard.type("@nobody and @");
      await menu.waitFor();
      await page.keyboard.press("Escape");
      await menu.waitFor({ state: "detached" });
      const want = `start @${today} @nm-ben [[${target.id}]] @${tomorrow} 15:00 (remind @nm-ann) @nobody and @`;
      await until(async () => (await content()) === want, { what: `the text reads ${want}` });

      await closeEditor(page);
      const r = row(page, "start");
      assertEq(JSON.stringify(await r.locator(".mentionChip").allInnerTexts()), JSON.stringify(["@Today", "@nm-ben", "@Tomorrow 3:00 PM"]));
      assertEq(await r.locator(".mentionChip.mention-remind").count(), 1, "the reminder's chip");
      assertEq(await r.locator(".blockRefChip").innerText(), "Powerloss", "the page is a [[link]]");
      assert((await r.innerText()).includes("@nobody"), "an unknown name is plain text");
      assertNoProblems(page);
    } finally {
      await ctx.close();
    }
  });

  await step("note mentions: Invite… opens the page's Share popover", async () => {
    const ctx = await ann.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${lab.id}&page=${src.id}`);
    try {
      await editRow(page, "start");
      await page.keyboard.type(" @inv");
      await page.locator(".mentionMenu .refPopupItem.selected", { hasText: "Invite…" }).waitFor();
      await page.keyboard.press("Enter");
      await page.locator(".sharePopover").waitFor();
      assertNoProblems(page);
    } finally {
      await ctx.close();
    }
  });

  await step("note mentions: a reminder due in another workspace is a card; Open goes to its note there, × dismisses it for the account", async () => {
    ann.ws = personal;
    const home = await ann.api("/api/pages", { method: "POST", body: { title: "Thesis plan" } });
    const today = new Date();
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    await ann.api(`/api/pages/${home.id}/ops`, { method: "POST", body: { client: "e2e", ops: [
      { op: "insert", id: "nmremind", parent: home.id, position: "a0", content: `Send the draft to @nm-ben @${iso} 00:00 (remind @nm-ann)` }] } });
    ann.ws = lab.id;
    const ctx = await ann.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=${lab.id}`);
    try {
      const card = page.locator(".reminderCard");
      await card.waitFor();
      assertEq(await card.locator(".reminderText").innerText(), "Send the draft to @nm-ben", "the note without its reminder");
      assertEq(await card.locator(".reminderPage").innerText(), "Thesis plan");
      await card.getByRole("button", { name: "Open", exact: true }).click();
      await until(() => new URL(page.url()).searchParams.get("ws") === personal, { what: "the reminder's workspace opens" });
      await page.locator(".blockRow", { hasText: "Send the draft" }).waitFor();
      await page.locator(".reminderCard").getByRole("button", { name: "Dismiss" }).click();
      await page.locator(".reminderCard").waitFor({ state: "detached" });
      await until(async () => (await ann.api("/api/reminders")).done.includes(`nmremind/${iso}/00:00`), { what: "dismissed for the account" });
      await page.reload();
      await page.locator(".blockRow", { hasText: "Send the draft" }).waitFor();
      assertEq(await page.locator(".reminderCard").count(), 0, "no card after a reload");
      assertNoProblems(page);
    } finally {
      await ctx.close();
    }
  });
}
