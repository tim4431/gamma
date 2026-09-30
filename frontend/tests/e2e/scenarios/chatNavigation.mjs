import { readFile } from "node:fs/promises";
import { fakeAiModels, wanted } from "../harness.mjs";
export async function chatNavigationScenarios(env) {
  const { server, browser, alice, bob, makePdf, step, until, assert, assertEq, assertNoProblems, openPage, flags } = env;
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

  await step("chat navigation: the header exports the context the model is sent", async () => {
    await alice.api(`/api/chats/${pdf.id}`, { method: "PUT", body: { messages: [
      { role: "user", text: "What is this paper about?" },
      { role: "ai", text: "It is about chat navigation." },
    ] } });
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${pdf.id}`);
    try {
      const input = page.getByRole("combobox", { name: "Message AI" });
      await input.waitFor();
      await until(async () => (await page.locator(".chatPanel").innerText()).includes("It is about chat navigation."));
      await input.fill("A draft question");
      const downloadEvent = page.waitForEvent("download");
      await page.getByRole("button", { name: "Export chat context", exact: true }).click();
      const download = await downloadEvent;
      assert(download.suggestedFilename().endsWith(" - context.md"), download.suggestedFilename());
      const text = await readFile(await download.path(), "utf8");
      assert(text.includes("## System prompt"), "the system prompt is in the export");
      assert(text.includes(`Gamma page ID: ${pdf.id}`), "the page's context is in the export");
      assert(text.includes("User question: What is this paper about?"), "the context rides on the oldest question");
      assert(text.trimEnd().endsWith("A draft question\n```"), "the draft is the last turn");
      assertEq(await input.inputValue(), "A draft question", "exporting keeps the draft");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/chats/${pdf.id}`, { method: "PUT", body: { messages: [] } });
    }
  });

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

  // A tab back in focus reads the stored conversation again, and "New chat"
  // in one tab wins over a tab still holding the old conversation: that
  // tab's next question starts the new conversation instead of bringing the
  // archived one back (chatSession.mergeChats).
  await step("chat navigation: a focused tab shows the other tab's answer; New chat in one tab wins over a stale tab", async () => {
    await alice.api(`/api/chats/${pdf.id}`, { method: "PUT", body: { messages: [] } });
    const historyCount = async () => (await alice.api(`/api/chat-history?bucket=${encodeURIComponent(pdf.id)}`)).sessions.length;
    const historyBefore = await historyCount();
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
      const ask = async ({ page }, question, answer) => {
        await page.evaluate(() => { window.chatStream = null; });
        const input = page.getByRole("combobox", { name: "Message AI" });
        await input.fill(question);
        await input.press("Enter");
        await page.waitForFunction(() => !!window.chatStream);
        await page.evaluate((text) => { window.chatStream.push({ delta: text }); window.chatStream.finish(); }, answer);
      };
      const stored = async () => ((await alice.api(`/api/chats/${pdf.id}`)).messages || []).map((m) => m.text);
      const panel = ({ page }) => page.locator(".chatPanel").innerText();
      const focus = ({ page }) => page.evaluate(() => window.dispatchEvent(new Event("focus")));
      const [a, b] = tabs;
      await ask(a, "First question in tab A", "First answer in tab A.");
      await until(async () => (await stored()).includes("First answer in tab A."), { what: "tab A's reply saved" });
      await focus(b);
      await until(async () => (await panel(b)).includes("First answer in tab A."), { what: "tab B, back in focus, shows it" });

      await a.page.getByRole("button", { name: "New chat", exact: true }).click();
      await until(async () => (await stored()).length === 0, { what: "the conversation archived" });
      // B still shows the old conversation and asks in it.
      await ask(b, "Question after the new chat", "Answer after the new chat.");
      await until(async () => JSON.stringify(await stored()) === JSON.stringify(["Question after the new chat", "Answer after the new chat."]),
        { what: "the stale tab's question starts the new conversation" });
      await until(async () => !(await panel(b)).includes("First answer in tab A."), { what: "tab B drops the archived turns" });
      assertEq(await historyCount(), historyBefore + 1, "the old conversation is in history once");
      await focus(a);
      await until(async () => (await panel(a)).includes("Answer after the new chat."), { what: "tab A, back in focus, shows it" });
      for (const tab of tabs) {
        assertEq(await tab.page.getByTestId("chat-save-error").count(), 0, "nothing is left unsaved");
        assertNoProblems(tab.page);
      }
    } finally {
      for (const { ctx } of tabs) await ctx.close();
    }
  });

  // Chats are the workspace's and only its editors write them: a viewer asks
  // the AI too, but the conversation stays in the tab (ChatDock's canSave,
  // App's chat session saving nothing).
  await step("chat navigation: a workspace viewer's chat is not saved — the tag shows, no History, New chat stays in the tab", async () => {
    const out = server.manage("create-workspace", "Chat readers", "alice", "shared");
    const labId = (out.match(/workspace (\S+)/) || [])[1];
    assert(labId, `shared workspace id from: ${out}`);
    server.manage("set-member", labId, "bob", "viewer");
    const owner = Object.assign(Object.create(Object.getPrototypeOf(alice)), alice, { ws: labId });
    const pg = await owner.api("/api/pages", { method: "POST", body: { title: "Read-only chat page" } });
    const kept = [{ role: "user", text: "The owner's question" }, { role: "ai", text: "The owner's stored answer." }];
    await owner.api(`/api/chats/${pg.id}`, { method: "PUT", body: { messages: kept } });
    const storedTexts = async () => ((await owner.api(`/api/chats/${pg.id}`)).messages || []).map((m) => m.text).join(" | ");
    const ctx = await bob.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    await ctx.route("**/api/ai/chat", (route) => route.fulfill({ contentType: "application/x-ndjson", body: '{"delta":"An answer for the viewer."}\n' }));
    const writes = []; // every chat write the viewer's tab sends
    ctx.on("request", (r) => {
      if (r.method() !== "GET" && /\/api\/chat(s|-history)\b/.test(r.url())) writes.push(`${r.method()} ${r.url()}`);
    });
    const page = await openPage(ctx, `${server.base}/?ws=${labId}&page=${pg.id}`);
    try {
      const panel = page.locator(".chatPanel");
      const input = page.getByRole("combobox", { name: "Message AI" });
      await input.waitFor();
      await until(async () => (await panel.innerText()).includes("The owner's stored answer."), { what: "the stored conversation shows" });
      await page.locator(".uiTag", { hasText: "Not saved" }).waitFor();
      assertEq(await page.getByRole("button", { name: "Chat history", exact: true }).count(), 0, "no History for a viewer");
      await input.fill("A viewer's question");
      await input.press("Enter");
      await until(async () => (await panel.innerText()).includes("An answer for the viewer."), { what: "the reply" });
      await until(async () => !(await page.getByRole("button", { name: "Stop generating", exact: true }).count()), { what: "the reply ends" });
      assertEq(await page.getByTestId("chat-save-error").count(), 0, "no save error: nothing was tried");
      await page.getByRole("button", { name: "New chat", exact: true }).click();
      await until(async () => !(await panel.innerText()).includes("The owner's stored answer."), { what: "New chat starts over in the tab" });
      assertEq(await storedTexts(), "The owner's question | The owner's stored answer.", "the stored conversation is untouched");
      assertEq(writes.join("\n"), "", "no chat save or archive was sent");
      // The server's copy is what a reload shows.
      await page.reload();
      await until(async () => (await page.locator(".chatPanel").innerText()).includes("The owner's stored answer."), { what: "the stored conversation after a reload" });
      assert(!(await page.locator(".chatPanel").innerText()).includes("An answer for the viewer."), "the viewer's exchange is gone");
      assertEq(writes.join("\n"), "", "still nothing written");
      assertNoProblems(page);
    } finally { await ctx.close(); }
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

  // A fetch_paper a publisher stopped (chat/FetchHandoffCards.jsx): the reply's
  // card opens the page through /go, follows the request on the server (the
  // Connector taking the tab shows), takes a PDF dropped on it — never the
  // library underneath — and the chat continues once, by itself. The
  // request's endpoints are faked here; the server's are in
  // backend/tests/test_fetch_handoff.py.
  await step("chat navigation: a blocked fetch's card gets the PDF from the browser and the chat continues", async () => {
    const HID = "e2eHandoffRequest01";
    const handoff = { id: HID, host: "www.science.org", wall: "captcha", source: "doi:10.1126/e2e.handoff" };
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [
      { id: "handoff-q", role: "user", text: "Read the Science paper" },
      { id: "handoff-a", role: "ai", text: "Science asked for a bot check, so I could not download it.",
        actions: [{ kind: "fetch", error: true, summary: "Needs your browser: www.science.org", tool: "fetch_paper",
          args: { source: handoff.source }, result: "error: blocked", handoff }] },
    ] } });
    let request = { ...handoff, url: "https://www.science.org/doi/10.1126/e2e.handoff", pdf_url: "", detail: "",
      status: "waiting", watched: false, pages: 0, from_url: "" };
    const uploads = [], prompts = [], libraryUploads = [];
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    // Gamma Connector's content script (extension/bridge.js), answering the
    // card's probe the way it does when it can serve the request — once
    // `connectorOn` is set; until then the page hears nothing, like a browser
    // without it (or with a version from before chat fetching). It takes the
    // card's open / show / close too, noting each in `connectorAsked`.
    await ctx.addInitScript(() => {
      window.connectorAsked = [];
      window.addEventListener("message", (e) => {
        const d = e.data;
        if (!window.connectorOn || d?.source !== "gamma-app") return;
        if (d.type === "connector-probe") {
          window.postMessage({ source: "gamma-connector", type: "connector-status", id: d.id, status: "ok" }, location.origin);
        } else if (d.type === "connector-tab") {
          window.connectorAsked.push({ do: d.do, background: !!d.background });
          const status = { open: "opened", show: "shown", close: "closed" }[d.do];
          window.postMessage({ source: "gamma-connector", type: "connector-tab", id: d.id, do: d.do, status }, location.origin);
        }
      });
    });
    await ctx.route(`**/api/ai/handoffs/${HID}**`, async (route) => {
      const req = route.request();
      const path = new URL(req.url()).pathname;
      if (path.endsWith("/go")) return route.fulfill({ contentType: "text/html", body: "<title>Opening</title>" });
      if (path.endsWith("/pdf")) {
        uploads.push(req.postDataBuffer());
        request = { ...request, status: "done", pages: 3 };
      }
      return route.fulfill({ json: request });
    });
    await ctx.route("**/api/ai/chat", (route) => {
      prompts.push(route.request().postDataJSON().prompt);
      return route.fulfill({ contentType: "application/x-ndjson", body: '{"delta":"Reading the delivered PDF now."}\n' });
    });
    ctx.on("request", (r) => { if (r.method() === "POST" && /\/api\/uploads\b/.test(r.url())) libraryUploads.push(r.url()); });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      const card = page.locator(".chatHandoff");
      await card.getByText("www.science.org asked for a CAPTCHA or bot check").waitFor();
      assert((await card.innerText()).includes(handoff.source), "the card names the paper");
      await card.getByText("Gamma Connector isn't answering in this browser").waitFor({ timeout: 8000 });
      assert((await card.innerText()).includes(`set it to ${server.base}`), "it says what address to set");
      // The Connector installed (or reloaded) meanwhile: coming back asks again.
      await page.evaluate(() => { window.connectorOn = true; window.dispatchEvent(new Event("focus")); });
      await card.getByText("Gamma Connector sends the PDF back from that tab by itself.").waitFor();

      // The Connector opens the publisher's tab itself: no Gamma page on the way.
      const pagesBefore = ctx.pages().length;
      await card.getByRole("button", { name: "Open www.science.org" }).click();
      await card.getByText("Finish in the tab that opened").waitFor();
      assertEq(JSON.stringify(await page.evaluate(() => window.connectorAsked)), JSON.stringify([{ do: "open", background: false }]));
      assertEq(ctx.pages().length, pagesBefore, "the page opened no window of its own");

      // The Connector took the tab: the card says so on its next look, and
      // brings that tab forward on request.
      request = { ...request, watched: true };
      await card.getByText("Gamma Connector is watching the tab").waitFor({ timeout: 8000 });
      await card.getByRole("button", { name: "Show the tab" }).click();
      await until(async () => (await page.evaluate(() => window.connectorAsked)).some((a) => a.do === "show"),
        { what: "the card asks to show the tab" });

      // A PDF dropped on the card goes to the request, not into the library.
      const drop = await page.evaluateHandle((bytes) => {
        const dt = new DataTransfer();
        dt.items.add(new File([new Uint8Array(bytes)], "paper.pdf", { type: "application/pdf" }));
        return dt;
      }, [...makePdf([["Delivered handoff paper"]])]);
      await card.dispatchEvent("dragover", { dataTransfer: drop });
      await card.dispatchEvent("drop", { dataTransfer: drop });
      await card.getByText("Got the PDF from your browser (3 pages).").waitFor();
      assertEq(uploads.length, 1, "one upload to the request");
      assert(uploads[0].includes(Buffer.from("%PDF")), "the upload carries the PDF");
      assertEq(libraryUploads.length, 0, "the page underneath did not import the drop");
      await until(async () => (await page.evaluate(() => window.connectorAsked)).some((a) => a.do === "close"),
        { what: "the Connector lets its tab go" });

      await until(() => prompts.length === 1, { what: "the chat continues by itself" });
      assertEq(prompts[0], `I got it in my browser — ${handoff.source} is available now. Please continue.`);
      await until(async () => (await page.locator(".chatPanel").innerText()).includes("Reading the delivered PDF now."));

      // A reload shows the settled card and sends nothing again.
      await page.reload();
      await page.locator(".chatHandoff.done").waitFor();
      await page.waitForTimeout(500);
      assertEq(prompts.length, 1, "no second continuation");
      assertEq(await page.locator(".chatHandoffContinue").count(), 0, "the conversation moved on: no Continue");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    }
  });

  await step("chat navigation: in the background the Connector takes a blocked fetch unasked; a reply's papers save to the library", async () => {
    const HID = "e2eHandoffBackground1";
    const handoff = { id: HID, host: "www.worldscientific.com", wall: "captcha", source: "doi:10.1142/e2e.bg" };
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [
      { id: "bg-q", role: "user", text: "Find papers on proximity junctions" },
      { id: "bg-a", role: "ai",
        text: "I read [Proximity effect in superconductors](https://arxiv.org/abs/2301.01234); see also "
          + "[A junction review](https://doi.org/10.1234/e2e.review). World Scientific asked for a bot check.",
        actions: [
          { kind: "fetch", tool: "fetch_paper", summary: "Fetched “2301.01234”", args: { source: "arXiv:2301.01234" },
            result: "Fetched PDF", url: "https://arxiv.org/pdf/2301.01234", title: "", pdf: true },
          { kind: "fetch", error: true, summary: "Needs your browser: www.worldscientific.com", tool: "fetch_paper",
            args: { source: handoff.source }, result: "error: blocked", handoff },
        ] },
    ] } });
    const { value: profile } = await alice.api("/api/prefs/profile");
    await alice.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), fetchInBackground: true } } });
    let request = { ...handoff, url: "https://www.worldscientific.com/doi/10.1142/e2e.bg", pdf_url: "", detail: "",
      status: "waiting", watched: false, note: "", background: false, pages: 0, from_url: "" };
    const clips = [];
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    await ctx.addInitScript(() => {
      window.connectorAsked = [];
      window.addEventListener("message", (e) => {
        const d = e.data;
        if (d?.source !== "gamma-app") return;
        if (d.type === "connector-probe") {
          window.postMessage({ source: "gamma-connector", type: "connector-status", id: d.id, status: "ok" }, location.origin);
        } else if (d.type === "connector-tab") {
          window.connectorAsked.push({ do: d.do, background: !!d.background });
          const status = { open: "opened", show: "shown", close: "closed" }[d.do];
          window.postMessage({ source: "gamma-connector", type: "connector-tab", id: d.id, do: d.do, status }, location.origin);
        }
      });
    });
    await ctx.route(`**/api/ai/handoffs/${HID}**`, (route) => {
      if (new URL(route.request().url()).pathname.endsWith("/store")) return route.fulfill({ status: 404, json: { detail: "not held" } });
      return route.fulfill({ json: request });
    });
    // The server's side of saving is POST /api/clip (tests/test_clip.py): here it only records.
    await ctx.route("**/api/clip", (route) => {
      const body = route.request().postDataJSON();
      clips.push(body);
      return route.fulfill({ json: { block_id: `saved-${clips.length}`, doc_id: `doc-${clips.length}`,
        title: body.title, existed: false, folder: body.folder, labels: [], open_url: "/" } });
    });
    await ctx.route("**/api/library/preview*", (route) => route.fulfill({ status: 404, json: { detail: "no registry record" } }));
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      const card = page.locator(".chatHandoff");
      await card.getByText("www.worldscientific.com asked for a CAPTCHA or bot check").waitFor();
      await until(async () => (await page.evaluate(() => window.connectorAsked)).length > 0,
        { what: "the card hands the request to the Connector without a click" });
      assertEq(JSON.stringify(await page.evaluate(() => window.connectorAsked)), JSON.stringify([{ do: "open", background: true }]));
      request = { ...request, watched: true, background: true };
      await card.getByText("Gamma Connector is getting it in a minimized window").waitFor({ timeout: 8000 });
      // The page wants the user: the card says so and offers the tab.
      request = { ...request, note: "check" };
      await card.getByText("The site is showing a bot check or CAPTCHA").waitFor({ timeout: 8000 });
      await card.locator("button.primary", { hasText: "Show the tab" }).click();
      await until(async () => (await page.evaluate(() => window.connectorAsked)).some((a) => a.do === "show"),
        { what: "the card asks to show the tab" });

      // The reply's papers: the one it read, the blocked one, the one it links.
      const pill = page.getByRole("button", { name: /3 papers in this reply · Save to library/ });
      await pill.click();
      const list = page.getByRole("group", { name: "Papers in this reply" });
      await list.getByText("Proximity effect in superconductors").waitFor();
      await list.getByRole("checkbox", { name: "Proximity effect in superconductors" }).check();
      await list.getByRole("checkbox", { name: "A junction review" }).check();
      await list.getByRole("button", { name: "Save 2 papers to library" }).click();
      await until(async () => (await list.getByText("Saved", { exact: true }).count()) === 2, { what: "both rows say Saved" });
      assertEq(JSON.stringify(clips.map((c) => [c.arxiv_id, c.doi, c.pdf_url, c.source_url, c.title])), JSON.stringify([
        ["2301.01234", "", "https://arxiv.org/pdf/2301.01234", "https://arxiv.org/abs/2301.01234", "Proximity effect in superconductors"],
        ["", "10.1234/e2e.review", "", "https://doi.org/10.1234/e2e.review", "A junction review"],
      ]));
      assert(clips.every((c) => c.folder === "" && c.save_copy === true && !c.doc_id), "into the library root, stored");
      assertNoProblems(page, [/api\/library\/(lookup|preview)/, /status of 404/]);
    } finally {
      await ctx.close();
      await alice.api("/api/prefs/profile", { method: "PUT", body: { value: profile || {} } });
      await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    }
  });

  await step("chat navigation: a change waits on its approval card; allowing for the chat, declining and always allowing", async () => {
    const notesPage = await alice.api("/api/pages", { method: "POST", body: { title: "Approvals page" } });
    const block = await alice.api("/api/blocks", { method: "POST", body: { parent_id: notesPage.id, content: "Draft summary" } });
    const { value: profile } = await alice.api("/api/prefs/profile");
    // The defaults: reading is allowed, a note edit asks.
    const { agentPerms: _perms, ...rest } = profile || {};
    await alice.api("/api/prefs/profile", { method: "PUT", body: { value: rest } });
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => {
      localStorage.setItem("gamma-ai-login-check", "off");
      window.chatBodies = [];
      window.approvalAnswers = [];
      const fetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        const url = String(input);
        if (url.endsWith("/api/ai/chat")) {
          window.chatBodies.push(JSON.parse(init.body));
          return Promise.resolve(new Response(new ReadableStream({
            start(controller) {
              window.chatStream = {
                push: (event) => controller.enqueue(new TextEncoder().encode(JSON.stringify(event) + "\n")),
                finish: () => controller.close(),
              };
            },
          }), { headers: { "Content-Type": "application/x-ndjson" } }));
        }
        if (url.includes("/api/ai/approvals/")) {
          window.approvalAnswers.push({ id: decodeURIComponent(url.split("/").pop()), ...JSON.parse(init.body) });
          return Promise.resolve(new Response(JSON.stringify({ ok: true }), { headers: { "Content-Type": "application/json" } }));
        }
        return fetch(input, init);
      };
    });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${notesPage.id}`);
    const push = (event) => page.evaluate((e) => window.chatStream.push(e), event);
    const finish = () => page.evaluate(() => window.chatStream.finish());
    const input = page.getByRole("combobox", { name: "Message AI" });
    const send = async (text, n) => {
      await input.fill(text);
      await input.press("Enter");
      await until(() => page.evaluate((count) => window.chatBodies.length === count, n), { what: `message ${n} is sent` });
      return page.evaluate(() => window.chatBodies.at(-1));
    };
    const card = page.locator(".chatApproval");
    const answers = () => page.evaluate(() => window.approvalAnswers);
    const edit = { block_id: block.id, mode: "append" };
    const editCard = (id) => ({ id, call_id: `c-${id}`, tool: "edit_block", perm: "block_edit", args: edit, timeout: 600,
      preview: { page_id: notesPage.id, title: "Approvals page", block_id: block.id, mode: "append",
        diff: [["ctx", "Draft summary"], ["ins", "\nThe key result."]] } });
    const edited = (approval) => ({ action: { kind: "edit", tool: "edit_block", summary: "Appended to a note in “Approvals page”",
      page_id: notesPage.id, block_id: block.id, mode: "append", title: "Approvals page", args: edit, result: "ok", approval } });
    try {
      await input.waitFor();
      await page.waitForLoadState("networkidle");

      // 1. The note edit waits on its card; "Allow in this chat" runs it.
      const first = await send("Add the key result to my notes", 1);
      assertEq(first.permissions.block_edit, "ask", "a note edit asks by default");
      assertEq(first.permissions.read, "allow");
      assertEq(JSON.stringify(first.granted), "[]");
      await push({ step: { id: "c-a1", tool: "edit_block", args: edit } });
      await push({ approval: editCard("a1") });
      await card.getByText("Add to a note in “Approvals page”").waitFor();
      assert((await card.innerText()).includes("Approval needed · Edit note blocks"), "the card names the permission");
      assertEq((await card.locator("ins").innerText()).trim(), "The key result.");
      await page.locator(".chatSteps").getByText("Waiting for your approval").waitFor();
      assertEq(await page.locator(".chatThinking").count(), 0, "no Thinking pill while the card waits");
      if (flags.keep) {
        await card.screenshot({ path: `${server.dir}/approval-card.png`, animations: "disabled" });
        await page.locator(".chatPanel").screenshot({ path: `${server.dir}/approval-panel.png`, animations: "disabled" });
      }
      await card.getByRole("button", { name: "Allow in this chat", exact: true }).click();
      await card.getByText("Allowed. The assistant goes on…").waitFor();
      await page.locator(".chatSteps").getByText("Appending to a note…").waitFor();
      assertEq(JSON.stringify(await answers()), JSON.stringify([{ id: "a1", decision: "chat" }]));
      await push(edited("chat"));
      await until(async () => !(await card.count()), { what: "the card goes once the call ran" });
      await push({ delta: "Added it." });
      await finish();
      const chip = page.getByRole("button", { name: "Allowed in this chat: Edit note blocks. Ask again", exact: true });
      await chip.waitFor();
      if (flags.keep) await page.locator(".chatComposer").screenshot({ path: `${server.dir}/approval-granted.png`, animations: "disabled" });

      // 2. The conversation's grant rides along; a read set to ask is declined.
      await page.locator('[title^="Chat settings"]').click();
      const popover = page.locator(".chatSettingsPop");
      await popover.getByRole("button", { name: "Fetch documents", exact: true }).click();
      await page.locator(".uiSelectMenu").getByRole("button", { name: "Ask", exact: true }).click();
      await page.locator('[title^="Chat settings"]').click();
      const second = await send("Fetch the paper it cites", 2);
      assertEq(JSON.stringify(second.granted), JSON.stringify(["block_edit"]));
      assertEq(second.permissions.web_read, "ask");
      await push({ step: { id: "c-a2", tool: "fetch_paper", args: { source: "doi:10.1234/e2e.cited" } } });
      await push({ approval: { id: "a2", call_id: "c-a2", tool: "fetch_paper", perm: "web_read",
        args: { source: "doi:10.1234/e2e.cited" }, preview: {}, timeout: 600 } });
      await card.getByText("Fetch doi:10.1234/e2e.cited").waitFor();
      // Don't allow asks, optionally, what to do instead; Back returns to the choices.
      await card.getByRole("button", { name: "Don't allow", exact: true }).click();
      const instead = card.getByRole("textbox", { name: "What should the assistant do instead? (optional)" });
      await instead.fill("Use the arXiv copy");
      await card.getByRole("button", { name: "Back", exact: true }).click();
      await card.getByRole("button", { name: "Allow once", exact: true }).waitFor();
      await card.getByRole("button", { name: "Don't allow", exact: true }).click();
      await instead.fill("Use the arXiv copy");
      if (flags.keep) await card.screenshot({ path: `${server.dir}/approval-decline.png`, animations: "disabled" });
      await instead.press("Enter");
      await card.getByText("Not allowed. The assistant goes on without it…").waitFor();
      assertEq(JSON.stringify((await answers())[1]), JSON.stringify({ id: "a2", decision: "deny", note: "Use the arXiv copy" }));
      await push({ action: { kind: "error", tool: "fetch_paper", summary: "Not allowed: fetch_paper", error: true,
        declined: true, approval: "deny", args: { source: "doi:10.1234/e2e.cited" }, result: "declined: the user did not allow this call" } });
      await push({ delta: "I left it unfetched." });
      await finish();
      await page.locator(".chatSteps").last().getByText("1 not allowed").waitFor();
      assertEq(await page.locator(".chatStepsFailed").count(), 0, "a declined call is not a failure");
      if (flags.keep) await page.locator(".chatPanel").screenshot({ path: `${server.dir}/approval-declined.png`, animations: "disabled" });

      // 3. Asking again for the chat, then "Always allow" sets it in Settings.
      await chip.click();
      await until(async () => !(await chip.count()), { what: "the chip goes once the chat asks again" });
      const third = await send("Add one more line", 3);
      assertEq(JSON.stringify(third.granted), "[]");
      await push({ step: { id: "c-a3", tool: "edit_block", args: edit } });
      await push({ approval: editCard("a3") });
      await card.getByRole("button", { name: "Always allow", exact: true }).click();
      await until(async () => (await alice.api("/api/prefs/profile")).value?.agentPerms?.notes?.block_edit === "allow",
        { what: "Always allow sets the notes chat's note editing to Allow" });
      assertEq(JSON.stringify((await answers()).map((a) => a.decision)), JSON.stringify(["chat", "deny", "always"]));
      await push(edited("always"));
      await push({ delta: "Done." });
      await finish();
      await until(async () => !(await page.getByRole("button", { name: "Stop generating", exact: true }).count()));
      const saved = await alice.api(`/api/chats/${notesPage.id}`);
      assert(saved.messages.every((m) => !m.approval), "a waiting card is never saved with the reply");
      assertEq(saved.messages.filter((m) => m.role === "ai").flatMap((m) => m.actions || []).map((a) => a.approval).join(),
        "chat,deny,always");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api("/api/prefs/profile", { method: "PUT", body: { value: profile || {} } });
      await alice.api(`/api/chats/${notesPage.id}`, { method: "PUT", body: { messages: [] } });
    }
  });
}
