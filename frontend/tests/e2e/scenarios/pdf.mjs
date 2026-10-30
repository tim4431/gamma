// PDF pages: upload + page creation by attachment, the viewer's render and
// text layer, creating a highlight from a text selection (overlay + block
// row + persisted position), the library card, and the search panel hitting
// PDF text on another page.
import { execFileSync } from "node:child_process";
import { closeEditor, tree, same, partialBlockSearch } from "./notes.mjs";
import { PYTHON, arrived, assert, assertEq, fakeAiModels, until } from "../harness.mjs";
import { DEFAULT_PALETTE, darkPageColor, darkPageLine } from "../../../src/shared/model/palette.js";

// Select `needle` inside one text-layer span of `pageNo` and release the
// mouse the way the viewer listens for it (document-level mouseup).
export async function selectPdfText(page, pageNo, needle) {
  const ok = await page.evaluate(([pageNo, needle]) => {
    const spans = Array.from(document.querySelectorAll(`[data-page="${pageNo}"] .textLayer span`));
    const span = spans.find((s) => s.textContent.includes(needle));
    if (!span || !span.firstChild) return false;
    const off = span.textContent.indexOf(needle);
    const range = document.createRange();
    range.setStart(span.firstChild, off);
    range.setEnd(span.firstChild, off + needle.length);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    return true;
  }, [pageNo, needle]);
  if (!ok) throw new Error(`text "${needle}" not found on page ${pageNo}'s text layer`);
}

// The same with the mouse: a drag over the needle's text, released at its
// end. Returns the point it was released at (the selection popup opens by it).
// `highlighted`: the text highlighter is on, so the release highlights the
// selection and clears it; the highlight's saved quote is then the check.
// The text is measured once two readings agree: a page still moving (cards
// settling beside it) would put the release off the text's spans, which
// selects to the end of its line (pdf.js's spans are absolute; only each
// line's <br> is in the flow). The drag also keeps clear of the viewer's
// edges: one that starts within Chromium's 20 px autoscroll belt scrolls
// the viewer under it while it runs, so a span left at an edge by "nearest"
// (the viewer scrolled to show a card) is brought to the middle.
export async function dragSelectPdfText(page, pageNo, needle, { highlighted = false } = {}) {
  const measure = () => page.evaluate(([pageNo, needle]) => {
    const span = [...document.querySelectorAll(`[data-page="${pageNo}"] .textLayer span`)].find((s) => s.textContent.includes(needle));
    if (!span?.firstChild) return null;
    span.scrollIntoView({ block: "nearest" }); // the drag is made in viewport pixels
    const view = span.closest(".pdfViewer")?.getBoundingClientRect();
    const at = span.getBoundingClientRect();
    if (view && (at.top - view.top < 40 || view.bottom - at.bottom < 40)) span.scrollIntoView({ block: "center" });
    const off = span.textContent.indexOf(needle);
    const range = document.createRange();
    range.setStart(span.firstChild, off);
    range.setEnd(span.firstChild, off + needle.length);
    const r = range.getBoundingClientRect();
    return { x1: r.left + 1, x2: r.right - 1, y: r.top + r.height / 2 };
  }, [pageNo, needle]);
  let last = null;
  const box = await until(async () => {
    const next = await measure();
    if (!next) throw new Error(`text "${needle}" not found on page ${pageNo}'s text layer`);
    const still = last && next.x1 === last.x1 && next.x2 === last.x2 && next.y === last.y;
    last = next;
    return still && next;
  }, { what: `"${needle}" to hold still` });
  await page.mouse.move(box.x1, box.y);
  await page.mouse.down();
  await page.mouse.move((box.x1 + box.x2) / 2, box.y, { steps: 4 });
  await page.mouse.move(box.x2, box.y, { steps: 4 });
  await page.mouse.up();
  if (highlighted) return { x: box.x2, y: box.y };
  const got = await page.evaluate(() => window.getSelection().toString().trim());
  if (got !== needle) throw new Error(`the drag over "${needle}" selected "${got}"`);
  return { x: box.x2, y: box.y };
}

export async function waitForPdf(page, pageNo = 1, timeout = 30000) {
  await page.waitForSelector(`[data-page="${pageNo}"] .textLayer span`, { timeout });
}

// The review card's parts in a browser step (pdf/HighlightReview.jsx): a highlight's card, its header, a step's
// button (data-step: accept, place, move, discard), Previous and Next (data-review-nav: prev, next), the count, a
// step's button pressed, the before picture loaded, the card at rest (its highlight scrolled to); `look`, the card
// as it looks (its box, header, picture,
// comparison, count and every button's box, the boxes as offsets from the card's own when `relative`: a step that
// took the highlight elsewhere, the card following it), and `sameLook`, two looks the same within a pixel.
export function reviewCardHelpers(p) {
  const cardOf = (id) => p.locator(`.hlReviewCard[data-review-id="${id}"]`);
  const headOf = (c) => c.locator(".hlReviewHead").textContent();
  const stepOf = (c, key) => c.locator(`.hlReviewActions [data-step="${key}"]`);
  const navOf = (c, dir) => c.locator(`.hlReviewActions [data-review-nav="${dir}"]`);
  const countOf = (c) => c.locator(".hlReviewCount:not(.hlReviewRoom)").textContent();
  const pressed = async (c, key) => (await stepOf(c, key).getAttribute("aria-pressed")) === "true";
  const pictureLoaded = (c) => until(() => c.locator(".hlReviewShot img").evaluate((img) => img.complete && img.naturalWidth > 0,
    null, { timeout: 1000 }), { what: "the before picture loaded", timeout: 15000 });
  const atRest = (c) => {
    let last = "";
    return until(async () => {
      const now = JSON.stringify(await c.boundingBox());
      const same = now === last;
      last = now;
      return same;
    }, { what: "the card at rest", every: 200 });
  };
  const look = (c, relative = false) => c.evaluate((el, relative) => {
    const at = el.getBoundingClientRect();
    const box = (r) => [r.left - (relative ? at.left : 0), r.top - (relative ? at.top : 0), r.width, r.height];
    const img = el.querySelector(".hlReviewShot img");
    return {
      box: box(at), head: el.querySelector(".hlReviewHead").textContent,
      picture: img ? { src: img.getAttribute("src"), loaded: img.complete && img.naturalWidth > 0, box: box(img.getBoundingClientRect()) } : null,
      diff: [...el.querySelectorAll(".hlReviewDiff")].map((d) => d.textContent).join(" | "),
      count: el.querySelector(".hlReviewCount:not(.hlReviewRoom)")?.textContent || "",
      buttons: [...el.querySelectorAll("button")].map((b) => ({ key: b.dataset.step || b.dataset.reviewNav || b.textContent, box: box(b.getBoundingClientRect()) })),
    };
  }, relative);
  const sameLook = (a, b, what) => {
    const near = (x, y) => x.length === y.length && x.every((v, i) => Math.abs(v - y[i]) <= 1);
    assert(near(a.box, b.box), `${what}: the card's box ${JSON.stringify(a.box)} → ${JSON.stringify(b.box)}`);
    assertEq(b.head, a.head, `${what}: its header`);
    assertEq(JSON.stringify(b.picture && { ...b.picture, box: 0 }), JSON.stringify(a.picture && { ...a.picture, box: 0 }), `${what}: its picture`);
    assert(!a.picture || near(a.picture.box, b.picture.box), `${what}: the picture's box ${JSON.stringify(a.picture?.box)} → ${JSON.stringify(b.picture?.box)}`);
    assertEq(b.diff, a.diff, `${what}: its comparison`);
    assertEq(b.count, a.count, `${what}: its count`);
    assertEq(JSON.stringify(b.buttons.map((x) => x.key)), JSON.stringify(a.buttons.map((x) => x.key)), `${what}: its buttons`);
    a.buttons.forEach((x, i) => assert(near(x.box, b.buttons[i].box), `${what}: ${x.key}'s box ${JSON.stringify(x.box)} → ${JSON.stringify(b.buttons[i].box)}`));
  };
  return { cardOf, headOf, stepOf, navOf, countOf, pressed, pictureLoaded, atRest, look, sameLook };
}

export async function pdfScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage, flags }, { alice2, second }) {
  let ctx, page, pageId, docId;
  const account = alice2;

  await step("pdf: upload, create the page by attachment, the viewer renders both pages", async () => {
    const pdf = makePdf([["Quantum entanglement in Rydberg arrays", "Second line of page one"], ["Page two says hello world"]]);
    const up = await account.upload("/api/uploads", pdf, "rydberg.pdf", "application/pdf");
    docId = up.doc_id;
    const created = await account.api(`/api/blocks/by-doc/${docId}`, { method: "POST", body: { default_title: "Rydberg paper", source_url: up.source_url } });
    pageId = created.id;
    ctx = await account.context(browser);
    await fakeAiModels(ctx); // the chat steps below send (their /api/ai/chat is faked)
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await until(async () => (await page.$$("[data-page]")).length >= 2, { what: "two page wrappers" });
    const text = await page.textContent(`[data-page="1"] .textLayer`);
    assert(text.includes("Rydberg"), `text layer: ${text.slice(0, 80)}`);
    assertNoProblems(page);
  });

  // An AI record no registry confirmed carries the red "!" until the user
  // verifies it in the metadata popover; the source file is a read-only row.
  await step("pdf: Verify vouches for an unverified metadata record; the source file is a table row", async () => {
    const meta = { title: "Quantum entanglement in Rydberg arrays", authors: ["A. Writer"], year: "2026", source: "ai", unverified: true };
    await account.api(`/api/blocks/${pageId}`, { method: "PUT", body: { properties: { meta, bibtex: "@article{writer2026,\n  title = {Quantum entanglement in Rydberg arrays}\n}" } } });
    await page.reload();
    await waitForPdf(page, 1);
    await page.locator(".metaWarnDot").waitFor();
    await page.click("button[aria-label='Paper metadata']");
    const pop = page.locator(".metaPopover");
    const row = (key) => pop.locator(".metaRow").filter({ has: page.locator(".metaKey").getByText(key, { exact: true }) });
    assertEq(await row("Source").locator(".metaValWarn").innerText(), "AI-extracted — unverified");
    const path = await row("Source file").locator(".metaPath").innerText();
    assert(path.startsWith("/api/uploads/") && path.includes(docId), `source file row: ${path}`);
    assertEq(await pop.locator("input:not(.metaInput)").count(), 0, "the path is not an edit box");
    await row("Source").getByRole("button", { name: "Verify", exact: true }).click();
    await until(async () => (await account.api(`/api/blocks/${pageId}`)).properties.meta.user_verified === true, { what: "the verified mark saved" });
    await until(async () => (await row("Source").innerText()).includes("AI-extracted — verified by hand"), { what: "the Source row says verified" });
    assertEq(await page.locator(".metaWarnDot").count(), 0, "the red ! is gone");
    assertEq(await row("Source").getByRole("button").count(), 0, "nothing left to verify");
    await page.click("button[aria-label='Paper metadata']");
    // The later steps expect a page without metadata.
    await account.api("/api/metadata/update", { method: "POST", body: { block_id: pageId, meta: {} } });
    assertNoProblems(page);
  });

  // What tied a registry record to the PDF (meta.match) shows on the Source
  // row: a title search hit whose title is only a phrase of the PDF warns,
  // with a hint that says so; one the AI confirmed reads "· confirmed by AI".
  await step("pdf: the Source row says what tied a registry record to the PDF, and warns for a title phrase", async () => {
    const pop = page.locator(".metaPopover");
    const source = pop.locator(".metaRow").filter({ has: page.locator(".metaKey").getByText("Source", { exact: true }) });
    const show = async (match, unverified) => {
      const meta = { title: "Quasi-phase-matching", authors: [], source: "doi", kind: "bookSection",
        doi: "10.1117/3.1002081.ch35", match, unverified };
      await account.api(`/api/blocks/${pageId}`, { method: "PUT", body: { properties: { meta } } });
      await page.reload();
      await waitForPdf(page, 1);
      await page.click("button[aria-label='Paper metadata']");
      await source.waitFor();
    };
    await show("phrase", true);
    await page.locator(".metaWarnDot").first().waitFor();
    assertEq(await source.locator(".metaValWarn").innerText(), "doi.org — unconfirmed");
    const hint = await source.locator(".metaValWarn").getAttribute("title");
    assert(/searching for the PDF's title/.test(hint || ""), `the hint says it was a title search: ${hint}`);
    await page.click("button[aria-label='Paper metadata']");
    await show("ai", false);
    await until(async () => (await source.innerText()).includes("doi.org · confirmed by AI"), { what: "the Source row says the AI confirmed it" });
    assertEq(await page.locator(".metaWarnDot").count(), 0, "no red ! for a confirmed record");
    await page.click("button[aria-label='Paper metadata']");
    // The later steps expect a page without metadata.
    await account.api("/api/metadata/update", { method: "POST", body: { block_id: pageId, meta: {} } });
    assertNoProblems(page);
  });

  // The item type (meta.kind, Zotero's names) is the popover's first row: a
  // record naming none shows the type its fields imply, another choice
  // swaps the rows (a conference paper's venue is its proceedings), and
  // saving stores the type and rebuilds the entry as an @inproceedings.
  await step("pdf: the Type row picks the item type, the rows that go with it and the BibTeX entry type", async () => {
    // The page's own title: saving renames a page still under its automatic title.
    const meta = { title: "Rydberg paper", authors: ["Kaiming He"], year: "2016", venue: "2016 IEEE CVPR", source: "manual" };
    await account.api(`/api/blocks/${pageId}`, { method: "PUT", body: { properties: { meta } } });
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Paper metadata']");
    const pop = page.locator(".metaPopover");
    const keys = async () => (await pop.locator(".metaKey").allInnerTexts()).map((s) => s.trim());
    const typeSelect = pop.getByRole("button", { name: "Item type", exact: true });
    assertEq((await typeSelect.innerText()).trim(), "Journal article", "a venue and no type read as a journal article");
    assert((await keys()).includes("Journal"), `rows: ${await keys()}`);
    assert(!(await keys()).includes("Edited"), "a record from before the stamp says no time");
    await typeSelect.click();
    await page.locator(".uiSelectMenu .ctxMenuItem").getByText("Conference paper", { exact: true }).click();
    await until(async () => (await keys()).includes("Proceedings"), { what: "the venue row reads Proceedings" });
    assert((await keys()).includes("Publisher") && !(await keys()).includes("Volume"), `conference rows: ${await keys()}`);
    if (flags.keep) await page.screenshot({ animations: "disabled", path: `${server.dir}/meta-type-row.png` });
    await pop.getByRole("button", { name: "Save metadata", exact: true }).click();
    const saved = await until(async () => {
      const props = (await account.api(`/api/blocks/${pageId}`)).properties;
      return props.meta?.kind === "conferencePaper" ? props : null;
    }, { what: "the item type saved" });
    assert(saved.bibtex.startsWith("@inproceedings{he2016,") && saved.bibtex.includes("booktitle = {2016 IEEE CVPR}"),
      `entry: ${saved.bibtex}`);
    // the hand edit is stamped, and the popover says when
    await until(async () => (await keys()).includes("Edited"), { what: "the Edited row" });
    await page.click("button[aria-label='Paper metadata']");
    await account.api("/api/metadata/update", { method: "POST", body: { block_id: pageId, meta: {} } });
    assertNoProblems(page);
  });

  // A DOI typed in place of the stored one is looked up by the save, and the
  // status line says what came of it. The suite's server asks no registry
  // (GAMMA_METADATA_LOOKUP=off), so here doi.org knows nothing: the fields
  // stay as typed. The found case is the backend's (test_metadata_update.py).
  await step("pdf: a DOI typed into the popover is looked up when the record is saved", async () => {
    const meta = { title: "Rydberg paper", authors: ["A. Writer"], year: "2026", doi: "10.1103/PhysRevA.99.013437",
      source: "doi", unverified: true };
    await account.api(`/api/blocks/${pageId}`, { method: "PUT", body: { properties: { meta } } });
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Paper metadata']");
    const pop = page.locator(".metaPopover");
    const doi = pop.locator(".metaRow").filter({ has: page.locator(".metaKey").getByText("DOI", { exact: true }) }).locator("input");
    await doi.fill("https://doi.org/10.1103/PhysRevLett.122.223203");
    await pop.getByRole("button", { name: "Save metadata", exact: true }).click();
    await until(async () => (await page.textContent(".statusPill").catch(() => "") || "")
      .includes("doi.org has no record of 10.1103/physrevlett.122.223203; saved as typed."), { what: "the lookup's status line" });
    const saved = (await account.api(`/api/blocks/${pageId}`)).properties.meta;
    assertEq(saved.source, "manual", "saved as typed");
    assertEq(saved.title, "Rydberg paper");
    assertEq(await page.locator(".metaWarnDot").count(), 0, "a hand edit clears the red !");
    await page.click("button[aria-label='Paper metadata']");
    await account.api("/api/metadata/update", { method: "POST", body: { block_id: pageId, meta: {} } });
    assertNoProblems(page);
  });

  // A stored entry may be invalid BibTeX (doi.org's one-line rendering with
  // a month no style defines, cached before the export normalized it). The
  // share popover copies the entry the .bib export prints, not the stored
  // text (docs/dev/import_export.md "BibTeX bibliography").
  await step("pdf: the share popover's Copy BibTeX is the entry the .bib export prints", async () => {
    const meta = { title: "Rydberg paper", authors: ["A. Writer"], year: "2026", source: "manual" };
    const bibtex = "@article{Writer_2026, title={Rydberg & arrays}, author={Writer, A.}, year={2026}, month=Sept }";
    await account.api(`/api/blocks/${pageId}`, { method: "PUT", body: { properties: { meta, bibtex } } });
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Share']");
    const entry = page.locator(".sharePopover .bibtexPre");
    const printed = await until(async () => {
      const text = await entry.innerText().catch(() => "");
      return text.startsWith("@misc{Writer_2026,") ? text : null;
    }, { what: "the printed entry in the share popover" });
    assert(printed.includes("title = {Rydberg \\& arrays}") && printed.includes("month = sep"), `entry: ${printed}`);
    await page.click("button[aria-label='Share']");
    await account.api("/api/metadata/update", { method: "POST", body: { block_id: pageId, meta: {} } });
    assertNoProblems(page);
  });

  // The spans must not inherit the interface language: pdf.js measures them
  // in the PDF's language, and a Chinese <html lang> resolves the generic
  // font families to CJK fonts — spans (and every highlight made from a
  // selection) came out wider than the glyphs they cover.
  await step("pdf: the text layer's geometry does not follow the interface language", async () => {
    const line = "Quantum entanglement in Rydberg arrays";
    const measure = (p) => p.evaluate((line) => {
      const span = [...document.querySelectorAll('[data-page="1"] .textLayer span')].find((s) => s.textContent === line);
      return span && span.getBoundingClientRect().width / span.closest("[data-page]").getBoundingClientRect().width;
    }, line);
    const english = await until(() => measure(page), { what: "the English span" });
    const zhCtx = await account.context(browser, { locale: "zh-CN" });
    try {
      const zh = await openPage(zhCtx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      await waitForPdf(zh, 1);
      assertEq(await zh.evaluate(() => document.documentElement.lang), "zh-CN", "the interface is Chinese");
      const chinese = await until(() => measure(zh), { what: "the Chinese-interface span" });
      assert(Math.abs(chinese / english - 1) < 0.01, `span width ${chinese.toFixed(4)} under zh-CN vs ${english.toFixed(4)} under en`);
      assertNoProblems(zh);
    } finally { await zhCtx.close(); }
  });

  await step("pdf: a text selection makes a highlight (overlay + note row), persisted with its position", async () => {
    await selectPdfText(page, 1, "entanglement in Rydberg");
    await page.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
    // The popup is as wide as its buttons, however many colours the palette has.
    const [left, right] = await page.evaluate(() => {
      const tip = document.querySelector(".plainTip").getBoundingClientRect();
      const btns = [...document.querySelectorAll(".plainTip .colorRow > button")].map((b) => b.getBoundingClientRect());
      return [Math.min(...btns.map((b) => b.left)) - tip.left, tip.right - Math.max(...btns.map((b) => b.right))];
    });
    assert(Math.abs(left - right) < 1, `the popup's space left of its buttons ${left}px, right ${right}px`);
    // A scroll event that leaves the viewer where it stood (a re-layout of
    // the pages sends one) keeps the popup.
    await page.evaluate(() => document.querySelector(".pdfViewer").dispatchEvent(new Event("scroll")));
    await sleep(100);
    assertEq(await page.locator(".plainTip").count(), 1, "a scroll event that moves nothing keeps the popup");
    await page.locator(".plainTip .colorBtn").first().click();
    await page.waitForSelector('[data-page="1"] [data-hl-id]', { timeout: 5000 });
    await page.waitForSelector(".blockRow .blockQuote", { timeout: 5000 });
    const quote = await page.textContent(".blockRow .blockQuote");
    assert(quote.includes("entanglement in Rydberg"), `quote: ${quote}`);
    const saved = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return (d.block.children || []).find((b) => b.properties?.pdf_position) || null;
    }, { what: "highlight block with pdf_position" });
    // The block is the highlight: no second id, the page and its size once.
    const { pdf_position: pos, ...rest } = saved.properties;
    assert(!("highlight_id" in rest) && !("pdf_page" in rest), `no highlight_id or pdf_page: ${JSON.stringify(rest)}`);
    assertEq(rest.color, "#ffe28fa6", "the first swatch, stored as hex with the highlight alpha");
    assertEq(pos.pageNumber, 1, "highlight page");
    assert(pos.width > 0 && pos.height > 0, "the page size the rects are measured in");
    assert(pos.rects.length && pos.rects.every((r) => JSON.stringify(Object.keys(r)) === '["x1","y1","x2","y2"]')
      && JSON.stringify(Object.keys(pos.boundingRect)) === '["x1","y1","x2","y2"]', `rects carry no size or page: ${JSON.stringify(pos)}`);
    assertNoProblems(page);
    await page.reload();
    await waitForPdf(page, 1);
    await page.waitForSelector('[data-page="1"] [data-hl-id]', { timeout: 10000 });
    // The popup is placed on the screen, so a scroll that moves the page closes it.
    await selectPdfText(page, 1, "Second line");
    await page.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
    await page.evaluate(() => { document.querySelector(".pdfViewer").scrollTop += 40; });
    await page.waitForSelector(".plainTip", { state: "detached", timeout: 3000 });
    await page.evaluate(() => { document.querySelector(".pdfViewer").scrollTop = 0; window.getSelection().removeAllRanges(); });
    assertNoProblems(page);
  });

  await step("pdf: clicking the overlay focuses its note row; the row's dot jumps back and the highlight pulses once", async () => {
    await page.click('[data-page="1"] [data-hl-id]');
    await until(async () => (await page.$(".blockRow.focused .blockQuote")) != null, { what: "focused highlight row" });
    await page.click(".blockRow.focused .highlightDotBtn");
    await page.waitForSelector('[data-page="1"] [data-hl-id].pdfHlFlash', { timeout: 3000 });
    await page.waitForSelector('[data-page="1"] [data-hl-id].pdfHlFlash', { state: "detached", timeout: 5000 });
    assertNoProblems(page);
  });

  // With the notes window closed the new highlight's row can't render. The
  // jump to it used to keep "unfolding" the tree as a load, so the highlight
  // was never sent, and the next edit was refused for naming a block the
  // server didn't have.
  await step("pdf: a highlight made with the notes window closed is saved, and so is the next", async () => {
    const toggleNotes = async () => {
      await page.getByRole("button", { name: "View", exact: true }).click();
      await page.locator(".menuPopover").getByRole("menuitemcheckbox", { name: "Notes" }).click();
      await page.keyboard.press("Escape");
    };
    const highlight = async (needle) => {
      await selectPdfText(page, 1, needle);
      await page.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
      await page.locator(".plainTip .colorBtn").first().click();
    };
    const saved = (needle) => until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return (d.block.children || []).some((b) => b.properties?.quote === needle);
    }, { what: `highlight "${needle}" saved` });
    await toggleNotes();
    await highlight("Quantum");
    await saved("Quantum");
    await toggleNotes();
    await page.locator(".blockRow .blockQuote", { hasText: "Quantum" }).waitFor();
    await highlight("Second line");
    await saved("Second line");
    // A collapsed notes window (double-click on its grip) hides the rows the
    // same way; the highlight must still reach the server.
    const grip = page.locator(".dockWindow .dockGrip", { hasText: "Notes" });
    await grip.dblclick();
    await page.locator(".dockWindow.collapsed .dockGrip", { hasText: "Notes" }).waitFor();
    await highlight("arrays");
    await saved("arrays");
    await grip.dblclick();
    await page.locator(".blockRow .blockQuote", { hasText: "arrays" }).waitFor();
    assertNoProblems(page);
  });

  await step("pdf: a highlight with a note shows a badge that opens the note", async () => {
    const data = await account.api(`/api/blocks/${pageId}/subtree`);
    const highlight = data.block.children.find((block) => block.properties?.pdf_position);
    await account.api(`/api/blocks/${highlight.id}`, { method: "PUT", body: { content: "A note on this passage" } });
    await page.reload();
    await waitForPdf(page);
    const badge = page.locator(".pdfNoteBadge").first();
    await badge.waitFor();
    await badge.click();
    await until(async () => (await page.$(".blockRow.focused .blockQuote")) != null, { what: "badge focuses its note" });
    assertNoProblems(page);
  });

  // A note placed on a page but not on a passage (an imported one, say)
  // carries ⊕: the next highlight clicked on the PDF is the one it links to.
  await step("pdf: a note's ⊕ links it to the highlight clicked next", async () => {
    const loose = await account.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "Loose remark",
      properties: { pdf_position: { pageNumber: 1 } } } });
    await page.reload();
    await waitForPdf(page);
    const mark = page.locator('[data-page="1"] div[data-hl-id]').first();
    await mark.waitFor();
    const target = await mark.getAttribute("data-hl-id");
    const row = page.locator(".blockRow", { hasText: "Loose remark" });
    await row.hover(); // ⊕ takes a press only while its row is hovered or focused
    await row.getByTitle("Attach to a PDF highlight").click();
    await page.locator(".attachModeBanner").waitFor();
    await mark.click();
    await page.locator(".attachModeBanner").waitFor({ state: "detached" });
    await row.getByTitle("Unlink highlight").waitFor();
    await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return (d.block.children || []).find((b) => b.id === loose.id)?.properties?.linked_highlight_id === target;
    }, { what: "the note is linked to the clicked highlight" });
    await account.api(`/api/blocks/${loose.id}`, { method: "DELETE" });
    assertNoProblems(page);
  });

  await step("pdf: flipped pages draw every highlight in a dark-tuned colour of its own hue, not screened", async () => {
    const mark = page.locator('[data-page="1"] div[data-hl-id]').first();
    const look = () => mark.evaluate((el) => { const s = getComputedStyle(el); return `${s.backgroundColor} ${s.mixBlendMode}`; });
    assertEq(await look(), "rgba(255, 226, 143, 0.65) multiply", "a light page multiplies the stored colour (#ffe28fa6)");
    const dark = await mark.evaluate((el) => el.style.getPropertyValue("--hl-dark").trim());
    assertEq(dark, darkPageColor("#ffe28fa6"), "the viewer derives the dark variant with the palette module");
    const flip = (on) => page.locator(".pdfViewer").evaluate((el, on) => el.classList.toggle("pdfDark", on), on);
    await flip(true);
    try {
      assertEq(await look(), `${dark} normal`, "the dark-tuned yellow, not screened");
      assertEq(await page.locator(".pdfNoteBadge").first().evaluate((el) => getComputedStyle(el).backgroundColor), "rgb(42, 42, 42)", "a dark note badge");
      // A colour from another app is treated the same: its own hue, dark-tuned.
      await mark.evaluate((el) => {
        el.style.setProperty("--hl", "rgba(255, 0, 0, 0.4)");
        el.style.setProperty("--hl-dark", "rgba(230, 20, 20, 0.3)");
      });
      assertEq(await look(), "rgba(230, 20, 20, 0.3) normal");
    } finally { await flip(false); }
    await page.reload();
    await waitForPdf(page);
    assertNoProblems(page);
  });

  await step("pdf: a changed palette swatch colours new highlights; stored ones keep theirs", async () => {
    // the first swatch of the Highlight group becomes a half-transparent orange
    const palette = JSON.parse(JSON.stringify(DEFAULT_PALETTE));
    palette.groups[0].colors[0] = { hex: "#ff8800", alpha: 0.5 };
    await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { palette } } });
    try {
      await page.reload();
      await waitForPdf(page, 1);
      const first = page.locator(".plainTip .colorBtn").first();
      await selectPdfText(page, 1, "Rydberg arrays");
      await page.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
      assertEq(await first.getAttribute("aria-label"), "Highlight in Amber", "the swatch is named after its nearest colour");
      assertEq(await first.evaluate((el) => getComputedStyle(el).backgroundColor), "rgba(255, 136, 0, 0.5)", "drawn at its alpha");
      await first.click();
      const saved = await until(async () => {
        const d = await account.api(`/api/blocks/${pageId}/subtree`);
        return (d.block.children || []).find((b) => b.properties?.quote === "Rydberg arrays") || null;
      }, { what: "the orange highlight" });
      assertEq(saved.properties.color, "#ff880080", "the new swatch at its own alpha");
      const kept = await account.api(`/api/blocks/${pageId}/subtree`);
      assert((kept.block.children || []).some((b) => b.properties?.color === "#ffe28fa6"), "the earlier highlight keeps its yellow");
      // the recolour menu offers the same swatches
      await page.click(`[data-page="1"] [data-hl-id="${saved.id}"]`, { button: "right" });
      const swatch = page.locator(".ctxMenuColors .colorBtn").nth(1);
      assertEq(await swatch.getAttribute("aria-label"), "Change to Green");
      await swatch.click();
      await until(async () => {
        const d = await account.api(`/api/blocks/${pageId}/subtree`);
        return (d.block.children || []).find((b) => b.id === saved.id)?.properties?.color === "#aaebaaa6";
      }, { what: "the highlight recoloured green" });
      assertNoProblems(page);
    } finally {
      await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { palette: DEFAULT_PALETTE } } });
      await page.reload();
      await waitForPdf(page, 1);
    }
  });

  // The strip's text highlighter: on, a drag over text is a highlight as the
  // mouse lets go, in the colour picked last, with no popup, and its note
  // opens for typing as the popup's does; its button's menu picks the
  // colour, and so does the popup. A paper of its own, in a tab of its own:
  // a drag that starts on a highlight is a click on it, and the steps after
  // this one drag the Rydberg paper's free text.
  await step("pdf: the text highlighter highlights a selection on release in the colour picked last, with no popup", async () => {
    const up = await account.upload("/api/uploads", makePdf([["Alpha beta gamma delta", "Epsilon zeta eta theta"]]), "highlighter.pdf", "application/pdf");
    const doc = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Highlighter paper", source_url: up.source_url } });
    const p = await openPage(ctx, `${server.base}/?page=${doc.id}&ws=${account.ws}`);
    try {
      await waitForPdf(p, 1);
      const button = p.getByRole("button", { name: "Text highlighter", exact: true });
      const made = (needle, color) => until(async () => {
        const d = await account.api(`/api/blocks/${doc.id}/subtree`);
        return (d.block.children || []).some((b) => b.properties?.quote === needle && b.properties?.color === color);
      }, { what: `highlight "${needle}" in ${color}` });
      const profileColor = (color) => until(async () => (await account.api("/api/prefs/profile")).value?.highlighterColor === color,
        { what: `the profile's highlighter colour ${color}` });
      // the button's menu: a colour turns it on
      await button.click({ button: "right" });
      await p.locator(".ctxMenuColors .colorBtn").nth(1).click();
      assertEq(await button.getAttribute("aria-pressed"), "true", "a colour from its menu turns it on");
      await profileColor("#aaebaaa6");
      assertEq(await p.locator(".pdfViewer").evaluate((el) => el.style.getPropertyValue("--selection-color")), "#aaebaaa6",
        "the selection is drawn in its colour");
      await dragSelectPdfText(p, 1, "beta gamma", { highlighted: true });
      await made("beta gamma", "#aaebaaa6");
      assertEq(await p.locator(".plainTip").count(), 0, "no popup");
      assertEq(await p.evaluate(() => window.getSelection().toString()), "", "the selection is gone into the highlight");
      await until(() => p.evaluate(() => !!document.activeElement?.closest(".blockEditorCm")), { what: "the new highlight's note open for typing" });
      // off, the popup is back; its swatch becomes the highlighter's colour
      await button.click();
      assertEq(await button.getAttribute("aria-pressed"), "false");
      await dragSelectPdfText(p, 1, "zeta eta");
      await p.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
      await p.locator(".plainTip .colorBtn").nth(2).click();
      await made("zeta eta", "#9bcdffa6");
      await profileColor("#9bcdffa6");
      await button.click(); // the popup's note is still open for typing, as when you read on
      await dragSelectPdfText(p, 1, "theta", { highlighted: true });
      await made("theta", "#9bcdffa6");
      // the pen's strip and the highlighter put each other away
      await p.locator('[data-guide="pdf.inkButton"]').click();
      await p.locator('[data-guide="ink.toolbar"]').waitFor();
      assertEq(await button.getAttribute("aria-pressed"), "false", "opening the pen's strip turns it off");
      await button.click();
      await p.locator('[data-guide="ink.toolbar"]').waitFor({ state: "detached", timeout: 5000 }); // turning it on closes the pen's strip
      assertNoProblems(p);
    } finally { await p.close(); }
  });

  await step("pdf: interface scale keeps note badges anchored and Tours consistent", async () => {
    // Sizes are compared with interface size 100% (measured first), not pinned in pixels.
    let base;
    for (const scale of [1, 0.7, 1.6]) {
      await page.evaluate((value) => localStorage.setItem("gamma-ui-scale", String(value)), scale);
      await page.reload();
      await waitForPdf(page);
      const badge = page.getByRole("button", { name: "Show highlight note", exact: true }).first();
      await badge.waitFor();
      const measure = async () => badge.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const marks = [...el.closest("[data-page]").querySelectorAll("div[data-hl-id]")]
          .filter((mark) => mark.dataset.hlId === el.dataset.hlId)
          .map((mark) => mark.getBoundingClientRect());
        const end = marks.sort((a, b) => b.top - a.top || b.right - a.right)[0];
        return { width: box.width, dx: box.left - end.right, dy: box.top - end.top };
      });
      // at the passage's end, just after it; zooming the PDF moves it along, same size and offset
      const before = await until(async () => {
        const m = await measure();
        return m.dx >= 0 && m.dx < 6 * scale && Math.abs(m.dy) < 16 * scale ? m : null;
      }, { what: `badge sits at the passage end with interface scale ${scale}` });
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
      await until(async () => {
        const m = await measure();
        return Math.abs(m.width - before.width) < 1 && Math.abs(m.dx - before.dx) < 1 && Math.abs(m.dy - before.dy) < 1;
      }, { what: `badge keeps its place and size when the PDF zooms (interface scale ${scale})` });
      await page.getByRole("button", { name: "Account & settings", exact: true }).click();
      const settings = page.getByRole("menuitem", { name: "Settings…", exact: true });
      const tours = page.locator('[data-guide="account.tour"]');
      await arrived(tours);
      const settingsBox = await settings.boundingBox();
      const toursBox = await tours.boundingBox();
      assert(Math.abs(settingsBox.height - toursBox.height) < 1, "Tours matches adjacent menu controls");
      await tours.click();
      const tour = page.getByRole("menuitem", { name: "Your first paper", exact: true });
      await arrived(tour);
      assert(Math.abs((await tour.boundingBox()).height - toursBox.height) < 1, "submenu scales once");
      await settings.click();
      // Settings opens on Account & sync; the interface size is under Appearance
      await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Appearance", exact: true }).click();
      const font = await page.getByText("Interface size", { exact: true }).evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
      base ||= { font, badge: before.width };
      assert(Math.abs(font / base.font - scale) < 0.02, `ordinary settings text follows interface size (${font} at ${scale})`);
      assert(Math.abs(before.width / base.badge - scale) < 0.08, `the badge follows interface size (${before.width} at ${scale})`);
      await page.getByRole("button", { name: "Close settings", exact: true }).click();
      assertNoProblems(page);
    }
    await page.evaluate(() => localStorage.removeItem("gamma-ui-scale"));
    await page.reload();
    await waitForPdf(page);
  });

  await step("pdf: search finds PDF text on page 2, the details list it, clicking marks it", async () => {
    await page.click("button[aria-label='Search']");
    await page.waitForSelector(".searchPopover .searchInput");
    await page.fill(".searchPopover .searchInput", "hello world");
    // The paper view opens the compact find bar: a match count, no rows.
    await until(async () => (await page.textContent(".searchPopover .searchFindCount").catch(() => "")) === "1/1", { what: "find count 1/1" });
    await page.click("button[aria-label='Toggle result details']");
    const hit = page.locator("button.searchResult", { hasText: "p. 2" }).first();
    try { await hit.waitFor({ timeout: 10000 }); }
    catch (e) { throw new Error(`no "p. 2" hit; popover showed: ${JSON.stringify(await page.textContent(".searchPopover"))}`); }
    await hit.click();
    await page.waitForSelector('[data-page="2"] .pdfFindMark', { timeout: 15000 });
    await page.keyboard.press("Escape");
    assertNoProblems(page);
  });

  // /block-search stops scanning at its time budget and answers `partial`:
  // the notes group says the list is incomplete instead of "No matches.".
  await step("pdf: a notes search the server cut short says so in the notes group", async () => {
    const unroute = await partialBlockSearch(page);
    try {
      await page.click("button[aria-label='Search']");
      await page.waitForSelector(".searchPopover .searchInput");
      const details = page.locator("button[aria-label='Toggle result details']");
      if (!/\bon\b/.test(await details.getAttribute("class"))) await details.click();
      const answered = page.waitForResponse((r) => r.url().includes("/api/block-search?q=zebrafish"));
      await page.fill(".searchPopover .searchInput", "zebrafish");
      await answered;
      const notice = page.locator(".searchPopover .searchHint", { hasText: "Stopped early — refine the search to see more." });
      await notice.waitFor();
      await until(async () => (await page.locator(".searchPopover .searchHint", { hasText: "Searching…" }).count()) === 0, { what: "the search settles" });
      assertEq(await page.locator(".searchPopover .searchSection", { hasText: "Other notes" }).count(), 1, "under the notes group");
      assertEq(await page.locator(".searchPopover .searchHint", { hasText: "No matches." }).count(), 0, "an incomplete search claims no \"No matches.\"");
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    } finally { await unroute(); }
  });

  await step("pdf: AI citation aligns with text and dismisses on outside clicks without creating a note", async () => {
    const before = await account.api(`/api/blocks/${pageId}/subtree`);
    const quote = "Page two says hello world";
    const href = `/?page=${pageId}&pdf_page=2&quote=${encodeURIComponent(quote)}`;
    await account.api(`/api/chats/${pageId}`, { method: "PUT", body: {
      messages: [{ role: "user", text: "Where is the greeting?" },
        { role: "ai", text: `The greeting is here [p. 2](${href}).` }],
    } });
    await page.reload();
    const link = page.locator('a.gammaLink-citation', { hasText: "p. 2" });
    await link.waitFor();
    assert((await link.getAttribute("href")).includes("quote="), "saved citation retains its quote");
    await link.click();
    const mark = page.locator('[data-page="2"] .pdfCitationMark').first();
    await mark.waitFor();
    const aligned = () => page.evaluate(() => {
      const mark = document.querySelector('[data-page="2"] .pdfCitationMark').getBoundingClientRect();
      const span = [...document.querySelectorAll('[data-page="2"] .textLayer span')]
        .find(s => s.textContent.includes("Page two says hello world"));
      const range = document.createRange(); range.selectNodeContents(span);
      const text = range.getBoundingClientRect();
      return Math.abs(mark.left - text.left) < 3 && Math.abs(mark.top - text.top) < 3
        && Math.abs(mark.width - text.width) < 3 && mark.top >= 0 && mark.top < innerHeight;
    });
    await until(aligned, { what: "citation rectangles align with the rendered text" });
    const box = await mark.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    assertEq(await mark.count(), 1, "clicking the highlighted passage keeps it visible");
    const paper = await page.locator('[data-page="2"]').boundingBox();
    await page.mouse.click(paper.x + 8, box.y + box.height / 2);
    await until(async () => await mark.count() === 0, { what: "clicking elsewhere on the PDF dismisses the citation" });
    await link.click();
    await mark.waitFor();
    await page.locator(".chatInputArea").click();
    await until(async () => await mark.count() === 0, { what: "clicking in chat dismisses the citation" });
    await link.click();
    await mark.waitFor();
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await until(async () => await mark.count() === 0, { what: "clicking a viewer control dismisses the citation" });
    await link.click();
    await mark.waitFor();
    await until(aligned, { what: "citation aligns after zoom and reopening" });
    await page.keyboard.press("Escape");
    await until(async () => await page.locator('.pdfCitationMark').count() === 0, { what: "citation dismissed" });
    await link.click();
    await mark.waitFor();
    const after = await account.api(`/api/blocks/${pageId}/subtree`);
    assertEq(JSON.stringify(after.block.children), JSON.stringify(before.block.children), "citation creates no annotations or notes");
    // Direct links (including Ctrl/Cmd-click) restore the passage on load.
    await page.goto(`${server.base}${href}&ws=${account.ws}`);
    await mark.waitFor();
    await until(aligned, { what: "direct citation link resolves on initial load" });
    assertNoProblems(page);
  });

  await step("pdf: a citation pasted into a note is the chat's pill, previews its passage and opens it in place", async () => {
    const quote = "Page two says hello world";
    const note = await account.api("/api/blocks", { method: "POST", body: {
      parent_id: pageId,
      // As pasted from a chat answer (relative), and as copied from a server
      // this library no longer lives on (absolute, another host).
      content: `See [p. 2](/?page=${pageId}&pdf_page=2&quote=${encodeURIComponent(quote)})`
        + ` and [moved](https://old-host.invalid:9001/?page=${pageId}&pdf_page=2&quote=${encodeURIComponent(quote)})`,
    } });
    await page.reload();
    await waitForPdf(page);
    const pill = page.locator(`.blockRow a.gammaLink-citation`, { hasText: "p. 2" });
    await pill.waitFor();
    assertEq(await page.locator(".blockRow a.linkChip").count(), 0, "a Gamma link is never an external link chip");
    assertEq(await page.locator(".blockRow a.citePill").count(), 2, "both citations are pills, the moved one too");
    assertEq(await page.locator(".blockRow a.gammaLinkCard").count(), 0, "a citation is not a card");
    await pill.hover();
    const preview = page.locator(".citePreview");
    await preview.waitFor();
    const title = (await account.api(`/api/blocks/${pageId}/subtree`)).block.content;
    assertEq(await preview.locator(".citePreviewTitle").textContent(), title, "the preview names the cited paper");
    assert((await preview.textContent()).includes(quote), "the preview shows the quote");
    await page.mouse.move(0, 0);
    await preview.waitFor({ state: "detached" });
    const url = page.url();
    await pill.click();
    const mark = page.locator('[data-page="2"] .pdfCitationMark').first();
    await mark.waitFor();
    assertEq(page.url(), url, "the citation opens in place, without navigating");
    await page.keyboard.press("Escape");
    await until(async () => await mark.count() === 0, { what: "citation dismissed" });
    // The same link written against the old server still resolves: the id is
    // what identifies the page, not the host.
    await page.locator(".blockRow a.gammaLink-citation", { hasText: "moved" }).click();
    await mark.waitFor();
    await page.keyboard.press("Escape");
    await account.api(`/api/blocks/${note.id}`, { method: "DELETE" });
    assertNoProblems(page);
  });

  await step("pdf: a citation stays clickable when a stream update arrives during a click", async () => {
    await page.reload();
    await waitForPdf(page);
    // Deliver chunks on demand so the regression cannot depend on timing or an AI provider.
    await page.evaluate(() => {
      const originalFetch = window.fetch;
      window.fetch = (input, init) => {
        if (!String(input).endsWith("/api/ai/chat")) return originalFetch(input, init);
        return Promise.resolve(new Response(new ReadableStream({
          start(controller) {
            window.citationStream = {
              delta(text) { controller.enqueue(new TextEncoder().encode(JSON.stringify({ delta: text }) + "\n")); },
              finish() { controller.close(); window.fetch = originalFetch; },
            };
          },
        }), { headers: { "Content-Type": "application/x-ndjson" } }));
      };
    });
    await page.locator("textarea.chatInputArea").fill("Show a citation while streaming");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await page.waitForFunction(() => !!window.citationStream);
    const href = `/?page=${pageId}&pdf_page=2&quote=${encodeURIComponent("Page two says hello world")}`;
    await page.evaluate(href => window.citationStream.delta(`See [streamed passage](${href}).\n\nContinuing`), href);
    const link = page.locator("a.gammaLink-citation", { hasText: "streamed passage" });
    await link.waitFor();
    await link.scrollIntoViewIfNeeded();
    const box = await link.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.evaluate(() => window.citationStream.delta(" the answer while the mouse is held."));
    await page.getByText("Continuing the answer while the mouse is held.", { exact: true }).waitFor();
    await page.mouse.up();
    await page.locator('[data-page="2"] .pdfCitationMark').first().waitFor({ timeout: 5000 });
    assertEq(await page.getByRole("button", { name: "Stop generating", exact: true }).count(), 1, "citation opens before the stream finishes");
    await page.evaluate(() => window.citationStream.delta(" More text after opening the citation."));
    await page.getByText(/More text after opening the citation\./).waitFor();
    assert(await page.locator('[data-page="2"] .pdfCitationMark').count() > 0, "further chunks keep the passage highlighted");
    await page.evaluate(() => window.citationStream.finish());
    await page.getByRole("button", { name: "Stop generating", exact: true }).waitFor({ state: "detached" });
    assertNoProblems(page);
  });

  await step("pdf: a selection goes to the chat with its page and region, and the reply says where it was placed", async () => {
    await page.reload();
    await waitForPdf(page, 2);
    const requests = [];
    // What the server reports it did with the selection (ai_context.selection_context).
    const coverage = { context: [{ title: "Rydberg paper", doc_id: docId, native: false, native_requested: false,
      partial: true, chars: 900, pages: 0, pages_shown: 0,
      selection: { passages: [{ page: 2, section: "Results", found: false, crop: true, box: [0.1, 0.1, 0.6, 0.2] }] } }] };
    await page.route("**/api/ai/chat", async (route) => {
      requests.push(route.request().postDataJSON());
      await route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify(coverage)}\n${JSON.stringify({ delta: "It says hello." })}\n` });
    });
    try {
      // The chat takes the selection from a mouseup inside the viewer.
      await page.evaluate(() => {
        const span = [...document.querySelectorAll('[data-page="2"] .textLayer span')]
          .find((s) => s.textContent.includes("says hello"));
        const off = span.textContent.indexOf("says hello");
        const range = document.createRange();
        range.setStart(span.firstChild, off);
        range.setEnd(span.firstChild, off + "says hello".length);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        span.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      });
      await page.locator(".chatSelChips").getByText("PDF passage", { exact: true }).waitFor();
      await page.locator(".chatSelChips").getByText("· p. 2", { exact: true }).waitFor();
      assertEq(await page.getByRole("button", { name: "PDF", exact: true }).count(), 1, "a PDF in context offers the PDF switch");
      await page.locator("textarea.chatInputArea").fill("What does this say?");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await until(() => requests.length === 1, { what: "the chat request" });
      const [sel] = requests[0].selections;
      assertEq(sel.text, "says hello");
      assertEq(sel.page, 2, "the page the selection starts on");
      assert(Array.isArray(sel.box) && sel.box.length === 4 && sel.box[0] < sel.box[2] && sel.box[1] < sel.box[3]
        && sel.box.every((v) => v >= 0 && v <= 1), `box as page fractions: ${JSON.stringify(sel.box)}`);
      await page.getByText("Model saw text around p. 2 · Results").waitFor();
      // Opening the pill shows the picture, drawn again from the saved box.
      await page.getByRole("button", { name: "Picture of the selection sent" }).click();
      const crop = page.locator("img.chatCoverageCrop");
      await crop.waitFor();
      await until(() => crop.evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the selection picture loads" });
    } finally {
      await page.unroute("**/api/ai/chat");
    }
    assertNoProblems(page);
  });

  await step("pdf: a passage longer than the message's budget says on its chip what goes, and the reply what went", async () => {
    // A page of eight lines against the smallest single-paper budget (100
    // characters, Settings → AI → Chat → Context size), in a context of its own.
    const lines = Array.from({ length: 8 }, (_, i) => `Line ${i + 1} of a passage longer than its budget.`);
    const up = await account.upload("/api/uploads", makePdf([lines]), "long-passage.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Long passage", source_url: up.source_url } });
    const { value: profile } = await account.api("/api/prefs/profile");
    await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { chatContextChars: 100 } } });
    const longCtx = await account.context(browser);
    try {
      await fakeAiModels(longCtx);
      const longPage = await openPage(longCtx, `${server.base}/?page=${created.id}&ws=${account.ws}`);
      await waitForPdf(longPage, 1);
      const requests = [];
      await longPage.route("**/api/ai/chat", async (route) => {
        const body = route.request().postDataJSON();
        requests.push(body);
        // What the server reports it cut (ai_context.passage_cuts).
        const shortened = { budget: 100, asked: 100,
          items: [{ kind: "pdf", n: 1, of: 1, kept: 100, total: body.selections[0].text.length }] };
        await route.fulfill({ contentType: "application/x-ndjson",
          body: `${JSON.stringify({ shortened })}\n${JSON.stringify({ delta: "A summary." })}\n` });
      });
      // The page's whole text layer, released inside the viewer.
      const selected = await longPage.evaluate(() => {
        const spans = [...document.querySelectorAll('[data-page="1"] .textLayer span')].filter((s) => s.firstChild?.nodeType === 3);
        const last = spans[spans.length - 1];
        const range = document.createRange();
        range.setStart(spans[0].firstChild, 0);
        range.setEnd(last.firstChild, last.firstChild.length);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        last.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
        return window.getSelection().toString().trim().length;
      });
      assert(selected > 300, `the page's text is selected (${selected} characters)`);
      const chip = longPage.locator(".chatSelChips .chatSelChip").filter({ hasText: "PDF passage" });
      await chip.locator(".chatSelChipNote.cut").getByText(`· first 100 of ${selected} characters`, { exact: true }).waitFor();
      await longPage.locator("textarea.chatInputArea").fill("Summarize this");
      await longPage.getByRole("button", { name: "Send", exact: true }).click();
      await until(() => requests.length === 1, { what: "the chat request" });
      assertEq(requests[0].selections[0].text.length, selected, "the whole passage goes; the server keeps what fits");
      assertEq(requests[0].context_char_limit, 100);
      await longPage.getByText(`PDF passage shortened: first 100 of ${selected} characters sent`).waitFor();
      assertNoProblems(longPage);
    } finally {
      await longCtx.close();
      await account.api("/api/prefs/profile", { method: "PUT", body: { value: profile || {} } });
      await account.api(`/api/blocks/${created.id}`, { method: "DELETE" });
    }
  });

  await step("pdf: a PDF that went as text says why, and a notes-only page says nothing", async () => {
    // The server's report (ai_context.gather_inputs): the paper's file was
    // too large to attach; the notes page has no file to ask for.
    const coverage = { context: [
      { title: "Rydberg paper", doc_id: docId, native: false, native_requested: true, native_skipped: "too_large",
        partial: false, chars: 900, pages: 2, pages_shown: 2 },
      { title: "Loose notes", page_id: "n", doc_id: "", native: false, native_requested: false, notes: true,
        partial: false, chars: 300, pages: 0, pages_shown: 0 }] };
    await page.route("**/api/ai/chat", (route) => route.fulfill({ contentType: "application/x-ndjson",
      body: `${JSON.stringify(coverage)}\n${JSON.stringify({ delta: "Sent as text." })}\n` }));
    try {
      await page.locator("textarea.chatInputArea").fill("Is the file attached?");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await page.getByText("Sent as text.").waitFor();
      await page.getByText("PDF too large to attach — sent as text").waitFor();
      assertEq(await page.getByText(/PDF file not accepted/).count(), 0, "no pill blames the provider");
    } finally {
      await page.unroute("**/api/ai/chat");
    }
    assertNoProblems(page);
  });

  await step("pdf: a picked picture is stored and attached by URL, and an area drag attaches the region the server draws", async () => {
    const requests = [];
    await page.route("**/api/ai/chat", async (route) => {
      requests.push(route.request().postDataJSON());
      await route.fulfill({ contentType: "application/x-ndjson", body: `${JSON.stringify({ delta: "A picture." })}\n` });
    });
    try {
      // A picked file goes to POST /api/ai/pictures and comes back stored.
      const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
      await page.locator('.chatComposer input[type="file"]').setInputFiles({ name: "figure.png", mimeType: "image/png", buffer: png });
      const thumbs = page.locator('[data-guide="chat.imageContext"] img');
      await thumbs.first().waitFor();
      assert((await thumbs.first().getAttribute("src")).includes("/api/uploads/"), "the composer shows the stored picture");
      await until(() => thumbs.first().evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the stored picture loads" });
      // Ctrl+drag a rectangle on the page in view (page 2, from the step
      // before): its region goes along, drawn by the server.
      const pageEl = page.locator('[data-page="2"]');
      await pageEl.scrollIntoViewIfNeeded();
      const box = await pageEl.boundingBox();
      const viewport = page.viewportSize();
      const x0 = box.x + 60, y0 = Math.max(box.y + 80, 120);
      assert(y0 + 140 < viewport.height, `page 2 is in view (${JSON.stringify(box)})`);
      await page.keyboard.down("Control");
      await page.mouse.move(x0, y0);
      await page.mouse.down();
      await page.mouse.move(x0 + 100, y0 + 60, { steps: 8 });
      await page.mouse.move(x0 + 200, y0 + 120, { steps: 8 });
      await page.mouse.up();
      await page.keyboard.up("Control");
      await until(async () => await thumbs.count() === 2, { what: "the region joins the pictures" });
      const region = await thumbs.nth(1).getAttribute("src");
      assert(region.includes("/api/ai/page-image/") && region.includes("page=2") && region.includes("box="), `a region URL: ${region}`);
      await until(() => thumbs.nth(1).evaluate((img) => img.complete && img.naturalWidth > 0), { what: "the region picture loads" });
      await page.keyboard.press("Escape");
      await page.locator("textarea.chatInputArea").fill("What are these?");
      await page.getByRole("button", { name: "Send", exact: true }).click();
      await until(() => requests.length === 1, { what: "the chat request" });
      const [sent] = requests;
      assertEq(sent.images.length, 2, "both pictures go with the message");
      assert(sent.images[0].url.startsWith("/api/uploads/") && sent.images[0].kind === "file", JSON.stringify(sent.images[0]));
      assertEq(sent.images[1].kind, "area");
      assertEq(sent.images[1].page, 2);
      assert(Array.isArray(sent.images[1].box) && sent.images[1].box.length === 4, "the region's box");
      assertEq(sent.max_pictures, 12, "the default picture budget");
      // The sent message shows both, and the composer row is empty again.
      await until(async () => await page.locator(".chatBubble.user img.chatMsgImage").count() === 2, { what: "the bubble shows the pictures" });
      assertEq(await thumbs.count(), 0, "the composer's pictures went with the message");
      // Exporting the conversation packs both pictures beside the transcript.
      await page.getByText("A picture.").waitFor();
      const downloadEvent = page.waitForEvent("download");
      await page.getByRole("button", { name: "Export chat", exact: true }).click();
      await page.getByRole("menuitem", { name: "Export conversation", exact: true }).click();
      const download = await downloadEvent;
      assert(download.suggestedFilename().endsWith(".zip"), download.suggestedFilename());
      const zip = JSON.parse(execFileSync(PYTHON, ["-c", [
        "import json, sys, zipfile",
        "z = zipfile.ZipFile(sys.argv[1])",
        "md = next(n for n in z.namelist() if n.endswith('.md'))",
        "print(json.dumps({'names': sorted(z.namelist()), 'md': z.read(md).decode()}))",
      ].join("\n"), await download.path()]).toString());
      const linked = (md) => [...md.matchAll(/!\[[^\]]*\]\((assets\/[^)]+)\)/g)].map((m) => m[1]);
      assert(linked(zip.md).every((name) => zip.names.includes(name)), `every picture is in the zip: ${zip.names}`);
      // (Earlier steps' messages come along, an earlier reply's selection crop among them.)
      const asked = zip.md.slice(zip.md.lastIndexOf("## You"));
      assertEq(linked(asked).length, 2, `both pictures are in the transcript:\n${asked}`);
      assert(asked.includes("![Image file") && asked.includes("![Region of PDF page 2](assets/"), asked);
      assert(asked.includes("What are these?") && asked.includes("## AI\n\nA picture."), asked);
    } finally {
      await page.unroute("**/api/ai/chat");
    }
    assertNoProblems(page);
  });

  await step("pdf: chat paper recommendations and bare identifiers open clickable source links", async () => {
    const doi = "https://doi.org/10.1103/PhysRevA.69.062320";
    await account.api(`/api/chats/${pageId}`, { method: "PUT", body: { messages: [
      { role: "ai", text: `[Cavity quantum electrodynamics](${doi})\n\nDOI: **10.1103/PhysRevA.69.062320**\n\narXiv:cond-mat/0402216` },
    ] } });
    await page.reload();
    const title = page.locator("a.gammaLinkCard", { hasText: "Cavity quantum electrodynamics" });
    await title.waitFor();
    assertEq(await title.getAttribute("href"), doi, "paper title links to the search result's DOI");
    const identifier = page.locator("strong a.gammaLinkCard", { hasText: "10.1103/PhysRevA.69.062320" });
    assertEq(await identifier.getAttribute("href"), doi, "saved bare DOI is clickable");
    assertEq(await page.locator("a.gammaLinkCard", { hasText: "arXiv:cond-mat/0402216" }).getAttribute("href"),
      "https://arxiv.org/abs/cond-mat/0402216", "legacy arXiv identifier is clickable");
    // Verify actual navigation without contacting the publisher.
    const chatUrl = page.url();
    await ctx.route(doi, route => route.fulfill({ contentType: "text/html", body: "<p>Paper source</p>" }));
    try {
      const [source] = await Promise.all([page.waitForEvent("popup"), identifier.click()]);
      await source.waitForLoadState();
      assertEq(source.url(), doi, "source opens in a new tab");
      assertEq(page.url(), chatUrl, "chat remains on the library page");
      await source.close();
    } finally {
      await ctx.unroute(doi);
    }
    assertNoProblems(page);
  });

  await step("pdf: citations open another document and report missing or ambiguous quotes", async () => {
    const quote = "A distinct source sentence in another document.";
    const uploaded = await account.upload("/api/uploads", makePdf([["Repeated source passage.", quote, "Repeated source passage."]]), "citation.pdf", "application/pdf");
    const target = await account.api(`/api/blocks/by-doc/${uploaded.doc_id}`, { method: "POST", body: { default_title: "Citation source", source_url: uploaded.source_url } });
    const href = `/?page=${target.id}&pdf_page=1&quote=${encodeURIComponent(quote)}`;
    await account.api(`/api/chats/${pageId}`, { method: "PUT", body: { messages: [
      { role: "ai", text: `[other source](${href})` },
    ] } });
    await page.reload();
    await page.locator("a.gammaLink-citation", { hasText: "other source" }).click();
    await page.waitForSelector('[data-page="1"] .pdfCitationMark');
    assert((await page.textContent('[data-page="1"] .textLayer')).includes(quote), "citation resolved against the requested document");
    for (const [text, message] of [["This passage does not exist.", "could not be located"], ["Repeated source passage.", "More than one passage"]]) {
      await page.goto(`${server.base}/?page=${target.id}&pdf_page=1&quote=${encodeURIComponent(text)}&ws=${account.ws}`);
      await page.locator('.pdfCitationNotice', { hasText: message }).waitFor();
      assertEq(await page.locator('.pdfCitationMark').count(), 0, "unresolved reference does not highlight guessed text");
      await page.locator(".chatInputArea").click();
      await until(async () => await page.locator('.pdfCitationNotice').count() === 0, { what: "clicking elsewhere dismisses an unresolved reference" });
    }
    assertNoProblems(page);
    await page.goto(`${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page);
  });

  await step("pdf: fuzzy citations align with source glyphs and reject changed numbers or competing passages", async () => {
    const text = "We carefully measured 53 atoms under stable experimental conditions";
    const quote = text.replace("carefully", "carefuly");
    const pdf = makePdf([{ lines: [text], box: [900, 792] }, { lines: [text, text], box: [900, 792] }]);
    const up = await account.upload("/api/uploads", pdf, "fuzzy-citation.pdf", "application/pdf");
    const target = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Fuzzy citation source", source_url: up.source_url } });
    const before = await account.api(`/api/blocks/${target.id}/subtree`);
    const href = (number, value) => `${server.base}/?page=${target.id}&pdf_page=${number}&quote=${encodeURIComponent(value)}&ws=${account.ws}`;
    await page.goto(href(1, quote));
    await page.locator('.pdfCitationNotice', { hasText: "approximate text match" }).waitFor();
    const aligned = () => page.evaluate(text => {
      const mark = document.querySelector('[data-page="1"] .pdfCitationMark')?.getBoundingClientRect();
      const span = [...document.querySelectorAll('[data-page="1"] .textLayer span')].find(s => s.textContent === text);
      if (!mark || !span) return false;
      const range = document.createRange(); range.selectNodeContents(span);
      const box = range.getBoundingClientRect();
      return ['left', 'top', 'width', 'height'].every(k => Math.abs(mark[k] - box[k]) < 3);
    }, text);
    await until(aligned, { what: "approximate citation covers the actual source glyphs" });
    await page.keyboard.press("Escape");
    await until(async () => await page.locator('.pdfCitationMark, .pdfCitationNotice').count() === 0, { what: "fuzzy citation and notice dismiss together" });
    for (const [number, value, message] of [[1, quote.replace("53", "54"), "could not be located"], [2, quote, "More than one passage"]]) {
      await page.goto(href(number, value));
      await page.locator('.pdfCitationNotice', { hasText: message }).waitFor();
      assertEq(await page.locator('.pdfCitationMark').count(), 0, "unsafe or ambiguous approximate citation has no highlight");
    }
    const after = await account.api(`/api/blocks/${target.id}/subtree`);
    assertEq(JSON.stringify(after.block.children), JSON.stringify(before.block.children), "fuzzy citation creates no annotations or notes");
    assertNoProblems(page);
    await page.goto(`${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page);
  });

  // The translate endpoint, answered locally: every text comes back as
  // "译:<text>" in one final NDJSON line (a translation service's shape).
  // The paper's brief, which a job asks for as it starts, is there at once.
  async function mockTranslate(requests) {
    await page.route("**/api/ai/translate", async (route) => {
      const body = route.request().postDataJSON();
      requests.push(body);
      await route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify({ translations: body.texts.map((t) => `译:${t}`), model: "engine:google", cached: false })}\n` });
    });
    await page.unroute("**/api/ai/translate/brief");
    await page.route("**/api/ai/translate/brief", (route) => route.fulfill({ contentType: "application/x-ndjson",
      body: `${JSON.stringify({ ready: true })}\n` }));
  }

  await step("pdf: the translate button translates the page being read, and shows/hides only on a translated one", async () => {
    await page.reload();
    await waitForPdf(page, 2);
    await sleep(1000); // the saved reading position is restored after the first paint
    const requests = [];
    await mockTranslate(requests);
    const button = page.getByRole("button", { name: "Translate", exact: true });
    // Scroll the visible viewer (another tab's stays mounted, hidden) so
    // `pn` is the page being read.
    const goTo = async (pn) => {
      await page.evaluate((pn) => {
        const v = [...document.querySelectorAll(".pdfViewer")].find((el) => el.offsetParent !== null);
        const el = v.querySelector(`[data-page="${pn}"]`);
        v.scrollTop = pn === 1 ? 0 : el.getBoundingClientRect().top - v.getBoundingClientRect().top + v.scrollTop;
        v.dispatchEvent(new Event("scroll"));
      }, pn);
      await until(() => page.evaluate(() => [...document.querySelectorAll("input[aria-label='Current page']")]
        .find((el) => el.offsetParent !== null)?.value).then((v) => v === String(pn)), { what: `page ${pn} is the one being read` });
      await sleep(300);
      assertEq(await page.evaluate(() => [...document.querySelectorAll("input[aria-label='Current page']")]
        .find((el) => el.offsetParent !== null)?.value), String(pn), "the viewer stays on the page");
    };
    try {
      await goTo(1);
      await button.click();
      await page.locator('[data-page="1"] .pdfTransPara').first().waitFor();
      await page.getByRole("button", { name: "Hide translation", exact: true }).waitFor();
      // Page 2 isn't translated yet: the button offers to translate it.
      await goTo(2);
      await button.click();
      await page.locator('[data-page="2"] .pdfTransPara').first().waitFor();
      assert(requests.some((r) => r.texts.some((t) => t.includes("says hello"))), "page 2's text was sent");
      // On a translated page the button hides every page's translation.
      await page.getByRole("button", { name: "Hide translation", exact: true }).click();
      await page.getByRole("button", { name: "Show translation", exact: true }).waitFor();
      assertNoProblems(page);
    } finally {
      await page.unroute("**/api/ai/translate");
      await page.reload(); // drop the translated view: later steps select PDF text
      await waitForPdf(page, 2);
    }
  });

  await step("pdf: the selection popup translates the selected text, or on select once that is on", async () => {
    const requests = [];
    await mockTranslate(requests);
    const { value: profile } = await account.api("/api/prefs/profile");
    try {
      // A mouse selection: the popup opens at the pointer, and a press
      // inside it never moves it (a release there is not a new selection's).
      await dragSelectPdfText(page, 2, "says hello");
      await page.locator(".plainTip").waitFor();
      const corner = () => page.locator(".plainTip").evaluate((el) => { const b = el.getBoundingClientRect(); return `${Math.round(b.left)},${Math.round(b.top)}`; });
      await sleep(100);
      const opened = await corner();
      await page.getByRole("button", { name: "Translate selection", exact: true }).click();
      const body = page.locator(".plainTip .selTransBody");
      await until(async () => (await body.textContent()) === "译:says hello", { what: "the translation under the colors" });
      assertEq(JSON.stringify(requests.at(-1).texts), JSON.stringify(["says hello"]));
      // The page rides along for the paper's brief, never waited for.
      assertEq(requests.at(-1).page_id, pageId, "the selection names its page");
      assertEq(requests.at(-1).wait_brief, false, "a selection does not wait for the brief");
      assertEq(requests.at(-1).keep, true, "its translation may be kept");
      assertEq(await corner(), opened, "the popup stays put on Translate");
      // The header folds the result; clicking into the text keeps the popup.
      await page.locator(".selTransToggle").click();
      assertEq(await body.count(), 0);
      await page.locator(".selTransToggle").click();
      await body.click();
      await sleep(150);
      assertEq(await page.locator(".plainTip").count(), 1, "the popup stays while reading the translation");
      assertEq(await corner(), opened, "and stays put on its header and text");

      // A refusal says why (a used-up shared AI allowance is a 429 whose
      // detail tells what to do): the popup shows the server's reason.
      await page.unroute("**/api/ai/translate");
      const refusal = "Shared AI allowance used up: add your own key under Settings → AI, or come back later.";
      await page.route("**/api/ai/translate", (route) => route.fulfill({ status: 429, json: { detail: refusal } }));
      await selectPdfText(page, 2, "Page two");
      await page.getByRole("button", { name: "Translate selection", exact: true }).click();
      await until(async () => (await body.textContent()) === `Translation failed: ${refusal}`, { what: "the refusal's reason in the popup" });
      assertNoProblems(page, [/429/]);
      await page.unroute("**/api/ai/translate");
      await mockTranslate(requests);

      // Translate on select: the result opens without a click.
      await account.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), selTranslateAuto: true } } });
      await page.reload();
      await waitForPdf(page, 2);
      await until(() => page.evaluate(() => localStorage.getItem("gamma-sel-translate-auto")).then((v) => v === "1" || v === "true"),
        { what: "the profile's translate-on-select" });
      const before = requests.length;
      await selectPdfText(page, 1, "Second line");
      await until(async () => (await page.locator(".plainTip .selTransBody").textContent().catch(() => "")) === "译:Second line",
        { what: "the translation, opened by itself" });
      assertEq(requests.length, before + 1);
      assertNoProblems(page);
    } finally {
      await page.unroute("**/api/ai/translate");
      await account.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), selTranslateAuto: false } } });
      await page.reload();
      await waitForPdf(page);
    }
  });

  // Area-highlight thumbnails are re-cropped from the loaded document. During
  // a paper switch the viewer still holds the previous paper's document (it
  // stays on screen until the new one commits): a crop taken then showed that
  // paper's page under the new paper's note, and stuck — cached by rect alone.
  await step("pdf: a note's area-highlight thumbnail comes from its own paper after switching papers", async () => {
    const rect = { x1: 60, y1: 40, x2: 480, y2: 110 };
    const area = (parent, content) => account.api("/api/blocks", { method: "POST", body: { parent_id: parent, content,
      properties: { quote: "", color: "rgba(255, 229, 100, 0.55)",
        pdf_position: { pageNumber: 2, width: 612, height: 792, boundingRect: rect, rects: [rect], area: true } } } });
    await area(pageId, "rydberg top of page two");
    const other = await account.upload("/api/uploads", makePdf([["Other paper page one"], ["OTHER PAPER TOP LINE"]]), "other.pdf", "application/pdf");
    const otherId = (await account.api(`/api/blocks/by-doc/${other.doc_id}`, { method: "POST", body: { default_title: "Other paper", source_url: other.source_url } })).id;
    await area(otherId, "other top of page two");
    const thumb = (p) => until(async () => (await p.$$eval("img.blockAreaSnap", (imgs) => imgs.map((i) => i.src)))[0] || null,
      { what: "the area thumbnail", timeout: 15000 });
    const switchTo = async (p, title, note) => {
      await p.keyboard.press("Escape"); // nothing else open, focus back on the page
      await p.keyboard.press("Control+p");
      const dialog = p.getByRole("dialog", { name: "Open a page" });
      await dialog.waitFor();
      const input = dialog.getByRole("textbox", { name: "Search pages by title or label" });
      await until(() => input.evaluate((el) => el === document.activeElement), { what: "the palette's input takes focus" });
      await input.fill(title);
      const rows = dialog.locator('[role="option"][data-kind="page"]');
      await until(async () => (await rows.count()) === 1 && new RegExp(title).test(await rows.textContent()), { what: `the "${title}" row` });
      await p.keyboard.press("Enter");
      await p.getByText(note, { exact: true }).waitFor();
    };
    const c = await account.context(browser);
    try {
      // The reference: each paper's thumbnail on a fresh load.
      let p = await openPage(c, `${server.base}/?page=${otherId}&ws=${account.ws}`);
      const otherFresh = await thumb(p);
      await p.close();
      p = await openPage(c, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      const rydberg = await thumb(p);
      assert(rydberg !== otherFresh, "the two papers' crops differ");
      // Switching inside the app: each paper's note shows its own crop.
      await switchTo(p, "Other paper", "other top of page two");
      await until(async () => (await thumb(p)) === otherFresh, { what: "the other paper's own crop", timeout: 15000 });
      await switchTo(p, "Rydberg paper", "rydberg top of page two");
      await until(async () => (await thumb(p)) === rydberg, { what: "the Rydberg paper's own crop", timeout: 15000 });
      assertNoProblems(p);
      await p.close();
    } finally { await c.close(); }
    await account.api(`/api/blocks/${otherId}`, { method: "DELETE" });
  });

  // A highlight's id is its block's: a link region names the highlight it
  // lands on by `link_block_id` (docs/dev/pdf_citations.md).
  await step("pdf: a link region opens the page it links to and lands on the highlight its link_block_id names", async () => {
    const rect = { x1: 60, y1: 40, x2: 480, y2: 60 };
    const at = (pageNumber) => ({ pageNumber, width: 612, height: 792, boundingRect: rect, rects: [rect] });
    const target = await account.api("/api/blocks", { method: "POST", body: { parent_id: pageId, content: "the cited passage",
      properties: { quote: "Page two says hello world", color: "rgba(255, 229, 100, 0.55)", pdf_position: at(2) } } });
    const up = await account.upload("/api/uploads", makePdf([["A citing paper", "see the Rydberg paper"]]), "citing.pdf", "application/pdf");
    const citing = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Citing paper" } });
    const link = await account.api("/api/blocks", { method: "POST", body: { parent_id: citing.id, content: "",
      properties: { quote: "see the Rydberg paper", color: "rgba(140, 180, 255, 0.35)", pdf_position: at(1),
        link_url: "", link_page_id: pageId, link_block_id: target.id } } });
    assertEq(link.kind, "link", "a link region's kind");
    const c = await account.context(browser);
    try {
      const p = await openPage(c, `${server.base}/?page=${citing.id}&ws=${account.ws}`);
      await waitForPdf(p, 1);
      await p.click(`[data-page="1"] [data-hl-id="${link.id}"]`);
      await p.waitForSelector(`[data-page="2"] [data-hl-id="${target.id}"].pdfHlFlash`, { timeout: 15000 });
      assertNoProblems(p);
      await p.close();
    } finally { await c.close(); }
    await account.api(`/api/blocks/${target.id}`, { method: "DELETE" });
    await account.api(`/api/blocks/${citing.id}`, { method: "DELETE" });
  });

  // Settings → Reading › PDFs › Smooth wheel scrolling (shared/lib/smoothWheel.js).
  await step("pdf: smooth wheel scrolling glides one notch over several frames at any zoom; off, it is the browser's jump", async () => {
    const lines = Array.from({ length: 30 }, (_, n) => `Wheel line ${n + 1}`);
    const up = await account.upload("/api/uploads", makePdf(Array.from({ length: 6 }, () => lines)), "wheel.pdf", "application/pdf");
    const doc = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Wheel paper" } });
    try {
      // At a scale other than 1 (a page zoom or the display's) Chromium
      // divides a notch's wheelDeltaY by devicePixelRatio: 96 at 1.25.
      for (const [on, scale] of [[true, 1], [false, 1], [true, 1.25]]) {
        await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { smoothWheel: on } } });
        const c = await account.context(browser, { deviceScaleFactor: scale });
        await c.addInitScript((v) => localStorage.setItem("gamma-smooth-wheel", v), on ? "1" : "0");
        try {
          const p = await openPage(c, `${server.base}/?page=${doc.id}&ws=${account.ws}`);
          await waitForPdf(p, 1);
          const box = await p.locator(".pdfViewer").boundingBox();
          await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
          // The positions one notch passes through, one sample per frame.
          await p.evaluate(() => {
            const v = document.querySelector(".pdfViewer");
            window.wheelTrail = [v.scrollTop];
            const tick = () => { window.wheelTrail.push(v.scrollTop); if (window.wheelTrail.length < 40) requestAnimationFrame(tick); };
            requestAnimationFrame(tick);
          });
          await p.mouse.wheel(0, 200);
          await until(() => p.evaluate(() => window.wheelTrail.length >= 40), { what: "40 frames sampled" });
          const trail = await p.evaluate(() => [...new Set(window.wheelTrail)]);
          assertEq(Math.round(trail.at(-1) - trail[0]), 200 / scale, `one notch scrolls its ${200 / scale} px (${on ? "on" : "off"}, scale ${scale})`);
          if (on) assert(trail.length >= 5, `on: several positions on the way at scale ${scale}, ${trail.join(" ")}`);
          else assertEq(trail.length, 2, `off: one jump, ${trail.join(" ")}`);
          if (on && scale === 1) {
            // A spin, notches closer than the glide: Chromium hands each
            // notch to the page inside a frame, before that frame's animation
            // callbacks, so a glide timed from the notch stood still for a
            // frame per notch (docs/research/pdf-wheel-spin.md).
            await p.evaluate(() => {
              const v = document.querySelector(".pdfViewer");
              window.spinTrail = []; window.spinning = true;
              const tick = () => { window.spinTrail.push([performance.now(), v.scrollTop]); if (window.spinning) requestAnimationFrame(tick); };
              requestAnimationFrame(tick);
            });
            const t0 = await p.evaluate(() => performance.now());
            for (let i = 0; i < 10; i++) { await p.mouse.wheel(0, 200); await sleep(20); }
            const t1 = await p.evaluate(() => performance.now());
            await until(() => p.evaluate(() => document.querySelector(".pdfViewer").scrollTop >= 2200), { what: "the spin's 2000 px" });
            const spin = (await p.evaluate(() => { window.spinning = false; return window.spinTrail; })).filter(([t]) => t > t0 + 40 && t < t1);
            const still = spin.filter(([, y], i) => i > 0 && y === spin[i - 1][1]).length;
            assert(spin.length >= 6 && still <= 1, `a spin moves in every frame: ${still} of ${spin.length} frames stood still`);
          }
          assertNoProblems(p);
          await p.close();
        } finally { await c.close(); }
      }
    } finally {
      await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { smoothWheel: false } } });
      await account.api(`/api/blocks/${doc.id}`, { method: "DELETE" });
    }
  });

  // After the PDF is replaced (docs/dev/highlight_relocation.md): each
  // highlight is found again by its text and moves with it; one whose text
  // the new version changed a little is matched roughly and drawn dashed,
  // pending, until Accept (its review card, the Document menu's Accept all)
  // draws it solid; one whose text is gone is drawn hollow where it was, and
  // a selection moves it by hand. The review card stays the same after a
  // step, nothing on it moving: only the step's button says it is done
  // (Accepted, Moved, Placed), pressed, and pressing it again undoes it.
  // Previous and Next walk the dashed and the hollow ones in document order,
  // round, with those resolved on a card where they stood, so the count
  // stays; a hollow one's card shows where it was. A move started from the
  // card opens it again, Moved. Translate on select is on throughout: the
  // selection that places a moved highlight is not translated. At last, an
  // account that applies only exact matches (relocateMinMatch 100) gets a
  // renumbered citation back lost, with its closest match kept, which its
  // card shows and places.
  await step("pdf: replacing the PDF moves a highlight to its text, dashes one matched roughly until Accept on its review card, hollows one whose text is gone, and a selection moves that one", async () => {
    const lineA = "Quantum dots confine single electrons";
    const lineB = "This sentence is only in the first version";
    // the next version renumbers both citations
    const lineC = "Spin echoes were measured by Hahn [27]";
    const lineD = "Coherence times were reported by Ramsey [31]";
    const up1 = await account.upload("/api/uploads", makePdf([[lineA, lineB, lineC, lineD]]), "v1.pdf", "application/pdf");
    const doc = await account.api(`/api/blocks/by-doc/${up1.doc_id}`, { method: "POST", body: { default_title: "Replaced paper" } });
    // a note placed on a page but not on a passage: its ⊕ turns attach mode on
    await account.api("/api/blocks", { method: "POST", body: { parent_id: doc.id, content: "Loose remark on the paper",
      properties: { pdf_position: { pageNumber: 1 } } } });
    const { value: profile } = await account.api("/api/prefs/profile");
    await account.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), selTranslateAuto: true } } });
    const c = await account.context(browser);
    const translations = [];
    await c.route("**/api/ai/translate", async (route) => {
      const body = route.request().postDataJSON();
      translations.push(body);
      await route.fulfill({ contentType: "application/x-ndjson",
        body: `${JSON.stringify({ translations: body.texts.map((text) => `译:${text}`), model: "engine:google", cached: false })}\n` });
    });
    try {
      const p = await openPage(c, `${server.base}/?page=${doc.id}&ws=${account.ws}`);
      await waitForPdf(p, 1);
      await until(() => p.evaluate(() => localStorage.getItem("gamma-sel-translate-auto")).then((v) => v === "1" || v === "true"),
        { what: "the profile's translate-on-select" });
      let made = 0;
      for (const needle of ["dots confine single", "only in the first version", "echoes were measured by Hahn [27]",
        "times were reported by Ramsey [31]"]) {
        await selectPdfText(p, 1, needle);
        await p.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
        // a selection made to highlight is translated by itself
        await until(async () => (await p.locator(".plainTip .selTransBody").textContent().catch(() => "")) === `译:${needle}`,
          { what: `the translation of "${needle}"` });
        await p.locator(".plainTip .colorBtn").first().click();
        made += 1;
        await until(async () => (await p.$$('[data-page="1"] div[data-hl-id]')).length >= made, { what: `the highlight of "${needle}"` });
      }
      await closeEditor(p); // the new highlight's note was open for typing; a row being edited shows no dot
      const saved = await until(async () => {
        const d = await account.api(`/api/blocks/${doc.id}/subtree`);
        const hls = (d.block.children || []).filter((b) => b.properties?.pdf_position?.rects);
        return hls.length === 4 ? hls : null;
      }, { what: "four saved highlights" });
      const idOf = (text) => saved.find((b) => (b.properties.quote || "").includes(text)).id;
      const [exactId, lostId, roughId, rough2Id] = [idOf("dots confine"), idOf("first version"), idOf("Hahn"), idOf("Ramsey")];
      // the new version: a page of front matter before, the first line kept, the second gone, the third's
      // and the fourth's citations renumbered
      const up2 = await account.upload("/api/uploads", makePdf([["Front matter added in revision"],
        [lineA, "A new sentence replaced the old one", "Spin echoes were measured by Hahn [25]",
          "Coherence times were reported by Ramsey [33]"]]), "v2.pdf", "application/pdf");
      const bound = await account.api(`/api/pages/${doc.id}/attachment`, { method: "PUT", body: { doc_id: up2.doc_id, original_filename: "v2.pdf" } });
      assert(bound.job?.kind === "relocate-highlights", `the replacement started the job: ${JSON.stringify(bound)}`);
      const job = await until(async () => {
        const j = await account.api(`/api/jobs/${bound.job.id}`);
        return ["done", "failed", "cancelled"].includes(j.state) ? j : null;
      }, { what: "the relocation job", timeout: 20000 });
      assertEq(job.state, "done", `the relocation job: ${job.error}`);
      for (const [key, count] of Object.entries({ highlights: 4, moved: 1, kept: 0, lost: 1, approx: 2, changed: 0 })) {
        assertEq(job.result?.[key], count, `result.${key} of ${JSON.stringify(job.result)}`);
      }
      // an account that set neither threshold runs with the defaults, recorded with the run
      assert(job.params?.min_match === 0.8 && job.params?.exact_chars === 12, `the run's thresholds: ${JSON.stringify(job.params)}`);

      // the open tab switched to the new file; the lost highlight is hollow where it was, on page 1
      const lostSel = `[data-page="1"] div[data-hl-id="${lostId}"].pdfHlLost`;
      await p.waitForSelector(lostSel, { timeout: 15000 });
      const look = () => p.$eval(lostSel, (el) => {
        const s = getComputedStyle(el);
        return { fill: s.backgroundColor, top: s.borderTopStyle, bottom: s.borderBottomStyle };
      });
      const hollow = JSON.stringify({ fill: "rgba(0, 0, 0, 0)", top: "dashed", bottom: "dashed" });
      assertEq(JSON.stringify(await look()), hollow, "a lost highlight is a dashed outline with no fill");
      // a flipped page (Flip page colors, Gamma Dark) draws it hollow too
      const flipped = await p.$eval(lostSel, (el) => {
        const viewer = el.closest(".pdfViewer");
        const had = viewer.classList.contains("pdfDark");
        viewer.classList.add("pdfDark");
        const fill = getComputedStyle(el).backgroundColor;
        if (!had) viewer.classList.remove("pdfDark");
        return fill;
      });
      assertEq(flipped, "rgba(0, 0, 0, 0)", "a lost highlight has no fill on a flipped page");
      const dot = await p.$eval(".blockRow .highlightDot.lost", (el) => {
        const s = getComputedStyle(el);
        return { fill: s.backgroundColor, edge: s.borderTopStyle };
      });
      assertEq(JSON.stringify(dot), JSON.stringify({ fill: "rgba(0, 0, 0, 0)", edge: "dashed" }), "its row's dot is hollow");
      // a comment on it (the commented look's underline) leaves it hollow
      await until(async () => (await account.api(`/api/pages/${doc.id}/comments`, { method: "POST",
        body: { block_id: lostId, body: "Where did this sentence go?" } }).catch(() => null))?.thread, { what: "the comment stored" });
      await until(async () => (await p.$(`.pdfNoteBadge[data-hl-id="${lostId}"], .commentCard`)) != null, { what: "the comment shown" });
      assertEq(JSON.stringify(await look()), hollow, "a lost highlight with a comment stays hollow");

      // attach mode and move mode end each other: one banner, the move's, and Escape ends it
      const loose = p.locator(".blockRow", { hasText: "Loose remark on the paper" });
      await loose.hover(); // ⊕ takes a press only while its row is hovered or focused
      await loose.getByTitle("Attach to a PDF highlight").click();
      await p.locator(".attachModeBanner").waitFor();
      await p.click(lostSel, { button: "right" });
      await p.locator(".ctxMenuItem", { hasText: "Move to a selection" }).click();
      await p.waitForSelector(".attachModeBanner[data-move-highlight]", { timeout: 5000 });
      assertEq(await p.locator(".attachModeBanner").count(), 1, "one banner");
      await p.keyboard.press("Escape");
      await p.locator(".attachModeBanner").waitFor({ state: "detached", timeout: 5000 });

      // the renumbered citations' passages, matched roughly on page 2: each its fill under a dashed outline
      // in its colour's line tone, pending, where it was before kept beside it, until Accept makes it solid
      const stored = async (id) => (await account.api(`/api/blocks/${id}`)).properties;
      const exact = (await stored(exactId)).pdf_position;
      assert(exact.pageNumber === 2 && !["approx", "match", "prev"].some((k) => k in exact), `the exact match is solid: ${JSON.stringify(exact)}`);
      const { pdf_position: roughPos, color: roughColor, quote: roughQuote } = await stored(roughId);
      assert(roughPos.pageNumber === 2 && roughPos.approx === true && !roughPos.lost, `the rough match: ${JSON.stringify(roughPos)}`);
      assert(typeof roughPos.match === "number" && roughPos.match >= 0.8 && roughPos.match < 1, `its match: ${roughPos.match}`);
      assert(roughQuote.includes("[25]"), `its quote is respelled to the new file: ${roughQuote}`);
      // where it was: the first file, its page there, its rectangles as page fractions, its quote then
      const before = roughPos.prev;
      assert(before && before.doc_id === up1.doc_id && before.pageNumber === 1 && before.quote.includes("[27]")
        && before.rects.length > 0 && before.rects.every((r) => r.length === 4 && r.every((v) => v >= 0 && v <= 1)),
        `its prev: ${JSON.stringify(before)}`);
      assert((await stored(rough2Id)).pdf_position.approx === true, "the second citation is pending too");
      const percent = Math.round(roughPos.match * 100);
      await until(async () => {
        await p.evaluate(() => document.querySelector('[data-page="2"]')?.scrollIntoView({ block: "start" }));
        return (await p.$('[data-page="2"] .textLayer span')) != null;
      }, { what: "page 2 rendered", timeout: 15000 });
      const roughSel = `[data-page="2"] div[data-hl-id="${roughId}"]`;
      await p.waitForSelector(roughSel, { timeout: 15000 });
      const roughLook = (flip = false) => p.$eval(roughSel, (el, flip) => {
        const viewer = el.closest(".pdfViewer");
        const had = viewer.classList.contains("pdfDark");
        if (flip) viewer.classList.add("pdfDark");
        const s = getComputedStyle(el);
        const out = { filled: s.backgroundColor !== "rgba(0, 0, 0, 0)", outline: s.outlineStyle, line: s.outlineColor, title: el.title };
        if (flip && !had) viewer.classList.remove("pdfDark");
        return out;
      }, flip);
      const line = darkPageLine(roughColor);
      for (const flip of [false, true]) { // a flipped page (Flip page colors, Gamma Dark) draws it dashed too
        const got = await roughLook(flip);
        assertEq(JSON.stringify([got.filled, got.outline, got.line]), JSON.stringify([true, "dashed", line]),
          `a rough match keeps its fill under a dashed outline${flip ? " on a flipped page" : ""}`);
      }
      // its review card says it all, so its rectangles carry no title of their own
      assertEq((await roughLook()).title, "", "a pending highlight has no title");
      // the rows' dots: the fill inside a dashed ring, the percentage in their titles
      const dots = await p.$$eval(".blockRow .highlightDot.approx", (els) => els.map((el) => {
        const s = getComputedStyle(el);
        return { filled: s.backgroundColor !== "rgba(0, 0, 0, 0)", edge: s.borderTopStyle, title: el.closest("button").title };
      }));
      assertEq(dots.length, 2, "two rows with a dashed dot");
      assert(dots.every((d) => d.filled && d.edge === "dashed"), `their dots have a dashed ring: ${JSON.stringify(dots)}`);
      assert(dots.some((d) => d.title.includes(`Matched roughly (${percent}%)`)), `a dot's title: ${JSON.stringify(dots)}`);
      // an exact match has nothing to accept
      await p.click(`[data-page="2"] div[data-hl-id="${exactId}"]`, { button: "right" });
      await p.locator(".ctxMenuItem", { hasText: "Move to a selection" }).waitFor();
      assertEq(await p.locator(".ctxMenuItem", { hasText: "Accept" }).count(), 0, "a solid highlight's menu has no Accept");
      await p.keyboard.press("Escape");
      await p.locator(".ctxMenuItem").first().waitFor({ state: "detached", timeout: 5000 });

      // hovering the rough match opens its review card: how alike, a picture of where it was in the first
      // file with its old rectangles over it, the words that changed, and Accept, Move…, Previous and Next
      const { cardOf, headOf, stepOf, navOf, countOf, pressed, pictureLoaded, atRest, look: cardLook, sameLook } = reviewCardHelpers(p);
      await p.hover(roughSel);
      const card = cardOf(roughId);
      await card.waitFor({ timeout: 5000 });
      assertEq(await headOf(card), `Matched roughly: ${percent}%`, "the card's header");
      await pictureLoaded(card);
      assert((await card.locator(".hlReviewShot.loaded .hlReviewRect").count()) >= 1, "its old rectangles are drawn over the picture");
      assert((await card.locator(".hlReviewDiff del").allTextContents()).join(" ").includes("[27]"), "the old citation struck through");
      assert((await card.locator(".hlReviewDiff ins").allTextContents()).join(" ").includes("[25]"), "the new citation marked");
      // the walk is the page's dashed and hollow highlights in document order: the hollow one on page 1 first
      assertEq(await countOf(card), "2 of 3", "the second of three to review on the page");
      // the row on one line: Accept, Move…, Previous, the count, Next
      const row = await card.locator(".hlReviewActions > *, .hlReviewNav > *").evaluateAll((els) => new Set(els.map((e) => {
        const r = e.getBoundingClientRect();
        return Math.round(r.top + r.height / 2);
      })).size);
      assertEq(row, 1, "the card's buttons on one line");
      // the pointer crosses into the card and it stays; leaving both closes it
      await card.hover();
      await sleep(400);
      assert(await card.isVisible(), "the card stays while the pointer is in it");
      await p.mouse.move(2, 2);
      await card.waitFor({ state: "detached", timeout: 5000 });
      // Next: the second pending highlight's card, scrolled to
      await p.hover(roughSel);
      await card.waitFor({ timeout: 5000 });
      await navOf(card, "next").click();
      const card2 = cardOf(rough2Id);
      await card2.waitFor({ timeout: 5000 });
      assertEq(await countOf(card2), "3 of 3", "Next moves to the third");
      assert((await card2.locator(".hlReviewDiff ins").allTextContents()).join(" ").includes("[33]"), "its own comparison");
      // Accept on its card: drawn solid where it is, the stored position without approx, match, prev or doc_id;
      // the card stays as it was, nothing on it moving, its Accept now Accepted, pressed
      const rough2Pos = (await stored(rough2Id)).pdf_position;
      const rough2Sel = `[data-page="2"] div[data-hl-id="${rough2Id}"]`;
      const outlineOf = (sel) => p.$eval(sel, (el) => getComputedStyle(el).outlineStyle);
      const acceptanceSaved = () => until(async () => {
        const pos = (await stored(rough2Id)).pdf_position;
        return !["approx", "match", "prev", "doc_id"].some((k) => k in pos) ? pos : null;
      }, { what: "the acceptance saved" });
      await pictureLoaded(card2);
      await atRest(card2);
      const pending2 = await cardLook(card2);
      assertEq(await stepOf(card2, "accept").locator(".hlReviewFace:not(.hlReviewRoom)").textContent(), "Accept", "Accept before");
      await stepOf(card2, "accept").click();
      await until(() => pressed(card2, "accept"), { what: "Accept pressed" });
      assertEq(await stepOf(card2, "accept").locator(".hlReviewFace:not(.hlReviewRoom)").textContent(), "Accepted", "it reads Accepted");
      assert((await stepOf(card2, "accept").getAttribute("title")).includes("press again to undo"), "its title says pressing it undoes it");
      await sleep(200);
      sameLook(pending2, await cardLook(card2), "after Accept");
      assert(await stepOf(card2, "move").isEnabled(), "Move… still moves it");
      await until(async () => (await outlineOf(rough2Sel)) === "none", { what: "the accepted highlight drawn solid" });
      let accepted = await acceptanceSaved();
      assertEq(JSON.stringify(accepted.rects), JSON.stringify(rough2Pos.rects), "it stays where it was matched");
      assertEq(await p.locator(".blockRow .highlightDot.approx").count(), 1, "its row's dot is solid");
      // Accepted pressed again: dashed and pending again as it was (one more tree edit, saved), the card as before;
      // Accept again resolves it
      await stepOf(card2, "accept").click();
      await until(async () => !(await pressed(card2, "accept")), { what: "Accept not pressed" });
      await until(async () => (await outlineOf(rough2Sel)) === "dashed", { what: "the highlight dashed again" });
      await until(async () => {
        const pos = (await stored(rough2Id)).pdf_position;
        return pos.approx === true && pos.match === rough2Pos.match && pos.prev?.doc_id === rough2Pos.prev.doc_id
          && JSON.stringify(pos.rects) === JSON.stringify(rough2Pos.rects);
      }, { what: "the undo saved" });
      assertEq(await stepOf(card2, "accept").locator(".hlReviewFace:not(.hlReviewRoom)").textContent(), "Accept", "Accept again");
      sameLook(pending2, await cardLook(card2), "after its undo");
      await stepOf(card2, "accept").click();
      await until(() => pressed(card2, "accept"), { what: "accepted again" });
      accepted = await acceptanceSaved();
      // hovering another dashed highlight leaves the accepted card where it is
      await p.hover(roughSel);
      await sleep(500);
      await p.mouse.move(2, 2);
      await sleep(400);
      assertEq(await cardOf(roughId).count(), 0, "no card opens in its place");
      assert(await pressed(card2, "accept"), "the accepted card stays");
      // Move… from the accepted card and a selection: Moved, pressed, the card as before. The app's own undo
      // (Ctrl+Z) takes the move back, leaving the highlight as Accept left it: the card stays on it, Accepted
      // pressed, the count as it was, and its redo (Ctrl+Y) brings Moved back. Moved pressed again, the move is
      // undone and the card is back on the acceptance, Accepted pressed
      await stepOf(card2, "move").click();
      await p.waitForSelector(`.attachModeBanner[data-move-highlight="${rough2Id}"]`, { timeout: 5000 });
      await selectPdfText(p, 2, "replaced the old one");
      await p.waitForSelector(".plainTip .tipMoveBtn", { timeout: 5000 });
      await p.locator(".plainTip .tipMoveBtn").click();
      await until(() => pressed(card2, "move").catch(() => false), { what: "the card saying it moved" });
      assertEq(await stepOf(card2, "move").locator(".hlReviewFace:not(.hlReviewRoom)").textContent(), "Moved", "Move… reads Moved");
      assertEq(await headOf(card2), `Matched roughly: ${Math.round(rough2Pos.match * 100)}%`, "the card as it was before the steps");
      assert(!(await pressed(card2, "accept")) && !(await stepOf(card2, "accept").isEnabled()), "Accept does not apply to a moved one");
      await until(async () => (await stored(rough2Id)).quote === "replaced the old one", { what: "the move saved" });
      await p.keyboard.press("Control+z");
      await until(async () => {
        const { pdf_position: pos, quote } = await stored(rough2Id);
        return JSON.stringify(pos.rects) === JSON.stringify(accepted.rects) && !("approx" in pos) && quote.includes("Ramsey [33]");
      }, { what: "Ctrl+Z's undo of the move saved" });
      await until(() => pressed(card2, "accept"), { what: "the card on the acceptance after Ctrl+Z" });
      assert(!(await pressed(card2, "move")) && await stepOf(card2, "move").isEnabled(), "Move… again after Ctrl+Z");
      assertEq(await countOf(card2), "3 of 3", "the count stays after Ctrl+Z");
      await p.keyboard.press("Control+y");
      await until(() => pressed(card2, "move"), { what: "Moved again after Ctrl+Y" });
      await until(async () => (await stored(rough2Id)).quote === "replaced the old one", { what: "the redo saved" });
      await stepOf(card2, "move").click();
      await until(() => pressed(card2, "accept").catch(() => false), { what: "the card back on the acceptance" });
      assert(!(await pressed(card2, "move")) && await stepOf(card2, "move").isEnabled(), "Move… again");
      await until(async () => {
        const { pdf_position: pos, quote } = await stored(rough2Id);
        return pos.pageNumber === accepted.pageNumber && JSON.stringify(pos.rects) === JSON.stringify(accepted.rects)
          && !["approx", "match", "prev", "doc_id"].some((k) => k in pos) && quote.includes("Ramsey [33]");
      }, { what: "the move taken back, saved" });
      // Next from it: round from the last to the first, the hollow one on page 1; the accepted one stays in the walk
      await navOf(card2, "next").click();
      const lostCard = cardOf(lostId);
      await lostCard.waitFor({ timeout: 5000 });
      assertEq(await headOf(lostCard), "Not found in this version", "a hollow highlight's card");
      assertEq(await countOf(lostCard), "1 of 3", "a step changes neither the count nor the walk");
      assertEq(await stepOf(lostCard, "accept").count(), 0, "nothing to accept on a hollow one");
      assert(await stepOf(lostCard, "move").isVisible(), "Move… is offered");
      // where it was: a picture of the first file, which its rectangles were measured on, with them drawn over it
      const lostPos = (await stored(lostId)).pdf_position;
      assert(lostPos.lost === true && lostPos.doc_id === up1.doc_id, `the lost one names the file it was measured on: ${JSON.stringify(lostPos)}`);
      await pictureLoaded(lostCard);
      assert((await lostCard.locator(".hlReviewShot img").getAttribute("src")).includes(`/ai/selection-crop/${up1.doc_id}?`),
        "the picture is of the first file");
      assert((await lostCard.locator(".hlReviewShot.loaded .hlReviewRect").count()) >= 1, "its rectangles are drawn over it");
      // Next goes on to the rough one; Previous goes back, and round from the first to the last: the accepted one,
      // reached again, shows Accepted, pressed; Escape closes it
      await navOf(lostCard, "next").click();
      await card.waitFor({ timeout: 5000 });
      assertEq(await countOf(card), "2 of 3", "the second of three");
      await navOf(card, "prev").click();
      await lostCard.waitFor({ timeout: 5000 });
      assertEq(await countOf(lostCard), "1 of 3", "Previous goes back");
      await navOf(lostCard, "prev").click();
      await card2.waitFor({ timeout: 5000 });
      assertEq(await countOf(card2), "3 of 3", "Previous from the first is the last");
      assert(await pressed(card2, "accept"), "the accepted one shows Accepted");
      assertEq(await headOf(card2), `Matched roughly: ${Math.round(rough2Pos.match * 100)}%`, "as its card was before the step");
      await p.keyboard.press("Escape");
      await card2.waitFor({ state: "detached", timeout: 5000 });
      // hovering the hollow highlight opens its card too, and leaving closes it
      await p.evaluate(() => document.querySelector('[data-page="1"]')?.scrollIntoView({ block: "start" }));
      await p.hover(lostSel);
      await lostCard.waitFor({ timeout: 5000 });
      assertEq(await headOf(lostCard), "Not found in this version", "the hovered hollow highlight's card");
      await pictureLoaded(lostCard);
      await p.mouse.move(2, 2);
      await lostCard.waitFor({ state: "detached", timeout: 5000 });

      // the Document menu: Review counts the ones left to check and opens the first (the hollow one), Accept all
      // accepts the dashed rest as one edit
      await p.click("button[aria-label='Document']");
      await p.locator(".attachPopover .ctxMenuItem", { hasText: "Review 2 highlights" }).click();
      await lostCard.waitFor({ timeout: 5000 });
      await p.keyboard.press("Escape");
      await lostCard.waitFor({ state: "detached", timeout: 5000 });
      await p.click("button[aria-label='Document']");
      await p.locator(".attachPopover .ctxMenuItem", { hasText: "Accept 1 rough match" }).click();
      await until(async () => (await roughLook()).outline === "none", { what: "the rest drawn solid" });
      assertEq(await p.locator(".blockRow .highlightDot.approx").count(), 0, "no dashed dot is left");
      const confirmed = await until(async () => {
        const pos = (await stored(roughId)).pdf_position;
        return !["approx", "match", "prev", "doc_id"].some((k) => k in pos) ? pos : null;
      }, { what: "Accept all saved" });
      assertEq(JSON.stringify(confirmed.rects), JSON.stringify(roughPos.rects), "it stays where it was matched");
      await p.click("button[aria-label='Document']");
      await p.locator(".attachPopover .ctxMenuItem", { hasText: "Find highlights in this PDF" }).waitFor();
      assertEq(await p.locator(".attachPopover .ctxMenuItem", { hasText: "rough match" }).count(), 0, "no rough match is left to accept");
      assertEq(await p.locator(".attachPopover .ctxMenuItem", { hasText: "Review 1 highlight" }).count(), 1, "the hollow one is left to review");
      await p.keyboard.press("Escape");

      // Move… on the hollow one's card: the card makes way for the selection, then opens again on the moved
      // highlight as it was, Move… now Moved; nothing is left to check, so the count says so; Moved pressed again
      // puts it back, hollow where it was
      await p.evaluate(() => document.querySelector('[data-page="1"]')?.scrollIntoView({ block: "start" }));
      await p.hover(lostSel);
      await lostCard.waitFor({ timeout: 5000 });
      assertEq(await countOf(lostCard), "1 of 2", "the hollow one and the one accepted on its card (Accept all's is not in the walk)");
      await stepOf(lostCard, "move").click();
      await p.waitForSelector(`.attachModeBanner[data-move-highlight="${lostId}"]`, { timeout: 5000 });
      assertEq(await lostCard.count(), 0, "the card makes way for the selection");
      await until(async () => {
        await p.evaluate(() => document.querySelector('[data-page="2"]')?.scrollIntoView({ block: "start" }));
        return (await p.$('[data-page="2"] .textLayer span')) != null;
      }, { what: "page 2 rendered", timeout: 15000 });
      await selectPdfText(p, 2, "new sentence replaced");
      await p.waitForSelector(".plainTip .tipMoveBtn", { timeout: 5000 });
      await p.locator(".plainTip .tipMoveBtn").click();
      await lostCard.waitFor({ timeout: 5000 });
      await until(() => pressed(lostCard, "move"), { what: "the card saying it moved" });
      assertEq(await headOf(lostCard), "Not found in this version", "the card as it was");
      assertEq(await countOf(lostCard), "All reviewed", "nothing left to check");
      assertEq(await lostCard.locator(".hlReviewActions button", { hasText: "Close" }).count(), 0, "no Close");
      await until(async () => {
        const pos = (await stored(lostId)).pdf_position;
        return pos.pageNumber === 2 && !pos.lost;
      }, { what: "the move from the card saved" });
      await stepOf(lostCard, "move").click();
      await until(async () => !(await pressed(lostCard, "move")), { what: "the card on the hollow one again" });
      assertEq(await countOf(lostCard), "1 of 2", "one to check again");
      await until(async () => {
        const pos = (await stored(lostId)).pdf_position;
        return pos.lost === true && pos.pageNumber === 1 && pos.doc_id === up1.doc_id;
      }, { what: "the move taken back, saved" });
      await p.waitForSelector(lostSel, { timeout: 5000 });
      await p.keyboard.press("Escape");
      await lostCard.waitFor({ state: "detached", timeout: 5000 });

      // move it by hand from its row's button: a selection on page 2 is its place
      const lostRow = p.locator(".blockRow", { has: p.locator(".highlightDot.lost") });
      await lostRow.hover(); // its buttons take a press only while the row is hovered or focused
      await lostRow.locator(".attachModeBtn[aria-label='Move to a selection on the PDF']").click();
      await p.waitForSelector(`.attachModeBanner[data-move-highlight="${lostId}"]`, { timeout: 5000 });
      // page 2 holds the moved highlight and the new sentence
      await until(async () => {
        await p.evaluate(() => document.querySelector('[data-page="2"]')?.scrollIntoView({ block: "start" }));
        return (await p.$('[data-page="2"] .textLayer span')) != null;
      }, { what: "page 2 rendered", timeout: 15000 });
      await until(async () => (await p.$$('[data-page="2"] div[data-hl-id]:not(.pdfHlLost)')).length > 0, { what: "the moved highlight on page 2" });
      const asked = translations.length;
      await selectPdfText(p, 2, "new sentence replaced");
      await p.waitForSelector(".plainTip .tipMoveBtn", { timeout: 5000 });
      assert(!(await p.$(".plainTip .colorBtn:not(.tipMoveBtn)")), "the tip offers the move alone");
      await sleep(300);
      assertEq(translations.length, asked, "the move's selection is not translated");
      await p.locator(".plainTip .tipMoveBtn").click();
      await p.waitForSelector(".attachModeBanner[data-move-highlight]", { state: "detached", timeout: 5000 });
      await until(async () => !(await p.$("[data-hl-id].pdfHlLost")) && (await p.$(`[data-page="2"] div[data-hl-id="${lostId}"]`)) != null,
        { what: "the moved highlight on page 2, nothing hollow" });
      const moved = await until(async () => {
        const b = await account.api(`/api/blocks/${lostId}`);
        return b.properties.pdf_position.pageNumber === 2 && !b.properties.pdf_position.lost ? b : null;
      }, { what: "the move saved" });
      assert((moved.properties.quote || "").includes("new sentence replaced"), `quote: ${moved.properties.quote}`);

      // moved again onto a box (an area drag), it becomes an area with no quote
      await p.click(`[data-page="2"] div[data-hl-id="${lostId}"]`, { button: "right" });
      await p.locator(".ctxMenuItem", { hasText: "Move to a selection" }).click();
      await p.waitForSelector(".attachModeBanner[data-move-highlight]", { timeout: 5000 });
      const pageEl = p.locator('[data-page="2"]');
      await pageEl.evaluate((el) => el.scrollIntoView({ block: "center" }));
      await sleep(300);
      const box = await pageEl.boundingBox();
      const x0 = box.x + 80, y0 = box.y + box.height / 2 - 60;
      assert(y0 > 0 && y0 + 140 < p.viewportSize().height, `page 2's middle is in view (${JSON.stringify(box)})`);
      await p.keyboard.down("Control");
      await p.mouse.move(x0, y0);
      await p.mouse.down();
      await p.mouse.move(x0 + 100, y0 + 60, { steps: 8 });
      await p.mouse.move(x0 + 200, y0 + 120, { steps: 8 });
      await p.mouse.up();
      await p.keyboard.up("Control");
      await p.waitForSelector(".plainTip .tipMoveBtn", { timeout: 5000 });
      await p.locator(".plainTip .tipMoveBtn").click();
      await p.waitForSelector(".attachModeBanner[data-move-highlight]", { state: "detached", timeout: 5000 });
      const boxed = await until(async () => {
        const b = await account.api(`/api/blocks/${lostId}`);
        return b.properties.pdf_position.area ? b : null;
      }, { what: "the area move saved" });
      assert(!boxed.properties.quote, `an area keeps no quote: ${boxed.properties.quote}`);
      // the box was only the highlight's new place: no picture of it waits in the chat
      await p.locator("textarea.chatInputArea").waitFor();
      assertEq(await p.locator('[data-guide="chat.imageContext"] img').count(), 0, "the move's area drag puts no picture in the chat");

      // an account that applies only exact matches (Settings → Reading & editing › Replacing a PDF, the
      // profile's relocateMinMatch): the next version renumbers the first citation again, and its
      // highlight is left lost where it was instead of matched roughly
      await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { relocateMinMatch: 100 } } });
      const up3 = await account.upload("/api/uploads", makePdf([["Front matter added in revision"],
        [lineA, "A new sentence replaced the old one", "Spin echoes were measured by Hahn [26]",
          "Coherence times were reported by Ramsey [33]"]]), "v3.pdf", "application/pdf");
      const bound3 = await account.api(`/api/pages/${doc.id}/attachment`, { method: "PUT", body: { doc_id: up3.doc_id, original_filename: "v3.pdf" } });
      assert(bound3.job?.kind === "relocate-highlights", `the third version started the job: ${JSON.stringify(bound3)}`);
      const job3 = await until(async () => {
        const j = await account.api(`/api/jobs/${bound3.job.id}`);
        return ["done", "failed", "cancelled"].includes(j.state) ? j : null;
      }, { what: "the exact-only relocation job", timeout: 20000 });
      assertEq(job3.state, "done", `the exact-only relocation job: ${job3.error}`);
      assert(job3.params?.min_match === 1 && job3.result?.min_match === 1,
        `the run took the account's 100%: ${JSON.stringify({ params: job3.params, result: job3.result })}`);
      assertEq(job3.result?.lost, 1, `one not found: ${JSON.stringify(job3.result)}`);
      const strict = (await stored(roughId)).pdf_position;
      assert(strict.lost === true && !("prev" in strict), `the renumbered one is lost, with no prev: ${JSON.stringify(strict)}`);
      // its best place read too unlike it for the account's 100%, and alike enough to keep as its closest match:
      // that place in the third version, in the stored shape, how alike, and its text there
      const near = strict.closest;
      assert(near && near.pageNumber === 2 && near.width > 0 && near.height > 0 && near.rects?.length > 0
        && near.match >= 0.5 && near.match < 1 && near.quote.includes("[26]"), `its closest match: ${JSON.stringify(near)}`);
      assertEq(strict.doc_id, up2.doc_id, "it names the second version, which its rectangles were measured on");
      const strictSel = `div[data-hl-id="${roughId}"].pdfHlLost`;
      await p.waitForSelector(strictSel, { timeout: 15000 });
      // its card: not found, where it was (a picture of the second version), the closest match's percentage and
      // the stored quote against its text
      await p.hover(strictSel);
      const card3 = cardOf(roughId);
      await card3.waitFor({ timeout: 5000 });
      assertEq(await headOf(card3), "Not found in this version", "the lost one's card");
      assertEq(await card3.locator(".hlReviewClosestHead .hlReviewLabel").textContent(), `Closest match: ${Math.round(near.match * 100)}%`,
        "how alike its closest match reads");
      assert((await card3.locator(".hlReviewDiff del").allTextContents()).join(" ").includes("[25]"), "its stored citation struck through");
      assert((await card3.locator(".hlReviewDiff ins").allTextContents()).join(" ").includes("[26]"), "the candidate's marked");
      await pictureLoaded(card3);
      assert((await card3.locator(".hlReviewShot img").getAttribute("src")).includes(`/ai/selection-crop/${up2.doc_id}?`),
        "the picture is of the second version");
      // Show outlines the closest match on its page while the card is open
      await card3.locator(".hlReviewClosestHead button", { hasText: "Show" }).click();
      await p.waitForSelector(`[data-page="2"] .pdfHlGhost[data-review-ghost="${roughId}"]`, { timeout: 5000 });
      assertEq(await p.locator(".pdfHlGhost").count(), near.rects.length, "one outline per rectangle");
      // Use closest match: solid on the candidate's place with its text, as one tree edit; the card stays as it was,
      // following the highlight there (the same size, the same picture and comparison, every button in its place),
      // Use closest match now Placed, pressed, and Discard disabled; the count stays, or says nothing is left to check
      const lostLook = await cardLook(card3, true);
      await stepOf(card3, "place").click();
      await until(() => pressed(card3, "place"), { what: "the card saying it placed it" });
      assertEq(await stepOf(card3, "place").locator(".hlReviewFace:not(.hlReviewRoom)").textContent(), "Placed", "it reads Placed");
      assert(!(await stepOf(card3, "discard").isEnabled()) && await stepOf(card3, "move").isEnabled(), "Discard no longer applies; Move… does");
      const left = /^\d+ of 1$/.test(lostLook.count) ? "All reviewed" : lostLook.count;
      await until(async () => (await countOf(card3)) === left, { what: `the count: ${left}` });
      await sleep(300);
      sameLook({ ...lostLook, count: "" }, { ...(await cardLook(card3, true)), count: "" }, "after Use closest match");
      await until(async () => !(await p.$(strictSel)) && (await p.$(`[data-page="2"] div[data-hl-id="${roughId}"]`)) != null,
        { what: "the highlight solid on page 2" });
      assertEq(await p.locator(".pdfHlGhost").count(), 0, "the outline goes once it is placed");
      const placed = await until(async () => {
        const b = await account.api(`/api/blocks/${roughId}`);
        const pos = b.properties.pdf_position;
        return !["lost", "closest", "doc_id", "approx", "match", "prev"].some((k) => k in pos) ? b : null;
      }, { what: "Use closest match saved" });
      assertEq(JSON.stringify(placed.properties.pdf_position.rects), JSON.stringify(near.rects), "on the candidate's rectangles");
      assertEq(placed.properties.quote, near.quote, "with the candidate's text");
      await p.keyboard.press("Escape");
      await card3.waitFor({ state: "detached", timeout: 5000 });
      assertNoProblems(p);
      await p.close();
    } finally {
      await c.close();
      await account.api("/api/prefs/profile", { method: "PUT", body: { value: { ...(profile || {}), selTranslateAuto: false } } });
      await account.api(`/api/blocks/${doc.id}`, { method: "DELETE" });
    }
  });

  // A hollow highlight's card (docs/dev/highlight_relocation.md "In the web app"). A card open while its highlight
  // is scrolled out of the viewer's sight shows nothing of itself, its before picture included, and is on the
  // highlight again when it scrolls back. Discard on a hollow highlight with nothing of its own deletes it, the card
  // staying as it was, Discarded, and Discarded pressed again brings it back hollow; on one with a note it takes the
  // highlight off the PDF and keeps the note, out of what is left to review; the card's walk keeps it.
  await step("pdf: a hollow highlight's card leaves nothing on screen while its highlight is scrolled away, and Discard deletes a bare one or keeps one with a note off the PDF", async () => {
    const kept = "Quantum dots confine single electrons";
    const bare = "This sentence is only in the first version";
    const noted = "Another remark was taken out of the second";
    const note = "Why was this taken out?";
    const rest = [["Page two of the paper"], ["Page three of the paper"]];
    const up1 = await account.upload("/api/uploads", makePdf([[kept, bare, noted], ...rest]), "v1.pdf", "application/pdf");
    const doc = await account.api(`/api/blocks/by-doc/${up1.doc_id}`, { method: "POST", body: { default_title: "Discard paper" } });
    const c = await account.context(browser);
    try {
      const p = await openPage(c, `${server.base}/?page=${doc.id}&ws=${account.ws}`);
      await waitForPdf(p, 1);
      // the bare one, then one with a note typed on it (its note opens for typing)
      await selectPdfText(p, 1, bare);
      await p.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
      await p.locator(".plainTip .colorBtn").first().click();
      await until(async () => (await p.$$('[data-page="1"] div[data-hl-id]')).length >= 1, { what: "the bare highlight" });
      await closeEditor(p);
      await selectPdfText(p, 1, noted);
      await p.waitForSelector(".plainTip .colorBtn", { timeout: 5000 });
      await p.locator(".plainTip .colorBtn").first().click();
      await p.locator(".blockEditorCm").waitFor({ timeout: 5000 });
      await p.keyboard.type(note);
      await closeEditor(p);
      const saved = await until(async () => {
        const d = await account.api(`/api/blocks/${doc.id}/subtree`);
        const hls = (d.block.children || []).filter((b) => b.properties?.pdf_position?.rects);
        return hls.length === 2 && hls.some((b) => b.content === note) ? hls : null;
      }, { what: "two saved highlights, one with its note" });
      const bareId = saved.find((b) => b.properties.quote === bare)?.id;
      const notedId = saved.find((b) => b.properties.quote === noted)?.id;
      assert(bareId && notedId, `the quotes as selected: ${JSON.stringify(saved.map((b) => b.properties.quote))}`);

      // the new version has neither passage: both hollow
      const up2 = await account.upload("/api/uploads", makePdf([[kept, "Quantum dots were measured in a new run"], ...rest]),
        "v2.pdf", "application/pdf");
      const bound = await account.api(`/api/pages/${doc.id}/attachment`, { method: "PUT", body: { doc_id: up2.doc_id, original_filename: "v2.pdf" } });
      const job = await until(async () => {
        const j = await account.api(`/api/jobs/${bound.job.id}`);
        return ["done", "failed", "cancelled"].includes(j.state) ? j : null;
      }, { what: "the relocation job", timeout: 20000 });
      assertEq(job.result?.lost, 2, `two not found: ${JSON.stringify(job.result)}`);
      const stored = async (id) => (await account.api(`/api/blocks/${id}`)).properties;
      assert(!(await stored(bareId)).pdf_position.closest, "nothing close to it either");
      const lostSel = (id) => `[data-page="1"] div[data-hl-id="${id}"].pdfHlLost`;
      await p.waitForSelector(lostSel(bareId), { timeout: 15000 });
      await p.waitForSelector(lostSel(notedId), { timeout: 15000 });
      const { cardOf, stepOf, navOf, countOf, headOf, pressed, pictureLoaded, atRest, look, sameLook } = reviewCardHelpers(p);

      // its card, its quote and its before picture
      await p.hover(lostSel(bareId));
      const bareCard = cardOf(bareId);
      await bareCard.waitFor({ timeout: 5000 });
      assertEq(await bareCard.locator(".hlReviewQuote").textContent(), bare, "the card's quote");
      await pictureLoaded(bareCard);

      // scrolled out of sight, nothing of the card is on screen; scrolled back, it is on its highlight again.
      // `seen`: the card's elements the page paints; `attached`: the card under or over its highlight
      const seen = () => p.evaluate(() => [...document.querySelectorAll(".hlReviewCard, .hlReviewCard *")].filter((el) => {
        const r = el.getBoundingClientRect();
        return getComputedStyle(el).visibility === "visible" && r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight;
      }).map((el) => el.tagName.toLowerCase() + [...el.classList].map((k) => `.${k}`).join("")));
      const attached = (id) => p.evaluate((id) => {
        const card = document.querySelector(".hlReviewCard")?.getBoundingClientRect();
        const hl = document.querySelector(`[data-page="1"] div[data-hl-id="${id}"]`)?.getBoundingClientRect();
        return !!card && !!hl && (Math.abs(card.top - hl.bottom - 6) <= 2 || Math.abs(hl.top - card.bottom - 6) <= 2);
      }, id);
      // away by most of the viewer's height (the highlight starts in its middle), back to where it was
      const scrollTop = () => p.evaluate(() => document.querySelector(".pdfViewer").scrollTop);
      const scrollTo = (top) => p.evaluate((top) => { document.querySelector(".pdfViewer").scrollTop = top; }, top);
      const scrollAway = () => p.evaluate(() => { const v = document.querySelector(".pdfViewer"); v.scrollTop += v.clientHeight * 0.8; });
      const scrollBack = (id) => p.evaluate((id) => document.querySelector(`[data-page="1"] div[data-hl-id="${id}"]`)
        ?.scrollIntoView({ block: "center" }), id);
      const nothingSeen = (what) => until(async () => (await seen()).length === 0, { what: `nothing of the card on screen ${what}`, timeout: 3000 })
        .catch(async () => { throw new Error(`the card's parts left on screen ${what}: ${JSON.stringify(await seen())}`); });
      // the pointer gone into the card, which keeps it open, and the viewer scrolled under it
      await bareCard.hover();
      await scrollAway();
      await nothingSeen("with the pointer in the card");
      await p.mouse.move(2, 2);
      await bareCard.waitFor({ state: "detached", timeout: 5000 });
      // opened from the Document menu (it stays), scrolled by the scroller and by the wheel
      await scrollBack(bareId);
      await p.click("button[aria-label='Document']");
      await p.locator(".attachPopover .ctxMenuItem", { hasText: "Review 2 highlights" }).click();
      await bareCard.waitFor({ timeout: 5000 });
      await pictureLoaded(bareCard);
      await until(() => attached(bareId), { what: "the card on its highlight" });
      const top0 = await scrollTop();
      await scrollAway();
      await nothingSeen("after the scroller moved");
      await scrollTo(top0);
      await until(async () => (await seen()).includes("img") && await attached(bareId), { what: "the card back on its highlight, picture and all" });
      const view = await p.locator(".pdfViewer").boundingBox();
      await p.mouse.move(view.x + view.width - 30, view.y + view.height / 2);
      for (let i = 0; i < 2; i++) await p.mouse.wheel(0, 350);
      await until(() => p.evaluate((id) => {
        const hl = document.querySelector(`[data-page="1"] div[data-hl-id="${id}"]`)?.getBoundingClientRect();
        return !!hl && hl.bottom < document.querySelector(".pdfViewer").getBoundingClientRect().top;
      }, bareId), { what: "the highlight wheeled out of sight" });
      await nothingSeen("after the wheel");
      let last = -1;
      await until(async () => { const now = await scrollTop(); const still = now === last; last = now; return still; },
        { what: "the wheel's glide over", every: 150 });
      await scrollTo(top0);
      await until(async () => (await seen()).includes("img") && await attached(bareId), { what: "the card back on its highlight again" });

      // Discard on the bare one: it says it deletes; the highlight goes from the page and the store, the card stays
      // where it was, as it was (its box, its picture, every button in its place), Discard now Discarded, pressed,
      // Move… disabled (nothing is left on the page to move), and the count as it was
      const discard = stepOf(bareCard, "discard");
      assert((await discard.getAttribute("title")).startsWith("Delete the highlight"), `its title: ${await discard.getAttribute("title")}`);
      await atRest(bareCard);
      const before = await look(bareCard);
      await discard.click();
      await until(() => pressed(bareCard, "discard"), { what: "the card saying it discarded" });
      assertEq(await discard.locator(".hlReviewFace:not(.hlReviewRoom)").textContent(), "Discarded", "it reads Discarded");
      assert(!(await stepOf(bareCard, "move").isEnabled()), "nothing to move");
      assert(await navOf(bareCard, "next").isEnabled(), "Next to the other one");
      assertEq(await p.locator(`[data-hl-id="${bareId}"]`).count(), 0, "gone from the page");
      await sleep(200);
      sameLook(before, await look(bareCard), "after Discard");
      await until(async () => !(await account.api(`/api/blocks/${doc.id}/subtree`)).block.children.some((b) => b.id === bareId),
        { what: "the highlight deleted" });
      // Discarded pressed again: back, hollow where it was, with the file it was measured on
      await discard.click();
      await until(async () => !(await pressed(bareCard, "discard")), { what: "the card on the hollow one again" });
      await p.waitForSelector(lostSel(bareId), { timeout: 5000 });
      await until(async () => {
        const props = await stored(bareId).catch(() => null);
        return props?.pdf_position?.lost === true && props.pdf_position.doc_id === up1.doc_id && props.quote === bare;
      }, { what: "the highlight back, saved" });

      // Next: the one with a note. Discard says it keeps the note, and takes the highlight off the PDF: the row and its
      // note stay, its position its page alone; Discarded pressed again puts it back hollow, and a second Discard
      // leaves one to review
      await navOf(bareCard, "next").click();
      const notedCard = cardOf(notedId);
      await notedCard.waitFor({ timeout: 5000 });
      const keep = stepOf(notedCard, "discard");
      assert((await keep.getAttribute("title")).includes("keep it as a note"), `its title: ${await keep.getAttribute("title")}`);
      await keep.click();
      await until(() => pressed(notedCard, "discard"), { what: "the noted one discarded" });
      assertEq(await headOf(notedCard), "Not found in this version", "its card as it was");
      assertEq(await p.locator(`[data-hl-id="${notedId}"]`).count(), 0, "off the page");
      assert(await p.locator(".blockRow", { hasText: note }).isVisible(), "its note row stays");
      await until(async () => {
        const props = await stored(notedId);
        return JSON.stringify(props.pdf_position) === JSON.stringify({ pageNumber: 1 }) && props.quote === noted ? props : null;
      }, { what: "its position its page alone, its quote kept" });
      assertEq((await account.api(`/api/blocks/${notedId}`)).content, note, "its note kept");
      await keep.click();
      await until(async () => !(await pressed(notedCard, "discard")), { what: "the noted one hollow again" });
      await p.waitForSelector(lostSel(notedId), { timeout: 5000 });
      await until(async () => (await stored(notedId)).pdf_position.lost === true, { what: "its undo saved" });
      await keep.click();
      await until(() => pressed(notedCard, "discard"), { what: "discarded again" });
      // the discarded one stays in the walk: Previous reaches the bare one, Next comes back to it, Discarded
      await navOf(notedCard, "prev").click();
      await bareCard.waitFor({ timeout: 5000 });
      assertEq(await countOf(bareCard), "1 of 2", "the walk keeps the discarded one");
      await navOf(bareCard, "next").click();
      await notedCard.waitFor({ timeout: 5000 });
      assert(await pressed(notedCard, "discard"), "reached again, it shows Discarded");
      await p.keyboard.press("Escape");
      await notedCard.waitFor({ state: "detached", timeout: 5000 });
      await p.click("button[aria-label='Document']");
      await p.locator(".attachPopover .ctxMenuItem", { hasText: "Find highlights in this PDF" }).waitFor();
      assertEq(await p.locator(".attachPopover .ctxMenuItem", { hasText: "Review 1 highlight" }).count(), 1, "the bare one is left to review");
      await p.keyboard.press("Escape");
      assertNoProblems(p);
      await p.close();
    } finally {
      await c.close();
      await account.api(`/api/blocks/${doc.id}`, { method: "DELETE" });
    }
  });

  // Review cards with nothing drawn to anchor to yet, or ever: Show on a closest match pages away, outlined
  // only once its page renders after the scroll, keeps the card in sight under the outline; a highlight lost
  // from a page the new version dropped, which no page draws, is reached by Next and the Document menu, its
  // card standing in the viewer, saying so, and staying until it is closed. Use closest match by the keyboard
  // takes the highlight pages away, the card hidden while the viewer scrolls there, and the focus stays on the
  // button, so Enter again takes the step back. On a 360 px phone, in English and in Chinese, the card's row
  // stays on one line.
  await step("pdf: a hollow highlight's card follows Show to a far page, and one from a page the new version dropped is reached by Next", async () => {
    const oldPages = Array.from({ length: 10 }, (_, i) => [`Page ${i + 1} of the paper`, `Some more text on page ${i + 1}`]);
    const up1 = await account.upload("/api/uploads", makePdf(oldPages), "old.pdf", "application/pdf");
    const newPages = oldPages.slice(0, 8).map((lines, i) => (i === 6 ? [...lines, "Spin echoes were measured by Hahn [26]"] : lines));
    const up2 = await account.upload("/api/uploads", makePdf(newPages), "new.pdf", "application/pdf");
    const doc = await account.api(`/api/blocks/by-doc/${up2.doc_id}`, { method: "POST", body: { default_title: "Far review" } });
    const box = (y) => ({ x1: 72, y1: y, x2: 400, y2: y + 22 });
    const lostAt = (pageNumber, y, extra = {}) => ({ pageNumber, width: 612, height: 792, boundingRect: box(y), rects: [box(y)],
      lost: true, doc_id: up1.doc_id, ...extra });
    const make = (quote, pdf_position) => account.api("/api/blocks", { method: "POST", body: { parent_id: doc.id, content: "",
      properties: { quote, color: "rgba(255, 226, 143, 0.4)", pdf_position } } });
    const near = await make("Spin echoes were recorded by Hahn [27]", lostAt(1, 84, { closest: { pageNumber: 7, width: 612, height: 792,
      boundingRect: box(118), rects: [box(118)], match: 0.7, quote: "Spin echoes were measured by Hahn [26]" } }));
    const dropped = await make("Some more text on page 10", lostAt(10, 84));
    const c = await account.context(browser);
    try {
      const p = await openPage(c, `${server.base}/?page=${doc.id}&ws=${account.ws}`);
      await waitForPdf(p, 1);
      const nearSel = `[data-page="1"] div[data-hl-id="${near.id}"].pdfHlLost`;
      await p.waitForSelector(nearSel, { timeout: 15000 });
      const { cardOf, stepOf, navOf, countOf, pressed } = reviewCardHelpers(p);
      const inSight = (card) => card.evaluate((el) => {
        const r = el.getBoundingClientRect();
        return el.style.visibility !== "hidden" && r.bottom > 0 && r.top < window.innerHeight;
      }).catch(() => false);
      await p.hover(nearSel);
      const nearCard = cardOf(near.id);
      await nearCard.waitFor({ timeout: 5000 });
      assertEq(await nearCard.locator(".hlReviewClosestHead .hlReviewLabel").textContent(), "Closest match: 70%", "its closest match");
      // Show: page 7 renders after the scroll, its outline then drawn, and the card follows it there without a scroll
      await nearCard.locator(".hlReviewClosestHead button", { hasText: "Show" }).click();
      const ghost = `[data-page="7"] .pdfHlGhost[data-review-ghost="${near.id}"]`;
      await p.waitForSelector(ghost, { timeout: 5000 });
      await until(() => inSight(nearCard), { what: "the card in sight under the outline", timeout: 2000 });
      const [ghostBox, cardBox] = [await p.locator(ghost).boundingBox(), await nearCard.boundingBox()];
      assert(Math.abs(cardBox.y - (ghostBox.y + ghostBox.height)) < 40 || Math.abs(cardBox.y + cardBox.height - ghostBox.y) < 40,
        `the card beside the outline: ${JSON.stringify({ ghostBox, cardBox })}`);
      assert(await stepOf(nearCard, "place").isVisible(), "Use closest match on it");
      // Next: the one from page 10, which this version does not have, its card in the viewer, saying so
      await navOf(nearCard, "next").click();
      const droppedCard = cardOf(dropped.id);
      await droppedCard.waitFor({ timeout: 5000 });
      await until(() => inSight(droppedCard), { what: "the dropped page's highlight's card in sight", timeout: 2000 });
      assertEq(await droppedCard.locator(".hlReviewHead").textContent(), "Not found in this version", "its header");
      assertEq(await droppedCard.locator(".hlReviewOffPage").textContent(), "This version of the PDF has no page 10.", "what it says");
      assertEq(await countOf(droppedCard), "2 of 2", "the second of two");
      assert(await stepOf(droppedCard, "move").isVisible(), "Move… is offered");
      // where it was: a picture of page 10 of the file it was measured on
      await until(() => droppedCard.locator(".hlReviewShot img").evaluate((img) => img.complete && img.naturalWidth > 0, null, { timeout: 1000 }),
        { what: "its before picture", timeout: 15000 });
      assert((await droppedCard.locator(".hlReviewShot img").getAttribute("src")).includes(`/ai/selection-crop/${up1.doc_id}?page=10&`),
        "of page 10 of the first file");
      // it waits for nothing to be drawn: still open past the wait for a page to render
      await sleep(4500);
      assert(await inSight(droppedCard), "the card stays");
      // and Next goes round to the first again
      await navOf(droppedCard, "next").click();
      await nearCard.waitFor({ timeout: 5000 });
      assertEq(await countOf(nearCard), "1 of 2", "the first again");
      await p.keyboard.press("Escape");
      await nearCard.waitFor({ state: "detached", timeout: 5000 });
      // the Document menu counts both, and opens the first one's card with the focus in it. Enter on Use closest
      // match places the highlight on page 7, the viewer scrolling there with the card hidden on the way: the focus
      // is on the button again once the card shows, Placed, and Enter again puts the highlight back, hollow on
      // page 1, the focus still on it
      await p.click("button[aria-label='Document']");
      await p.locator(".attachPopover .ctxMenuItem", { hasText: "Review 2 highlights" }).click();
      await nearCard.waitFor({ timeout: 5000 });
      await until(() => inSight(nearCard), { what: "the card opened from the Document menu" });
      const focused = () => p.evaluate(() => {
        const a = document.activeElement;
        return a?.closest(".hlReviewCard") ? a.dataset.step || a.dataset.reviewNav || "the card" : a?.tagName.toLowerCase() || "nothing";
      });
      const focusOn = (key, what) => until(async () => (await inSight(nearCard)) && (await focused()) === key, { what, timeout: 10000 })
        .catch(async (e) => { throw new Error(`${e.message} (the focus on ${await focused()})`); });
      await stepOf(nearCard, "place").focus();
      await p.keyboard.press("Enter");
      await until(() => pressed(nearCard, "place"), { what: "Placed" });
      await until(async () => (await account.api(`/api/blocks/${near.id}`)).properties.pdf_position.pageNumber === 7,
        { what: "placed on page 7, saved" });
      await focusOn("place", "the card shown on page 7, the focus on Placed");
      await p.keyboard.press("Enter");
      await until(async () => !(await pressed(nearCard, "place")), { what: "Use closest match again" });
      await until(async () => (await account.api(`/api/blocks/${near.id}`)).properties.pdf_position.lost === true,
        { what: "hollow again, saved" });
      await p.waitForSelector(nearSel, { timeout: 5000 });
      await focusOn("place", "the card back on page 1, the focus on Use closest match");
      await p.keyboard.press("Escape");
      await nearCard.waitFor({ state: "detached", timeout: 5000 });
      assertNoProblems(p);
      await p.close();

      // a 360 px phone makes the card narrower than its 360 px; its row (Use closest match, Move…, Discard,
      // Previous, the count, Next) stays on one line, in English and in Chinese
      for (const locale of ["en-US", "zh-CN"]) {
        const phone = await account.context(browser, { locale, viewport: { width: 360, height: 780 }, isMobile: true, hasTouch: true });
        try {
          const q = await openPage(phone, `${server.base}/?page=${doc.id}&ws=${account.ws}`);
          await waitForPdf(q, 1);
          await q.waitForSelector(nearSel, { timeout: 15000 });
          await sleep(500);
          await q.locator(nearSel).first().tap();
          const card = q.locator(`.hlReviewCard[data-review-id="${near.id}"]`);
          await card.waitFor({ timeout: 5000 });
          const row = await card.evaluate((el) => {
            const kids = [...el.querySelectorAll(".hlReviewActions > button, .hlReviewNav > *")];
            const rowBox = el.querySelector(".hlReviewActions").getBoundingClientRect();
            return {
              card: el.getBoundingClientRect().width,
              keys: kids.map((k) => k.dataset.step || k.dataset.reviewNav || "count"),
              lines: new Set(kids.map((k) => { const r = k.getBoundingClientRect(); return Math.round(r.top + r.height / 2); })).size,
              height: rowBox.height, tallest: Math.max(...kids.map((k) => k.getBoundingClientRect().height)),
            };
          });
          assertEq(row.keys.join(" "), "place move discard prev count next", `${locale}: the lost card's row`);
          assert(row.card < 360, `${locale}: the card narrower than 360 px (${row.card})`);
          assert(row.lines === 1 && row.height <= row.tallest + 1, `${locale}: the row on one line at 360 px: ${JSON.stringify(row)}`);
          assertNoProblems(q);
          await q.close();
        } finally {
          await phone.close();
        }
      }
    } finally {
      await c.close();
      await account.api(`/api/blocks/${doc.id}`, { method: "DELETE" });
    }
  });

  if (ctx) await ctx.close();
  return { pdfPageId: pageId, docId };
}
