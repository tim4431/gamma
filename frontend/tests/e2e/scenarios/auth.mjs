// Signing in: an unavailable explicit workspace, the login page, guest login.
export async function authScenarios({ server, browser, alice, step, assert, assertNoProblems, openPage }) {
  await step("workspaces: an unavailable explicit workspace never falls back to another library", async () => {
    const ctx = await alice.context(browser);
    const page = await openPage(ctx, `${server.base}/?ws=unavailable-workspace`);
    await page.getByText("This workspace is unavailable", { exact: true }).waitFor();
    assert(new URL(page.url()).searchParams.get("ws") === "unavailable-workspace", "the requested workspace stays in the URL");
    assert((await page.$$(".folderNewBtn")).length === 0, "no fallback library is opened");
    assertNoProblems(page);
    await page.getByRole("button", { name: "Open my workspaces" }).click();
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    assertNoProblems(page);
    await ctx.close();
  });

  await step("auth: login page, wrong password, then sign in", async () => {
    const ctx = await browser.newContext();
    const page = await openPage(ctx, `${server.base}/`);
    await page.waitForSelector(".loginInput");
    await page.fill(".loginInput >> nth=0", "alice");
    await page.fill("input[type=password]", "nope");
    await page.click(".loginBtn");
    await page.waitForSelector(".loginError");
    await page.fill("input[type=password]", "alice-pw");
    await page.click(".loginBtn");
    await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
    assertNoProblems(page, [/401/]);
    await ctx.close();
  });

  await step("auth: guest login lands on the welcome page", async () => {
    const ctx = await browser.newContext();
    const page = await openPage(ctx, `${server.base}/`);
    await page.waitForSelector(".loginGuestBtn");
    await page.click(".loginGuestBtn");
    await page.waitForSelector(".folderNewBtn, .cm-content, .blockRow", { timeout: 15000 });
    assertNoProblems(page);
    await ctx.close();
  });
}
