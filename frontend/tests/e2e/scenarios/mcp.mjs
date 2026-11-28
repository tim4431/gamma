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

  await step("mcp: a connection that edits asks before each change through the client, and makes only the changes allowed", async () => {
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
      // "Don't ask again": this change goes through, and the next one is not asked about.
      const remembered = await change("Remembered note", { action: "accept", content: { remember: true } });
      assert(remembered.question && !remembered.result.isError, JSON.stringify(remembered.result));
      assertEq((await alice.api("/api/integrations/tokens")).tokens.find((t) => t.id === agent.id).asks, false);
      const unasked = await change("Unasked note", null);
      assert(!unasked.question && !unasked.result.isError, "no question once it changes without asking");
      assertEq(JSON.stringify(await notes()), JSON.stringify(["Asked note", "Pasted one", "- pasted two", "- pasted three",
        "Remembered note", "Unasked note"]));
    } finally {
      await alice.api(`/api/integrations/tokens/${agent.id}`, { method: "DELETE" });
    }
  });

  await step("mcp: while the account reviews, a connection's note and text box are made without asking the client, suggestions drawn where they land with ✓ and ✗; back in Editing it asks again, and its row in Settings → Integrations opens the change where it landed", async () => {
    // A client that can be asked, and a connection that edits alice's workspace, asking first (the default).
    const agent = await alice.api("/api/integrations/tokens", { method: "POST", body: { name: "Reviewed agent", edit_workspaces: [alice.ws] } });
    const up = await alice.upload("/api/uploads", makePdf([["A paper the assistant marks up", "a passage to remark on"]]), "reviewed.pdf", "application/pdf");
    const paper = (await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
      body: { default_title: "Reviewed paper", source_url: up.source_url } })).id;
    const ctx = await alice.context(browser);
    try {
      const send = await askedSession(agent.token);
      // A call: its stream and its first message, Gamma's question or, asking nobody, its own result.
      let id = 40;
      const call = async (name, args) => {
        const stream = messages(await send({ jsonrpc: "2.0", id: ++id, method: "tools/call", params: { name, arguments: args } }));
        return { stream, first: (await stream.next()).value, id };
      };
      await alice.api("/api/prefs/ai-review", { method: "PUT", body: { value: true } });
      const tab = await openPage(ctx, `${server.base}/?ws=${alice.ws}&page=${paper}`);
      await waitForPdf(tab, 1);
      await tab.getByRole("button", { name: "View", exact: true }).click();
      const notesItem = tab.locator(".menuPopover").getByRole("menuitemcheckbox", { name: "Notes" });
      if ((await notesItem.getAttribute("aria-checked")) !== "true") await notesItem.click();
      await tab.keyboard.press("Escape");
      await tab.locator(".notesTail.isEmpty").waitFor();
      // Reviewing: the note is made at once, a suggestion drawn as new with ✓ and ✗ that name the agent.
      const note = await call("create_block", { parent_id: paper, content: "A note the assistant suggests" });
      assertEq(note.first.id, note.id, "no question: the call's own result");
      assert(!note.first.result.isError && note.first.result.structuredContent.change_state === "open", JSON.stringify(note.first.result));
      const suggested = tab.locator(".blockRowWrap", { hasText: "A note the assistant suggests" });
      await suggested.locator(".mdDiffView.mdDiffNew").waitFor();
      const accept = suggested.getByRole("button", { name: "Accept", exact: true });
      assert((await accept.getAttribute("title")).includes("Reviewed agent"), "its ✓ names the agent");
      // and a text box on the PDF page, framed there with its own ✓ and ✗: ✗ takes it back
      const box = await call("add_text_box", { page_id: paper, pdf_page: 1, text: "A margin remark", at: [0.1, 0.5] });
      assertEq(box.first.id, box.id, "no question for the text box either");
      const layer = tab.locator('.pdfPageWrap[data-page="1"] .reviewLayer');
      const remark = tab.locator('.pdfViewer [data-page="1"] .textBox', { hasText: "A margin remark" });
      await layer.locator(".reviewObjectFrame").waitFor();
      await remark.waitFor();
      await layer.getByRole("button", { name: "Reject", exact: true }).click();
      await until(async () => !(await layer.locator(".reviewObjectFrame").count()) && !(await remark.count()),
        { what: "rejected, the text box goes" });
      await accept.click();
      await until(async () => !(await suggested.locator(".mdDiffView, .reviewBtns").count()), { what: "accepted, the note is drawn as written" });
      // Editing again: the next change asks the client first, and lands as written.
      await alice.api("/api/prefs/ai-review", { method: "PUT", body: { value: false } });
      const asked = await call("create_block", { parent_id: paper, content: "A note asked about" });
      assertEq(asked.first.method, "elicitation/create", "Editing asks the client");
      assertEq((await send({ jsonrpc: "2.0", id: asked.first.id, result: { action: "accept", content: { remember: false } } })).status, 202);
      const { value: made } = await asked.stream.next();
      assert(made.id === asked.id && !made.result.isError && made.result.structuredContent.change_state === "kept", JSON.stringify(made));
      const kept = tab.locator(".blockRowWrap", { hasText: "A note asked about" });
      await kept.waitFor();
      assertEq(await kept.locator(".mdDiffView, .reviewBtns").count(), 0, "allowed in the client: drawn as written");
      // Its row among the connection's changes in Settings → Integrations opens it where it landed, its diff shown.
      await tab.locator('[data-guide="header.account"]').click();
      await tab.getByRole("menuitem", { name: "Settings…", exact: true }).click();
      await tab.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Integrations", exact: true }).click();
      await tab.locator('.settingsPane [data-setting="Reviewed agent"]').getByRole("button", { name: "Changes", exact: true }).click();
      const changeRows = tab.locator('.settingsPane [data-setting="Added a note in “Reviewed paper”"]');
      await until(async () => (await changeRows.count()) === 2, { what: "both notes among its changes" });
      await changeRows.first().getByRole("button", { name: "Open", exact: true }).click();
      await tab.getByRole("dialog", { name: "Settings", exact: true }).waitFor({ state: "detached" });
      await kept.locator(".mdDiffView.mdDiffNew").waitFor();
      await kept.getByRole("button", { name: "Hide the change", exact: true }).click();
      await until(async () => !(await kept.locator(".mdDiffView").count()), { what: "× hides it" });
      assertEq(JSON.stringify((await alice.api(`/api/blocks/${paper}/children`)).children.map((b) => b.content)),
        JSON.stringify(["A note the assistant suggests", "A note asked about"]), "the text box rejected, the notes kept");
      assertNoProblems(tab);
    } finally {
      await ctx.close();
      await alice.api("/api/prefs/ai-review", { method: "PUT", body: { value: false } });
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
