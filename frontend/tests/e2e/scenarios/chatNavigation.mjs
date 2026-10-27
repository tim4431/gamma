import { readFile } from "node:fs/promises";
import { FAKE_AI_MODELS, fakeAiModels, live, wanted } from "../harness.mjs";
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
            await page.locator(".menuPopover").getByRole("menuitemcheckbox", { name: "AI Chat" }).click();
            await page.getByRole("button", { name: "View", exact: true }).click();
            await until(async () => (await page.locator(".chatPanel").innerText()).includes("Continued with the panel closed."));
            await page.getByRole("button", { name: "Stop generating", exact: true }).waitFor();
            // Text after the last report counts as a "~" estimate in the Responding pill.
            const live = page.locator(".chatSteps .chatMsgUsage");
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

  await step("chat navigation: a draft typed while the stored conversation loads stays", async () => {
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    await ctx.route("**/api/chats/home", async (route) => { await held; await route.continue(); });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      const input = page.getByRole("combobox", { name: "Message AI" });
      await input.waitFor();
      await input.fill("Typed before the load");
      const loaded = page.waitForResponse((res) => res.url().endsWith("/api/chats/home"));
      release();
      await loaded;
      await page.waitForLoadState("networkidle");
      assertEq(await input.inputValue(), "Typed before the load");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

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
      await page.getByRole("button", { name: "Export chat", exact: true }).click();
      await page.getByRole("menuitem", { name: "Export context", exact: true }).click();
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

  await step("chat navigation: Alt over New chat arms Discard, Alt+click discards the conversation; a plain click keeps it in History", async () => {
    const own = await alice.api("/api/pages", { method: "POST", body: { title: "Discarded chat" } });
    const historyCount = async () => (await alice.api(`/api/chat-history?bucket=${own.id}`)).sessions.length;
    const seed = (text) => alice.api(`/api/chats/${own.id}`, { method: "PUT", body: { messages: [
      { role: "user", text }, { role: "ai", text: `${text}: answered.` },
    ] } });
    await seed("Throw this one away");
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${own.id}`);
    try {
      const panel = page.locator(".chatPanel");
      const newChat = page.getByRole("button", { name: "New chat", exact: true });
      const discard = page.getByRole("button", { name: "Discard chat", exact: true });
      await until(async () => (await panel.innerText()).includes("Throw this one away: answered."));
      // Alt over the button shows the discard before the click (its name and
      // the danger tint); releasing Alt or leaving puts New chat back.
      await newChat.hover();
      await page.keyboard.down("Alt");
      await discard.waitFor();
      assert(await discard.evaluate((el) => el.classList.contains("danger")), "the armed button is red");
      await page.keyboard.up("Alt");
      await newChat.waitFor();
      await page.keyboard.down("Alt");
      await discard.waitFor();
      await panel.locator(".chatMessages").hover();
      await newChat.waitFor();
      await page.keyboard.up("Alt");
      await newChat.click({ modifiers: ["Alt"] });
      await until(async () => !(await panel.innerText()).includes("Throw this one away"), { what: "Alt+click empties the chat" });
      assertEq((await alice.api(`/api/chats/${own.id}`)).messages.length, 0, "the active conversation is cleared");
      assertEq(await historyCount(), 0, "nothing reaches History");
      await newChat.hover();
      await page.keyboard.down("Alt");
      await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
      assertEq(await discard.count(), 0, "an empty chat has nothing to discard");
      await page.keyboard.up("Alt");
      await seed("Keep this one");
      await page.reload();
      await until(async () => (await panel.innerText()).includes("Keep this one: answered."));
      await newChat.click();
      await until(async () => (await historyCount()) === 1, { what: "a plain click archives" });
      // The archived conversation exports from its History row's menu.
      await page.getByRole("button", { name: "Chat history", exact: true }).click();
      await page.locator(".chatHistRow", { hasText: "Keep this one" }).click({ button: "right" });
      const downloadEvent = page.waitForEvent("download");
      await page.getByRole("menuitem", { name: "Export", exact: true }).click();
      const download = await downloadEvent;
      assertEq(download.suggestedFilename(), "Keep this one.md", "no pictures: the bare transcript");
      const text = await readFile(await download.path(), "utf8");
      assert(text.startsWith("# Keep this one\n") && text.includes("## AI\n\nKeep this one: answered."), text);
      assertNoProblems(page);
    } finally { await ctx.close(); }
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
    const out = await server.manage("create-workspace", "Chat readers", "alice", "shared");
    const labId = (out.match(/workspace (\S+)/) || [])[1];
    assert(labId, `shared workspace id from: ${out}`);
    await server.manage("set-member", labId, "bob", "viewer");
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
        // the retried answer's save, not the refused request's (two messages, an error kind, too)
        return saved.messages?.length === 2 && saved.messages.at(-1).errorKind === "overloaded" && !saved.messages.at(-1).partial;
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
      // One pill from the start: "Thinking", then the step running now.
      await pill.filter({ hasText: "Thinking" }).waitFor();
      await page.evaluate(() => window.chatStream.push({ step: { id: "c1", tool: "search_library", args: { query: "cavity" } } }));
      await pill.filter({ hasText: "Searching library for “cavity”…" }).waitFor();
      assertEq(await pill.count(), 1, "the running step takes the Thinking pill's words");
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

  await step("chat navigation: a call that hands papers to helpers lists each helper with what it is doing, and its chip keeps their calls", async () => {
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
      await input.fill("What do these two papers measure?");
      await input.press("Enter");
      await page.waitForFunction(() => !!window.chatStream);
      const push = (...events) => page.evaluate((list) => list.forEach((event) => window.chatStream.push(event)), events);
      const pill = page.locator(".chatSteps");
      const rows = page.locator(".chatHelpers .chatToolActionHead");
      const cat = { id: "h1", label: "Cat qubits", state: "reading", steps: 0 };
      const transmon = { id: "h2", label: "Transmon readout", state: "reading", steps: 0 };
      // The pill says helpers are at work; each has a row from the moment it starts.
      await push({ step: { id: "c1", tool: "read_paper", args: { source: "arXiv:1905.00450" }, batch: 2 } },
        { helper: cat }, { helper: transmon });
      await pill.filter({ hasText: "2 helpers are reading documents…" }).waitFor();
      await rows.filter({ hasText: "Cat qubits" }).filter({ hasText: "Starting…" }).waitFor();
      assertEq(await rows.count(), 2);
      assertEq(await rows.locator(".transferSpin").count(), 2, "both are still at work");
      // A row follows its own helper: the call it is on, then its answer, then done.
      await push({ helper: { ...cat, step: { id: "f1", tool: "fetch_paper", args: { source: "arXiv:1905.00450" } } } });
      await rows.filter({ hasText: "Cat qubits" }).filter({ hasText: "Fetching arXiv:1905.00450…" }).waitFor();
      await push({ helper: { ...cat, state: "answering", steps: 1 } }, { helper: { ...transmon, state: "failed" } });
      await rows.filter({ hasText: "Cat qubits" }).filter({ hasText: "Writing its answer…" }).waitFor();
      await rows.and(page.locator(".err")).filter({ hasText: "Could not read it" }).waitFor();
      await push({ helper: { ...cat, state: "done", steps: 1 } });
      await rows.filter({ hasText: "Cat qubits" }).filter({ hasText: "Done · 1 step" }).waitFor();
      assertEq(await rows.locator(".transferSpin").count(), 0, "nothing spins once both ended");
      assertEq(await pill.count(), 1, "the running call still speaks through the one pill");
      // The call's chips land: the rows go, and the helper's own calls stay under its chip.
      await push({ action: { kind: "helper", tool: "read_paper", summary: "Helper read “Cat qubits”", steps: 2,
        args: { source: "arXiv:1905.00450", question: "what do they measure?" }, result: "A helper read arXiv:1905.00450…",
        children: [{ kind: "fetch", tool: "fetch_paper", summary: "Fetched “Cat qubits”", version: "preprint", pdf_pages: [1, 4] },
          { kind: "fetch", tool: "fetch_paper", summary: "Fetched “Cat qubits”", version: "preprint", pdf_pages: [4, 9] }] } },
      { action: { kind: "error", tool: "read_paper", summary: "error: the provider did not answer", args: {}, result: "error", error: true } },
      { delta: "They measure the bit-flip time." });
      await page.evaluate(() => window.chatStream.finish());
      await pill.filter({ hasText: "2 steps · used 1 helper" }).waitFor();
      assertEq(await rows.count(), 0);
      await pill.click();
      // The helper's chip says it is one and how much it did; its reads are told apart by their pages.
      const chip = page.locator(".chatToolActionHead", { hasText: "Helper read “Cat qubits”" });
      assert((await chip.innerText()).includes("2 steps"), "the helper's chip counts its calls");
      await chip.click();
      const child = page.locator(".chatToolChildren .chatToolActionHead");
      assertEq(await child.count(), 2);
      assert((await child.first().innerText()).includes("pp. 1–4"), "the helper's reads are listed under its chip");
      assert((await child.last().innerText()).includes("pp. 4–9"), "each with the pages it read");
      assert((await child.first().innerText()).includes("arXiv preprint"), "and the copy it read");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat navigation: the effort and speed menus offer the model's own levels, the nearest effort is sent, and a reply names them", async () => {
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    // One page load per model: its levels and speed tiers (the provider's
    // listing, models.dev or the wire itself, pinned by the backend tests)
    // are stubbed here.
    const load = async (efforts, speeds = []) => {
      const ctx = await alice.context(browser);
      await fakeAiModels(ctx);
      await ctx.route("**/api/ai/model-info?**", (route) => route.fulfill({
        json: { model: "model", context_window: null, source: "", efforts, efforts_source: "provider",
                speeds, speeds_source: "provider" } }));
      await ctx.addInitScript(() => {
        localStorage.setItem("gamma-ai-login-check", "off");
        window.chatBodies = [];
        const fetch = window.fetch.bind(window);
        window.fetch = (input, init) => {
          if (!String(input).endsWith("/api/ai/chat")) return fetch(input, init);
          const body = JSON.parse(init.body);
          window.chatBodies.push(body);
          const lines = [{ model: { id: body.model, name: "model", effort: body.effort, speed: body.speed } },
            { delta: "ok" }];
          return Promise.resolve(new Response(lines.map((l) => JSON.stringify(l)).join("\n") + "\n",
            { headers: { "Content-Type": "application/x-ndjson" } }));
        };
      });
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
      await page.getByRole("combobox", { name: "Message AI" }).waitFor();
      return { ctx, page };
    };
    const chip = (page) => page.locator(".chatModelChip .uiSelectBtn");
    // The speed shows as a glyph, not a word: the chip's text names the model
    // and effort, the glyph and the tooltip the tier.
    const chipSpeed = async (page) => [await chip(page).locator(".chatSpeedGlyph").count(),
      await chip(page).getAttribute("title")];
    const menuItems = (page) => page.locator(".uiSelectMenu .ctxMenuItem").allInnerTexts();
    // Until model-info answers, the menu offers the generic levels: wait for the model's own.
    const menuShows = (page, want, what) => until(async () => (await menuItems(page)).join() === want, { what });
    // The pick is an account preference: wait for it to reach the profile
    // before the next page load reads it.
    const saved = (effort) => until(async () => ((await alice.api("/api/prefs/profile")).value?.chatEffort || "") === effort,
      { what: `the profile holds effort "${effort}"` });
    const send = async (page) => {
      const input = page.getByRole("combobox", { name: "Message AI" });
      await input.fill("Which effort?");
      await input.press("Enter");
      await until(() => page.evaluate(() => window.chatBodies.length === 1), { what: "the message is sent" });
      return page.evaluate(() => [window.chatBodies[0].effort, window.chatBodies[0].speed]);
    };

    let { ctx, page } = await load(["low", "medium", "high", "xhigh"]);
    try {
      await chip(page).click();
      await menuShows(page, "model,Default,low,medium,high,xhigh", "the model's own levels");
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "xhigh" }).click();
      await until(async () => (await chip(page).innerText()) === "model · xhigh", { what: "the chip names the effort" });
      assertEq((await send(page)).join(), "xhigh,", "the effort, and no speed tier to ask for");
      await page.locator(".chatMsgModel", { hasText: "model · xhigh" }).waitFor();
      await saved("xhigh");
      assertNoProblems(page);
    } finally { await ctx.close(); }

    // A model without xhigh gets the nearest level it takes; the preference stays.
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    ({ ctx, page } = await load(["low", "medium", "high"]));
    try {
      await until(async () => (await chip(page).innerText()) === "model · high", { what: "the nearest level" });
      assertEq((await send(page)).join(), "high,");
      await page.locator(".chatMsgModel", { hasText: "model · high" }).waitFor();
      await chip(page).click();
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "Default" }).click();
      await until(async () => (await chip(page).innerText()) === "model", { what: "Default leaves the effort out" });
      await saved("");
      assertNoProblems(page);
    } finally { await ctx.close(); }

    // A model with no effort control and no speed tiers: neither section.
    ({ ctx, page } = await load([]));
    try {
      await chip(page).click();
      await menuShows(page, "model", "no effort levels to offer");
      assertEq(await page.locator(".uiSelectMenu", { hasText: "Reasoning effort" }).count(), 0);
      assertEq(await page.locator(".uiSelectMenu", { hasText: "Speed" }).count(), 0);
      assertNoProblems(page);
    } finally { await ctx.close(); }

    // A model whose provider sells service tiers: the Speed section offers
    // its own, the pick rides on the request and the reply names it.
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    ({ ctx, page } = await load(["low", "high"], ["flex", "fast"]));
    try {
      await chip(page).click();
      await menuShows(page, "model,Default,low,high,Default,flex,fast", "effort then speed");
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "fast" }).click();
      await until(async () => (await chipSpeed(page)).join() === "1,Model, reasoning effort and speed (fast)",
        { what: "the chip carries the speed glyph" });
      // Both knobs at once: the words are the model and effort, then the glyph.
      await chip(page).click();
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "high" }).click();
      await until(async () => (await chip(page).innerText()) === "model · high", { what: "the chip names both" });
      assertEq((await send(page)).join(), "high,fast");
      const foot = page.locator(".chatMsgModel", { hasText: "model · high" });
      await foot.waitFor();
      assertEq(await foot.locator(".chatSpeedGlyph").count(), 1, "the reply's foot carries the glyph too");
      await until(async () => ((await alice.api("/api/prefs/profile")).value?.chatSpeed || "") === "fast",
        { what: 'the profile holds speed "fast"' });
      assertNoProblems(page);
    } finally { await ctx.close(); }

    // The same preference on a model whose provider sells none: nothing is
    // asked for, and the kept pick comes back on the next model that has it.
    ({ ctx, page } = await load(["low", "high"], []));
    try {
      await until(async () => (await chipSpeed(page)).join() === "0,Model, reasoning effort and speed",
        { what: "no speed to ask for" });
      assertEq(await page.locator(".uiSelectMenu", { hasText: "Speed" }).count(), 0);
      assertNoProblems(page);
    } finally { await ctx.close(); }

    ({ ctx, page } = await load(["low", "high"], ["fast"]));
    try {
      await until(async () => (await chipSpeed(page))[0] === 1, { what: "the kept speed" });
      await chip(page).click();
      // The second "Default" is the Speed section's (effort's comes first).
      await page.locator(".uiSelectMenu .ctxMenuItem", { hasText: "Default" }).last().click();
      await until(async () => (await chipSpeed(page))[0] === 0, { what: "Default drops the speed" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    }
  });

  // Several connections: the menu lists one connection's models — the picked
  // one's, else the first's (providerModels in chat/modelPrefs.js) — never all.
  await step("chat navigation: the model menu lists one connection's models, the first's when none is picked", async () => {
    const models = { ...FAKE_AI_MODELS, default: "a:one", models: [
      { id: "a:one", provider: "a", provider_name: "First", model: "one" },
      { id: "a:two", provider: "a", provider_name: "First", model: "two" },
      { id: "b:three", provider: "b", provider_name: "Second", model: "three" },
    ] };
    const pick = (value) => alice.api("/api/prefs/ai-provider", { method: "PUT", body: { value } });
    // `shown` is the chip's model once the account's pick has loaded.
    const menu = async (shown) => {
      const ctx = await alice.context(browser);
      await fakeAiModels(ctx, models);
      await ctx.route("**/api/ai/model-info?**", (route) => route.fulfill({
        json: { model: "", context_window: null, source: "", efforts: [], efforts_source: "provider",
                speeds: [], speeds_source: "provider" } }));
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      try {
        const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
        await page.getByRole("combobox", { name: "Message AI" }).waitFor();
        const chip = page.locator(".chatModelChip .uiSelectBtn");
        await until(async () => (await chip.innerText()) === shown, { what: `the chip shows "${shown}"` });
        await chip.click();
        const items = await page.locator(".uiSelectMenu .ctxMenuItem").allInnerTexts();
        assertNoProblems(page);
        return items.join();
      } finally { await ctx.close(); }
    };
    try {
      await pick("");
      assertEq(await menu("one"), "one,two", "nothing picked: the first connection");
      await pick("b");
      assertEq(await menu("three"), "three", "the picked connection");
      await pick("removed");
      assertEq(await menu("one"), "one,two", "a pick that is gone: the first connection");
    } finally { await pick(""); }
  });

  // A fetch_paper a publisher stopped (chat/FetchHandoffCards.jsx): the reply's
  // card opens the page through /go, follows the request on the server (the
  // Connector taking the tab shows), takes a PDF dropped on it — never the
  // library underneath — and then offers to continue the conversation. This
  // is the card a request left behind: while a reply is still running the
  // server waits on the card itself, which the backend tests cover
  // (backend/tests/test_fetch_handoff.py, along with these endpoints).
  await step("chat navigation: a card left behind gets the PDF from the browser and offers to continue", async () => {
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
    // This is the by-hand path: "Fetch blocked papers in the background" is
    // on by default, and would hand the request over before the user clicks.
    const { value: profile } = await alice.api("/api/prefs/profile");
    await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { fetchInBackground: false } } });
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

      // The chat never speaks in the user's name: the card offers to go on.
      const goOn = page.locator(".chatHandoffContinue");
      await goOn.waitFor();
      assertEq(prompts.length, 0, "nothing was sent for the user");
      await goOn.click();
      await until(() => prompts.length === 1, { what: "Continue asks the chat to go on" });
      assertEq(prompts[0], `I got it in my browser — ${handoff.source} is available now. Please continue.`);
      await until(async () => (await page.locator(".chatPanel").innerText()).includes("Reading the delivered PDF now."));

      // A reload shows the settled card and offers nothing again.
      await page.reload();
      await page.locator(".chatHandoff.done").waitFor();
      await page.waitForTimeout(500);
      assertEq(prompts.length, 1, "no second continuation");
      assertEq(await goOn.count(), 0, "the conversation moved on: no Continue");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api("/api/prefs/profile", { method: "PUT", body: { value: profile || {} } });
      await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    }
  });

  // A reply a limit stopped (the stream's last {"limit"} line, saved on the
  // message) or the output cap cut off ({"truncated"}) gets a pill saying
  // why and a Continue under it while it is the conversation's last reply
  // (chat/coverage.js). Continue sends a message in the user's name, as the
  // handoff card's does. Under Unlimited rounds a running reply has a row in
  // Background tasks whose Stop ends it. The server side (the limit lines,
  // Unlimited, Stop ending the loop) is backend/tests/test_ai_tool_limits.py's.
  await step("chat navigation: a reply that stopped short says why and offers Continue, which carries on as the user's message; an Unlimited reply shows in Background tasks, whose Stop ends it", async () => {
    // No title, so the tray's row is named from the first question (a save
    // without one keeps the title an earlier step gave the conversation).
    const { title: homeTitle } = await alice.api("/api/chats/home");
    await alice.api("/api/chats/home", { method: "PUT", body: { title: "", messages: [
      { id: "limit-q", role: "user", text: "Rename every page to AuthorYear" },
      { id: "limit-a", role: "ai", text: "Renamed 40 pages so far.\n\n*(stopped: tool-round limit reached)*",
        limit: { kind: "rounds", rounds: 32, changes: 40 } },
    ] } });
    const { value: profile } = await alice.api("/api/prefs/profile");
    const bodies = [];
    const answers = [
      '{"delta":"Renamed the rest."}\n{"limit":{"kind":"changes","rounds":3,"changes":200}}\n',
      '{"delta":"Renamed some more"}\n{"truncated":true}\n',
    ];
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    await ctx.route("**/api/ai/chat", async (route) => {
      const n = bodies.push(route.request().postDataJSON());
      if (n <= answers.length) return route.fulfill({ contentType: "application/x-ndjson", body: answers[n - 1] });
      // The Unlimited reply runs until it is stopped.
      await held;
      try { await route.fulfill({ contentType: "application/x-ndjson", body: '{"delta":"Too late."}\n' }); } catch { /* the page stopped it */ }
    });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      const chat = page.locator(".chatPanel");
      const reply = (i) => chat.locator(`.chatBubbleRow[data-msg-idx="${i}"]`);
      const pill = (text) => chat.locator(".chatCoverage .chatPill", { hasText: text });
      const note = (text) => chat.locator(".chatCoverageNote", { hasText: text });
      const goOn = chat.locator(".chatLimitActions").getByRole("button", { name: "Continue", exact: true });
      const input = page.getByRole("combobox", { name: "Message AI" });
      // The one Continue there is sits under the conversation's last reply.
      const onlyUnderLast = async (last, what) => {
        await until(async () => (await goOn.count()) === 1, { what: `one Continue, ${what}` });
        assertEq(await reply(last).locator(".chatLimitActions").count(), 1, `Continue is under the last reply, ${what}`);
      };

      // The pill says which limit and where to lift it.
      await pill("Stopped at the tool-round limit").click();
      await note("used its 32 tool rounds").waitFor();
      assert((await note("used its 32 tool rounds").innerText()).includes("Settings / AI / Tool usage / Tool limits"),
        "the pill names where the limit is set");

      // The chat never speaks in the user's name by itself, nor over a draft.
      await goOn.waitFor();
      assertEq(bodies.length, 0, "nothing was sent for the user");
      await input.fill("x");
      await until(async () => (await goOn.count()) === 0, { what: "a draft hides Continue" });
      await input.fill("");
      await goOn.waitFor();

      // Continue after a limit: a new message, with a fresh budget.
      await goOn.click();
      await until(() => bodies.length === 1, { what: "Continue sends a message" });
      assertEq(bodies[0].prompt, "Please continue where you stopped.");
      assertEq(bodies[0].tool_rounds, 32);
      assertEq(bodies[0].tool_rounds_unlimited, undefined, "Unlimited is off");
      await reply(3).getByText("Renamed the rest.").waitFor();
      await pill("Stopped at the change limit").click();
      await note("after 200 changes").waitFor();
      await onlyUnderLast(3, "after the change limit");

      // Continue after the output cap.
      await goOn.click();
      await until(() => bodies.length === 2, { what: "Continue after the output cap" });
      await reply(5).getByText("Renamed some more").waitFor();
      await reply(5).locator(".chatCoverage .chatPill", { hasText: "Reply cut off at the output limit" }).waitFor();
      await onlyUnderLast(5, "after the output cap");

      // A reload keeps both from the saved conversation.
      await until(async () => (await alice.api("/api/chats/home")).messages?.[5]?.truncated === true,
        { what: "the cut-off reply is saved" });
      await page.reload();
      await reply(5).getByText("Renamed some more").waitFor();
      await onlyUnderLast(5, "after a reload");
      await reply(1).locator(".chatCoverage .chatPill", { hasText: "Stopped at the tool-round limit" }).waitFor();
      assertEq(await reply(1).locator(".chatLimitActions").count(), 0, "an earlier reply offers no Continue");

      // Unlimited rounds: the request says so, and the running reply has a
      // row in Background tasks whose Stop ends it.
      await alice.api("/api/prefs/profile", { method: "PATCH", body: { set: { toolRoundsUnlimited: true } } });
      await page.reload();
      await until(async () => (await page.evaluate(() => localStorage.getItem("gamma-ai-tool-rounds-unlimited"))) === "1",
        { what: "the profile's Unlimited rounds reaches the page" });
      await goOn.click();
      await until(() => bodies.length === 3, { what: "Continue under Unlimited" });
      assertEq(bodies[2].tool_rounds_unlimited, true);
      assertEq(bodies[2].tool_rounds, 32, "the number is kept for when Unlimited is off");
      await page.click("button[aria-label='Background tasks']");
      const tray = page.locator(live(".tasksPopover"));
      const task = tray.locator(live(".taskRow"), { hasText: "AI reply — Rename every page to AuthorYear" });
      await task.waitFor();
      assert((await task.innerText()).includes("Unlimited rounds"), "the row says why it is there");
      await task.locator('button[aria-label^="Stop AI reply"]').click();
      await until(async () => (await reply(7).innerText()).includes("(stopped)"), { what: "the reply ends stopped" });
      await task.waitFor({ state: "detached" });
      await page.waitForTimeout(300);
      assertEq(await goOn.count(), 0, "Stop is no limit: no Continue");
      assertNoProblems(page);
    } finally {
      release();
      await ctx.close();
      await alice.api("/api/prefs/profile", { method: "PUT", body: { value: profile || {} } });
      await alice.api("/api/chats/home", { method: "PUT", body: { title: homeTitle || "", messages: [] } });
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
      await card.getByText("Gamma Connector is getting it in a tab of its own").waitFor({ timeout: 8000 });
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
      assertEq(await page.locator(".chatSteps .shimmerText").count(), 0, "nothing says work is under way while the card waits");
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
      // A narrow chat: the chip's words give way and the send button stays inside the composer.
      const composer = page.locator(".chatComposer");
      await composer.evaluate((el) => { el.style.width = "200px"; });
      const narrow = await page.evaluate(() => {
        const box = document.querySelector(".chatComposer").getBoundingClientRect();
        const sendBtn = document.querySelector('.chatComposer button[aria-label="Send"]').getBoundingClientRect();
        const label = document.querySelector(".chatGrantLabel");
        return { inside: sendBtn.right <= box.right, given: label.clientWidth < label.scrollWidth };
      });
      if (flags.keep) await composer.screenshot({ path: `${server.dir}/approval-granted-narrow.png`, animations: "disabled" });
      await composer.evaluate((el) => { el.style.width = ""; });
      assert(narrow.inside, "the send button stays inside a narrow composer");
      assert(narrow.given, "the grant chip's words give way first");

      // 2. The conversation's grant rides along; a read set to ask is declined.
      await page.locator('[title^="Chat settings"]').click();
      const popover = page.locator(".chatSettingsPop");
      await popover.getByRole("button", { name: "Fetch documents", exact: true }).click();
      await page.locator(".uiSelectMenu").getByRole("menuitemradio", { name: "Ask", exact: true }).click();
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
      // The final save queues behind the reply's checkpoints (one of which
      // held the waiting card), so it can land after Stop goes.
      const saved = await until(async () => {
        const chat = await alice.api(`/api/chats/${notesPage.id}`);
        return chat.messages.every((m) => !m.partial) && chat;
      }, { what: "the finished reply is saved" });
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

  await step("chat navigation: the agent's note changes revert from their rows — at once, or after asking when the note changed since", async () => {
    const notesPage = await alice.api("/api/pages", { method: "POST", body: { title: "Revert page" } });
    const one = await alice.api("/api/blocks", { method: "POST", body: { parent_id: notesPage.id, content: "First draft" } });
    const two = await alice.api("/api/blocks", { method: "POST", body: { parent_id: notesPage.id, content: "Second draft", before: one.position } });
    // What the agent's edit_block calls wrote, and what their actions recorded.
    const edited = async (block, before, after) => {
      await alice.api(`/api/blocks/${block.id}`, { method: "PUT", body: { content: after } });
      return { kind: "edit", tool: "edit_block", mode: "replace", page_id: notesPage.id, block_id: block.id,
        title: "Revert page", summary: "Edited a note in “Revert page”", args: { block_id: block.id },
        result: `ok — block [${block.id}] updated`, revert: { before, after } };
    };
    const actions = [await edited(one, "First draft", "First draft, improved by AI"),
      await edited(two, "Second draft", "Second draft, improved by AI")];
    // The user rewrote the agent's words in the second note since.
    await alice.api(`/api/blocks/${two.id}`, { method: "PUT", body: { content: "Second draft, improved by me" } });
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => {
      localStorage.setItem("gamma-ai-login-check", "off");
      window.chatBodies = [];
      const fetch = window.fetch.bind(window);
      window.fetch = (input, init) => {
        if (String(input).endsWith("/api/ai/chat")) {
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
        return fetch(input, init);
      };
    });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${notesPage.id}`);
    const input = page.getByRole("combobox", { name: "Message AI" });
    const send = async (text, n) => {
      await input.fill(text);
      await input.press("Enter");
      await until(() => page.evaluate((count) => window.chatBodies.length === count && !!window.chatStream, n), { what: `message ${n} is sent` });
    };
    const noteText = (id) => alice.api(`/api/blocks/${id}`).then((b) => b.content);
    try {
      await input.waitFor();
      await page.waitForLoadState("networkidle");
      await send("Improve both notes", 1);
      await page.evaluate((list) => {
        for (const action of list) window.chatStream.push({ action });
        window.chatStream.push({ delta: "Improved both notes." });
        window.chatStream.finish();
        window.chatStream = null;
      }, actions);
      const changes = page.locator(".chatChanges", { hasText: "Changed in your notes · 2" });
      await changes.waitFor();
      await until(async () => !(await page.getByRole("button", { name: "Stop generating", exact: true }).count()));
      assertEq(await changes.getByRole("button", { name: "Revert all" }).count(), 1, "two changes offer Revert all");
      const rows = changes.locator(".chatChange");
      // A change nobody touched since goes back at once, on the open page too.
      await rows.nth(0).getByRole("button", { name: "Revert this change" }).click();
      await rows.nth(0).filter({ hasText: "Reverted" }).waitFor();
      assertEq(await noteText(one.id), "First draft");
      await page.locator(".blockRow", { hasText: "First draft" }).filter({ hasNotText: "improved" }).waitFor();
      assertEq(await changes.getByRole("button", { name: "Revert all" }).count(), 0, "one change left: no Revert all");
      // A reverted change redoes from the same row, and reverts again.
      await rows.nth(0).getByRole("button", { name: "Redo this change" }).click();
      await rows.nth(0).getByRole("button", { name: "Revert this change" }).waitFor();
      assertEq(await rows.nth(0).filter({ hasText: "Reverted" }).count(), 0, "a redone change is not marked");
      assertEq(await noteText(one.id), "First draft, improved by AI");
      await page.locator(".blockRow", { hasText: "First draft, improved by AI" }).waitFor();
      await rows.nth(0).getByRole("button", { name: "Revert this change" }).click();
      await rows.nth(0).getByRole("button", { name: "Redo this change" }).waitFor();
      assertEq(await noteText(one.id), "First draft");
      // One the user changed since asks first and shows what forcing it does.
      await rows.nth(1).getByRole("button", { name: "Revert this change" }).click();
      const ask = changes.locator(".chatRevertAsk");
      await ask.filter({ hasText: "The note was changed since." }).waitFor();
      assert((await ask.locator("del").innerText()).includes("improved by me"), "the diff shows the user's words going");      assertEq(await noteText(two.id), "Second draft, improved by me", "nothing is written before the user decides");
      await ask.getByRole("button", { name: "Revert anyway" }).click();
      await rows.nth(1).filter({ hasText: "Reverted" }).waitFor();
      assertEq(await ask.count(), 0);
      assertEq(await noteText(two.id), "Second draft");
      // The conversation keeps the marks, and the next request tells the model.
      const saved = await until(async () => {
        const chat = await alice.api(`/api/chats/${notesPage.id}`);
        const marks = chat.messages.find((m) => m.role === "ai")?.actions?.map((a) => !!a.reverted) || [];
        return marks.length === 2 && marks.every(Boolean) && chat;
      }, { what: "the reverts are saved with the reply" });
      assert(saved.messages.find((m) => m.role === "ai").actions.every((a) => a.revert), "the saved actions keep what reverting needs");
      await send("Thanks", 2);
      const history = await page.evaluate(() => window.chatBodies.at(-1).history);
      const sent = history.find((m) => m.role === "ai").actions;
      assert(sent.every((a) => a.reverted && !("revert" in a)), "the replay carries the marks, not the texts");
      await page.evaluate(() => { window.chatStream.push({ delta: "You're welcome." }); window.chatStream.finish(); });
      await until(async () => !(await page.getByRole("button", { name: "Stop generating", exact: true }).count()));
      assertNoProblems(page, ["POST /api/ai/revert -> 409"]); // the row's question, answered above
    } finally {
      await ctx.close();
      await alice.api(`/api/chats/${notesPage.id}`, { method: "PUT", body: { messages: [] } });
    }
  });

  await step("chat navigation: deleting a folder always asks with two answers, and its row puts the folder back with its pages filed", async () => {
    const name = "E2E doomed folder";
    const folder = await alice.folder(name);
    const filed = await alice.api("/api/pages", { method: "POST", body: { title: "Doomed folder page" } });
    await alice.file(filed.id, { folders: [name] });
    const { position, properties } = await alice.api(`/api/blocks/${folder}`);
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    const { value: profile } = await alice.api("/api/prefs/profile");
    // The defaults: Delete folders and labels asks.
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
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    const push = (event) => page.evaluate((e) => window.chatStream.push(e), event);
    const input = page.getByRole("combobox", { name: "Message AI" });
    const card = page.locator(".chatApproval");
    // The folder as the library listing shows it (list or grid).
    const listed = page.locator(".fileList, .fileGrid").getByText(name, { exact: true });
    const gone = async () => { try { await alice.api(`/api/blocks/${folder}`); return false; } catch (e) { return e.status === 404; } };
    const filedIn = async () => (await alice.api(`/api/blocks/${filed.id}`)).properties?.folders || [];
    // The deletion as the library chat's saved reply keeps it, once `reverted` is as wanted.
    const savedAs = (reverted) => until(async () => {
      const chat = await alice.api("/api/chats/home");
      const action = chat.messages.find((m) => m.role === "ai")?.actions?.find((a) => a.tool === "delete_folder");
      return action && !!action.reverted === reverted && action.revert?.tree === "folders";
    }, { what: `the saved reply marks the deletion ${reverted ? "reverted" : "not reverted"}` });
    const args = { folder: name };
    try {
      await input.waitFor();
      await page.waitForLoadState("networkidle");
      await listed.first().waitFor();
      await input.fill("Delete the doomed folder");
      await input.press("Enter");
      await until(() => page.evaluate(() => window.chatBodies.length === 1 && !!window.chatStream), { what: "the message is sent" });
      const sent = await page.evaluate(() => window.chatBodies[0]);
      assertEq(sent.permissions.organize_delete, "ask", "deleting folders asks by default");
      assertEq(sent.permissions.organize, "ask");

      // The card: Allow once, in red, and Don't allow — nothing that stops asking.
      await push({ step: { id: "c-d1", tool: "delete_folder", args } });
      await push({ approval: { id: "d1", call_id: "c-d1", tool: "delete_folder", perm: "organize_delete", args, timeout: 600,
        preview: { title: name, diff: [["del", name]], subfolders: [], subfolder_count: 0, pages: ["Doomed folder page"],
          page_count: 1, chats: 0, chat_folders: [], chat_folder_count: 0 } } });
      await card.getByText(`Delete the folder “${name}”`).waitFor();
      assert((await card.innerText()).includes("Approval needed · Delete folders and labels"), "the card names the permission");
      assertEq(await card.locator(".chatApprovalNote").innerText(), "1 page leaves it and stays in your library: Doomed folder page");
      assertEq((await card.locator("del").innerText()).trim(), name);
      assertEq(await card.getByRole("button", { name: "Allow in this chat", exact: true }).count(), 0);
      assertEq(await card.getByRole("button", { name: "Always allow", exact: true }).count(), 0);
      assertEq(await card.getByRole("button", { name: "Don't allow", exact: true }).count(), 1);
      const once = card.getByRole("button", { name: "Allow once", exact: true });
      assert((await once.getAttribute("class")).split(" ").includes("danger"), "Allow once is the red button of a deletion");
      if (flags.keep) await card.screenshot({ path: `${server.dir}/approval-delete-folder.png`, animations: "disabled" });
      await once.click();
      await card.getByText("Allowed. The assistant goes on…").waitFor();
      await page.locator(".chatSteps").getByText(`Deleting the folder ${name}…`).waitFor();
      assertEq(JSON.stringify(await page.evaluate(() => window.approvalAnswers)), JSON.stringify([{ id: "d1", decision: "once" }]));

      // The deletion the tool made (DELETE /api/folders runs the same ops.delete_tree), and its row.
      const deleted = await alice.api(`/api/folders/${folder}`, { method: "DELETE" });
      assertEq(JSON.stringify(deleted.pages), JSON.stringify([filed.id]));
      await push({ action: { kind: "delete", tool: "delete_folder", block_id: folder, title: name, count: 1, chats: 0,
        summary: `Deleted the folder “${name}”`, args, approval: "once",
        result: `ok — deleted the folder "${name}"; its 1 page stays in the library, filed in their other folders or at the library root. The user can revert it from the reply.`,
        revert: { tree: "folders", blocks: [[folder, "folders", position, name, JSON.stringify(properties || {})]],
          pages: { [filed.id]: [folder] } } } });
      await until(async () => !(await card.count()), { what: "the card goes once the call ran" });
      await push({ delta: "Deleted the folder; its page stays in your library." });
      await page.evaluate(() => window.chatStream.finish());
      const changes = page.locator(".chatChanges", { hasText: "Changed in your library · 1" });
      await changes.waitFor();
      await until(async () => !(await page.getByRole("button", { name: "Stop generating", exact: true }).count()));
      const row = changes.locator(".chatChange");
      assertEq(await row.locator(".chatChangeText").innerText(), `Deleted the folder ${name}; its 1 page stays in your library`);
      assertEq(await page.getByRole("button", { name: "Revert all" }).count(), 0, "Revert all is the notes' only");
      await until(async () => !(await listed.count()), { what: "the reply's change reloads the listing" });

      // Revert puts the folder back under its id and files its page again; redo deletes it again.
      await row.getByRole("button", { name: "Revert this change" }).click();
      await row.filter({ hasText: "Reverted" }).waitFor();
      const back = await alice.api(`/api/blocks/${folder}`);
      assertEq(JSON.stringify([back.content, back.page_id, back.parent_id, back.position]),
        JSON.stringify([name, "folders", "folders", position]));
      assertEq(JSON.stringify(await filedIn()), JSON.stringify([folder]));
      await listed.first().waitFor(); // the library listing reads it again
      await savedAs(true);
      await row.getByRole("button", { name: "Redo this change" }).click();
      await row.getByRole("button", { name: "Revert this change" }).waitFor();
      assertEq(await row.filter({ hasText: "Reverted" }).count(), 0, "a redone change is not marked");
      assert(await gone(), "the redo deletes the folder again");
      assertEq((await filedIn()).length, 0, "and takes it off its page");
      await until(async () => !(await listed.count()), { what: "the listing loses the folder again" });
      await savedAs(false);
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api("/api/prefs/profile", { method: "PUT", body: { value: profile || {} } });
      await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
      if (!(await gone())) await alice.api(`/api/folders/${folder}`, { method: "DELETE" });
    }
  });

  await step("chat navigation: renaming several pages asks once on one card that lists them, and its row folds out to the pages and reverts them together", async () => {
    const names = ["one", "two", "three"];
    const pages = [];
    for (const name of names) pages.push(await alice.api("/api/pages", { method: "POST", body: { title: `E2E batch ${name}` } }));
    const [one, two, three] = pages;
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
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
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    const push = (event) => page.evaluate((e) => window.chatStream.push(e), event);
    const input = page.getByRole("combobox", { name: "Message AI" });
    const send = async (text, n) => {
      await input.fill(text);
      await input.press("Enter");
      await until(() => page.evaluate((count) => window.chatBodies.length === count && !!window.chatStream, n), { what: `message ${n} is sent` });
    };
    const finish = () => page.evaluate(() => { window.chatStream.finish(); window.chatStream = null; });
    const card = page.locator(".chatApproval");
    const rename = (p, title) => alice.api(`/api/blocks/${p.id}`, { method: "PUT", body: { content: title } });
    const titles = async () => JSON.stringify(await Promise.all(pages.map((p) => alice.api(`/api/blocks/${p.id}`).then((b) => b.content))));
    // What the agent's rename_page call recorded: each page as the one-page action names it, and the revert.
    const to = (i) => `E2E batch ${i + 1}`;
    const items = pages.map((p, i) => ({ page_id: p.id, title: `E2E batch ${names[i]}`, from: `E2E batch ${names[i]}`, to: to(i) }));
    const revert = { pages: pages.map((p, i) => [p.id, "title", `E2E batch ${names[i]}`, to(i)]) };
    // The rename as the library chat's saved reply keeps it, once its marks are as wanted.
    const savedAs = ({ reverted, kept }) => until(async () => {
      const chat = await alice.api("/api/chats/home");
      const action = chat.messages.find((m) => m.role === "ai")?.actions?.find((a) => a.tool === "rename_page");
      return action && action.reverted === reverted && action.kept === kept && action.revert?.pages?.length === 3;
    }, { what: `the saved reply marks the rename ${reverted ? "reverted" : "not reverted"}, ${kept || 0} kept` });
    try {
      await input.waitFor();
      await page.waitForLoadState("networkidle");
      await send("Rename the three batch pages", 1);

      // One card for the call: its pages counted, one line each, the one it leaves out named.
      await push({ step: { id: "c-b1", tool: "rename_page", args: {}, items: 3 } });
      await page.locator(".chatSteps").getByText("Renaming 3 pages…").waitFor();
      await push({ approval: { id: "b1", call_id: "c-b1", tool: "rename_page", perm: "rename", args: {}, timeout: 600,
        preview: { count: 3, diff: names.flatMap((name, i) => [...(i ? [["ctx", "\n"]] : []), ["ctx", "E2E batch "], ["del", name], ["ins", String(i + 1)]]),
          left_out: ["zzz"], left_out_count: 1, same_count: 0, digest: "0123456789abcdef" } } });
      await card.getByText("Rename 3 pages", { exact: true }).waitFor();
      assert((await card.innerText()).includes("Approval needed · Rename pages"), "the card names the permission");
      assertEq(await card.locator(".chatApprovalNote").innerText(), "1 page is left out: zzz");
      assertEq((await card.locator(".chatApprovalDiff").innerText()).split("\n").length, 3, "one line per page");
      assertEq(JSON.stringify(await card.locator(".chatApprovalDiff del").allInnerTexts()), JSON.stringify(names));
      for (const name of ["Allow once", "Allow in this chat", "Always allow", "Don't allow"]) {
        assertEq(await card.getByRole("button", { name, exact: true }).count(), 1, `the card offers ${name}`);
      }
      if (flags.keep) await card.screenshot({ path: `${server.dir}/approval-batch.png`, animations: "disabled" });
      await card.getByRole("button", { name: "Allow once", exact: true }).click();
      await card.getByText("Allowed. The assistant goes on…").waitFor();
      await page.locator(".chatSteps").getByText("Renaming 3 pages…").waitFor();
      assertEq(JSON.stringify(await page.evaluate(() => window.approvalAnswers)), JSON.stringify([{ id: "b1", decision: "once" }]));

      // The renames the tool made, and its row: one for the call, its pages folded under it.
      for (const [i, p] of pages.entries()) await rename(p, to(i));
      await push({ action: { kind: "rename", tool: "rename_page", summary: "Renamed 3 pages", changes: 3, items, revert,
        args: {}, result: "ok — renamed 3 pages.", approval: "once" } });
      await until(async () => !(await card.count()), { what: "the card goes once the call ran" });
      await push({ delta: "Renamed the three pages." });
      await finish();
      const changes = page.locator(".chatChanges", { hasText: "Changed in your library · 3" });
      await changes.waitFor();
      await until(async () => !(await page.getByRole("button", { name: "Stop generating", exact: true }).count()));
      const row = changes.locator(".chatChange");
      assertEq(await row.count(), 1, "one call is one row");
      assertEq((await row.locator(".chatChangeText").innerText()).split("\n")[0], "Renamed 3 pages");
      const links = row.locator(".chatChangeItems .chatChangeLink");
      assertEq(await links.first().isVisible(), false, "the pages start folded");
      await row.getByText("Show the pages", { exact: true }).click();
      await links.nth(2).waitFor();
      assertEq(JSON.stringify(await links.allInnerTexts()), JSON.stringify(["E2E batch 1", "E2E batch 2", "E2E batch 3"]));
      assertEq(JSON.stringify(await row.locator(".chatChangeItems .chatChangeOld").allInnerTexts()),
        JSON.stringify(["E2E batch one", "E2E batch two", "E2E batch three"]));
      if (flags.keep) await changes.screenshot({ path: `${server.dir}/changes-batch.png`, animations: "disabled" });

      // Page one was renamed by hand since: the revert puts the other two back, leaves it and says so.
      await rename(one, "E2E batch hand");
      await row.getByRole("button", { name: "Revert this change" }).click();
      await row.filter({ hasText: "Reverted" }).waitFor();
      const note = changes.locator(".chatRevertAsk");
      await note.filter({ hasText: "1 of these pages changed since, or was deleted, and was left as it is." }).waitFor();
      assertEq(JSON.stringify(await note.getByRole("button").allInnerTexts()), JSON.stringify(["Dismiss"]), "nothing to force");
      assertEq(await titles(), JSON.stringify(["E2E batch hand", "E2E batch two", "E2E batch three"]));
      await savedAs({ reverted: true, kept: 1 });
      // The next request tells the model, without the per-page lists.
      await send("Thanks", 2);
      const sent = (await page.evaluate(() => window.chatBodies.at(-1).history)).find((m) => m.role === "ai").actions
        .find((a) => a.tool === "rename_page");
      assert(sent.reverted === true && sent.kept === 1 && !("revert" in sent) && !("items" in sent),
        "the replay carries the marks and the count, not the pages");
      await push({ delta: "You're welcome." });
      await finish();
      await until(async () => !(await page.getByRole("button", { name: "Stop generating", exact: true }).count()));

      // Redo renames the two again and leaves page one: the Reverted mark goes, and the count stays, now the redo's.
      await row.getByRole("button", { name: "Redo this change" }).click();
      await row.getByRole("button", { name: "Revert this change" }).waitFor();
      assertEq(await row.filter({ hasText: "Reverted" }).count(), 0, "a redone change is not marked");
      await note.filter({ hasText: "1 of these pages changed since, or was deleted, and was left as it is." }).waitFor();
      assertEq(await titles(), JSON.stringify(["E2E batch hand", "E2E batch 2", "E2E batch 3"]));
      await savedAs({ kept: 1 });

      // Every page renamed by hand since: nothing is written, the row says so and stays revertible.
      await rename(two, "E2E batch hand 2");
      await rename(three, "E2E batch hand 3");
      await row.getByRole("button", { name: "Revert this change" }).click();
      const ask = changes.locator(".chatRevertAsk", { hasText: "These 3 pages changed since, or were deleted, and were left as they are." });
      await ask.waitFor();
      assertEq(JSON.stringify(await ask.getByRole("button").allInnerTexts()), JSON.stringify(["Dismiss"]), "nothing to force");
      assertEq(await row.filter({ hasText: "Reverted" }).count(), 0, "a revert that wrote nothing is not marked");
      assertEq(await titles(), JSON.stringify(["E2E batch hand", "E2E batch hand 2", "E2E batch hand 3"]));
      await savedAs({ kept: 1 }); // as the redo left it
      await ask.getByRole("button", { name: "Dismiss", exact: true }).click();
      await until(async () => !(await ask.count()) && row.getByRole("button", { name: "Revert this change" }).isEnabled(),
        { what: "the row stays revertible once the note is dismissed" });
      assertNoProblems(page, ["POST /api/ai/revert -> 409"]); // every page kept, answered above
    } finally {
      await ctx.close();
      await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    }
  });

  await step("chat navigation: the history popover ticks conversations and deletes them in one call", async () => {
    // Its own page: earlier steps left conversations in the other buckets.
    const histPage = await alice.api("/api/pages", { method: "POST", body: { title: "History page" } });
    const bucket = histPage.id;
    const archived = {};
    for (const title of ["Alpha talk", "Beta talk", "Gamma talk"]) {
      const r = await alice.api("/api/chat-history/archive",
        { method: "POST", body: { bucket, title, messages: [{ role: "user", text: title }] } });
      archived[title] = r.id;
    }
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${bucket}`);
    try {
      await page.getByRole("button", { name: "Chat history", exact: true }).click();
      const rows = page.locator(".chatHistRow:not(.active)");
      await until(async () => (await rows.count()) === 3, { what: "the three archived conversations list" });
      const bulk = page.locator(".chatHistBulk");
      assertEq(await bulk.count(), 0, "no bar until something is ticked");
      const tick = (text) => page.locator(".chatHistRow", { hasText: text }).locator(".chatHistPick");
      // A row's menu starts the picking; from the first tick on, every row has a box.
      await page.locator(".chatHistRow", { hasText: "Alpha talk" }).locator(".rowMenuBtn").click();
      await page.locator(".ctxMenuItem", { hasText: "Select" }).click();
      assert(await tick("Alpha talk").isChecked(), "Select ticks the row");
      await tick("Beta talk").check();
      assert((await bulk.innerText()).includes("2 selected"), "the bar counts the ticks");
      // A search that hides a ticked row drops its tick, so Delete never
      // takes a conversation the user cannot see.
      await page.locator(".chatHistoryPop .searchInput").fill("Beta");
      await until(async () => (await bulk.innerText()).includes("1 selected"), { what: "the hidden row's tick is dropped" });
      await page.locator(".chatHistoryPop .searchInput").fill("");
      await until(async () => (await rows.count()) === 3);
      await bulk.getByRole("button", { name: "Select all", exact: true }).click();
      await until(async () => (await bulk.innerText()).includes("3 selected"), { what: "Select all takes the listed rows" });
      await tick("Alpha talk").uncheck();
      await bulk.getByRole("button", { name: "Delete", exact: true }).click();
      const confirm = page.locator(".confirmModal");
      await confirm.waitFor();
      assert((await confirm.innerText()).includes("2 conversations"), "the dialog says how many");
      await confirm.getByRole("button", { name: "Delete", exact: true }).click();
      await until(async () => (await rows.count()) === 1, { what: "the ticked rows go" });
      assertEq(await bulk.count(), 0, "the bar goes with the selection");
      // The rows go as soon as Delete is confirmed (deletePicked drops them
      // before awaiting the call), so the server is checked on its own time.
      await until(async () => (await alice.api(`/api/chat-history?bucket=${bucket}`))
        .sessions.map((e) => e.id).join() === archived["Alpha talk"],
      { what: "only the unticked conversation is left on the server" });
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/blocks/${histPage.id}`, { method: "DELETE" });
    }
  });


  // Motion in the transcript (docs/dev/ui-design.md, "Lists that change"):
  // a context whose chat stream is faked (window.chatStream push / finish,
  // a fresh one per question), recording from before the page loads what
  // comes into the transcript and which status pill folds away.
  async function streamingContext(options = {}) {
    const ctx = await alice.context(browser, options);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => {
      localStorage.setItem("gamma-ai-login-check", "off");
      window.chatMotion = [];
      new MutationObserver((records) => {
        for (const r of records) {
          const value = r.target.getAttribute(r.attributeName);
          if (r.attributeName === "data-entering" && value) window.chatMotion.push(`enter ${value} ${r.target.dataset.motionKey}`);
          if (r.attributeName === "data-phase" && r.target.classList.contains("chatStepsFold")) window.chatMotion.push(`fold ${value}`);
        }
      }).observe(document, { subtree: true, attributes: true, attributeFilter: ["data-entering", "data-phase"] });
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
    return ctx;
  }
  const chatMotion = (page) => page.evaluate(() => window.chatMotion.splice(0));
  async function ask(page, text) {
    await page.evaluate(() => { window.chatStream = null; });
    const input = page.getByRole("combobox", { name: "Message AI" });
    await input.fill(text);
    await input.press("Enter");
    await page.waitForFunction(() => !!window.chatStream);
  }
  const push = (page, ...events) => page.evaluate((list) => list.forEach((event) => window.chatStream.push(event)), events);

  await step("chat navigation: a question and its reply rise into place under one pill that morphs from Thinking to each step; a loaded conversation shows still", async () => {
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [
      { role: "user", text: "Earlier question" }, { role: "ai", text: "Earlier answer" },
    ] } });
    const ctx = await streamingContext();
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      await page.locator(".chatBubbleRow.ai", { hasText: "Earlier answer" }).waitFor();
      await page.waitForLoadState("networkidle");
      assertEq((await chatMotion(page)).join(), "", "the stored conversation shows still");
      await ask(page, "Tidy up the library");
      const came = await chatMotion(page);
      assertEq(came.filter((m) => m.startsWith("enter")).join(), "enter rise 2,enter rise 3", "the question and its reply rise in");
      const reply = page.locator(".chatBubbleRow.ai").last();
      const pill = reply.locator(".chatSteps");
      await pill.filter({ hasText: "Thinking" }).waitFor();
      assertEq(await pill.locator(".shimmerText").count(), 1, "the light says work is under way");
      assert(await pill.isDisabled(), "until a step lands the pill opens nothing and takes no focus");
      const said = async () => pill.evaluate((el) => [...el.querySelectorAll("[aria-live]")].map((n) => n.className));
      assertEq((await said()).join(), "chatPillText shimmerText", "only the label is a live region");
      const morphed = page.waitForSelector(".chatSteps .morph.moving", { state: "attached" });
      await push(page, { step: { id: "c1", tool: "search_library", args: { query: "cavity" } } });
      await morphed;
      await pill.filter({ hasText: "Searching library for “cavity”…" }).waitFor();
      await push(page, { action: { kind: "search", tool: "search_library", summary: "Searched library for “cavity” — 1 hit", args: { query: "cavity" }, result: "…" } });
      await pill.filter({ hasText: "Thinking" }).waitFor();
      assert(!(await pill.isDisabled()), "with a step it opens them");
      assertEq(await pill.getAttribute("aria-expanded"), "false");
      await push(page, { delta: "Nothing to tidy." });
      await pill.filter({ hasText: "Responding" }).waitFor();
      assertEq(await pill.evaluate((el) => [...el.querySelectorAll(".chatMsgUsage.live")].every((n) => n.closest("[aria-hidden]"))), true,
        "the token count ticking beside it is not read out");
      await page.evaluate(() => window.chatStream.finish());
      await pill.filter({ hasText: "1 step" }).waitFor();
      assertEq(await pill.locator(".shimmerText").count(), 0, "a finished reply's pill is still");
      // A reply that ends without a step folds its pill away.
      await ask(page, "Thanks");
      const plain = page.locator(".chatBubbleRow.ai").last();
      await plain.locator(".chatSteps", { hasText: "Thinking" }).waitFor();
      await push(page, { delta: "You're welcome." });
      await plain.locator(".chatSteps", { hasText: "Responding" }).waitFor();
      await chatMotion(page);
      await page.evaluate(() => window.chatStream.finish());
      await until(async () => !(await plain.locator(".chatSteps").count()), { what: "the plain reply's pill goes" });
      assert((await chatMotion(page)).includes("fold closing"), "it folds away");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat navigation: under reduced motion a question and its reply come in still", async () => {
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    const ctx = await streamingContext({ reducedMotion: "reduce" });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      await page.getByRole("combobox", { name: "Message AI" }).waitFor();
      await page.waitForLoadState("networkidle");
      await ask(page, "Hello");
      await push(page, { delta: "Hi." });
      await page.evaluate(() => window.chatStream.finish());
      await until(async () => !(await page.locator(".chatSteps").count()), { what: "the pill goes" });
      assertEq((await chatMotion(page)).filter((m) => m.startsWith("enter")).join(), "", "nothing rises");
      assertEq(await page.locator(".chatBubbleRow").count(), 2);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat navigation: back on a page, its conversation shows at once while it is read again; the history popover keeps its list, and a quick list never shows Loading…", async () => {
    const back = await alice.api("/api/pages", { method: "POST", body: { title: "Cached chat page" } });
    // Archiving clears the bucket's active conversation: it goes first.
    await alice.api("/api/chat-history/archive", { method: "POST", body: { bucket: back.id, title: "Older talk",
      messages: [{ role: "user", text: "Older talk" }] } });
    await alice.api(`/api/chats/${back.id}`, { method: "PUT", body: { messages: [
      { role: "user", text: "What does it cite?" }, { role: "ai", text: `It cites [Linked paper](/?page=${target.id}).` },
    ] } });
    const ctx = await streamingContext();
    // Reads of this page's conversation and of its history wait while held.
    const held = { chat: null, history: null };
    const hold = (what) => { let release; held[what] = new Promise((resolve) => { release = resolve; }); return () => { held[what] = null; release(); }; };
    await ctx.route((url) => url.pathname === `/api/chats/${back.id}`, async (route) => {
      if (route.request().method() === "GET" && held.chat) await held.chat;
      await route.continue();
    });
    await ctx.route((url) => url.pathname === "/api/chat-history" && url.searchParams.get("bucket") === back.id, async (route) => {
      if (held.history) await held.history;
      await route.continue();
    });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${back.id}`);
    try {
      const question = page.locator(".chatBubbleRow.user", { hasText: "What does it cite?" });
      await question.waitFor();
      await page.waitForLoadState("networkidle");
      // A first look at the history: "Loading…" stays unseen for a moment.
      const releaseHistory = hold("history");
      const historyButton = page.getByRole("button", { name: "Chat history", exact: true });
      await historyButton.click();
      const hint = page.locator(".chatHistoryPop .loadingHint");
      await hint.waitFor();
      assertEq(await hint.evaluate((el) => getComputedStyle(el).opacity), "0", "an answer that comes quickly never shows Loading…");
      await until(async () => (await hint.evaluate((el) => getComputedStyle(el).opacity)) === "1", { what: "a slow one shows it" });
      releaseHistory();
      const older = page.locator(".chatHistRow:not(.active)", { hasText: "Older talk" });
      await older.waitFor();
      await historyButton.click();
      // Away to the linked page and back, with the conversation's read held.
      await page.locator(".chatPanel").getByRole("link", { name: "Linked paper" }).click();
      await until(() => new URL(page.url()).searchParams.get("block") === target.id);
      await question.waitFor({ state: "detached" });
      await chatMotion(page);
      const releaseChat = hold("chat");
      await page.getByRole("button", { name: "Back", exact: true }).click();
      await question.waitFor();
      assert(await page.locator(".chatBubbleRow.ai", { hasText: "It cites" }).isVisible(), "the reply is back too, before the read");
      // The popover opens on the list it had while that is read again.
      const releaseAgain = hold("history");
      await historyButton.click();
      await older.waitFor();
      assertEq(await page.locator(".chatHistoryPop .loadingHint").count(), 0, "no Loading… over a list it has");
      releaseAgain();
      releaseChat();
      await page.waitForLoadState("networkidle");
      assert(await question.isVisible(), "the stored copy replaces it in place");
      assertEq((await chatMotion(page)).filter((m) => m.startsWith("enter")).join(), "", "a conversation come back to shows still");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/blocks/${back.id}`, { method: "DELETE" });
    }
  });

  await step("chat navigation: the history popover shows an open and a New chat made here at once — the conversation filed leads, the one opened is gone", async () => {
    const swap = await alice.api("/api/pages", { method: "POST", body: { title: "Swap page" } });
    await alice.api("/api/chat-history/archive", { method: "POST", body: { bucket: swap.id, title: "Older talk",
      messages: [{ role: "user", text: "Older talk question" }] } });
    await alice.api(`/api/chats/${swap.id}`, { method: "PUT", body: { title: "Current talk", messages: [
      { role: "user", text: "Current talk question" }, { role: "ai", text: "Current answer" },
    ] } });
    const ctx = await alice.context(browser);
    await fakeAiModels(ctx);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    // The list's reads wait while held: what shows meanwhile is the list as
    // this tab changed it.
    let held = null;
    await ctx.route((url) => url.pathname === "/api/chat-history" && url.searchParams.get("bucket") === swap.id, async (route) => {
      if (held) await held.promise;
      await route.continue();
    });
    const hold = () => { let release; held = { promise: new Promise((resolve) => { release = resolve; }) }; return () => { held = null; release(); }; };
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${swap.id}`);
    const historyButton = page.getByRole("button", { name: "Chat history", exact: true });
    const listed = () => page.locator(".chatHistoryPop:not([data-leaving]) .chatHistRow:not(.active) .chatHistTitle").allInnerTexts();
    try {
      await page.locator(".chatBubbleRow.ai", { hasText: "Current answer" }).waitFor();
      await page.waitForLoadState("networkidle");
      await historyButton.click();
      await page.locator(".chatHistRow:not(.active)", { hasText: "Older talk" }).click();
      await page.locator(".chatBubbleRow.user", { hasText: "Older talk question" }).waitFor();
      let release = hold();
      await historyButton.click();
      await page.locator(".chatHistoryPop .chatHistRow.active", { hasText: "Older talk" }).waitFor();
      assertEq((await listed()).join(), "Current talk", "the conversation it replaced is listed, the one opened is not");
      // The row shown before the read is the server's own: it opens.
      await page.locator(".chatHistRow:not(.active)", { hasText: "Current talk" }).click();
      await page.locator(".chatBubbleRow.ai", { hasText: "Current answer" }).waitFor();
      release();
      await page.waitForLoadState("networkidle");
      // New chat files the conversation: it leads the list before the read.
      await page.getByRole("button", { name: "New chat", exact: true }).click();
      await page.locator(".chatBubbleRow").first().waitFor({ state: "detached" });
      release = hold();
      await historyButton.click();
      await until(async () => (await listed()).join() === "Current talk,Older talk", { what: "the conversation kept leads the list" });
      release();
      await page.waitForLoadState("networkidle");
      assertEq((await listed()).join(), "Current talk,Older talk", "the read agrees");
      assertEq((await alice.api(`/api/chat-history?bucket=${swap.id}`)).sessions.map((e) => e.title).join(), "Current talk,Older talk",
        "the server holds the same");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/blocks/${swap.id}`, { method: "DELETE" });
    }
  });

  await step("chat navigation: until the chat knows its AI it is a plain frame — no header tools, no empty-state text", async () => {
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    const ctx = await alice.context(browser);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    let release, answered = false;
    const held = new Promise((resolve) => { release = resolve; });
    await ctx.route("**/api/ai/models*", async (route) => { await held; await route.fulfill({ json: FAKE_AI_MODELS }); });
    ctx.on("response", (res) => { if (new URL(res.url()).pathname === "/api/chats/home") answered = true; });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      await page.locator(".chatPanel").waitFor();
      await until(() => answered, { what: "the conversation is read" });
      await page.waitForTimeout(300); // what is absent is looked for once the read has landed
      assertEq(await page.locator(".chatEmpty").count(), 0, "no empty-state text yet");
      assertEq(await page.getByRole("button", { name: "Find in this conversation" }).count(), 0, "no header tools yet");
      const composer = page.getByRole("combobox", { name: "Message AI" });
      assertEq(await composer.getAttribute("placeholder"), "", "the field names nothing yet");
      release();
      await page.locator(".chatEmpty", { hasText: "Ask AI anything" }).waitFor();
      await page.getByRole("button", { name: "Find in this conversation" }).waitFor();
      assert(/^Ask/.test(await composer.getAttribute("placeholder")), "then it asks");
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("chat navigation: when the read of the AI's models fails the chat stops waiting — its tools and empty-state text show", async () => {
    await alice.api("/api/chats/home", { method: "PUT", body: { messages: [] } });
    const ctx = await alice.context(browser);
    await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
    await ctx.route("**/api/ai/models*", (route) => route.fulfill({ status: 500, json: { detail: "models unavailable" } }));
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      await page.locator(".chatEmpty", { hasText: "Ask AI anything" }).waitFor();
      await page.getByRole("button", { name: "Chat history", exact: true }).waitFor();
      await page.getByRole("button", { name: "Find in this conversation" }).waitFor();
      assertEq(await page.locator(".chatSetup").count(), 0, "no setup card: the chat does not know there is no AI");
      assert(await page.getByRole("combobox", { name: "Message AI" }).isEnabled(), "the message box takes a question");
      assertNoProblems(page, [/\/api\/ai\/models -> 500/]);
    } finally { await ctx.close(); }
  });

  // Pages opened from the library, each its own conversation, then walked
  // between by their tabs.
  async function openFromLibrary(page, title) {
    await page.locator('[data-guide="header.home"]').click();
    await page.locator(".fileRow", { hasText: title }).first().dblclick();
    await page.locator(".tabStrip .tab.active", { hasText: title }).waitFor();
  }
  const tabOf = (page, title) => page.locator(".tabStrip .tab[role=tab]", { hasText: title }).first();

  await step("chat navigation: switched back to a conversation seen before, its replies show still: no steps pill grows in, morphs or folds away, and none is read out", async () => {
    const pages = [];
    for (const [title, question, answer, actions] of [
      ["Switch alpha", "What is in alpha?", "Alpha holds one note.", [{ kind: "read", tool: "read_page", summary: "Read Switch alpha", ok: true }]],
      ["Switch beta", "Search for beta", "Found two.", [{ kind: "search", tool: "search_library", summary: "Searched for x", ok: true },
        { kind: "search", tool: "search_library", summary: "Searched for y", ok: true }]],
      ["Switch charlie", "Hi charlie", "Hello there.", null],
    ]) {
      const made = await alice.api("/api/pages", { method: "POST", body: { title } });
      await alice.api(`/api/chats/${made.id}`, { method: "PUT", body: { messages: [
        { role: "user", text: question }, { role: "ai", text: answer, ...(actions ? { actions } : {}) },
      ] } });
      pages.push({ id: made.id, title, answer });
    }
    const ctx = await streamingContext();
    // A steps pill growing in or folding away, a label morphing, and text
    // changing inside a live region, from any commit.
    await ctx.addInitScript(() => {
      window.chatSwitch = [];
      const look = (el) => {
        if (!(el instanceof Element)) return;
        for (const fold of [el, ...el.querySelectorAll(".chatStepsFold")]) {
          if (fold.matches(".chatStepsFold") && /opening|closing/.test(fold.dataset.phase)) window.chatSwitch.push(`fold ${fold.dataset.phase}`);
        }
        if (el.closest(".chatPanel") && (el.matches(".morph.moving") || el.querySelector(".morph.moving"))) window.chatSwitch.push("morph");
      };
      new MutationObserver((records) => {
        for (const r of records) {
          if (r.type === "attributes") look(r.target);
          else for (const n of r.addedNodes) look(n);
          const at = r.target.nodeType === 1 ? r.target : r.target.parentElement;
          const live = r.type !== "attributes" && at?.closest('.chatPanel [aria-live="polite"]');
          if (live) window.chatSwitch.push(`said ${live.textContent}`);
        }
      }).observe(document, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["data-phase", "class"] });
    });
    const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
    try {
      const reply = (p) => page.locator(".chatBubbleRow.ai", { hasText: p.answer });
      for (const p of pages) {
        await openFromLibrary(page, p.title);
        await reply(p).waitFor();
      }
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(400);
      await page.evaluate(() => window.chatSwitch.splice(0));
      // from charlie: to alpha, beta, charlie, beta, alpha
      for (const p of [pages[0], pages[1], pages[2], pages[1], pages[0]]) {
        await tabOf(page, p.title).click();
        await reply(p).waitFor();
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(300);
        const seen = await page.evaluate(() => window.chatSwitch.splice(0));
        assertEq(seen.join(" | "), "", `switched to ${p.title}, its replies show still`);
      }
      assertEq(await page.locator('.chatPanel .chatPillText[aria-live="polite"]').count(), 0, "a loaded reply's label is no live region");
      assertNoProblems(page);
    } finally {
      await ctx.close();
      for (const p of pages) await alice.api(`/api/blocks/${p.id}`, { method: "DELETE" });
    }
  });

  for (const ai of [true, false]) {
    await step(`chat navigation: a tab switched back to an empty conversation already read keeps its ${ai ? "empty line" : "setup card (no AI connected)"} while it is read again`, async () => {
      const pages = [];
      for (const title of [`Empty one ${ai}`, `Empty two ${ai}`]) {
        const made = await alice.api("/api/pages", { method: "POST", body: { title } });
        pages.push({ id: made.id, title });
      }
      const ctx = await alice.context(browser);
      if (ai) await fakeAiModels(ctx);
      await ctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      // the conversations' reads from a server a little away, once both were read
      let slow = false;
      await ctx.route((url) => pages.some((p) => url.pathname === `/api/chats/${p.id}`), async (route) => {
        if (slow && route.request().method() === "GET") await new Promise((done) => setTimeout(done, 400));
        await route.continue();
      });
      const page = await openPage(ctx, `${server.base}/?ws=${alice.ws}`);
      const placeholder = ai ? ".chatPanel .chatEmpty" : ".chatPanel .chatSetup";
      try {
        for (const p of pages) {
          await openFromLibrary(page, p.title);
          await page.waitForLoadState("networkidle");
          await page.locator(placeholder).waitFor();
        }
        slow = true;
        for (const p of [pages[0], pages[1]]) {
          // after every change to the page, whether the placeholder stands
          await page.evaluate((placeholder) => {
            window.placeholderGone = 0;
            window.placeholderWatch?.disconnect();
            window.placeholderWatch = new MutationObserver(() => { if (!document.querySelector(placeholder)) window.placeholderGone++; });
            window.placeholderWatch.observe(document, { subtree: true, childList: true });
          }, placeholder);
          const read = page.waitForResponse((res) => res.url().endsWith(`/api/chats/${p.id}`));
          await tabOf(page, p.title).click();
          await read;
          await page.waitForTimeout(100);
          assertEq(await page.evaluate(() => window.placeholderGone), 0, `back on ${p.title}, its placeholder never went`);
        }
        assertNoProblems(page);
      } finally {
        await ctx.close();
        for (const p of pages) await alice.api(`/api/blocks/${p.id}`, { method: "DELETE" });
      }
    });
  }
}
