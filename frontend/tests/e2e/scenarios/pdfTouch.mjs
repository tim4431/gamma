import { waitForPdf } from "./pdf.mjs";

export async function pdfTouchScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage, flags }) {
  let ctx, page, pageId;
  // A page's bitmaps (pdf/pageRaster.js): the base over the whole page and,
  // past the zoom where the canvas cap bites, the detail over the part in view.
  const CAP = 8 * 1024 * 1024; // shared/lib/canvasSize.js
  // The canvas on screen; its hidden twin (pdf/pageRaster.js `pair`) carries data-spare.
  const bitmap = (pn, which = "base") => page.locator(`[data-page="${pn}"] > .pdfPageCanvas > ${which === "detail" ? ".pdfDetailCanvas" : "canvas:not(.pdfDetailCanvas)"}:not([data-spare])`);
  const painted = async (pn, which) => bitmap(pn, which).evaluate((canvas) => {
    if (!canvas.width || !canvas.height) return false;
    const sample = document.createElement("canvas"); sample.width = sample.height = 128;
    const c = sample.getContext("2d"); c.drawImage(canvas, 0, 0, 128, 128);
    const data = c.getImageData(0, 0, 128, 128).data;
    let dark = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i + 3] > 0 && data[i] < 180) dark++;
    sample.width = sample.height = 0;
    return dark > 80;
  });
  // One page's two canvases, and whether the detail's box on screen covers
  // the part of the page inside the viewer's viewport (2 px slack).
  const raster = (pn) => page.locator(`[data-page="${pn}"]`).evaluate((wrap) => {
    const slot = wrap.querySelector(".pdfPageCanvas");
    const base = slot.querySelector("canvas:not(.pdfDetailCanvas):not([data-spare])"), detail = slot.querySelector(".pdfDetailCanvas:not([data-spare])");
    const viewer = wrap.closest(".pdfViewer");
    const p = wrap.getBoundingClientRect(), s = viewer.getBoundingClientRect(), d = detail.getBoundingClientRect();
    const vl = s.left + viewer.clientLeft, vt = s.top + viewer.clientTop;
    const seen = { left: Math.max(p.left, vl), top: Math.max(p.top, vt), right: Math.min(p.right, vl + viewer.clientWidth), bottom: Math.min(p.bottom, vt + viewer.clientHeight) };
    return {
      box: [p.width, p.height], base: [base.width, base.height], baseRatio: base.width / p.width,
      detail: [detail.width, detail.height], shown: getComputedStyle(detail).display !== "none",
      detailRatio: d.width ? detail.width / d.width : 0, at: `${detail.style.left} ${detail.style.top}`,
      covers: d.left <= seen.left + 2 && d.top <= seen.top + 2 && d.right >= seen.right - 2 && d.bottom >= seen.bottom - 2,
    };
  });
  // Every page: its backing pixels, and how far (CSS px, vertically) it lies
  // outside the viewer's viewport.
  const holdings = () => page.locator(".pdfPageWrap").evaluateAll((wraps) => wraps.map((wrap) => {
    const slot = wrap.querySelector(".pdfPageCanvas");
    const base = slot.querySelector("canvas:not(.pdfDetailCanvas):not([data-spare])"), detail = slot.querySelector(".pdfDetailCanvas:not([data-spare])");
    const viewer = wrap.closest(".pdfViewer");
    const p = wrap.getBoundingClientRect(), top = viewer.getBoundingClientRect().top + viewer.clientTop;
    return {
      page: +wrap.dataset.page, gap: Math.max(top - p.bottom, p.top - (top + viewer.clientHeight), 0), view: viewer.clientHeight,
      canvases: [[base.width, base.height], [detail.width, detail.height]], shown: getComputedStyle(detail).display !== "none",
    };
  }));
  const pixels = ([w, h]) => w * h;
  // The zoom buttons step to the next multiple of 20%: press one until page 1
  // is `width` CSS px wide (a Letter page is 612 at 100%).
  const boxWidth = async () => (await page.locator('[data-page="1"]').boundingBox()).width;
  const zoomUntil = async (button, width) => {
    for (let i = 0; i < 60 && Math.abs(await boxWidth() - width) >= 1; i++) {
      const was = await boxWidth();
      await page.getByRole("button", { name: button, exact: true }).click();
      await until(async () => Math.abs(await boxWidth() - was) > 1, { what: `${button} resizes the page` });
    }
    assert(Math.abs(await boxWidth() - width) < 1, `${button} reaches a ${width} px page: ${await boxWidth()}`);
  };
  const toTop = () => page.locator(".pdfViewer").evaluate((el) => el.scrollTo(0, 0));
  // 400% at the top of page 1 with its detail canvas in: where a step leaves
  // the page for the next one.
  const at400 = async (button) => {
    await zoomUntil(button, 2448);
    await toTop();
    await until(async () => (await raster(1)).covers && painted(1, "detail"), { what: "400% detail again" });
  };
  const withinCap = (hs) => hs.every((h) => h.canvases.every(([w, ht]) => w * ht <= CAP && Math.max(w, ht) <= 4096));
  await step("pdf touch: 400% paints within iPad canvas limits and releases distant pages", async () => {
    const pdf = makePdf(Array.from({ length: 8 }, (_, p) => Array.from({ length: 24 }, (_, n) => `Page ${p + 1}, line ${n + 1}: high zoom reading`)));
    const up = await alice.upload("/api/uploads", pdf, "high-zoom.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "iPad zoom", source_url: up.source_url } });
    pageId = created.id;
    ctx = await alice.context(browser, { hasTouch: true, isMobile: true, deviceScaleFactor: 2, viewport: { width: 1024, height: 768 } });
    await ctx.addInitScript(() => {
      localStorage.setItem("gamma-ink-pen-only", "1");
      // Reproduce allocation refusal on constrained WebKit devices. Without
      // the cap, 400% Letter at DPR 2 requests over 30 million pixels.
      window.oversizedCanvases = [];
      const getContext = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (...args) {
        if (this.width * this.height > 16777216) {
          window.oversizedCanvases.push([this.width, this.height]);
          return null;
        }
        return getContext.apply(this, args);
      };
    });
    page = await openPage(ctx, `${server.base}/?page=${created.id}&ws=${alice.ws}`);
    await waitForPdf(page);
    await zoomUntil("Zoom in", 2448);
    // Pages 3 to 8 have no text yet. Their empty layers are scaled like the
    // built ones and must not reach past the page box into the scroll area.
    assert(await page.locator(".pdfPageWrap").evaluateAll((wraps) => wraps.every((w) => {
      const t = w.querySelector(".textLayer").getBoundingClientRect(), b = w.getBoundingClientRect();
      return t.right <= b.right + 1 && t.bottom <= b.bottom + 1;
    })), "every text layer, built or not, stays inside its page box");
    await toTop();
    await until(() => painted(1), { what: "400% bitmap contains PDF text" });
    // Past the cap: the base keeps the capped resolution and a detail canvas
    // at the screen's own resolution covers the part of the page in view.
    await until(async () => (await raster(1)).covers && painted(1, "detail"), { what: "detail canvas covers the view with PDF text" });
    let r = await raster(1);
    assert(r.shown && r.detailRatio >= 1.95, `detail is sharp at DPR 2: ${r.detail} for ${r.detailRatio.toFixed(3)} px/px`);
    assert(pixels(r.base) > CAP * 0.95, `base keeps the capped resolution in detail mode: ${r.base}`);
    assert(withinCap(await holdings()), "bounded backing size");
    assertEq(await page.evaluate(() => window.oversizedCanvases.length), 0);
    const note = `base ${r.base.join("x")}, detail ${r.detail.join("x")}`;
    // Scrolled on by more than a viewport within the page, the detail follows.
    const before = r.at;
    await page.locator(".pdfViewer").evaluate((el) => el.scrollBy(el.clientWidth * 1.2, el.clientHeight * 1.5));
    await until(async () => { const r = await raster(1); return r.shown && r.covers && r.at !== before; }, { what: "detail canvas follows the scroll" });
    await until(() => painted(1, "detail"), { what: "the moved detail canvas contains PDF text" });
    assert((await raster(1)).detailRatio >= 1.95, "the moved detail is still sharp");
    let from = 1;
    for (const pn of [4, 8, 1]) {
      await page.locator(`[data-page="${pn}"]`).evaluate((el) => {
        const viewer = el.closest(".pdfViewer");
        viewer.scrollTop += el.getBoundingClientRect().top - viewer.getBoundingClientRect().top;
      });
      await until(() => painted(pn), { what: `page ${pn} paints after navigation` });
      await until(async () => (await raster(pn)).covers && painted(pn, "detail"), { what: `page ${pn} gets a detail canvas` });
      // Counted in pages holding pixels: the page in view has two canvases.
      // Past the 900 px look-ahead a page holds none; outside the viewport
      // grown by half its size, no detail.
      await until(async () => {
        const hs = await holdings();
        return hs.filter((h) => h.canvases.some((c) => pixels(c) > 0)).length <= 3
          && hs.every((h) => h.gap <= 1000 || h.canvases.every((c) => pixels(c) === 0))
          && hs.every((h) => h.gap <= h.view / 2 + 4 || (pixels(h.canvases[1]) === 0 && !h.shown));
      }, { what: "offscreen canvases released" });
      const was = await raster(from);
      assert([...was.base, ...was.detail].every((v) => v === 0), `page ${from} released both canvases: ${was.base}, ${was.detail}`);
      assert(withinCap(await holdings()), "bounded backing size after navigation");
      from = pn;
    }
    assertEq(await page.evaluate(() => window.oversizedCanvases.length), 0);
    if (flags.keep) await page.screenshot({ path: `${server.dir}/pdf-touch-400.png` });
    assertNoProblems(page);
    return note;
  });

  await step("pdf touch: below the cap one supersampled canvas; the text layer and its selection survive a zoom", async () => {
    // 200% is the last step before the cap bites at DPR 2 (Letter at 220% would
    // need a smaller ratio than 2 backing px per CSS px).
    await zoomUntil("Zoom out", 1224);
    await toTop();
    await until(async () => { const r = await raster(1); return !r.shown && pixels(r.detail) === 0 && r.baseRatio >= 1.99; }, { what: "detail released, base supersampled" });
    assert(await painted(1), "the base alone shows the page");
    assert(withinCap(await holdings()), "bounded backing size");
    // Into detail mode and back: the spans are re-measured, not rebuilt.
    const span = page.locator('[data-page="1"] .textLayer span').filter({ hasText: "line 2:" }).first();
    const selected = await span.evaluate((el) => {
      el.zoomMark = "kept"; el.dataset.zoomMark = "kept";
      getSelection().selectAllChildren(el);
      return getSelection().toString();
    });
    assert(selected.includes("line 2:"), `selected ${selected}`);
    const base = (await raster(1)).base.join("x");
    await zoomUntil("Zoom in", 1346.4);
    await until(async () => { const r = await raster(1); return r.shown && r.covers && r.base.join("x") !== base; }, { what: "220% redrawn in detail mode" });
    await sleep(300); // past the text layer's own settle
    const kept = await page.evaluate(() => {
      const el = document.querySelector('[data-page="1"] [data-zoom-mark="kept"]');
      const sel = getSelection();
      return { mark: el?.zoomMark, attached: !!el?.isConnected, text: sel.toString(), inside: !!el && sel.rangeCount > 0 && el.contains(sel.anchorNode) };
    });
    assert(kept.attached && kept.mark === "kept", "the same span element survives the zoom");
    assert(kept.text === selected && kept.inside, `the selection survives the zoom: ${JSON.stringify(kept.text)}`);
    await page.evaluate(() => getSelection().removeAllRanges());
    await at400("Zoom in");
    assertNoProblems(page);
  });

  await step("pdf touch: 800% is the limit, as sharp as 400% and inside the same canvas limits", async () => {
    await zoomUntil("Zoom in", 4896);
    await toTop();
    await until(async () => { const r = await raster(1); return r.shown && r.covers && r.detailRatio >= 1.95; }, { what: "800% redrawn in detail mode" });
    await until(() => painted(1, "detail"), { what: "the 800% detail canvas contains PDF text" });
    const r = await raster(1);
    assert(pixels(r.base) > CAP * 0.95, `base keeps the capped resolution: ${r.base}`);
    assert(withinCap(await holdings()), "bounded backing size");
    assertEq(await page.evaluate(() => window.oversizedCanvases.length), 0);
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await sleep(300);
    assert(Math.abs(await boxWidth() - 4896) < 1, "800% is the limit");
    await at400("Zoom out");
    assertNoProblems(page);
    return `base ${r.base.join("x")}, detail ${r.detail.join("x")}`;
  });

  await step("pdf touch: live handwriting paints at 400% and releases its backing on lift", async () => {
    await page.getByRole("button", { name: "Handwriting tools", exact: true }).click();
    const box = await page.locator(".pdfViewer").boundingBox();
    const x = box.x + 180, y = box.y + 300;
    await page.mouse.move(x, y); await page.mouse.down();
    await page.mouse.move(x + 130, y + 40, { steps: 10 });
    await until(async () => page.locator('[data-page="1"] .inkCanvas').evaluate((c) => {
      if (!c.width || !c.height) return false;
      return c.getContext("2d").getImageData(0, 0, c.width, c.height).data.some((v, i) => i % 4 === 3 && v > 0);
    }), { what: "live ink visible at high zoom" });
    await page.mouse.up();
    await page.waitForSelector('[data-page="1"] .inkLayer path');
    assertEq(await page.locator('[data-page="1"] .inkCanvas').evaluate((c) => c.width * c.height), 0);
    assertEq(await page.evaluate(() => window.oversizedCanvases.length), 0);
    await page.getByRole("button", { name: "Hand", exact: true }).click();
    assertNoProblems(page);
  });

  // CDP supplies native Chromium touch gestures. The raster scenarios above
  // also run against WebKit via GAMMA_E2E_BROWSER=webkit.
  if (browser.browserType().name() === "chromium") await step("pdf touch: high-zoom swipes avoid scroll corrections during touch and allow diagonal panning", async () => {
    const cdp = await ctx.newCDPSession(page);
    await page.locator(".pdfViewer").evaluate((el) => {
      el.scrollTo({ left: 350, top: 300 });
      window.scrollCorrections = [];
      let touching = false;
      el.addEventListener("touchstart", () => { touching = true; }, { passive: true, capture: true });
      el.addEventListener("touchend", () => { touching = false; }, { passive: true, capture: true });
      const desc = Object.getOwnPropertyDescriptor(Element.prototype, "scrollLeft");
      Object.defineProperty(el, "scrollLeft", { get() { return desc.get.call(this); }, set(value) {
        window.scrollCorrections.push({ touching, method: "scrollLeft" }); desc.set.call(this, value);
      } });
      const scrollTo = el.scrollTo;
      el.scrollTo = function (...args) { window.scrollCorrections.push({ touching, method: "scrollTo" }); return scrollTo.apply(this, args); };
    });
    const box = await page.locator(".pdfViewer").boundingBox();
    const x = Math.round(box.x + box.width * 0.6), y = Math.round(box.y + box.height * 0.75);
    const drag = async (dx, dy) => {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
      for (let i = 1; i <= 10; i++) {
        await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x + dx * i / 10, y: y + dy * i / 10 }] });
        await sleep(16);
      }
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    };
    await drag(-20, -220);
    await until(async () => await page.locator(".pdfViewer").evaluate((el) => el.scrollTop) > 400, { what: "native vertical swipe scrolls" });
    await sleep(900);
    let writes = await page.evaluate(() => window.scrollCorrections);
    assert(writes.every((w) => !w.touching) && writes.length <= 1, "no offset writes during touch; at most one correction after settling");
    const before = await page.locator(".pdfViewer").evaluate((el) => el.scrollLeft);
    await page.evaluate(() => { window.scrollCorrections = []; });
    await drag(-160, -90);
    await until(async () => await page.locator(".pdfViewer").evaluate((el) => el.scrollLeft) > before + 70);
    await sleep(700);
    assertEq(await page.evaluate(() => window.scrollCorrections.length), 0, "diagonal pan is not pulled back");
    assertNoProblems(page);
  });
  // The other half of shared/lib/viewerZoom.js: the notebook's side of it is
  // tests/e2e/scenarios/notebooks.mjs.
  if (browser.browserType().name() === "chromium") await step("pdf touch: two fingers pinch-zoom the pages and pan; Ctrl+wheel zooms; both hold the point under them", async () => {
    const cdp = await ctx.newCDPSession(page);
    await page.getByRole("button", { name: "Fit to width", exact: true }).click();
    await until(async () => Math.abs(await boxWidth() - 612) > 1, { what: "fit-width settles off the 100% width" });
    // Which page a view point sits on, and where down it: what a zoom holds.
    const pointAt = (vx, vy) => page.evaluate(([vx, vy]) => {
      for (const node of document.querySelectorAll(".pdfPageWrap")) {
        const r = node.getBoundingClientRect();
        if (vy >= r.top && vy <= r.bottom) return { page: node.dataset.page, fy: (vy - r.top) / r.height };
      }
      return null;
    }, [vx, vy]);
    const box = await page.locator(".pdfViewer").boundingBox();
    const cx = Math.round(box.x + box.width / 2), cy = Math.round(box.y + box.height / 2);
    const twoFingers = async (from, to, dx = 0, dy = 0) => {
      const pts = (gap, f) => [
        { x: cx + dx * f - gap / 2, y: cy + dy * f, id: 1 },
        { x: cx + dx * f + gap / 2, y: cy + dy * f, id: 2 },
      ];
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: pts(from, 0) });
      for (let i = 1; i <= 12; i++) {
        await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: pts(from + (to - from) * i / 12, i / 12) });
        await sleep(16);
      }
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await sleep(400);
    };

    for (const [from, to] of [[120, 300], [300, 150]]) {
      const was = await boxWidth(), held = await pointAt(cx, cy);
      await twoFingers(from, to);
      const now = await until(async () => {
        const w = await boxWidth();
        return Math.abs(w - was) > 1 ? w : false;
      }, { what: `pages resize for a ${(to / from).toFixed(2)}x pinch` });
      assert(Math.abs(now / was - to / from) < 0.03, `pinch ${from}->${to}: ${(now / was).toFixed(3)}x is the fingers' ${(to / from).toFixed(3)}x`);
      const after = await pointAt(cx, cy);
      assert(after && after.page === held.page && Math.abs(after.fy - held.fy) < 0.03,
        `the page under the fingers is held: ${held.page}@${held.fy.toFixed(3)} -> ${after?.page}@${after?.fy.toFixed(3)}`);
    }
    assertEq(await page.evaluate(() => visualViewport.scale), 1, "the browser's own zoom stays out of it");

    const zoomed = await boxWidth();
    // Against the room the view actually has: a pan that asks for more than
    // the content has left is clamped, and that is not a failure.
    const room = () => page.locator(".pdfViewer").evaluate((el) => ({
      left: el.scrollLeft, top: el.scrollTop,
      maxLeft: el.scrollWidth - el.clientWidth, maxTop: el.scrollHeight - el.clientHeight,
    }));
    const from = await room();
    await twoFingers(200, 200, -120, -150);
    const to = await room();
    const want = [Math.min(from.left + 120, from.maxLeft), Math.min(from.top + 150, from.maxTop)];
    assert(Math.abs(to.left - want[0]) < 3 && Math.abs(to.top - want[1]) < 3,
      `a two-finger drag pans by what the fingers travelled: ${[from.left, from.top]} -> ${[to.left, to.top]}, wanted ${want}`);
    assertEq(await boxWidth(), zoomed, "and does not change the zoom");

    // Ctrl+wheel, off the same reader: one notch is the shared rate and the
    // point under the cursor is held.
    await page.getByRole("button", { name: "Fit to width", exact: true }).click();
    await sleep(400);
    const off = [Math.round(box.x + box.width * 0.32), Math.round(box.y + box.height * 0.28)];
    const under = await pointAt(...off);
    const before = await boxWidth();
    // Against the delta the browser actually delivered, not the one asked
    // for: an emulated device may scale it.
    await page.locator(".pdfViewer").evaluate((el) => {
      window.wheelDy = 0;
      el.addEventListener("wheel", (e) => { window.wheelDy += e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY; }, true);
    });
    await page.mouse.move(...off);
    await page.keyboard.down("Control");
    await page.mouse.wheel(0, -120);
    await page.keyboard.up("Control");
    const after = await until(async () => {
      const w = await boxWidth();
      return Math.abs(w - before) > 0.5 ? w : false;
    }, { what: "Ctrl+wheel zooms the pages" });
    const dy = await page.evaluate(() => window.wheelDy);
    const rate = Math.exp(-dy * 0.0015); // WHEEL_RATE in shared/lib/viewerZoom.js
    assert(Math.abs(after / before - rate) < 0.005,
      `the wheel zooms at the shared rate: ${(after / before).toFixed(4)} vs ${rate.toFixed(4)} for ${dy}px`);
    const stillUnder = await pointAt(...off);
    assert(stillUnder && stillUnder.page === under.page && Math.abs(stillUnder.fy - under.fy) < 0.03,
      `the point under the cursor is held: ${under.fy.toFixed(3)} -> ${stillUnder?.fy.toFixed(3)}`);
    assertNoProblems(page);
  });

  // React's DevTools hook, stood in for before the app loads (react-dom looks
  // for it once, as it starts). After each commit it walks the fiber tree and
  // logs which pages rendered (PdfPage: React.memo's fiber, tag 15, over a
  // .pdfPageWrap, with new props or state) and whether the viewer did (its
  // .pdfZoomLayer got new props). These are React internals: a counter that
  // stops seeing them fails the positive controls below instead of passing.
  const countRenders = (c) => c.addInitScript(() => {
    const log = window.__renders = { injected: false, commits: [] };
    const seen = new WeakMap();
    const changed = (f) => {
      const was = seen.get(f) || (f.alternate && seen.get(f.alternate));
      seen.set(f, [f.memoizedProps, f.memoizedState]);
      if (f.alternate) seen.delete(f.alternate);
      return !was || was[0] !== f.memoizedProps || was[1] !== f.memoizedState;
    };
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
      supportsFiber: true, isDisabled: false, renderers: new Map(),
      inject() { log.injected = true; return 1; },
      onCommitFiberRoot(id, root) {
        const commit = { t: performance.now(), viewer: false, pages: {} };
        const stack = [root.current];
        while (stack.length) {
          const f = stack.pop();
          if (f.tag === 5 && f.stateNode?.classList?.contains("pdfZoomLayer")) {
            if (changed(f)) commit.viewer = true;
          } else if (f.tag === 15 && f.child?.tag === 5 && f.child.stateNode?.classList?.contains("pdfPageWrap")) {
            const pn = f.child.stateNode.dataset.page;
            if (changed(f)) commit.pages[pn] = (commit.pages[pn] || 0) + 1;
          }
          if (f.sibling) stack.push(f.sibling);
          if (f.child) stack.push(f.child);
        }
        log.commits.push(commit);
      },
      onCommitFiberUnmount() {}, onPostCommitFiberRoot() {}, checkDCE() {},
    };
  });
  // The zoom reaches the pages as a CSS variable (shared/lib/viewerZoom.js
  // SCALE_VAR), not a prop: a wheel train renders the viewer each frame and
  // no page, which sizes, and places its overlays, in CSS alone. In its own
  // desktop context, with the counter in before the app, in every engine.
  await step("pdf touch: a Ctrl+wheel train resizes the pages without rendering them; they render once it rests, the highlight and its note where they belong", async () => {
    const pdf = makePdf(Array.from({ length: 3 }, (_, p) => Array.from({ length: 24 }, (_, n) => `Page ${p + 1}, line ${n + 1}: a wheel zoom`)));
    const up = await alice.upload("/api/uploads", pdf, "wheel-zoom.pdf", "application/pdf");
    const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Wheel zoom", source_url: up.source_url } });
    // A highlight with a note on page 1, in the 612 x 792 frame it was made in.
    const at = { x1: 72, y1: 96, x2: 361.5, y2: 110.25 };
    await alice.api("/api/blocks", { method: "POST", body: { parent_id: created.id, content: "A note to zoom with",
      properties: { quote: "", color: "rgba(255, 229, 100, 0.55)", pdf_position: { pageNumber: 1, width: 612, height: 792, boundingRect: at, rects: [at] } } } });
    const c = await alice.context(browser, { viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1 });
    await countRenders(c);
    const p = await openPage(c, `${server.base}/?page=${created.id}&ws=${alice.ws}`);
    try {
      await waitForPdf(p);
      const one = p.locator('[data-page="1"]');
      await one.locator("div[data-hl-id]").waitFor();
      await one.locator(".pdfNoteAnchor").waitFor();
      // Page 1's box, its built text layer, its highlight, its note's
      // anchor and its base canvas, on screen.
      const geometry = () => one.evaluate((wrap) => {
        const r = (el) => { const b = el.getBoundingClientRect(); return { left: b.left, top: b.top, width: b.width, height: b.height }; };
        const base = wrap.querySelector(".pdfPageCanvas > canvas:not(.pdfDetailCanvas):not([data-spare])");
        return { box: r(wrap), text: r(wrap.querySelector(".textLayer")), hl: r(wrap.querySelector("div[data-hl-id]")),
          anchor: r(wrap.querySelector(".pdfNoteAnchor")), base: base.width };
      });
      // Where its stored position puts the highlight in the box as it is,
      // and the badge 2 px past its end, 8 px above its top. A text layer
      // that missed the zoom would stay at 100%.
      const inPlace = (g, when) => {
        const kx = g.box.width / 612, ky = g.box.height / 792;
        const want = { left: g.box.left + at.x1 * kx, top: g.box.top + at.y1 * ky, width: (at.x2 - at.x1) * kx, height: (at.y2 - at.y1) * ky };
        for (const key of Object.keys(want)) {
          assert(Math.abs(g.hl[key] - want[key]) < 0.5, `${when}: the highlight's ${key} is ${g.hl[key].toFixed(2)}, its stored place ${want[key].toFixed(2)}`);
        }
        assert(Math.abs(g.anchor.left - (want.left + want.width + 2)) < 0.5 && Math.abs(g.anchor.top - (want.top - 8)) < 0.5,
          `${when}: the note's badge stands at the passage's end: ${g.anchor.left.toFixed(2)},${g.anchor.top.toFixed(2)}`);
        assert(Math.abs(g.text.width - g.box.width) < 1 && Math.abs(g.text.height - g.box.height) < 1,
          `${when}: the text layer covers the page: ${g.text.width.toFixed(1)}x${g.text.height.toFixed(1)} on ${g.box.width.toFixed(1)}x${g.box.height.toFixed(1)}`);
      };
      await sleep(400); // past the load's own renders and paints
      const before = await geometry();
      inPlace(before, "before the zoom");
      const pointAt = (vx, vy) => p.evaluate(([vx, vy]) => {
        for (const node of document.querySelectorAll(".pdfPageWrap")) {
          const r = node.getBoundingClientRect();
          if (vy >= r.top && vy <= r.bottom) return { page: node.dataset.page, fx: (vx - r.left) / r.width, fy: (vy - r.top) / r.height };
        }
        return null;
      }, [vx, vy]);
      const view = await p.locator(".pdfViewer").boundingBox();
      const top = Math.max(before.box.top, view.y), bottom = Math.min(before.box.top + before.box.height, view.y + view.height);
      const cursor = [Math.round(before.box.left + before.box.width * 0.4), Math.round(top + (bottom - top) * 0.4)];
      const under = await pointAt(...cursor);
      // Style writes on the page's box, its text layer, its highlight and
      // its note's anchor; and the wheel as delivered (an emulated device
      // may scale the delta).
      await p.evaluate(() => {
        const wrap = document.querySelector('[data-page="1"]');
        window.__writes = [];
        const watch = new MutationObserver((list) => { for (const m of list) window.__writes.push({ t: performance.now(), on: m.target.className }); });
        for (const el of [wrap, wrap.querySelector(".textLayer"), ...wrap.querySelectorAll("div[data-hl-id]"), wrap.querySelector(".pdfNoteAnchor")]) {
          watch.observe(el, { attributes: true, attributeFilter: ["style"] });
        }
        window.__wheel = { dy: 0, first: 0, last: 0 };
        wrap.closest(".pdfViewer").addEventListener("wheel", (e) => {
          const w = window.__wheel;
          w.dy += e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY;
          w.last = performance.now();
          w.first ||= w.last;
        }, true);
      });
      // Twelve notches, each in a frame of its own and all well inside the
      // zoom's settle (pdf/pageRaster.js ZOOM_SETTLE_MS, 150 ms).
      await p.mouse.move(...cursor);
      await p.keyboard.down("Control");
      for (let i = 0; i < 12; i++) { await p.mouse.wheel(0, -25); await sleep(40); }
      await p.keyboard.up("Control");
      await sleep(1300);
      const seen = await p.evaluate(() => {
        const { dy, first, last } = window.__wheel, quiet = last + 100;
        const { injected, commits } = window.__renders || { injected: false, commits: [] };
        const renders = (list) => list.reduce((n, c) => n + (c.pages["1"] || 0), 0);
        const train = commits.filter((c) => c.t >= first && c.t <= quiet), rest = commits.filter((c) => c.t > quiet && c.t <= last + 1100);
        return { dy, injected, span: last - first, viewer: train.filter((c) => c.viewer).length, during: renders(train), after: renders(rest),
          writes: window.__writes.filter((w) => w.t >= first && w.t <= quiet) };
      });
      // The counter is live: React found the hook, and the viewer rendered
      // in most of the train's frames.
      assert(seen.injected, "React found the stand-in DevTools hook");
      assert(seen.viewer >= 6, `the viewer renders through the train: ${seen.viewer} times over ${seen.span.toFixed(0)} ms`);
      assertEq(seen.during, 0, "page 1 does not render while the wheel turns");
      assert(seen.after >= 1, `page 1 renders once the zoom rests: ${seen.after}`);
      assertEq(seen.writes.length, 0, `no style is written on page 1's box, text layer, highlight or badge: ${JSON.stringify(seen.writes.slice(0, 4))}`);
      const rate = Math.exp(-seen.dy * 0.0015); // WHEEL_RATE in shared/lib/viewerZoom.js
      let after = await geometry();
      assert(Math.abs(after.box.width / before.box.width - rate) < 0.01,
        `the page grew by the notches' compounded rate: ${(after.box.width / before.box.width).toFixed(4)} vs ${rate.toFixed(4)} for ${seen.dy}px`);
      // Drawn again at the new size, and at least as sharp as the screen.
      after = await until(async () => {
        const g = await geometry();
        return g.base !== before.base && g.base / g.box.width >= 0.99 ? g : false;
      }, { what: "the base canvas is redrawn at the new size" });
      const held = await pointAt(...cursor);
      assert(held && held.page === under.page && Math.abs(held.fx - under.fx) < 0.02 && Math.abs(held.fy - under.fy) < 0.02,
        `the point under the cursor is held: ${JSON.stringify(under)} -> ${JSON.stringify(held)}`);
      inPlace(after, "after the zoom");
      assertNoProblems(p);
      return `${seen.viewer} viewer renders, page 1: ${seen.during} during, ${seen.after} after`;
    } finally {
      await c.close();
    }
  });

  await step("pdf touch: prefers native fullscreen and exits with the toggle or browser", async () => {
    await until(async () => (await alice.api(`/api/blocks/${pageId}/subtree`)).block.children.some((b) => b.properties?.ink_url), { what: "ink saved before navigation" });
    await page.reload(); // Start independently of the previous fling and its instrumentation.
    await waitForPdf(page);
    assert(await page.evaluate(() => matchMedia("(pointer: coarse)").matches), "touch device uses a coarse pointer");
    const nativeFullscreen = await page.evaluate(() => !!document.fullscreenEnabled);
    const enterFullscreen = async () => {
      await page.getByRole("button", { name: "Full screen", exact: true }).tap();
      await page.getByRole("button", { name: "Exit full screen", exact: true }).waitFor();
      if (nativeFullscreen) {
        assert(await page.evaluate(() => document.fullscreenElement === document.documentElement), "touch enters native fullscreen for the whole app");
        assertEq(await page.locator(".app.pseudoFullscreen").count(), 0);
      } else {
        await page.locator(".app.pseudoFullscreen").waitFor();
      }
    };
    const checkExited = async () => {
      await page.getByRole("button", { name: "Full screen", exact: true }).waitFor();
      assert(await page.evaluate(() => !document.fullscreenElement), "native fullscreen exited");
      assertEq(await page.locator(".app.pseudoFullscreen").count(), 0);
      assertEq(await page.locator("html.appFocusFullscreen").count(), 0);
    };
    await enterFullscreen();
    await page.getByRole("button", { name: "Exit full screen", exact: true }).tap();
    await checkExited();
    await enterFullscreen();
    if (nativeFullscreen) {
      await page.evaluate(() => document.exitFullscreen());
    } else {
      await page.keyboard.press("Escape");
    }
    await checkExited();
    assertNoProblems(page);
  });
  await step("pdf touch: paper color survives large zoom cycles and live ink keeps its own theme", async () => {
    // Decode the screenshot in the browser to check the composited output,
    // rather than only checking CSS declarations or the raw white bitmap:
    // the average or the darkest pixel of a clip at dx, dy in page 1's box.
    const screenPixel = async (dx, dy, width, height, darkest) => {
      const rect = await page.locator('[data-page="1"]').boundingBox();
      const png = await page.screenshot({ clip: { x: Math.round(rect.x + dx), y: Math.round(rect.y + dy), width, height } });
      return page.evaluate(async ([data, darkest]) => {
        const img = new Image(); img.src = `data:image/png;base64,${data}`; await img.decode();
        const c = document.createElement("canvas");
        c.width = darkest ? img.width : 1; c.height = darkest ? img.height : 1;
        const ctx = c.getContext("2d"); ctx.drawImage(img, 0, 0, c.width, c.height);
        const px = ctx.getImageData(0, 0, c.width, c.height).data;
        let best = 0;
        for (let i = 4; i < px.length; i += 4) if (px[i] + px[i + 1] + px[i + 2] < px[best] + px[best + 1] + px[best + 2]) best = i;
        return Array.from(px.slice(best, best + 3));
      }, [png.toString("base64"), darkest]);
    };
    const paperPixel = () => screenPixel(20, 150, 2, 2, false);
    // From 200%, the last step before the cap bites (the second step above):
    // twelve steps in are past the cap, twelve back out are under it. The
    // zoom the steps before this one leave differs by browser (two of them
    // run in Chromium only), so it is set here.
    await zoomUntil((await boxWidth()) > 1224 ? "Zoom out" : "Zoom in", 1224);
    // One light theme and the flipped dark page: the two ways paper is composited.
    for (const [theme, flip, expected] of [["sepia", false, [253, 246, 227]], ["dark", true, [15, 15, 15]]]) {
      await page.evaluate(([theme, flip]) => {
        document.documentElement.setAttribute("data-theme", theme);
        document.documentElement.setAttribute("data-scheme", theme === "dark" ? "dark" : "light");
        document.querySelector(".pdfViewer").classList.toggle("pdfDark", flip);
      }, [theme, flip]);
      for (const direction of ["in", "out"]) {
        for (let i = 0; i < 12; i++) await page.getByRole("button", { name: `Zoom ${direction}`, exact: true }).click();
        await toTop();
        await until(() => painted(1));
        // Sampled once the zoom has settled. Zoomed in, past the cap, the
        // point lies under the detail canvas: the two canvases are composited
        // into the paper once, not blended twice.
        if (direction === "in") {
          await until(async () => (await raster(1)).covers && painted(1, "detail"), { what: "settled in detail mode" });
          assert(await bitmap(1, "detail").evaluate((c) => {
            const p = c.closest(".pdfPageWrap").getBoundingClientRect(), d = c.getBoundingClientRect();
            return d.left <= p.left + 20 && d.right >= p.left + 22 && d.top <= p.top + 150 && d.bottom >= p.top + 152;
          }), "the sample lies under the detail canvas");
          // White multiplied onto paper is paper however often it is blended,
          // so the paper alone cannot tell; the ink can. Black text under the
          // multiply at opacity 0.82 is 0.18 × paper once, ~0.03 × paper if
          // the base and the detail were each blended. The clip is the first
          // line's glyphs (72 pt in, baseline 72 pt down) at this zoom.
          if (!flip) {
            const k = await boxWidth() / 612;
            const ink = await screenPixel(72 * k, 56 * k, Math.round(Math.min(300, 150 * k)), Math.round(18 * k), true), want = expected.map((v) => v * 0.18);
            assert(ink.every((v, i) => Math.abs(v - want[i]) <= 4), `${theme}: ink ${ink} is blended once (${want.map(Math.round)})`);
          }
        } else {
          await until(async () => { const r = await raster(1); return !r.shown && r.baseRatio >= 1.99; }, { what: "settled on one supersampled canvas" });
        }
        const rgb = await paperPixel();
        assert(rgb.every((v, i) => Math.abs(v - expected[i]) <= 2), `${theme}, zoom ${direction}: paper ${rgb} matches ${expected}`);
      }
      const style = await page.locator('[data-page="1"] .inkCanvas').evaluate((el) => {
        const s = getComputedStyle(el); return { opacity: s.opacity, blend: s.mixBlendMode, filter: s.filter };
      });
      assertEq(style.opacity, "1", "live ink does not inherit PDF soft-ink opacity");
      assertEq(style.blend, "normal", "live ink does not inherit PDF paper blending");
      assertEq(style.filter, flip ? "invert(1) hue-rotate(180deg)" : "none");
    }
    if (flags.keep) await page.screenshot({ path: `${server.dir}/pdf-zoom-theme.png` });
    assertNoProblems(page);
  });
  if (ctx) await ctx.close();
}
