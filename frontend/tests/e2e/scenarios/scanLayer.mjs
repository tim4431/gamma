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
//
// Then the server's text recognition (docs/dev/pdf_loading.md, gamma/pdf_ocr.py,
// pdf/ocrState.js, pdf/ocrText.js), with the real engine: a printed scan
// whose invisible layer a poor OCR garbled (backend/tests/ocr_fixtures.py
// printed_scan_pdf, built through the backend's Python), read through the
// Document menu's "Recognize text in this PDF":
// - its row is offered for the scan, and the run is a Background tasks row
//   that ends with its summary in the status line;
// - the text layer then holds the recognized words, the printed ones, each
//   span over its printed word, none of the garbled layer's;
// - Ctrl+F finds "resonance", a word only the recognition read, where it is
//   printed;
// - of the two highlights made on the garbled layer before the run, both keep
//   their place; the one whose quote reads like the recognized text takes it
//   as its quote, the one garbled past that guard keeps its own;
// - the metadata popover's text cell says the text was recognized and when,
//   and the Document menu no longer offers to recognize it;
// - the Document menu's "Forget the recognized text…" asks first, then the
//   page reads its garbled layer again, the highlight that took the
//   recognized line quotes the layer again, and Recognize text is offered
//   again;
// - a scan with no layer at all: the popover's cell points to Recognize text,
//   which the Document menu offers.
//
// Then what reaches the other readers and the other servers
// (docs/dev/pdf_loading.md "Recognized words", gamma/ocr_auto.py,
// pdf/ocrRuns.js):
// - a second account's tab open on a shared workspace's scan reads the
//   recognized words once a recognition started elsewhere lands, and its own
//   layer again once they are forgotten, told by the page's socket (`ocr`),
//   without a reload;
// - on a server that leaves the reading to the window (GAMMA_OCR_ENGINE=
//   window), Recognize text reads the page in Chromium with tesseract.js,
//   every file from that server, the `ocr-tab` job's row ends done with its
//   summary, and the text layer and Ctrl+F read what the window recognized;
// - on a server with automatic recognition on (the server option
//   ocr_auto), a scan uploaded whose pages are only pictures is recognized
//   by itself: the workspace's run, its row in Background tasks and its
//   summary on the page shown; Settings → Server → Text recognition shows
//   its rows, and its automatic switch saves.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pageText } from "../../../src/pdf/pageText.js";
import { Account, PYTHON, ROOT, Server, fakeAiModels, live, settingsPane, wanted } from "../harness.mjs";
import { COVER, COVER_TITLE, PAGE_H, PAGE_W, PARAGRAPH, printedWidth, scanPaper } from "../scanPdf.mjs";
import { movePast, selectPdfText, waitForPdf } from "./pdf.mjs";

// What `sel` matches on page `pn` of the viewer shown, in pt from the page's
// top-left corner (y down), with its text. Both papers here are 612 pt wide.
const boxesOn = (page, pn, sel) => page.evaluate(([pn, sel, W]) => {
  const wrap = [...document.querySelectorAll(`.pdfPageWrap[data-page="${pn}"]`)].find((el) => el.offsetParent !== null);
  if (!wrap) return [];
  const at = wrap.getBoundingClientRect(), k = W / at.width;
  return [...wrap.querySelectorAll(sel)].map((el) => {
    const r = el.getBoundingClientRect();
    return { text: el.textContent, x0: (r.left - at.left) * k, x1: (r.right - at.left) * k, top: (r.top - at.top) * k, bottom: (r.bottom - at.top) * k };
  });
}, [pn, sel, PAGE_W]);

// The text layer's spans of page `pn`, once two readings agree (a layer is
// built when the scroller rests) and `ready(spans)` says they are the ones
// awaited.
const restingSpans = (page, until, pn, ready = () => true, timeout = 8000) => {
  let last = "";
  return until(async () => {
    const now = (await boxesOn(page, pn, ".textLayer span")).filter((s) => s.text);
    const same = now.length && JSON.stringify(now) === last;
    last = JSON.stringify(now);
    return same && ready(now) && now;
  }, { what: `page ${pn}'s text layer at rest`, every: 150, timeout });
};

export async function scanLayerScenarios(env) {
  if (!wanted("scan layer")) return;
  await repairedLayerSteps(env);
  await recognitionSteps(env);
  await otherTabSteps(env);
  await windowSteps(env);
  await automaticSteps(env);
}

async function repairedLayerSteps({ server, browser, alice, step, until, assert, assertEq, assertNoProblems, openPage }) {
  const { bytes, words, lines } = scanPaper();
  const wordsOf = (k) => words.filter((w) => w.line === k);
  let ctx = null, page = null, pageId = "", cover = [];
  const requests = [], briefs = [];
  const spansOf = (pn) => restingSpans(page, until, pn);
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
      lies(box(await boxesOn(page, 2, `[data-hl-id="${hl.id}"]`)), "the highlight drawn");
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
        const now = await boxesOn(page, 2, ".pdfFindMark");
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
      const [block] = await boxesOn(page, 2, ".pdfTransPara");
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

// The printed scan of backend/tests/ocr_fixtures.py (printed_scan_pdf) under
// the layer argv[3] names ("garbled", or "none" for none), written to
// argv[2], and where its words are printed (Helvetica's advances, as
// insert_text sets them): {lines: [{text, garbled, base, size, words: [{text,
// x0, x1}]}]}, in pt from the page's top-left corner. PyMuPDF alone, no gamma.
const PRINTED_SCAN_PY = `
import json, sys
sys.path.insert(0, sys.argv[1])
import pymupdf
from ocr_fixtures import GARBLE, PRINTED, printed_scan_pdf
with open(sys.argv[2], "wb") as f:
    f.write(printed_scan_pdf(layer=None if sys.argv[3] == "none" else sys.argv[3]))
lines = []
for x, base, size, text in PRINTED:
    words, at = [], x
    for word in text.split(" "):
        width = pymupdf.get_text_length(word, fontname="helv", fontsize=size)
        words.append({"text": word, "x0": at, "x1": at + width})
        at += width + pymupdf.get_text_length(" ", fontname="helv", fontsize=size)
    lines.append({"text": text, "garbled": text.translate(GARBLE), "base": base, "size": size, "words": words})
print(json.dumps({"lines": lines}))
`;

function printedScan(dir, layer) {
  const file = path.join(dir, `printed-scan-${layer}.pdf`);
  const out = execFileSync(PYTHON, ["-I", "-c", PRINTED_SCAN_PY, path.join(ROOT, "backend", "tests"), file, layer], { encoding: "utf8" });
  return { bytes: fs.readFileSync(file), ...JSON.parse(out) };
}

// The printed scan with no layer, its page argv[3] times over: a scan whose
// pages are all pictures, written to argv[2].
const PICTURE_SCAN_PY = `
import sys
sys.path.insert(0, sys.argv[1])
import pymupdf
from ocr_fixtures import printed_scan_pdf
one = pymupdf.open(stream=printed_scan_pdf(layer=None), filetype="pdf")
doc = pymupdf.open()
for _ in range(int(sys.argv[3])):
    doc.insert_pdf(one)
with open(sys.argv[2], "wb") as f:
    f.write(doc.tobytes(deflate=True))
`;

function pictureScan(dir, pages) {
  const file = path.join(dir, `picture-scan-${pages}.pdf`);
  execFileSync(PYTHON, ["-I", "-c", PICTURE_SCAN_PY, path.join(ROOT, "backend", "tests"), file, String(pages)]);
  return fs.readFileSync(file);
}

// JSON with its keys in order: the same position however a write re-serialized it.
const canon = (v) => JSON.stringify(v, (_k, x) => (x && typeof x === "object" && !Array.isArray(x)
  ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) : x));

// The printed words a text layer should read, a span a word with the space
// folded in, from lines of words.
const spanTexts = (lineWords) => lineWords.flatMap((ws) => ws.map((w, i) => (i < ws.length - 1 ? `${w} ` : w)));

// Ctrl+F for `word` on `page`: one match, and its marks on page `pn`.
async function findMarks(page, until, word, pn = 1) {
  await page.keyboard.press("ControlOrMeta+f");
  const input = page.locator(".searchPopover .searchInput");
  await input.waitFor();
  await input.fill(word);
  await until(async () => (await page.locator(".searchPopover .searchFindCount").textContent({ timeout: 500 }).catch(() => "")) === "1/1",
    { what: `find count 1/1 for "${word}"` });
  const marks = await until(async () => {
    const now = await boxesOn(page, pn, ".pdfFindMark");
    return now.length && now;
  }, { what: `the find mark of "${word}"` });
  await page.keyboard.press("Escape");
  return marks;
}

// What the status line says once it matches `re`.
const statusSaid = (page, until, re, timeout = 120000) => until(async () => {
  const now = (await page.locator(".statusPill").textContent({ timeout: 500 }).catch(() => "")) || "";
  return re.test(now) && now.trim();
}, { what: `the status line matching ${re}`, timeout, every: 250 });

// The Background tasks row titled `title`, once it is there; the tray stays open.
async function taskRow(page, title) {
  await page.click("button[aria-label='Background tasks']");
  const row = page.locator(live(".tasksPopover .taskRow"), { hasText: title });
  await row.waitFor();
  return row;
}

// A job's end, read until it is over.
const jobEnd = (who, until, id, timeout = 120000) => until(async () => {
  const job = await who.api(`/api/jobs/${id}`);
  return ["done", "failed", "cancelled"].includes(job.state) && job;
}, { what: `job ${id}'s end`, timeout, every: 250 });

async function recognitionSteps({ server, browser, alice, step, until, assert, assertEq, assertNoProblems, openPage }) {
  let ctx = null, page = null, pageId = "", docId = "", title = "", lines = [];
  // the highlights made on the garbled layer, by printed line
  const made = {};
  const children = async () => (await alice.api(`/api/blocks/${pageId}/subtree`)).block.children || [];
  const spansOf = (ready, timeout) => restingSpans(page, until, 1, ready, timeout);
  const pt = (v) => v.toFixed(2);
  // The middle of a printed line's letters, pt from the page's top: half
  // Helvetica's x-height over the baseline.
  const middleOf = (line) => line.base - 0.27 * line.size;
  const documentMenu = async () => {
    await page.click("button[aria-label='Document']");
    await page.locator(".attachPopover").waitFor();
  };
  const recognizeRow = () => page.locator('.attachPopover [data-ocr="recognize"]');

  // Select `phrase` on page 1's text layer, across its spans, and let go: the
  // selection popup opens, as after a drag.
  const selectPhrase = async (phrase) => {
    const ok = await page.evaluate((phrase) => {
      const wrap = [...document.querySelectorAll('.pdfPageWrap[data-page="1"]')].find((el) => el.offsetParent !== null);
      const nodes = [...(wrap?.querySelectorAll(".textLayer span") || [])].map((s) => s.firstChild).filter((n) => n?.nodeType === Node.TEXT_NODE);
      const starts = [];
      let text = "";
      for (const n of nodes) { starts.push(text.length); text += n.data; }
      const i = text.indexOf(phrase);
      if (i < 0) return false;
      // the text node holding character k, or for an end the one it ends
      const at = (k, end) => {
        let j = 0;
        while (j + 1 < nodes.length && (end ? starts[j + 1] < k : starts[j + 1] <= k)) j++;
        return [nodes[j], k - starts[j]];
      };
      const range = document.createRange();
      range.setStart(...at(i, false));
      range.setEnd(...at(i + phrase.length, true));
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      return true;
    }, phrase);
    if (!ok) throw new Error(`"${phrase}" is not on page 1's text layer`);
  };
  // `phrase` highlighted from the selection popup's first swatch: the block saved.
  const highlight = async (phrase) => {
    const had = new Set((await children()).map((b) => b.id));
    await selectPhrase(phrase);
    const tip = page.locator(".plainTip");
    await tip.waitFor();
    await tip.locator(".colorBtn").first().click();
    const hl = await until(async () => (await children()).find((b) => !had.has(b.id) && b.properties?.pdf_position),
      { what: `the highlight of "${phrase}" saved` });
    await tip.waitFor({ state: "detached" });
    await page.evaluate(() => getSelection().removeAllRanges());
    return hl;
  };
  // Ctrl+F for `word`: one match, and its marks on page 1.
  const findOne = async (word) => {
    await page.keyboard.press("ControlOrMeta+f");
    const input = page.locator(".searchPopover .searchInput");
    await input.waitFor();
    await input.fill(word);
    await until(async () => (await page.locator(".searchPopover .searchFindCount").textContent({ timeout: 500 }).catch(() => "")) === "1/1",
      { what: `find count 1/1 for "${word}"` });
    const marks = await until(async () => {
      const now = await boxesOn(page, 1, ".pdfFindMark");
      return now.length && now;
    }, { what: `the find mark of "${word}"` });
    await page.keyboard.press("Escape");
    return marks;
  };
  // The metadata popover's text cell, once its check is in (its title says what it found).
  const textCell = async (ready) => {
    await page.click("button[aria-label='Paper metadata']");
    const cell = page.locator(".metaPopover .metaStatus .metaCell").first();
    const title = await until(async () => {
      const now = await cell.getAttribute("title", { timeout: 500 }).catch(() => "");
      return ready(now || "") && now;
    }, { what: "the text cell's check" });
    const text = (await cell.innerText()).trim();
    await page.click("button[aria-label='Paper metadata']");
    return { title, text };
  };

  try {
    await step("scan layer: recognition setup, a printed scan under a garbled invisible layer, uploaded to a server whose engine runs", async () => {
      const scan = printedScan(server.dir, "garbled");
      lines = scan.lines;
      // What pdf.js and pdf/pageText.js read of it before any recognition:
      // the garbled layer, a scan's.
      const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
      const doc = await pdfjs.getDocument({ data: new Uint8Array(scan.bytes), verbosity: 0 }).promise;
      try {
        const tc = await pageText(await doc.getPage(1));
        assertEq(tc.repaired, true, "pdf/pageText.js calls the page a scan");
        const read = tc.items.map((it) => `${it.str}${it.hasEOL ? "\n" : ""}`).join("").split("\n").map((s) => s.trim()).filter(Boolean);
        assertEq(JSON.stringify(read), JSON.stringify(lines.map((l) => l.garbled)), "its layer reads the garbled lines");
      } finally {
        await doc.destroy();
      }
      const config = await alice.api("/api/server-config");
      assert(config.ocr?.engine === "tesseract", `the server recognizes text: ${JSON.stringify(config.ocr)}`);

      const up = await alice.upload("/api/uploads", scan.bytes, "printed-scan.pdf", "application/pdf");
      docId = up.doc_id;
      const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Printed scan", source_url: up.source_url } });
      pageId = created.id;
      title = (await alice.api(`/api/blocks/${pageId}`)).content || "Printed scan";
      ctx = await alice.context(browser);
      page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${alice.ws}`);
      await waitForPdf(page, 1);
      const words = (await spansOf()).map((s) => s.text.trim());
      assert(words.includes("rc50m4mcc") && !words.includes("resonance"), `the text layer spells "resonance" as the layer does: ${JSON.stringify(words)}`);
      assertNoProblems(page);
      return `"${title}", ${lines.length} printed lines`;
    });

    await step("scan layer: on the garbled layer, two lines are highlighted with its spelling, and Ctrl+F finds its misspelling", async () => {
      for (const k of [2, 4]) {
        const hl = await highlight(lines[k].garbled);
        assertEq(hl.properties.quote, lines[k].garbled, `line ${k + 1}'s highlight quotes the layer`);
        made[k] = hl;
      }
      const [mark, ...more] = await findOne("rc50m4mcc");
      assertEq(more.length, 0, "one mark");
      const mid = middleOf(lines[4]);
      assert(mark.top <= mid && mid <= mark.bottom, `the mark ${pt(mark.top)}..${pt(mark.bottom)} is on line 5 (its letters' middle ${pt(mid)})`);
      assertNoProblems(page);
    });

    await step("scan layer: the Document menu's Recognize text in this PDF runs as a Background tasks row that ends with its summary", async () => {
      await documentMenu();
      const row = recognizeRow();
      // offered once the viewer met the scan: the server has not read the file yet
      await row.waitFor();
      assertEq((await row.innerText()).trim(), "Recognize text in this PDF");
      assertEq(await row.getAttribute("title"), "Read the words of this PDF's scanned pages from their pictures: selection, search, the AI and translation read them, and each highlight there keeps its place and takes the new text. The PDF file itself is unchanged.");
      assert(await row.isEnabled(), "the row can be chosen");
      await row.click();
      const said = await until(async () => {
        const now = (await page.locator(".statusPill").textContent({ timeout: 500 }).catch(() => "")) || "";
        return /Text recognized on|Could not start|failed/.test(now) && now.trim();
      }, { what: "the run's summary in the status line", timeout: 120000, every: 250 });
      assertEq(said, `Text recognized on 1 page of “${title}”. 1 highlight took the new text.`, "the summary");
      await page.click("button[aria-label='Background tasks']");
      const task = page.locator(live(".tasksPopover .taskRow"), { hasText: `Recognizing the text of “${title}”` });
      await task.waitFor();
      assert(await task.evaluate((el) => el.classList.contains("done")), `the row ended done: ${await task.getAttribute("class")}`);
      await page.keyboard.press("Escape");
      // what the job did
      const job = (await alice.api("/api/jobs")).jobs.find((j) => j.kind === "ocr" && j.params?.doc_id === docId);
      const { result } = await alice.api(`/api/jobs/${job.id}`);
      assertEq(JSON.stringify(["scanned", "recognized", "kept", "empty", "failed", "good_layer", "highlights_respelled", "highlights_kept"].map((k) => result[k])),
        JSON.stringify([1, 1, 0, 0, 0, false, 1, 1]), `its result: ${JSON.stringify(result)}`);
      assert(result.shared < 0.93, `the layer shares under 93% of its words with the recognition: ${result.shared}`);
      assertNoProblems(page);
      return `${said} (the layer shared ${Math.round(result.shared * 100)}% of its words)`;
    });

    await step("scan layer: the text layer then holds the recognized words, the printed ones, each span over its printed word", async () => {
      const spans = await spansOf((now) => now.some((s) => s.text.trim() === "resonance"), 15000);
      const got = await alice.api(`/api/ocr/${docId}/pages?first=1&last=1`);
      const read = got.pages["1"].lines.map((line) => line.map((w) => w[0]));
      assertEq(JSON.stringify(read), JSON.stringify(lines.map((l) => l.text.split(" "))), "the engine read the printed words");
      assertEq(JSON.stringify(spans.map((s) => s.text)), JSON.stringify(read.flatMap((ws) => ws.map((w, i) => (i < ws.length - 1 ? `${w} ` : w)))),
        "a span a recognized word, the space folded in");
      const misplaced = [];
      let n = 0;
      lines.forEach((line, k) => {
        const mid = middleOf(line);
        for (const w of line.words) {
          const s = spans[n++];
          const at = `line ${k + 1}, "${w.text}" printed ${pt(w.x0)}..${pt(w.x1)} (letters' middle ${pt(mid)}), its span ${pt(s.x0)}..${pt(s.x1)} × ${pt(s.top)}..${pt(s.bottom)}`;
          const centre = (w.x0 + w.x1) / 2;
          if (!(s.x0 <= centre && centre <= s.x1)) misplaced.push(`${at}: the printed word's middle is outside its span`);
          if (!(s.top <= mid && mid <= s.bottom)) misplaced.push(`${at}: off its printed line`);
          const other = lines.find((o, j) => j !== k && s.top <= middleOf(o) && middleOf(o) <= s.bottom);
          if (other) misplaced.push(`${at}: reaches the line "${other.text}"`);
        }
      });
      assertEq(misplaced.join("\n          "), "", "every recognized word's span over its printed word");
      assertNoProblems(page);
      return `${spans.length} spans, the stamp ${got.stamp}`;
    });

    await step("scan layer: Ctrl+F finds a word only the recognition read, where it is printed", async () => {
      const line = lines[4], word = line.words.find((w) => w.text === "resonance");
      const [m, ...more] = await findOne("resonance");
      assertEq(more.length, 0, "one mark");
      const centre = (word.x0 + word.x1) / 2, mid = middleOf(line);
      assert(m.x0 <= centre && centre <= m.x1, `the mark ${pt(m.x0)}..${pt(m.x1)} is over "resonance" printed ${pt(word.x0)}..${pt(word.x1)}`);
      const cover = (Math.min(m.x1, word.x1) - Math.max(m.x0, word.x0)) / (word.x1 - word.x0);
      assert(cover >= 0.8, `the mark covers ${cover.toFixed(2)} of the printed word`);
      assert(m.top <= mid && mid <= m.bottom, `the mark ${pt(m.top)}..${pt(m.bottom)} is on its line (letters' middle ${pt(mid)})`);
      assertNoProblems(page);
      return `mark ${pt(m.x0)}..${pt(m.x1)} × ${pt(m.top)}..${pt(m.bottom)} over "resonance" ${pt(word.x0)}..${pt(word.x1)}`;
    });

    // The respell's guard (highlight_relocate.respell_quotes): a quote takes
    // the recognized text only where that reads like the layer's text under
    // its rectangles, or a rectangle drawn off its line would take the line
    // next to it. Line 3's layer misreads 19 of its 42 letters and still
    // reads alike enough (difflib's likeness 0.61; the guard's QUOTE_AGREES
    // is 0.6); line 5's misreads 23 of 39 (0.50) and keeps its quote. Both
    // keep their place: the picture did not change.
    await step("scan layer: the highlights made before keep their place; the one reading like the recognized line takes it as its quote, one garbled past that keeps its own", async () => {
      const now = Object.fromEntries((await children()).map((b) => [b.id, b.properties]));
      const [respelled, kept] = [made[2], made[4]];
      assertEq(now[respelled.id].quote, lines[2].text, "line 3's highlight quotes the recognized line");
      assertEq(now[kept.id].quote, lines[4].garbled, "line 5's keeps the layer's spelling");
      for (const hl of [respelled, kept]) {
        assertEq(canon(now[hl.id].pdf_position), canon(hl.properties.pdf_position), `"${hl.properties.quote}" keeps its place`);
      }
      // as the page shows them: the notes' quotes, the highlights on their lines
      await until(async () => (await page.locator(".blockQuote").allInnerTexts()).map((s) => s.trim()).includes(lines[2].text),
        { what: "the notes show the new quote" });
      assert((await page.locator(".blockQuote").allInnerTexts()).map((s) => s.trim()).includes(lines[4].garbled), "and the kept one");
      for (const [k, hl] of [[2, respelled], [4, kept]]) {
        const drawn = await boxesOn(page, 1, `[data-hl-id="${hl.id}"]`);
        const mid = middleOf(lines[k]);
        assert(drawn.length && Math.min(...drawn.map((r) => r.top)) <= mid && mid <= Math.max(...drawn.map((r) => r.bottom)),
          `line ${k + 1}'s highlight is drawn on it: ${JSON.stringify(drawn.map((r) => [pt(r.top), pt(r.bottom)]))}, letters' middle ${pt(mid)}`);
      }
      assertNoProblems(page);
    });

    await step("scan layer: the metadata popover says the text was recognized and when, and the Document menu offers no more recognition", async () => {
      const state = await alice.api(`/api/ocr/${docId}`);
      assertEq(JSON.stringify([state.recognized, state.scanned, state.pending]), JSON.stringify([[1], 1, false]), `the file's recognition: ${JSON.stringify(state)}`);
      const date = new Intl.DateTimeFormat("en-US", { dateStyle: "medium" }).format(new Date(state.at));
      const cell = await textCell((title) => title.startsWith("Text recognized"));
      assertEq(cell.title, `Text recognized from the page pictures on ${date}: the AI and search read it; the PDF file itself is unchanged.`);
      assertEq(cell.text, "text");
      await documentMenu();
      await page.locator(".attachPopover .ctxMenuItem", { hasText: "Find highlights in this PDF" }).waitFor();
      assertEq(await recognizeRow().count(), 0, "every scanned page is recognized: no Recognize text in this PDF");
      await page.keyboard.press("Escape");
      assertNoProblems(page);
      return cell.title;
    });

    // The respell back (highlight_relocate.respell_quotes, back=True): line
    // 3's quote, the recognized line, reads like the recognized text under
    // its rectangles and takes the layer's spelling again; line 5's already
    // is the layer's.
    let stampBefore = "";
    await step("scan layer: Forget the recognized text…, a danger row of the Document menu, asks first; its Forget runs as a Background tasks row that ends with its summary", async () => {
      stampBefore = (await alice.api(`/api/ocr/${docId}`)).stamp;
      const forgetRow = page.locator('.attachPopover [data-ocr="forget"]');
      await documentMenu();
      await forgetRow.waitFor();
      assertEq((await forgetRow.innerText()).trim(), "Forget the recognized text…");
      assertEq(await forgetRow.getAttribute("title"), "Read this PDF's own text again in place of the words recognized from its page pictures, for everyone in this workspace");
      assert(await forgetRow.evaluate((el) => el.classList.contains("danger")), "a danger row");
      assert(await forgetRow.isEnabled(), "the row can be chosen");
      await forgetRow.click();
      const confirm = page.locator(".confirmModal");
      await confirm.waitFor();
      assertEq((await confirm.locator(".reportModalTitle").innerText()).trim(), "Forget the recognized text?");
      assertEq((await confirm.locator(".confirmMessage").innerText()).trim(),
        `The words recognized from the page pictures of “${title}” are forgotten for everyone in this workspace: selection, search, the AI and translation read the PDF's own text again, and a page that is only a picture has no text. Highlights there take the PDF's own text as their quote where it reads under them. Recognizing it again takes the time it took.`);
      assert(await confirm.locator(".confirmIcon.danger").count(), "a destructive action's chip");
      // Cancel forgets nothing.
      await confirm.locator(".reportModalBtns button", { hasText: "Cancel" }).click();
      await confirm.waitFor({ state: "detached" });
      assert(!(await alice.api("/api/jobs")).jobs.some((j) => j.kind === "ocr-drop"), "no forget started on Cancel");
      assertEq((await alice.api(`/api/ocr/${docId}`)).stamp, stampBefore, "the recognized text stays");

      await documentMenu();
      await forgetRow.click();
      await confirm.locator(".reportModalBtns button", { hasText: "Forget" }).click();
      const said = await statusSaid(page, until, /reads its own text again|Could not start|failed/, 60000);
      assertEq(said, `“${title}” reads its own text again on 1 page. 1 highlight took the PDF's own text as its quote.`, "the summary");
      const task = await taskRow(page, `Forgetting the recognized text of “${title}”`);
      assert(await task.evaluate((el) => el.classList.contains("done")), `the row ended done: ${await task.getAttribute("class")}`);
      await page.keyboard.press("Escape");
      const job = (await alice.api("/api/jobs")).jobs.find((j) => j.kind === "ocr-drop" && j.params?.doc_id === docId);
      const { result } = await alice.api(`/api/jobs/${job.id}`);
      assertEq(JSON.stringify(["pages", "highlights_respelled", "highlights_kept", "highlights_changed", "highlights_unread"].map((k) => result[k])),
        JSON.stringify([1, 1, 0, 0, 0]), `its result: ${JSON.stringify(result)}`);
      assertNoProblems(page);
      return said;
    });

    await step("scan layer: after the forget, the text layer and Ctrl+F read the garbled layer again, the highlight that took the recognized line quotes the layer again, both in place, and Recognize text is offered again", async () => {
      const spans = await spansOf((now) => now.some((s) => s.text.trim() === "rc50m4mcc"), 15000);
      assertEq(JSON.stringify(spans.map((s) => s.text)), JSON.stringify(spanTexts(lines.map((l) => l.garbled.split(" ")))),
        "a span a word of the garbled layer, as before the recognition");
      const now = Object.fromEntries((await children()).map((b) => [b.id, b.properties]));
      assertEq(now[made[2].id].quote, lines[2].garbled, "line 3's highlight quotes the layer again");
      assertEq(now[made[4].id].quote, lines[4].garbled, "line 5's keeps the layer's spelling");
      for (const k of [2, 4]) {
        assertEq(canon(now[made[k].id].pdf_position), canon(made[k].properties.pdf_position), `line ${k + 1}'s highlight keeps its place`);
      }
      await until(async () => (await page.locator(".blockQuote").allInnerTexts()).map((s) => s.trim()).includes(lines[2].garbled),
        { what: "the notes show the layer's quote again" });
      const [mark, ...more] = await findMarks(page, until, "rc50m4mcc");
      assertEq(more.length, 0, "one mark");
      const mid = middleOf(lines[4]);
      assert(mark.top <= mid && mid <= mark.bottom, `the mark ${pt(mark.top)}..${pt(mark.bottom)} is on line 5 (its letters' middle ${pt(mid)})`);
      const state = await alice.api(`/api/ocr/${docId}`);
      assertEq(JSON.stringify([state.recognized, state.pending, state.todo, state.staged]), JSON.stringify([[], false, 1, 0]),
        `the file's recognition: ${JSON.stringify(state)}`);
      assert(state.stamp && state.stamp !== stampBefore, `a stamp of its own: ${state.stamp} after ${stampBefore}`);
      await documentMenu();
      await recognizeRow().waitFor();
      assertEq((await recognizeRow().innerText()).trim(), "Recognize text in this PDF");
      assert(await recognizeRow().isEnabled(), "the row can be chosen");
      assertEq(await page.locator('.attachPopover [data-ocr="forget"]').count(), 0, "nothing left to forget");
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    });

    await step("scan layer: a scan with no layer: the metadata popover's text cell points to Recognize text in this PDF, which the Document menu offers", async () => {
      const bare = printedScan(server.dir, "none");
      const up = await alice.upload("/api/uploads", bare.bytes, "picture-scan.pdf", "application/pdf");
      const created = await alice.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Picture scan", source_url: up.source_url } });
      await page.goto(`${server.base}/?page=${created.id}&ws=${alice.ws}`);
      // the canvas on screen: once the page is painted, the first in the DOM is its hidden twin (pdf/pageRaster.js `pair`)
      await page.locator('.pdfPageWrap[data-page="1"] .pdfPageCanvas > canvas:not(.pdfDetailCanvas):not([data-spare])').waitFor();
      await documentMenu();
      // offered once the viewer read the page and found no text on it
      await recognizeRow().waitFor({ timeout: 15000 });
      assert(await recognizeRow().isEnabled(), "the row can be chosen");
      await page.keyboard.press("Escape");
      const cell = await textCell((title) => title.startsWith("No text layer"));
      assertEq(cell.title, "No text layer — a scan? Choose Recognize text in this PDF in the Document menu so the AI and search can read it.");
      assertEq(cell.text, "no text");
      assertNoProblems(page);
    });
  } finally {
    await ctx?.close();
  }
}

// A second account's tab on a shared workspace's scan, while the file's
// recognized text changes elsewhere (gamma/pdf_ocr.py announce,
// collab.publish_file; collaboration/collabSession.js onOcr): the job is
// another account's, so the tab hears of it from its page's socket alone.
async function otherTabSteps({ server, browser, alice, bob, step, until, assert, assertEq, assertNoProblems, openPage }) {
  let ctx = null, page = null, ann = null, pageId = "", lines = [], ws = "";
  const spansOf = (ready, timeout) => restingSpans(page, until, 1, ready, timeout);
  const printed = () => spanTexts(lines.map((l) => l.text.split(" ")));
  const garbled = () => spanTexts(lines.map((l) => l.garbled.split(" ")));
  try {
    await step("scan layer: another tab: a printed scan under a garbled layer in a shared workspace, open in a second account's tab", async () => {
      const out = await server.manage("create-workspace", "Scan lab", "alice", "shared");
      ws = (out.match(/workspace (\S+)/) || [])[1];
      assert(ws, `the shared workspace's id from: ${out}`);
      await server.manage("set-member", ws, "bob", "editor");
      // the same sessions, bound to the shared workspace
      const as = (who) => Object.assign(new Account(server, who.name, who.password), { session: who.session, defaultWs: who.defaultWs, ws });
      ann = as(alice);
      const ben = as(bob);
      const scan = printedScan(server.dir, "garbled");
      lines = scan.lines;
      const up = await ann.upload("/api/uploads", scan.bytes, "lab-scan.pdf", "application/pdf");
      pageId = (await ann.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Lab scan", source_url: up.source_url } })).id;
      ctx = await ben.context(browser);
      page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${ws}`);
      await waitForPdf(page, 1);
      assertEq(JSON.stringify((await spansOf()).map((s) => s.text)), JSON.stringify(garbled()), "the tab reads the garbled layer");
      // gone with a reload
      await page.evaluate(() => { window.__e2eSameLoad = true; });
      assertNoProblems(page);
    });

    await step("scan layer: another tab: a recognition the first account starts reaches the second account's open tab by its socket: its text layer and Ctrl+F read the recognized words, with no reload", async () => {
      const job = await ann.api("/api/jobs/ocr", { method: "POST", body: { page_id: pageId } });
      const ended = await jobEnd(ann, until, job.id);
      assertEq(ended.state, "done", `the recognition ended: ${JSON.stringify(ended)}`);
      const t0 = Date.now();
      const spans = await spansOf((now) => now.some((s) => s.text.trim() === "resonance"), 15000);
      const ms = Date.now() - t0;
      assertEq(JSON.stringify(spans.map((s) => s.text)), JSON.stringify(printed()), "a span a recognized word");
      assertEq(await page.evaluate(() => window.__e2eSameLoad), true, "the tab was not reloaded");
      const [mark, ...more] = await findMarks(page, until, "resonance");
      assertEq(more.length, 0, "one mark");
      const mid = lines[4].base - 0.27 * lines[4].size;
      assert(mark.top <= mid && mid <= mark.bottom, `the mark ${mark.top.toFixed(2)}..${mark.bottom.toFixed(2)} is on line 5`);
      assertNoProblems(page);
      return `the tab read the recognized words ${ms} ms after the job ended`;
    });

    await step("scan layer: another tab: the first account forgets the recognized text, and the second account's open tab reads its own layer again, with no reload", async () => {
      const job = await ann.api("/api/jobs/ocr-drop", { method: "POST", body: { page_id: pageId } });
      const ended = await jobEnd(ann, until, job.id, 60000);
      assertEq(ended.state, "done", `the forget ended: ${JSON.stringify(ended)}`);
      const t0 = Date.now();
      const spans = await spansOf((now) => now.some((s) => s.text.trim() === "rc50m4mcc"), 15000);
      const ms = Date.now() - t0;
      assertEq(JSON.stringify(spans.map((s) => s.text)), JSON.stringify(garbled()), "the garbled layer again");
      assertEq(await page.evaluate(() => window.__e2eSameLoad), true, "the tab was not reloaded");
      assertNoProblems(page);
      return `the tab read its own layer again ${ms} ms after the job ended`;
    });
  } finally {
    await ctx?.close();
  }
}

// A server that reads no text itself and leaves the reading to the window
// that asks (GAMMA_OCR_ENGINE=window, as the apps' servers): the Document
// menu's Recognize text reads the page in this Chromium with tesseract.js
// (pdf/ocrRuns.js, pdf/ocrTab.js, pdf/ocrLoop.js), sends its words, and the
// server's `ocr-tab` job judges and applies them.
async function windowSteps({ browser, step, until, assert, assertEq, assertNoProblems, openPage }) {
  const win = new Server({ env: { GAMMA_OCR_ENGINE: "window" } });
  let ctx = null, page = null, reader = null, pageId = "", docId = "", title = "", lines = [], config = null;
  const requests = [];
  const spansOf = (ready, timeout) => restingSpans(page, until, 1, ready, timeout);
  try {
    await step("scan layer: in the window: setup, a server that reads no text itself, and a printed scan under a garbled layer open there", async () => {
      await win.start();
      await win.manage("create-user", "win-reader", "win-reader-pw");
      reader = await new Account(win, "win-reader", "win-reader-pw").login();
      config = (await reader.api("/api/server-config")).ocr;
      assert(config?.where === "window" && config.engine === "tesseract.js 7.0.0" && /^\/api\/ocr\/models\/[0-9a-f]{12}$/.test(config.models || "")
        && config.auto === false, `the server leaves the reading to the window: ${JSON.stringify(config)}`);
      const scan = printedScan(win.dir, "garbled");
      lines = scan.lines;
      const up = await reader.upload("/api/uploads", scan.bytes, "window-scan.pdf", "application/pdf");
      docId = up.doc_id;
      pageId = (await reader.api(`/api/blocks/by-doc/${docId}`, { method: "POST", body: { default_title: "Window scan", source_url: up.source_url } })).id;
      title = (await reader.api(`/api/blocks/${pageId}`)).content || "Window scan";
      ctx = await reader.context(browser);
      // every request the page and its workers make
      ctx.on("request", (r) => requests.push(r.url()));
      page = await openPage(ctx, `${win.base}/?page=${pageId}&ws=${reader.ws}`);
      await waitForPdf(page, 1);
      assert((await spansOf()).some((s) => s.text.trim() === "rc50m4mcc"), "the text layer reads the garbled layer");
      assertNoProblems(page);
      return `"${title}"`;
    });

    await step("scan layer: in the window: Recognize text in this PDF reads the page in this window, every file from its server, and the job's row in Background tasks ends done with its summary", async () => {
      await page.click("button[aria-label='Document']");
      await page.locator(".attachPopover").waitFor();
      const row = page.locator('.attachPopover [data-ocr="recognize"]');
      await row.waitFor();
      assertEq((await row.innerText()).trim(), "Recognize text in this PDF");
      assertEq(await row.getAttribute("title"), "Read the words of this PDF's scanned pages from their pictures: selection, search, the AI and translation read them, and each highlight there keeps its place and takes the new text. The PDF file itself is unchanged. The pages are read in this window: keep it open and in front until it is done.");
      const t0 = Date.now();
      await row.click();
      const said = await statusSaid(page, until, /Text recognized on|Could not start|failed/);
      const seconds = (Date.now() - t0) / 1000;
      assertEq(said, `Text recognized on 1 page of “${title}”.`, "the summary");
      const task = await taskRow(page, `Reading the text of “${title}” in a window`);
      assert(await task.evaluate((el) => el.classList.contains("done")), `the row ended done: ${await task.getAttribute("class")}`);
      await page.keyboard.press("Escape");
      const job = (await reader.api("/api/jobs")).jobs.find((j) => j.kind === "ocr-tab" && j.params?.doc_id === docId);
      assert(job, "an ocr-tab job");
      const { result } = await reader.api(`/api/jobs/${job.id}`);
      assertEq(JSON.stringify(["scanned", "recognized", "kept", "empty", "failed", "good_layer"].map((k) => result[k])),
        JSON.stringify([1, 1, 0, 0, 0, false]), `its result: ${JSON.stringify(result)}`);
      const state = await reader.api(`/api/ocr/${docId}`);
      assertEq(JSON.stringify([state.recognized, state.engine, state.pending]), JSON.stringify([[1], "tesseract.js 7.0.0", false]),
        `the file's recognition: ${JSON.stringify(state)}`);
      // tesseract.js's own defaults are a CDN's: nothing went there
      const away = requests.filter((u) => !u.startsWith(win.base) && !/^(data|blob):/.test(u));
      assertEq(away.join(", "), "", "every request went to the server");
      for (const lang of ["eng", "fra", "deu"]) {
        assert(requests.some((u) => u === `${win.base}${config.models}/${lang}.traineddata`),
          `the ${lang} model came from the server: ${requests.filter((u) => /traineddata|tesseract|worker/.test(u)).join(", ")}`);
      }
      assert(requests.some((u) => /\/assets\/tesseract-core-simd-lstm\.wasm-[\w-]+\.js$/.test(u)) && requests.some((u) => /\/assets\/worker\.min-[\w-]+\.js$/.test(u)),
        "the core and the worker are the build's hashed assets");
      assertNoProblems(page);
      return `${said} (${seconds.toFixed(1)} s from the click, the engine's start included)`;
    });

    await step("scan layer: in the window: the text layer reads the words the window recognized, each on its printed line, and Ctrl+F finds one only the recognition read", async () => {
      const spans = await spansOf((now) => now.some((s) => s.text.trim() === "resonance"), 15000);
      const got = await reader.api(`/api/ocr/${docId}/pages?first=1&last=1`);
      const read = got.pages["1"].lines.map((line) => line.map((w) => w[0]));
      assertEq(JSON.stringify(read), JSON.stringify(lines.map((l) => l.text.split(" "))), "the window read the printed words");
      assertEq(JSON.stringify(spans.map((s) => s.text)), JSON.stringify(spanTexts(read)), "a span a recognized word");
      let n = 0;
      const off = [];
      lines.forEach((line, k) => {
        const mid = line.base - 0.27 * line.size;
        for (const w of line.words) {
          const s = spans[n++];
          const centre = (w.x0 + w.x1) / 2;
          if (!(s.x0 <= centre && centre <= s.x1 && s.top <= mid && mid <= s.bottom)) {
            off.push(`line ${k + 1}, "${w.text}" printed ${w.x0.toFixed(2)}..${w.x1.toFixed(2)}, its span ${s.x0.toFixed(2)}..${s.x1.toFixed(2)} × ${s.top.toFixed(2)}..${s.bottom.toFixed(2)}`);
          }
        }
      });
      assertEq(off.join("\n          "), "", "every recognized word's span over its printed word");
      const word = lines[4].words.find((w) => w.text === "resonance");
      const [m, ...more] = await findMarks(page, until, "resonance");
      assertEq(more.length, 0, "one mark");
      const centre = (word.x0 + word.x1) / 2;
      assert(m.x0 <= centre && centre <= m.x1, `the mark ${m.x0.toFixed(2)}..${m.x1.toFixed(2)} is over "resonance" printed ${word.x0.toFixed(2)}..${word.x1.toFixed(2)}`);
      assertNoProblems(page);
      return `${spans.length} spans`;
    });
  } finally {
    await ctx?.close();
    await win.stop();
  }
}

// A server that recognizes the text of a scan uploaded with no text by
// itself (the server option ocr_auto on; gamma/ocr_auto.py): the run is the workspace's
// own, one file at a time, and a tab showing the page says when it is done
// (tasks/taskKinds.js ocrEnding), whether its tasks store saw the run going
// or only heard of its end from the page socket (useTasks expectEnd). The
// scan is long enough (a quarter of a second a page here) for the tab
// usually to open on it while it still goes.
const AUTO_PAGES = 40;

async function automaticSteps({ browser, step, until, assert, assertEq, assertNoProblems, openPage }) {
  const auto = new Server({ options: { ocr_auto: "on" } });
  let ctx = null, page = null, owner = null;
  try {
    await step("scan layer: automatically: setup, a server that recognizes scans by itself", async () => {
      await auto.start();
      await auto.manage("create-user", "auto-owner", "auto-owner-pw");
      await auto.manage("set-admin", "auto-owner", "on");
      owner = await new Account(auto, "auto-owner", "auto-owner-pw").login();
      const config = (await owner.api("/api/server-config")).ocr;
      assert(config?.where === "server" && config.auto === true, `the server recognizes scans by itself: ${JSON.stringify(config)}`);
      ctx = await owner.context(browser);
      page = await openPage(ctx, `${auto.base}/?ws=${owner.ws}`);
      await page.waitForSelector(".folderNewBtn");
      assertNoProblems(page);
    });

    await step("scan layer: automatically: a scan uploaded whose pages are only pictures is recognized by itself, as the workspace's run in Background tasks, and the tab showing it says so when it is done", async () => {
      const up = await owner.upload("/api/uploads", pictureScan(auto.dir, AUTO_PAGES), "picture-book.pdf", "application/pdf");
      const docId = up.doc_id;
      const pageId = (await owner.api(`/api/blocks/by-doc/${docId}`, { method: "POST", body: { default_title: "Picture book", source_url: up.source_url } })).id;
      await page.goto(`${auto.base}/?page=${pageId}&ws=${owner.ws}`);
      await waitForPdf(page, 1);
      const title = (await owner.api(`/api/blocks/${pageId}`)).content || "Picture book";
      const job = await until(async () => (await owner.api("/api/jobs")).jobs.find((j) => j.kind === "ocr" && j.params?.doc_id === docId),
        { what: "the automatic run's job", timeout: 15000 });
      assertEq(JSON.stringify([job.owner, job.params.auto, job.params.page_id, job.params.title]), JSON.stringify(["", true, pageId, title]),
        `the workspace's own run, of the page uploaded: ${JSON.stringify(job)}`);
      // the open tray reads the jobs every 1.5 s; the row may already be done
      // (a loaded machine opens the tab late): the summary below comes either
      // way, from the page socket's `ocr` message (useTasks expectEnd)
      const task = await taskRow(page, `Recognizing the text of “${title}” automatically`);
      await page.keyboard.press("Escape");
      const said = await statusSaid(page, until, /Text recognized automatically|failed/);
      assertEq(said, `Text recognized automatically on ${AUTO_PAGES} pages of “${title}”.`, "the summary");
      const ended = await jobEnd(owner, until, job.id, 10000);
      assertEq(JSON.stringify([ended.state, ended.result?.recognized, ended.result?.scanned]), JSON.stringify(["done", AUTO_PAGES, AUTO_PAGES]),
        `the run: ${JSON.stringify(ended)}`);
      await page.click("button[aria-label='Background tasks']");
      await until(() => task.evaluate((el) => el.classList.contains("done")), { what: "the row ended done" });
      await page.keyboard.press("Escape");
      const spans = await restingSpans(page, until, 1, (s) => s.some((x) => x.text.trim() === "resonance"), 15000);
      assert(spans.map((s) => s.text).join("").includes("Lounis and Cohen measured the resonance twice."),
        `the text layer reads the recognized words: ${JSON.stringify(spans.map((s) => s.text))}`);
      assertNoProblems(page);
      return said;
    });

    await step("scan layer: automatically: Settings → Server → Text recognition shows its rows, and the automatic switch saves", async () => {
      const options = async () => (await owner.api("/api/admin/settings")).options;
      await page.locator('[data-guide="header.account"]').click();
      await page.getByRole("menuitem", { name: "Settings…", exact: true }).click();
      await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
      await settingsPane(page, "Server");
      const recognition = page.getByRole("checkbox", { name: "Recognize text in scanned PDFs", exact: true });
      const automatic = page.getByRole("checkbox", { name: "Recognize scans automatically", exact: true });
      await automatic.waitFor();
      assert(await recognition.isChecked() && !(await recognition.isDisabled()), "recognition on, and the admin's to turn off");
      assert(await automatic.isChecked(), "the automatic switch on");
      assertEq(await page.getByRole("spinbutton", { name: "Files recognized at once", exact: true }).inputValue(), "1", "one file at a time");
      await automatic.click();
      await until(async () => (await options()).ocr_auto === false, { what: "the automatic switch saved off" });
      assert((await owner.api("/api/server-config")).ocr?.auto === false, "the server no longer recognizes scans by itself");
      await automatic.click();
      await until(async () => (await options()).ocr_auto === true, { what: "and on again" });
      await page.keyboard.press("Escape");
      assertNoProblems(page);
    });
  } finally {
    await ctx?.close();
    await auto.stop();
  }
}
