// What a tab closed right after a change keeps: a note typed, a stroke
// drawn or a setting changed, then the tab closed before the save that
// change waits for (docs/dev/collab.md "Keepalive", handwriting.md
// "Fences"). A scratch scenario of its own, outside the suite's groups: it
// counts outcomes over runs, closes and engines, one engine at a time
// (docs/research/keepalive-abort.md "The app at a close"), and
// `context.close()` keeps no change in Chromium's shell or Firefox.
//
//   node tests/e2e/tabClose.mjs                        # each case once, closed by page.close()
//   node tests/e2e/tabClose.mjs --runs 5 --close page,context
//   node tests/e2e/tabClose.mjs --stop pagehide        # no pagehide listener of the app runs
//   GAMMA_E2E_BROWSER=firefox node tests/e2e/tabClose.mjs --runs 5
//
// `page.close()` closes the tab alone and runs its pagehide listeners
// (Firefox stops the first one). `context.close()` closes the browser
// profile with it: Chromium's headless shell runs no listener there, and
// Firefox sends the page's last requests without the session cookie. Each
// step asserts that nothing is lost: the server has the change, or, for a
// stroke closed by page.close(), the draft journal brings it back on the
// next visit in the same profile and it is saved then. The table at the
// end counts each case's outcomes and what the journal held after each
// page.close(). `--stop pagehide` keeps every pagehide listener the app adds
// from running, as Firefox stops a closing page's first one: what the close
// keeps then went from visibilitychange alone. Exit 1 on any loss.
import { Account, Server, flags, launchBrowser, makePdf, openPage, results, sleep, step, until } from "./harness.mjs";
import { editRow } from "./scenarios/notes.mjs";
import { waitForPdf } from "./scenarios/pdf.mjs";

const argValue = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i > 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith("--") ? process.argv[i + 1] : fallback;
};
const runs = Math.max(1, Number(argValue("--runs", "1")) || 1);
const closes = argValue("--close", "page").split(",").map((s) => s.trim()).filter(Boolean);
if (!closes.length || closes.some((how) => !["page", "context"].includes(how))) {
  throw new Error(`--close takes page and/or context, got ${closes.join(",")}`);
}
const stop = argValue("--stop", "");
if (stop && stop !== "pagehide") throw new Error(`--stop takes pagehide, got ${stop}`);
flags.continueOnFail = true;   // every run of every case, then the table

const server = new Server();
let browser;
const tallies = new Map();   // "case | close" → {outcome: count}
const tally = (key, what) => {
  const t = tallies.get(key) || {};
  t[what] = (t[what] || 0) + 1;
  tallies.set(key, t);
};

// The API writes the server took since `mark` (an offset into its log), as
// "METHOD /api/path status": what the closing page got through.
const writesSince = (mark) => server.log().slice(mark).split("\n")
  .map((line) => line.match(/"(POST|PUT|PATCH|DELETE) (\/api\/[^ ?"]*)[^"]*" (\d{3})/))
  .filter((m) => m && !m[2].startsWith("/api/ai/"))
  .map((m) => `${m[1]} ${m[2].replace(/^\/api\/(pages|blocks)\/[^/]+/, "/api/$1/:id")} ${m[3]}`);
const said = (writes) => (writes.length ? writes.join(", ") : "nothing");

// Close the tab (`page`) or the profile (`context`) at once: no last look,
// which would take a screenshot first.
const shut = (how, page, ctx) => (how === "page" ? page.close({ lastLook: false }) : ctx.close({ lastLook: false }));

try {
  await server.start();
  await server.manage("create-user", "closer", "closer-pw");
  const user = await new Account(server, "closer", "closer-pw").login();
  browser = await launchBrowser();
  console.log(`${browser.browserType().name()} ${browser.version()}: ${runs} run(s) of each case, closed by ${closes.map((how) => `${how}.close()`).join(" and ")}${stop ? ", no pagehide listener running" : ""}`);
  // A fresh profile, signed in; with --stop pagehide, one where the app's
  // pagehide listeners are never added.
  const context = async () => {
    const ctx = await user.context(browser);
    if (stop) {
      await ctx.addInitScript(() => {
        const add = EventTarget.prototype.addEventListener;
        EventTarget.prototype.addEventListener = function (type, ...rest) {
          if (type === "pagehide" && this === window) return undefined;
          return add.call(this, type, ...rest);
        };
      });
    }
    return ctx;
  };

  // The strokes the paper's saved files hold (a block naming a file the
  // server lacks holds none).
  const savedStrokes = async (paper) => {
    let n = 0;
    for (const block of (await user.api(`/api/blocks/${paper.id}/subtree`)).block.children || []) {
      if (!block.properties?.ink_url?.endsWith(".ink")) continue;
      n += (await user.api(block.properties.ink_url).catch(() => null))?.strokes?.length || 0;
    }
    return n;
  };
  const strokesReach = (paper, n, timeout) => until(async () => (await savedStrokes(paper)) >= n, { timeout, what: `${n} stroke(s) saved` })
    .then(() => true, () => false);
  // The draft journal's entries, as their stroke counts, read on a page of
  // the origin that runs no app, so no restore takes them first.
  const journal = async (ctx) => {
    const bare = await ctx.newPage();
    try {
      await bare.goto(`${server.base}/api/health`);
      return await bare.evaluate(() => new Promise((resolve) => {
        const req = indexedDB.open("gamma-ink-journal", 1);
        req.onupgradeneeded = () => { try { req.result.createObjectStore("drafts"); } catch { /* there already */ } };
        req.onerror = () => resolve([]);
        req.onsuccess = () => {
          const db = req.result;
          const all = db.transaction("drafts", "readonly").objectStore("drafts").getAll();
          all.onsuccess = () => { db.close(); resolve(all.result.map((e) => JSON.parse(e.bytes).strokes.length)); };
          all.onerror = () => { db.close(); resolve([]); };
        };
      }));
    } finally { await bare.close(); }
  };
  const draw = async (page, y) => {
    const box = await page.locator('[data-page="1"]').boundingBox();
    await page.mouse.move(box.x + 120, box.y + y);
    await page.mouse.down();
    await page.mouse.move(box.x + 260, box.y + y + 30, { steps: 12 });
    await page.mouse.up();
  };

  // A paper of its own, open in a fresh profile with the pen out and the
  // page settled, holding `before` saved strokes (0 or 1). A stroke is
  // drawn and the tab closed `wait` ms after the pen lifts: saved by the
  // close, or (page.close) brought back by the draft journal on the next
  // visit and saved then. → {outcome, note, journal}; throws on a loss.
  async function strokeThenClose(how, title, { before = 0, wait = 0 } = {}) {
    const up = await user.upload("/api/uploads", makePdf([[title, "a page to write on"]]), "close.pdf", "application/pdf");
    const created = await user.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: title, source_url: up.source_url } });
    const paper = { id: created.id, url: `${server.base}/?page=${created.id}&ws=${user.ws}` };
    const ctx = await context();
    try {
      const page = await openPage(ctx, paper.url);
      await waitForPdf(page, 1);
      await page.click("button[aria-label='Handwriting tools']");
      await page.waitForSelector(".pdfInkBar .inkToolBtn.modeActive .inkToolInk");
      await sleep(500);
      if (before) {
        await draw(page, 150);
        await until(async () => (await savedStrokes(paper)) >= before, { timeout: 10000, what: "the first stroke saved" });
        await sleep(1000);   // its answer read: the draft is clean again
      }
      const mark = server.log().length;
      await draw(page, 300);
      if (wait) await sleep(wait);
      await shut(how, page, ctx);
      const saved = await strokesReach(paper, before + 1, 3000);
      const writes = said(writesSince(mark));
      if (how === "context") {
        if (!saved) throw new Error(`lost: the server got ${writes}`);
        return { outcome: "saved by the close", note: `the server got ${writes}` };
      }
      const held = await journal(ctx);
      const kept = held.includes(before + 1) ? "held the stroke" : "did not hold it";
      const note = `the server got ${writes}; the journal held ${JSON.stringify(held)}`;
      if (saved) return { outcome: "saved by the close", note, journal: kept };
      // the next visit, in the same browser profile
      const next = await openPage(ctx, paper.url);
      await waitForPdf(next, 1);
      if (await strokesReach(paper, before + 1, 10000)) return { outcome: "brought back by the journal", note, journal: kept };
      const lost = new Error(`lost: ${note}; the next visit restored nothing`);
      lost.journal = kept;
      throw lost;
    } finally { await ctx.close(); }
  }

  const CASES = [
    {
      name: "a note typed just before the close reaches the server",
      run: async (how, n) => {
        const pg = await user.api("/api/pages", { method: "POST", body: { title: `Typed then closed ${how} ${n}` } });
        const text = `typed then closed ${how} ${n}`;
        await user.api("/api/blocks", { method: "POST", body: { parent_id: pg.id, content: text } });
        const ctx = await context();
        try {
          const page = await openPage(ctx, `${server.base}/?page=${pg.id}&ws=${user.ws}`);
          await editRow(page, text);
          const mark = server.log().length;
          await page.keyboard.type(" and kept");
          await shut(how, page, ctx);
          const kept = await until(async () => JSON.stringify((await user.api(`/api/blocks/${pg.id}/subtree`)).block).includes(`${text} and kept`),
            { timeout: 3000, what: "the typing on the server" }).then(() => true, () => false);
          if (!kept) throw new Error(`lost: the server got ${said(writesSince(mark))}`);
          return { outcome: "saved by the close", note: `the server got ${said(writesSince(mark))}` };
        } finally { await ctx.close(); }
      },
    },
    {
      name: "a paper's first stroke, the tab closed as the pen lifts, is kept",
      run: (how, n) => strokeThenClose(how, `First stroke ${how} ${n}`),
    },
    {
      name: "a stroke added to a saved group, the tab closed as the pen lifts, is kept",
      run: (how, n) => strokeThenClose(how, `Second stroke ${how} ${n}`, { before: 1 }),
    },
    {
      // past the journal's 300 ms write, before the 700 ms save
      name: "a paper's first stroke, the tab closed 400 ms after the pen lifts, is kept",
      run: (how, n) => strokeThenClose(how, `Later close ${how} ${n}`, { wait: 400 }),
    },
    {
      name: "a setting changed just before the close reaches the account",
      run: async (how) => {
        await user.api("/api/prefs/profile", { method: "PATCH", body: { set: { pdfDarkPage: false } } });
        const ctx = await context();
        try {
          const page = await openPage(ctx);
          const pulled = page.waitForResponse((r) => r.url().includes("/api/prefs/profile") && r.request().method() === "GET");
          await page.goto(server.base);
          await pulled;
          await page.locator('[data-guide="header.account"]').click();
          await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
          const dark = page.getByRole("checkbox", { name: "Dark PDF pages", exact: true });
          await dark.waitFor();
          const mark = server.log().length;
          await dark.check();
          await shut(how, page, ctx);
          const kept = await until(async () => (await user.api("/api/prefs/profile")).value?.pdfDarkPage === true,
            { timeout: 3000, what: "the setting in the profile" }).then(() => true, () => false);
          if (!kept) throw new Error(`lost: the server got ${said(writesSince(mark))}`);
          return { outcome: "saved by the close", note: `the server got ${said(writesSince(mark))}` };
        } finally { await ctx.close(); }
      },
    },
  ];

  for (let n = 1; n <= runs; n++) {
    for (const c of CASES) {
      for (const how of closes) {
        const key = `${c.name} | ${how}.close()`;
        await step(`tab close (${how}.close): ${c.name}${runs > 1 ? `, run ${n}` : ""}`, async () => {
          try {
            const { outcome, note, journal: kept } = await c.run(how, n);
            tally(key, outcome);
            if (kept) tally(key, `journal ${kept}`);
            return `${outcome}: ${note}`;
          } catch (e) {
            const lost = String(e.message).startsWith("lost");
            tally(key, lost ? "lost" : "failed to run");
            if (e.journal) tally(key, `journal ${e.journal}`);
            throw e;
          }
        });
      }
    }
  }
} catch (e) {
  console.log(`\nsetup failed: ${e.stack || e}`);
  results.push({ name: "setup", ok: false });
} finally {
  if (browser) await browser.close().catch(() => {});
  await server.stop();
}

console.log("\ncase | close | outcomes");
for (const [key, t] of tallies) console.log(`${key} | ${Object.entries(t).map(([what, k]) => `${what} ${k}`).join(", ")}`);
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} steps lost nothing`);
process.exit(failed || !results.length ? 1 : 0);
