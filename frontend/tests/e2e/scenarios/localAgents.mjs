import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, PYTHON, ROOT, Server, wanted } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";

// An agent on this computer answering a Send (docs/dev/local_agents.md): the
// desktop app's own server, where Gamma itself starts Codex in a work
// folder's directory with the comments its maker sent. The vaults recipe
// (GAMMA_FOLDER_LINKS, no shell secret, a desktop user agent) with local
// agents on and the stand-ins of backend/tests/fake_agents/ in place of the
// CLIs, so no real agent ever runs: the SyncTeX sample paper as a work
// folder, two comments on its PDF, Codex turned on for the folder by its
// maker, the Feedback dialog naming Codex before and after, Send, the
// request worked and done with what changed, the replies signed Codex on
// the person's computer and the threads resolved, and the agent turned off.
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const PLACES = JSON.parse(fs.readFileSync(path.join(PAPER, "places.json"), "utf8"));
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const PAGE = { width: 612, height: 792 };
const SUMMARY = "Moved the legend and introduced the penalty's weight.";

// An area on page 1 of the paper from places.json's fraction rect, in the
// page's points as the viewer stores it.
function areaOf([x1, y1, x2, y2]) {
  const r = { x1: x1 * PAGE.width, y1: y1 * PAGE.height, x2: x2 * PAGE.width, y2: y2 * PAGE.height };
  return { pageNumber: 1, ...PAGE, boundingRect: r, rects: [r], area: true };
}

export async function localAgentScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until } = env;
  if (!wanted("local-agents")) return;
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-agents-"));
  const agentLog = path.join(disk, "agent.jsonl");
  // Both agents are the stand-ins, so neither probe reaches a CLI of this
  // computer. Codex answers every comment it is handed as fixed, after an
  // edit of the paper's method and a pause long enough to be seen working.
  const server = new Server({ env: {
    GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: "", GAMMA_LOCAL_AGENTS: "1",
    GAMMA_AGENT_CODEX: JSON.stringify([PYTHON, path.join(FAKES, "fake_codex.py")]),
    GAMMA_AGENT_CLAUDE: JSON.stringify([PYTHON, path.join(FAKES, "fake_claude.py")]),
    FAKE_AGENT: JSON.stringify({ each: "fixed", summary: SUMMARY, delay: 8, log: agentLog,
      edits: [{ path: "sections/method.tex", old: "by gradient descent.", new: "by gradient descent with weight decay $\\lambda$." }] }),
  } });
  try {
    await server.start();
    await server.manage("create-user", "la-user", "la-pw");
    const user = await new Account(server, "la-user", "la-pw").login();
    const at = (id) => `${server.base}/?page=${id}&ws=${user.ws}`;
    const desktop = () => user.context(browser, { userAgent: DESKTOP_UA });
    // written as an editor that finished a while ago, so a round takes it at once
    const paperDir = path.join(disk, "paper");
    for (const rel of ["main.tex", "sections/method.tex", "figures/fig1.pdf", "main.pdf", "main.synctex.gz"]) {
      const file = path.join(paperDir, rel);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, fs.readFileSync(path.join(PAPER, "v1", rel)));
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    }
    const method = path.join(paperDir, "sections", "method.tex");
    const threadsOf = async (page) => (await user.api(`/api/pages/${page}/comments?state=all`)).threads;
    const openComments = async (page) => {
      const panel = page.locator(".commentsPanel");
      if (!(await panel.count())) {
        await page.getByRole("button", { name: "View", exact: true }).click();
        await page.locator(".menuPopover .ctxMenuItem", { hasText: "Comments" }).click();
        await page.keyboard.press("Escape");
      }
      await panel.waitFor();
      return panel;
    };
    const openFeedback = async (page) => {
      await openComments(page);
      await page.locator(".dockWindow:has(.commentsPanel)").getByRole("button", { name: "Feedback", exact: true }).click();
      const d = page.getByRole("dialog", { name: "Feedback", exact: true });
      await d.locator(".feedbackEntry").nth(1).waitFor({ timeout: 10000 });
      return d;
    };
    const notes = async (d) => (await d.locator(".feedbackAgentNote").allInnerTexts()).map((s) => s.trim());

    let link = null, main = null;
    const threads = [];
    await step("local-agents: a work folder of the paper with two comments on its PDF, and Codex found on this computer", async () => {
      link = await user.api("/api/folder-links", { method: "POST", body: { path: paperDir, new_folder: true, pull: true, push: "everything" } });
      await user.api(`/api/folder-links/${link.id}/sync?wait=1`, { method: "POST" });
      main = await until(async () => (await user.api(`/api/folders/${link.folder_id}/pages?limit=500`)).pages
        .find((p) => p.properties?.file_name === "main.pdf"), { timeout: 30000, what: "main.pdf's page" });
      for (const [place, body] of [[PLACES.v1.figure.rect, "move the legend inside the plot"], [PLACES.v1.equation.rect, "lambda is never introduced"]]) {
        const area = await user.api("/api/blocks", { method: "POST", body: { parent_id: main.id, content: "",
          properties: { pdf_position: areaOf(place), color: "rgba(255, 226, 143, 0.65)" } } });
        threads.push((await user.api(`/api/pages/${main.id}/comments`, { method: "POST", body: { block_id: area.id, body } })).thread);
      }
      const found = (await user.api("/api/local-agents?fresh=1")).agents;
      assertEq(found.map((a) => a.name).join(","), "codex,claude", "the agents Gamma knows");
      const codex = found.find((a) => a.name === "codex");
      assert(codex.found && codex.ready, `Codex is found and signed in: ${JSON.stringify(codex)}`);
      assertEq(codex.label, "Codex");
      assertEq(link.agent ?? null, null, "the folder has no agent yet");
    });

    await step("local-agents: with no agent on, the Feedback dialog sends to an agent the person runs and says where to turn one on", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const d = await openFeedback(page);
        await d.getByRole("button", { name: "Send 2 comments", exact: true }).waitFor();
        // the folder is kept here and an agent was found: the line offering one
        await until(async () => (await notes(d)).some((s) => s.startsWith("To have Codex or Claude Code answer here, turn one on in the sync panel")),
          { timeout: 15000, what: "the line saying where to turn an agent on" });
        await page.keyboard.press("Escape");
        await d.waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("local-agents: the vault's maker turns Codex on for the work folder, which nothing waits on yet", async () => {
      const turned = await user.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { agent: "codex" } });
      assertEq(turned.queued, 0, "no Send was waiting");
      assertEq(turned.agent?.name, "codex", "the vault's agent");
      assertEq(turned.agent.label, "Codex");
      assertEq(turned.agent.ready, true, "ready, from the probe the PATCH ran");
      assertEq(turned.agent.running, null, "nothing running");
      const listed = (await user.api("/api/folder-links")).links.find((l) => l.id === link.id);
      assertEq(listed.agent?.name, "codex", "the listing carries it");
      const doc = await user.api(`/api/feedback?scope=${link.folder_id}`);
      assertEq(JSON.stringify([doc.agent?.name, doc.agent?.takes, doc.agent?.ready]), JSON.stringify(["codex", true, true]), "the feedback document's agent takes the maker's Sends");
    });

    let request = null;
    await step("local-agents: Send to Codex from the Feedback dialog, which shows it working and then done with what it changed", async () => {
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const d = await openFeedback(page);
        const send = d.getByRole("button", { name: "Send 2 comments to Codex", exact: true });
        await send.waitFor({ timeout: 10000 });
        assert((await notes(d)).includes("Codex works on this computer, in this folder's directory, and answers each comment."), "the hint under the note");
        await d.getByRole("textbox", { name: "Instruction for the agent (optional)" }).fill("fix these");
        await send.click();
        const row = d.locator(".feedbackRequest");
        await row.waitFor({ timeout: 10000 });
        const state = row.locator(".feedbackRequestState");
        await until(async () => (await state.innerText()).trim() === "Codex is working", { timeout: 20000, what: "the request reads Codex is working" });
        await until(async () => (await state.innerText()).trim() === "Done by Codex", { timeout: 40000, what: "the request reads Done by Codex" });
        // the agent's own words on a line of their own, under the request's
        const done = (await row.locator(".feedbackRequestDetail").innerText()).trim();
        assert(done.includes(SUMMARY), `the request's detail has the summary: ${done}`);
        // the file the comment's source line names is named; the others are counted
        assert(done.includes("Changed sections/method.tex."), `and the file changed: ${done}`);
        await page.keyboard.press("Escape");
        await d.waitFor({ state: "detached" });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const listed = await user.api("/api/feedback/requests?after=0");
      assertEq(listed.requests.length, 1, "one request");
      request = listed.requests[0];
      assertEq(JSON.stringify([request.state, request.state_by, request.note]), JSON.stringify(["done", "Codex", "fix these"]), "done by Codex");
      assert(fs.readFileSync(method, "utf8").includes("by gradient descent with weight decay $\\lambda$."), "the edit is in the directory");
      // the agent ran in the folder's directory, told of both threads, and
      // got none of the server's own variables
      const seen = fs.readFileSync(agentLog, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
      const servers = seen.filter((e) => e.argv?.[0] === "app-server");
      assert(servers.some((e) => path.relative(e.cwd, paperDir) === ""), `an app-server ran in the work folder: ${JSON.stringify(servers)}`);
      assert(seen.every((e) => !e.env || !e.env.some((name) => name.startsWith("GAMMA_"))), "no GAMMA_ variable reached the agent");
      const turn = seen.find((m) => m.in?.method === "turn/start").in.params;
      const prompt = turn.input.filter((p) => p.type === "text").map((p) => p.text).join("\n");
      assert(threads.every((th) => prompt.includes(`"thread_id": "${th.id}"`)), "the prompt names both threads");
      assertEq(turn.sandboxPolicy?.networkAccess, false, "a sandbox without network");
    });

    await step("local-agents: Codex's replies are signed Codex on the person's computer and the threads it fixed are resolved", async () => {
      const stored = await threadsOf(main.id);
      for (const th of threads) {
        const got = stored.find((s) => s.id === th.id);
        assert(got?.resolved_at, `${th.id} is resolved`);
        const reply = got.comments[1];
        assertEq(JSON.stringify([reply?.agent, reply?.agent_kind, reply?.author, reply?.body]),
          JSON.stringify(["Codex", "local", "la-user", `Done for ${th.id}.`]), "the reply");
      }
      const ctx = await desktop();
      const page = await openPage(ctx, at(main.id));
      try {
        await waitForPdf(page, 1);
        const panel = await openComments(page);
        await page.locator(".dockWindow:has(.commentsPanel) .segGroup button", { hasText: "Resolved (2)" }).click();
        const row = panel.locator(`.commentsPanelRow[data-thread-id="${threads[0].id}"]`);
        await row.waitFor();
        await row.click();
        const reply = page.locator(".commentThread .commentItem.reply", { has: page.locator(".commentAuthor", { hasText: "Codex" }) }).first();
        await reply.waitFor({ timeout: 10000 });
        const byline = "Written by Codex on la-user's computer";
        assertEq(await reply.locator(".commentAuthor").getAttribute("title"), byline, "the byline names the computer");
        assertEq(await reply.locator(":scope > [title]").first().getAttribute("title"), byline, "and so does the avatar");
        await page.keyboard.press("Escape");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("local-agents: turned off by its maker, the folder has no agent and the feedback document names none", async () => {
      const off = await user.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { agent: "" } });
      assertEq(off.agent ?? null, null, "no agent");
      const doc = await user.api(`/api/feedback?scope=${link.folder_id}`);
      assertEq(doc.agent, null, "the document says none");
      await user.api(`/api/folder-links/${link.id}`, { method: "DELETE" });
    });
  } finally {
    await server.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
