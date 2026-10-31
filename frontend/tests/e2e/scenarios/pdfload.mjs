// PDF loading (docs/dev/pdf_loading.md). First the timing probe: a 300-page,
// ~20 MB document opened cold on a throttled connection, then warm from the
// browser caches once the background backfill has landed, then again from
// the parsed-document cache. It reads the performance marks App stamps per
// load phase and reports them as the step's note; it asserts only that the
// document paints at all, so a slow machine never fails the suite — the
// numbers are the deliverable. Then the behaviours the fast path must keep:
// page boxes laid out from the manifest, a reading position restored across
// a reload, two large documents alternating without mixing, a paper's tab
// coming back drawn where it was left (from a notes page with the scrollbar
// taking room, and between two papers read at different zooms), and the
// share view opening a large document by ranges with the token on every
// request.
import { launchBrowser } from "../harness.mjs";

export async function pdfLoadScenarios({ server, browser, alice, makePdf, step, until, assert, assertNoProblems, openPage }) {
  const PAGES = 300;
  const PAD = 20 * 1024 * 1024;
  // 20 Mbps down, 40 ms latency — a home connection to a self-hosted server.
  const NET = { offline: false, latency: 40, downloadThroughput: (20e6) / 8, uploadThroughput: (5e6) / 8 };

  // The marks of the LATEST load (from its "open" on), so a second open in
  // the same tab is not read against the first one's phases.
  async function marks(page) {
    return page.evaluate(() => {
      const all = performance.getEntriesByType("mark").filter((m) => m.name.startsWith("pdf-"));
      const opens = all.filter((m) => m.name === "pdf-open");
      const since = opens.length ? opens[opens.length - 1].startTime : 0;
      const out = {};
      for (const m of all) if (m.startTime >= since) out[m.name.slice(4)] = m.detail?.ms;
      return out;
    });
  }
  const paintCount = (page) => page.evaluate(() => performance.getEntriesByName("pdf-painted").length);
  const waitPainted = (page, before) =>
    until(async () => (await paintCount(page)) > before, { timeout: 120000, every: 200, what: "first page paint" });
  // The pdf.js worker script as the page's resource timing saw it: bytes over
  // the wire (0 = served from the browser cache) and how long it took.
  const workerFetch = (page) => page.evaluate(() => {
    const r = performance.getEntriesByType("resource").find((e) => e.name.includes("pdf.worker"));
    return r ? `worker ${r.transferSize ? `${(r.transferSize / 1048576).toFixed(1)} MB` : "from cache"} in ${Math.round(r.duration)} ms` : "worker fetch not seen";
  });
  function fmt(m) {
    return Object.entries(m).filter(([, v]) => v != null).sort((a, b) => a[1] - b[1]).map(([k, v]) => `${k} +${v}`).join(" ");
  }
  // What the browser actually pulled over the wire for the PDF file: CDP's
  // encodedDataLength counts bytes received, not what a cancelled request
  // would have been, and the status tells ranges (206) from whole files (200).
  // The viewer's HEAD (the file's size, raced against the manifest) carries
  // no file: it usually ends aborted, but finishes, 200, when it completes
  // first, which counted it as a whole file now and then.
  async function meter(page, throttle = true) {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Network.enable");
    if (throttle) await cdp.send("Network.emulateNetworkConditions", NET);
    const byId = new Map();
    const heads = new Set();
    const stat = { ranges: 0, whole: 0, bytes: 0, manifest: 0 };
    cdp.on("Network.requestWillBeSent", (e) => {
      if (e.request.method === "HEAD") heads.add(e.requestId);
    });
    cdp.on("Network.responseReceived", (e) => {
      const u = e.response.url;
      if (u.includes("/api/pdf-info/")) stat.manifest += 1;
      if (u.includes("/api/uploads/") && u.includes(".pdf") && !heads.has(e.requestId)) byId.set(e.requestId, e.response.status);
    });
    cdp.on("Network.loadingFinished", (e) => {
      const status = byId.get(e.requestId);
      if (status == null) return;
      if (status === 206) stat.ranges += 1; else stat.whole += 1;
      stat.bytes += e.encodedDataLength;
    });
    stat.text = () => `${stat.ranges} range + ${stat.whole} whole request(s), ${(stat.bytes / 1048576).toFixed(1)} MB on the wire${stat.manifest ? ", manifest used" : ""}`;
    return stat;
  }
  const hasDiskCopy = (page, pdfUrl) => page.evaluate((u) => new Promise((resolve) => {
    const rq = indexedDB.open("gamma-pdf-cache"); // whatever version the app made
    rq.onerror = () => resolve(false);
    rq.onsuccess = () => {
      const db = rq.result;
      try {
        const g = db.transaction("pdfs").objectStore("pdfs").getKey(u);
        g.onsuccess = () => { db.close(); resolve(g.result !== undefined); };
        g.onerror = () => { db.close(); resolve(false); };
      } catch { db.close(); resolve(false); }
    };
  }), pdfUrl);
  const pageText = (page, n) => page.textContent(`[data-page="${n}"] .textLayer`).catch(() => "");
  // The text of the page the viewer says it is on — after a restore that is
  // not page 1, and page 1 may not even be rendered.
  const currentPageText = async (page) => {
    const n = await page.inputValue("input[aria-label='Current page']").catch(() => "");
    return n ? `page ${n}: ${await pageText(page, n)}` : "";
  };
  const box = (page, sel) => page.evaluate((s) => {
    const r = document.querySelector(s)?.getBoundingClientRect();
    return r ? { top: r.top, width: r.width, height: r.height } : null;
  }, sel);

  // A NON-default workspace on purpose: pdf.js fetches the range requests
  // itself, with an absolute url, and the fetch wrapper has to tag those with
  // the workspace too — in the default workspace a missing header goes
  // unnoticed.
  const probeWs = await alice.api("/api/workspaces", { method: "POST", body: { name: "Probe" } });
  const account = Object.assign(Object.create(Object.getPrototypeOf(alice)), alice, { ws: probeWs.id });
  async function bigDoc(title, lines) {
    const pdf = makePdf(Array.from({ length: PAGES }, (_, i) => lines(i + 1)), { padBytes: PAD });
    const up = await account.upload("/api/uploads", pdf, `${title}.pdf`, "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: title, source_url: up.source_url } });
    return { up, pageId: created.id, url: `${server.base}/?page=${created.id}&ws=${account.ws}` };
  }
  const A = await bigDoc("Timing probe", (n) => [`Timing probe page ${n}`, "Lorem ipsum dolor sit amet"]);

  await step("pdf load: worker MIME failure stays visible after a late manifest, and reload recovers", async () => {
    const c = await account.context(browser);
    let releaseManifest;
    const gate = new Promise((resolve) => { releaseManifest = resolve; });
    await c.route("**/assets/pdf.worker*.mjs*", (route) => route.fulfill({
      status: 200, contentType: "text/plain", body: "export {};",
    }));
    await c.route("**/api/pdf-info/**", async (route) => {
      const response = await route.fetch();
      await gate;
      await route.fulfill({ response });
    });
    try {
      const p = await openPage(c, A.url);
      const error = p.locator(".statusPill.error");
      await until(async () => (await error.textContent().catch(() => "")).includes("PDF load failed"),
        { timeout: 15000, what: "worker failure shown" });
      const manifest = p.waitForResponse((r) => r.url().includes("/api/pdf-info/"));
      releaseManifest();
      await (await manifest).finished();
      // Let fetch's continuation and React's layout effects finish before
      // checking that the late skeleton did not replace the error pill.
      await p.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const phases = await p.evaluate(() => performance.getEntriesByType("mark")
        .filter((m) => m.name.startsWith("pdf-")).map((m) => m.name));
      assert(!phases.slice(phases.indexOf("pdf-error") + 1).includes("pdf-layout"),
        `no skeleton after failure: ${phases.join(", ")}`);
      assert((await error.textContent()).includes("PDF load failed"), "failure remains visible");
      assert(await error.locator(".pillSpin").count() === 0, "failed load stops spinning");
      assertNoProblems(p, [/^console: Failed to load module script:.*MIME type of "text\/plain"/]);

      await c.unroute("**/assets/pdf.worker*.mjs*");
      await p.reload();
      await waitPainted(p, 0);
      assertNoProblems(p);
    } finally {
      releaseManifest();
      await c.close();
    }
  });

  let ctx, page;
  await step("pdf load: cold open of a 300-page, 20 MB document at 20 Mbps", async () => {
    ctx = await account.context(browser);
    page = await openPage(ctx); // collectors attached, no navigation yet
    const stat = await meter(page);
    await page.goto(A.url);
    await waitPainted(page, 0);
    const m = await marks(page);
    assert((await page.$$("[data-page]")).length >= PAGES, "every page box is in the DOM");
    assertNoProblems(page);
    return `${fmt(m)} ms; ${stat.text()}; ${await workerFetch(page)}`;
  });

  await step("pdf load: the whole file is backfilled into IndexedDB after the range open", async () => {
    const t0 = Date.now();
    await until(() => hasDiskCopy(page, A.up.source_url), { timeout: 120000, every: 500, what: "IndexedDB copy" });
    assertNoProblems(page);
    return `landed ${Date.now() - t0} ms after first paint`;
  });

  let p2;
  await step("pdf load: warm reopen of the same document", async () => {
    // A fresh page in the same context keeps IndexedDB and the HTTP cache.
    p2 = await openPage(ctx);
    const stat = await meter(p2);
    await p2.goto(A.url);
    await waitPainted(p2, 0);
    const m = await marks(p2);
    assertNoProblems(p2);
    return `${fmt(m)} ms; ${stat.text()}; ${await workerFetch(p2)}`;
  });

  await step("pdf load: back to the paper from the home library, same tab (parsed document kept)", async () => {
    const before = await paintCount(p2);
    await p2.click("button[aria-label='Home']");
    // The title is whatever metadata extraction read off page 1, so match the document either way.
    const card = p2.locator(".pageCard", { hasText: /Timing probe|Lorem ipsum/ }).first();
    await card.waitFor({ timeout: 15000 });
    await card.dblclick();
    await waitPainted(p2, before);
    const m = await marks(p2);
    assertNoProblems(p2);
    await ctx.close();
    return `${fmt(m)} ms`;
  });

  // ---- behaviours -----------------------------------------------------------

  await step("pdf load: page boxes come from the manifest — a landscape page far below the fold has its own shape", async () => {
    // 40 pages so page 30 sits well past the render look-ahead: its box is
    // never measured by pdf.js here, so a landscape box can only have come
    // from the manifest (the old estimate gave every unmeasured page page 1's
    // portrait shape).
    const pdf = makePdf(Array.from({ length: 40 }, (_, i) => (i === 29
      ? { lines: [`Wide page ${i + 1}`], box: [792, 612] }
      : [`Narrow page ${i + 1}`])));
    const up = await account.upload("/api/uploads", pdf, "mixed.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Mixed sizes", source_url: up.source_url } });
    const c = await account.context(browser);
    const p = await openPage(c, `${server.base}/?page=${created.id}&ws=${account.ws}`);
    await waitPainted(p, 0);
    const first = await box(p, '[data-page="1"]');
    const wide = await box(p, '[data-page="30"]');
    assert(first && wide, "page boxes exist");
    assert(!(await p.$('[data-page="30"] .textLayer span')), "page 30 is not rendered (below the look-ahead)");
    assert(Math.abs(first.width / first.height - 612 / 792) < 0.02, `page 1 portrait: ${first.width}x${first.height}`);
    assert(Math.abs(wide.width / wide.height - 792 / 612) < 0.02, `page 30 landscape: ${wide.width}x${wide.height}`);
    assertNoProblems(p);
    await c.close();
  });

  await step("pdf load: a paper opened and left without scrolling gets its cover on the recents card", async () => {
    // The cover is read off the canvas each page shows. Those come in pairs
    // that swap at every paint (pageRaster.js `pair`), so after one paint a
    // page's first canvas is the released spare and a capture from it found
    // nothing.
    const pdf = makePdf(Array.from({ length: 3 }, (_, i) => [`Cover sample page ${i + 1}`, "Lorem ipsum dolor sit amet"]));
    const up = await account.upload("/api/uploads", pdf, "cover.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Cover sample", source_url: up.source_url } });
    const c = await account.context(browser);
    try {
      const p = await openPage(c, `${server.base}/?page=${created.id}&ws=${account.ws}`);
      await waitPainted(p, 0);
      await until(async () => !!(await account.api("/api/page-snaps")).snaps?.[created.id]?.img,
        { timeout: 30000, every: 500, what: "the cover stored on the server" });
      await p.click("button[aria-label='Home']");
      await p.locator(".pageCard", { hasText: /Cover sample/ }).first().locator(".pageCardCover img").waitFor({ timeout: 10000 });
      assertNoProblems(p);
    } finally {
      await c.close();
    }
  });

  await step("pdf load: fit to width fits the text pages, not a cover of another size — from the manifest and measured", async () => {
    // A scanned book's shape: a small cover image, then text pages 1.76×
    // wider. Fit-width used to size to page 1, which put every text page past
    // the viewer's edge. 40 pages, so without a manifest most widths are
    // estimates from the eight measured ones.
    const pdf = makePdf(Array.from({ length: 40 }, (_, i) => (i === 0
      ? { lines: ["Cover"], box: [252, 366] }
      : { lines: [`Text page ${i + 1}`], box: [444, 648] })));
    const up = await account.upload("/api/uploads", pdf, "covered.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Covered book", source_url: up.source_url } });
    const url = `${server.base}/?page=${created.id}&ws=${account.ws}`;
    const fits = async (p, how) => {
      const v = await p.evaluate(() => {
        const el = document.querySelector(".pdfViewer");
        const w = (n) => document.querySelector(`[data-page="${n}"]`)?.getBoundingClientRect().width;
        return { client: el.clientWidth, scroll: el.scrollWidth, cover: w(1), text: w(2), far: w(30) };
      });
      const seen = `${how}: ${JSON.stringify(v)}`;
      assert(Math.abs(v.text - v.client) <= 1 && Math.abs(v.far - v.client) <= 1, `text pages fill the width — ${seen}`);
      assert(v.scroll <= v.client, `no horizontal overflow — ${seen}`);
      assert(Math.abs(v.cover - v.client * 252 / 444) <= 2, `the cover is narrower — ${seen}`);
    };
    const c = await account.context(browser);
    try {
      const p = await openPage(c, url); // fit-width is the default zoom
      await waitPainted(p, 0);
      await fits(p, "manifest");
      assertNoProblems(p);
      // A manifest that describes nothing: the viewer measures the first
      // eight pages and estimates the rest.
      await c.route("**/api/pdf-info/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
      const q = await openPage(c, url);
      await waitPainted(q, 0);
      await fits(q, "measured");
      assertNoProblems(q);
    } finally {
      await c.close();
    }
  });

  let ctx3, p3;
  await step("pdf load: the reading position survives a reload of the large document", async () => {
    ctx3 = await account.context(browser);
    p3 = await openPage(ctx3, A.url);
    await waitPainted(p3, 0);
    // Jump to page 150 the way a reader does, then wait for the position to
    // be recorded (localStorage gets it at once; the server later).
    await p3.fill("input[aria-label='Current page']", "150");
    await p3.press("input[aria-label='Current page']", "Enter");
    await until(() => p3.evaluate(() => Object.keys(localStorage).some((k) => k.startsWith("gamma-read-pos:") && /"page":150\b/.test(localStorage.getItem(k) || ""))),
      { timeout: 15000, what: "page 150 recorded" });
    await p3.reload(); // a reload starts a fresh performance timeline: marks count from zero again
    await waitPainted(p3, 0);
    const m = await marks(p3);
    assert(m.layout != null, `the skeleton was laid out (${fmt(m)})`);
    // Page 150's box sits at the top of the viewer (under the viewer's 80 px
    // context margin): the restore landed.
    await until(async () => {
      const v = await box(p3, ".pdfViewer");
      const b = await box(p3, '[data-page="150"]');
      return v && b && b.top >= v.top - 2 && b.top <= v.top + 100;
    }, { timeout: 10000, what: "page 150 at the top of the viewer" });
    await until(async () => (await p3.inputValue("input[aria-label='Current page']")) === "150", { timeout: 5000, what: "page indicator 150" });
    assertNoProblems(p3);
    return fmt(m) + " ms";
  });

  await step("pdf load: two large documents alternate through the parsed-document cache without mixing", async () => {
    const B = await bigDoc("Second book", (n) => [`Second book page ${n}`, "Alpha beta gamma delta"]);
    // Open a paper from the library and check that the page the viewer lands
    // on (A comes back at its restored page 150) shows THAT book's text.
    const open = async (re, book) => {
      const before = await paintCount(p3);
      await p3.click("button[aria-label='Home']");
      // A recently viewed paper is a card in the recents strip; one never
      // opened in this browser is only a row in the library list.
      const card = p3.locator(".pageCard, .fileRow", { hasText: re }).first();
      await card.waitFor({ timeout: 15000 });
      await card.dblclick();
      await waitPainted(p3, before);
      // Text-layer spans concatenate without spaces ("page 1Alpha beta"), so no word boundary after the number.
      const shows = new RegExp(`^page (\\d+): .*${book} page \\1(?!\\d)`);
      await until(async () => shows.test(await currentPageText(p3)), { timeout: 15000, what: `the visible page is from "${book}"` });
      return marks(p3);
    };
    const firstB = await open(/Second book|Alpha beta/, "Second book"); // cold: ranges
    const backA = await open(/Timing probe|Lorem ipsum/, "Timing probe");
    const backB = await open(/Second book|Alpha beta/, "Second book");
    assert(backA.cached != null && backB.cached != null, `both reopen from the cache (A ${fmt(backA)}; B ${fmt(backB)})`);
    assertNoProblems(p3);
    await ctx3.close();
    return `B cold ${fmt(firstB)} ms; A back ${fmt(backA)} ms; B back ${fmt(backB)} ms`;
  });

  // From the next press on, every painted frame while `name` is the
  // page's title: its page boxes in view, how many of them hold pixels, the
  // highlights drawn on them, the scroll, the first page's width, the page
  // number shown and the status pill. `framesSeen` ends the watch and hands
  // the frames over.
  const watchFrames = (p, name) => p.evaluate((name) => {
    const seen = (window.__seen = []);
    let pressed = false;
    window.addEventListener("pointerdown", () => { pressed = true; }, { capture: true, once: true });
    const look = () => {
      if (window.__seen !== seen) return;
      const v = document.querySelector(".viewerWrap:not(.pdfHidden) .pdfViewer");
      if (pressed && v && document.querySelector(".pageTitleRow .titleText")?.textContent.trim() === name) {
        const box = v.getBoundingClientRect();
        const inView = [...v.querySelectorAll(".pdfPageWrap")].filter((el) => {
          const r = el.getBoundingClientRect();
          return r.bottom > box.top && r.top < box.bottom;
        });
        const drawn = inView.filter((el) => [...el.querySelectorAll(".pdfPageCanvas > canvas")]
          .some((cv) => cv.width > 0 && getComputedStyle(cv).display !== "none"));
        const hl = inView.reduce((n, el) => n + el.querySelectorAll("[data-hl-id]").length, 0);
        seen.push({ pages: inView.length, drawn: drawn.length, hl, top: Math.round(v.scrollTop),
          width: Math.round(v.querySelector('[data-page="1"]')?.getBoundingClientRect().width || 0),
          pn: document.querySelector(".pdfPageWidget input")?.value || "",
          pill: document.querySelector(".statusPill .pillText")?.textContent || "" });
      }
      requestAnimationFrame(look);
    };
    requestAnimationFrame(look);
  }, name);
  const framesSeen = (p) => p.evaluate(() => { const s = window.__seen; window.__seen = null; return s; });
  const saidOf = (seen) => seen.map((f) => `${f.drawn}/${f.pages} hl${f.hl} at ${f.top} w${f.width} p${f.pn}${f.pill ? ` "${f.pill}"` : ""}`).join(", ");
  // Where the reader is, once every page in view holds its pixels (what a
  // document leaving keeps is what its pages in view show) and two readings
  // agree (the page number follows a scroll a frame later).
  const placeOf = async (p) => {
    let at = null;
    await until(async () => {
      const now = await p.locator(".pdfViewer").evaluate((v) => {
        const box = v.getBoundingClientRect();
        const inView = [...v.querySelectorAll(".pdfPageWrap")].filter((el) => {
          const r = el.getBoundingClientRect();
          return r.bottom > box.top && r.top < box.bottom;
        });
        const drawn = inView.filter((el) => [...el.querySelectorAll(".pdfPageCanvas > canvas")].some((cv) => cv.width > 0 && getComputedStyle(cv).display !== "none"));
        return { all: inView.length > 0 && drawn.length === inView.length, top: Math.round(v.scrollTop),
          hl: inView.reduce((n, el) => n + el.querySelectorAll("[data-hl-id]").length, 0),
          width: Math.round(v.querySelector('[data-page="1"]').getBoundingClientRect().width),
          pn: document.querySelector("input[aria-label='Current page']")?.value || "" };
      });
      const same = at && now.all && at.all && now.top === at.top && now.width === at.width && now.pn === at.pn && now.hl === at.hl;
      at = now;
      return same;
    }, { what: "every page in view drawn, the place settled" });
    return { top: at.top, width: at.width, pn: at.pn, hl: at.hl };
  };

  // With the browser's scrollbars shown, which take room in the viewer as
  // on Windows, Linux and a Mac with a mouse: the pages' width must not
  // depend on whether the scrollbar is there yet, or the fit-width settles
  // twice as the viewer comes back and each return lands a few px higher.
  await step("pdf load: back to a paper's tab from a notes page, every frame shows its pages drawn, with their highlights, where they were left, at the same zoom, round after round, with the scrollbar taking room", async () => {
    const pdf = makePdf(Array.from({ length: 4 }, (_, i) => [`Return page ${i + 1}`, "Lorem ipsum dolor sit amet"]));
    const up = await account.upload("/api/uploads", pdf, "return.pdf", "application/pdf");
    const paper = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Return paper", source_url: up.source_url } });
    // A highlight on each of the pages the reader is left on: it comes back
    // with its page's pixels, in the same frame.
    for (const [pageNumber, y] of [[1, 700], [2, 40]]) {
      const rect = { x1: 60, y1: y, x2: 480, y2: y + 20 };
      await account.api("/api/blocks", { method: "POST", body: { parent_id: paper.id, content: "",
        properties: { quote: "", color: "rgba(255, 229, 100, 0.55)", pdf_position: { pageNumber, width: 612, height: 792, boundingRect: rect, rects: [rect] } } } });
    }
    const notes = await account.api("/api/pages", { method: "POST", body: { title: "Return notes" } });
    await account.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [{ id: paper.id, title: "Return paper" }, { id: notes.id, title: "Return notes" }] } });
    const shown = await launchBrowser({ scrollbars: true });
    const c = await account.context(shown);
    const p = await openPage(c, `${server.base}/?page=${paper.id}&ws=${account.ws}`);
    const title = () => p.locator(".pageTitleRow .titleText").innerText();
    const tab = (name) => p.locator(".tabStrip .tab:not([data-leaving])", { hasText: name });
    try {
      await waitPainted(p, 0);
      if (shown.browserType().name() === "chromium") {
        const gutter = await p.locator(".pdfViewer").evaluate((el) => el.offsetWidth - el.clientWidth);
        assert(gutter > 0, `the viewer's scrollbar takes room (${gutter} px)`);
      }
      await p.locator(".pdfViewer").evaluate((el) => el.scrollTo(0, 1000));
      await until(async () => (await p.inputValue("input[aria-label='Current page']")) === "2", { what: "the reader on page 2" });
      const left = await placeOf(p);
      assert(left.hl > 0, `the highlights drawn on the pages in view (${left.hl})`);
      for (const round of [1, 2, 3]) {
        await tab("Return notes").click();
        await until(async () => (await title()) === "Return notes", { what: "the notes page open" });
        await watchFrames(p, "Return paper");
        await tab("Return paper").click();
        await until(async () => (await title()) === "Return paper", { what: "the paper back" });
        await p.waitForTimeout(300);
        const seen = await framesSeen(p);
        const said = saidOf(seen);
        assert(seen.length > 0, `round ${round}: frames of the paper seen`);
        assert(seen.every((f) => f.pages > 0 && f.drawn === f.pages), `round ${round}: every page in view is drawn in every frame: ${said}`);
        assert(seen.every((f) => f.hl === left.hl), `round ${round}: their highlights with them in every frame (${left.hl}): ${said}`);
        assert(seen.every((f) => Math.abs(f.top - left.top) <= 1 && f.width === left.width), `round ${round}: every frame at the place and zoom left (${left.top}, w${left.width}): ${said}`);
        assert(seen.every((f) => f.pn === "2"), `round ${round}: the page number it was left on in every frame: ${said}`);
      }
      assertNoProblems(p);
    } finally {
      await c.close();
      await shown.close();
    }
  });

  // The notes beside a paper keep their place too: its tab comes back to
  // the row the reader had at the notes' top, as far into it, not to the
  // first row (the paper's read-pos entry is its PDF page, so this place
  // is the session's).
  await step("pdf load: back to a paper's tab, its notes are where they were left, not at the first row", async () => {
    const up = await account.upload("/api/uploads", makePdf([["Notes place page 1", "Lorem ipsum dolor sit amet"]]), "notes-place.pdf", "application/pdf");
    const paper = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Notes place paper", source_url: up.source_url } });
    for (let i = 1; i <= 40; i++) {
      await account.api("/api/blocks", { method: "POST", body: { parent_id: paper.id, content: `Place note ${i}\n\nwith a second paragraph` } });
    }
    const other = await account.api("/api/pages", { method: "POST", body: { title: "Notes place other" } });
    await account.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [{ id: paper.id, title: "Notes place paper" }, { id: other.id, title: "Notes place other" }] } });
    const c = await account.context(browser);
    const p = await openPage(c, `${server.base}/?page=${paper.id}&ws=${account.ws}`);
    const title = () => p.locator(".pageTitleRow .titleText").innerText();
    const tab = (name) => p.locator(".tabStrip .tab:not([data-leaving])", { hasText: name });
    // the row at the notes' top, and how far it starts above it
    const topRow = () => p.locator(".sidebar .blockList").evaluate((list) => {
      const top = list.getBoundingClientRect().top;
      for (const row of list.querySelectorAll(".blockRowWrap[data-block-id]")) {
        const r = row.getBoundingClientRect();
        if (r.bottom > top + 4) return { text: row.innerText.match(/Place note \d+/)?.[0], offset: Math.round(top - r.top), scrolled: list.scrollTop };
      }
      return null;
    });
    try {
      await p.locator(".blockRowWrap", { hasText: "Place note 40" }).waitFor();
      await p.locator(".sidebar .blockList").evaluate((list) => {
        const row = [...list.querySelectorAll(".blockRowWrap[data-block-id]")].find((r) => /Place note 25\b/.test(r.innerText));
        list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top + 10;
      });
      const left = await topRow();
      assert(left.text === "Place note 25", `the notes scrolled to the 25th note: ${left.text}`);
      for (const round of [1, 2]) {
        await tab("Notes place other").click();
        await until(async () => (await title()) === "Notes place other", { what: "the other page open" });
        await tab("Notes place paper").click();
        await until(async () => (await title()) === "Notes place paper", { what: "the paper back" });
        await p.locator(".blockRowWrap", { hasText: "Place note 40" }).waitFor();
        await p.waitForTimeout(300);
        const back = await topRow();
        assert(back.text === left.text, `round ${round}: the same row at the notes' top: ${back.text}`);
        assert(Math.abs(back.offset - left.offset) <= 2, `round ${round}: as far into it (${back.offset} vs ${left.offset})`);
      }
      assert(await p.locator(".blockRow.landed").count() === 0, "no landing flash");
      assertNoProblems(p);
    } finally { await c.close(); }
  });

  // Each tab brings its zoom with its url, so the paper leaving is laid
  // out at the next one's zoom in the commit that takes it off screen: what
  // it keeps must be the pages the reader saw, not the ones under its
  // scroll at the other zoom.
  await step("pdf load: between two papers' tabs read at different zooms, every frame shows the pages in view drawn, where each was left, at its own zoom, with no \"Rendering page…\"", async () => {
    const paperOf = async (name) => {
      const pdf = makePdf(Array.from({ length: 10 }, (_, i) => [`${name} page ${i + 1}`, "Lorem ipsum dolor sit amet"]));
      const up = await account.upload("/api/uploads", pdf, `${name.toLowerCase()}.pdf`, "application/pdf");
      return account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: `${name} paper`, source_url: up.source_url } });
    };
    const near = await paperOf("Near"), far = await paperOf("Far");
    await account.api("/api/prefs/open-tabs", { method: "PUT", body: { value: [{ id: near.id, title: "Near paper" }, { id: far.id, title: "Far paper" }] } });
    const c = await account.context(browser);
    const p = await openPage(c, `${server.base}/?page=${near.id}&ws=${account.ws}`);
    const title = () => p.locator(".pageTitleRow .titleText").innerText();
    const tab = (name) => p.locator(".tabStrip .tab:not([data-leaving])", { hasText: name });
    const width = () => p.locator(".pdfViewer").evaluate((el) => Math.round(el.querySelector('[data-page="1"]').getBoundingClientRect().width));
    // Zoomed by the toolbar's button, `times` presses, and read from
    // `share` of the way down.
    const readAt = async (button, times, share) => {
      for (let i = 0; i < times; i++) {
        const before = await width();
        await p.getByRole("button", { name: button, exact: true }).click();
        await until(async () => (await width()) !== before, { what: `${button} applied` });
      }
      await p.locator(".pdfViewer").evaluate((el, share) => el.scrollTo(0, Math.round(el.scrollHeight * share)), share);
      return placeOf(p);
    };
    try {
      await waitPainted(p, 0);
      const left = { "Near paper": await readAt("Zoom in", 2, 0.45) };
      const painted = await paintCount(p);
      await tab("Far paper").click();
      await until(async () => (await title()) === "Far paper", { what: "the far paper open" });
      await waitPainted(p, painted);
      left["Far paper"] = await readAt("Zoom out", 3, 0.4);
      assert(left["Near paper"].width !== left["Far paper"].width, `the two papers read at different zooms (w${left["Near paper"].width}, w${left["Far paper"].width})`);
      for (const name of ["Near paper", "Far paper", "Near paper", "Far paper"]) {
        await watchFrames(p, name);
        await tab(name).click();
        await until(async () => (await title()) === name, { what: `${name} back` });
        await p.waitForTimeout(300);
        const seen = await framesSeen(p);
        const said = saidOf(seen);
        const at = left[name];
        assert(seen.length > 0, `${name}: frames of it seen`);
        assert(seen.every((f) => f.pages > 0 && f.drawn === f.pages), `${name}: every page in view is drawn in every frame: ${said}`);
        assert(seen.every((f) => Math.abs(f.top - at.top) <= 1 && f.width === at.width && f.pn === at.pn), `${name}: every frame where it was left (${at.top}, w${at.width}, p${at.pn}): ${said}`);
        assert(!seen.some((f) => f.pill.includes("Rendering page")), `${name}: no "Rendering page…" for a paper drawn already: ${said}`);
      }
      assertNoProblems(p);
    } finally {
      await c.close();
    }
  });

  await step("pdf load: an anonymous share visitor opens the large document by ranges", async () => {
    const share = await account.api(`/api/share/${A.pageId}`, { method: "POST" });
    assert(share.token, "share token");
    const c = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const p = await openPage(c);
    const stat = await meter(p, false);
    await p.goto(`${server.base}/?share=${share.token}`);
    await waitPainted(p, 0);
    await until(async () => (await pageText(p, 1)).includes("Timing probe page 1"), { timeout: 15000, what: "page 1 text" });
    assert(stat.ranges >= 1 && stat.whole === 0, `opened by ranges: ${stat.text()}`);
    assert(stat.manifest >= 1, "the manifest was fetched through the share");
    assertNoProblems(p); // a HEAD, a manifest or a range without the token would have logged a 401/403
    await c.close();
    return stat.text();
  });
}
