import { fakeAiModels, wanted } from "../harness.mjs";
export async function chatNavigationScenarios(env) {
  const { server, browser, alice, makePdf, step, until, assert, assertEq, assertNoProblems, openPage, flags } = env;
  if (!wanted("chat navigation")) return;
  const target = await alice.api("/api/pages", { method: "POST", body: { title: "Linked paper" } });
  const upload = await alice.upload("/api/uploads", makePdf([["Chat navigation paper"]]), "chat-navigation.pdf", "application/pdf");
  const pdf = await alice.api(`/api/blocks/by-doc/${upload.doc_id}`, { method: "POST", body: {
    default_title: "Chat navigation paper", source_url: upload.source_url,
  } });

  for (const source of [{ name: "library", key: "home" }, { name: "PDF", key: pdf.id }]) {
    for (const finishAway of [false, true]) {
      await step(`chat navigation: ${source.name} reply survives returning ${finishAway ? "after" : "before"} completion`, async () => {
        await alice.api(`/api/chats/${source.key}`, { method: "PUT", body: { messages: [] } });
        const ctx = await alice.context(browser);
        await fakeAiModels(ctx);
        await ctx.addInitScript(() => {
          localStorage.setItem("gamma-ai-login-check", "off");
          const fetch = window.fetch.bind(window);
          window.fetch = (input, init) => {
            if (String(input).endsWith("/api/ai/chat")) {
              return Promise.resolve(new Response(new ReadableStream({
                start(controller) {
                  window.chatStream = {
                    push: (event) => controller.enqueue(new TextEncoder().encode(JSON.stringify(event) + "\n")),
                    finish: () => controller.close(),
                  };
                  init.signal.addEventListener("abort", () => controller.error(new DOMException("Stopped", "AbortError")));
                },
              }), { headers: { "Content-Type": "application/x-ndjson" } }));
            }
            return fetch(input, init);
          };
        });
        const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}${source.key === "home" ? "" : `&page=${source.key}`}`);
        try {
          const input = page.getByRole("combobox", { name: "Message AI" });
          await input.waitFor();
          await page.waitForLoadState("networkidle");
          await input.fill("Find a related paper");
          await input.press("Enter");
          await page.waitForFunction(() => !!window.chatStream);
          await page.evaluate((id) => window.chatStream.push({ delta: `Read [Linked paper](/?page=${id}).` }), target.id);
          const reply = page.locator(".chatBubble.ai");
          await page.locator(".chatPanel").getByRole("link", { name: "Linked paper" }).click();
          await until(() => new URL(page.url()).searchParams.get("block") === target.id);
          assertEq(await reply.count(), 0, "the other page does not display the source reply");
          await page.evaluate(() => window.chatStream.push({ delta: " This text arrived while away." }));
          // The provider's token report (one {"usage"} line per turn) sums onto the reply
          // and shows under it once the stream ends: "↑ 1.2k ↓ 34 · 50% cached".
          await page.evaluate(() => window.chatStream.push({ usage: { input: 1000, output: 30, cache_read: 600, cache_write: 0 } }));
          await page.evaluate(() => window.chatStream.push({ usage: { input: 200, output: 4, cache_read: 0, cache_write: 0 } }));
          if (finishAway) await page.evaluate(() => window.chatStream.finish());
          await page.getByRole("button", { name: "Back", exact: true }).click();
          await until(async () => (await page.locator(".chatPanel").innerText()).includes("This text arrived while away."));
          if (!finishAway) {
            await page.getByRole("button", { name: "Stop generating", exact: true }).waitFor();
            await page.getByRole("button", { name: "Close Chat", exact: true }).click();
            await until(async () => !(await page.locator(".chatPanel").count()));
            await page.evaluate(() => window.chatStream.push({ delta: " Continued with the panel closed." }));
            await page.getByRole("button", { name: "View", exact: true }).click();
            await page.locator(".menuPopover").getByRole("button", { name: "AI Chat" }).click();
            await page.getByRole("button", { name: "View", exact: true }).click();
            await until(async () => (await page.locator(".chatPanel").innerText()).includes("Continued with the panel closed."));
            await page.getByRole("button", { name: "Stop generating", exact: true }).waitFor();
            // Text after the last report counts as a "~" estimate next to the Responding pill.
            const live = page.locator(".chatThinking .chatMsgUsage");
            await live.waitFor();
            assert((await live.innerText()).includes("~"), "the streaming round shows an estimate");
            assert((await live.innerText()).includes("1.2k"), "reported rounds stay exact while streaming");
            await page.evaluate(() => { window.chatStream.push({ delta: " Finished after returning." }); window.chatStream.finish(); });
          }
          await until(async () => !(await page.getByRole("button", { name: "Stop generating", exact: true }).count()));
          let saved;
          await until(async () => {
            saved = await alice.api(`/api/chats/${source.key}`);
            return saved.messages?.at(-1)?.text?.includes("This text arrived while away.") && !saved.messages.at(-1).partial;
          });
          assertEq(saved.messages.length, 2);
          assertEq(saved.messages.at(-1).usage.input, 1200, "the reply keeps the summed token report");
          assertEq(saved.messages.at(-1).context_tokens, 204, "the context ring's figure is the last round alone");
          const usageLine = page.locator(".chatBubbleRow.ai .chatMsgUsage");
          await usageLine.waitFor();
          assert((await usageLine.innerText()).replace(/\s+/g, " ").includes("1.2k"), "input tokens shown under the reply");
          assert((await usageLine.innerText()).includes("50% cached"), "cached share shown under the reply");
          assertEq((await alice.api(`/api/chats/${target.id}`)).messages.length, 0);
          await page.getByRole("button", { name: "New chat", exact: true }).click();
          await until(async () => !(await reply.count()));
          await page.getByRole("button", { name: "Chat history", exact: true }).click();
          await page.locator(".chatHistRow:not(.active)").filter({ hasText: "Find a related paper" }).first().click();
          await until(async () => (await page.locator(".chatPanel").innerText()).includes("This text arrived while away."));
          await page.reload();
          await until(async () => (await page.locator(".chatPanel").innerText()).includes("This text arrived while away."));
          assertNoProblems(page);
          assert(finishAway || saved.messages.at(-1).text.includes("Finished after returning."));
        } finally { await ctx.close(); }
      });
    }
  }

  await step("chat navigation: two papers answer at once, each with its own Stop", async () => {
    await alice.api(`/api/chats/${pdf.id}`, { method: "PUT", body: { messages: [] } });
    await alice.api(`/api/chats/${target.id}`, { method: "PUT", body: { messages: [] } });
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => {
      localStorage.setItem("gamma-ai-login-check", "off");
      window.chatStreams = []; // one fake stream per /api/ai/chat call, in send order
      const fetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        if (String(input).endsWith("/api/ai/chat")) {
          return Promise.resolve(new Response(new ReadableStream({
            start(controller) {
              window.chatStreams.push({
                pageId: JSON.parse(init.body).page_id,
                push: (event) => controller.enqueue(new TextEncoder().encode(JSON.stringify(event) + "\n")),
                finish: () => controller.close(),
              });
              init.signal.addEventListener("abort", () => controller.error(new DOMException("Stopped", "AbortError")));
            },
          }), { headers: { "Content-Type": "application/x-ndjson" } }));
        }
        return fetch(input, init);
      };
    });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${pdf.id}`);
    try {
      const input = page.getByRole("combobox", { name: "Message AI" });
      const stop = page.getByRole("button", { name: "Stop generating", exact: true });
      await input.waitFor();
      await page.waitForLoadState("networkidle");
      await input.fill("Summarize the first paper");
      await input.press("Enter");
      await page.waitForFunction(() => window.chatStreams.length === 1);
      await page.evaluate((id) => window.chatStreams[0].push({ delta: `First paper: see [Linked paper](/?page=${id}), ` }), target.id);
      await stop.waitFor();
      // Walk to the other page while the first reply streams: its composer is
      // free — the first paper's Stop is not this conversation's.
      await page.locator(".chatPanel").getByRole("link", { name: "Linked paper" }).click();
      await until(() => new URL(page.url()).searchParams.get("block") === target.id);
      await input.waitFor();
      assertEq(await stop.count(), 0, "the other page's composer is not blocked by the first reply");
      await input.fill("Summarize the second paper");
      await input.press("Enter");
      await page.waitForFunction(() => window.chatStreams.length === 2);
      assertEq((await page.evaluate(() => window.chatStreams.map((s) => s.pageId))).join(), `${pdf.id},${target.id}`);
      await page.evaluate(() => { window.chatStreams[0].push({ delta: "still going." }); window.chatStreams[1].push({ delta: "Second paper: " }); });
      await until(async () => (await page.locator(".chatPanel").innerText()).includes("Second paper:"));
      assert(!(await page.locator(".chatPanel").innerText()).includes("First paper"), "the first reply stays in its own conversation");
      // Stop here aborts only the second paper's reply.
      await stop.click();
      await until(async () => !(await stop.count()));
      await page.evaluate(() => { window.chatStreams[0].push({ delta: " Done." }); window.chatStreams[0].finish(); });
      let first, second;
      await until(async () => {
        first = await alice.api(`/api/chats/${pdf.id}`);
        second = await alice.api(`/api/chats/${target.id}`);
        return first.messages?.at(-1)?.text?.includes("Done.") && !first.messages.at(-1).partial
          && second.messages?.at(-1)?.text?.includes("(stopped)") && !second.messages.at(-1).partial;
      });
      assert(first.messages.at(-1).text.endsWith(", still going. Done."), "the first reply streamed to its end");
      assertEq(second.messages.at(-1).text, "Second paper: \n\n*(stopped)*");
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await until(async () => (await page.locator(".chatPanel").innerText()).includes("still going. Done."));
      assertEq(await stop.count(), 0);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat navigation: two tabs asking in one conversation keep each other's messages", async () => {
    await alice.api(`/api/chats/${pdf.id}`, { method: "PUT", body: { messages: [] } });
    const tabs = [];
    try {
      for (let i = 0; i < 2; i++) {
        const ctx = await alice.context(browser);
        await fakeAiModels(ctx);
        await ctx.addInitScript(() => {
          localStorage.setItem("gamma-ai-login-check", "off");
          const fetch = window.fetch.bind(window);
          window.fetch = (input, init) => {
            if (String(input).endsWith("/api/ai/chat")) {
              return Promise.resolve(new Response(new ReadableStream({
                start(controller) {
                  window.chatStream = {
                    push: (event) => controller.enqueue(new TextEncoder().encode(JSON.stringify(event) + "\n")),
                    finish: () => controller.close(),
                  };
                },
              }), { headers: { "Content-Type": "application/x-ndjson" } }));
            }
            return fetch(input, init);
          };
        });
        tabs.push({ ctx, page: await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${pdf.id}`) });
      }
      for (const { page } of tabs) {
        await page.getByRole("combobox", { name: "Message AI" }).waitFor();
        await page.waitForLoadState("networkidle");
      }
      // Both tabs show the empty conversation; A asks and its answer is
      // saved, then B — still on its empty copy — asks too.
      const ask = async ({ page }, question, answer) => {
        const input = page.getByRole("combobox", { name: "Message AI" });
        await input.fill(question);
        await input.press("Enter");
        await page.waitForFunction(() => !!window.chatStream);
        await page.evaluate((text) => { window.chatStream.push({ delta: text }); window.chatStream.finish(); }, answer);
      };
      const stored = async () => ((await alice.api(`/api/chats/${pdf.id}`)).messages || []).map((m) => m.text);
      await ask(tabs[0], "Question from tab A", "Answer for tab A.");
      await until(async () => (await stored()).includes("Answer for tab A."), { what: "tab A's reply saved" });
      await ask(tabs[1], "Question from tab B", "Answer for tab B.");
      await until(async () => {
        const texts = await stored();
        return ["Question from tab A", "Answer for tab A.", "Question from tab B", "Answer for tab B."]
          .every((text) => texts.includes(text));
      }, { what: "both tabs' messages stored" });
      await until(async () => (await tabs[1].page.locator(".chatPanel").innerText()).includes("Answer for tab A."),
        { what: "tab B shows tab A's messages" });
      assertEq(await tabs[1].page.getByTestId("chat-save-error").count(), 0, "nothing is left unsaved");
      for (const { page } of tabs) assertNoProblems(page);
    } finally {
      for (const { ctx } of tabs) await ctx.close();
    }
  });

  await step("chat navigation: the page picker's keys — Enter ticks the best match, arrows walk, Ctrl+F stays in the picker", async () => {
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      // The conversation's load resets the picked pages: pick after it settled.
      await page.waitForLoadState("networkidle");
      await page.getByRole("button", { name: "Add attachments or chat context" }).click();
      await page.locator(".chatPlusMenuItem", { hasText: "Add pages from library" }).click();
      const picker = page.locator(".docPickerModal");
      const box = picker.getByPlaceholder("Search your pages…");
      await box.waitFor();
      const linked = picker.locator(".docPickerItem", { hasText: "Linked paper" }).locator("input");
      await box.fill("Linked paper");
      await box.press("Enter");
      await until(() => linked.isChecked());
      assertEq(await box.evaluate((el) => el.selectionStart === 0 && el.selectionEnd === el.value.length), true, "the query is selected for the next name");
      // ↓ reaches the page's checkbox, Enter unticks it, ↑ goes back to the box.
      await page.keyboard.press("ArrowDown");
      assertEq(await linked.evaluate((el) => el === document.activeElement), true);
      await page.keyboard.press("Enter");
      await until(async () => !(await linked.isChecked()));
      await page.keyboard.press("ArrowUp");
      await until(() => box.evaluate((el) => el === document.activeElement));
      // Ctrl+F from a checkbox comes back to the picker's box, not find-in-chat.
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Control+f");
      await until(() => box.evaluate((el) => el === document.activeElement));
      assertEq(await page.locator(".chatFindRow").count(), 0);
      // Enter on an empty box is Done.
      await box.fill("");
      await box.press("Enter");
      await until(async () => !(await picker.count()));
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat navigation: a failed request is a card with its fix; Retry asks again; a broken-off reply keeps its text", async () => {
    await alice.api(`/api/chats/${pdf.id}`, { method: "PUT", body: { messages: [] } });
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => {
      localStorage.setItem("gamma-ai-login-check", "off");
      window.chatCalls = 0;
      const fetch = window.fetch.bind(window);
      const lines = (events) => new Response(events.map((e) => JSON.stringify(e) + "\n").join(""),
        { headers: { "Content-Type": "application/x-ndjson" } });
      window.fetch = (input, init) => {
        if (String(input).endsWith("/api/ai/chat")) {
          window.chatCalls += 1;
          // 1st: the provider refused the key (the server's classified 502);
          // 2nd (Retry): an answer that breaks off with an overloaded error.
          if (window.chatCalls === 1) {
            return Promise.resolve(new Response(JSON.stringify({
              detail: "AI call failed: upstream 401: Incorrect API key provided: sk-t***123",
              kind: "auth", status: 401, provider_name: "OpenAI", provider_id: "nope", provider_auth: "key",
            }), { status: 502, headers: { "Content-Type": "application/json" } }));
          }
          return Promise.resolve(lines([{ delta: "Half an answer" },
            { error: "AI call failed: upstream 529: Overloaded", kind: "overloaded", status: 529, provider_name: "OpenAI" }]));
        }
        return fetch(input, init);
      };
    });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${pdf.id}`);
    try {
      const input = page.getByRole("combobox", { name: "Message AI" });
      await input.waitFor();
      await page.waitForLoadState("networkidle");
      await input.fill("Summarize this");
      await input.press("Enter");
      const card = page.locator(".chatErrorCard");
      await card.filter({ hasText: "OpenAI rejected the API key" }).waitFor();
      await card.getByRole("button", { name: "Update key", exact: true }).waitFor();
      // The provider's own words are folded away under Details.
      assertEq(await card.locator("details:not([open]) summary", { hasText: "Details from the provider" }).count(), 1);
      // Retry re-sends the question: the card is replaced by the new reply,
      // which broke off — its text stays, with the compact card under it.
      await card.getByRole("button", { name: "Retry", exact: true }).click();
      await page.locator(".chatErrorCard.compact", { hasText: "OpenAI is overloaded" }).waitFor();
      assert((await page.locator(".chatBubble.ai").innerText()).includes("Half an answer"), "the partial answer stays");
      assertEq(await page.locator(".chatErrorCard").count(), 1, "the refused request was replaced, not kept");
      let saved;
      await until(async () => {
        saved = await alice.api(`/api/chats/${pdf.id}`);
        return saved.messages?.length === 2 && saved.messages.at(-1).errorKind && !saved.messages.at(-1).partial;
      });
      assertEq(saved.messages.at(-1).text, "Half an answer");
      assertEq(saved.messages.at(-1).errorKind, "overloaded");
      assertEq(saved.messages.at(-1).error, undefined, "a broken-off answer is still an answer");
      // The saved card survives a reload.
      await page.reload();
      await page.locator(".chatErrorCard.compact", { hasText: "OpenAI is overloaded" }).waitFor();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat navigation: the agent's steps sum up in one pill that names the running step; its changes link to what changed", async () => {
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => {
      localStorage.setItem("gamma-ai-login-check", "off");
      const fetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        if (String(input).endsWith("/api/ai/chat")) {
          return Promise.resolve(new Response(new ReadableStream({
            start(controller) {
              window.chatStream = {
                push: (event) => controller.enqueue(new TextEncoder().encode(JSON.stringify(event) + "\n")),
                finish: () => controller.close(),
              };
            },
          }), { headers: { "Content-Type": "application/x-ndjson" } }));
        }
        return fetch(input, init);
      };
    });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      const input = page.getByRole("combobox", { name: "Message AI" });
      await input.waitFor();
      await page.waitForLoadState("networkidle");
      await input.fill("Tidy up the library");
      await input.press("Enter");
      await page.waitForFunction(() => !!window.chatStream);
      const pill = page.locator(".chatSteps");
      // The step running now replaces "Thinking".
      await page.evaluate(() => window.chatStream.push({ step: { id: "c1", tool: "search_library", args: { query: "cavity" } } }));
      await pill.filter({ hasText: "Searching library for “cavity”…" }).waitFor();
      assertEq(await page.locator(".chatThinking").count(), 0, "the running step stands in for the Thinking pill");
      await page.evaluate(({ id, title }) => {
        const s = window.chatStream;
        s.push({ action: { kind: "search", tool: "search_library", summary: "Searched library for “cavity” — 1 hit", args: { query: "cavity" }, result: "…" } });
        s.push({ action: { kind: "rename", tool: "rename_page", summary: `Renamed “${title}” → “Kimble 2008”`, args: { page_id: id, title: "Kimble 2008" }, result: "ok",
          page_id: id, title, from: title, to: "Kimble 2008" } });
        s.push({ action: { kind: "error", tool: "rename_page", summary: "error: no such page", args: {}, result: "error: no such page", error: true } });
        s.push({ delta: "Renamed one paper." });
        s.finish();
      }, { id: pdf.id, title: "Chat navigation paper" });
      await pill.filter({ hasText: "3 steps · searched" }).waitFor();
      assert((await pill.innerText()).includes("1 failed"), "failures are counted on the pill");
      const changes = page.locator(".chatChanges");
      await changes.filter({ hasText: "Changed in your library · 1" }).waitFor();
      assertEq(await changes.locator(".chatChangeOld").innerText(), "Chat navigation paper");
      // The chips are one click away.
      assertEq(await page.locator(".chatToolAction").count(), 0);
      await pill.click();
      assertEq(await page.locator(".chatToolAction").count(), 3);
      // The new title opens the renamed page.
      await changes.getByRole("button", { name: "Kimble 2008", exact: true }).click();
      await until(() => new URL(page.url()).searchParams.get("block") === pdf.id);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat navigation: the effort menu offers the model's own levels, the nearest one is sent, and a reply names its model and effort", async () => {
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    // One page load per model: its levels (the provider's listing or
    // models.dev, pinned by the backend tests) are stubbed here.
    const load = async (efforts) => {
      const ctx = await alice.context(browser);
      await fakeAiModels(ctx);
      await ctx.route("**/api/ai/model-info?**", (route) => route.fulfill({
        json: { model: "model", context_window: null, source: "", efforts, efforts_source: "provider" } }));
      await ctx.addInitScript(() => {
        localStorage.setItem("gamma-ai-login-check", "off");
        window.chatBodies = [];
        const fetch = window.fetch.bind(window);
        window.fetch = (input, init) => {
          if (!String(input).endsWith("/api/ai/chat")) return fetch(input, init);
          const body = JSON.parse(init.body);
          window.chatBodies.push(body);
          const lines = [{ model: { id: body.model, name: "model", effort: body.effort } }, { delta: "ok" }];
          return Promise.resolve(new Response(lines.map((l) => JSON.stringify(l)).join("\n") + "\n",
            { headers: { "Content-Type": "application/x-ndjson" } }));
        };
      });
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
      await page.getByRole("combobox", { name: "Message AI" }).waitFor();
      return { ctx, page };
    };
    const chip = (page) => page.getByRole("button", { name: "Model and reasoning effort", exact: true });
    const menuItems = (page) => page.locator(".uiSelectMenu .ctxMenuItem").allInnerTexts();
    // The pick is an account preference: wait for it to reach the profile
    // before the next page load reads it.
    const saved = (effort) => until(async () => ((await alice.api("/api/prefs/profile")).value?.chatEffort || "") === effort,
      { what: `the profile holds effort "${effort}"` });
    const send = async (page) => {
      const input = page.getByRole("combobox", { name: "Message AI" });
      await input.fill("Which effort?");
      await input.press("Enter");
      await until(() => page.evaluate(() => window.chatBodies.length === 1), { what: "the message is sent" });
      return page.evaluate(() => window.chatBodies[0].effort);
    };

    let { ctx, page } = await load(["low", "medium", "high", "xhigh"]);
    try {
      await chip(page).click();
      assertEq((await menuItems(page)).join(), "model,Default,low,medium,high,xhigh", "the model's own levels");
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "xhigh" }).click();
      await until(async () => (await chip(page).innerText()) === "model · xhigh", { what: "the chip names the effort" });
      assertEq(await send(page), "xhigh");
      await page.locator(".chatMsgModel", { hasText: "model · xhigh" }).waitFor();
      await saved("xhigh");
      assertNoProblems(page);
    } finally { await ctx.close(); }

    // A model without xhigh gets the nearest level it takes; the preference stays.
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    ({ ctx, page } = await load(["low", "medium", "high"]));
    try {
      await until(async () => (await chip(page).innerText()) === "model · high", { what: "the nearest level" });
      assertEq(await send(page), "high");
      await page.locator(".chatMsgModel", { hasText: "model · high" }).waitFor();
      await chip(page).click();
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "Default" }).click();
      await until(async () => (await chip(page).innerText()) === "model", { what: "Default leaves the effort out" });
      await saved("");
      assertNoProblems(page);
    } finally { await ctx.close(); }

    // A model with no effort control: no effort section at all.
    ({ ctx, page } = await load([]));
    try {
      await chip(page).click();
      assertEq((await menuItems(page)).join(), "model", "no effort levels to offer");
      assertEq(await page.locator(".uiSelectMenu", { hasText: "Reasoning effort" }).count(), 0);
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    }
  });
}
