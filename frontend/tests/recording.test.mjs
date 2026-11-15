// node --test tests/  (from frontend/) — a recording's rules (recording/recording.js),
// the WebM duration patch (recording/webm.js), and the WAV the PCM capture
// writes (recording/wav.js, recorder.js createPcmSegmenter).
import assert from "node:assert/strict";
import { test } from "node:test";
import { createPcmSegmenter } from "../src/recording/recorder.js";
import { extensionFor, heldElsewhere, isRecording, isRecordingBlock, normalizeRecording, recordingAt } from "../src/recording/recording.js";
import { PCM_RATE, createDownsampler, wavHeader, withWavSizes } from "../src/recording/wav.js";
import { readDuration, withDuration } from "../src/recording/webm.js";

test("a recording block is one whose recording property is an object", () => {
  assert.ok(isRecording({ recording: { started: 1, segments: [] } }));
  assert.ok(!isRecording({ recording: null }));
  assert.ok(!isRecording({ recording: [] }));
  assert.ok(!isRecording({}));
  assert.ok(isRecordingBlock({ properties: { recording: {} } }));
  assert.ok(!isRecordingBlock(null));
});

test("normalizeRecording: integers, segments in order, the total summed, the lease only while held", () => {
  const rec = normalizeRecording({
    started: 1000.4, state: "paused", by: "tab1", at: 2000.6,
    segments: [
      { url: "/api/uploads/b.webm", started: 5000, dur: 10.2, bytes: 3, mime: "audio/webm" },
      { url: "/api/uploads/a.webm", started: 1000, dur: 20, bytes: 7 },
      { url: 5, started: 1, dur: 1 },                 // not a file: dropped
      { url: "/api/uploads/x.webm", started: 1, dur: -1 },  // no length: dropped
    ],
  });
  assert.deepEqual(rec, {
    started: 1000, state: "paused", by: "tab1", at: 2001, dur: 30,
    segments: [
      { url: "/api/uploads/a.webm", started: 1000, dur: 20, bytes: 7, mime: "" },
      { url: "/api/uploads/b.webm", started: 5000, dur: 10, bytes: 3, mime: "audio/webm" },
    ],
  });
  const done = normalizeRecording({ segments: [{ url: "/api/uploads/a.webm", started: 42, dur: 1 }] });
  assert.equal(done.state, "done");
  assert.equal(done.started, 42, "a recording without a start begins with its first segment");
  assert.ok(!("by" in done));
  assert.deepEqual(normalizeRecording(null), { started: null, segments: [], state: "done", dur: 0 });
});

test("heldElsewhere: another tab's live lease, and nothing else", () => {
  const now = 100_000;
  assert.ok(heldElsewhere({ state: "recording", by: "other", at: now - 1000 }, "me", now));
  assert.ok(!heldElsewhere({ state: "recording", by: "me", at: now - 1000 }, "me", now), "my own");
  assert.ok(!heldElsewhere({ state: "recording", by: "other", at: now - 361_000 }, "me", now), "stale");
  assert.ok(!heldElsewhere({ state: "done", by: "other", at: now }, "me", now));
});

test("recordingAt finds the recording whose span covers a moment", () => {
  const blocks = [
    { id: "r1", properties: { recording: { started: 1000, segments: [{ url: "/api/uploads/a.webm", started: 1000, dur: 500 }] } } },
    { id: "r2", properties: { recording: { started: 5000, segments: [{ url: "/api/uploads/b.webm", started: 5000, dur: 500 }, { url: "/api/uploads/c.webm", started: 6000, dur: 500 }] } } },
  ];
  assert.equal(recordingAt(blocks, 1200)?.id, "r1");
  assert.equal(recordingAt(blocks, 5800)?.id, "r2", "a moment in the pause belongs to the recording");
  assert.equal(recordingAt(blocks, 3000), null);
});

test("extensionFor the recorder's container", () => {
  assert.equal(extensionFor("audio/webm;codecs=opus"), ".webm");
  assert.equal(extensionFor("audio/mp4"), ".m4a");
  assert.equal(extensionFor("audio/ogg;codecs=opus"), ".ogg");
  assert.equal(extensionFor("audio/wav"), ".wav");
  assert.equal(extensionFor(""), ".webm");
});

// A WebM as MediaRecorder writes one: an EBML header, a Segment of
// unknown size, an Info with a TimecodeScale and no Duration, a Cluster.
function webm({ duration = null } = {}) {
  const header = [0x1a, 0x45, 0xdf, 0xa3, 0x84, 0x42, 0x86, 0x81, 0x01];           // EBML { EBMLVersion 1 }
  const scale = [0x2a, 0xd7, 0xb1, 0x83, 0x0f, 0x42, 0x40];                           // TimecodeScale 1 000 000
  const dur = duration == null ? [] : [0x44, 0x89, 0x88, ...new Uint8Array(new Float64Array([duration]).buffer).reverse()];
  const info = [0x15, 0x49, 0xa9, 0x66, 0x80 | (scale.length + dur.length), ...scale, ...dur];
  const cluster = [0x1f, 0x43, 0xb6, 0x75, 0x83, 0xe7, 0x81, 0x00];                  // Cluster { Timecode 0 }
  const segment = [0x18, 0x53, 0x80, 0x67, 0x01, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, ...info, ...cluster];
  return new Uint8Array([...header, ...segment]);
}

test("withDuration writes the length into a WebM that has none, and over one that has", () => {
  const bare = webm();
  assert.equal(readDuration(bare), null);
  const fixed = withDuration(bare, 12_345);
  assert.notEqual(fixed, bare);
  assert.equal(readDuration(fixed), 12_345);
  assert.equal(fixed.length, bare.length + 11 + 7, "an 8-byte size vint in place of the 1-byte one, plus the Duration element");
  // the cluster follows intact
  assert.deepEqual([...fixed.slice(-8)], [0x1f, 0x43, 0xb6, 0x75, 0x83, 0xe7, 0x81, 0x00]);
  const had = webm({ duration: 5 });
  assert.equal(readDuration(had), 5);
  const over = withDuration(had, 999);
  assert.equal(over, had, "in place");
  assert.equal(readDuration(had), 999);
  const junk = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  assert.equal(withDuration(junk, 1), junk, "not a WebM: untouched");
  assert.equal(readDuration(junk), null);
});

const ascii = (bytes, at, n) => String.fromCharCode(...bytes.subarray(at, at + n));
const u32 = (bytes, at) => new DataView(bytes.buffer, bytes.byteOffset).getUint32(at, true);
const u16 = (bytes, at) => new DataView(bytes.buffer, bytes.byteOffset).getUint16(at, true);

test("wavHeader: 16-bit mono PCM at the rate, its sizes known or to the end", () => {
  const h = wavHeader(16000, 3200);
  assert.equal(h.length, 44);
  assert.equal(ascii(h, 0, 4), "RIFF");
  assert.equal(ascii(h, 8, 8), "WAVEfmt ");
  assert.equal(ascii(h, 36, 4), "data");
  assert.deepEqual([u32(h, 4), u16(h, 20), u16(h, 22), u32(h, 24), u32(h, 28), u16(h, 32), u16(h, 34), u32(h, 40)],
    [36 + 3200, 1, 1, 16000, 32000, 2, 16, 3200]);
  const open = wavHeader(16000, null);
  assert.equal(u32(open, 4), 0xffffffff);
  assert.equal(u32(open, 40), 0xffffffff);
});

test("withWavSizes makes a crashed segment's sizes true to its length, and leaves other bytes alone", () => {
  const file = new Uint8Array(44 + 101); // 50 samples and a stray byte
  file.set(wavHeader(16000, null));
  withWavSizes(file);
  assert.equal(u32(file, 40), 100, "whole samples only");
  assert.equal(u32(file, 4), 136);
  const other = new Uint8Array([1, 2, 3]);
  assert.equal(withWavSizes(other), other);
  assert.deepEqual([...other], [1, 2, 3]);
});

test("createDownsampler: the rate's share of samples, a steady level kept, batches joined without a seam", () => {
  const down = createDownsampler(48000, 16000);
  const out = down(new Float32Array(4800).fill(0.5));
  assert.equal(out.length, 1600);
  assert.ok(out.every((v) => v === Math.round(0.5 * 32767)), "a steady input stays at its level");
  // 44.1 kHz in uneven batches: the same samples as in one go
  const whole = createDownsampler(44100, 16000)(Float32Array.from({ length: 4410 }, (_, i) => Math.sin(i / 7)));
  const split = createDownsampler(44100, 16000);
  const parts = [];
  for (let at = 0; at < 4410; at += 333) parts.push(...split(Float32Array.from({ length: Math.min(333, 4410 - at) }, (_, i) => Math.sin((at + i) / 7))));
  assert.equal(whole.length, 1600);
  assert.equal(parts.length, whole.length);
  assert.ok(parts.every((v, i) => Math.abs(v - whole[i]) <= 1), "the split matches the whole");
  // clipped at full scale, and no faster than the rate it was given
  assert.deepEqual([...createDownsampler(16000, 16000)(Float32Array.from([2, -2, 0]))], [32767, -32768, 0]);
  assert.throws(() => createDownsampler(8000, 16000));
});

test("createPcmSegmenter: a chunk a second with the header first, segments rolled over at the sample, a pause ends one", async () => {
  let clock = 100_000;
  const chunks = [], segments = [];
  const s = createPcmSegmenter({ segmentMs: 2500, now: () => clock, onChunk: (seg, n, blob) => chunks.push({ seg, n, blob }), onSegment: (x) => segments.push(x) });
  const second = () => new Float32Array(48000).fill(0.25); // a second at 48 kHz
  for (let i = 0; i < 6; i++) { clock += 1000; s.push(second(), 48000); }
  // 6 s of audio in 2.5 s segments: two finished, the third holding 1 s
  assert.equal(segments.length, 2);
  assert.deepEqual(segments.map((x) => [x.seg, x.started, x.dur, x.mime]), [[0, 100_000, 2500, "audio/wav"], [1, 102_500, 2500, "audio/wav"]],
    "the first began a batch before it came; the next where the last ended");
  const first = new Uint8Array(await segments[0].blob.arrayBuffer());
  assert.equal(first.length, 44 + 2500 * 16 * 2);
  assert.equal(u32(first, 40), 2500 * 32, "the finished segment's header has its sizes");
  assert.equal(new DataView(first.buffer).getInt16(44, true), Math.round(0.25 * 32767));
  // chunks: out once a second's samples wait (the rollover's half second
  // rides with the next batch), the segment's rest at its end; the first
  // of each segment carries the header
  assert.deepEqual(chunks.map((c) => [c.seg, c.n, c.blob.size]),
    [[0, 0, 44 + 32000], [0, 1, 32000], [0, 2, 16000], [1, 0, 44 + 48000], [1, 1, 32000], [2, 0, 44 + 32000]]);
  assert.equal(u32(new Uint8Array(await chunks[0].blob.arrayBuffer()), 40), 0xffffffff, "a chunk's header says to the end");
  assert.equal(s.recordedMs(), 6000);
  // a pause: the third ends with what it has; the next push after it starts afresh
  assert.equal(s.cut(), 105_000 + 1000);
  assert.deepEqual(segments.map((x) => [x.seg, x.started, x.dur]).slice(2), [[2, 105_000, 1000]]);
  assert.equal(s.cut(), null, "nothing in progress");
  clock = 200_000;
  s.push(new Float32Array(4800), 48000);
  s.cut();
  assert.deepEqual(segments.map((x) => [x.seg, x.started, x.dur]).at(-1), [3, 199_900, 100]);
});

test("createPcmSegmenter with no segment length keeps one file, at a rate below PCM_RATE kept as it is", () => {
  const segments = [];
  const s = createPcmSegmenter({ segmentMs: 0, now: () => 0, onSegment: (x) => segments.push(x) });
  for (let i = 0; i < 4; i++) s.push(new Float32Array(8000), 8000);
  s.cut();
  assert.equal(segments.length, 1);
  assert.equal(segments[0].dur, 4000);
  assert.ok(PCM_RATE > 8000);
});

