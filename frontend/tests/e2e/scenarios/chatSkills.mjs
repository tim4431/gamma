import { Account, fakeAiModels, wanted } from "../harness.mjs";

// Skills in the chat (docs/dev/skills.md): the composer's "/" menu over the
// built-in skills and the account's own, a message that runs one posting it
// beside its arguments, the bubble's chip and its copy, the menu's keys,
// Retry and the edit bubble carrying the skill or dropping it, Settings →
// Chat's skills reaching the menu without a reload, and a skill gone
// between the catalog's read and the send putting the message back.
// /api/ai/chat is faked and its bodies recorded, as the mentions group does.
export async function chatSkillScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("chat-skills")) return;
  await server.manage("create-user", "skills-user", "skills-pw");
  const user = await new Account(server, "skills-user", "skills-pw").login();
  const notes = await user.api("/api/pages", { method: "POST", body: { title: "Skill check" } });
  // `answer(body)` is what the faked chat route sends back: a reply by default.
  const setup = async ({ answer = null } = {}) => {
    const ctx = await user.context(browser, { permissions: ["clipboard-read", "clipboard-write"] });
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    const page = await openPage(ctx, `${server.base}/?page=${notes.id}&ws=${user.ws}`);
    const requests = [];
    await page.route("**/api/ai/chat", async (route) => {
      const body = route.request().postDataJSON();
      requests.push(body);
      await route.fulfill(answer?.(body) || { contentType: "application/x-ndjson", body: '{"delta":"Done."}\n' });
    });
    const input = page.getByRole("combobox", { name: "Message AI" });
    await input.waitFor();
    await page.waitForLoadState("networkidle"); // the catalog has landed: "/" opens nothing before
    return { ctx, page, input, requests };
  };
  const menu = (page) => page.getByRole("listbox", { name: "Skills" });
  const names = async (page) => (await menu(page).locator(".slashMenuLabel").allInnerTexts()).map((s) => s.trim());
  const saved = async () => (await user.api(`/api/chats/${notes.id}`)).messages || [];
  const clearChat = () => user.api(`/api/chats/${notes.id}`, { method: "PUT", body: { messages: [] } });

  await step("chat skills: / opens the menu, typing filters and Enter inserts; the message posts its skill and arguments, the bubble shows its chip, and Copy gives /name args", async () => {
    await clearChat();
    const { ctx, page, input, requests } = await setup();
    try {
      assert((await input.getAttribute("placeholder")).includes("(/ skill)"), "the placeholder offers skills");
      await input.fill("/");
      await menu(page).waitFor();
      const listed = await names(page);
      for (const name of ["/explain", "/review", "/summarize"]) assert(listed.includes(name), `${name} is in the menu: ${listed}`);
      assertEq(await menu(page).getByRole("option").first().locator(".uiTag").innerText(), "Built-in", "a built-in says so");
      // ↓ moves the choice, Enter inserts it and sends nothing.
      await input.press("ArrowDown");
      await input.press("Enter");
      assertEq(await input.inputValue(), `${listed[1]} `, "the second row's name");
      assertEq(await menu(page).count(), 0, "a pick closes the menu");
      assertEq(requests.length, 0, "choosing a skill does not send the message");
      await input.fill("/sum");
      await until(async () => (await names(page))[0] === "/summarize" && !(await names(page)).includes("/review"),
        { what: "the query filters the rows" });
      assertEq((await menu(page).getByRole("option").first().locator(".slashMenuLabel mark").allInnerTexts()).join(""), "sum",
        "the matched letters are marked");
      await input.press("Enter");
      assertEq(await input.inputValue(), "/summarize ");
      await input.pressSequentially("for a journal club");
      await input.press("Enter");
      await until(() => requests.length === 1);
      assertEq(JSON.stringify(requests[0].skill), JSON.stringify({ id: "builtin:summarize", name: "summarize", args: "for a journal club" }),
        "the message names its skill and its arguments");
      assertEq(requests[0].prompt, "for a journal club", "the text is the arguments");
      const bubble = page.locator(".chatBubble.user").last();
      assertEq((await bubble.locator(".chatMsgSkill").innerText()).trim(), "/summarize", "the bubble's chip");
      await until(async () => (await saved()).length === 2, { what: "the conversation saved" });
      assertEq(JSON.stringify((await saved())[0].skill), JSON.stringify(requests[0].skill), "stored with the message");
      await page.locator(".chatBubbleRow.user").last().hover(); // a message's tools show with its row
      await page.locator(".chatBubbleRow.user").last().getByTitle("Copy message").click();
      await until(async () => (await page.evaluate(() => navigator.clipboard.readText())) === "/summarize for a journal club",
        { what: "Copy gives /name args" });
      // An argument-less skill message is sendable on its own.
      await input.fill("/explain");
      await input.press("Escape");
      await input.press("Enter");
      await until(() => requests.length === 2);
      assertEq(JSON.stringify(requests[1].skill), JSON.stringify({ id: "builtin:explain", name: "explain", args: "" }), "a skill alone goes");
      assertEq(JSON.stringify(requests[1].history.find((m) => m.role === "user")?.skill), JSON.stringify(requests[0].skill),
        "the earlier turn's skill rides in the history");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat skills: Escape dismisses the menu where it was, and an unknown /usr with Enter goes as plain text", async () => {
    await clearChat();
    const { ctx, page, input, requests } = await setup();
    try {
      await input.fill("/rev");
      await menu(page).waitFor();
      await input.press("Escape");
      assertEq(await menu(page).count(), 0, "Escape closes the menu");
      await input.press("End");
      await input.pressSequentially("i");
      assertEq(await menu(page).count(), 0, "it stays closed at that place");
      await input.fill(""); // the "/" gone, a new one opens the menu again
      await input.fill("/usr");
      await menu(page).waitFor();
      await menu(page).getByText("No skill matches “/usr”").waitFor();
      await menu(page).getByRole("button", { name: "Manage skills…" }).waitFor();
      await input.press("Enter");
      await until(() => requests.length === 1);
      assertEq(requests[0].prompt, "/usr", "the text as typed");
      assert(!requests[0].skill, "no skill");
      assertEq(await page.locator(".chatBubble.user .chatMsgSkill").count(), 0, "no chip");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat skills: Retry and an unchanged edit send the skill again; the edit bubble keeps its chip, whose × sends the message without it", async () => {
    await clearChat();
    const down = { status: 500, contentType: "application/json", body: JSON.stringify({ detail: "The provider is down.", kind: "other" }) };
    let fail = true;
    const { ctx, page, input, requests } = await setup({ answer: () => (fail ? ((fail = false), down) : null) });
    const summarize = JSON.stringify({ id: "builtin:summarize", name: "summarize", args: "the methods" });
    const edit = async () => {
      await page.locator(".chatBubbleRow.user").last().hover();
      await page.locator(".chatBubbleRow.user").last().getByTitle("Edit and re-send (removes later messages)").click();
      return page.locator(".chatEditBubble");
    };
    try {
      await input.fill("/summarize the methods");
      await input.press("Escape");
      await input.press("Enter");
      await until(() => requests.length === 1);
      await page.getByRole("button", { name: "Retry", exact: true }).click();
      await until(() => requests.length === 2, { what: "Retry sends it again" });
      assertEq(JSON.stringify(requests[1].skill), summarize, "Retry runs the skill again");
      await page.locator(".chatBubble.ai", { hasText: "Done." }).waitFor();
      let bubble = await edit();
      assertEq((await bubble.locator(".chatMsgSkill").innerText()).trim(), "/summarize", "the edit bubble keeps the chip");
      await bubble.locator(".chatEditSend").click();
      await until(() => requests.length === 3, { what: "the edit sent" });
      assertEq(JSON.stringify(requests[2].skill), summarize, "an unchanged edit runs the skill again");
      bubble = await edit();
      await bubble.getByRole("button", { name: "Remove the skill /summarize", exact: true }).click();
      assertEq(await bubble.locator(".chatMsgSkill").count(), 0, "the × drops the chip");
      await bubble.locator(".chatEditSend").click();
      await until(() => requests.length === 4, { what: "the edit without the skill sent" });
      assert(!requests[3].skill, "no skill");
      assertEq(requests[3].prompt, "the methods", "its text alone");
      await until(async () => (await page.locator(".chatBubble.user").count()) === 1 && (await page.locator(".chatBubble.user .chatMsgSkill").count()) === 0,
        { what: "the bubble without its chip" });
      assertNoProblems(page, [/\/api\/ai\/chat -> 500/]);
    } finally { await ctx.close(); }
  });

  await step("chat skills: a skill made in Settings is in the / menu without a reload; a built-in turned off leaves it, one kept to / stays", async () => {
    await clearChat();
    const { ctx, page, input, requests } = await setup();
    try {
      // The menu that matches none opens Settings → Chat at Your skills.
      await input.fill("/journal-club");
      await page.getByRole("button", { name: "Manage skills…" }).click();
      const settings = page.getByRole("dialog", { name: "Settings", exact: true });
      await settings.locator('.settingsPane .setHead[data-setting="Chat"]').waitFor();
      await until(() => page.evaluate(() => document.activeElement?.dataset?.setting === "Your skills"),
        { what: "the pane opened at Your skills" });
      await settings.getByRole("button", { name: "Add skill" }).click();
      const editor = page.getByRole("dialog", { name: "New skill", exact: true });
      await editor.getByPlaceholder("paper-summary").fill("journal-club");
      await editor.locator(".setField", { hasText: "Description" }).locator("textarea").fill("Prepares a handout for a journal club");
      await editor.getByPlaceholder("[section or topic]").fill("[audience]");
      await editor.getByRole("textbox", { name: "Instructions", exact: true }).fill("Write a one-page handout for $ARGUMENTS.");
      await editor.getByRole("button", { name: "Save", exact: true }).click();
      await editor.waitFor({ state: "detached" });
      await settings.locator('.settingsPane [data-setting="/journal-club"]').waitFor();
      // How a built-in is offered follows the server's answer: Off leaves the
      // menu, Only from / keeps it there and takes it from the assistant.
      const stored = async (id) => (await user.api("/api/ai/skills")).skills.find((s) => s.id === id);
      const offered = (name) => settings.getByRole("button", { name: `How /${name} is offered`, exact: true });
      await offered("explain").click();
      await page.getByRole("menuitemradio", { name: "Off", exact: true }).click();
      await until(async () => (await stored("builtin:explain"))?.off, { what: "the built-in stored as off" });
      await until(async () => (await offered("explain").innerText()).includes("Off"), { what: "the row reads Off" });
      await offered("summarize").click();
      await page.getByRole("menuitemradio", { name: "Only from /", exact: true }).click();
      await until(async () => (await stored("builtin:summarize"))?.model === false,
        { what: "the built-in stored as run from / only" });
      assert(!(await stored("builtin:summarize"))?.off, "still offered in the menu");
      await settings.getByRole("button", { name: "Close settings", exact: true }).click();
      await settings.waitFor({ state: "detached" });
      await input.fill("/");
      await menu(page).waitFor();
      const listed = await names(page);
      assert(listed.includes("/journal-club") && !listed.includes("/explain"), `the new skill in, the one turned off out: ${listed}`);
      assert(listed.includes("/summarize"), `one kept to "/" is still in the menu: ${listed}`);
      const row = menu(page).getByRole("option").filter({ hasText: "/journal-club" });
      assert((await row.innerText()).includes("[audience]"), "its argument hint");
      assertEq(await row.locator(".uiTag").count(), 0, "an account skill has no Built-in tag");
      await input.fill("/journal");
      await input.press("Tab");
      assertEq(await input.inputValue(), "/journal-club ");
      await input.pressSequentially("undergraduates");
      await input.press("Enter");
      await until(() => requests.length === 1);
      assertEq(requests[0].skill?.name, "journal-club");
      assert(requests[0].skill.id && !requests[0].skill.id.startsWith("builtin:"), "the account skill's own id");
      assertEq(requests[0].skill.args, "undergraduates");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      // Restore the catalog for the steps after.
      for (const s of (await user.api("/api/ai/skills")).skills) {
        if (s.source === "account") await user.api(`/api/ai/skills/${s.id}?updated_at=${encodeURIComponent(s.updated_at)}`, { method: "DELETE" });
        else if (s.off) await user.api(`/api/ai/skills/${s.id}/state`, { method: "PUT", body: { off: false } });
      }
    }
  });

  await step("chat skills: a skill gone before the send puts the message back in the composer, with the card saying why", async () => {
    await clearChat();
    const gone = { status: 400, contentType: "application/json",
      body: JSON.stringify({ detail: "The skill /summarize was deleted or turned off.", kind: "skill" }) };
    const { ctx, page, input, requests } = await setup({ answer: () => gone });
    let reads = 0;
    page.on("request", (r) => { if (new URL(r.url()).pathname === "/api/ai/skills") reads++; });
    try {
      await input.fill("/summarize the methods");
      await input.press("Escape");
      await input.press("Enter");
      await until(() => requests.length === 1);
      assertEq(requests[0].skill?.name, "summarize");
      await page.getByText("That skill is no longer available").waitFor();
      await until(async () => (await input.inputValue()) === "/summarize the methods", { what: "the message back in the composer" });
      assertEq(await page.locator(".chatBubble.user").count(), 0, "the failed message is not left in the transcript");
      await until(() => reads > 0, { what: "the catalog read again" });
      assertNoProblems(page, [/\/api\/ai\/chat -> 400/]);
    } finally { await ctx.close(); }
  });
}
