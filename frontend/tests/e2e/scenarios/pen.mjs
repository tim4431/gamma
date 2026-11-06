// What a pen's press is (docs/dev/handwriting.md "Claiming input"): the armed tool,
// and with nothing armed what a mouse does on the page. On a PDF page a drag
// selects text, the popup opening at its lift; with the text highlighter on
// that selection is highlighted at once; in rectangle mode the drag draws
// the rectangle. An armed tool takes the pen over all of these, the pen's
// eraser end erases with nothing armed, and paper, which has no text, is
// written on. Then the touch events a tablet sends with those drags (each
// cancelled as an armed pen's is, fingers palms, a tap's click sent by the
// page), the preference that keeps a pen writing on PDFs and the one it
// replaced, and
// both shells: nothing armed claims nothing, and a gesture from the hand
// comes back to the hand. The rules are frontend/tests/pencilActions.test.mjs.
// A physical iPad and tablet are still needed for the system's own pan,
// long press and selection handles (docs/dev/ipad.md, android.md device
// checks).
import { waitForPdf } from "./pdf.mjs";
import { stylus } from "./textBoxes.mjs";
import { shellPaper } from "../shells.mjs";

export async function penScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage }) {
  const account = alice;
  const up = await account.upload("/api/uploads", makePdf([["Alpha beta gamma delta", "Epsilon zeta eta theta", "Iota kappa lambda mu", "Nu xi omicron pi"], ["The next page"]]),
    "pen.pdf", "application/pdf");
  const doc = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Pen paper", source_url: up.source_url } });
  const paperUrl = `${server.base}/?page=${doc.id}&ws=${account.ws}`;
  const ctx = await account.context(browser, { hasTouch: true });
  const page = await openPage(ctx, paperUrl);
  await waitForPdf(page, 1);
  const cdp = await ctx.newCDPSession(page);
  const pen = stylus(cdp);
  const ink = '[data-page="1"] .inkLayer path';
  const selected = () => page.evaluate(() => window.getSelection().toString().trim());
  const pageBox = () => page.locator('[data-page="1"]').boundingBox();
  // Where `needle` stands on page 1, in viewport px.
  const textAt = (needle) => until(() => page.evaluate((needle) => {
    const span = [...document.querySelectorAll('[data-page="1"] .textLayer span')].find((s) => s.textContent.includes(needle));
    if (!span?.firstChild) return null;
    const off = span.textContent.indexOf(needle), range = document.createRange();
    range.setStart(span.firstChild, off);
    range.setEnd(span.firstChild, off + needle.length);
    const r = range.getBoundingClientRect();
    return { x1: r.left + 1, x2: r.right - 1, y: r.top + r.height / 2 };
  }, needle), { what: `"${needle}" on the text layer` });
  const penOver = async (needle) => { const at = await textAt(needle); await pen(at.x1, at.y, at.x2, at.y); };

  await step("pen: with nothing armed a drag selects text as a mouse's does, the popup opens at its lift, and no ink is written", async () => {
    await penOver("beta gamma");
    assertEq(await selected(), "beta gamma");
    await page.locator(".plainTip").waitFor();
    assertEq(await page.locator(ink).count(), 0, "no ink");
    assertEq(await page.locator(".pdfViewer").evaluate((el) => el.dataset.penSelect), undefined, "the page answers hit tests again");
    assertNoProblems(page);
  });

  await step("pen: a tap selects nothing and lets go of the selection, as a mouse's click", async () => {
    const box = await pageBox();
    await pen(box.x + 300, box.y + 500);
    await until(async () => (await selected()) === "", { what: "the selection gone" });
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the popup gone" });
    assertEq(await page.locator(ink).count(), 0, "no dot of ink");
    assertNoProblems(page);
  });

  await step("pen: a pause mid-drag opens no popup: only the lift ends the selection", async () => {
    const at = await textAt("Alpha beta");
    const press = (type, x, buttons) => cdp.send("Input.dispatchMouseEvent", { type, x, y: at.y, button: type === "mouseMoved" ? "none" : "left", buttons, clickCount: type === "mouseMoved" ? 0 : 1, pointerType: "pen" });
    await press("mousePressed", at.x1, 1);
    for (let i = 1; i <= 8; i++) await press("mouseMoved", at.x1 + (at.x2 - at.x1) * i / 8, 1);
    await until(async () => (await selected()) === "Alpha beta", { what: "the selection growing under the pen" });
    await sleep(500);
    assertEq(await page.locator(".plainTip").count(), 0, "still dragging: no popup");
    await press("mouseReleased", at.x2, 0);
    await page.locator(".plainTip").waitFor();
    const box = await pageBox();
    await pen(box.x + 300, box.y + 500); // let go
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the popup gone" });
    assertNoProblems(page);
  });

  await step("pen: with the text highlighter on the selection is highlighted at its lift, and the bar it gets stays", async () => {
    const button = page.getByRole("button", { name: "Text highlighter", exact: true });
    await button.click();
    await sleep(50); // a mouse's release reads the selection 10 ms on: that of this click, not the pen's
    await penOver("zeta eta");
    await until(async () => {
      const d = await account.api(`/api/blocks/${doc.id}/subtree`);
      return (d.block.children || []).some((b) => b.properties?.quote === "zeta eta");
    }, { what: "the highlight" });
    assertEq(await selected(), "", "the selection is gone into the highlight");
    await page.locator(".plainTip").waitFor();
    await sleep(500); // past the selection's own settling: an empty selection must not take the bar
    assertEq(await page.locator(".plainTip").count(), 1, "the highlight's bar stays");
    assertEq(await page.locator(ink).count(), 0, "no ink");
    await button.click();
    assertEq(await button.getAttribute("aria-pressed"), "false");
    const box = await pageBox();
    await pen(box.x + 300, box.y + 500);
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the bar gone" });
    assertNoProblems(page);
  });

  await step("pen: a lift with a frame still queued leaves the highlight alone, and a palm mid-drag does not stop it; the chat gets the passage", async () => {
    const button = page.getByRole("button", { name: "Text highlighter", exact: true });
    const highlights = async (quote) => ((await account.api(`/api/blocks/${doc.id}/subtree`)).block.children || []).filter((b) => b.properties?.quote === quote).length;
    await button.click();
    // one task: the move's frame is still queued at the lift, which falls just past the selection
    const at = await textAt("kappa lambda");
    await page.locator('[data-page="1"]').evaluate((el, at) => {
      const send = (type, x, buttons) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 58, isPrimary: true,
        button: 0, buttons, clientX: x, clientY: at.y }));
      send("pointerdown", at.x1, 1);
      send("pointermove", at.x2 + 2, 1);
      send("pointerup", at.x2 + 2, 0);
    }, at);
    await until(async () => await highlights("kappa lambda") === 1, { what: "the highlight" });
    await sleep(500);
    assertEq(await selected(), "", "nothing reselects the text after the highlighter took it");
    assertEq(await highlights("kappa lambda"), 1, "one highlight");
    const box = await pageBox();
    await pen(box.x + 300, box.y + 500); // its bar stands over the line below
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the bar gone" });
    const palmOver = await textAt("xi omicron");
    await page.locator('[data-page="1"]').evaluate((el, at) => {
      const send = (type, pointerType, id, x, buttons) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType, pointerId: id, isPrimary: true,
        button: 0, buttons, clientX: x, clientY: at.y }));
      send("pointerdown", "pen", 59, at.x1, 1);
      send("pointermove", "pen", 59, at.x2, 1);
      send("pointerdown", "touch", 60, at.x2 + 60, 1);
      send("pointerup", "touch", 60, at.x2 + 60, 0);
      send("pointerup", "pen", 59, at.x2, 0);
    }, palmOver);
    await until(async () => await highlights("xi omicron") === 1, { what: "the highlight under a palm" });
    const passage = page.locator(".chatSelChips .chatSelChip").filter({ hasText: "PDF passage" });
    await until(async () => (await passage.textContent().catch(() => "")).includes("xi omicron"), { what: "the whole passage on the chat's chip" });
    await button.click();
    await pen(box.x + 300, box.y + 500);
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the bar gone" });
    assertNoProblems(page);
  });

  await step("pen: a press on what keeps its own press still lets go of the popup and the selection", async () => {
    await penOver("gamma delta");
    await page.locator(".plainTip").waitFor();
    await page.locator('[data-page="1"]').evaluate((el) => {
      const box = document.createElement("div");
      box.className = "textBox";
      box.style.cssText = "position:absolute;left:300px;top:500px;width:60px;height:40px;z-index:6";
      el.append(box);
      const send = (type, buttons) => box.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 61, isPrimary: true,
        button: 0, buttons, clientX: el.getBoundingClientRect().left + 320, clientY: el.getBoundingClientRect().top + 520 }));
      send("pointerdown", 1);
      send("pointerup", 0);
      box.remove();
    });
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the popup gone" });
    assertEq(await selected(), "", "the selection gone");
    // an open note gives up the focus as to a mouse's press, or it writes its selection over the pen's
    await page.evaluate(() => {
      const field = document.body.appendChild(document.createElement("input"));
      field.id = "e2eFocus";
      field.focus();
    });
    const at = await textAt("Alpha");
    await pen(at.x1, at.y, at.x2, at.y);
    assertEq(await selected(), "Alpha");
    assertEq(await page.evaluate(() => { const field = document.getElementById("e2eFocus"), had = document.activeElement === field; field.remove(); return had; }), false, "the focus let go");
    const box = await pageBox();
    await pen(box.x + 300, box.y + 500);
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the popup gone" });
    assertNoProblems(page);
  });

  await step("pen: a palm on the next page or in a gap is held too: it takes no press and neither pinches nor pans, and after the lift two fingers pinch", async () => {
    const result = await page.locator(".pdfViewer").evaluate((viewer) => {
      const one = viewer.querySelector('[data-page="1"]'), two = viewer.querySelector('[data-page="2"]');
      const pointer = (el, type, pointerType, id, x = 200) => {
        const event = new PointerEvent(type, { bubbles: true, cancelable: true, pointerType, pointerId: id, isPrimary: true, button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: x, clientY: 300 });
        el.dispatchEvent(event);
        return event.defaultPrevented;
      };
      const pair = (type, dy) => {
        const event = new Event(type, { bubbles: true, cancelable: true });
        const touches = type === "touchend" ? [] : [{ identifier: 1, clientX: 200, clientY: 300 + dy }, { identifier: 2, clientX: 320, clientY: 420 + dy }];
        Object.defineProperties(event, { changedTouches: { value: touches }, touches: { value: touches } });
        viewer.dispatchEvent(event);
        return event.defaultPrevented;
      };
      const moved = () => [...viewer.children].some((c) => c.style.transform);
      pointer(one, "pointerdown", "pen", 62);
      pointer(one, "pointermove", "pen", 62, 260); // past a tap
      one.dispatchEvent(new PointerEvent("lostpointercapture", { bubbles: true, pointerId: 62 })); // what a box's drag does: the pen is still down
      const palmOnNextPage = pointer(two, "pointerdown", "touch", 63);
      pointer(two, "pointerup", "touch", 63);
      pair("touchstart", 0);
      const palmInGap = pair("touchmove", 12);
      const pinchedUnderPen = moved();
      pointer(one, "pointerup", "pen", 62);
      // two touches left of three when the pen lifts: no pinch started under it goes on from there
      pair("touchmove", 24);
      const pinchedFromPen = moved();
      pair("touchend", 0);
      pair("touchstart", 0);
      pair("touchmove", 12);
      const pinchedAfter = moved();
      pair("touchend", 0);
      return { palmOnNextPage, palmInGap, pinchedUnderPen, pinchedFromPen, pinchedAfter };
    });
    assert(result.palmOnNextPage, "a touch on the next page is a palm");
    assert(result.palmInGap, "a touch in a gap does not pan");
    assert(!result.pinchedUnderPen && !result.pinchedFromPen, "nor pinch, nor go on to one");
    assert(result.pinchedAfter, "two fingers pinch again once the pen lifts");
    const box = await pageBox();
    await pen(box.x + 300, box.y + 500);
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the popup gone" });
    assertNoProblems(page);
  });

  await step("pen: in rectangle mode a drag draws the rectangle, not a selection; a tap lets it go", async () => {
    const mode = page.locator('[data-guide="viewer.selectMode"]');
    await mode.click();
    const box = await pageBox();
    await pen(box.x + 80, box.y + 120, box.x + 320, box.y + 300);
    await page.locator(".plainTip").waitFor();
    await page.locator(".pdfAreaMarquee").waitFor();
    assertEq(await selected(), "", "no text selected");
    assertEq(await page.locator(ink).count(), 0, "no ink");
    await pen(box.x + 300, box.y + 500);
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the area's popup gone" });
    await mode.click();
    assertNoProblems(page);
  });

  await step("pen: an armed tool takes the pen over text and rectangle mode alike, and the hand gives it back", async () => {
    await page.click("button[aria-label='Handwriting tools']"); // armed: the stylus pen
    await penOver("Epsilon");
    await until(async () => await page.locator(ink).count() === 1, { what: "a stroke over the text" });
    assertEq(await selected(), "", "no text selected");
    const mode = page.locator('[data-guide="viewer.selectMode"]');
    await mode.click();
    const box = await pageBox();
    await pen(box.x + 80, box.y + 200, box.x + 320, box.y + 240);
    await until(async () => await page.locator(ink).count() === 2, { what: "a stroke in rectangle mode" });
    assertEq(await page.locator(".plainTip").count(), 0, "no area drawn");
    await mode.click();
    await page.getByRole("button", { name: "Hand", exact: true }).click();
    await penOver("Alpha");
    assertEq(await selected(), "Alpha", "the hand: the pen selects again");
    assertEq(await page.locator(ink).count(), 2, "and writes no more");
    // over the stroke on Epsilon and the highlight on zeta eta, which lie above the text
    await pen(box.x + 300, box.y + 500); // the popup stands over the line below: let go of it
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the popup gone" });
    const from = await textAt("Epsilon"), to = await textAt("theta");
    await pen(from.x1, from.y, to.x2, to.y);
    assertEq(await selected(), "Epsilon zeta eta theta", "a selection runs under what lies over the text");
    assertNoProblems(page);
  });

  await step("pen: with nothing armed its eraser end still erases, and a press on a stroke selects it as a mouse's does", async () => {
    const box = await pageBox();
    await pen(box.x + 200, box.y + 220); // the drawn line in rectangle mode, at its middle
    await page.locator(".inkEditMenu").waitFor();
    await page.keyboard.press("Escape");
    await until(async () => await page.locator(".inkEditMenu").count() === 0, { what: "the selection let go" });
    // with a palm already down, whose presence is in the pen's own touch
    await page.locator('[data-page="1"]').evaluate((el) => {
      const r = el.getBoundingClientRect();
      const pointer = (type, pointerType, id, x) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType, pointerId: id, isPrimary: true,
        button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: r.left + x, clientY: r.top + 220 }));
      const touch = (type) => {
        const event = new Event(type, { bubbles: true, cancelable: true }), points = [{ identifier: 1, touchType: "stylus", clientX: r.left + 200, clientY: r.top + 220 }, { identifier: 2, touchType: "direct", clientX: r.left + 500, clientY: r.top + 400 }];
        Object.defineProperties(event, { changedTouches: { value: [points[0]] }, touches: { value: points } });
        el.dispatchEvent(event);
      };
      pointer("pointerdown", "touch", 42, 500);
      pointer("pointerdown", "pen", 43, 200);
      touch("touchstart");
      pointer("pointerup", "pen", 43, 200);
      pointer("pointerup", "touch", 42, 500);
    });
    await page.locator(".inkEditMenu").waitFor();
    await page.keyboard.press("Escape");
    await until(async () => await page.locator(".inkEditMenu").count() === 0, { what: "the selection let go" });
    // an eraser end is buttons 32, which the debugger's pen cannot send
    await page.locator('[data-page="1"]').evaluate((el) => {
      const r = el.getBoundingClientRect();
      const send = (type, x, buttons) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 41, isPrimary: true,
        button: type === "pointermove" ? -1 : 5, buttons, clientX: r.left + x, clientY: r.top + 220 }));
      send("pointerdown", 150, 32);
      for (let x = 160; x <= 260; x += 10) send("pointermove", x, 32);
      send("pointerup", 260, 0);
    });
    await until(async () => await page.locator(ink).count() === 1, { what: "the line erased" });
    assertNoProblems(page);
  });

  await step("pen: a tablet's touches: each is cancelled as an armed pen's is, with or without touchType, fingers are palms until it lifts, and a tap still clicks, once", async () => {
    const box = await pageBox();
    // a desktop's pen sends no touch events and keeps its own click: one, not two
    const clicks = () => page.evaluate(() => window.e2ePenClicks);
    await page.evaluate(() => { window.e2ePenClicks = 0; document.addEventListener("click", (e) => { window.e2ePenClicks += 1; window.e2ePenDetail = e.detail; }, true); });
    await pen(box.x + 300, box.y + 500);
    await until(async () => await clicks() === 1, { what: "a desktop pen's own click" });
    await sleep(100);
    assertEq(await clicks(), 1, "its click, once");
    // a finger's touch beside that pen is none of the pen's: it still clicks once
    await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const pointer = (type, pointerType, pointerId) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType, pointerId,
        isPrimary: true, button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + 300, clientY: box.top + 500 }));
      const event = new Event("touchstart", { bubbles: true, cancelable: true }), contact = { identifier: 1, clientX: box.left + 400, clientY: box.top + 500 };
      Object.defineProperties(event, { changedTouches: { value: [contact] }, touches: { value: [contact] } });
      pointer("pointerdown", "pen", 49);
      pointer("pointerdown", "touch", 50);
      el.dispatchEvent(event);
      pointer("pointerup", "touch", 50);
      pointer("pointerup", "pen", 49);
    });
    await pen(box.x + 300, box.y + 500);
    await until(async () => await clicks() === 2, { what: "its next click" });
    await sleep(100);
    assertEq(await clicks(), 2, "its click, still once");
    await page.evaluate(() => { window.e2ePenClicks = 1; });
    const result = await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const pointer = (type, pointerType, pointerId, x) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType, pointerId,
        isPrimary: true, button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + x, clientY: box.top + 350 }));
      const touch = (type, touchType) => {
        const event = new Event(type, { bubbles: true, cancelable: true });
        const contact = { identifier: 1, touchType, clientX: box.left + 100, clientY: box.top + 350 };
        Object.defineProperties(event, { changedTouches: { value: [contact] }, touches: { value: [contact] } });
        el.dispatchEvent(event);
        return event.defaultPrevented;
      };
      const before = touch("touchstart", "stylus");
      pointer("pointerdown", "pen", 51, 100);
      const start = touch("touchstart", "stylus");
      const move = touch("touchmove", "stylus");
      const untyped = touch("touchmove", undefined); // Chromium has no touchType
      const palm = touch("touchstart", "direct");
      pointer("pointerdown", "touch", 52, 140);
      pointer("pointerup", "touch", 52, 140);
      pointer("pointermove", "pen", 51, 180);
      pointer("pointerup", "pen", 51, 180);
      return { before, start, move, untyped, palm, after: touch("touchstart", "direct") };
    });
    assert(result.before && result.start, "a pen's press is cancelled: no pan, no long press, whichever event comes first");
    assert(result.move && result.untyped, "every move of the pen stops the pan");
    assert(result.palm && !result.after, "fingers are palms while the pen is down, and navigate after it");
    await sleep(100);
    assertEq(await clicks(), 1, "a drag clicks nothing");
    await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const pointer = (type, x) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 55,
        isPrimary: true, button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + x, clientY: box.top + 350 }));
      pointer("pointerdown", 100);
      pointer("pointermove", 103);
      pointer("pointerup", 103);
    });
    await until(async () => await clicks() === 2, { what: "the tap's click, its touch cancelled" });
    await sleep(100);
    assertEq(await clicks(), 2, "the tap's click, once");
    assertEq(await page.evaluate(() => window.e2ePenDetail), 1, "a tap's detail, which a press-and-click tells from a key");
    assertNoProblems(page);
  });

  await step("pen: in rectangle mode a palm leaves the pen's rectangle alone", async () => {
    await page.locator('[data-guide="viewer.selectMode"]').click();
    await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const pointer = (type, pointerType, pointerId, x, y) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType, pointerId,
        isPrimary: true, button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + x, clientY: box.top + y }));
      pointer("pointerdown", "pen", 53, 100, 300);
      pointer("pointerdown", "touch", 54, 400, 500);
      pointer("pointermove", "pen", 53, 300, 420);
      pointer("pointerup", "pen", 53, 300, 420);
    });
    await page.locator(".plainTip").waitFor();
    const box = await pageBox();
    await pen(box.x + 300, box.y + 520);
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the area's popup gone" });
    await page.locator('[data-guide="viewer.selectMode"]').click();
    assertNoProblems(page);
  });

  await step("pen: paper has no text to select, so with nothing armed it is written on", async () => {
    const paperCtx = await account.context(browser, { hasTouch: true });
    const p = await openPage(paperCtx, `${server.base}/?ws=${account.ws}`);
    try {
      await p.click("button[aria-label='Add']");
      await p.getByRole("menuitem", { name: "New notebook" }).or(p.locator("button", { hasText: "New notebook" })).first().click();
      await p.waitForSelector(".nbViewer .nbSheet", { timeout: 15000 });
      const penOnPaper = stylus(await paperCtx.newCDPSession(p));
      const b = await p.locator(".nbSheet").first().boundingBox();
      await penOnPaper(b.x + 80, b.y + 100, b.x + 260, b.y + 130);
      await until(async () => await p.locator(".nbSheet >> nth=0 >> .inkLayer path").count() === 1, { what: "a stroke on the sheet" });
      assertNoProblems(p);
    } finally { await paperCtx.close(); }
  });

  await step("pen: the zoom window's panel has no text either, so with the hand armed it is written on", async () => {
    const zoomCtx = await account.context(browser, { hasTouch: true, viewport: { width: 1280, height: 900 } });
    const p = await openPage(zoomCtx, paperUrl);
    try {
      await waitForPdf(p, 1);
      await p.click("button[aria-label='Handwriting tools']");
      await p.getByRole("button", { name: "Hand", exact: true }).click();
      await p.keyboard.press("z");
      await p.locator(".inkZoomView").waitFor();
      const before = await p.locator(".inkZoomView .inkLayer path").count();
      const b = await p.locator(".inkZoomView").boundingBox();
      await stylus(await zoomCtx.newCDPSession(p))(b.x + 80, b.y + b.height / 2, b.x + 280, b.y + b.height / 2 + 20);
      await until(async () => await p.locator(".inkZoomView .inkLayer path").count() === before + 1, { what: "a stroke in the panel" });
      assertNoProblems(p);
    } finally { await zoomCtx.close(); }
  });

  await step("pen: a drag the system cancels keeps its selection and gets the popup, without the highlighter's commit", async () => {
    const button = page.getByRole("button", { name: "Text highlighter", exact: true });
    await button.click();
    await sleep(50); // a mouse's release reads the selection 10 ms on: that of this click, not the pen's
    const at = await textAt("Alpha beta");
    const cancelled = await page.locator('[data-page="1"]').evaluate((el, at) => {
      const send = (type, x, buttons) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 57, isPrimary: true,
        button: 0, buttons, clientX: x, clientY: at.y }));
      send("pointerdown", at.x1, 1);
      send("pointermove", at.x2, 1);
      return new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() => {
        send("pointercancel", at.x2, 0);
        done(window.getSelection().toString().trim());
      })));
    }, at);
    assertEq(cancelled, "Alpha beta");
    await page.locator(".plainTip").waitFor();
    assertEq(await selected(), "Alpha beta", "the selection stays, not taken by the highlighter");
    await button.click();
    const box = await pageBox();
    await pen(box.x + 300, box.y + 500);
    await until(async () => await page.locator(".plainTip").count() === 0, { what: "the popup gone" });
    assertNoProblems(page);
  });

  await step("pen: Stylus draws on PDFs right away writes with nothing armed; the old preference's stored on does not", async () => {
    const before = await page.locator(ink).count();
    await page.evaluate(() => localStorage.setItem("gamma-ink-auto-pen", "1")); // what every browser has stored
    await page.reload();
    await waitForPdf(page, 1);
    await penOver("Alpha");
    assertEq(await selected(), "Alpha", "the old key's on is no choice of this one");
    assertEq(await page.locator(ink).count(), before, "no ink");
    await page.evaluate(() => localStorage.setItem("gamma-ink-pen-writes-pdf", "1"));
    await page.reload();
    await waitForPdf(page, 1);
    await penOver("beta");
    await until(async () => await page.locator(ink).count() === before + 1, { what: "the pen writing with nothing armed" });
    assertEq(await selected(), "", "no text selected");
    await page.evaluate(() => localStorage.removeItem("gamma-ink-pen-writes-pdf"));
    await page.close();
    await ctx.close();
  });

  for (const platform of ["ipad", "android"]) {
    await step(`pen: ${platform} shell: with nothing armed the page claims nothing, and a gesture from the hand comes back to it`, async () => {
      const { ctx: shellCtx, page: p, shell } = await shellPaper({ alice: account, browser, server, openPage, until, makePdf }, platform, `free-${platform}`);
      try {
        await p.getByRole("button", { name: "Hand", exact: true }).click();
        const pressed = (name) => p.getByRole("button", { name, exact: true }).first().evaluate((el) => el.classList.contains("modeActive"));
        await shell.pdf.evaluate((el) => {
          const box = el.getBoundingClientRect();
          const send = (type, x) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 61, isPrimary: true,
            button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + x, clientY: box.top + 300 }));
          send("pointerdown", 100);
          send("pointermove", 200);
          send("pointerup", 200);
        });
        await sleep(100);
        assertEq((await shell.sent("claim")).length, 0, "the touch stays the page's");
        await shell.receive({ type: "pencil", gesture: "tap", action: "switchEraser" });
        await until(async () => !(await pressed("Hand")), { what: "the eraser armed" });
        await shell.receive({ type: "pencil", gesture: "tap", action: "switchEraser" });
        await until(() => pressed("Hand"), { what: "the hand again, not a pen" });
        assertNoProblems(p);
      } finally { await shellCtx.close(); }
    });
  }
}
