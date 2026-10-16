// node --test tests/  (from frontend/) — a recording's rules (recording/recording.js)
// and the WebM duration patch (recording/webm.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import { extensionFor, heldElsewhere, isRecording, isRecordingBlock, normalizeRecording, recordingAt } from "../src/recording/recording.js";
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
