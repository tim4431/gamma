// Audio recordings (docs/dev/recording.md): the mic button records through
// a MediaRecorder into a recording block whose segment uploads and serves as
// audio, the note typed and the stroke drawn meanwhile carry the moment they
// were made, the card plays the recording back with the stroke faded until
// its moment and the note shaded, a tap on the stroke seeks, and Escape
// closes the player; in a notebook view, Follow scrolls the viewer to the
// sheet being written on; with no viewer shown, the player docks over the
// notes. The browser has no microphone and no decoder
// here: an init script stands in for MediaRecorder and Audio. The server's
// side is backend/tests/test_recordings.py.
import { waitForPdf } from "./pdf.mjs";
import { assertMenuFrom, assertMenuRoles } from "../harness.mjs";
import { encodeStroke, newCanvasInk } from "../../../src/ink/ink.js";

async function drawLine(page, from, to) {
  await page.mouse.move(from[0], from[1]);
  await page.mouse.down();
  await page.mouse.move(to[0], to[1], { steps: 12 });
  await page.mouse.up();
}

// Runs in the page before the app: a MediaRecorder that delivers a chunk a
// second (the first a minimal WebM: EBML header, a Segment of unknown size,
// Info with a TimecodeScale, a Cluster), a microphone that is granted, and
// an Audio whose clock runs on its own since there is nothing to decode.
function fakeMedia() {
  const WEBM = [0x1a, 0x45, 0xdf, 0xa3, 0x84, 0x42, 0x86, 0x81, 0x01,
    0x18, 0x53, 0x80, 0x67, 0x01, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
    0x15, 0x49, 0xa9, 0x66, 0x87, 0x2a, 0xd7, 0xb1, 0x83, 0x0f, 0x42, 0x40,
    0x1f, 0x43, 0xb6, 0x75, 0x83, 0xe7, 0x81, 0x00];
  const chunk = (bytes) => ({ data: new Blob([new Uint8Array(bytes)], { type: "audio/webm" }) });
  class FakeRecorder {
    constructor() { this.mimeType = "audio/webm;codecs=opus"; this.state = "inactive"; }
    static isTypeSupported() { return true; }
    start(slice) {
      this.state = "recording";
      let n = 0;
      setTimeout(() => this.onstart?.(), 0);
      this.timer = setInterval(() => this.ondataavailable?.(chunk(n++ ? [0xa3, 0x81, 0x00] : WEBM)), slice || 1000);
    }
    stop() {
      clearInterval(this.timer);
      this.state = "inactive";
      this.ondataavailable?.(chunk([0xa3, 0x81, 0x00]));
      setTimeout(() => this.onstop?.(), 0);
    }
  }
  window.MediaRecorder = FakeRecorder;
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }], getAudioTracks: () => [] }) },
  });
  class FakeAudio extends EventTarget {
    constructor() { super(); this.paused = true; this.t = 0; this.at = 0; this.playbackRate = 1; this.dataset = {}; this.preservesPitch = true; }
    get currentTime() { return this.paused ? this.t : this.t + ((performance.now() - this.at) / 1000) * this.playbackRate; }
    set currentTime(v) { this.t = v; this.at = performance.now(); }
    play() { this.t = this.currentTime; this.at = performance.now(); this.paused = false; return Promise.resolve(); }
    pause() { this.t = this.currentTime; this.paused = true; }
    removeAttribute() {}
    load() {}
  }
  window.Audio = FakeAudio;
}

export async function recordingScenarios({ server, browser, alice, makePdf, step, until, sleep, assert, assertEq, assertNoProblems, openPage, flags }) {
  let ctx, page, pageId, box;
  const account = alice;
  const children = async () => (await account.api(`/api/blocks/${pageId}/subtree`)).block.children || [];
  const recordingOnServer = async () => (await children()).find((b) => b.properties?.recording) || null;

  await step("recording: the mic records into a recording block; a note typed and a stroke drawn meanwhile carry their moment", async () => {
    const pdf = makePdf([["A lecture handout", "with some text"]]);
    const up = await account.upload("/api/uploads", pdf, "lecture.pdf", "application/pdf");
    const created = await account.api(`/api/blocks/by-doc/${up.doc_id}`, { method: "POST", body: { default_title: "Lecture", source_url: up.source_url } });
    pageId = created.id;
    ctx = await account.context(browser);
    await ctx.addInitScript(fakeMedia);
    page = await openPage(ctx, `${server.base}/?page=${pageId}&ws=${account.ws}`);
    await waitForPdf(page, 1);
    await page.click("button[aria-label='Record audio']");
    await page.waitForSelector(".recordingPill.live", { timeout: 5000 });
    await page.waitForSelector(".recordingCard.live", { timeout: 5000 });
    assert(await page.$("button[aria-label='Stop recording'].modeActive"), "the mic button shows the recording");
    const started = await until(async () => (await recordingOnServer())?.properties.recording.started || null, { what: "the recording block on the server" });
    // a note typed while recording
    await page.click(".notesTail");
    await page.keyboard.type("key idea");
    // a stroke drawn while recording
    await page.click("button[aria-label='Handwriting tools']");
    await page.waitForSelector(".pdfInkBar");
    box = await page.locator('[data-page="1"]').boundingBox();
    await drawLine(page, [box.x + 100, box.y + 150], [box.x + 250, box.y + 170]);
    await until(async () => (await page.$$('[data-page="1"] .inkLayer path')).length === 1, { what: "a stroke path" });
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".pdfInkBar")), { what: "strip closed" });
    await sleep(1200);
    await page.click("button[aria-label='Stop recording']");
    const rec = await until(async () => {
      const b = await recordingOnServer();
      return b && b.properties.recording.state === "done" && b.properties.recording.segments.length === 1 ? b : null;
    }, { what: "a finished recording with one uploaded segment", timeout: 15000 });
    const seg = rec.properties.recording.segments[0];
    assert(seg.url.endsWith(".webm") && seg.dur >= 1000 && seg.bytes > 0, `the segment is a webm with a measured length: ${JSON.stringify(seg)}`);
    assertEq(rec.properties.recording.dur, seg.dur, "the total is the segments' sum");
    assertEq(rec.properties.t0, started, "the block is stamped with the moment it began");
    assert(!("by" in rec.properties.recording), "the lease is gone once the recording is done");
    const served = await page.evaluate(async (url) => {
      const r = await fetch(url);
      return [r.status, r.headers.get("content-type") || "", r.headers.get("content-disposition") || ""];
    }, seg.url);
    assertEq(served[0], 200, "the audio file is served");
    assert(served[1].startsWith("audio/webm"), `served as audio: ${served[1]}`);
    assert(!served[2].startsWith("attachment"), "served inline, for an <audio> element");
    const note = (await children()).find((b) => b.content === "key idea");
    assert(Number.isFinite(note?.properties?.t0) && note.properties.t0 >= started, "the note carries the moment it was written, inside the recording");
    const ink = (await children()).find((b) => b.properties?.ink_url);
    const file = await account.api(ink.properties.ink_url);
    assert(file.strokes[0].t0 >= started, "the stroke's own t0 falls inside the recording");
    assert(!(await page.$(".recordingPill")), "the pill is gone");
    await page.waitForSelector(".recordingCard button[aria-label='Play recording']", { timeout: 5000 });
    // the card's menu: Transcribe (an editor) and Download
    await page.hover(".recordingCard");
    await page.click(".recordingCard .recMenuBtn");
    await page.waitForSelector(".ctxMenu", { timeout: 5000 });
    const items = await page.locator(".ctxMenu .ctxMenuItem").evaluateAll((els) => els.map((el) => el.textContent.trim()));
    assertEq(items.join(" | "), "Transcribe | Download audio", "the recording's menu");
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".ctxMenu")), { what: "menu closed" });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/recording-done.png` });
    assertNoProblems(page);
  });

  await step("recording: Play shades the note and fades the stroke until their moment; a tap on the stroke seeks; Escape closes", async () => {
    await page.click(".recordingCard button[aria-label='Play recording']");
    await page.waitForSelector(".replayBar", { timeout: 5000 });
    await page.waitForSelector(".blockRow.replayPending", { timeout: 5000 });
    assert(await page.$('[data-page="1"] .inkLayer .inkPending path'), "the stroke not yet reached is faded (Spotlight)");
    const pickMode = async (name) => {
      await page.click(".replayBar button[data-role='mode']");
      await page.locator(".ctxMenu .ctxMenuItem", { hasText: name }).click();
      await until(async () => !(await page.$(".ctxMenu")), { what: "mode menu closed" });
    };
    await pickMode("Reveal");
    await until(async () => !(await page.$('[data-page="1"] .inkLayer .inkPending')), { what: "no faded strokes in Reveal" });
    await pickMode("Spotlight");
    // the fake audio's clock runs: the recording is a few seconds long, so
    // the note and the stroke are reached, and the player holds at the end
    await until(async () => (await page.$$(".blockRow.replayReached")).length >= 1, { what: "the note reached", timeout: 15000 });
    await until(async () => !(await page.$('[data-page="1"] .inkLayer .inkPending')), { what: "the stroke reached", timeout: 15000 });
    await until(async () => (await page.$("button[aria-label='Play (Space)']")) !== null, { what: "the player holds at the end", timeout: 15000 });
    // The bar's tooltips stay off its open menus, with no gap between the
    // two: the press itself swaps the trigger's tooltip for the menu (the
    // click that follows on release would leave the press's length of
    // nothing in between), keeps the tooltip away while the pointer stays on
    // the button, and the menu's own rows raise none of their own over it.
    // The bar lifted well off the window's bottom (a layout with a window
    // docked under the viewer), where a tip would otherwise go below: the
    // bar's tips stay above, on the side its menus open.
    const lift = await page.addStyleTag({ content: ".replayBar { bottom: 160px !important; }" });
    await page.hover(".replayBar .replayPlay");
    const modeBtn = await page.locator(".replayBar button[data-role='mode']").boundingBox();
    const onMode = (dx = 0, dy = 0) => page.mouse.move(modeBtn.x + modeBtn.width / 2 + dx, modeBtn.y + modeBtn.height / 2 + dy);
    await onMode();
    await until(async () => !!(await page.$(".inkTooltip")), { what: "the mode button's tooltip" });
    const tip = await page.locator(".inkTooltip").boundingBox();
    assert(tip.y + tip.height <= modeBtn.y, `the tip sits above the button, where the menu opens: tip ${JSON.stringify(tip)}, button ${JSON.stringify(modeBtn)}`);
    await page.mouse.down();
    const held = await page.evaluate(() => ({ tooltip: !!document.querySelector(".inkTooltip"), menu: !!document.querySelector(".ctxMenu") }));
    assert(held.menu && !held.tooltip, `the press swaps the tooltip for the menu at once: ${JSON.stringify(held)}`);
    await page.mouse.up();
    await page.waitForSelector(".ctxMenu .ctxMenuItem", { timeout: 5000 });
    // up from the bar, by the button's left edge
    await assertMenuFrom(page.locator(".ctxMenu"), page.locator(".replayBar button[data-role='mode']"), "above", "the mode menu");
    // one menu, named by its button, the modes radios and the current one checked
    const modeName = await page.getAttribute(".replayBar button[data-role='mode']", "aria-label");
    const modes = await assertMenuRoles(page.getByRole("menu", { name: modeName, exact: true }), "the mode menu");
    assert(modes.every((m) => m.role === "menuitemradio") && modes.filter((m) => m.checked === "true").length === 1,
      `the modes are radios, one checked: ${JSON.stringify(modes)}`);
    await onMode(9, 0);                   // the smallest move a hand makes, off the glyph
    await sleep(500);
    assert(!(await page.$(".inkTooltip")), "the press keeps the trigger's tooltip away while the pointer stays on it");
    for (const row of await page.locator(".ctxMenu .ctxMenuItem").all()) await row.hover();
    await sleep(500);
    assert(!(await page.$(".inkTooltip")), "no tooltip of the bar's covers the open mode menu");
    await onMode();
    await page.mouse.down(); await page.mouse.up();
    await until(async () => !(await page.$(".ctxMenu")), { what: "mode menu closed by a second press" });
    // A press on the speed button while the mode menu stands open swaps the
    // two: one menu, the speed's, and its button the one expanded. A second
    // press on the speed button closes it.
    await onMode();
    await page.mouse.down(); await page.mouse.up();
    await page.waitForSelector(".ctxMenu .ctxMenuItem", { timeout: 5000 });
    await page.click(".replayBar .replayRate");
    await until(async () => (await page.locator(".ctxMenu .ctxMenuItem").allTextContents()).join(" ") === "0.7× 1× 1.25× 1.5× 2×",
      { what: "the speed menu in the mode menu's place" });
    assertEq(await page.locator(".ctxMenu").count(), 1, "one menu at a time");
    assertEq(await page.getAttribute(".replayBar button[data-role='mode']", "aria-expanded"), "false", "the mode button no longer expanded");
    assertEq(await page.getAttribute(".replayBar .replayRate", "aria-expanded"), "true", "the speed button expanded");
    await assertMenuFrom(page.locator(".ctxMenu"), page.locator(".replayBar .replayRate"), "above", "the speed menu, from its own button");
    const speedName = await page.getAttribute(".replayBar .replayRate", "aria-label");
    const speeds = await assertMenuRoles(page.getByRole("menu", { name: speedName, exact: true }), "the speed menu");
    assert(speeds.every((s) => s.role === "menuitemradio") && speeds.filter((s) => s.checked === "true").length === 1,
      `the speeds are radios, one checked: ${JSON.stringify(speeds)}`);
    await page.click(".replayBar .replayRate");
    await until(async () => !(await page.$(".ctxMenu")), { what: "speed menu closed by a second press" });
    // Enter (a click with no press behind it) opens it too; Escape closes
    // the menu and leaves the player, the focus still on the button.
    await page.focus(".replayBar button[data-role='mode']");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".ctxMenu .ctxMenuItem", { timeout: 5000 });
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".ctxMenu")), { what: "mode menu closed by Escape" });
    assert(await page.$(".replayBar"), "Escape on the open menu leaves the player");
    assert(await page.$eval(".replayBar button[data-role='mode']", (el) => el === document.activeElement), "the focus stays on the mode button");
    await lift.evaluate((el) => el.remove());
    // back to the start, then a tap on the stroke plays from just before it was written
    await page.$eval(".replayScrub input[type='range']", (el) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, "0");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await until(async () => (await page.textContent(".replayBar .replayTime")) === "0:00", { what: "scrubbed to the start" });
    // Follow scrolled the page to the writing meanwhile: measure it again.
    box = await page.locator('[data-page="1"]').boundingBox();
    await page.mouse.click(box.x + 175, box.y + 160);
    await until(async () => await page.$("button[aria-label='Pause (Space)']"), { what: "playing after the tap" });
    await until(async () => !(await page.$('[data-page="1"] .inkLayer .inkPending')), { what: "the tapped stroke is reached" });
    if (flags.keep) await page.screenshot({ path: `${server.dir}/recording-player.png` });
    await page.keyboard.press("Escape");
    await until(async () => !(await page.$(".replayBar")), { what: "player closed by Escape" });
    assert(!(await page.$(".blockRow.replayPending")), "no row stays shaded once the player closes");
    assertNoProblems(page);
  });

  await step("recording: in the notebook view, Follow scrolls the viewer to the sheet being written on", async () => {
    // Follow is App's showTick, one function for the app's life: a group is shown through the page as it
    // is when its mark is reached, not as the app's first render had it (no notebook view). A notebook
    // made through the API: two sheets, a recording four seconds long and, on the second sheet, a stroke
    // written a second and a half into it.
    const audio = await account.upload("/api/upload-file", Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0x84, 0x42, 0x86, 0x81, 0x01]), "follow.webm", "audio/webm");
    const started = Date.now() - 60_000;
    const stroke = encodeStroke({ id: "followed", color: "#1f1f1f", size: 2, ch: "xyt", t0: started + 1500,
      samples: Array.from({ length: 13 }, (_, i) => ({ x: 80 + i * 10, y: 120, t: i * 40 })) });
    const notebook = await account.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Followed notebook" } });
    const paper = { width: 595.28, height: 841.89, color: "#ffffff", pattern: "blank", spacing: 24, line: "#c8d1dc" };
    const sheets = [];
    for (let i = 0; i < 2; i++) {
      sheets.push(await account.api("/api/blocks", { method: "POST", body: { parent_id: notebook.id, content: "", properties: { sheet: paper, collapsed: true } } }));
    }
    const sheetInk = await account.api("/api/upload-ink", { method: "POST", body: { ...newCanvasInk(paper.width, paper.height), strokes: [stroke] } });
    await account.api("/api/blocks", { method: "POST", body: { parent_id: sheets[1].id, content: "", properties: { ink_url: sheetInk.url, ink_strokes: 1 } } });
    await account.api("/api/blocks", { method: "POST", body: { parent_id: notebook.id, content: "Followed audio",
      properties: { t0: started, recording: { started, state: "done", dur: 4000,
        segments: [{ url: audio.url, started, dur: 4000, bytes: 9, mime: "audio/webm" }] } } } });
    const fctx = await account.context(browser);
    await fctx.addInitScript(fakeMedia);
    try {
      // the notebook view, at its first sheet: Follow brings the second into view
      const npage = await openPage(fctx, `${server.base}/?page=${notebook.id}&ws=${account.ws}`);
      await npage.locator(".noteSheetBar button[aria-label='Notebook view']").first().click();
      await npage.waitForSelector(".nbViewer .nbSheet", { timeout: 15000 });
      const secondInView = () => npage.evaluate((id) => {
        const viewer = document.querySelector(".nbViewer"), sheet = viewer?.querySelector(`.nbSheet[data-sheet-id="${id}"]`);
        if (!sheet) return false;
        const v = viewer.getBoundingClientRect(), s = sheet.getBoundingClientRect();
        return s.top < v.bottom - 100 && s.bottom > v.top + 100;
      }, sheets[1].id);
      assert(!(await secondInView()), "the second sheet starts out of view");
      await npage.click(".recordingCard button[aria-label='Play recording']");
      await npage.waitForSelector(".replayBar", { timeout: 5000 });
      await until(secondInView, { what: "Follow scrolls the notebook view to the second sheet", timeout: 10000 });
      await npage.keyboard.press("Escape");
      await until(async () => !(await npage.$(".replayBar")), { what: "the player closed" });
      assertNoProblems(npage);
    } finally { await fctx.close(); }
  });

  await step("recording: with no viewer shown, Play docks the player over the notes, and Escape closes it", async () => {
    // A page of notes with no paper and no notebook view: the player has no viewer to sit in, so it docks
    // in the notes' list (App's notesReplayDock), stuck to its bottom edge as the list scrolls.
    const audio = await account.upload("/api/upload-file", Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0x84, 0x42, 0x86, 0x81, 0x01]), "notes.webm", "audio/webm");
    const started = Date.now() - 60_000;
    const notes = await account.api("/api/blocks", { method: "POST", body: { parent_id: "root", content: "Notes with a recording" } });
    await account.api("/api/blocks", { method: "POST", body: { parent_id: notes.id, content: "Notes audio",
      properties: { t0: started, recording: { started, state: "done", dur: 4000,
        segments: [{ url: audio.url, started, dur: 4000, bytes: 9, mime: "audio/webm" }] } } } });
    const nctx = await account.context(browser);
    await nctx.addInitScript(fakeMedia);
    try {
      const npage = await openPage(nctx, `${server.base}/?page=${notes.id}&ws=${account.ws}`);
      await npage.click(".recordingCard button[aria-label='Play recording']");
      const bar = npage.locator(".blockList .notesReplayDock .replayBar");
      await bar.waitFor({ timeout: 5000 });
      assertEq(await npage.locator(".replayBar").count(), 1, "one player, over the notes");
      const [list, shown] = [await npage.locator(".blockList").boundingBox(), await bar.boundingBox()];
      assert(shown.width > 0 && shown.y >= list.y && shown.y + shown.height <= list.y + list.height + 1,
        `the player in view, within the notes' list: bar ${JSON.stringify(shown)}, list ${JSON.stringify(list)}`);
      await npage.keyboard.press("Escape");
      await until(async () => !(await npage.$(".replayBar")), { what: "the player closed" });
      assertNoProblems(npage);
    } finally { await nctx.close(); }
  });
}
