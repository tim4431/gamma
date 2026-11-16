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
// makes a work folder of the NAS's folder "Paper", shows the computer there,
// turns Codex on for the computer, and with the shell's word lets it ask: a
// turn of the NAS asks on this computer. The shell is that account with the word (`ShellAccount`), and it
// follows what waits on this computer from the setup to the end, as the
// desktop app's main does (a held read of the asks, again and again), so the
// computer hears its shell throughout. Alice's plain browser on the NAS then
// sends a message: her page says the turn waits for her answer on the
// computer and draws no card, the NAS holds a `waiting` flag and never the
// command; the computer's own server gives the card's words to the shell's
// word alone, and only that answers: allowed once, the reply comes back;
// declined, Codex goes on without it; Stop on the NAS page takes a waiting
// ask off the computer. Settings → AI in the computer's desktop window lists
// the NAS among the servers this computer answers (step 4.1), its asks off,
// each control an ask of the shell. Then (step 4.3 of
// docs/research/agents-as-participants-plan.md, its Done-when) the computer's
// link lets the NAS answer what its agents ask, with the shell's word alone:
// on a phone, on a library PDF of the NAS in no synced folder, alice picks
// Codex on the computer and asks it to rebuild the PDF; it asks to run the
// build, the card is drawn on the phone (allowed once at most) while the
// computer's bar lists it too, and the phone's Allow once is the answer the
// turn goes on with.
const FAKES = path.join(ROOT, "backend", "tests", "fake_agents");
const PAPER = path.join(ROOT, "backend", "tests", "samples", "synctex", "paper");
const FETCH = "curl -L https://example.org/refs.bib -o refs.bib";
const REASON = "Fetch the bibliography the paper cites.";
const HOST = "example.org";
const REBUILD = "rebuild the PDF";
const BUILD = "latexmk -pdf main.tex";
const BUILD_REASON = "Rebuild the PDF after the edit.";
// The desktop window's user agent: the page is the desktop app's (IS_DESKTOP).
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Gamma/1.0.0 "
  + "Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36";

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
  const { browser, step, openPage, assert, assertEq, assertNoProblems, until, makePdf, sleep } = env;
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
    // ("rebuild the PDF" asks to run the build instead)
    FAKE_AGENT: JSON.stringify({ ask: [{ command: FETCH, reason: REASON, host: HOST }], log: agentLog,
      turns: { [REBUILD]: { ask: [{ command: BUILD, reason: BUILD_REASON }] } } }),
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

    let pdf = "", name = "", linkId = "", channelId = "";
    const at = () => `${nas.base}/?ws=${wsId}&page=${pdf}`;
    const conversation = async () => (await alice.api(`/api/local-agents/chat?bucket=${pdf}`)).conversation;
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
    // The chat's Codex side, picked in the composer's chip unless it shows
    // already (no AI service: the agent is the chat's own).
    const codexSide = async (page) => {
      const dock = await openChat(page);
      await dock.locator(".chatModelChip .uiSelectBtn").waitFor();
      if (!(await dock.locator(".computerStrip").count())) {
        await dock.locator(".chatModelChip .uiSelectBtn").click();
        await page.getByRole("menuitemradio", { name: /^Codex on / }).click();
      }
      await dock.locator(".computerStrip").waitFor();
      return dock;
    };
    const stripText = async (dock) => (await dock.locator(".computerStrip").innerText().catch(() => "")).trim();
    // The strip's line alone, without where the agent works after it (a page in no synced folder).
    const stripLine = async (dock) => (await dock.locator(".computerStrip .chatHealthText").innerText().catch(() => "")).trim();
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
      assertEq(await dock.locator(".chatAsk").count(), 0, "no card on the NAS's page");
    };

    await step("remote-asks: letting Codex ask is set only with the shell's word", async () => {
      const token = await alice.api("/api/integrations/tokens", { method: "POST",
        body: { name: "Gamma desktop: Paper synced folder", scope: "write", expires_in_days: 365 } });
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
      channelId = made.link.id;
      await until(async () => (await shell.api("/api/computer-links")).links.find((l) => l.id === made.link.id)?.status?.state === "connected",
        { timeout: 45000, every: 500, what: "the channel connected" });
      assertEq((await shell.api("/api/local-agents/codex", { method: "PATCH", body: { enabled: true } })).enabled, true,
        "Codex on for the computer");

      const asks = (account) => account.api("/api/local-agents/codex", { method: "PATCH", raw: true, body: { runs: "ask" } });
      const plainly = await asks(owner);
      assertEq(plainly.status, 403, `a session alone may not: ${await plainly.text()}`);
      const set = await asks(shell);
      const body = await set.json();
      assertEq(set.status, 200, `set with the shell's word: ${JSON.stringify(body)}`);
      assertEq(body.runs, "ask", "Codex may ask: a turn of the NAS's folder asks on this computer");
      assertEq((await shell.api("/api/local-agents")).agents.find((a) => a.name === "codex")?.runs, "ask",
        "and the listing says so");

      const [seen] = await until(async () => {
        const list = (await alice.api(`/api/pages/${pdf}/computer`)).computers;
        return list.length && list[0].online && list[0].vault?.work && list[0].assistants.some((a) => a.name === "codex" && a.enabled)
          ? list : null;
      }, { timeout: 45000, every: 500, what: "the page's computer, online, keeping its work folder with Codex on" });
      assertEq(seen.asks, false, "the NAS may not answer what the computer's agents ask yet");
      name = seen.name;
      await until(async () => (await alice.api(`/api/local-agents/chat?bucket=${pdf}`)).computer?.ready === true,
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
        await dock.locator(".chatBubble.ai", { hasText: "(stopped)" }).waitFor({ timeout: 30000 });
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

    // Step 4.1: Settings → AI → Agents in the desktop window
    // (an Electron user agent) on the computer's own server. The shell is not
    // there to answer, so each control is seen to ask it and change nothing.
    await step("remote-asks: Settings → AI → Agents in the desktop window lists the NAS among the servers this computer answers, its asks off, and each control asks the shell", async () => {
      const ctx = await owner.context(browser, { userAgent: DESKTOP_UA });
      try {
        await ctx.addInitScript(() => {
          localStorage.setItem("gamma-ai-login-check", "off");
          window.shellAsks = [];
          window.addEventListener("message", (e) => {
            if (e.data?.source === "gamma-app" && e.data.type !== "connector-hello") window.shellAsks.push(e.data);
          });
        });
        const page = await openPage(ctx, `${computer.base}/?settings=agents`);
        const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
        await dialog.waitFor();
        const section = dialog.locator('.setSection[data-setting="Servers this computer answers"]');
        await section.waitFor();
        const server = new URL(nas.base).hostname;
        const row = dialog.locator(`.setRow[data-setting="${server}: Optics lab"]`);
        await row.waitFor();
        const hint = (await row.locator(".settingDesc").innerText()).trim();
        assertEq(hint, `${nas.base} · as ra-alice · Connected`, "its address, the account there and its channel");
        const asksLabel = `${server} may answer what the agents ask`;
        const asks = dialog.getByRole("checkbox", { name: asksLabel, exact: true });
        assertEq(await asks.isChecked(), false, "off to begin with");
        assertEq((await dialog.locator(`.setRow[data-setting="${asksLabel}"] .settingDesc`).innerText()).trim(),
          `On: the agents' asks may also be allowed from ${server}`, "and what on would do");
        const shellAsked = () => page.evaluate(() => window.shellAsks
          .filter((m) => ["link-asks", "add-server", "remove-server"].includes(m.type))
          .map(({ type, link, asks }) => ({ type, link, asks })));
        await asks.click();
        await until(async () => (await shellAsked()).some((m) => m.type === "link-asks"), { what: "the switch asks the shell" });
        await dialog.getByRole("button", { name: "Add a server…", exact: true }).click();
        await row.getByRole("button", { name: `Stop answering ${server}`, exact: true }).click();
        await until(async () => (await shellAsked()).length === 3, { what: `three asks of the shell: ${JSON.stringify(await shellAsked())}` });
        assertEq(JSON.stringify(await shellAsked()), JSON.stringify([
          { type: "link-asks", link: channelId, asks: true }, { type: "add-server", link: "new" },
          { type: "remove-server", link: channelId }]), "the shell is asked; the page changes nothing itself");
        assertEq(await asks.isChecked(), false, "the switch stays as the link is until the shell acts");
        const kept = (await shell.api("/api/computer-links")).links.find((l) => l.id === channelId);
        assertEq(JSON.stringify([kept?.asks, kept?.status?.state]), JSON.stringify([false, "connected"]), "the link as it was");
        assertNoProblems(page);
      } finally { await ctx.close(); }
    });

    await step("remote-asks: the link lets the NAS answer; on a phone, a library PDF in no synced folder, Codex on the computer asks to run the build and the card is answered on the phone", async () => {
      const relay = (account) => account.api(`/api/computer-links/${channelId}`, { method: "PATCH", raw: true, body: { asks: true } });
      const plainly = await relay(owner);
      assertEq(plainly.status, 403, `a page's session alone may not let the NAS answer: ${await plainly.text()}`);
      const set = await relay(shell);
      assertEq(set.status, 200, `with the shell's word: ${await set.text()}`);
      await until(async () => (await alice.api(`/api/pages/${pdf}/computer`)).computers[0]?.asks === true,
        { timeout: 45000, every: 500, what: "the NAS hears the computer lets it answer" });
      const up = await alice.upload("/api/uploads", makePdf([["Notes on the build", "of the paper"]]), "build-notes.pdf", "application/pdf");
      const notes = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST",
        body: { default_title: "Build notes", source_url: up.source_url, original_filename: "build-notes.pdf" } });
      assertEq(JSON.stringify((await alice.api(`/api/pages/${notes.id}/computer`)).computers.map((c) => [c.name, c.vault ?? null, c.asks])),
        JSON.stringify([[name, null, true]]), "the computer's agents are the page's, though it keeps none of it");
      const lastThere = async () => (await alice.api(`/api/local-agents/chat?bucket=${notes.id}`)).conversation?.messages.at(-1);
      const before = decisions().length;
      const ctx = await alice.context(browser, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
      const page = await openPage(ctx, `${nas.base}/?ws=${wsId}&page=${notes.id}`);
      let onComputer = null;
      try {
        await waitForPdf(page, 1);
        await page.getByRole("button", { name: "AI chat", exact: true }).click();
        const dock = page.locator(".phonePanel:not(.phonePanelHidden) .chatPanel");
        await dock.locator(".chatModelChip .uiSelectBtn").waitFor();
        if (!(await dock.locator(".computerStrip").count())) {
          await dock.locator(".chatModelChip .uiSelectBtn").click();
          await page.getByRole("menuitemradio", { name: /^Codex on / }).click();
        }
        await until(async () => (await stripLine(dock)) === `Codex on ${name}`, { what: `the strip names the computer: ${await stripText(dock)}` });
        const asked = new Set(waiting().map((a) => a.id));
        await dock.getByRole("textbox", { name: "Message Codex", exact: true }).fill(REBUILD);
        await dock.getByRole("button", { name: "Send", exact: true }).click();
        // the card on the phone: the build, allowed once at most
        const card = dock.locator(".chatAsk");
        await card.waitFor({ timeout: 30000 });
        await card.getByText("Codex wants to run a command").waitFor();
        await card.locator(".chatAskCommand", { hasText: BUILD }).waitFor();
        assertEq(await card.getByRole("button", { name: "Allow in this chat", exact: true }).count(), 0,
          "no grant for a turn on another computer");
        await until(async () => (await stripLine(dock)) === "Waiting for you: Codex wants to run a command",
          { what: `the strip waits for alice: ${await stripText(dock)}` });
        // the computer's bar lists it too, and the NAS's message holds it, relayed
        onComputer = await until(() => waiting().find((a) => !asked.has(a.id) && a.command === BUILD),
          { timeout: 20000, every: 250, what: "the same ask on the computer's bar" });
        const message = await lastThere();
        assertEq(JSON.stringify([message.state, message.waiting, message.asks.map((a) => [a.id, a.command, a.once])]),
          JSON.stringify(["running", false, [[onComputer.id, BUILD, true]]]), "the NAS's message holds the ask");
        await card.getByRole("button", { name: "Allow once", exact: true }).tap();
        await dock.locator(".chatBubble.ai", { hasText: "Ran it." }).waitFor({ timeout: 30000 });
        await until(() => !waiting().some((a) => a.id === onComputer.id), { timeout: 25000, every: 250, what: "the bar's card closes" });
        assertEq(await dock.locator(".chatAsk").count(), 0, "the phone's card closes");
        assertNoProblems(page);
      } finally { await ctx.close(); }
      assertEq(JSON.stringify(decisions().slice(before)), JSON.stringify(["accept"]), "Codex heard the phone's accept");
      assertEq((await lastThere()).state, "done", "the turn went on to its answer");
      const late = await answer(onComputer, "deny");
      assert([404, 409].includes(late.status), `answered on the phone first: the bar's answer is refused: ${late.status}`);
    });
  } finally {
    await follower?.stop();
    await computer.stop();
    await nas.stop();
    fs.rmSync(disk, { recursive: true, force: true });
  }
}
