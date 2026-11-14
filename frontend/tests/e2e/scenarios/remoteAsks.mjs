import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Account, PYTHON, ROOT, Server, wanted } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";

// Ask on this computer (step 5b of docs/research/agent-collab.md;
// docs/dev/local_agents.md "Asking on this computer"): the two Gammas of
// remoteChat.mjs, a NAS (a plain server on loopback, so reached securely)
// and a computer (the desktop app's own server with local agents on, a
// shell secret and the stand-in Codex of backend/tests/fake_agents/, which
// asks to fetch the bibliography in every turn). The computer's own account
// makes a work folder of the NAS's folder "Paper", shows the computer there
// and turns Codex on for it, and with the shell's word lets it ask on this
// computer. The shell is that account with the word (`ShellAccount`), and it
// follows what waits on this computer from the setup to the end, as the
// desktop app's main does (a held read of the asks, again and again), so the
// computer hears its shell throughout. Alice's plain browser on the NAS then
// sends a message: her page says the turn waits for her answer on the
// computer and draws no card, the NAS holds a `waiting` flag and never the
// command; the computer's own server gives the card's words to the shell's
// word alone, and only that answers: allowed once, the reply comes back;
// declined, Codex goes on without it; Stop on the NAS page takes a waiting
// ask off the computer.
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const FETCH = "curl -L https://example.org/refs.bib -o refs.bib";
const REASON = "Fetch the bibliography the paper cites.";
const HOST = "example.org";

// The computer's own account in the desktop window, with the shell's word
// (main.js shellWord) on every request.
class ShellAccount extends Account {
  constructor(server, name, password, secret) {
    super(server, name, password);
    this.secret = secret;
  }
  headers(extra = {}) {
    return super.headers({ "X-Gamma-Shell": this.secret, ...extra });
  }
}

// The desktop shell's follower of what waits on this computer (main.js
// followAsks): GET /api/local-agents/asks held while nothing changed, read
// again at once, its newest `{version, asks}` kept; a refusal (`refused`, a
// bug) or a lost connection (`error`) noted and read again after a pause.
// `stop()` ends it.
function followAsks(server, shell, sleep) {
  const seen = { version: 0, asks: [], reads: 0, refused: "", error: "" };
  const stopping = new AbortController();
  const done = (async () => {
    while (!stopping.signal.aborted) {
      try {
        const r = await server.fetch(`${server.base}/api/local-agents/asks?after=${seen.version}&wait=20`,
          { headers: shell.headers(), signal: stopping.signal });
        const body = await r.json().catch(() => null);
        if (!r.ok || !body) {
          seen.refused = `${r.status}: ${JSON.stringify(body)}`;
          await sleep(500);
          continue;
        }
        Object.assign(seen, { version: body.version, asks: body.asks || [], error: "" });
        seen.reads += 1;
      } catch (err) {
        if (stopping.signal.aborted) break;
        seen.error = String(err?.message || err);
        await sleep(500);
      }
    }
  })();
  return { seen, stop: async () => { stopping.abort(); await done; } };
}

export async function remoteAskScenarios(env) {
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until, sleep } = env;
  if (!wanted("remote-asks")) return;
  const disk = fs.mkdtempSync(path.join(os.tmpdir(), "gamma-e2e-remote-asks-"));
  const agentLog = path.join(disk, "agent.jsonl");
  const secret = crypto.randomBytes(24).toString("hex");
  const nas = new Server();
  const computer = new Server({ env: {
    GAMMA_FOLDER_LINKS: "1", GAMMA_SHELL_SECRET: secret, GAMMA_LOCAL_AGENTS: "1",
    GAMMA_AGENT_CODEX: JSON.stringify([PYTHON, path.join(FAKES, "fake_codex.py")]),
    GAMMA_AGENT_CLAUDE: path.join(disk, "no-claude.exe"),
    // every turn asks to fetch the bibliography, and its reply says what it
    // was told: "Ran it." or "Did not run it."
    FAKE_AGENT: JSON.stringify({ ask: [{ command: FETCH, reason: REASON, host: HOST }], log: agentLog }),
  } });
  let follower = null;
  try {
    await nas.start();
    await computer.start();
    await nas.manage("create-user", "ra-alice", "ra-alice-pw");
    const out = await nas.manage("create-workspace", "Optics lab", "ra-alice", "shared");
    const wsId = (out.match(/workspace (\S+)/) || [])[1];
    assert(wsId, `shared workspace id from: ${out}`);
    const alice = await new Account(nas, "ra-alice", "ra-alice-pw").login();
    alice.ws = wsId;
    await computer.manage("create-user", "ra-owner", "ra-owner-pw");
    // the same session, as a page of the window has it (no word) and as the shell sends it
    const owner = await new Account(computer, "ra-owner", "ra-owner-pw").login();
    const shell = new ShellAccount(computer, "ra-owner", "ra-owner-pw", secret);
    Object.assign(shell, { session: owner.session, ws: owner.ws, defaultWs: owner.defaultWs });
    follower = followAsks(computer, shell, sleep);
    const waiting = () => follower.seen.asks;

    const folderId = await alice.folder("Paper");
    const filesOf = async (folder) => (await alice.api(`/api/folders/${folder}/pages?limit=500`)).pages;
    const heard = () => fs.readFileSync(agentLog, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
    const decisions = () => heard().filter((e) => e.answer?.result?.decision !== undefined).map((e) => e.answer.result.decision);
    const dir = path.join(disk, "Paper");
    for (const rel of ["main.tex", "sections/method.tex", "figures/fig1.pdf", "main.pdf", "main.synctex.gz"]) {
      const file = path.join(dir, rel);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, fs.readFileSync(path.join(PAPER, "v1", rel)));
      const before = new Date(Date.now() - 10_000);
      fs.utimesSync(file, before, before);
    }

    let pdf = "", name = "", linkId = "";
    const at = () => `${nas.base}/?ws=${wsId}&page=${pdf}`;
    const conversation = async () => (await alice.api(`/api/local-agents/chat?page=${pdf}`)).conversation;
    const lastMessage = async () => (await conversation())?.messages.at(-1);
    const openChat = async (page) => {
      const dock = page.locator(".dockWindow:has(.chatPanel)");
      if (!(await dock.count())) {
        await page.getByRole("button", { name: "View", exact: true }).click();
        await page.locator(".menuPopover .ctxMenuItem", { hasText: "AI Chat" }).click();
        await page.keyboard.press("Escape");
      }
      await dock.waitFor();
      return dock;
    };
    const codexSide = async (page) => {
      const dock = await openChat(page);
      await dock.locator(".segGroup").getByRole("button", { name: "Codex", exact: true }).click();
      await dock.locator(".computerStrip").waitFor();
      return dock;
    };
    const stripText = async (dock) => (await dock.locator(".computerStrip").innerText().catch(() => "")).trim();
    const pillText = async (dock) => (await dock.locator(".chatSteps").last().innerText().catch(() => "")).trim();
    // a message from alice's page, and the ask it raised on the computer
    const send = async (dock, text) => {
      const before = new Set(waiting().map((a) => a.id));
      await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill(text);
      await dock.getByRole("button", { name: "Send", exact: true }).click();
      await dock.locator(".chatBubble.user", { hasText: text }).waitFor();
      return until(() => waiting().find((a) => !before.has(a.id)),
        { timeout: 30000, every: 250, what: `the ask on the computer (${follower.seen.refused || follower.seen.error || `${follower.seen.reads} reads`})` });
    };
    // the shell's answer, as main.js answerAgentAsk posts it
    const answer = (ask, decision, account = shell) => computer.fetch(`${computer.base}/api/local-agents/asks/${encodeURIComponent(ask.id)}`,
      { method: "POST", headers: account.headers({ "Content-Type": "application/json" }), body: JSON.stringify({ decision }) });
    // the waiting strip and pill on alice's page, and no card
    const saysItWaits = async (dock) => {
      const words = `Waiting for your answer on ${name}`;
      await until(async () => (await stripText(dock)) === words && (await dock.locator(".computerStrip.asking").count()) === 1,
        { timeout: 20000, every: 250, what: `the strip reads ${words}: ${await stripText(dock)}` });
      await until(async () => (await pillText(dock)) === `Codex is waiting for your answer on ${name}`,
        { what: `the turn's pill says it waits on the computer: ${await pillText(dock)}` });
      assertEq(await dock.locator(".computerAsk").count(), 0, "no card on the NAS's page");
    };

    await step("remote-asks: Ask on this computer is set only with the shell's word, only for the NAS's work folder", async () => {
      const token = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: "Gamma desktop: Paper synced vault", scope: "write", expires_in_days: 365 } });
      const link = await shell.api("/api/folder-links", { method: "POST", body: { folder: folderId, path: dir, notes: true, pull: true,
        push: "everything", choose: true, remote_url: nas.base, workspace: wsId, token: token.token, token_id: token.id } });
      linkId = link.id;
      await shell.api(`/api/folder-links/${link.id}/selection`, { method: "PUT", body: { base_hash: "",
        selection: { base: "preset", changes: [{ path: "main.pdf", on: true }, { format: "latex", on: true }] } } });
      const main = await until(async () => {
        const now = await shell.api(`/api/folder-links/${link.id}`);
        if (now.status?.last_error) throw new Error(`the round failed: ${now.status.last_error}`);
        return (await filesOf(folderId)).find((p) => p.properties?.file_name === "main.pdf");
      }, { timeout: 45000, every: 500, what: "main.pdf pushed into the NAS's folder" });
      pdf = main.id;
      const minted = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: `Gamma desktop: ${os.hostname()}`, scope: "computer", expires_in_days: 365 } });
      const made = await shell.api("/api/computer-links", { method: "POST",
        body: { remote_url: nas.base, workspace: wsId, token: minted.token, token_id: minted.id } });
      await until(async () => (await shell.api("/api/computer-links")).links.find((l) => l.id === made.link.id)?.status?.state === "connected",
        { timeout: 45000, every: 500, what: "the channel connected" });
      assertEq((await shell.api(`/api/folder-links/${link.id}`, { method: "PATCH", body: { agent: "codex" } })).agent?.name, "codex",
        "Codex on for the work folder");

      const runs = (account, value) => account.api(`/api/folder-links/${link.id}`, { method: "PATCH", raw: true, body: { agent_runs: value } });
      const plainly = await runs(owner, "computer");
      assertEq(plainly.status, 403, `a session alone may not: ${await plainly.text()}`);
      const inChat = await runs(shell, "ask");
      assertEq(inChat.status, 409, `asking in the chat is for this computer's own work folders: ${await inChat.text()}`);
      const set = await runs(shell, "computer");
      const body = await set.json();
      assertEq(set.status, 200, `set with the shell's word: ${JSON.stringify(body)}`);
      assertEq(body.agent?.runs, "computer", "the folder's agent asks on this computer");
      assert(!body.older, "the NAS says that its pages show a turn that waits");
      assertEq((await shell.api(`/api/folder-links/${link.id}`)).agent?.runs, "computer", "and the listing says so");

      const [seen] = await until(async () => {
        const list = (await alice.api(`/api/pages/${pdf}/computer`)).computers;
        return list.length && list[0].online && list[0].vault?.agent?.name === "codex" ? list : null;
      }, { timeout: 45000, every: 500, what: "the page's computer, online, its vault with Codex" });
      name = seen.name;
      await until(async () => (await alice.api(`/api/local-agents/chat?page=${pdf}`)).computer?.ready === true,
        { timeout: 30000, every: 500, what: "the chat's computer, ready" });
      assertEq(follower.seen.refused, "", "the shell reads what waits on the computer");
      assertEq(waiting().length, 0, "nothing waits yet");
    });

    let first = null;
    await step("remote-asks: a message on the NAS page asks; the page says it waits on the computer and draws no card", async () => {
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        first = await send(dock, "get the references");
        await saysItWaits(dock);
        // the turn the NAS holds for her is a row of Background tasks that says so
        await page.click("button[aria-label='Background tasks']");
        await page.locator(".tasksPopover .taskRow", { hasText: `Codex on ${name}` })
          .filter({ hasText: `Waiting for your answer on ${name}` }).waitFor({ timeout: 10000 });
        await page.keyboard.press("Escape");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const message = await lastMessage();
      assertEq(JSON.stringify([message.state, message.waiting, message.asks?.length || 0]), JSON.stringify(["running", true, 0]),
        "the NAS's message waits and holds no ask");
      assert(message.steps.some((s) => s.kind === "run" && s.program === "curl" && s.status === "asked"), `its step: ${JSON.stringify(message.steps)}`);
      const there = JSON.stringify(await conversation());
      for (const word of ["refs.bib", HOST, REASON, first.id, JSON.stringify(dir).slice(1, -1)]) assert(!there.includes(word), `the NAS never holds ${word}: ${there.slice(0, 600)}`);
      // the NAS's own answer route refuses a turn of another computer, even with the right ids
      const r = await nas.fetch(`${nas.base}/api/local-agents/chat/turns/${encodeURIComponent(message.turn_id)}/asks/${encodeURIComponent(first.id)}`,
        { method: "POST", headers: alice.headers({ "Content-Type": "application/json" }), body: JSON.stringify({ decision: "once" }) });
      assertEq(r.status, 409, `answered on the computer, not on the NAS: ${await r.text()}`);
      assertEq(waiting().map((a) => a.id).join(), first.id, "the ask still waits on the computer");
    });

    await step("remote-asks: the computer's own server gives the card's words, and only the shell's word answers", async () => {
      const ask = waiting().find((a) => a.id === first.id);
      assert(ask, `the ask is listed: ${JSON.stringify(waiting())}`);
      assertEq(JSON.stringify([ask.kind, ask.agent, ask.label, ask.command, ask.host, ask.reason, ask.link_id]),
        JSON.stringify(["command", "codex", "Codex", FETCH, HOST, REASON, linkId]), "what the card shows");
      assertEq(path.relative(ask.dir, dir), "", `it runs in the paper's directory: ${ask.dir}`);
      assertEq(path.relative(ask.folder, dir), "", `the work folder's directory: ${ask.folder}`);
      assertEq(JSON.stringify(ask.server), JSON.stringify({ origin: nas.base, account: "ra-alice" }), "from the NAS, as alice");
      assert(!ask.inner && !ask.marked && !ask.background, `nothing more to say of it: ${JSON.stringify(ask)}`);
      assert(Date.parse(ask.expires_at) > Date.parse(ask.at), `declined by itself later: ${ask.at} → ${ask.expires_at}`);

      // the window's session alone neither reads nor answers, nor does the computer's own chat route
      const read = await computer.fetch(`${computer.base}/api/local-agents/asks`, { headers: owner.headers() });
      assertEq(read.status, 403, `no word, no list: ${await read.text()}`);
      const plainly = await answer(ask, "once", owner);
      assertEq(plainly.status, 403, `no word, no answer: ${await plainly.text()}`);
      const inChat = await computer.fetch(`${computer.base}/api/local-agents/chat/turns/${encodeURIComponent(ask.turn_id)}/asks/${encodeURIComponent(ask.id)}`,
        { method: "POST", headers: owner.headers({ "Content-Type": "application/json" }), body: JSON.stringify({ decision: "once" }) });
      assertEq(inChat.status, 409, `a remote turn is answered on its own route alone: ${await inChat.text()}`);
      const forChat = await answer(ask, "chat");
      assertEq(forChat.status, 400, `only once or a decline here: ${await forChat.text()}`);
      assertEq(waiting().map((a) => a.id).join(), ask.id, "still waiting after all of them");

      const before = decisions().length;
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        await saysItWaits(dock);
        const allowed = await answer(ask, "once");
        const said = await allowed.json();
        assertEq(allowed.status, 200, `allowed once: ${JSON.stringify(said)}`);
        assertEq(JSON.stringify([said.answered, typeof said.version]), JSON.stringify([true, "number"]), "the answer and the asks' version");
        await until(() => !waiting().some((a) => a.id === ask.id), { timeout: 25000, every: 250, what: "the ask leaves the shell's list" });
        await dock.locator(".chatBubble.ai", { hasText: "Ran it." }).waitFor({ timeout: 30000 });
        await dock.locator(".chatSteps").last().click();
        // allowed, the step goes on to read what the command did (only a
        // declined step keeps its answer: docs/dev/local_agents.md)
        await dock.locator(".chatToolActionHead", { hasText: "Ran curl" }).waitFor();
        await until(async () => (await stripText(dock)).startsWith(`Codex on ${name}`), { what: `the strip ready again: ${await stripText(dock)}` });
        assertNoProblems(page);
      } finally { await ctx.close(); }
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["accept"]), "Codex heard accept");
      const message = await lastMessage();
      assertEq(JSON.stringify([message.state, message.waiting]), JSON.stringify(["done", false]), "the NAS's message no longer waits");
    });

    await step("remote-asks: a decline goes back as declined, and Stop on the NAS takes a waiting ask off the computer", async () => {
      const before = decisions().length;
      const ctx = await alice.context(browser);
      const page = await openPage(ctx, at());
      try {
        await waitForPdf(page, 1);
        const dock = await codexSide(page);
        const second = await send(dock, "and the figures' sources");
        await saysItWaits(dock);
        const declined = await answer(second, "deny");
        assertEq(declined.status, 200, `declined: ${await declined.text()}`);
        await dock.locator(".chatBubble.ai", { hasText: "Did not run it." }).waitFor({ timeout: 30000 });
        await dock.locator(".chatSteps").last().click();
        await dock.locator(".chatToolActionHead", { hasText: "Not allowed to run curl" }).waitFor();
        assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["decline"]), "Codex heard decline");

        const third = await send(dock, "one more try");
        await saysItWaits(dock);
        await dock.getByRole("button", { name: "Stop Codex", exact: true }).click();
        await until(() => !waiting().some((a) => a.id === third.id), { timeout: 30000, every: 250, what: "the stopped turn's ask leaves the computer" });
        await dock.locator(".computerEnded", { hasText: "Stopped" }).waitFor({ timeout: 30000 });
        assertEq(await dock.locator(".computerStrip.asking").count(), 0, "the strip no longer waits");
        const late = await answer(third, "once");
        assert([404, 409].includes(late.status), `an answer after Stop allows nothing: ${late.status} ${await late.text()}`);
        assertNoProblems(page);
      } finally { await ctx.close(); }
      const message = await lastMessage();
      assertEq(JSON.stringify([message.state, message.waiting]), JSON.stringify(["stopped", false]), "stored as stopped, waiting no more");
      assert(!decisions().slice(before).includes("accept"), `nothing was allowed: ${JSON.stringify(decisions().slice(before))}`);
      assertEq(follower.seen.refused, "", "the shell was never refused");
    });
  } finally {
    await follower?.stop();
    await computer.stop();
    await nas.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
