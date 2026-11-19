// A scanned page's invisible OCR layer, repaired before the viewer uses it
// (docs/dev/pdf_loading.md "A scan's text layer"; pdf/pageText.js,
// pdf/scanDetect.js, pdf/scanText.js), at the real UI. The paper
// (tests/e2e/scanPdf.mjs) is a born-digital cover, as HAL puts before a scan,
// then a page that is a picture with a flipped, ink-sized OCR layer over its
// one paragraph of four printed lines. Every check is against where the
// fixture printed the words:
// - the cover's text layer is pdf.js's own: a span an item, where pdf.js
//   puts it;
// - the scan's spans sit on the print, one top and height a line (its ink's
//   band, not the line above pdf.js would draw the flipped layer at), tiling
//   it, each word's span over its printed word;
// - a mouse drag across a printed line selects its words in order, with
//   spaces; the selection's translation says `ocr`, the cover's does not; the
//   highlight made from it lies on the printed line;
// - Ctrl+F marks a word on its printed place;
// - translating the scan page sends its paragraph as one text, in requests
//   that say `ocr`; the cover's requests do not, and neither page's brief
//   request does (the server checks a stored file's opening pages itself).
// The translate endpoints are answered locally ("译:" + each text).
import { pageText } from "../../../src/pdf/pageText.js";
import { fakeAiModels, wanted } from "../harness.mjs";
import { COVER, COVER_TITLE, PAGE_H, PAGE_W, PARAGRAPH, printedWidth, scanPaper } from "../scanPdf.mjs";
import { movePast, selectPdfText, waitForPdf } from "./pdf.mjs";

export async function scanLayerScenarios({ server, browser, alice, step, until, assert, assertEq, assertNoProblems, openPage }) {
  if (!wanted("scan layer")) return;
  const { bytes, words, lines } = scanPaper();
  const wordsOf = (k) => words.filter((w) => w.line === k);
  let ctx = null, page = null, pageId = "", cover = [];
  const requests = [], briefs = [];

  // What `sel` matches on page `pn` of the viewer shown, in pt from the page's
  // top-left corner (y down), with its text.
  const boxesOn = (pn, sel) => page.evaluate(([pn, sel, W]) => {
    const wrap = [...document.querySelectorAll(`.pdfPageWrap[data-page="${pn}"]`)].find((el) => el.offsetParent !== null);
    if (!wrap) return [];
    const at = wrap.getBoundingClientRect(), k = W / at.width;
    return [...wrap.querySelectorAll(sel)].map((el) => {
      const r = el.getBoundingClientRect();
      return { text: el.textContent, x0: (r.left - at.left) * k, x1: (r.right - at.left) * k, top: (r.top - at.top) * k, bottom: (r.bottom - at.top) * k };
    });
  }, [pn, sel, PAGE_W]);
  // The text layer's spans of page `pn`, once two readings agree (a layer is
  // built when the scroller rests).
  const spansOf = async (pn) => {
    let last = "";
    return until(async () => {
      const now = (await boxesOn(pn, ".textLayer span")).filter((s) => s.text);
      const same = now.length && JSON.stringify(now) === last;
      last = JSON.stringify(now);
      return same && now;
    }, { what: `page ${pn}'s text layer at rest`, every: 150 });
  };
  // A point on page `pn` (pt, y down) in the window's pixels.
  const toScreen = (pn, x, y) => page.evaluate(([pn, x, y, W]) => {
    const wrap = [...document.querySelectorAll(`.pdfPageWrap[data-page="${pn}"]`)].find((el) => el.offsetParent !== null);
    const at = wrap.getBoundingClientRect(), k = W / at.width;
    return { x: at.left + x / k, y: at.top + y / k };
  }, [pn, x, y, PAGE_W]);
  const pt = (v) => v.toFixed(2);

  try {
    await step("scan layer: setup, a paper with a born-digital cover and a scanned page whose OCR layer pdf.js reads flipped, a word an item", async () => {
      // The fixture, read in Node by the same pdf.js and pdf/pageText.js: what
      // the steps below mean it to be.
      const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
      const doc = await pdfjs.getDocument({ data: new Uint8Array(bytes), verbosity: 0 }).promise;
      try {
        const first = await doc.getPage(1), second = await doc.getPage(2);
        cover = (await first.getTextContent()).items.filter((it) => it.str)
          .map((it) => ({ str: it.str, x: it.transform[4], base: PAGE_H - it.transform[5], size: it.transform[3], width: it.width }));
        assertEq(cover.map((it) => it.str).join("|"), [COVER_TITLE, ...COVER].join("|"), "pdf.js reads the cover a line an item");
        const raw = (await second.getTextContent()).items.filter((it) => it.str.trim());
        assertEq(raw.map((it) => it.str).join(" "), PARAGRAPH.join(" "), "pdf.js reads the layer's words in order");
        assertEq(raw.length, words.length, "a word an item");
        assert(raw.every((it) => it.transform[3] < 0), "the layer is read flipped");
        assertEq((await pageText(second)).repaired, true, "pdf/pageText.js calls the second page a scan");
        assertEq((await pageText(first)).repaired, undefined, "and leaves the cover as pdf.js reads it");
      } finally {
        await doc.destroy();
      }

      const up = await alice.upload("/api/uploads", bytes, "scanned-1992.pdf", "application/pdf");
      const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Scanned paper", source_url: up.source_url } });
      pageId = created.id;
      ctx = await alice.context(browser);
      await fakeAiModels(ctx);
      await ctx.route("**/api/ai/translate", async (route) => {
        const body = route.request().postDataJSON();
        requests.push(body);
        await route.fulfill({ contentType: "application/x-ndjson",
          body: `${JSON.stringify({ translations: body.texts.map((t) => `译:${t}`), model: "demo:model", cached: false })}\n` });
      });
      await ctx.route("**/api/ai/translate/brief", async (route) => {
        briefs.push(route.request().postDataJSON());
        await route.fulfill({ contentType: "application/x-ndjson", body: `${JSON.stringify({ ready: true })}\n` });
      });
      page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
      await waitForPdf(page, 1);
      await page.locator('.pdfPageWrap[data-page="2"]').scrollIntoViewIfNeeded();
      await waitForPdf(page, 2);
      assertNoProblems(page);
    });

    await step("scan layer: the born-digital cover's text layer is pdf.js's own, a span an item where pdf.js puts it", async () => {
      await page.locator('.pdfPageWrap[data-page="1"]').scrollIntoViewIfNeeded();
      const spans = await spansOf(1);
      assertEq(spans.map((s) => s.text).join("|"), cover.map((it) => it.str).join("|"), "a span an item, its text pdf.js's");
      spans.forEach((s, i) => {
        const it = cover[i];
        assert(Math.abs(s.x0 - it.x) < 0.5, `"${it.str}" starts at its origin: ${pt(s.x0)} vs ${pt(it.x)}`);
        assert(Math.abs((s.x1 - s.x0) - it.width) < Math.max(0.5, 0.01 * it.width), `"${it.str}" is as wide as pdf.js measures it: ${pt(s.x1 - s.x0)} vs ${pt(it.width)}`);
        // pdf.js's own placement: the span's top an ascent over the baseline
        const ascent = (it.base - s.top) / it.size;
        assert(ascent > 0.7 && ascent < 0.95, `"${it.str}" hangs from its baseline by pdf.js's ascent: ${ascent.toFixed(3)}`);
      });
      assertNoProblems(page);
      return `${spans.length} spans, as pdf.js reads the page`;
    });

    await step("scan layer: the scan's spans sit on the print: one top and height a printed line, tiling it, each word's span over its printed word", async () => {
      await page.locator('.pdfPageWrap[data-page="2"]').scrollIntoViewIfNeeded();
      const spans = await spansOf(2);
      assertEq(spans.length, words.length, `a span a word: ${JSON.stringify(spans.map((s) => s.text))}`);
      let minCover = 1;
      // every word's misplacement, said together
      const misplaced = [];
      lines.forEach((line, k) => {
        const ws = wordsOf(k);
        const ls = ws.map((w) => spans[words.indexOf(w)]);
        ls.forEach((s, i) => {
          const last = i === ls.length - 1;
          assertEq(s.text, last ? ws[i].text : `${ws[i].text} `, `line ${k + 1}'s word ${i + 1}, the space folded in`);
        });
        const tops = ls.map((s) => s.top), heights = ls.map((s) => s.bottom - s.top);
        assert(Math.max(...tops) - Math.min(...tops) < 0.3 && Math.max(...heights) - Math.min(...heights) < 0.3,
          `line ${k + 1}: one top and height: ${tops.map(pt)} / ${heights.map(pt)}`);
        // The band is the line's ink, descender to ascender; pdf.js would have
        // drawn the flipped layer an ink height higher (a word's span from
        // top - 0.8 size), mostly over the line above.
        assert(Math.abs(ls[0].top - line.top) < 0.6 && Math.abs(ls[0].bottom - line.bottom) < 0.6,
          `line ${k + 1} spans ${pt(ls[0].top)}..${pt(ls[0].bottom)}, printed ${pt(line.top)}..${pt(line.bottom)}`);
        for (let i = 0; i + 1 < ls.length; i++) {
          assert(Math.abs(ls[i].x1 - ls[i + 1].x0) < 0.3, `line ${k + 1}: "${ls[i].text}" runs to "${ls[i + 1].text}": ${pt(ls[i].x1)} vs ${pt(ls[i + 1].x0)}`);
        }
        ws.forEach((w, i) => {
          const s = ls[i];
          const at = `line ${k + 1}, "${w.text}" printed ${pt(w.x0)}..${pt(w.x1)}, its span ${pt(s.x0)}..${pt(s.x1)}`;
          const centre = (w.x0 + w.x1) / 2;
          if (!(s.x0 <= centre && centre <= s.x1)) misplaced.push(`${at}: the printed word's middle is outside its span`);
          const cover = (Math.min(s.x1, w.x1) - Math.max(s.x0, w.x0)) / (w.x1 - w.x0);
          minCover = Math.min(minCover, cover);
          if (!(cover >= 0.8)) misplaced.push(`${at}: the span covers ${cover.toFixed(2)} of the printed word`);
          for (const other of [ws[i - 1], ws[i + 1]]) {
            if (!other) continue;
            const into = Math.min(s.x1, other.x1) - Math.max(s.x0, other.x0);
            if (!(into < 1)) misplaced.push(`${at}: the span reaches ${pt(into)} pt into "${other.text}"`);
          }
        });
      });
      assertEq(misplaced.join("\n          "), "", "every word's span over its printed word");
      assertNoProblems(page);
      return `bands ${lines.map((l) => `${pt(l.top)}..${pt(l.bottom)}`).join(", ")} pt as printed; the least of a printed word a span covers: ${minCover.toFixed(2)}`;
    });

    await step("scan layer: a mouse drag across a printed line selects its words in order with spaces, its translation says ocr and the cover's does not, and its highlight lies on the line", async () => {
      const k = 2, line = lines[k], ws = wordsOf(k);
      const spans = await spansOf(2);
      const first = spans[words.indexOf(ws[0])], last = spans[words.indexOf(ws.at(-1))];
      const mid = (line.top + line.bottom) / 2;
      // The line's first and last printed letters: [x0, x1].
      const firstLetter = [ws[0].x0, ws[0].x0 + printedWidth(ws[0].text[0])];
      const lastLetter = [ws.at(-1).x1 - printedWidth(ws.at(-1).text.at(-1)), ws.at(-1).x1];
      // Pressed on the first printed letter's left half, let go on the last
      // one's right half (or just past it, inside the line's last span).
      const from = { x: Math.max(firstLetter[0], first.x0 + 0.3), y: mid };
      const to = { x: Math.min(last.x1 - 0.3, lastLetter[1] + 2), y: mid };
      assert(from.x < (firstLetter[0] + firstLetter[1]) / 2, `the press (${pt(from.x)}) is on the first printed letter's left half (${pt(firstLetter[0])}..${pt(firstLetter[1])}), inside its span (${pt(first.x0)}..)`);
      assert(to.x > (lastLetter[0] + lastLetter[1]) / 2, `the release (${pt(to.x)}) is on the last printed letter's right half or past it (${pt(lastLetter[0])}..${pt(lastLetter[1])}), inside its span (..${pt(last.x1)})`);
      const a = await toScreen(2, from.x, from.y), b = await toScreen(2, to.x, to.y);
      await page.mouse.move(a.x, a.y);
      await page.mouse.down();
      await page.mouse.move((a.x + b.x) / 2, a.y, { steps: 5 });
      await page.mouse.move(b.x, b.y, { steps: 5 });
      await page.mouse.up();
      const got = await page.evaluate(() => String(getSelection()));
      assertEq(got.trim(), line.text, "the line's words, in order, one space apart");
      await movePast(page, b);
      const tip = page.locator(".plainTip");
      await tip.waitFor();

      // Its translation: one text, from a scan.
      const before = requests.length;
      await tip.getByRole("button", { name: "Translate selection", exact: true }).click();
      await until(async () => (await tip.locator(".selTransBody").textContent().catch(() => "")) === `译:${line.text}`, { what: "the selection's translation" });
      const sent = requests.slice(before);
      assertEq(sent.length, 1, "one request");
      assertEq(JSON.stringify(sent[0].texts), JSON.stringify([line.text]));
      assertEq(sent[0].ocr, true, "a selection on the scan says it was read by OCR");
      assertEq(sent[0].page_id, pageId);

      // Its highlight: the first swatch.
      await tip.locator(".colorBtn").first().click();
      const hl = await until(async () => ((await alice.api(`/api/blocks/${pageId}/subtree`)).block.children || [])
        .find((b) => b.properties?.pdf_position?.pageNumber === 2), { what: "the highlight saved" });
      assertEq(hl.properties.quote, line.text, "its quote is the line");
      const pos = hl.properties.pdf_position;
      const sx = PAGE_W / pos.width, sy = PAGE_H / pos.height;
      const rects = pos.rects.map((r) => ({ x0: r.x1 * sx, x1: r.x2 * sx, top: r.y1 * sy, bottom: r.y2 * sy }));
      const box = (rs) => ({ x0: Math.min(...rs.map((r) => r.x0)), x1: Math.max(...rs.map((r) => r.x1)), top: Math.min(...rs.map((r) => r.top)), bottom: Math.max(...rs.map((r) => r.bottom)) });
      // Its rects are the selection's text boxes (the browser font's ascent and
      // descent, not the span's band): over the printed line, off the lines
      // above and below it.
      const above = lines[k - 1], below = lines[k + 1];
      const lies = (b, what) => {
        assert(b.top <= line.top + 0.5 && b.bottom >= line.bottom - 0.5 && b.top > above.bottom && b.bottom < below.top,
          `${what}: ${pt(b.top)}..${pt(b.bottom)} over the printed line ${pt(line.top)}..${pt(line.bottom)}, off the lines above (..${pt(above.bottom)}) and below (${pt(below.top)}..)`);
        // from the first printed letter to the last, more than half of each
        assert(b.x0 < (firstLetter[0] + firstLetter[1]) / 2 && b.x1 > (lastLetter[0] + lastLetter[1]) / 2,
          `${what}: ${pt(b.x0)}..${pt(b.x1)} over the print ${pt(ws[0].x0)}..${pt(ws.at(-1).x1)} (first letter ..${pt(firstLetter[1])}, last ${pt(lastLetter[0])}..)`);
      };
      lies(box(rects), "the stored rects");
      // and as drawn
      await page.locator(`.pdfPageWrap[data-page="2"] [data-hl-id="${hl.id}"]`).first().waitFor();
      lies(box(await boxesOn(2, `[data-hl-id="${hl.id}"]`)), "the highlight drawn");
      await page.locator(".plainTip").waitFor({ state: "detached" });

      // The cover's selection sends no ocr.
      await page.locator('.pdfPageWrap[data-page="1"]').scrollIntoViewIfNeeded();
      await selectPdfText(page, 1, "open access archive");
      const coverTip = page.locator(".plainTip");
      await coverTip.getByRole("button", { name: "Translate selection", exact: true }).click();
      await until(async () => (await coverTip.locator(".selTransBody").textContent().catch(() => "")) === "译:open access archive", { what: "the cover selection's translation" });
      assert(!("ocr" in requests.at(-1)), `a selection on the cover says nothing of OCR: ${JSON.stringify(requests.at(-1))}`);
      await page.keyboard.press("Escape");
      await page.evaluate(() => getSelection().removeAllRanges());
      assertNoProblems(page);
      return `highlight ${pt(box(rects).x0)}..${pt(box(rects).x1)} × ${pt(box(rects).top)}..${pt(box(rects).bottom)} pt over the printed line ${pt(line.x0)}..${pt(line.x1)} × ${pt(line.top)}..${pt(line.bottom)}`;
    });

    await step("scan layer: Ctrl+F marks a word of the scan on its printed place", async () => {
      const word = words.find((w) => w.text === "transitions");
      const line = lines[word.line];
      await page.locator('.pdfPageWrap[data-page="2"]').scrollIntoViewIfNeeded();
      await spansOf(2);
      await page.keyboard.press("ControlOrMeta+f");
      const input = page.locator(".searchPopover .searchInput");
      await input.waitFor();
      await input.fill(word.text);
      await until(async () => (await page.textContent(".searchPopover .searchFindCount").catch(() => "")) === "1/1", { what: "find count 1/1" });
      const marks = await until(async () => {
        const now = await boxesOn(2, ".pdfFindMark");
        return now.length && now;
      }, { what: "the find mark on the scan page" });
      assertEq(marks.length, 1, "one mark");
      const [m] = marks;
      const centre = (word.x0 + word.x1) / 2;
      assert(m.x0 <= centre && centre <= m.x1, `the mark ${pt(m.x0)}..${pt(m.x1)} is over "${word.text}" printed ${pt(word.x0)}..${pt(word.x1)}`);
      const cover = (Math.min(m.x1, word.x1) - Math.max(m.x0, word.x0)) / (word.x1 - word.x0);
      assert(cover >= 0.8, `the mark covers ${cover.toFixed(2)} of the printed word`);
      assert(m.top <= word.top + 0.3 && m.bottom >= word.bottom - 0.3, `the mark ${pt(m.top)}..${pt(m.bottom)} holds the word's ink ${pt(word.top)}..${pt(word.bottom)}`);
      const above = lines[word.line - 1], below = lines[word.line + 1];
      assert(m.top > above.bottom && m.bottom < below.top,
        `the mark ${pt(m.top)}..${pt(m.bottom)} stays on its line (${pt(line.top)}..${pt(line.bottom)}), off the lines above (..${pt(above.bottom)}) and below (${pt(below.top)}..)`);
      await page.keyboard.press("Escape");
      assertNoProblems(page);
      return `mark ${pt(m.x0)}..${pt(m.x1)} × ${pt(m.top)}..${pt(m.bottom)} over "${word.text}" ${pt(word.x0)}..${pt(word.x1)} × ${pt(word.top)}..${pt(word.bottom)}`;
    });

    await step("scan layer: translating the scan sends its paragraph as one text in requests that say ocr; the cover's say nothing of it", async () => {
      // Scroll the viewer shown so `pn` is the page being read.
      const goTo = async (pn) => {
        await page.evaluate((pn) => {
          const v = [...document.querySelectorAll(".pdfViewer")].find((el) => el.offsetParent !== null);
          const el = v.querySelector(`[data-page="${pn}"]`);
          v.scrollTop = pn === 1 ? 0 : el.getBoundingClientRect().top - v.getBoundingClientRect().top + v.scrollTop;
          v.dispatchEvent(new Event("scroll"));
        }, pn);
        await until(() => page.evaluate(() => [...document.querySelectorAll("input[aria-label='Current page']")]
          .find((el) => el.offsetParent !== null)?.value).then((v) => v === String(pn)), { what: `page ${pn} is the one being read` });
      };
      const button = page.getByRole("button", { name: "Translate", exact: true });

      await goTo(2);
      let start = requests.length, briefStart = briefs.length;
      await button.click();
      const paras = page.locator('.pdfPageWrap[data-page="2"] .pdfTransPara');
      await until(async () => (await paras.locator(".pdfTransText").allTextContents()).some((t) => t.startsWith("译:We consider")),
        { what: "the scan's paragraph translated" });
      await page.locator(".pdfTransBar").waitFor({ state: "detached" });
      const scan = requests.slice(start);
      assertEq(JSON.stringify(scan.flatMap((r) => r.texts)), JSON.stringify([PARAGRAPH.join(" ")]), "the paragraph goes as one text, not a line or a word a text");
      assert(scan.every((r) => r.ocr === true && r.page_id === pageId), `its requests say ocr: ${JSON.stringify(scan)}`);
      const scanBriefs = briefs.slice(briefStart);
      assertEq(scanBriefs.length, 1, "the job asks for the paper's brief");
      // A stored file's opening pages the server checks itself
      // (translate_context.Paper.scanned): the viewer says nothing of them.
      assert(!("ocr" in scanBriefs[0]), `the brief leaves the stored file's OCR to the server: ${JSON.stringify(scanBriefs[0])}`);
      assertEq(scanBriefs[0].page_id, pageId);
      // One block, over the printed paragraph.
      assertEq(await paras.count(), 1, "one translated block");
      const [block] = await boxesOn(2, ".pdfTransPara");
      assert(block.x0 <= lines[0].x0 + 3 && block.x1 >= Math.max(...lines.map((l) => l.x1)) - 3
        && block.top <= lines[0].top + 1.5 && block.top >= lines[0].top - 4 && block.bottom >= lines[3].bottom - 1.5 && block.bottom <= lines[3].bottom + 4,
      `the block ${pt(block.x0)}..${pt(block.x1)} × ${pt(block.top)}..${pt(block.bottom)} lies over the paragraph ${pt(lines[0].x0)}..${pt(Math.max(...lines.map((l) => l.x1)))} × ${pt(lines[0].top)}..${pt(lines[3].bottom)}`);

      // The cover, a page of its own, says nothing of OCR.
      await goTo(1);
      start = requests.length;
      briefStart = briefs.length;
      await button.click();
      await until(async () => (await page.locator('.pdfPageWrap[data-page="1"] .pdfTransText').allTextContents()).some((t) => t.startsWith("译:HAL is")),
        { what: "the cover translated" });
      await page.locator(".pdfTransBar").waitFor({ state: "detached" });
      const plain = requests.slice(start);
      // (how the cover's lines group into paragraphs is pdf.js's text and the segmenter's, as before)
      const coverTexts = plain.flatMap((r) => r.texts);
      for (const l of [COVER_TITLE, ...COVER]) {
        assertEq(coverTexts.filter((t) => t.includes(l)).length, 1, `the cover's line "${l}" goes in one text: ${JSON.stringify(coverTexts)}`);
      }
      assert(plain.length && plain.every((r) => !("ocr" in r)), `the cover's requests say nothing of OCR: ${JSON.stringify(plain)}`);
      const plainBriefs = briefs.slice(briefStart);
      assert(plainBriefs.length === 1 && !("ocr" in plainBriefs[0]), `nor does its brief: ${JSON.stringify(plainBriefs)}`);
      assertNoProblems(page);
      return `scan: ${scan.length} request(s), 1 text; cover: ${plain.length} request(s), ${plain.flatMap((r) => r.texts).length} texts`;
    });
  } finally {
    await ctx?.close();
  }
}
