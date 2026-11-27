import { createHash, randomBytes } from "node:crypto";
import path from "node:path";
import fs from "node:fs";
import { newPageViaUi } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";
import { Account, wanted } from "../harness.mjs";

export async function mcpScenarios(env) {
  const { server, browser, alice, makePdf, step, openPage, assert, assertEq, assertNoProblems, sleep, until } = env;
  if (!wanted("mcp")) return;
  await step("mcp: administrator confirms the suggested server URL and it persists", async () => {
    await server.manage("create-user", "mcp-admin", "mcp-admin-pw");
    await server.manage("set-admin", "mcp-admin", "on");
    const admin = await new Account(server, "mcp-admin", "mcp-admin-pw").login();
    const ctx = await admin.context(browser);
    try {
      const page = await openPage(ctx, server.base);
      const openServer = async () => {
        await page.getByRole("button", { name: "Account & settings", exact: true }).click();
        await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
        await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Server", exact: true }).click();
        await page.getByRole("textbox", { name: "Public server URL", exact: true }).waitFor();
      };
      await openServer();
      const address = page.getByRole("textbox", { name: "Public server URL", exact: true });
      await until(() => address.inputValue().then((v) => v === server.base));
      assertEq((await admin.api("/api/admin/settings")).public_url, "", "suggestion is not implicitly trusted");
      await page.getByRole("button", { name: "Confirm", exact: true }).click();
      await until(() => admin.api("/api/admin/settings").then((v) => v.public_url === server.base));
      await address.fill("https://draft.example");
      // an unconfirmed edit is a draft: leaving the pane asks, discarding restores the stored address
      await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Users", exact: true }).click();
      await page.getByRole("button", { name: "Keep editing", exact: true }).click();
      assertEq(await address.inputValue(), "https://draft.example");
      await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Users", exact: true }).click();
      await page.getByRole("button", { name: "Discard changes", exact: true }).click();
      await page.reload();
      await openServer();
      await until(() => address.inputValue().then((v) => v === server.base));
      assertEq(await page.getByRole("button", { name: "Confirm", exact: true }).count(), 0, "a confirmed, unchanged address needs no button");
      assert((await page.locator(".settingsPane .uiTag", { hasText: "confirmed" }).count()) >= 1, "the confirmed chip shows");
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        fs.mkdirSync(process.env.GAMMA_MCP_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "public-url-desktop.png") });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "public-url-mobile.png") });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "server settings fit mobile width");
      }
      assertNoProblems(page);
    } finally {
      await admin.api("/api/admin/settings", { method: "PUT", body: { public_url: "" } });
      await ctx.close();
    }
  });
  // An authorization request, for reading alone or (`scope` with
  // gamma:feedback, the Comment level's name) also commenting.
  async function request(scope = "gamma:read") {
    const redirect = "https://client.example/callback";
    const verifier = randomBytes(32).toString("base64url");
    const registered = await fetch(`${server.base}/oauth/register`, { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_name: "Codex browser test", redirect_uris: [redirect], token_endpoint_auth_method: "none" }) });
    assertEq(registered.status, 201);
    const client = await registered.json();
    const params = new URLSearchParams({ response_type: "code", client_id: client.client_id, redirect_uri: redirect,
      resource: `${server.base}/mcp`, code_challenge: createHash("sha256").update(verifier).digest("base64url"),
      code_challenge_method: "S256", state: "browser-test", scope });
    return { url: `${server.base}/oauth/authorize?${params}`, client, redirect, verifier };
  }

  await step("mcp: browser login, workspace approval, token exchange and revocation", async () => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const second = await alice.api("/api/workspaces", { method: "POST", body: { name: "Assistant second library" } });
    try {
      await ctx.route("https://client.example/callback**", (route) => route.fulfill({ status: 200, contentType: "text/plain", body: "Connection approved. Return to your assistant." }));
      const flow = await request();
      const page = await openPage(ctx, flow.url);
      await page.getByText("Sign in to connect your assistant").waitFor();
      await page.locator('input[type="text"]').fill("alice");
      await page.locator('input[type="password"]').fill("alice-pw");
      await page.getByRole("button", { name: "Log in", exact: true }).click();
      await page.getByRole("button", { name: "Allow read-only access" }).waitFor();
      const workspaces = (await alice.api("/api/session")).workspaces || [];
      // the default workspace starts ticked; one connection may read several
      const picker = page.getByRole("group", { name: "Workspaces it may read" });
      const home = picker.getByRole("checkbox", { name: workspaces.find((w) => w.id === alice.ws)?.name || "", exact: true });
      const other = picker.getByRole("checkbox", { name: "Assistant second library", exact: true });
      assert(await home.isChecked() && !await other.isChecked(), "only the default workspace starts ticked");
      await home.uncheck();
      assert(await page.getByRole("button", { name: "Allow read-only access" }).isDisabled(), "a connection reads at least one workspace");
      await home.check();
      await other.check();
      // a client asking to read alone: each workspace starts at View, and the connection is read-only
      assertEq(JSON.stringify(await picker.getByRole("button", { pressed: true }).allTextContents()),
        JSON.stringify(["View", "View"]), "the Comment level is not asked for");
      if (process.env.GAMMA_MCP_SCREENSHOTS) {
        fs.mkdirSync(process.env.GAMMA_MCP_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "consent-desktop.png") });
        await page.setViewportSize({ width: 390, height: 844 });
        await page.screenshot({ path: path.join(process.env.GAMMA_MCP_SCREENSHOTS, "consent-mobile.png"), fullPage: true });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no horizontal overflow");
      }
      await page.getByRole("button", { name: "Allow read-only access" }).click();
      await page.waitForURL("https://client.example/callback**");
      const callback = new URL(page.url());
      assertEq(callback.searchParams.get("state"), "browser-test");
      const response = await fetch(`${server.base}/oauth/token`, { method: "POST", body: new URLSearchParams({
        grant_type: "authorization_code", client_id: flow.client.client_id, code: callback.searchParams.get("code"),
        code_verifier: flow.verifier, redirect_uri: flow.redirect, resource: `${server.base}/mcp`,
      }) });
      assertEq(response.status, 200);
      const token = (await response.json()).access_token;
      const rpc = (name = "list_pages") => fetch(`${server.base}/mcp`, { method: "POST", headers: { Authorization: `Bearer ${token}`,
        "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: {} } }) });
      const read = await rpc();
      assertEq(read.status, 200);
      assert(!(await read.json()).result.isError, "MCP read succeeds");
      const listed = (await (await rpc("list_workspaces")).json()).result.structuredContent.workspaces.map((w) => w.id);
      assertEq(JSON.stringify(listed), JSON.stringify([alice.ws, second.id]), "the connection reads both workspaces ticked");
      const connection = (await alice.api("/api/integrations/tokens")).tokens.find((t) => t.name === "Codex browser test (OAuth)");
      assert(connection, "OAuth connection appears in settings");
      await alice.api(`/api/integrations/tokens/${connection.id}`, { method: "DELETE" });
      assertEq((await rpc()).status, 401);
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/workspaces/${second.id}`, { method: "DELETE" });
    }
  });

  await step("mcp: a client asking for gamma:feedback starts at the Comment level, and the connection comments there under its name", async () => {
    const ctx = await alice.context(browser);
    try {
      await ctx.route("https://client.example/callback**", (route) => route.fulfill({ status: 200, contentType: "text/plain", body: "Connection approved." }));
      const flow = await request("gamma:read gamma:feedback");
      const page = await openPage(ctx, flow.url);
      const spaces = page.getByRole("group", { name: "Workspaces it may read" });
      const comment = spaces.getByRole("button", { name: "Comment", exact: true });
      await comment.waitFor();
      assertEq(await comment.getAttribute("aria-pressed"), "true", "asked for, the workspace starts at Comment");
      assert((await comment.getAttribute("title")).includes("comment, reply, resolve and highlight"), "its tooltip says what it may do");
      await page.getByText("Read and comment", { exact: true }).waitFor();
      // at View, the page reads as a read-only connection's; at Comment again, as one that comments
      await spaces.getByRole("button", { name: "View", exact: true }).click();
      await page.getByRole("button", { name: "Allow read-only access" }).waitFor();
      await comment.click();
      await page.getByRole("button", { name: "Allow reading and commenting" }).click();
      await page.waitForURL("https://client.example/callback**");
      const callback = new URL(page.url());
      const response = await fetch(`${server.base}/oauth/token`, { method: "POST", body: new URLSearchParams({
        grant_type: "authorization_code", client_id: flow.client.client_id, code: callback.searchParams.get("code"),
        code_verifier: flow.verifier, redirect_uri: flow.redirect, resource: `${server.base}/mcp`,
      }) });
      assertEq(response.status, 200);
      const granted = await response.json();
      assert(granted.scope.split(" ").includes("gamma:feedback"), `the token's scope: ${granted.scope}`);
      const listed = await fetch(`${server.base}/mcp`, { method: "POST", headers: { Authorization: `Bearer ${granted.access_token}`,
        "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }) });
      assertEq(listed.status, 200);
      const names = (await listed.json()).result.tools.map((tool) => tool.name);
      assert(["comment", "resolve", "highlight"].every((name) => names.includes(name)), `the writing tools are listed: ${names.join(", ")}`);
      const connection = (await alice.api("/api/integrations/tokens")).tokens.find((t) => t.name === "Codex browser test (OAuth)");
      assertEq(connection?.scope, "read", "a read connection in settings");
      assertEq(JSON.stringify(connection?.comment_workspaces), JSON.stringify([alice.ws]), "it comments in the workspace marked Comment");
      // its comment lands on the page under the connection's name, for alice, and the page's thread shows it
      const pageId = (await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Commented by a connection" } })).id;
      const made = await fetch(`${server.base}/mcp`, { method: "POST", headers: { Authorization: `Bearer ${granted.access_token}`,
        "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "comment", arguments: { page_id: pageId, body: "Read it, looks right." } } }) });
      const result = (await made.json()).result;
      assert(!result.isError, `the comment is written: ${JSON.stringify(result.content)}`);
      const [thread] = (await alice.api(`/api/pages/${pageId}/comments`)).threads;
      assertEq(thread?.comments?.[0]?.agent, "Codex browser test", "signed with the connection's name");
      assertEq(thread.comments[0].author, "alice", "for its account");
      await alice.api(`/api/integrations/tokens/${connection.id}`, { method: "DELETE" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("mcp: the sign-in may let it edit a workspace's notes, and the connection gets the note tools there", async () => {
    const ctx = await alice.context(browser);
    try {
      await ctx.route("https://client.example/callback**", (route) => route.fulfill({ status: 200, contentType: "text/plain", body: "Connection approved." }));
      const flow = await request();
      const page = await openPage(ctx, flow.url);
      const spaces = page.getByRole("group", { name: "Workspaces it may read" });
      await page.getByRole("button", { name: "Allow read-only access" }).waitFor();
      // the open workspace is ticked, View; Edit makes it a connection that edits there
      await spaces.getByRole("button", { name: "Edit", exact: true }).click();
      await page.getByText("Read and edit notes", { exact: true }).waitFor();
      await page.getByRole("button", { name: "Allow reading and editing" }).click();
      await page.waitForURL("https://client.example/callback**");
      const response = await fetch(`${server.base}/oauth/token`, { method: "POST", body: new URLSearchParams({
        grant_type: "authorization_code", client_id: flow.client.client_id, code: new URL(page.url()).searchParams.get("code"),
        code_verifier: flow.verifier, redirect_uri: flow.redirect, resource: `${server.base}/mcp`,
      }) });
      assertEq(response.status, 200);
      const listed = await fetch(`${server.base}/mcp`, { method: "POST", headers: { Authorization: `Bearer ${(await response.json()).access_token}`,
        "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }) });
      const names = (await listed.json()).result.tools.map((tool) => tool.name);
      assert(names.includes("edit_block") && names.includes("create_block") && !names.includes("delete_block"),
        `the note tools, never a deletion: ${names.join(", ")}`);
      const connection = (await alice.api("/api/integrations/tokens")).tokens.find((t) => t.name === "Codex browser test (OAuth)");
      assertEq(JSON.stringify(connection?.edit_workspaces), JSON.stringify([alice.ws]), "it edits the workspace marked Edit");
      await alice.api(`/api/integrations/tokens/${connection.id}`, { method: "DELETE" });
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  // The JSON-RPC messages of an SSE answer, as they come.
  async function* messages(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      for (let end = buffer.indexOf("\n\n"); end >= 0; end = buffer.indexOf("\n\n")) {
        const data = buffer.slice(0, end).split("\n").filter((line) => line.startsWith("data:")).map((line) => line.slice(5)).join("");
        buffer = buffer.slice(end + 2);
        if (data.trim()) yield JSON.parse(data);
      }
    }
  }
  // A session over a connection's token, opened by a client that can show a
  // question (elicitation): `send(message)` posts a message in it.
  async function askedSession(token) {
    const post = (body, session) => fetch(`${server.base}/mcp`, { method: "POST", headers: {
      Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json, text/event-stream",
      "MCP-Protocol-Version": "2025-11-25", ...(session ? { "Mcp-Session-Id": session } : {}) }, body: JSON.stringify(body) });
    const opened = await post({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-11-25",
      capabilities: { elicitation: { form: {} } }, clientInfo: { name: "asked-e2e", version: "0" } } });
    const session = opened.headers.get("mcp-session-id");
    assert(session, "a client that can be asked gets a session");
    for await (const message of messages(opened)) { assertEq(message.result?.serverInfo?.name, "Gamma"); break; }
    assertEq((await post({ jsonrpc: "2.0", method: "notifications/initialized" }, session)).status, 202);
    return (body) => post(body, session);
  }

  await step("mcp: a connection that edits asks before each change through the client and in Gamma, and makes only the changes allowed", async () => {
    // A client that can show a question (elicitation) and a connection that
    // edits alice's workspace, asking first (the default).
    const agent = await alice.api("/api/integrations/tokens", { method: "POST", body: { name: "Asked agent", edit_workspaces: [alice.ws] } });
    const page = await alice.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Asked page" } });
    const notes = async () => (await alice.api(`/api/blocks/${page.id}/children`)).children.map((b) => b.content);
    try {
      const send = await askedSession(agent.token);
      // One change: Gamma's question comes on the call's stream, the answer goes back in the session.
      let id = 10;
      const change = async (content, answer) => {
        const call = messages(await send({ jsonrpc: "2.0", id: ++id, method: "tools/call", params: {
          name: "create_block", arguments: { parent_id: page.id, content } } }));
        let { value: message } = await call.next();
        let question = null;
        if (message.method === "elicitation/create") {
          question = message.params;
          assertEq((await send({ jsonrpc: "2.0", id: message.id, result: answer })).status, 202);
          ({ value: message } = await call.next());
        }
        assertEq(message.id, id, "the call's own result");
        return { question, result: message.result };
      };
      const allowed = await change("Asked note", { action: "accept", content: { remember: false } });
      assert(allowed.question, "Gamma asks first");
      assert(allowed.question.message.startsWith("Asked agent wants to add a note to “Asked page”. It adds 1 line."), allowed.question.message);
      assert(allowed.question.message.includes("\n+ Asked note"), "the question shows the change line by line");
      assert(allowed.question.requestedSchema.properties.remember, "and offers not asking again");
      assert(!allowed.result.isError, JSON.stringify(allowed.result));
      const declined = await change("Declined note", { action: "decline" });
      assert(declined.question && declined.result.isError && declined.result.content[0].text.startsWith("declined"), JSON.stringify(declined.result));
      assertEq(JSON.stringify(await notes()), JSON.stringify(["Asked note"]), "only the change allowed was made");
      // A passage of several blocks is one paste, asked about once (`change` takes one question, then the result).
      const pasted = await change("Pasted one\n\n- pasted two\n- pasted three", { action: "accept", content: { remember: false } });
      assert(pasted.question?.message.startsWith("Asked agent wants to add 3 notes to “Asked page”. It adds 3 lines."),
        pasted.question?.message);
      assert(!pasted.result.isError && pasted.result.structuredContent.block_ids.length === 3, JSON.stringify(pasted.result));
      assertEq(JSON.stringify(await notes()), JSON.stringify(["Asked note", "Pasted one", "- pasted two", "- pasted three"]));
      // Answered in Gamma: the open page draws the question where the note would go, by the agent's name, with
      // Keep and Undo; Keep there makes the change, the client's question is withdrawn, and the note lands
      // folded to its mark (kept on its question).
      const ctx = await alice.context(browser);
      try {
        const tab = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${page.id}`);
        await tab.locator(".blockRowWrap", { hasText: "Asked note" }).waitFor();
        const call = messages(await send({ jsonrpc: "2.0", id: ++id, method: "tools/call", params: {
          name: "create_block", arguments: { parent_id: page.id, content: "Kept in Gamma" } } }));
        const { value: asking } = await call.next();
        assertEq(asking.method, "elicitation/create");
        const ghost = tab.locator(".aiAskGhostWrap", { hasText: "Kept in Gamma" });
        await ghost.waitFor();
        await ghost.getByText("Asked agent wants to add this note").waitFor();
        await ghost.getByRole("button", { name: "Keep", exact: true }).click();
        const { value: withdrawn } = await call.next();
        assertEq(withdrawn.method, "notifications/cancelled");
        assertEq(withdrawn.params.requestId, asking.id, "the client's own question is withdrawn");
        const { value: done } = await call.next();
        assert(done.id === id && !done.result.isError, JSON.stringify(done));
        await until(async () => !(await ghost.count()), { what: "answered, the page draws the question no more" });
        await tab.locator(".blockRowWrap", { hasText: "Kept in Gamma" }).locator(".aiChangeMark").waitFor();
        assertNoProblems(tab);
      } finally {
        await ctx.close();
      }
      assertEq(JSON.stringify(await notes()), JSON.stringify(["Asked note", "Pasted one", "- pasted two", "- pasted three",
        "Kept in Gamma"]));
      // "Don't ask again": this change goes through, and the next one is not asked about.
      const remembered = await change("Remembered note", { action: "accept", content: { remember: true } });
      assert(remembered.question && !remembered.result.isError, JSON.stringify(remembered.result));
      assertEq((await alice.api("/api/integrations/tokens")).tokens.find((t) => t.id === agent.id).asks, false);
      const unasked = await change("Unasked note", null);
      assert(!unasked.question && !unasked.result.isError, "no question once it changes without asking");
      assertEq(JSON.stringify(await notes()), JSON.stringify(["Asked note", "Pasted one", "- pasted two", "- pasted three",
        "Kept in Gamma", "Remembered note", "Unasked note"]));
    } finally {
      await alice.api(`/api/integrations/tokens/${agent.id}`, { method: "DELETE" });
    }
  });

  await step("mcp: an assistant's question is drawn in Gamma whatever the notes show: a drawing on the PDF with the Notes window closed, a note on a page with none", async () => {
    const agent = await alice.api("/api/integrations/tokens", { method: "POST", body: { name: "Drawing agent", edit_workspaces: [alice.ws] } });
    const up = await alice.upload("/api/uploads", makePdf([["A page the assistant draws on", "a passage to box"]]), "asked.pdf", "application/pdf");
    const paper = (await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
      body: { default_title: "Asked paper", source_url: up.source_url } })).id;
    const bare = await alice.upload("/api/uploads", makePdf([["A paper nobody annotated yet"]]), "bare.pdf", "application/pdf");
    const empty = (await alice.api(`/api/blocks/by-doc/${bare.doc_id}`, { method: "POST",
      body: { default_title: "Unannotated paper", source_url: bare.source_url } })).id;
    const ctx = await alice.context(browser);
    try {
      const send = await askedSession(agent.token);
      // A call's question: the call's stream, and the question it opens with.
      const ask = async (id, name, args) => {
        const call = messages(await send({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } }));
        const { value: asking } = await call.next();
        assertEq(asking.method, "elicitation/create");
        return { call, asking };
      };
      const tab = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${paper}`);
      await waitForPdf(tab, 1);
      // the Notes window closed: the PDF alone shows the page
      await tab.getByRole("button", { name: "View", exact: true }).click();
      const notesItem = tab.locator(".menuPopover").getByRole("menuitemcheckbox", { name: "Notes" });
      if ((await notesItem.getAttribute("aria-checked")) === "true") await notesItem.click();
      await tab.keyboard.press("Escape");
      await until(async () => !(await tab.locator('[data-guide="dock.notes"]').count()), { what: "the Notes window closed" });
      const strokes = () => tab.locator('.pdfViewer [data-page="1"] .inkLayer path').count();
      assertEq(await strokes(), 0, "nothing drawn yet");
      // The drawing is drawn where it would land, by the agent's name, with Keep; Keep there answers the client too.
      const drawing = await ask(31, "draw", { page_id: paper, pdf_page: 1, shapes: [{ kind: "rect", box: [0.1, 0.1, 0.6, 0.3] }] });
      const layer = tab.locator('.pdfPageWrap[data-page="1"] .reviewLayer');
      await layer.locator(".reviewFrame").waitFor();
      const tag = layer.locator(".reviewTag", { hasText: "Drawing agent wants to draw this" });
      await tag.waitFor();
      await tag.getByRole("button", { name: "Keep", exact: true }).click();
      const { value: withdrawn } = await drawing.call.next();
      assertEq(withdrawn.method, "notifications/cancelled");
      assertEq(withdrawn.params.requestId, drawing.asking.id, "the client's own question is withdrawn");
      const { value: drawn } = await drawing.call.next();
      assert(drawn.id === 31 && !drawn.result.isError, JSON.stringify(drawn));
      await until(async () => !(await layer.locator(".reviewFrame").count()), { what: "answered, the page draws the question no more" });
      await until(async () => (await strokes()) > 0, { what: "the drawing on page 1" });
      await layer.locator(".reviewObjectMark").waitFor(); // kept on its question: marked where it landed
      // A paper with no notes yet (a PDF's page seeds no placeholder note, so its tree is empty), the Notes
      // window open again, draws a new note it asks to add; declined in the client, it goes from the page.
      await tab.goto(`${server.base}/?ws=${alice.ws}&page=${empty}`);
      await waitForPdf(tab, 1);
      await tab.getByRole("button", { name: "View", exact: true }).click();
      if ((await notesItem.getAttribute("aria-checked")) !== "true") await notesItem.click();
      await tab.keyboard.press("Escape");
      await tab.locator(".notesTail.isEmpty").waitFor();
      const note = await ask(32, "create_block", { parent_id: empty, content: "A first note, asked for" });
      const ghost = tab.locator(".aiAskGhostWrap", { hasText: "A first note, asked for" });
      await ghost.waitFor();
      await ghost.getByText("Drawing agent wants to add this note").waitFor();
      assertEq((await send({ jsonrpc: "2.0", id: note.asking.id, result: { action: "decline" } })).status, 202);
      const { value: declined } = await note.call.next();
      assert(declined.id === 32 && declined.result.isError, JSON.stringify(declined));
      await until(async () => !(await ghost.count()), { what: "answered in the client, the page draws the question no more" });
      assertEq((await alice.api(`/api/blocks/${empty}/children`)).children.length, 0, "declined: no note");
      assertNoProblems(tab);
    } finally {
      await ctx.close();
      await alice.api(`/api/integrations/tokens/${agent.id}`, { method: "DELETE" });
    }
  });

  await step("mcp: a saved library page cannot dismiss authorization", async () => {
    const ctx = await alice.context(browser);
    try {
      const page = await openPage(ctx, server.base);
      const pageId = await newPageViaUi(page, "Page open before authorization");
      assert(pageId, "saved session names a real page");
      const key = `gamma-session:alice@${alice.ws}`;
      await page.evaluate(({ key, pageId }) => localStorage.setItem(key, JSON.stringify({ focusedBlockId: pageId })), { key, pageId });
      const saved = await page.evaluate((key) => localStorage.getItem(key), key);
      const before = (await alice.api("/api/integrations/tokens")).tokens.length;
      await page.goto((await request()).url);
      await page.getByRole("button", { name: "Allow read-only access" }).waitFor();
      // The asynchronous saved-page restore must leave ?gamma_oauth in the
      // URL and the consent screen mounted; give it time to run.
      await sleep(1500);
      assert(new URL(page.url()).searchParams.has("gamma_oauth"), "authorization URL is preserved");
      assert(await page.getByRole("button", { name: "Allow read-only access" }).isVisible());
      assertEq(await page.evaluate((key) => localStorage.getItem(key), key), saved, "saved library session is untouched");
      await page.reload();
      await page.getByRole("button", { name: "Allow read-only access" }).waitFor();
      assertEq((await alice.api("/api/integrations/tokens")).tokens.length, before);
      await page.getByRole("button", { name: "Use another account" }).click();
      await page.getByText("Sign in to connect your assistant").waitFor();
      assert(new URL(page.url()).searchParams.has("gamma_oauth"), "switching accounts keeps the request");
      await alice.login();
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("mcp: cancel returns access_denied without granting a connection", async () => {
    const ctx = await alice.context(browser);
    try {
      await ctx.route("https://client.example/callback**", (route) => route.fulfill({ status: 200, body: "Cancelled" }));
      const before = (await alice.api("/api/integrations/tokens")).tokens.length;
      const page = await openPage(ctx, (await request()).url);
      await page.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.waitForURL("https://client.example/callback**");
      assertEq(new URL(page.url()).searchParams.get("error"), "access_denied");
      assertEq((await alice.api("/api/integrations/tokens")).tokens.length, before);
      assertNoProblems(page);
    } finally { await ctx.close(); }
  });

  await step("mcp: resolve browser page URLs, block links and share links", async () => {
    const ctx = await alice.context(browser, { permissions: ["clipboard-read", "clipboard-write"] });
    const credential = await alice.api("/api/integrations/tokens", { method: "POST", body: { name: "Link browser test" } });
    const readLink = async (url) => {
      const response = await fetch(`${server.base}/mcp`, { method: "POST", headers: {
        Authorization: `Bearer ${credential.token}`, "Content-Type": "application/json", Accept: "application/json, text/event-stream",
      }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "read_gamma_link", arguments: { url } } }) });
      assertEq(response.status, 200);
      const result = (await response.json()).result;
      assert(!result.isError, JSON.stringify(result));
      return result;
    };
    try {
      const page = await openPage(ctx, server.base);
      const pageId = await newPageViaUi(page, "Assistant context page");
      const note = await alice.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "The exact note to discuss" } });
      await page.reload();
      await until(() => new URL(page.url()).searchParams.get("block") === pageId);
      const url = new URL(page.url());
      assertEq(url.searchParams.get("block"), pageId);
      assertEq(url.searchParams.get("ws"), alice.ws);
      assert(!url.searchParams.has("share"));
      assertEq((await alice.api(`/api/share-settings/${pageId}`)).token, null, "reading a browser URL needs no share");
      const result = await readLink(url.href);
      assertEq(result.structuredContent.page_id, pageId);
      assert(result.content[0].text.includes("The exact note to discuss"));
      await page.locator(`.sortableBlockWrap[data-block-id="${note.id}"]`).hover(); // the handle shows with its row
      await page.locator(`.sortableBlockWrap[data-block-id="${note.id}"] .dragHandle`).first().click();
      await page.getByText("Copy link to block", { exact: true }).click();
      const noteUrl = new URL(await page.evaluate(() => navigator.clipboard.readText()));
      assertEq(noteUrl.searchParams.get("block"), note.id);
      assertEq((await readLink(noteUrl.href)).structuredContent.block_id, note.id);
      const share = await alice.api(`/api/share/${pageId}`, { method: "POST" });
      assertEq((await readLink(`${server.base}/?share=${share.token}`)).structuredContent.page_id, pageId);
      assertNoProblems(page);
    } finally {
      await ctx.close();
      await alice.api(`/api/integrations/tokens/${credential.id}`, { method: "DELETE" });
    }
  });

}
