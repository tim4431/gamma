// Handwriting (docs/dev/handwriting.md): the tool strip and its presets,
// mouse strokes becoming an ink block with an .ink upload, persistence
// across a reload, the version 2 file a pressure stroke leaves and the
// highlighter band under the ink, the eraser, the lasso, the notes card's jump + flash,
// the pen styles and lines, draw and hold (a line, and a circle and a rectangle
// closed where they began), the keys of a stroke's ink-live chunks, a Brush
// preset's held line at constant width
// (in the Markdown export's SVG too), a group's Transcribe with AI, the draft
// journal bringing back what a crashed tab never saved, and a browser with pens
// of its own asked once whether to keep them over the account's. /Ink in the
// annotated PDF is backend/tests/test_ink.py; the chat's view_ink is
// test_ai_tools_ink.py.
import { PYTHON, assertOpensInPlace, fakeAiModels, until } from "../harness.mjs";
import { waitForPdf } from "./pdf.mjs";
import { DEFAULT_TOOLS, decodeStroke, encodeStroke, newInk, strokeWidth } from "../../../src/ink/ink.js";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

async function drawLine(page, from, to) {
  await page.mouse.move(from[0], from[1]);
  await page.mouse.down();
  await page.mouse.move(to[0], to[1], { steps: 12 });
  await page.mouse.up();
}

// The strokes saved on a page, by id, over every group (a stroke joins the
// current group, so a new one is found by its id, not its block).
export async function savedInkStrokes(account, pageId) {
  const out = new Map();
  for (const b of (await account.api(`/api/blocks/${pageId}/subtree`)).block.children) {
    if (!b.properties?.ink_url?.endsWith(".ink")) continue;
    for (const st of (await account.api(b.properties.ink_url)).strokes) out.set(st.id, st);
  }
  return out;
}
// The strokes saved since `before` (savedInkStrokes'), once there are `n`.
export const newInkStrokes = (account, pageId, before, n, what) => until(async () => {
  const fresh = [...(await savedInkStrokes(account, pageId)).values()].filter((st) => !before.has(st.id));
  return fresh.length >= n ? fresh : null;
}, { what });

// The armed preset's Style picker (its options row open): a style from its menu.
export async function pickPenStyle(page, name) {
  await page.locator(".pdfInkSub .inkStyleBtn").click();
  await page.getByRole("menu", { name: "Pen style" }).getByRole("menuitemradio", { name, exact: true }).click();
  await page.locator(".pdfInkSub").getByRole("button", { name: `Style: ${name}`, exact: true }).waitFor();
}

export async function inkScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage, flags }) {
  let ctx, page, pageId, box, inkBlock;
  const account = alice;
  const inkBlockOnServer = async () => {
    const d = await account.api(`/api/blocks/${pageId}/subtree`);
    return (d.block.children || []).find((b) => b.properties?.ink_url) || null;
  };

  await step("ink: the pen strip opens and two mouse strokes make a handwriting block with an .ink upload", async () => {
    const pdf = makePdf([["A page to write on", "with some text"]]);
    const up = await account.upload("/api/uploads", pdf, "ink.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Ink paper", source_url: up.source_url } });
    pageId = created.id;
    ctx = await account.context(browser);
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    assert(await page.$(".pdfInkBar .inkToolBtn.modeActive .inkToolInk"), "a pen preset is armed when the strip opens");
    assertEq((await page.$$(".pdfInkBar .inkToolInk")).length, 7, "the default presets: four pens, three highlighters");
    const buttons = await page.locator(".pdfInkRow button").evaluateAll((els) => els.map((el) => el.getAttribute("aria-label")));
    assertEq(JSON.stringify(buttons.slice(-2)), JSON.stringify(["Undo ink", "Redo ink"]), "history controls are last");
    // Five groups split by rules: presets | eraser, lasso, text | hand | More, close | undo, redo.
    const groups = await page.locator(".pdfInkRow").evaluate((row) => [...row.children].map((el) =>
      el.classList.contains("pdfInkSep") ? "|" : el.classList.contains("pdfInkHistory") ? "history"
        : el.hasAttribute("data-preset") ? "preset" : (el.getAttribute("aria-label") || "").split(" ")[0]).join(" "));
    assertEq(groups, "preset preset preset preset preset preset preset | Eraser Lasso Text | Hand | More Close | history", "the row's five groups");
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 150], [box.x + 250, box.y + 170]);
    await drawLine(page, [box.x + 100, box.y + 250], [box.x + 250, box.y + 280]);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "two stroke paths" });
    await page.waitForSelector(".blockRow .inkMarker", { timeout: 5000 });
    await page.waitForSelector(".blockInkCard", { timeout: 5000 });
    inkBlock = await until(async () => {
      const b = await inkBlockOnServer();
      return b && b.properties.ink_url.endsWith(".ink") && b.properties.ink_strokes === 2 ? b : null;
    }, { what: "ink block with two uploaded strokes" });
    assert(!("pdf_page" in inkBlock.properties), "the page is the position's alone");
    const pos = inkBlock.properties.pdf_position;
    assertEq(JSON.stringify([pos.pageNumber, pos.width, pos.height]), JSON.stringify([1, 612, 792]), "pdf_position page and size");
    assertEq(JSON.stringify(Object.keys(pos.boundingRect)), JSON.stringify(["x1", "y1", "x2", "y2"]), "no size per rect");
    const ink = await account.api(inkBlock.properties.ink_url);
    assertEq(ink.format, "gamma-ink", "file format");
    assertEq(ink.strokes.length, 2, "strokes in the file");
    assert(ink.strokes.every((s) => s.pen === false && s.ch === "xyt"), "mouse strokes carry no pressure channel");
    await page.getByRole("button", { name: "Undo ink", exact: true }).click();
    await page.getByRole("button", { name: "Redo ink", exact: true }).click();
    await page.waitForSelector(".statusPill");
    const toolbar = await page.locator(".pdfInkBar").boundingBox();
    const message = await page.locator(".statusPill").boundingBox();
    assert(message.y >= toolbar.y + toolbar.height, "undo/redo feedback stays below the toolbar");
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-drawn.png` });
    assertNoProblems(page);
  });

  await step("ink: strokes survive a reload; the eraser removes one and the file follows", async () => {
    await page.reload();
    await waitForPdf(page, 1);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "two paths after reload", timeout: 15000 });
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    await page.keyboard.press("e");
    await page.waitForSelector(".pdfInkBar button[title^='Eraser'].modeActive", { timeout: 5000 });
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 175, box.y + 140], [box.x + 178, box.y + 185]);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 1, { what: "one path left" });
    await until(async () => (await inkBlockOnServer())?.properties.ink_strokes === 1, { what: "one stroke on the server" });
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".pdfInkBar")), { what: "strip closed by Escape" });
    assertNoProblems(page);
  });

  await step("ink: Ctrl+Z undoes the erasure and a stroke; Ctrl+Shift+Z redoes", async () => {
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    // The history is per visit: after the reload only the erasure is on it.
    await page.keyboard.press("Control+z");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "erased stroke back" });
    await page.keyboard.press("p");
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 400], [box.x + 250, box.y + 420]);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 3, { what: "a third stroke" });
    await page.keyboard.press("Control+z");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "third stroke undone" });
    await page.keyboard.press("Control+Shift+z");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 3, { what: "redone" });
    await page.keyboard.press("Control+z");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "undone again" });
    await until(async () => (await inkBlockOnServer())?.properties.ink_strokes === 2, { what: "two strokes on the server" });
    assert((await page.$$(".blockRow")).length >= 1, "the block is still there");
    assertNoProblems(page);
  });

  await step("ink: the partial eraser cuts a stroke in two; the lasso selects and moves them", async () => {
    await page.keyboard.press("e");
    await page.click(".pdfInkBar button[title^='Eraser']");          // the armed tool again: its options row
    await page.waitForSelector(".pdfInkSub[data-ink-options='eraser']");
    await page.click(".pdfInkBar button[aria-label='Partial']");
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-eraser.png` });
    box = await page.locator('[data-page="1"]').boundingBox();
    // straight down through the middle of the first stroke (y ≈ 160 at x = 175)
    await drawLine(page, [box.x + 175, box.y + 140], [box.x + 175, box.y + 185]);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 3, { what: "three pieces" });
    await until(async () => (await inkBlockOnServer())?.properties.ink_strokes === 3, { what: "three strokes on the server" });
    // lasso around the whole second stroke (100..250 × 250..280)
    await page.keyboard.press("l");
    await page.mouse.move(box.x + 80, box.y + 235);
    await page.mouse.down();
    for (const [x, y] of [[270, 235], [270, 300], [80, 300], [80, 235]]) await page.mouse.move(box.x + x, box.y + y, { steps: 4 });
    await page.mouse.up();
    await page.waitForSelector('[data-page="1"] .inkSelRect', { timeout: 5000 });
    const before = (await inkBlockOnServer()).properties.pdf_position.boundingRect;
    await drawLine(page, [box.x + 170, box.y + 265], [box.x + 170, box.y + 365]);   // drag the box 100 px down
    await until(async () => {
      const b = await inkBlockOnServer();
      return b && b.properties.pdf_position.boundingRect.y2 > before.y2 + 50 ? b : null;
    }, { what: "the group's box moved down" });
    await page.keyboard.press("Delete");
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 2, { what: "selection deleted" });
    assertNoProblems(page);
  });

  await step("ink: the partial eraser swept in three long jumps cuts every stroke it crosses at its edge, in two groups, one Ctrl+Z restores them all and Ctrl+Shift+Z brings the pass back", async () => {
    // A page of its own with two seeded groups, in a context of its own (the
    // eraser's mode is this browser's): pens, a held straight line of two
    // samples, a fast mouse stroke whose samples lie 40 pt apart and a
    // highlighter, all running down the page, and one stroke the pass never
    // comes near.
    const up = await account.upload("/api/uploads", makePdf([["A page to sweep"]]), "sweep.pdf", "application/pdf");
    const doc = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Fast eraser", source_url: up.source_url } });
    const down = (from, to, gap) => Array.from({ length: Math.round((to - from) / gap) + 1 }, (_, i) => from + i * gap);
    const t0 = 1757760000000;
    const groups = {
      a: [
        encodeStroke({ id: "held", ch: "xyt", pen: false, size: 2, t0, samples: [{ x: 300, y: 80, t: 0 }, { x: 300, y: 420, t: 600 }],
          shape: { kind: "line", params: { x1: 300, y1: 80, x2: 300, y2: 420 } } }),
        encodeStroke({ id: "sparse", ch: "xyt", pen: false, size: 2, t0, samples: down(80, 420, 40).map((y, i) => ({ x: 200 + 6 * Math.sin(i), y, t: 8 * i })) }),
        encodeStroke({ id: "pressed", ch: "xyptw", size: 1.4, t0, samples: down(80, 420, 2).map((y, i) => {
          const p = 0.5 + 0.3 * Math.sin(i / 9);
          return { x: 430, y, p, t: 4 * i, w: Math.round((p + 0.5) * 1000) / 1000 };
        }) }),
      ],
      b: [
        encodeStroke({ id: "marker", tool: "highlighter", color: "#fde047", ch: "xy", pen: false, size: 10, opacity: 0.6,
          samples: down(80, 420, 20).map((y) => ({ x: 370, y })) }),
        encodeStroke({ id: "plain", ch: "xyt", pen: false, size: 2, t0, samples: down(80, 420, 5).map((y, i) => ({ x: 240, y, t: 4 * i })) }),
        encodeStroke({ id: "aside", ch: "xyt", pen: false, size: 2, t0, samples: [{ x: 100, y: 600, t: 0 }, { x: 200, y: 600, t: 100 }] }),
      ],
    };
    const ids = {};
    for (const [name, strokes] of Object.entries(groups)) {
      const file = await account.api("/api/upload-ink", { method: "POST", body: { ...newInk(1, 612, 792), strokes } });
      ids[name] = (await account.api("/api/blocks", { method: "POST", body: { parent_id: doc.id, content: `Group ${name}`,
        properties: { ink_url: file.url, pdf_position: file.pdf_position, ink_strokes: strokes.length } } })).id;
    }
    const stored = async () => {
      const d = await account.api(`/api/blocks/${doc.id}/subtree`);
      const out = {};
      for (const [name, id] of Object.entries(ids)) {
        const b = d.block.children.find((c) => c.id === id);
        out[name] = b?.properties?.ink_url ? (await account.api(b.properties.ink_url)).strokes : [];
      }
      return out;
    };
    const crossed = ["held", "sparse", "pressed", "marker", "plain"];
    const sweepCtx = await account.context(browser);
    try {
      const tab = await openPage(sweepCtx, `${server.base}/?page=${doc.id}&ws=${account.ws}`);
      await waitForPdf(tab, 1);
      await until(async () => (await tab.locator('[data-page="1"] .inkLayer path').count()) === 6, { what: "the seeded strokes drawn" });
      await tab.click("button[aria-label='Handwriting tools']");
      await tab.waitForSelector(".pdfInkBar");
      await tab.keyboard.press("e");
      await tab.click(".pdfInkBar button[title^='Eraser']");          // the armed tool again: its options row
      await tab.waitForSelector(".pdfInkSub[data-ink-options='eraser']");
      await tab.click(".pdfInkSub button[aria-label='Partial']");
      // A zigzag of three jumps across the page, 470 pt or more each, one
      // pointer move per jump and a frame between them: no position the
      // pointer reports lies near a stroke, and each jump is a flush of its own.
      const rect = await tab.locator('[data-page="1"]').evaluate((el) => {
        const r = el.getBoundingClientRect();
        return { left: r.left, top: r.top, k: r.width / 612 };
      });
      const corners = [[60, 130], [550, 200], [80, 290], [560, 380]].map(([x, y]) => [Math.round(rect.left + x * rect.k), Math.round(rect.top + y * rect.k)]);
      const sweep = corners.map(([x, y]) => ({ x: (x - rect.left) / rect.k, y: (y - rect.top) / rect.k }));   // as the layer reads them
      const radius = 9 / rect.k;                                         // the middle eraser, 9 css px
      assert(corners.slice(1).every(([x, y], i) => Math.hypot(x - corners[i][0], y - corners[i][1]) > 200), "jumps of over 200 px");
      const frame = () => tab.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
      await tab.mouse.move(...corners[0]);
      await tab.mouse.down();
      for (const corner of corners.slice(1)) { await frame(); await tab.mouse.move(...corner); }
      await tab.mouse.up();
      const cut = await until(async () => {
        const s = await stored();
        const all = [...s.a, ...s.b];
        return crossed.every((id) => !all.some((st) => st.id === id)) ? s : null;
      }, { what: "every stroke the pass crossed cut in the stored files" });
      const all = [...cut.a, ...cut.b];
      for (const id of crossed) assertEq(all.filter((st) => st.from === id).length, 4, `${id}, crossed three times, is in four pieces`);
      assert(cut.b.some((st) => st.id === "aside"), "the stroke the pass never came near stands");
      // No stored sample lies inside the swept band: each is at least the
      // radius plus its own half-width from the path. The pieces' new ends
      // lie at that edge.
      const toPath = (q) => Math.min(...sweep.slice(1).map((b, i) => {
        const a = sweep[i], dx = b.x - a.x, dy = b.y - a.y;
        const t = Math.max(0, Math.min(1, ((q.x - a.x) * dx + (q.y - a.y) * dy) / (dx * dx + dy * dy)));
        return Math.hypot(a.x + t * dx - q.x, a.y + t * dy - q.y);
      }));
      for (const st of all) for (const q of decodeStroke(st)) {
        const need = radius + strokeWidth(st, q) / 2, d = toPath(q);
        assert(d >= need - 0.05, `a sample of ${st.from || st.id} at (${q.x}, ${q.y}) lies ${(need - d).toFixed(2)} pt inside the swept band`);
      }
      for (const st of all.filter((s) => s.from === "held")) {
        const q = decodeStroke(st);
        for (const end of [q[0], q[q.length - 1]].filter((e) => e.y !== 80 && e.y !== 420)) {
          assert(Math.abs(toPath(end) - (radius + 1)) < 0.05, `the held line cut at the eraser's edge (${toPath(end).toFixed(3)} vs ${(radius + 1).toFixed(3)} pt)`);
        }
      }
      // The pass, three flushes over two groups, is one undo entry: one
      // Ctrl+Z brings every stroke back as it was, and there is nothing
      // before it.
      await tab.keyboard.press("Control+z");
      await tab.getByText("Undone: partial ink erasure (page 1).", { exact: true }).waitFor();
      const back = await until(async () => {
        const s = await stored();
        const names = (list) => list.map((st) => st.id).sort().join();
        return names(s.a) === "held,pressed,sparse" && names(s.b) === "aside,marker,plain" ? s : null;
      }, { what: "one Ctrl+Z restored both groups" });
      for (const [name, strokes] of Object.entries(groups)) {
        for (const st of strokes) {
          assertEq(JSON.stringify(back[name].find((x) => x.id === st.id).pts), JSON.stringify(st.pts), `${st.id} back as it was`);
        }
      }
      // Ctrl+Shift+Z brings the whole pass back, every piece of both groups
      // as it was cut (the entry's `after` per group is the last flush's).
      await tab.keyboard.press("Control+Shift+z");
      await tab.getByText("Redone: partial ink erasure (page 1).", { exact: true }).waitFor();
      const ids = (list) => list.map((st) => st.id).sort().join();
      const redone = await until(async () => {
        const s = await stored();
        return ids(s.a) === ids(cut.a) && ids(s.b) === ids(cut.b) ? s : null;
      }, { what: "Ctrl+Shift+Z brought the pass back in both groups" });
      for (const name of Object.keys(groups)) {
        for (const st of cut[name]) {
          assertEq(JSON.stringify(redone[name].find((x) => x.id === st.id).pts), JSON.stringify(st.pts), `${st.from || st.id} as it was cut`);
        }
      }
      await tab.keyboard.press("Control+z");
      await until(async () => {
        const s = await stored();
        return ids(s.a) === "held,pressed,sparse" && ids(s.b) === "aside,marker,plain";
      }, { what: "Ctrl+Z after the redo restored both groups again" });
      await tab.keyboard.press("Control+z");
      await tab.getByText("Nothing to undo in handwriting.", { exact: true }).waitFor();
      assertNoProblems(tab);
    } finally {
      await sweepCtx.close();
    }
  });

  await step("ink: a preset's options row sets its colour and width, duplicates and removes it; presets follow the account; the box lasso", async () => {
    try {
      await page.keyboard.press("1");
      await page.click(".pdfInkBar .inkToolBtn.modeActive");             // tap the armed preset again
      await page.waitForSelector(".pdfInkSub[data-ink-options='tool']");
      assertEq(JSON.stringify(await page.locator(".pdfInkSub .inkOptCaption").allTextContents()), JSON.stringify(["Style", "Color", "Width"]),
        "three captioned sub-rows");
      await page.click(".pdfInkSub button[aria-label='Red']");
      await page.click(".pdfInkSub button[aria-label='Width 4 pt']");
      assertEq(await page.locator(".pdfInkSub button[aria-label='Width 4 pt']").getAttribute("title"), "Width 4 pt · [ ]", "a width dot names the bracket keys");
      // the Style picker: its menu opens on the press, the five styles in order, the current one checked
      const picker = page.locator(".pdfInkSub .inkStyleBtn");
      assertEq(JSON.stringify([await picker.getAttribute("aria-label"), await picker.getAttribute("aria-expanded")]), JSON.stringify(["Style: Pen", "false"]));
      await picker.click();
      const styles = page.getByRole("menu", { name: "Pen style" });
      assertEq(JSON.stringify(await styles.getByRole("menuitemradio").allTextContents()), JSON.stringify(["Pen", "Ballpoint", "Brush", "Pencil", "Calligraphy"]),
        "Pen · Ballpoint · Brush · Pencil · Calligraphy");
      assertEq(await styles.getByRole("menuitemradio", { name: "Pen", exact: true }).getAttribute("aria-checked"), "true");
      assertEq(await styles.locator(".inkBrushPreview").count(), 5, "each row led by a stroke in its look");
      await styles.getByRole("menuitemradio", { name: "Ballpoint", exact: true }).click();
      await page.locator(".pdfInkSub").getByRole("button", { name: "Style: Ballpoint", exact: true }).waitFor();
      assertEq(await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label"),
        "Red ballpoint pen · 4 pt · key 1 — tap again for options", "a preset is named in words, not a hex code");
      await page.click(".pdfInkSub button[aria-label='Duplicate tool']");
      await until(async () => (await page.$$(".pdfInkBar .inkToolInk")).length === 8, { what: "a duplicated preset" });
      assert(await page.$(".pdfInkSub[data-ink-options='tool']"), "the copy stays open for editing");
      if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-presets.png` });
      box = await page.locator('[data-page="1"]').boundingBox();
      await drawLine(page, [box.x + 100, box.y + 400], [box.x + 250, box.y + 420]);
      // The reload in an earlier step ended the group: this stroke is a new
      // block. Its file carries the copy's look.
      const red = await until(async () => {
        const d = await account.api(`/api/blocks/${pageId}/subtree`);
        for (const b of d.block.children || []) {
          if (!b.properties?.ink_url?.endsWith(".ink")) continue;
          const ink = await account.api(b.properties.ink_url);
          const hit = ink.strokes.find((st) => st.color === "#dc2626");
          if (hit) return hit;
        }
        return null;
      }, { what: "a stroke in the copy's colour on the server" });
      assertEq(red.size, 4, "the copy drew with its width");
      assertEq(red.brush, "monoline", "the duplicated Ballpoint preset saved the monoline brush");
      assertEq(red.tool, "pen", "…as a pen");
      await page.click(".pdfInkSub button[aria-label='Remove tool']");
      await until(async () => (await page.$$(".pdfInkBar .inkToolInk")).length === 7, { what: "the copy removed" });
      assert(await page.$(".pdfInkBar .inkToolBtn.modeActive"), "a neighbour is armed after the removal");
      // The presets are the account's: the edited list reaches the profile,
      // and a browser with no local copy takes it from there on load.
      const profileTools = async () => (await account.api("/api/prefs/profile")).value?.inkTools || [];
      await until(async () => {
        const tools = await profileTools();
        return tools.length === 7 && tools[0].color === "#dc2626" && tools[0].size === 4 && tools[0].brush === "monoline";
      }, { what: "the edited presets in the account's profile" });
      await page.evaluate(() => localStorage.removeItem("gamma-ink-tools"));
      await page.reload();
      await waitForPdf(page, 1);
      await page.click("button[aria-label='Handwriting tools']");
      await page.waitForSelector(".pdfInkBar");
      await until(async () => (await page.$eval(".pdfInkBar .inkToolInk", (el) => getComputedStyle(el).backgroundColor)) === "rgb(220, 38, 38)",
        { what: "the first preset's colour, back from the account" });
      await page.keyboard.press("1");
      await page.click(".pdfInkBar .inkToolBtn.modeActive");
      assertEq(await page.locator(".pdfInkSub .inkStyleBtn").getAttribute("aria-label"), "Style: Ballpoint",
        "the first preset kept its brush across reload");
      await page.locator(".pdfInkSub .inkStyleBtn").click();
      await page.getByRole("menu", { name: "Pen style" }).getByRole("menuitemradio", { name: "Pen", exact: true }).click();
      await page.locator(".pdfInkSub").getByRole("button", { name: "Style: Pen", exact: true }).waitFor();
      // The lasso's box mode: a dragged rectangle selects what it covers.
      await page.keyboard.press("l");
      await page.click(".pdfInkBar button[title^='Lasso']");
      await page.waitForSelector(".pdfInkSub[data-ink-options='select']");
      await page.click(".pdfInkSub button[aria-label='Box']");
      box = await page.locator('[data-page="1"]').boundingBox();
      await drawLine(page, [box.x + 80, box.y + 380], [box.x + 270, box.y + 440]);
      await page.waitForSelector('[data-page="1"] .inkSelRect', { timeout: 5000 });
      assertEq(await page.locator(".pdfInkSub").count(), 0, "a selection closes the options row");
      await page.keyboard.press("Escape");                               // drops the selection
      await until(async () => !(await page.$('[data-page="1"] .inkSelRect')), { what: "selection dropped" });
      assert(await page.$(".pdfInkBar"), "the strip is still open");
      await page.keyboard.press("Escape");                               // closes the strip
      await until(async () => !(await page.$(".pdfInkBar")), { what: "strip closed" });
    } finally {
      // The presets back to the defaults — on the account, which every group
      // on this worker shares, and in this browser — for the steps that follow.
      // The browser's last change (Pen) pushes after a second: let it land
      // first, or it would put the edited list back over the reset.
      await until(async () => {
        const first = ((await account.api("/api/prefs/profile")).value?.inkTools || [])[0];
        return first && first.color === "#dc2626" && !first.brush;
      }, { what: "the last preset change pushed" }).catch(() => {});
      await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: null } } }).catch(() => {});
      await page.evaluate(() => localStorage.removeItem("gamma-ink-tools")).catch(() => {});
      await page.reload().catch(() => {});
      await waitForPdf(page, 1).catch(() => {});
    }
    assertNoProblems(page);
  });

  await step("ink: More (⋯) opens its menu on the press, Escape closes the menu alone, and Handwriting settings… lands on the section", async () => {
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    const more = page.locator(".pdfInkBar button[aria-label='More handwriting tools']");
    assertEq(await more.getAttribute("aria-haspopup"), "menu");
    assertEq(await more.getAttribute("data-guide"), "ink.more");
    await assertOpensInPlace(page, ".inkStripMenu", () => more.click(), { what: "the More menu" });
    assertEq(await more.getAttribute("aria-expanded"), "true");
    const menu = page.locator(".inkStripMenu");
    const paste = menu.locator("[data-command='ink.paste']");
    assert(await paste.isDisabled(), "nothing on the handwriting clipboard: Paste is greyed");
    assertEq(await paste.getAttribute("title"), "Copy or cut handwriting first");
    assertEq(await paste.textContent(), "Paste handwritingCtrl+V", "the command's label and its chord");
    assert(!(await menu.locator("[data-command='ink.selectAll']").isDisabled()), "Select all applies with a tool armed");
    assertEq(await menu.getByRole("button", { name: "Handwriting settings…", exact: true }).count(), 1);
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".inkStripMenu")), { what: "the menu closed by Escape" });
    assertEq(await page.locator(".pdfInkBar").count(), 1, "the strip stays open: Escape went to the menu");
    assertEq(await more.getAttribute("aria-expanded"), "false");
    // From the keyboard: Enter on the focused More (a click with no press
    // behind it) opens the menu; Escape closes it, the focus still on More.
    await more.focus();
    await page.keyboard.press("Enter");
    await menu.locator("[data-command='ink.paste']").waitFor();
    assertEq(await more.getAttribute("aria-expanded"), "true", "opened from the keyboard");
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".inkStripMenu")), { what: "the keyboard's menu closed by Escape" });
    assertEq(await page.locator(".pdfInkBar").count(), 1, "the strip stays open");
    assert(await more.evaluate((el) => el === document.activeElement), "the focus stays on More");
    await more.click();
    await menu.getByRole("button", { name: "Handwriting settings…", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
    await dialog.waitFor();
    await until(() => page.locator(".settingsPane [data-setting='Reset presets']").first().isVisible(), { what: "the Handwriting section in view" });
    await page.getByRole("button", { name: "Close settings", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    assertNoProblems(page);
  });

  await step("ink: Move right, Hide and Hidden tools in the Tool menu, and a mouse drag, reorder the presets; the number keys follow", async () => {
    const armed = () => page.locator(".pdfInkBar .inkToolBtn.modeActive");
    const name = async () => (await armed().getAttribute("aria-label")).split(" · ")[0];
    const presets = () => page.locator(".pdfInkRow [data-preset]");
    const presetName = async (i) => (await presets().nth(i).getAttribute("aria-label")).split(" · ")[0];
    const pillText = async () => (await page.locator(".statusPill .pillText").textContent()) || "";
    const toolMenu = async () => {
      await page.locator(".pdfInkSub button[aria-label='Tool menu']").click();
      await page.waitForSelector(".inkStripMenu");
      return page.locator(".inkStripMenu");
    };
    const ids = async () => page.evaluate(() => JSON.parse(localStorage.getItem("gamma-ink-tools")).map((t) => t.id).join());
    const order = await ids();
    await page.keyboard.press("1");
    const first = await name();                                          // the first default pen, by its colour
    await armed().click();                                               // its options row
    await page.waitForSelector(".pdfInkSub[data-ink-options='tool']");
    let menu = await toolMenu();
    assert(await menu.getByRole("button", { name: "Move left", exact: true }).isDisabled(), "nothing to the left of key 1");
    assert(await menu.getByRole("button", { name: "Hidden tools", exact: true }).isDisabled(), "no hidden tools yet");
    // A press on More while the Tool menu stands open swaps the two: one
    // menu, More's, and the Tool button no longer expanded.
    const more = page.locator(".pdfInkBar button[aria-label='More handwriting tools']");
    await more.click();
    await until(async () => (await page.locator(".inkStripMenu [data-command='ink.paste']").count()) === 1, { what: "More's menu in the Tool menu's place" });
    assertEq(await page.locator(".inkStripMenu").count(), 1, "one strip menu at a time");
    assertEq(await page.locator(".pdfInkSub button[aria-label='Tool menu']").getAttribute("aria-expanded"), "false", "the Tool button no longer expanded");
    assertEq(await more.getAttribute("aria-expanded"), "true", "More expanded");
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".inkStripMenu")), { what: "More's menu closed by Escape" });
    menu = await toolMenu();
    await menu.getByRole("button", { name: "Move right", exact: true }).click();
    await until(async () => (await pillText()).includes(`${first} is now key 2.`), { what: "the move's pill" });
    assertEq(await presetName(1), first, "key 2 is the moved preset");
    assert((await armed().getAttribute("aria-label")).includes("key 2"), "still armed, at key 2");
    await page.keyboard.press("1");
    assert((await name()) !== first, "key 1 arms the preset that moved up");
    await page.keyboard.press("2");
    assertEq(await name(), first, "key 2 arms the moved one");
    await armed().click();
    menu = await toolMenu();
    await menu.getByRole("button", { name: "Move left", exact: true }).click();
    await until(async () => (await pillText()).includes(`${first} is now key 1.`), { what: "moved back" });
    assertEq(await ids(), order, "the ids never change");
    // Hide: off the row and out of the keys, kept in the list; the pill offers Show; Hidden tools brings it back
    menu = await toolMenu();
    await menu.getByRole("button", { name: "Hide", exact: true }).click();
    await until(async () => (await presets().count()) === 6, { what: "six presets on the row" });
    assert((await pillText()).includes(`${first} hidden.`), "the pill names it");
    assertEq(await page.locator(".statusPill").getByRole("button", { name: "Show", exact: true }).count(), 1, "Show is on the pill");
    assert((await presetName(0)) !== first, "key 1 is the next preset");
    assert((await armed().getAttribute("aria-label")).includes("key 1"), "its neighbour is armed");
    assertEq(await page.evaluate(() => JSON.parse(localStorage.getItem("gamma-ink-tools")).filter((t) => t.hidden === true).length), 1, "kept in the list, hidden");
    await armed().click();
    menu = await toolMenu();
    await menu.getByRole("button", { name: "Hidden tools", exact: true }).click();
    await menu.getByRole("menu", { name: "Hidden tools" }).getByRole("button", { name: new RegExp(`^${first} · `) }).click();
    await until(async () => (await presets().count()) === 7, { what: "seven presets again" });
    assertEq(await presetName(0), first, "back where it was, at key 1");
    assertEq(await ids(), order);
    // A mouse drag: past 6 px the preset rides along as a ghost, a bar marks the slot, the drop writes the order
    const from = await presets().nth(0).boundingBox(), third = await presets().nth(2).boundingBox();
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(from.x + from.width / 2 + 12, from.y + from.height / 2, { steps: 3 });
    await page.waitForSelector(".inkDragGhost");
    assert(await page.$(".pdfInkRow .inkDropBar"), "the insertion bar shows");
    assert((await pillText()).includes("Drop to reorder."), "the pill says what the drag does");
    await page.mouse.move(third.x + third.width - 2, third.y + third.height / 2, { steps: 6 });   // past the third preset's centre: after it
    await page.mouse.up();
    await until(async () => (await pillText()).includes(`${first} is now key 3.`), { what: "the drop's pill" });
    await until(async () => !(await page.$(".inkDragGhost")), { what: "the ghost gone" });
    assertEq(await presetName(2), first, "the dragged preset at key 3");
    assert(await page.$(".pdfInkSub[data-ink-options='tool']"), "the drop was no tap: the options row is as it was");
    await page.keyboard.press("3");
    assertEq(await name(), first, "key 3 arms it");
    const back = await presets().nth(2).boundingBox(), front = await presets().nth(0).boundingBox();
    await page.mouse.move(back.x + back.width / 2, back.y + back.height / 2);
    await page.mouse.down();
    await page.mouse.move(front.x + 2, front.y + front.height / 2, { steps: 8 });
    await page.mouse.up();
    await until(async () => (await pillText()).includes(`${first} is now key 1.`), { what: "dragged back to the front" });
    assertEq(await ids(), order, "the original order, the same ids");
    assertNoProblems(page);
  });

  await step("ink: opening the strip arms the stylus pen: with the last pen hidden and a highlighter first in the row, the first pen, not the highlighter", async () => {
    const armed = () => page.locator(".pdfInkBar .inkToolBtn.modeActive");
    const name = async () => (await armed().getAttribute("aria-label")).split(" · ")[0];
    const presets = () => page.locator(".pdfInkRow [data-preset]");
    const presetName = async (i) => (await presets().nth(i).getAttribute("aria-label")).split(" · ")[0];
    const pillText = async () => (await page.locator(".statusPill .pillText").textContent()) || "";
    const ids = async () => page.evaluate(() => JSON.parse(localStorage.getItem("gamma-ink-tools")).map((t) => t.id).join());
    const optionsRow = async () => {
      await armed().click();
      await page.waitForSelector(".pdfInkSub[data-ink-options='tool']");
    };
    const toolMenu = async () => {
      await page.locator(".pdfInkSub button[aria-label='Tool menu']").click();
      await page.waitForSelector(".inkStripMenu");
      return page.locator(".inkStripMenu");
    };
    // Move the armed preset one key at a time, `n` keys left (< 0) or right.
    const shift = async (who, n) => {
      for (let i = 0; i < Math.abs(n); i++) {
        const key = Number((await armed().getAttribute("aria-label")).match(/key (\d)/)[1]) + Math.sign(n);
        await (await toolMenu()).getByRole("button", { name: n < 0 ? "Move left" : "Move right", exact: true }).click();
        await until(async () => (await pillText()).includes(`${who} is now key ${key}.`), { what: `${who} at key ${key}` });
      }
    };
    const order = await ids();
    // the default row: four pens, then three highlighters; the first highlighter goes to the front
    await page.keyboard.press("5");
    const highlighter = await name();
    let lastPen = null;
    try {
      await optionsRow();
      await shift(highlighter, -4);
      assertEq(await presetName(0), highlighter, "a highlighter at key 1");
      const firstPen = await presetName(1);
      // the last pen armed (the stylus pen), then hidden: its neighbour, a highlighter, is armed in its place
      await page.keyboard.press("5");
      lastPen = await name();
      await optionsRow();
      await (await toolMenu()).getByRole("button", { name: "Hide", exact: true }).click();
      await until(async () => (await presets().count()) === 6, { what: "the last pen hidden" });
      assert((await name()) !== lastPen, "its neighbour is armed");
      // closed and opened again: the strip arms the first pen, as the stylus writes with it
      await page.click("button[aria-label='Handwriting tools']");
      await until(async () => !(await page.locator(".pdfInkBar").count()), { what: "the strip closed" });
      await page.click("button[aria-label='Handwriting tools']");
      await page.waitForSelector(".pdfInkBar");
      assertEq(await name(), firstPen, "the strip opens on the first pen, not the highlighter before it");
    } finally {
      // back as it was, after a failure too: the hidden pen shown, the highlighter at key 5 again
      if (!(await page.locator(".pdfInkBar").count())) {
        await page.click("button[aria-label='Handwriting tools']").catch(() => {});
        await page.waitForSelector(".pdfInkBar").catch(() => {});
      }
      if (lastPen && (await presets().count()) < 7) {
        await page.keyboard.press("1");
        await optionsRow().catch(() => {});
        const menu = await toolMenu().catch(() => null);
        await menu?.getByRole("button", { name: "Hidden tools", exact: true }).click().catch(() => {});
        await menu?.getByRole("menu", { name: "Hidden tools" }).getByRole("button", { name: new RegExp(`^${lastPen} · `) }).click().catch(() => {});
        await until(async () => (await presets().count()) === 7, { what: "seven presets again" }).catch(() => {});
      }
      const at = (await presets().evaluateAll((els) => els.map((el) => el.getAttribute("aria-label").split(" · ")[0]))).indexOf(highlighter);
      if (at >= 0 && at !== 4) {
        await page.keyboard.press(String(at + 1));
        await optionsRow().catch(() => {});
        await shift(highlighter, 4 - at).catch(() => {});
      }
    }
    assertEq(await ids(), order, "the original order, the same ids");
    assertEq(await page.evaluate(() => JSON.parse(localStorage.getItem("gamma-ink-tools")).filter((t) => t.hidden).length), 0, "nothing hidden");
    assertNoProblems(page);
  });

  await step("ink: [ and ] step the armed preset's width with a pill; E held across an erasing pass returns to the tool before it, a lasso selection with it", async () => {
    const armed = () => page.locator(".pdfInkBar .inkToolBtn.modeActive");
    const pillText = async () => (await page.locator(".statusPill .pillText").textContent()) || "";
    await page.keyboard.press("1");
    const [first, width] = (await armed().getAttribute("aria-label")).split(" · ");
    assertEq(width, "2 pt", "the first default pen");
    await armed().click();
    await page.waitForSelector(".pdfInkSub[data-ink-options='tool']");
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".pdfInkSub")), { what: "Escape closed the options row" });
    assert(await page.$(".pdfInkBar"), "…and left the strip open");
    await page.keyboard.press("BracketRight");
    await until(async () => (await pillText()).includes(`${first} · 2.8 pt.`), { what: "the width pill" });
    assert((await armed().getAttribute("aria-label")).includes(" · 2.8 pt · "), "the preset is thicker, options row closed or not");
    await page.keyboard.press("BracketLeft");
    await until(async () => (await pillText()).includes(`${first} · 2 pt.`), { what: "thinner again" });
    // A tap on E arms the eraser and it stays; E held while a pass is made goes back to the tool before it.
    await page.keyboard.press("e");
    assert((await armed().getAttribute("aria-label")).startsWith("Eraser"), "a tap arms the eraser");
    await page.keyboard.press("1");
    box = await page.locator('[data-page="1"]').boundingBox();
    await page.keyboard.down("e");
    await until(async () => (await armed().getAttribute("aria-label")).startsWith("Eraser"), { what: "the eraser armed on the keydown" });
    await sleep(350);
    await drawLine(page, [box.x + 420, box.y + 700], [box.x + 440, box.y + 720]);   // a pass over nothing still counts
    await page.keyboard.up("e");
    await until(async () => (await armed().getAttribute("aria-label")).startsWith(first), { what: "back to the pen after the hold" });
    // With the lasso: its selection comes back with it.
    await page.keyboard.press("l");
    await drawLine(page, [box.x + 80, box.y + 380], [box.x + 270, box.y + 440]);   // the box lasso, from the step before
    await page.waitForSelector('[data-page="1"] .inkSelRect', { timeout: 5000 });
    await page.keyboard.down("e");
    await until(async () => !(await page.$('[data-page="1"] .inkSelRect')), { what: "the eraser drops the selection" });
    await sleep(350);
    await drawLine(page, [box.x + 420, box.y + 700], [box.x + 440, box.y + 720]);
    await page.keyboard.up("e");
    await page.waitForSelector('[data-page="1"] .inkSelRect', { timeout: 5000 });
    assert((await armed().getAttribute("aria-label")).startsWith("Lasso"), "the lasso is armed again, its selection restored");
    await page.keyboard.press("Escape");                               // drops the selection
    await until(async () => !(await page.$('[data-page="1"] .inkSelRect')), { what: "selection dropped" });
    await page.keyboard.press("Escape");                               // closes the strip
    await until(async () => !(await page.$(".pdfInkBar")), { what: "strip closed" });
    assertNoProblems(page);
  });

  await step("ink: the notes card jumps to the group and outlines it; its replay plays on the PDF page", async () => {
    await page.click(".blockInkCard");
    await page.waitForSelector('[data-page="1"] .inkFlash', { timeout: 5000 });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-notes.png` });
    const paths = '[data-page="1"] .inkLayer path';
    const whole = (await page.$$(paths)).length;
    await page.hover(".blockInkCardWrap");
    await page.click(".blockInkCardWrap .inkReplayBtn");
    await until(async () => (await page.$$(paths)).length < whole, { what: "the page draws the group stroke by stroke", every: 10 });
    await until(async () => (await page.$$(paths)).length === whole, { what: "then whole again" });
    assertNoProblems(page);
  });

  await step("ink: erasing the last stroke stays erased while block deletion is pending", async () => {
    // Reopening the page starts a fresh handwriting group.
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("p");
    const paths = '[data-page="1"] .inkLayer path';
    const count = await page.locator(paths).count();
    const before = await account.api(`/api/blocks/${pageId}/subtree`);
    const ids = new Set(before.block.children.map((b) => b.id));
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 320], [box.x + 250, box.y + 320]);
    const group = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.find((b) => !ids.has(b.id) && b.properties?.ink_url);
    }, { what: "new group saved before erasure" });
    await until(async () => await page.locator(paths).count() === count + 1);
    let release, deleting = false;
    const held = new Promise((resolve) => { release = resolve; });
    const url = `**/api/blocks/${group.id}`;
    await page.route(url, async (route) => {
      if (route.request().method() === "DELETE") { deleting = true; await held; }
      await route.continue();
    });
    try {
      await page.keyboard.press("e");
      await page.click(".pdfInkBar button[title^='Eraser']");
      await page.click(".pdfInkSub button[aria-label='Whole strokes']");
      box = await page.locator('[data-page="1"]').boundingBox();
      await drawLine(page, [box.x + 175, box.y + 310], [box.x + 175, box.y + 330]);
      await until(async () => await page.locator(paths).count() === count);
      await until(() => deleting, { what: "delete request held in flight" });
      const stayedErased = await page.evaluate(async ({ paths, count }) => {
        const end = performance.now() + 300;
        do {
          await new Promise(requestAnimationFrame);
          if (document.querySelectorAll(paths).length !== count) return false;
        } while (performance.now() < end);
        return true;
      }, { paths, count });
      assert(stayedErased, "saved strokes must not reappear while deletion is pending");
    } finally { release(); }
    await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return !d.block.children.some((b) => b.id === group.id);
    }, { what: "empty group deleted" });
    await page.unroute(url);
    assertEq(await page.locator(paths).count(), count);
    assertNoProblems(page);
  });

  await step("ink: Pencil touch gestures are claimed and finger gestures remain available", async () => {
    // Synthetic Safari event ordering exercises the handlers; a physical
    // iPad is still needed to verify native Pencil/scroll arbitration.
    await page.keyboard.press("Escape");
    if (await page.locator(".pdfInkBar").count()) await page.keyboard.press("Escape");
    const paths = '[data-page="1"] .inkLayer path';
    const count = await page.locator(paths).count();
    const result = await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const pointer = (type, pointerType, pointerId, x) => el.dispatchEvent(new PointerEvent(type, {
        bubbles: true, cancelable: true, pointerType, pointerId, button: 0,
        buttons: type === "pointerup" ? 0 : 1, pressure: 0.7,
        clientX: box.left + x, clientY: box.top + 350,
      }));
      const touch = (type, touchType) => {
        const event = new Event(type, { bubbles: true, cancelable: true });
        const contact = { identifier: 1, touchType, clientX: box.left + 100, clientY: box.top + 350 };
        Object.defineProperties(event, {
          changedTouches: { value: [contact] }, touches: { value: [contact] },
        });
        el.dispatchEvent(event);
        return event.defaultPrevented;
      };
      const fingerBefore = touch("touchstart", "direct");
      const pencilBefore = touch("touchstart", "stylus");
      pointer("pointerdown", "pen", 71, 100);
      const pencilStart = touch("touchstart", "stylus");
      const pencilMove = touch("touchmove", "stylus");
      const untypedPencil = touch("touchmove", undefined);
      const fingerDuring = touch("touchstart", "direct");
      pointer("pointerdown", "touch", 72, 120);
      pointer("pointerup", "touch", 72, 120);
      pointer("pointermove", "pen", 71, 220);
      pointer("pointerup", "pen", 71, 220);
      const fingerAfter = touch("touchstart", "direct");
      return { fingerBefore, pencilBefore, pencilStart, pencilMove, untypedPencil, fingerDuring, fingerAfter };
    });
    assert(result.pencilBefore && result.pencilStart && result.pencilMove && result.untypedPencil,
      "Pencil touch events prevent native panning, including before pointerdown");
    assert(!result.fingerBefore && result.fingerDuring && !result.fingerAfter,
      "palms are blocked during writing; fingers navigate before and after");
    await until(async () => await page.locator(paths).count() === count + 1, { what: "pen stroke survived a second contact" });
    assertNoProblems(page);
  });

  await step("ink: coalesced pen samples, prediction preview, lift endpoint and pressure persist accurately", async () => {
    const visibleCount = await page.locator('[data-page="1"] .inkLayer path').count();
    await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.reduce((n, b) => n + (b.properties?.ink_strokes || 0), 0) === visibleCount;
    }, { what: "previous Pencil stroke persisted before starting a separate group" });
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("p");
    const before = await account.api(`/api/blocks/${pageId}/subtree`);
    const ids = new Set(before.block.children.map((b) => b.id));
    const result = await page.locator('[data-page="1"]').evaluate(async (el) => {
      const box = el.getBoundingClientRect();
      const make = (type, x, time, pressure) => {
        const ev = new PointerEvent(type, { bubbles: true, cancelable: true,
          pointerType: "pen", pointerId: 81, button: 0, buttons: type === "pointerup" ? 0 : 1,
          clientX: box.left + x, clientY: box.top + 300, pressure });
        Object.defineProperty(ev, "timeStamp", { value: 1000 + time });
        return ev;
      };
      el.dispatchEvent(make("pointerdown", 100, 0, 0.2));
      const move = make("pointermove", 130, 12, 0.8);
      Object.defineProperties(move, {
        getCoalescedEvents: { value: () => [make("pointermove", 110, 4, 0.4),
          make("pointermove", 120, 8, 0.6), make("pointermove", 130, 12, 0.8)] },
        getPredictedEvents: { value: () => [make("pointermove", 138, 20, 0.8)] },
      });
      el.dispatchEvent(move);
      await new Promise(requestAnimationFrame);
      const canvas = el.querySelector(".inkCanvas");
      const painted = canvas.style.display === "block" && canvas.getContext("2d")
        .getImageData(0, 0, canvas.width, canvas.height).data.some((v, i) => i % 4 === 3 && v > 0);
      el.dispatchEvent(make("pointerup", 140, 24, 0));
      return { painted, hidden: canvas.style.display === "none",
        k: box.width / el.querySelector(".inkLayer").viewBox.baseVal.width };
    });
    assert(result.painted && result.hidden, "live canvas paints before lift and hands off to SVG");
    const block = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.find((b) => !ids.has(b.id) && b.properties?.ink_url);
    }, { what: "sampled pen stroke saved" });
    const ink = await account.api(block.properties.ink_url);
    const st = ink.strokes[0];
    assertEq(ink.version, 2);
    assertEq(st.ch, "xyptw");
    assertEq(st.pts.length, 25, "five real samples, no prediction saved");
    assertEq(JSON.stringify(st.pts.filter((_, i) => i % 5 === 2)), "[200,400,600,800,800]", "pressure including lift");
    assertEq(JSON.stringify(st.pts.filter((_, i) => i % 5 === 3)), "[0,4,4,4,12]", "hardware time deltas");
    assertEq(JSON.stringify(st.pts.filter((_, i) => i % 5 === 4)), "[700,200,200,200,0]", "the baked width, 1 + (p - 0.5), as deltas");
    const endpoint = st.pts.filter((_, i) => i % 5 === 0).reduce((a, b) => a + b, 0) / 100;
    assert(Math.abs(endpoint - 140 / result.k) < 0.02, "the final pointer-up position is saved");
    await page.reload();
    await waitForPdf(page, 1);
    await page.waitForSelector(`[data-page="1"] [data-ink-id="${block.id}"] path`);
    assertNoProblems(page);
  });

  await step("ink: a pressure stroke is stored as version 2 with its baked width; a highlighter draws under the ink", async () => {
    // The file a pen stroke leaves (docs/dev/handwriting.md "The stroke file"):
    // version 2, the drawn width baked into the w channel on the client
    // (finishStroke.js bakeWidths: w = 1 + (p - 0.5) for the default pen),
    // one stride per sample. Then a highlighter drawn over that stroke
    // after a reload, in a group of its own, is painted before the pen's
    // group: band 0 under every group's ink (InkLayer.jsx).
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("p");
    const before = await account.api(`/api/blocks/${pageId}/subtree`);
    const ids = new Set(before.block.children.map((b) => b.id));
    await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const send = (type, x, time, pressure) => {
        const ev = new PointerEvent(type, { bubbles: true, cancelable: true,
          pointerType: "pen", pointerId: 82, button: 0, buttons: type === "pointerup" ? 0 : 1,
          clientX: box.left + x, clientY: box.top + 360, pressure });
        Object.defineProperty(ev, "timeStamp", { value: 2000 + time });
        el.dispatchEvent(ev);
      };
      send("pointerdown", 90, 0, 0.3);
      send("pointermove", 110, 8, 0.5);
      send("pointermove", 130, 16, 0.7);
      send("pointerup", 150, 24, 0);
    });
    const penBlock = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.find((b) => !ids.has(b.id) && b.properties?.ink_url);
    }, { what: "pressure stroke saved" });
    await page.reload();
    await waitForPdf(page, 1);
    await page.waitForSelector(`[data-page="1"] [data-ink-id="${penBlock.id}"] path`);
    const ink = await account.api(penBlock.properties.ink_url);
    assertEq(ink.format, "gamma-ink");
    assertEq(ink.version, 2, "the client writes version 2");
    const st = ink.strokes[0];
    assert(/^xy[a-z]*$/.test(st.ch) && st.ch.includes("w"), `the baked width rides in ch (${st.ch})`);
    assertEq(st.pts.length % st.ch.length, 0, "whole strides");
    assertEq(st.pts.length / st.ch.length, 4, "one sample per pointer event, the lift included");
    const p0 = st.pts[st.ch.indexOf("p")] / 1000, w0 = st.pts[st.ch.indexOf("w")] / 1000;
    assertEq(p0, 0.3, "the first sample's pressure");
    assert(Math.abs(w0 - (1 + (p0 - 0.5))) < 0.0005, `the first sample's width multiplier is 1 + (p - 0.5) (${w0})`);
    assertEq(st.size * w0, st.size * 0.8, "the drawn width is size × w, no formula at render time");

    // a highlighter across the pen stroke, after the reload: its own group
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("h");
    assert((await page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label")).includes("highlighter"), "a highlighter preset is armed");
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 80, box.y + 360], [box.x + 160, box.y + 360]);
    const hlBlock = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      return d.block.children.find((b) => !ids.has(b.id) && b.id !== penBlock.id && b.properties?.ink_url);
    }, { what: "highlighter stroke saved in a group of its own" });
    const hlInk = await account.api(hlBlock.properties.ink_url);
    assertEq(hlInk.strokes[0].tool, "highlighter");
    assert(!hlInk.strokes[0].ch.includes("w") && hlInk.strokes[0].band === undefined, "a highlighter carries no width channel and its band is the tool's default");
    const order = await page.locator('[data-page="1"] .inkLayer').evaluate((svg, [penId, hlId]) => {
      const groups = [...svg.querySelectorAll("g[data-ink-id]")];
      const hl = groups.findIndex((g) => g.dataset.inkId === hlId);
      const pen = groups.findIndex((g) => g.dataset.inkId === penId);
      const hlPath = svg.querySelector(`g[data-ink-id="${hlId}"] path`);
      const penPath = svg.querySelector(`g[data-ink-id="${penId}"] path`);
      return { hl, pen, multiply: getComputedStyle(hlPath).mixBlendMode,
        pathOrder: !!(hlPath.compareDocumentPosition(penPath) & Node.DOCUMENT_POSITION_FOLLOWING) };
    }, [penBlock.id, hlBlock.id]);
    assert(order.hl >= 0 && order.pen >= 0 && order.hl < order.pen, `the highlighter's group is painted before the pen's (${order.hl} < ${order.pen})`);
    assert(order.pathOrder, "the highlighter path precedes the pen path");
    assertEq(order.multiply, "multiply", "the highlighter multiplies onto the page");
    assertNoProblems(page);
  });

  await step("ink: interrupted strokes clean up and a pen can take over a palm contact", async () => {
    // Enable finger writing to exercise the harder palm-first ordering.
    await page.evaluate(() => localStorage.setItem("gamma-ink-pen-only", "0"));
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    const paths = '[data-page="1"] .inkLayer path';
    const count = await page.locator(paths).count();
    const clean = await page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      const send = (type, id, pointerType = "pen", x = 100) => el.dispatchEvent(new PointerEvent(type, {
        bubbles: true, cancelable: true, pointerType, pointerId: id, button: 0,
        buttons: type === "pointerup" ? 0 : 1, pressure: 0.6,
        clientX: box.left + x, clientY: box.top + 330,
      }));
      send("pointerdown", 90);
      send("pointermove", 90, "pen", 150);
      send("pointercancel", 90);
      const cancelled = el.querySelector(".inkCanvas").style.display === "none";
      send("pointerdown", 91);
      send("lostpointercapture", 91);
      const lost = el.querySelector(".inkCanvas").style.display === "none";
      send("pointerdown", 92, "touch");
      send("pointermove", 92, "touch", 150);
      send("pointerdown", 93);
      send("pointerup", 92, "touch", 160);
      send("pointermove", 93, "pen", 180);
      send("pointerup", 93, "pen", 190);
      return cancelled && lost;
    });
    assert(clean, "cancellation and lost capture clear the live preview");
    await until(async () => await page.locator(paths).count() === count + 1,
      { what: "only the pen stroke survives palm takeover and interruptions" });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-touch-writing.png` });
    assertNoProblems(page);
  });

  await step("ink: native Chromium touch writes without scrolling and native pen pressure is saved", async () => {
    const paths = '[data-page="1"] .inkLayer path';
    const count = await page.locator(paths).count();
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    box = await page.locator('[data-page="1"]').boundingBox();
    const x = Math.round(box.x + 100), y = Math.round(box.y + 220);
    const scrollBefore = await page.locator(".pdfViewer").evaluate((el) => el.scrollTop);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    for (let i = 1; i <= 12; i++) {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x + i * 8, y: y + i * 3 }] });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await until(async () => await page.locator(paths).count() === count + 1, { what: "native touch stroke" });
    assertEq(await page.locator(".pdfViewer").evaluate((el) => el.scrollTop), scrollBefore, "finger drawing does not pan");
    await page.getByRole("button", { name: "Hand", exact: true }).click();
    await cdp.send("Input.dispatchMouseEvent", { type: "mousePressed", x, y: y + 80,
      pointerType: "pen", button: "left", buttons: 1, clickCount: 1, force: 0.2 });
    for (let i = 1; i <= 12; i++) {
      await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: x + i * 8, y: y + 80 + Math.sin(i / 2) * 15,
        pointerType: "pen", button: "left", buttons: 1, force: 0.2 + i * 0.05 });
    }
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: x + 100, y: y + 76,
      pointerType: "pen", button: "left", buttons: 0, clickCount: 1 });
    await until(async () => await page.locator(paths).count() === count + 2, { what: "native pen stroke in Hand mode" });
    await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      for (const b of d.block.children) {
        if (!b.properties?.ink_url) continue;
        const ink = await account.api(b.properties.ink_url);
        if (ink.strokes.some((s) => s.pen && s.ch === "xyptw" && s.pts.length >= 60 && s.pts[2] === 200
          && s.pts.filter((_, i) => i % 5 === 2).includes(800))) return true;
      }
      return false;
    }, { what: "native pen pressure uploaded" });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/ink-native-touch-pen.png` });
    await page.evaluate(() => localStorage.setItem("gamma-ink-pen-only", "1"));
    await page.reload();
    await waitForPdf(page, 1);
    await until(async () => await page.locator(paths).count() === count + 2, { what: "native strokes restored" });
    await page.click("button[aria-label='Handwriting tools']");
    const scrollStart = await page.locator(".pdfViewer").evaluate((el) => el.scrollTop);
    box = await page.locator('[data-page="1"]').boundingBox();
    const fingerX = Math.round(box.x + 300);
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: fingerX, y: 500 }] });
    for (let i = 1; i <= 10; i++) {
      await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: fingerX, y: 500 - i * 12 }] });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await until(async () => await page.locator(".pdfViewer").evaluate((el) => el.scrollTop) > scrollStart + 20,
      { what: "fingers scroll with a pen armed in pen-only mode" });
    assertEq(await page.locator(paths).count(), count + 2, "finger navigation adds no ink");
    await cdp.detach();
    assertNoProblems(page);
  });

  await step("ink: two tabs changing one group at once keep both changes (the save merges by stroke)", async () => {
    // Tab A draws two strokes into a fresh group; tab B erases one of them
    // while A draws a third. B's save is held until A's has landed, so it
    // arrives with a base the server has moved past: the server merges
    // (ops.py merge_ink) and both tabs end with A's new stroke and without
    // the erased one.
    await page.reload();
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Handwriting tools']");
    await page.keyboard.press("p");
    const paths = '[data-page="1"] .inkLayer path';
    const known = new Set((await account.api(`/api/blocks/${pageId}/subtree`)).block.children.map((b) => b.id));
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 600], [box.x + 250, box.y + 600]);
    await drawLine(page, [box.x + 100, box.y + 650], [box.x + 250, box.y + 650]);
    const group = await until(async () => {
      const d = await account.api(`/api/blocks/${pageId}/subtree`);
      const g = d.block.children.find((b) => !known.has(b.id) && b.properties?.ink_url);
      return g && g.properties.ink_strokes === 2 ? g : null;
    }, { what: "a new group with two strokes saved" });
    const firstY = (ink) => Math.min(...ink.strokes.map((st) => st.pts[1] / 100));
    const erasedY = firstY(await account.api(group.properties.ink_url)); // the upper stroke, which B erases
    const ctx2 = await account.context(browser);
    try {
      const other = await openPage(ctx2, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      await waitForPdf(other, 1);
      const countA = await page.locator(paths).count();
      await until(async () => await other.locator(paths).count() === countA, { what: "tab B shows the group" });
      let release;
      const held = new Promise((resolve) => { release = resolve; });
      let holding = false;
      const url = `**/api/blocks/${group.id}`;
      await other.route(url, async (route) => {
        if (route.request().method() === "PUT") { holding = true; await held; }
        await route.continue();
      });
      await other.click("button[aria-label='Handwriting tools']");
      await other.waitForSelector(".pdfInkBar");
      await other.keyboard.press("e");
      const b2 = await other.locator('[data-page="1"]').boundingBox();
      await drawLine(other, [b2.x + 175, b2.y + 590], [b2.x + 175, b2.y + 610]); // through the first stroke
      await until(async () => await other.locator(paths).count() === countA - 1, { what: "tab B erased a stroke" });
      await until(() => holding, { what: "tab B's save held in flight" });
      await drawLine(page, [box.x + 100, box.y + 700], [box.x + 250, box.y + 700]); // A's third stroke joins its group
      await until(async () => (await account.api(`/api/blocks/${group.id}`)).properties.ink_strokes === 3,
        { what: "tab A's save landed first" });
      release();
      await until(async () => {
        const block = await account.api(`/api/blocks/${group.id}`);
        if (block.properties.ink_strokes !== 2) return false;
        return firstY(await account.api(block.properties.ink_url)) > erasedY + 5;
      }, { what: "the merge kept A's new stroke and dropped the erased one" });
      await until(async () => await page.locator(paths).count() === countA, { what: "tab A shows the merge" });
      await until(async () => await other.locator(paths).count() === countA, { what: "tab B shows the merge" });
      await other.unroute(url);
      assertNoProblems(other);
    } finally { await ctx2.close(); }
    assertNoProblems(page);
  });

  // --- the pen feel (docs/dev/handwriting.md "What the user sees") ---
  const pathCount = () => page.locator('[data-page="1"] .inkLayer path').count();
  const pillText = async () => (await page.locator(".statusPill").allInnerTexts().catch(() => [])).join(" | ");
  // The strokes saved on the page, and the ones saved since.
  const savedStrokes = () => savedInkStrokes(account, pageId);
  const newStrokes = (before, n, what) => newInkStrokes(account, pageId, before, n, what);
  // The armed preset's options row: a tap on the armed tool opens it.
  const openOptions = async () => {
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();
    await page.waitForSelector(".pdfInkSub");
  };
  const armedLabel = () => page.locator(".pdfInkBar .inkToolBtn.modeActive").getAttribute("aria-label");
  // The strip, open (the pen button toggles it, so only when it is closed).
  const openStrip = async () => {
    if (!(await page.locator(".pdfInkBar").count())) await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
  };
  // Settings → Reading & editing, its rows, and back.
  const openReadingSettings = async () => {
    await page.locator('[data-guide="header.account"]').click();
    await page.getByRole("button", { name: "Settings…", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Settings", exact: true });
    await dialog.waitFor();
    await page.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Reading & editing", exact: true }).click();
    return dialog;
  };
  const settingRow = (name) => page.locator(`.settingsPane [data-setting="${name}"]`).first();
  const closeSettings = async (dialog) => {
    await page.getByRole("button", { name: "Close settings", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
  };
  // A pen stroke of synthetic pointer events on their own clock: points
  // [x, y, ms] from the page's corner, the lift at the last point. The
  // scribble and the smoothing read the samples' timing and spacing.
  const penStroke = (points) => page.locator('[data-page="1"]').evaluate((el, points) => {
    const box = el.getBoundingClientRect();
    const send = (type, x, y, time) => {
      const ev = new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 95, isPrimary: true,
        button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + x, clientY: box.top + y, pressure: type === "pointerup" ? 0 : 0.5 });
      Object.defineProperty(ev, "timeStamp", { value: 9000 + time });
      el.dispatchEvent(ev);
    };
    points.forEach(([x, y, time], i) => send(i ? "pointermove" : "pointerdown", x, y, time));
    const [x, y, time] = points[points.length - 1];
    send("pointerup", x, y, time + 8);
  }, points);
  // A saved stroke's spread across the line, over its middle (the start and
  // the lift are the pen's own points), in page points.
  const spread = (st) => {
    const a = decodeStroke(st);
    const ys = a.filter((q) => Math.min(Math.abs(q.x - a[0].x), Math.abs(q.x - a.at(-1).x)) > 30).map((q) => q.y);
    return Math.max(...ys) - Math.min(...ys);
  };

  await step("ink: Settings → Reading & editing has the account's Handwriting rows and this device's, and Undo with fingers is set to Double tap", async () => {
    const dialog = await openReadingSettings();
    const row = settingRow;
    for (const name of ["Handwriting", "Reset presets", "Eraser returns to the last tool", "Erases", "Draw and hold", "Scribble to erase",
      "This device", "Draws with", "Stylus draws right away", "Pressure-sensitive strokes", "Undo with fingers", "Hover preview"]) {
      assert(await row(name).count(), `the row "${name}" is in Reading & editing`);
    }
    // the Apple Pencil's gestures and haptics are the iPad app's alone (ipad.mjs shows them under its host)
    assertEq(await row("Apple Pencil").count(), 0, "no Apple Pencil section in a browser");
    assertEq(await row("Hover preview").locator("input[type=checkbox]").isChecked(), true, "the hover preview is on by default");
    const scope = (title) => page.locator(`.settingsPane .setSection[data-setting="${title}"] .setScope`).getAttribute("data-scope");
    assertEq(await scope("Handwriting"), "account", "Handwriting follows the account");
    assertEq(await scope("This device"), "browser", "This device stays with the browser");
    assertEq(await row("Draw and hold").locator("input[type=checkbox]").isChecked(), true, "draw and hold is on by default");
    assertEq(await row("Scribble to erase").locator("input[type=checkbox]").isChecked(), false, "scribble to erase is off by default");
    assertEq(await row("Erases").getByRole("button", { name: "All", exact: true }).getAttribute("aria-pressed"), "true", "erases everything by default");
    // a mouse's browser: finger undo is off until asked for
    assertEq(await row("Undo with fingers").getByRole("button", { name: "Off", exact: true }).getAttribute("aria-pressed"), "true");
    await row("Undo with fingers").getByRole("button", { name: "Double tap", exact: true }).click();
    assertEq(await row("Undo with fingers").getByRole("button", { name: "Double tap", exact: true }).getAttribute("aria-pressed"), "true");
    assertEq(await page.evaluate(() => localStorage.getItem("gamma-ink-finger-undo")), "double", "a browser preference");
    // the search knows every row by its section
    await page.getByRole("searchbox", { name: "Search settings" }).fill("fingers undo");
    await page.locator(".settingsSearchResult").filter({ has: page.getByText("Undo with fingers", { exact: true }) }).first().waitFor();
    await closeSettings(dialog);
    assertNoProblems(page);
  });

  await step("ink: a pen held still at the end of a stroke snaps it to a straight line (pen: false, shape line) with Undo on the pill", async () => {
    await openStrip();
    await page.keyboard.press("1");
    const saved = await savedStrokes();
    const before = await pathCount();
    await page.locator('[data-page="1"]').evaluate(async (el) => {
      const box = el.getBoundingClientRect();
      const send = (type, x, y, time) => {
        const ev = new PointerEvent(type, { bubbles: true, cancelable: true,
          pointerType: "pen", pointerId: 91, button: 0, buttons: type === "pointerup" ? 0 : 1,
          clientX: box.left + x, clientY: box.top + y, pressure: type === "pointerup" ? 0 : 0.5 });
        Object.defineProperty(ev, "timeStamp", { value: 5000 + time });
        el.dispatchEvent(ev);
      };
      // a slightly wobbly 150 px line drawn over 300 ms, then the tip stays still
      send("pointerdown", 100, 450, 0);
      for (let i = 1; i <= 10; i++) send("pointermove", 100 + i * 15, 450 + (i % 2 ? 1.2 : -1.2), i * 30);
      await new Promise((r) => setTimeout(r, 900)); // past the 600 ms hold
      send("pointerup", 250, 450, 1200);
    });
    await until(async () => (await pathCount()) === before + 1, { what: "the snapped stroke drawn" });
    const [st] = await newStrokes(saved, 1, "the held stroke saved");
    assertEq(st.pen, false, "an even line");
    assertEq(st.ch, "xyt", "no pressure channel on a snapped stroke");
    assertEq(st.shape?.kind, "line", `the shape record (${JSON.stringify(st.shape)})`);
    assertEq(st.pts.length, 6, "two samples");
    assert(Math.abs(st.pts[4]) <= 1, `a line within 5° of horizontal is snapped level (dy ${st.pts[4]})`);
    await until(async () => (await pillText()).includes("Straightened."), { what: "the Straightened pill" });
    assert(await page.locator(".statusPill").getByRole("button", { name: "Undo", exact: true }).count(), "Undo is on the pill");
    await page.locator(".statusPill").getByRole("button", { name: "Undo", exact: true }).click();
    await until(async () => (await pathCount()) === before, { what: "the snap undone from the pill" });
    assertNoProblems(page);
  });

  await step("ink: a closed circle and a closed rectangle held still where they began snap to their shapes (pen: false; the circle an ellipse record with rx = ry)", async () => {
    await openStrip();
    await page.keyboard.press("1");
    // A pen stroke through `points` ([x, y, ms] from the page's corner), the
    // tip then still past the 600 ms hold, and the lift. A closed stroke ends
    // where it began: its extent, not its chord, carries it past the size floor.
    const heldStroke = (points, pointerId) => page.locator('[data-page="1"]').evaluate(async (el, [points, pointerId]) => {
      const box = el.getBoundingClientRect();
      const send = (type, [x, y, time]) => {
        const ev = new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId, isPrimary: true,
          button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + x, clientY: box.top + y, pressure: type === "pointerup" ? 0 : 0.5 });
        Object.defineProperty(ev, "timeStamp", { value: 70000 + time });
        el.dispatchEvent(ev);
      };
      points.forEach((p, i) => send(i ? "pointermove" : "pointerdown", p));
      await new Promise((r) => setTimeout(r, 900)); // past the 600 ms hold
      const last = points[points.length - 1];
      send("pointerup", [last[0], last[1], last[2] + 900]);
    }, [points, pointerId]);
    // a circle of 60 px drawn over 540 ms, back at its start; a 220 × 130 px rectangle from its top-left corner, back to it
    const circle = Array.from({ length: 37 }, (_, i) => { const a = (i % 36) * Math.PI / 18; return [330 + 60 * Math.cos(a), 330 + 60 * Math.sin(a), 15 * i]; });
    const side = (a, b, n) => Array.from({ length: n }, (_, i) => [a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n]);
    const [c0, c1, c2, c3] = [[200, 520], [420, 520], [420, 650], [200, 650]];
    const rect = [...side(c0, c1, 20), ...side(c1, c2, 12), ...side(c2, c3, 20), ...side(c3, c0, 12), c0].map(([x, y], i) => [x, y, 10 * i]);
    for (const [name, points, pointerId, kind, fits] of [
      ["circle", circle, 92, "ellipse", (p) => p.rx === p.ry && p.angle === 0],
      ["rectangle", rect, 93, "rect", (p) => p.angle === 0 && p.w > p.h],
    ]) {
      const saved = await savedStrokes();
      const before = await pathCount();
      await heldStroke(points, pointerId);
      await until(async () => (await pathCount()) === before + 1, { what: `the held ${name} drawn` });
      const [st] = await newStrokes(saved, 1, `the held ${name} saved`);
      assertEq(st.pen, false, `the ${name} is even`);
      assertEq(st.shape?.kind, kind, `the ${name}'s shape record (${JSON.stringify(st.shape)})`);
      assert(fits(st.shape.params), `the ${name}'s params (${JSON.stringify(st.shape.params)})`);
      await until(async () => (await pillText()).includes(`Shape: ${name}.`), { what: `the "Shape: ${name}." pill` });
      await page.locator(".statusPill").getByRole("button", { name: "Undo", exact: true }).click();
      await until(async () => (await pathCount()) === before, { what: `the ${name} undone from the pill` });
    }
    assertNoProblems(page);
  });

  await step("ink: a stroke's ink-live chunks on the page socket carry its page, id, look, n and samples, and no g or k", async () => {
    await openStrip();
    await page.keyboard.press("1");
    // every message the page's sockets send from here on
    await page.evaluate(() => {
      const send = WebSocket.prototype.send;
      window.e2eSocketSent = [];
      window.e2eSocketSend = send;
      WebSocket.prototype.send = function (data) { window.e2eSocketSent.push(String(data)); return send.call(this, data); };
    });
    const saved = await savedStrokes();
    const before = await pathCount();
    try {
      box = await page.locator('[data-page="1"]').boundingBox();
      await drawLine(page, [box.x + 120, box.y + 330], [box.x + 300, box.y + 350]);
      const [st] = await newStrokes(saved, 1, "the stroke saved");
      const chunks = (await page.evaluate(() => window.e2eSocketSent)).filter((s) => s.includes('"ink-live"'))
        .map((s) => JSON.parse(s)).filter((m) => m.t === "ink-live" && m.id === st.id);
      assert(chunks.length && chunks.at(-1).done === true, `the stroke went out live, its last chunk marked done (${chunks.length} chunks)`);
      for (const m of chunks) {
        assert(["page", "id", "style", "n", "samples"].every((key) => key in m) && !("g" in m) && !("k" in m),
          `a chunk's keys (${JSON.stringify(Object.keys(m))})`);
      }
      assertEq(chunks[0].page, 1, "the surface: the PDF page");
    } finally {
      await page.evaluate(() => { WebSocket.prototype.send = window.e2eSocketSend; });
    }
    await page.keyboard.press("Control+z");
    await until(async () => (await pathCount()) === before, { what: "the stroke undone" });
    assertNoProblems(page);
  });

  await step("ink: a highlighter preset set to Straight: Always flattens a wavy highlight to its chord", async () => {
    await page.keyboard.press("h");
    await openOptions();
    const row = page.locator(".pdfInkSub");
    assertEq(await row.getByRole("button", { name: "Hold", exact: true }).getAttribute("aria-pressed"), "true", "Hold is the default");
    assertEq(await row.locator(".inkSmoothDot").count(), 5, "the Smoothing ladder: Off and four levels");
    await row.getByRole("button", { name: "Always", exact: true }).click();
    assertEq(await row.getByRole("button", { name: "Always", exact: true }).getAttribute("aria-pressed"), "true");
    assert(JSON.parse(await page.evaluate(() => localStorage.getItem("gamma-ink-tools"))).some((t) => t.straight === "always"), "the preset keeps it");
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click(); // closes the row
    const saved = await savedStrokes();
    const before = await pathCount();
    box = await page.locator('[data-page="1"]').boundingBox();
    await page.mouse.move(box.x + 100, box.y + 500);
    await page.mouse.down();
    for (let i = 1; i <= 20; i++) await page.mouse.move(box.x + 100 + i * 10, box.y + 500 + (i % 2 ? 3 : -3));
    await page.mouse.up();
    await until(async () => (await pathCount()) === before + 1, { what: "the highlight drawn" });
    const [st] = await newStrokes(saved, 1, "the straight highlight saved");
    assertEq(st.tool, "highlighter");
    assertEq(st.pen, false);
    assertEq(st.shape?.kind, "line", `the chord carries a line record (${JSON.stringify(st.shape)})`);
    assertEq(st.pts.length, 6, "two samples, xyt");
    assertEq(st.pts[4], 0, "snapped horizontal");
    await until(async () => (await pillText()).includes("Straightened."), { what: "the first Always straightening says so" });
    // back to Hold, so the presets are as they were
    await openOptions();
    await page.locator(".pdfInkSub").getByRole("button", { name: "Hold", exact: true }).click();
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();
    assertNoProblems(page);
  });

  await step("ink: the eraser set to Highlighters leaves the pen stroke under its pass intact; the hover ring carries the kind", async () => {
    const saved = await savedStrokes();
    const before = await pathCount();
    box = await page.locator('[data-page="1"]').boundingBox();
    await page.keyboard.press("1");
    await drawLine(page, [box.x + 100, box.y + 560], [box.x + 260, box.y + 560]);
    await page.keyboard.press("h");
    await drawLine(page, [box.x + 100, box.y + 560], [box.x + 260, box.y + 560]);
    await until(async () => (await pathCount()) === before + 2, { what: "a pen stroke and a highlight over it" });
    const drawn = await newStrokes(saved, 2, "both strokes saved");
    const penStroke = drawn.find((st) => st.tool === "pen"), highlight = drawn.find((st) => st.tool === "highlighter");
    assert(penStroke && highlight, "a pen stroke and a highlight");
    await page.keyboard.press("e");
    await openOptions();
    const row = page.locator(".pdfInkSub");
    assertEq(await row.getByRole("button", { name: "All", exact: true }).getAttribute("aria-pressed"), "true");
    await row.getByRole("button", { name: "Highlighters", exact: true }).click();
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkEraserFilter === "highlighter", { what: "the filter is the account's" });
    await page.mouse.move(box.x + 180, box.y + 540);
    await until(() => page.locator('[data-page="1"] .inkCursor[data-filter="highlighter"]').count(), { what: "the hover ring shows the kind" });
    await drawLine(page, [box.x + 180, box.y + 540], [box.x + 182, box.y + 580]);
    await until(async () => (await pathCount()) === before + 1, { what: "the highlight gone" });
    await until(async () => !(await savedStrokes()).has(highlight.id), { what: "the highlight gone from the file" });
    assert((await savedStrokes()).has(penStroke.id), "the pen stroke under the pass stays");
    await row.getByRole("button", { name: "All", exact: true }).click();
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkEraserFilter === "all", { what: "back to everything" });
    await page.mouse.move(box.x + 300, box.y + 540);
    await page.mouse.move(box.x + 302, box.y + 542);
    await until(async () => (await page.locator('[data-page="1"] .inkCursor').count()) === 1
      && (await page.locator('[data-page="1"] .inkCursor[data-filter]').count()) === 0, { what: "no glyph without a filter" });
    assertNoProblems(page);
  });

  await step("ink: with Back to last tool on, lifting the eraser re-arms the preset used before it, its row closed", async () => {
    await page.keyboard.press("2");
    assert((await armedLabel()).includes("key 2"), "preset 2 armed");
    await page.keyboard.press("e");
    await openOptions();
    const toggle = page.locator(".pdfInkSub").getByRole("button", { name: "Back to last tool", exact: true });
    assertEq(await toggle.getAttribute("aria-pressed"), "false", "off by default");
    await toggle.click();
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkEraserReturn === true, { what: "the return is the account's" });
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 400, box.y + 700], [box.x + 420, box.y + 720]); // over nothing: the return does not depend on erasing
    await until(async () => (await armedLabel() || "").includes("key 2"), { what: "preset 2 armed again after the eraser lifted" });
    assertEq(await page.locator(".pdfInkSub").count(), 0, "the options row is closed");
    await page.keyboard.press("e");
    await openOptions();
    await page.locator(".pdfInkSub").getByRole("button", { name: "Back to last tool", exact: true }).click();
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkEraserReturn === false, { what: "turned off again" });
    await page.keyboard.press("1");
    assertNoProblems(page);
  });

  await step("ink: a two-finger double tap undoes the last stroke and says so once, with Turn off", async () => {
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 620], [box.x + 240, box.y + 640]);
    const before = await pathCount();
    const chord = (ids, x, y) => page.locator('[data-page="1"]').evaluate(async (el, [ids, x, y]) => {
      const box = el.getBoundingClientRect();
      const send = (type, id, dx) => el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
        pointerType: "touch", pointerId: id, isPrimary: id === ids[0], button: 0, buttons: type === "pointerup" ? 0 : 1,
        clientX: box.left + x + dx, clientY: box.top + y }));
      ids.forEach((id, i) => send("pointerdown", id, i * 40));
      await new Promise((r) => setTimeout(r, 40));
      ids.forEach((id, i) => send("pointerup", id, i * 40));
    }, [ids, x, y]);
    await chord([201, 202], 300, 300);
    await new Promise((r) => setTimeout(r, 120));
    await chord([203, 204], 300, 300);
    await until(async () => (await pathCount()) === before - 1, { what: "the stroke undone by the chord" });
    await until(async () => (await pillText()).includes("Two fingers undid a stroke."), { what: "the first chord explains itself" });
    await page.locator(".statusPill").getByRole("button", { name: "Turn off", exact: true }).click();
    await until(async () => (await page.evaluate(() => localStorage.getItem("gamma-ink-finger-undo"))) === "off", { what: "Turn off sets the preference" });
    await page.getByRole("button", { name: "Redo ink", exact: true }).click();
    await until(async () => (await pathCount()) === before, { what: "redone from the strip" });
    await chord([205, 206], 300, 300);
    await new Promise((r) => setTimeout(r, 120));
    await chord([207, 208], 300, 300);
    await sleep(400);
    assertEq(await pathCount(), before, "off: a chord undoes nothing");
    assertNoProblems(page);
  });

  await step("ink: with Scribble to erase on, a fast back-and-forth with the pen erases the stroke under it as one undo step, and nothing is drawn", async () => {
    let dialog = await openReadingSettings();
    await settingRow("Scribble to erase").locator("input[type=checkbox]").check();
    await until(async () => (await pillText()).includes("Scribble to erase is on"), { what: "turning it on explains the gesture" });
    await closeSettings(dialog);
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkScribbleErase === true, { what: "the account's" });
    await page.keyboard.press("1");
    box = await page.locator('[data-page="1"]').boundingBox();
    const saved = await savedStrokes();
    const before = await pathCount();
    await drawLine(page, [box.x + 100, box.y + 450], [box.x + 160, box.y + 450]);
    await until(async () => (await pathCount()) === before + 1, { what: "a pen stroke to scratch out" });
    const [victim] = await newStrokes(saved, 1, "the stroke saved");
    // five passes over it, 70 px wide and 360 ms long: four sharp reversals, fast and dense
    const scribble = [];
    for (let pass = 0; pass < 5; pass++) for (let i = 0; i < 8; i++) {
      const u = i / 8;
      scribble.push([pass % 2 ? 165 - 70 * u : 95 + 70 * u, 450 + (pass % 2 ? 4 : -4) + (i % 2 ? 1 : -1), (pass * 8 + i) * 9]);
    }
    scribble.push([165, 454, 40 * 9]);
    await penStroke(scribble);
    await until(async () => (await pathCount()) === before, { what: "the stroke gone and the scribble not drawn" });
    await until(async () => !(await savedStrokes()).has(victim.id), { what: "the stroke gone from the file" });
    assertEq((await savedStrokes()).size, saved.size, "the scribble itself is not saved");
    await until(async () => (await pillText()).includes("Erased 1 stroke."), { what: "the pill counts the erasure" });
    await page.locator(".statusPill").getByRole("button", { name: "Undo", exact: true }).click();
    await until(async () => (await pathCount()) === before + 1, { what: "the stroke back from the pill's Undo" });
    await until(async () => (await savedStrokes()).has(victim.id), { what: "and back in the file" });
    dialog = await openReadingSettings();
    await settingRow("Scribble to erase").locator("input[type=checkbox]").uncheck();
    await closeSettings(dialog);
    await until(async () => (await account.api("/api/prefs/profile")).value?.inkScribbleErase === false, { what: "off again" });
    assertNoProblems(page);
  });

  await step("ink: Smoothing 4 on a preset draws a shaky pen's zigzag as a calm line, where Smoothing off copies it", async () => {
    await page.keyboard.press("1");
    const zigzag = (y) => Array.from({ length: 61 }, (_, i) => [100 + i * 4, y + (i % 2 ? 5 : -5), i * 12]);
    let saved = await savedStrokes();
    let before = await pathCount();
    await penStroke(zigzag(400));
    await until(async () => (await pathCount()) === before + 1, { what: "the zigzag drawn with Smoothing off" });
    const [raw] = await newStrokes(saved, 1, "the raw zigzag saved");
    await openOptions();
    const row = page.locator(".pdfInkSub");
    assertEq(await row.locator(".inkSmoothing button").count(), 5, "Off and four levels");
    assertEq(await row.getByRole("button", { name: "Smoothing off", exact: true }).getAttribute("aria-pressed"), "true", "off by default");
    await row.getByRole("button", { name: "Smoothing 4 of 4", exact: true }).click();
    assertEq(await row.getByRole("button", { name: "Smoothing 4 of 4", exact: true }).getAttribute("aria-pressed"), "true");
    assertEq(JSON.parse(await page.evaluate(() => localStorage.getItem("gamma-ink-tools"))).find((t) => t.stabilize)?.stabilize, 4, "the preset keeps the level");
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click(); // closes the row
    saved = await savedStrokes();
    before = await pathCount();
    await penStroke(zigzag(420));
    await until(async () => (await pathCount()) === before + 1, { what: "the zigzag drawn with Smoothing 4" });
    const [smooth] = await newStrokes(saved, 1, "the smoothed stroke saved");
    assert(spread(raw) > 5, `off: the line copies the shake (spread ${spread(raw).toFixed(2)} pt)`);
    assert(spread(smooth) < 0.4 * spread(raw), `Smoothing 4: a calm line (spread ${spread(smooth).toFixed(2)} of ${spread(raw).toFixed(2)} pt)`);
    // both strokes start at the pen's own point (the first sample passes through), 20 px apart; the
    // smoothed one must end at the pen's lift, the same 20 px under the raw one's
    const first = (st) => decodeStroke(st)[0], end = (st) => decodeStroke(st).at(-1);
    const dy = first(smooth).y - first(raw).y;
    assert(Math.abs(end(smooth).x - end(raw).x) < 1 && Math.abs(end(smooth).y - end(raw).y - dy) < 1,
      `the smoothed line still reaches the lift point (ends ${JSON.stringify(end(smooth))} vs ${JSON.stringify(end(raw))}, starts ${dy.toFixed(2)} apart)`);
    await openOptions();
    await page.locator(".pdfInkSub").getByRole("button", { name: "Smoothing off", exact: true }).click();
    await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();
    assertNoProblems(page);
  });

  // --- pen styles and lines (docs/dev/handwriting.md "The brush table", "Dash geometry") ---
  // A stylus stroke of synthetic pointer events on their own clock: points
  // [x, y, ms, pressure, altitude°, azimuth°] from the page's corner (the
  // tilt optional, as altitudeAngle / azimuthAngle), the lift at the last.
  const stylusStroke = (points) => page.locator('[data-page="1"]').evaluate((el, points) => {
    const box = el.getBoundingClientRect();
    const send = (type, [x, y, time, pressure, alt, az]) => {
      const ev = new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 97, isPrimary: true,
        button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + x, clientY: box.top + y, pressure: type === "pointerup" ? 0 : pressure });
      Object.defineProperty(ev, "timeStamp", { value: 30000 + time });
      if (alt != null) {
        Object.defineProperty(ev, "altitudeAngle", { value: alt * Math.PI / 180 });
        Object.defineProperty(ev, "azimuthAngle", { value: (az || 0) * Math.PI / 180 });
      }
      el.dispatchEvent(ev);
    };
    points.forEach((p, i) => send(i ? "pointermove" : "pointerdown", p));
    const last = points[points.length - 1];
    send("pointerup", [last[0], last[1], last[2] + 8, 0, last[4], last[5]]);
  }, points);
  const pickStyle = (name) => pickPenStyle(page, name);
  const lineRow = () => page.locator(".pdfInkSub").getByRole("group", { name: "Line", exact: true });
  // The group (block id) a saved stroke is in, and the last path drawn for that group.
  const groupOf = async (strokeId) => {
    const d = await account.api(`/api/blocks/${pageId}/subtree`);
    for (const b of d.block.children) {
      if (!b.properties?.ink_url?.endsWith(".ink")) continue;
      if ((await account.api(b.properties.ink_url)).strokes.some((st) => st.id === strokeId)) return b.id;
    }
    return null;
  };
  const lastPath = (gid) => page.locator(`[data-page="1"] .inkLayer g[data-ink-id="${gid}"] path`).last();
  // Preset 1 back to a solid plain pen, for the steps (and scenarios) after:
  // the Pen style from its options row (with `line`, the Solid line too), the
  // row closed. The presets are the account's, which the scenarios after
  // these share: then wait until preset 1 is a solid plain pen there again.
  const resetPreset = async ({ line = false } = {}) => {
    if (!(await page.locator(".pdfInkSub").count())) await openOptions().catch(() => {});
    if (await page.locator(".pdfInkSub .inkStyleBtn").count()) {
      if ((await page.locator(".pdfInkSub .inkStyleBtn").getAttribute("aria-label")) !== "Style: Pen") await pickStyle("Pen").catch(() => {});
      if (line) await lineRow().getByRole("button", { name: "Solid", exact: true }).click().catch(() => {});
    }
    if (await page.locator(".pdfInkSub").count()) await page.locator(".pdfInkBar .inkToolBtn.modeActive").click().catch(() => {});
    await until(async () => {
      const first = ((await account.api("/api/prefs/profile")).value?.inkTools || [])[0];
      return !first || (!first.brush && !first.dash && !first.stabilize);
    }, { what: "preset 1 a solid plain pen on the account again" }).catch(() => {});
  };

  await step("ink: the Style picker's Brush bakes a tapered pressure stroke; Dashed cuts it into dashes and dashes an even line; Shift+[ ] step the style", async () => {
    await openStrip();
    await page.keyboard.press("1");
    try {
      await openOptions();
      await pickStyle("Brush");
      assert((await armedLabel()).includes(" brush pen · "), `the preset is named a brush pen (${await armedLabel()})`);
      await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();   // closes the row
      let saved = await savedStrokes();
      // a 120 px line at even pressure, a sample every 4 px: tapered in and out
      await stylusStroke(Array.from({ length: 31 }, (_, i) => [150 + 4 * i, 480, i * 8, 0.6]));
      const [brush] = await newStrokes(saved, 1, "the brush stroke saved");
      assertEq(JSON.stringify([brush.brush, brush.ch, brush.pen]), JSON.stringify(["brush", "xyptw", true]));
      const ws = decodeStroke(brush).map((q) => q.w), mid = ws[Math.floor(ws.length / 2)];
      assert(ws[0] < mid && ws.at(-1) < mid, `tapered at both ends (${ws[0]}, ${mid}, ${ws.at(-1)})`);
      assert(Math.abs(mid - 1.2) < 0.002, `2p in the middle (${mid})`);
      // Dashed: stored as the stroke's line, drawn as outlined dashes (several subpaths in its one path)
      await openOptions();
      assertEq(await lineRow().getByRole("button", { name: "Solid", exact: true }).getAttribute("aria-pressed"), "true", "solid by default");
      await lineRow().getByRole("button", { name: "Dashed", exact: true }).click();
      assertEq(await lineRow().getByRole("button", { name: "Dashed", exact: true }).getAttribute("aria-pressed"), "true");
      await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();
      saved = await savedStrokes();
      await stylusStroke(Array.from({ length: 31 }, (_, i) => [150 + 4 * i, 520, i * 8, 0.6]));
      const [dashed] = await newStrokes(saved, 1, "the dashed brush stroke saved");
      assertEq(JSON.stringify([dashed.brush, dashed.dash]), JSON.stringify(["brush", "dashed"]));
      const gid = await groupOf(dashed.id);
      await until(async () => ((await lastPath(gid).getAttribute("d")) || "").split("M").length - 1 >= 2, { what: "the dashed stroke drawn as its dashes" });
      // an even line (a mouse Ballpoint) takes the pattern as a stroke-dasharray, two widths on, two off
      await openOptions();
      await pickStyle("Ballpoint");
      await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();
      saved = await savedStrokes();
      box = await page.locator('[data-page="1"]').boundingBox();
      await drawLine(page, [box.x + 150, box.y + 560], [box.x + 270, box.y + 560]);
      const [even] = await newStrokes(saved, 1, "the dashed ballpoint stroke saved");
      assertEq(JSON.stringify([even.brush, even.dash, even.pen, even.ch]), JSON.stringify(["monoline", "dashed", false, "xyt"]));
      await until(async () => (await lastPath(await groupOf(even.id)).getAttribute("stroke-dasharray")) === `${2 * even.size} ${2 * even.size}`,
        { what: "the even line stroked with its dashes" });
      // Shift+] / Shift+[ step the armed pen's style, wrapping, with the width's pill
      await page.keyboard.press("Shift+BracketRight");
      await until(async () => (await armedLabel()).includes(" brush pen · "), { what: "Shift+] steps Ballpoint to Brush" });
      await until(async () => (await pillText()).includes("brush pen · "), { what: "the pill names the style" });
      await page.keyboard.press("Shift+BracketLeft");
      await page.keyboard.press("Shift+BracketLeft");
      await until(async () => { const label = await armedLabel(); return / pen · /.test(label) && !/ballpoint|brush|pencil/.test(label); },
        { what: "back to the plain pen" });
      await page.keyboard.press("Shift+BracketLeft");
      await until(async () => (await armedLabel()).includes(" calligraphy pen · "), { what: "Shift+[ wraps from Pen to Calligraphy" });
      await page.keyboard.press("Shift+BracketLeft");
      await until(async () => (await armedLabel()).includes(" pencil · "), { what: "and on to Pencil" });
      // Calligraphy's smoothing of 2 is its default, never stored: the pen stepped past it smooths as before (not at all)
      await until(async () => { const first = JSON.parse(await page.evaluate(() => localStorage.getItem("gamma-ink-tools")) || "[]")[0];
        return first?.brush === "pencil" && !("stabilize" in first); }, { what: "a pencil with no smoothing left behind by Calligraphy" });
    } finally {
      await resetPreset({ line: true });
    }
    assertNoProblems(page);
  });

  await step("ink: a Brush preset's held straight line is stored at constant width (its brush kept, shape line, xyt) and the Markdown export's SVG draws it at the preset's size", async () => {
    await openStrip();
    await page.keyboard.press("1");
    try {
      await openOptions();
      await pickStyle("Brush");
      await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();   // closes the row
      const size = Number((await armedLabel()).match(/· ([\d.]+) pt/)?.[1]);
      assert(size > 0, `the preset's size from its label (${await armedLabel()})`);
      const saved = await savedStrokes();
      const before = await pathCount();
      // a slightly wobbly 150 px stylus line drawn over 300 ms, then the tip still past the 600 ms hold
      await page.locator('[data-page="1"]').evaluate(async (el) => {
        const box = el.getBoundingClientRect();
        const send = (type, x, y, time) => {
          const ev = new PointerEvent(type, { bubbles: true, cancelable: true, pointerType: "pen", pointerId: 99, isPrimary: true,
            button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: box.left + x, clientY: box.top + y, pressure: type === "pointerup" ? 0 : 0.6 });
          Object.defineProperty(ev, "timeStamp", { value: 50000 + time });
          el.dispatchEvent(ev);
        };
        send("pointerdown", 120, 690, 0);
        for (let i = 1; i <= 10; i++) send("pointermove", 120 + i * 15, 690 + (i % 2 ? 1.2 : -1.2), i * 30);
        await new Promise((r) => setTimeout(r, 900));
        send("pointerup", 270, 690, 1200);
      });
      await until(async () => (await pathCount()) === before + 1, { what: "the snapped Brush line drawn" });
      const [st] = await newStrokes(saved, 1, "the held Brush line saved");
      assertEq(JSON.stringify([st.brush, st.pen, st.ch, st.shape?.kind]), JSON.stringify(["brush", false, "xyt", "line"]),
        "a shape is constant width: the brush key kept, no w channel");
      assertEq(st.size, size, "the preset's size");
      const ends = decodeStroke(st);
      assertEq(ends.length, 2, "two samples");
      // The Markdown export draws each group with the server's to_svg: the line's one segment is the size wide, not the taper's 0.1 × size.
      const gid = await groupOf(st.id);
      const group = (await account.api(`/api/blocks/${pageId}/subtree`)).block.children.find((b) => b.id === gid);
      const svgName = group.properties.ink_url.split("/").pop().replace(/\.ink$/, ".svg");
      const res = await account.api(`/api/pages/${pageId}/export?mode=readable&pdf=0`, { raw: true });
      assert(res.ok, `the Markdown export answers ${res.status}`);
      const zipPath = path.join(server.dir, "ink-brush-shape.zip");
      fs.writeFileSync(zipPath, Buffer.from(await res.arrayBuffer()));
      const svg = execFileSync(PYTHON, ["-c", "import sys, zipfile; sys.stdout.write(zipfile.ZipFile(sys.argv[1]).read('assets/' + sys.argv[2]).decode())",
        zipPath, svgName]).toString();
      const near = (a, b) => Math.abs(a - b) < 0.011;
      const segments = [...svg.matchAll(/<path d="M(-?[\d.]+) (-?[\d.]+) L(-?[\d.]+) (-?[\d.]+)" stroke-width="([\d.]+)"\/>/g)]
        .map((m) => m.slice(1).map(Number))
        .filter(([x1, y1, x2, y2]) => near(x1, ends[0].x) && near(y1, ends[0].y) && near(x2, ends[1].x) && near(y2, ends[1].y));
      assertEq(segments.length, 1, `the line is one segment of the group's SVG (${svgName})`);
      assert(near(segments[0][4], size), `drawn ${segments[0][4]} pt wide, the preset's ${size} pt (a hairline would be ${0.1 * size})`);
    } finally {
      await resetPreset();
    }
    assertNoProblems(page);
  });

  await step("ink: a tilted Pencil stores its tilt and shading (a/z and o), fills its shades with the grain, and its hover footprint leans", async () => {
    await openStrip();
    await page.keyboard.press("1");
    try {
      await openOptions();
      await pickStyle("Pencil");
      await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();
      // the hover footprint: an ellipse turned the way the pencil leans
      const hover = await page.locator('[data-page="1"]').evaluate((el) => {
        const box = el.getBoundingClientRect();
        const ev = new PointerEvent("pointermove", { bubbles: true, pointerType: "pen", pointerId: 98, buttons: 0,
          clientX: box.left + 300, clientY: box.top + 600 });
        Object.defineProperty(ev, "altitudeAngle", { value: 40 * Math.PI / 180 });
        Object.defineProperty(ev, "azimuthAngle", { value: 120 * Math.PI / 180 });
        el.dispatchEvent(ev);
        const c = el.querySelector(".inkCursor");
        return { brush: c.dataset.brush, w: parseFloat(c.style.width), h: parseFloat(c.style.height), transform: c.style.transform };
      });
      assertEq(hover.brush, "pencil");
      assert(hover.w > hover.h && hover.transform.includes("rotate(120deg)"), `a leaning ellipse (${JSON.stringify(hover)})`);
      const saved = await savedStrokes();
      // pressure rising 0.3 → 0.9 at an altitude of 40°, the azimuth 120°: wider than upright, darker as it presses
      await stylusStroke(Array.from({ length: 25 }, (_, i) => [150 + 5 * i, 640, i * 8, 0.3 + 0.025 * i, 40, 120]));
      const [pencil] = await newStrokes(saved, 1, "the pencil stroke saved");
      assertEq(JSON.stringify([pencil.brush, pencil.ch]), JSON.stringify(["pencil", "xyptazwo"]));
      const q = decodeStroke(pencil);
      assert(q.every((s) => s.a === 40 && s.z === 120), "the tilt as integer degrees on every sample");
      const law = (s) => (1 + (s.p - 0.5)) * (1 + 2 * (1 - 40 / 90));
      assert(q.every((s) => Math.abs(s.w - Math.round(law(s) * 1000) / 1000) < 0.0011), "w from pressure and tilt");
      assert(q.every((s) => Math.abs(s.o - Math.round((0.15 + 0.85 * s.p) * 1000) / 1000) < 0.0011), "o from pressure");
      assert(q[0].o < q.at(-1).o, "darker as it presses");
      // drawn in shades of the grain: up to three paths, each filled with the layer's pattern for its colour
      const gid = await groupOf(pencil.id);
      const fills = await until(async () => {
        const out = await page.locator(`[data-page="1"] .inkLayer g[data-ink-id="${gid}"] path[fill^="url(#inkGrain"]`).evaluateAll((els) =>
          els.map((p) => [p.getAttribute("fill"), p.getAttribute("fill-opacity")]));
        return out.length >= 2 ? out : null;
      }, { what: "the pencil's shades drawn with the grain" });
      assert(new Set(fills.map(([, o]) => o)).size >= 2, `the shades differ (${JSON.stringify(fills)})`);
      const pattern = fills[0][0].slice(5, -1);
      assertEq(await page.locator(`[data-page="1"] .inkLayer defs pattern[id="${pattern}"] image`).count(), 1, "one grain pattern in the layer's defs");
    } finally {
      await resetPreset();
    }
    assertNoProblems(page);
  });

  await step("ink: the hover footprint dashes a dashed preset's ring; with Hover preview off every pen shows a plain ring", async () => {
    await openStrip();
    await page.keyboard.press("1");
    // a pen hovering over the page (no buttons): the footprint as it is drawn
    const hover = () => page.locator('[data-page="1"]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      el.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, pointerType: "pen", pointerId: 96, buttons: 0,
        clientX: box.left + 320, clientY: box.top + 620 }));
      const c = el.querySelector(".inkCursor");
      return { brush: c.dataset.brush ?? null, dash: c.dataset.dash ?? null, border: getComputedStyle(c).borderTopStyle };
    });
    try {
      await openOptions();
      await pickStyle("Brush");
      await lineRow().getByRole("button", { name: "Dotted", exact: true }).click();
      await page.locator(".pdfInkBar .inkToolBtn.modeActive").click();   // closes the row
      assertEq(JSON.stringify(await hover()), JSON.stringify({ brush: "brush", dash: "dotted", border: "dotted" }), "a dotted brush pen's teardrop");
      const dialog = await openReadingSettings();
      await settingRow("Hover preview").locator("input[type=checkbox]").uncheck();
      assertEq(await page.evaluate(() => localStorage.getItem("gamma-ink-hover-preview")), "0", "a browser preference");
      await closeSettings(dialog);
      assertEq(JSON.stringify(await hover()), JSON.stringify({ brush: null, dash: null, border: "solid" }), "the plain ring");
    } finally {
      const dialog = await openReadingSettings().catch(() => null);
      if (dialog) {
        await settingRow("Hover preview").locator("input[type=checkbox]").check().catch(() => {});
        await closeSettings(dialog).catch(() => {});
      }
      await resetPreset({ line: true });
    }
    assertNoProblems(page);
  });

  await step("ink: Transcribe with AI asks the chat about the group, with the group attached", async () => {
    const up = await account.api("/api/upload-ink", { method: "POST", body: {
      format: "gamma-ink", version: 2, space: { kind: "pdf-page", page: 1, width: 612, height: 792 },
      strokes: [{ id: "tr1", color: "#1f1f1f", size: 2, ch: "xy", pts: [10000, 60000, 3000, 400] }],
    } });
    const group = await account.api("/api/blocks", { method: "POST", body: {
      parent_id: pageId, content: "", properties: { ink_url: up.url, pdf_position: up.pdf_position, ink_strokes: 1 },
    } });
    const tctx = await account.context(browser);
    try {
      await fakeAiModels(tctx);
      await tctx.addInitScript(() => localStorage.setItem("gamma-ai-login-check", "off"));
      const sent = [];
      await tctx.route("**/api/ai/chat", async (route) => {
        sent.push(route.request().postDataJSON());
        await route.fulfill({ contentType: "application/x-ndjson", body: '{"delta":"It says: hello."}\n' });
      });
      const tpage = await openPage(tctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
      const wrap = tpage.locator(`.sortableBlockWrap[data-block-id="${group.id}"]`).first();
      await wrap.hover();
      await wrap.locator(".dragHandle").first().click();
      await tpage.locator(".ctxMenuItem", { hasText: "Transcribe with AI" }).click();
      await until(() => sent.length === 1, { what: "one chat request" });
      assertEq(sent[0].prompt, "Transcribe this handwriting into its caption.", "the request's text");
      assert(sent[0].context_blocks.includes(group.id), "the group rides with it as a chip");
      await tpage.locator(".chatBubble.ai", { hasText: "It says: hello." }).waitFor();
      await tpage.locator(".chatBubble.user", { hasText: "Transcribe this handwriting" }).waitFor();
      assertEq(sent.length, 1, "sent once");
      assertNoProblems(tpage);
    } finally { await tctx.close(); }
  });

  await step("ink: strokes a crashed tab never saved come back on the next visit from the draft journal and are saved — also when the block names their lost upload — and Discard on the pill takes them off again", async () => {
    // The journal is this browser's IndexedDB: a browser profile of its own, and a paper per case.
    const jctx = await account.context(browser);
    const paperFor = async (title) => {
      const up = await account.upload("/api/uploads", makePdf([[title, "and its unsaved stroke"]]), "journal.pdf", "application/pdf");
      const paper = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: title, source_url: up.source_url } });
      return { id: paper.id, url: `${server.base}/?page=${paper.id}&ws=${account.ws}` };
    };
    const groups = async (paper) => ((await account.api(`/api/blocks/${paper.id}/subtree`)).block.children || [])
      .filter((b) => b.properties && "ink_url" in b.properties);
    // the stroke ids the paper's saved files hold
    const savedIds = async (paper) => {
      const out = [];
      for (const b of await groups(paper)) {
        if (!b.properties.ink_url?.endsWith(".ink")) continue;
        const file = await account.api(b.properties.ink_url).catch(() => null);
        out.push(...(file?.strokes || []).map((s) => s.id));
      }
      return out;
    };
    // the journal's entries: their bytes
    const journal = (p) => p.evaluate(() => new Promise((resolve) => {
      const req = indexedDB.open("gamma-ink-journal", 1);
      req.onupgradeneeded = () => { try { req.result.createObjectStore("drafts"); } catch { /* there already */ } };
      req.onerror = () => resolve([]);
      req.onsuccess = () => {
        const db = req.result;
        try {
          const all = db.transaction("drafts", "readonly").objectStore("drafts").getAll();
          all.onsuccess = () => { db.close(); resolve(all.result.map((e) => e.bytes)); };
          all.onerror = () => { db.close(); resolve([]); };
        } catch { db.close(); resolve([]); }
      };
    }));
    const idsOf = (bytes) => JSON.parse(bytes).strokes.map((s) => s.id);
    // A visit whose uploads never land: a stroke, kept by the journal, and the tab crashes
    // (no pagehide, so nothing more is sent). → the entry's bytes.
    const unsavedVisit = async (paper) => {
      const tab = await openPage(jctx, paper.url);
      await tab.route("**/api/upload-ink", (route) => route.abort());
      await waitForPdf(tab, 1);
      await tab.click("button[aria-label='Handwriting tools']");
      await tab.waitForSelector(".pdfInkBar .inkToolBtn.modeActive .inkToolInk");
      const b = await tab.locator('[data-page="1"]').boundingBox();
      await drawLine(tab, [b.x + 120, b.y + 200], [b.x + 260, b.y + 230]);
      const bytes = await until(async () => {
        const all = await journal(tab);
        return all.length === 1 && idsOf(all[0]).length === 1 ? all[0] : null;
      }, { what: "the unsaved stroke in the draft journal" });
      await until(async () => (await tab.locator(".statusPill").allInnerTexts().catch(() => [])).join(" ").includes("Handwriting not saved yet"),
        { what: "the save failing" });
      await until(async () => (await groups(paper)).length === 1, { what: "the group's block in the tree on the server" });
      assertEq(JSON.stringify(await savedIds(paper)), "[]", "no stroke saved on the server");
      const cdp = await jctx.newCDPSession(tab);
      const crashed = new Promise((resolve) => tab.once("crash", resolve));
      cdp.send("Page.crash").catch(() => {});   // never answered: the renderer goes at once
      await Promise.race([crashed, sleep(5000)]);
      assert(tab.isClosed() || await tab.evaluate(() => false).catch(() => true), "the tab crashed");
      await Promise.race([tab.close().catch(() => {}), sleep(5000)]);
      return bytes;
    };
    // The next visit: the strokes are back, drawn and saved, with Discard on the pill.
    const restoredVisit = async (paper, bytes, allow = []) => {
      const tab = await openPage(jctx, paper.url);
      await waitForPdf(tab, 1);
      const pill = tab.locator(".statusPill", { hasText: "Unsaved handwriting from a previous visit was restored." });
      await pill.waitFor();
      await until(async () => (await tab.$$('[data-page="1"] .inkLayer path')).length === 1, { what: "the restored stroke drawn" });
      await until(async () => JSON.stringify(await savedIds(paper)) === JSON.stringify(idsOf(bytes)),
        { what: "the restored stroke saved on the server", timeout: 15000 });
      assertEq((await groups(paper)).length, 1, "into the group it was drawn in");
      await until(async () => (await journal(tab)).length === 0, { what: "the journal entry gone once the save landed" });
      return { tab, pill };
    };
    try {
      // a lost connection: the block still names no file
      const plain = await paperFor("Journal paper");
      const bytes = await unsavedVisit(plain);
      assertEq((await groups(plain))[0].properties.ink_url, "", "the block names no file yet");
      const { tab } = await restoredVisit(plain, bytes);
      assertNoProblems(tab);
      await tab.close();
      // The pagehide flush sends the block's PUT beside the upload, naming the file by its hash
      // ("Fences"); a PUT that landed while the upload did not leaves the block naming a file the
      // server lacks, which the entry's bytes are (inkStore lostUpload). That PUT, as it lands:
      const lostPaper = await paperFor("Lost upload paper");
      const lostBytes = await unsavedVisit(lostPaper);
      const lost = `/api/uploads/${createHash("sha256").update(lostBytes, "utf8").digest("hex").slice(0, 24)}.ink`;
      const block = (await groups(lostPaper))[0];
      await account.api(`/api/blocks/${block.id}`, { method: "PUT", body: { properties: { ...block.properties, ink_url: lost, ink_strokes: 1 } } });
      assert(!(await account.api(lost).then(() => true, () => false)), `the server lacks ${lost}`);
      const again = await restoredVisit(lostPaper, lostBytes);
      // Discard takes the restored stroke off again, and that is saved too
      await again.pill.getByRole("button", { name: "Discard", exact: true }).click();
      await until(async () => (await again.tab.$$('[data-page="1"] .inkLayer path')).length === 0, { what: "the restored stroke discarded" });
      await until(async () => (await savedIds(lostPaper)).length === 0, { what: "the discard saved", timeout: 15000 });
      // the block's missing file answers 404 when the page first loads it, a designed "no"
      assertNoProblems(again.tab, [`GET ${lost} -> 404`]);
    } finally { await jctx.close(); }
  });

  await step("ink: a browser with pens of its own is asked once, on the account's first pull, whether to keep them; Use this browser's pushes them; a later pull, and a browser on the defaults, never ask", async () => {
    const ask = "Use this browser's pens instead?";
    // B's own pens and another device's later edit, each its own first pen's colour
    const pens = (color) => DEFAULT_TOOLS.map((tool, i) => (i ? tool : { ...tool, color }));
    const [ownPens, laterPens] = [pens("#7c3aed"), pens("#ea580c")];
    const accountFirst = async () => ((await account.api("/api/prefs/profile")).value?.inkTools || [])[0]?.color;
    const localFirst = (p) => p.evaluate(() => JSON.parse(localStorage.getItem("gamma-ink-tools") || "[]")[0]?.color);
    // how many times this session asked: Settings → Help & diagnostics lists every status the pill showed
    const asked = async (p) => {
      await p.locator('[data-guide="header.account"]').click();
      await p.getByRole("button", { name: "Settings…", exact: true }).click();
      const dialog = p.getByRole("dialog", { name: "Settings", exact: true });
      await dialog.waitFor();
      await p.getByRole("navigation", { name: "Settings categories" }).getByRole("button", { name: "Help & diagnostics", exact: true }).click();
      await p.locator(".sysLogBox").waitFor();
      const n = await p.locator(".sysLogMsg").evaluateAll((els, text) => els.filter((el) => el.textContent === text).length, ask);
      await p.getByRole("button", { name: "Close settings", exact: true }).click();
      await dialog.waitFor({ state: "hidden" });
      return n;
    };
    // a paper of its own, whose strip A writes with
    const up = await account.upload("/api/uploads", makePdf([["Pens on the account"]]), "pens.pdf", "application/pdf");
    const paper = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Pens paper", source_url: up.source_url } });
    const library = `${server.base}/?ws=${account.ws}`;
    // a browser signed in to the account, with this localStorage before its first load
    const contexts = [];
    const browserWith = async (storageState) => {
      const c = await account.context(browser, storageState ? { storageState } : {});
      contexts.push(c);
      return c;
    };
    try {
      // A saves a pen list of its own to the account: its first pen made green on the strip
      const A = await openPage(await browserWith(), `${server.base}/?page=${paper.id}&ws=${account.ws}`);
      await waitForPdf(A, 1);
      await A.click("button[aria-label='Handwriting tools']");
      await A.waitForSelector(".pdfInkBar");
      await A.keyboard.press("1");
      await A.click(".pdfInkBar .inkToolBtn.modeActive");
      await A.waitForSelector(".pdfInkSub[data-ink-options='tool']");
      await A.click(".pdfInkSub button[aria-label='Green']");
      await until(async () => (await accountFirst()) === "#15803d", { what: "A's pens on the account" });
      // C, a browser on the defaults, takes the account's pens without a question
      const C = await openPage(await browserWith(), library);
      await until(async () => (await localFirst(C)) === "#15803d", { what: "C takes the account's pens" });
      // B had pens of its own when it signed in: the account's stand, and it is asked once (in the
      // library: a page opening as the pull lands says "Ready." on the same pill, in the question's place)
      const B = await openPage(await browserWith({ cookies: [], origins: [{ origin: server.base,
        localStorage: [{ name: "gamma-ink-tools", value: JSON.stringify(ownPens) }] }] }), library);
      const pill = B.locator(".statusPill", { hasText: ask });
      await pill.waitFor();
      assertEq(await localFirst(B), "#15803d", "the account's pens stand until B answers");
      const pushed = B.waitForResponse((r) => r.url().endsWith("/api/prefs/profile") && r.request().method() === "PATCH");
      await pill.getByRole("button", { name: "Use this browser's", exact: true }).click();
      await pushed;
      await until(async () => (await accountFirst()) === "#7c3aed", { what: "B's own pens on the account" });
      assertEq(await localFirst(B), "#7c3aed", "and in B");
      // A sees them after a reload
      await A.reload();
      await waitForPdf(A, 1);
      await A.click("button[aria-label='Handwriting tools']");
      await until(async () => (await A.$eval(".pdfInkBar .inkToolInk", (el) => getComputedStyle(el).backgroundColor)) === "rgb(124, 58, 237)",
        { what: "A's strip shows B's first pen" });
      // another device edits the account's pens: B's next pull takes the edit and does not ask again
      await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: laterPens } } });
      const pulled = B.waitForResponse((r) => r.url().endsWith("/api/prefs/profile") && r.request().method() === "GET");
      await B.evaluate(() => window.dispatchEvent(new Event("focus")));
      await pulled;
      await until(async () => (await localFirst(B)) === "#ea580c", { what: "B takes the later edit" });
      assertEq(await asked(B), 1, "B was asked once, on its first pull");
      // C's next pull replaces A's pens, which it took on its first, with the edit, and asks nothing: a later
      // pull never asks, in a session not asked yet too (B's once-a-session question cannot hide it)
      const pulledC = C.waitForResponse((r) => r.url().endsWith("/api/prefs/profile") && r.request().method() === "GET");
      await C.evaluate(() => window.dispatchEvent(new Event("focus")));
      await pulledC;
      await until(async () => (await localFirst(C)) === "#ea580c", { what: "C takes the later edit" });
      assertEq(await asked(C), 0, "a browser on the defaults is never asked, on its first pull or a later one");
      for (const p of [A, B, C]) assertNoProblems(p);
    } finally {
      for (const c of contexts) await c.close();
      // the account back on the default pens for the groups after this one
      await account.api("/api/prefs/profile", { method: "PATCH", body: { set: { inkTools: null } } }).catch(() => {});
    }
  });

  if (ctx) await ctx.close();
  return { inkPageId: pageId };
}
