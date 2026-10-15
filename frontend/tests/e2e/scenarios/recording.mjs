// Audio recordings (docs/dev/recording.md): the mic button records through
// a MediaRecorder into a recording block whose segment uploads and serves as
// audio, the note typed and the stroke drawn meanwhile carry the moment they
// were made, the card plays the recording back with the stroke faded until
// its moment and the note shaded, a tap on the stroke seeks, and Escape
// closes the player. The browser has no microphone and no decoder here:
// an init script stands in for MediaRecorder and Audio. The server's side
// is backend/tests/test_recordings.py.
import { waitForPdf } from "./pdf.mjs";

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
}
