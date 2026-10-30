// A Zotero link (docs/dev/zotero_sync.md): a personal workspace kept in
// step with a Zotero library through the Web API, on the clone's surfaces
// (Settings → Workspaces → Clones and Zotero links, the header's sync pill
// and popover, the conflict chips). A second Gamma talks to the fake Zotero
// (backend/tests/zotero_fake.py over HTTP, serving the standard library of
// backend/tests/zotero_fixtures.py) for its API and website, and to a
// streaming address that refuses connections, so nothing reaches the
// internet. An account connects with a pasted API key (Settings → Account &
// sync → Zotero); links My Library into a new workspace from the Clones and
// Zotero links section, which opens that workspace, its pill showing the
// first sync from its first read; the link's row is a Zotero one, never a
// clone's, and syncs it; the row's Open opens the workspace: the pill waits
// on the library's linked file until Zotero trashes it, the paper has its
// PDF, its highlight, ink and text box, the collections and tags came as
// folders and labels. A highlight's note edited here reaches Zotero on Sync
// now, a colour changed in Zotero comes back, and a record field both sides
// changed is a conflict on the pill and on the page's record, resolved with
// Zotero's value. A group library linked "When opened" into the open
// workspace (once the dialog has asked again for the account it could not
// read) closes Settings on that workspace, gives it its pill at once, and
// shows its paper's PDF card until the page, opened, brings the PDF; a
// paper whose PDF is a linked file takes a PDF attached in Gamma, under the
// card that says why. Then the pill's settings detach the link and, once
// the account is connected again, reattach it, and Settings removes it: the
// row goes, the workspace stays.
import { spawn, execFileSync } from "node:child_process";
import net from "node:net";
import { Account, PYTHON, ROOT, Server, makePdf, wanted } from "../harness.mjs";
import { closeEditor, row as blockRow } from "./notes.mjs";
import { waitForPdf } from "./pdf.mjs";

const LIB = "users/123"; // the fake's account "tim"
const GROUP = "groups/9"; // its group "Lab"
const GOOD_KEY = "zkey-good";
const PAPER = "Attention is all you need";
const ZOTERO_HEADERS = { "Zotero-API-Key": GOOD_KEY, "Zotero-API-Version": "3" };

// The fake on a free port: its base URL once it says so, and how to stop it.
async function startFakeZotero() {
  const proc = spawn(PYTHON, ["-I", "backend/tests/zotero_fake.py", "--port", "0"], {
    cwd: ROOT, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });
  let out = "";
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`the fake Zotero did not start:\n${out}`)), 20000);
    const read = (chunk) => {
      out += chunk;
      const found = /ZOTERO (\S+)/.exec(out);
      if (found) { clearTimeout(timer); resolve(found[1]); }
    };
    proc.stdout.on("data", read);
    proc.stderr.on("data", read);
    proc.on("exit", (code) => { clearTimeout(timer); reject(new Error(`the fake Zotero exited (${code}):\n${out}`)); });
  });
  const stop = () => {
    if (proc.exitCode != null) return;
    if (process.platform === "win32") { try { execFileSync("taskkill", ["/pid", String(proc.pid), "/t", "/f"], { stdio: "ignore" }); } catch {} }
    else proc.kill("SIGTERM");
  };
  return { base, stop };
}

// A loopback port nothing listens on (taken and let go): Zotero's stream
// address for the second Gamma, which then finds the stream refused.
function closedPort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); });
    s.on("error", reject);
  });
}

export async function zoteroLinkScenarios(env) {
  const { server, browser, step, openPage, assert, assertEq, assertNoProblems, until, flags } = env;
  if (!wanted("zotero link")) return;
  const fake = await startFakeZotero();
  const zotero = new Server({ env: { GAMMA_ZOTERO_API_BASE: fake.base, GAMMA_ZOTERO_WEB_BASE: fake.base,
    GAMMA_ZOTERO_STREAM_URL: `ws://127.0.0.1:${await closedPort()}` } });
  let ctx = null;
  try {
    await zotero.start();
    await zotero.manage("create-user", "zed", "zed-pw");
    const zed = await new Account(zotero, "zed", "zed-pw").login();
    ctx = await zed.context(browser);
    // zed's API in workspace `ws` (a link's own)
    const inWs = (ws) => Object.assign(Object.create(zed), { ws });
    const shot = async (page, name) => { if (flags.keep) await page.screenshot({ path: `${server.dir}/${name}.png` }); };

    // Zotero's own answers, as another client reads and writes them.
    const fakeApi = async (path, { method = "GET", body, version } = {}) => {
      const headers = { ...ZOTERO_HEADERS, ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(version != null ? { "If-Unmodified-Since-Version": String(version) } : {}) };
      const r = await fetch(`${fake.base}/${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
      assert(r.status < 300, `the fake Zotero answers ${method} ${path} with ${r.status}: ${await r.clone().text()}`);
      return r.status === 204 ? null : r.json();
    };
    const item = (library, key) => fakeApi(`${library}/items/${key}`);
    // a change made in Zotero (its desktop, another device): PATCH at the item's version
    const zoteroEdit = async (library, key, fields) => {
      const { version } = await item(library, key);
      await fakeApi(`${library}/items/${key}`, { method: "PATCH", body: fields, version });
    };

    const settingsNav = (page, name) => page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name, exact: true });
    async function openSettings(page, pane) {
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await settingsNav(page, pane).click();
    }
    const button = (scope, name) => scope.getByRole("button", { name, exact: true });
    // the link dialog's Library menu
    const libraryField = (dlg) => dlg.locator(".setField", { has: dlg.page().locator(".setFieldLabel", { hasText: /^Library$/ }) }).getByRole("button");
    const linkRow = (page, name) => page.locator('.aiProvRow[data-link="zotero"]', { hasText: name });
    const pillOf = (page) => page.getByRole("button", { name: "Sync status", exact: true });
    const popoverOf = (page) => page.getByRole("dialog", { name: "Sync status", exact: true });
    const pillState = (page, state, what = `the pill reads ${state}`, timeout = 20000) =>
      until(() => pillOf(page).getAttribute("data-state").then((s) => s === state), { timeout, what });
    // Sync now in the pill's popover (open), and the round it runs settled
    async function syncFromPopover(page) {
      const pop = popoverOf(page);
      if (!(await pop.isVisible())) await pillOf(page).click();
      await pop.waitFor();
      const sync = button(pop, "Sync");
      await until(() => sync.isEnabled(), { timeout: 20000, what: "the popover's Sync free" });
      const done = page.waitForResponse((r) => r.request().method() === "POST" && /\/api\/zotero\/links\/[^/]+\/sync\b/.test(r.url()), { timeout: 30000 });
      await sync.click();
      assertEq((await done).status(), 200, "Sync now ran its round");
      await until(() => sync.isEnabled(), { timeout: 20000, what: "the round ended" });
    }

    let linkWs = "";
    let labWs = "";
    let labName = "";
    let paperId = "";
    let paperKey = "";
    let highlight = null;

    // Settings → Account & sync → Zotero: a key made at zotero.org, pasted.
    await step("zotero link: connect the Zotero account with an API key in Settings › Account & sync", async () => {
      const page = await openPage(ctx, zotero.base);
      try {
        await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
        await openSettings(page, "Account & sync");
        const row = page.locator('.setRow[data-setting="Zotero"]');
        await row.waitFor();
        await button(row, "Use an API key…").click();
        const keyDialog = page.getByRole("dialog", { name: "Connect Zotero with an API key", exact: true });
        await keyDialog.waitFor();
        await keyDialog.locator(".pwField input").fill(GOOD_KEY);
        await button(keyDialog, "Save").click();
        await keyDialog.waitFor({ state: "detached" });
        await row.locator(".uiTag").filter({ hasText: /^connected$/ }).waitFor();
        const hint = await row.locator(".settingDesc").innerText();
        assert(hint.startsWith("tim · Read"), `the row names the Zotero user and what the key may do (${hint})`);
        const account = await zed.api("/api/zotero/account");
        assertEq(`${account.connected}/${account.via}/${account.username}`, "true/key/tim", "the pasted key is kept");
        assert(!JSON.stringify(account).includes(GOOD_KEY), "the key itself never comes back");
        await shot(page, "zotero-link-account");
        assertNoProblems(page);
      } finally {
        await page.close();
      }
    });

    // Workspaces → Clones and Zotero links → Link a Zotero library…: My Library
    // into a new workspace, which Link opens: Settings closes, the tab moves
    // there and its pill reads the link at once, the first sync running or
    // done, never "Not synced yet". The row is a link's (Zotero in its line,
    // never "clone of"); its Sync runs a round inline and the status line
    // says when.
    await step("zotero link: link My Library into a new workspace from Settings › Workspaces, which opens it, and sync it", async () => {
      const page = await openPage(ctx, zotero.base);
      try {
        await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
        await openSettings(page, "Workspaces");
        await page.getByText("No clones or Zotero links yet.", { exact: true }).waitFor();
        await page.getByRole("button", { name: "Link a Zotero library…", exact: true }).click();
        const dlg = page.getByRole("dialog", { name: "Link a Zotero library", exact: true });
        await dlg.waitFor();
        const library = libraryField(dlg);
        await until(async () => (await library.innerText()).includes("My Library"), { what: "My Library offered first" });
        await dlg.getByText(/3 PDFs · .* in Zotero Storage · 1 linked file Gamma cannot download/).first().waitFor({ timeout: 15000 });
        await dlg.getByRole("textbox", { name: /^Name here/ }).fill("Zotero papers");
        await shot(page, "zotero-link-dialog");
        // every reading of the pill from the next page's first render on (its data-state, each change once)
        await page.addInitScript(() => {
          const seen = (window.__pillStates = []);
          const look = () => {
            const state = document.querySelector('button.mirrorPill[data-guide="sync.pill"]')?.getAttribute("data-state");
            if (state && seen[seen.length - 1] !== state) seen.push(state);
          };
          new MutationObserver(look).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-state"] });
        });
        // (the answer's body is not read: the page navigates away in the same task that reads it,
        // and Chromium drops the old page's bodies then)
        const tabWs = () => new URL(page.url()).searchParams.get("ws");
        const fromWs = tabWs();
        assert(fromWs, "the tab names its workspace");
        const created = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/zotero/links");
        await button(dlg, "Link").click();
        assertEq((await created).status(), 201, "the link is made");
        // Link opens the linked workspace: Settings closes and the tab moves there
        await until(() => Promise.resolve(Boolean(tabWs()) && tabWs() !== fromWs), { timeout: 15000, what: "the tab moves to another workspace" });
        linkWs = (await zed.api("/api/zotero/links")).links.find((l) => l.library === LIB)?.workspace_id;
        assert(linkWs && linkWs !== zed.defaultWs && linkWs !== fromWs, `the link made a workspace of its own (${linkWs})`);
        assertEq(tabWs(), linkWs, "the tab opens the linked workspace");
        await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
        assertEq(await page.getByRole("dialog", { name: "Settings", exact: true }).count(), 0, "Settings closed");
        // the workspace opens as a linked one: its pill's first read of the link shows the first
        // sync running, or done already
        await pillOf(page).waitFor({ timeout: 15000 });
        await until(() => pillOf(page).getAttribute("data-state").then((s) => ["busy", "waiting", "queued", "ok"].includes(s)),
          { timeout: 10000, what: "the pill shows the first sync" });
        const states = await page.evaluate(() => window.__pillStates);
        const read = states.slice(states.findIndex((s) => s !== "new")); // (before its first read the pill knows no link)
        assert(read.length && !read.includes("new"), `once it has read the link the pill never says "Not synced yet" (${states.join(" → ")})`);
        await openSettings(page, "Workspaces");
        const row = linkRow(page, "Zotero papers");
        await row.waitFor();
        await row.locator(".uiTag").filter({ hasText: /^open$/ }).waitFor();
        assertEq(await button(row, "Open").count(), 0, "the open workspace's row offers no Open");
        const text = await row.innerText();
        assert(text.includes("Zotero") && text.includes("My Library"), `the row says it is a Zotero link of My Library (${text})`);
        assert(!text.includes("clone of"), `a link's row is no clone's (${text})`);

        await button(row, "Sync").click();
        await until(() => row.locator(".aiProvDesc").last().textContent().then((t) => /up to date \d/.test(t)),
          { timeout: 30000, what: "the row reports the round" });
        const links = (await zed.api("/api/zotero/links")).links;
        assertEq(links.length, 1, "one link");
        const link = links[0];
        assertEq(`${link.kind}/${link.library}/${link.name}/${link.mode}`, `zotero/${LIB}/Zotero papers/two-way`, "the link as asked");
        assert(!link.status.last_error, `no sync error: ${link.status.last_error}`);
        assertEq(link.workspace_id, linkWs, "the link keeps the workspace Link opened");
        const session = await zed.api("/api/session");
        assertEq(session.workspaces.find((w) => w.id === linkWs)?.zotero_of, "My Library", "the session row names the library");
        await shot(page, "zotero-link-row");
        assertNoProblems(page);
      } finally {
        await page.close();
      }
    });

    // Open moves the tab to the linked workspace. The pill says what waits
    // (the library's linked file, which Gamma cannot download) until Zotero
    // trashes it; the paper came with its PDF and every kind of annotation,
    // the collections and tags with it.
    await step("zotero link: the linked workspace: pill, the paper's PDF and annotations, folders and labels", async () => {
      assert(linkWs, "the link step ran");
      // (from the account's own workspace: the tab last used the linked one)
      const page = await openPage(ctx, `${zotero.base}/?ws=${zed.defaultWs}`);
      try {
        await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
        await openSettings(page, "Workspaces");
        await button(linkRow(page, "Zotero papers"), "Open").click();
        await until(() => Promise.resolve(new URL(page.url()).searchParams.get("ws") === linkWs), { what: "the linked workspace is open" });
        await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
        for (const name of ["ML", "Reading"]) await page.locator(".fileList .folderRow", { hasText: name }).first().waitFor();
        await page.getByText(PAPER, { exact: true }).first().waitFor();

        // the linked file: the pill waits on it, amber, and says why
        await pillState(page, "waiting");
        assertEq(await pillOf(page).locator(".mirrorPillDot.warn").count(), 1, "an amber dot: something waits outside Gamma");
        await pillOf(page).click();
        const pop = popoverOf(page);
        await pop.getByText("1 PDF not in Zotero Storage", { exact: true }).waitFor();
        await pop.getByText(/1 is a linked file: Gamma cannot download them/).first().waitFor();
        await pop.locator(".mirrorPopHead").getByText("My Library", { exact: true }).waitFor();
        await pop.locator(".mirrorPopHead").getByText("Zotero · tim", { exact: true }).waitFor();
        await pop.getByText("Discussion threads stay in Gamma. Highlight notes travel as Zotero comments.", { exact: true }).waitFor();
        await pop.locator(".mirrorPopItemRow", { hasText: PAPER }).first().waitFor();
        await shot(page, "zotero-link-waiting");
        // trashed in Zotero, the file waits no more: Sync now, and the link is up to date
        const linked = (await fakeApi(`${LIB}/items?limit=100`)).find((i) => i.data.linkMode === "linked_file");
        assert(linked, "the fixture library has its linked file");
        await zoteroEdit(LIB, linked.key, { deleted: 1 });
        await syncFromPopover(page);
        await pillState(page, "ok");
        assertEq(await pillOf(page).locator(".mirrorPillDot.ok").count(), 1, "a green dot: up to date");
        await pop.getByText(/^Up to date · /).first().waitFor();
        // the cadence and a round after an edit, in the pill's settings: from here on
        // every round is the Sync the steps press
        await button(pop, "Sync settings").click();
        await pop.getByText("Sync settings", { exact: true }).waitFor();
        await button(pop, "Manual").click();
        await until(() => zed.api(`/api/zotero/links/${linkWs}`).then((l) => l.poll_s === 0), { what: "Manual is saved" });
        const onChange = pop.getByRole("checkbox", { name: "Sync after an edit", exact: true });
        assert(await onChange.isChecked(), "a round after an edit, by default");
        // (the switch follows the saved setting, once the PATCH has answered)
        await onChange.click();
        await until(() => zed.api(`/api/zotero/links/${linkWs}`).then((l) => l.on_change === false), { what: "no round after an edit" });
        await until(() => onChange.isChecked().then((on) => !on), { what: "the switch turned off" });
        await button(pop, "Back").click();
        await pillOf(page).click();
        await pop.waitFor({ state: "detached" });
        assertNoProblems(page);

        // the paper: its record, its PDF, a highlight, an ink group, text boxes, notes
        const pages = (await inWs(linkWs).api("/api/blocks/root/children")).children;
        const paper = pages.find((p) => p.content === PAPER);
        assert(paper, "the paper page came");
        paperId = paper.id;
        const props = (await inWs(linkWs).api(`/api/blocks/${paperId}`)).properties;
        assert(props.doc_id, "its PDF came along");
        assertEq(props.meta.venue, "Nature", "its record came along");
        paperKey = props.zotero_key.split("/").pop();
        assert(props.zotero_key === `http://zotero.org/${LIB}/items/${paperKey}`, props.zotero_key);
        const kids = (await inWs(linkWs).api(`/api/blocks/${paperId}/subtree`)).block.children;
        highlight = kids.find((c) => c.properties?.quote === "dominant sequence transduction models");
        assert(highlight && highlight.content === "great point" && highlight.properties.zotero_annotation, "the highlight and its comment");
        const ink = kids.find((c) => c.properties?.ink_url);
        assert(ink && ink.properties.ink_strokes === 2, "the ink annotation is an ink group of two strokes");
        assert(kids.some((c) => c.properties?.text_box && c.content === "A text box"), "the text annotation is a text box");
        assert(kids.some((c) => c.properties?.zotero_note && c.content === "# Key points"), "the child note is a block tree");
        // the collections as folders, the manual tags as labels (the automatic one stays in Zotero)
        const folders = (await inWs(linkWs).api("/api/blocks/folders/subtree")).block;
        const ml = folders.children.find((f) => f.content === "ML");
        const transformers = ml?.children.find((f) => f.content === "Transformers");
        assert(transformers?.children.some((f) => f.content === "Efficient"), "ML > Transformers > Efficient");
        assertEq(props.folders.join(), transformers.id, "the paper is filed in Transformers");
        const labels = (await inWs(linkWs).api("/api/blocks/labels/subtree")).block.children.map((l) => l.content).sort();
        assertEq(labels.join(", "), "textbook, transformers", "the manual tags became labels");

        await page.goto(`${zotero.base}/?page=${paperId}&ws=${linkWs}`);
        await waitForPdf(page);
        await page.waitForSelector(`[data-page="1"] [data-hl-id="${highlight.id}"]`, { timeout: 15000 });
        await until(async () => (await page.locator('[data-page="1"] .inkLayer path').count()) === 2, { timeout: 15000, what: "the ink group's two strokes drawn" });
        await page.locator('[data-page="1"] .textBox', { hasText: "A text box" }).waitFor();
        await blockRow(page, "great point").waitFor();
        await blockRow(page, "Key points").waitFor();
        const filing = page.locator('[data-guide="page.labels"]');
        await filing.locator(".categoryBadge.folderChip", { hasText: /Transformers/ }).waitFor();
        await filing.locator(".categoryBadge:not(.folderChip)", { hasText: /^transformers$/ }).waitFor();
        await pillState(page, "ok");
        await shot(page, "zotero-link-paper");
        assertNoProblems(page);
      } finally {
        await page.close();
      }
    });

    // A highlight's note is its Zotero annotation's comment: edited here,
    // the pill counts it unsent until Sync now sends it.
    await step("zotero link: a highlight's note edited in Gamma reaches Zotero on Sync now", async () => {
      assert(paperId && highlight, "the workspace step ran");
      const page = await openPage(ctx, `${zotero.base}/?page=${paperId}&ws=${linkWs}`);
      try {
        await waitForPdf(page);
        await blockRow(page, "great point").locator(".blockBody").click();
        await page.waitForSelector(".blockEditorCm .cm-content", { timeout: 5000 });
        await page.keyboard.press("End");
        await page.keyboard.type(", read twice");
        await closeEditor(page);
        await until(() => inWs(linkWs).api(`/api/blocks/${highlight.id}`).then((b) => b.content === "great point, read twice"),
          { what: "the edit saved" });
        await pillState(page, "pending", "the pill counts the edit unsent");
        await syncFromPopover(page);
        const annotation = await item(LIB, highlight.properties.zotero_annotation);
        assertEq(annotation.data.annotationComment, "great point, read twice", "Zotero's annotation carries the new comment");
        assertEq(annotation.data.annotationColor, "#ffd400", "and keeps its colour");
        await pillState(page, "ok");
        assertNoProblems(page);
      } finally {
        await page.close();
      }
    });

    // The colour, changed in Zotero's reader: Sync now brings it, and the
    // open page redraws the highlight.
    await step("zotero link: an annotation's colour changed in Zotero shows in Gamma on Sync now", async () => {
      assert(paperId && highlight, "the workspace step ran");
      const page = await openPage(ctx, `${zotero.base}/?page=${paperId}&ws=${linkWs}`);
      try {
        await waitForPdf(page);
        const mark = page.locator(`[data-page="1"] [data-hl-id="${highlight.id}"]`).first();
        await mark.waitFor({ timeout: 15000 });
        const colour = () => mark.evaluate((el) => el.style.getPropertyValue("--hl"));
        assert((await colour()).startsWith("#ffd400"), `yellow first (${await colour()})`);
        await zoteroEdit(LIB, highlight.properties.zotero_annotation, { annotationColor: "#ff6666" });
        await syncFromPopover(page);
        await until(async () => (await colour()).startsWith("#ff6666"), { what: "the highlight redrawn red" });
        assert((await inWs(linkWs).api(`/api/blocks/${highlight.id}`)).properties.color.startsWith("#ff6666"), "the block keeps Zotero's colour");
        assertNoProblems(page);
      } finally {
        await page.close();
      }
    });

    // The volume changed on both sides: Gamma's wins the round and Zotero's
    // waits as a conflict, on the pill (its count) and on the page's record
    // (a chip on its header: the record is the page itself, no row). Taking
    // Zotero's writes it here, and the next round sends it back.
    await step("zotero link: a record field changed on both sides is a conflict on the pill and the record, resolved with Zotero's", async () => {
      assert(paperId && paperKey, "the workspace step ran");
      const page = await openPage(ctx, `${zotero.base}/?page=${paperId}&ws=${linkWs}`);
      try {
        await waitForPdf(page);
        await zoteroEdit(LIB, paperKey, { volume: "649" });
        const { properties } = await inWs(linkWs).api(`/api/blocks/${paperId}`);
        await inWs(linkWs).api(`/api/blocks/${paperId}`, { method: "PUT", body: { properties: { meta: { ...properties.meta, volume: "648" } } } });
        await syncFromPopover(page);
        await pillState(page, "conflicts");
        assertEq((await pillOf(page).locator(".mirrorPillBadge").textContent()).trim(), "1", "the pill counts one conflict");
        await popoverOf(page).getByText("1 conflict", { exact: true }).waitFor();
        await pillOf(page).click();
        await popoverOf(page).waitFor({ state: "detached" });
        const chip = page.locator(".zoteroRecordChip .mergeChip");
        await chip.waitFor({ timeout: 15000 });
        await chip.click();
        const merge = page.getByRole("dialog", { name: "Merge", exact: true });
        await merge.waitFor();
        const gamma = merge.getByRole("region", { name: "Gamma (changed here)", exact: true });
        const theirs = merge.getByRole("region", { name: "Zotero (changed in Zotero)", exact: true });
        assertEq((await gamma.locator(".mergeText").innerText()).trim(), "Volume: 648", "Gamma's value");
        assertEq((await theirs.locator(".mergeText").innerText()).trim(), "Volume: 649", "Zotero's value");
        assertEq(await merge.locator(".mergeVersion").count(), 2, "two sides, no merged text");
        assert(await merge.getByRole("radio", { name: "Gamma", exact: true }).isChecked(), "Gamma's, in the page now, is preselected");
        await shot(page, "zotero-link-conflict");
        await merge.getByRole("radio", { name: "Zotero", exact: true }).check();
        await button(merge, "Apply").click();
        await chip.waitFor({ state: "detached", timeout: 15000 });
        await until(() => inWs(linkWs).api(`/api/blocks/${paperId}`).then((b) => b.properties.meta.volume === "649"),
          { what: "Zotero's volume in the page" });
        assertEq((await zed.api(`/api/zotero/links/${linkWs}`)).conflicts_open, 0, "no conflict left");
        await syncFromPopover(page);
        assertEq((await item(LIB, paperKey)).data.volume, "649", "Zotero keeps its volume");
        await pillState(page, "ok");
        assertNoProblems(page);
      } finally {
        await page.close();
      }
    });

    // A group library linked "When opened" into the workspace open in the
    // tab: its records come at once, a PDF when its page opens (the 50
    // newest items' at once: the 50 added first keep the paper's out of
    // them). The dialog, whose first read of the account failed, says so and
    // asks again; Link closes Settings on the open workspace, which gets its
    // pill without a reload. The page shows the PDF's card in place of the
    // attach prompt, and opening it asks for the PDF, which then comes.
    await step("zotero link: a \"When opened\" link into the open workspace: its pill at once, the PDF card on a paper whose PDF has not come, and opening it brings the PDF", async () => {
      await fakeApi(`${GROUP}/items`, { method: "POST", body: Array.from({ length: 50 }, (_, i) => ({
        itemType: "journalArticle", title: `Group paper ${i + 1}`, creators: [], tags: [], collections: [], relations: {} })) });
      labWs = zed.defaultWs;
      labName = (await zed.api("/api/workspaces/mine")).workspaces.find((w) => w.id === labWs).name;
      const page = await openPage(ctx, `${zotero.base}/?ws=${labWs}`);
      try {
        await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
        assertEq(await pillOf(page).count(), 0, "an ordinary workspace has no pill");
        // the server cannot be asked for the account just now (it restarts)
        let restarting = true;
        await page.route("**/api/zotero/account", (route) => (restarting && route.request().method() === "GET"
          ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Service restarting" }) })
          : route.continue()));
        await openSettings(page, "Workspaces");
        await page.getByRole("button", { name: "Link a Zotero library…", exact: true }).click();
        const dlg = page.getByRole("dialog", { name: "Link a Zotero library", exact: true });
        await dlg.waitFor();
        await dlg.getByRole("alert").filter({ hasText: "Could not ask this server for your Zotero account: Service restarting" }).waitFor();
        restarting = false;
        await button(dlg, "Try again").click();
        const library = libraryField(dlg);
        await until(async () => (await library.innerText()).includes("Lab"), { what: "Lab offered (My Library is linked)" });
        await page.unroute("**/api/zotero/account");
        // into the workspace open in this tab
        await dlg.locator(".setField", { has: page.locator(".setFieldLabel", { hasText: /^Into$/ }) }).getByRole("button").click();
        await page.locator(".uiSelectMenu").getByRole("menuitemradio", { name: labName, exact: true }).click();
        await button(dlg, "When opened").click();
        assertEq(await button(dlg, "When opened").getAttribute("aria-pressed"), "true", "When opened chosen");
        await page.evaluate(() => { window.__sameLoad = true; });
        await button(dlg, "Link").click();
        await dlg.waitFor({ state: "detached" });
        // the workspace open here, linked, stays open: Settings closes on it,
        // and its pill shows without a reload
        await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor({ state: "detached" });
        assertEq(new URL(page.url()).searchParams.get("ws"), labWs, "the tab stays on the workspace it linked");
        await pillOf(page).waitFor({ timeout: 15000 });
        // (it reads "new" until its first read of the link answers)
        await until(() => pillOf(page).getAttribute("data-state").then((s) => s && s !== "new" && s !== "detached"),
          { timeout: 30000, what: "the pill reads the live link" });
        assert(await page.evaluate(() => window.__sameLoad === true), "no reload");
        await openSettings(page, "Workspaces");
        const row = linkRow(page, labName);
        await row.waitFor();
        await row.getByText(/Zotero · Lab · /).first().waitFor();
        await button(row, "Sync").click();
        // (the workspace's own pages, just touched, wait for quiet: "last round … queued")
        await until(() => row.locator(".aiProvDesc").last().textContent().then((t) => /(up to date|last round) \d/.test(t)),
          { timeout: 30000, what: "the group's row reports the round" });
        const lab = (await zed.api("/api/zotero/links")).links.find((l) => l.library === GROUP);
        assertEq(`${lab.download}/${lab.workspace_id}`, `open/${labWs}`, "the group's link, into the open workspace, its PDFs when opened");
        await button(page, "Close settings").click();
        assertNoProblems(page, [/GET \/api\/zotero\/account -> 503/]);
        const reading = (await inWs(labWs).api("/api/blocks/root/children")).children.find((p) => p.content === "Group reading");
        assert(reading, "the group's paper came");
        const before = (await inWs(labWs).api(`/api/blocks/${reading.id}`)).properties;
        assert(!before.doc_id && before.zotero_pdf?.state === "remote", `its PDF waits in Zotero (${JSON.stringify(before.zotero_pdf)})`);
        assertNoProblems(page);

        // the page opened asks for its PDF once; held here until the card has been seen
        const asked = [];
        await page.route("**/api/zotero/links/*/fetch", (route) => { asked.push(route); });
        await page.goto(`${zotero.base}/?page=${reading.id}&ws=${labWs}`);
        await page.getByText("Group reading", { exact: true }).first().waitFor({ timeout: 15000 });
        await until(() => Promise.resolve(asked.length === 1), { what: "opening the page asks for its PDF" });
        const attach = button(page, "PDF in Zotero");
        await attach.click();
        const card = page.locator(".zoteroPdfCard");
        await card.waitFor();
        await card.getByText(/^PDF in Zotero · \d+ KB$/).waitFor();
        await card.getByText("group-reading.pdf", { exact: true }).waitFor();
        await button(card, "Download now").waitFor();
        await shot(page, "zotero-link-pdf-card");
        await page.keyboard.press("Escape");
        await card.waitFor({ state: "detached" });
        await asked[0].continue();
        await page.unroute("**/api/zotero/links/*/fetch");
        // the PDF comes over the page's socket, with the group's highlights
        await waitForPdf(page);
        await until(async () => (await page.locator('[data-page="1"] [data-hl-id]').count()) >= 2, { timeout: 15000, what: "the group's highlights drawn" });
        assertEq(asked.length, 1, "asked once");
        const after = (await inWs(labWs).api(`/api/blocks/${reading.id}`)).properties;
        assert(after.doc_id && !after.zotero_pdf, "the page holds its PDF now");
        // another member's annotation is read-only in Zotero: a badge on its row
        await page.getByRole("img", { name: "carol's annotation: read-only in Zotero", exact: true }).waitFor();
        assertNoProblems(page);
      } finally {
        await page.close();
      }
    });

    // A paper whose only PDF in Zotero is a linked file (ZotFile, WebDAV
    // alike): Gamma can never download it, so the card says why above the
    // ordinary attach prompt, and a copy of the person's own attaches. It
    // stays in Gamma: Zotero gets no second attachment.
    await step("zotero link: a paper whose PDF is a linked file takes a PDF attached in Gamma, under the card that says why", async () => {
      assert(labWs, "the group step ran");
      const made = await fakeApi(`${GROUP}/items`, { method: "POST", body: [{
        itemType: "journalArticle", title: "Linked paper", creators: [], tags: [], collections: [], relations: {} }] });
      const parentKey = made.success["0"];
      await fakeApi(`${GROUP}/items`, { method: "POST", body: [{ itemType: "attachment", parentItem: parentKey, linkMode: "linked_file",
        title: "linked-paper.pdf", contentType: "application/pdf", path: "/Users/tim/Papers/linked-paper.pdf", tags: [], relations: {} }] });
      const page = await openPage(ctx, `${zotero.base}/?ws=${labWs}`);
      try {
        await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
        await syncFromPopover(page);
        await pillOf(page).click();
        await popoverOf(page).waitFor({ state: "detached" });
        const paper = await until(async () => (await inWs(labWs).api("/api/blocks/root/children")).children.find((p) => p.content === "Linked paper"),
          { what: "the paper came" });
        const mark = (await inWs(labWs).api(`/api/blocks/${paper.id}`)).properties.zotero_pdf;
        assertEq(`${mark?.state}/${mark?.reason}`, "unavailable/linked", "its PDF cannot come");
        await page.goto(`${zotero.base}/?page=${paper.id}&ws=${labWs}`);
        await page.getByText("Linked paper", { exact: true }).first().waitFor({ timeout: 15000 });
        assertEq(await button(page, "PDF in Zotero").count(), 0, "the paperclip stays a paperclip");
        await button(page, "Attach document").click();
        const pop = page.locator(".attachPopover");
        const card = pop.locator(".zoteroPdfCard");
        await card.getByText("It cannot come here: a linked file in Zotero.", { exact: true }).waitFor();
        await card.getByText("Attach your own copy below: it stays in Gamma and is not sent to Zotero.", { exact: true }).waitFor();
        assertEq(await button(card, "Download now").count(), 0, "nothing to download");
        await pop.getByText("Attach a document", { exact: true }).waitFor();
        const chooser = page.waitForEvent("filechooser");
        await pop.locator(".ctxMenuItem", { hasText: "Upload a PDF…" }).click();
        await (await chooser).setFiles({ name: "my-linked-paper.pdf", mimeType: "application/pdf",
          buffer: Buffer.from(makePdf([["My own copy of the linked paper"]])) });
        await waitForPdf(page);
        const props = (await inWs(labWs).api(`/api/blocks/${paper.id}`)).properties;
        assert(props.doc_id, "the page holds the PDF attached here");
        // a round later Zotero still has only its linked file: the copy stays in Gamma
        await syncFromPopover(page);
        const children = (await fakeApi(`${GROUP}/items/${parentKey}/children`)).map((c) => c.data.linkMode);
        assertEq(children.join(), "linked_file", "Zotero gets no second attachment");
        assertNoProblems(page);
      } finally {
        await page.close();
      }
    });

    // The pill's settings detach the link (nothing moves) and reattach it;
    // with the Zotero account disconnected meanwhile, Reattach is refused and
    // says why, with the way to connect again. Each detach or reattach reads
    // the session again, so the pages' PDF cards follow, and the pill that
    // detached the link stays, for its Reattach.
    await step("zotero link: detach and reattach from the pill's sync settings", async () => {
      assert(linkWs, "the link step ran");
      const page = await openPage(ctx, `${zotero.base}/?ws=${linkWs}`);
      try {
        await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
        await pillState(page, "ok");
        await pillOf(page).click();
        const pop = popoverOf(page);
        await button(pop, "Sync settings").click();
        const sessionRead = () => page.waitForResponse((r) => r.request().method() === "GET"
          && new URL(r.url()).pathname === "/api/session", { timeout: 15000 });
        const rowOf = async (read) => (await (await read).json()).workspaces.find((w) => w.id === linkWs) || {};
        let read = sessionRead();
        await button(pop, "Detach").click();
        await pillState(page, "detached");
        assertEq((await zed.api(`/api/zotero/links/${linkWs}`)).mode, "off", "detached: the link is off");
        const detachedRow = await rowOf(read);
        assertEq(`${detachedRow.zotero_detached}/${detachedRow.zotero_of}`, "true/", "the session read again says detached");
        assert(await button(pop, "Reattach").isVisible(), "Reattach offered");
        // no account connected: the refusal shows, with Reconnect
        await zed.api("/api/zotero/account", { method: "DELETE" });
        await button(pop, "Reattach").click();
        await pop.getByRole("alert").filter({ hasText: /^Could not do that: / }).waitFor();
        await button(pop, "Reconnect").waitFor();
        await pillState(page, "detached", "still detached");
        await zed.api("/api/zotero/account", { method: "PUT", body: { api_key: GOOD_KEY } });
        read = sessionRead();
        await button(pop, "Reattach").click();
        const liveRow = await rowOf(read);
        assertEq(`${liveRow.zotero_detached}/${liveRow.zotero_of}`, "false/My Library", "the session read again names the library");
        await pillState(page, "ok", "reattached and synced");
        assertEq(await pop.getByRole("alert").count(), 0, "the refusal is gone");
        const link = await zed.api(`/api/zotero/links/${linkWs}`);
        assertEq(`${link.mode}/${link.detached}`, "two-way/false", "two-way again");
        assertNoProblems(page, [/POST \/api\/zotero\/links\/[^/ ]+\/relink -> 409/]);
      } finally {
        await page.close();
      }
    });

    // Remove link (the row's More menu, then the confirm): the link goes,
    // the workspace stays an ordinary one, without its pill.
    await step("zotero link: remove the link in Settings: the row goes, the workspace stays", async () => {
      assert(linkWs, "the link step ran");
      const page = await openPage(ctx, `${zotero.base}/?ws=${linkWs}`);
      try {
        await page.waitForSelector(".folderNewBtn", { timeout: 15000 });
        await pillOf(page).waitFor();
        await openSettings(page, "Workspaces");
        const row = linkRow(page, "Zotero papers");
        await row.waitFor();
        await button(row, "More").click();
        await page.getByRole("menuitem", { name: "Remove link", exact: true }).click();
        await button(page, "Remove link").click(); // the confirm
        await row.waitFor({ state: "detached" });
        await linkRow(page, labName).waitFor();
        const links = (await zed.api("/api/zotero/links")).links;
        assertEq(links.map((l) => l.library).join(), GROUP, "only the group's link is left");
        assert((await zed.api("/api/workspaces/mine")).workspaces.some((w) => w.id === linkWs), "the workspace stays");
        assertEq((await zed.api("/api/session")).workspaces.find((w) => w.id === linkWs)?.zotero_of, "", "and is linked no more");
        await until(() => pillOf(page).count().then((n) => n === 0), { what: "its pill goes" });
        assertNoProblems(page);
      } finally {
        await page.close();
      }
    });
  } finally {
    if (ctx) await ctx.close().catch(() => {});
    await zotero.stop();
    fake.stop();
  }
}
