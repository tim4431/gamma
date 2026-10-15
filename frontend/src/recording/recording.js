// A recording's shape and rules (docs/dev/recording.md), pure. A recording
// is a block whose `recording` property holds the audio: when it began,
// its segments (each an uploaded file with the wall-clock moment its audio
// began and its measured length) and, while a tab holds the microphone,
// that tab's lease. The block's content is the title. The server's rules
// are gamma/recording.py; tests/recording.test.mjs.
import { fmtDate } from "../shared/i18n/i18n.js";

// Capture rolls over to a new segment this often, so a crash loses at most
// this much and each file stays small enough to upload in one request.
export const SEGMENT_MS = 5 * 60 * 1000;
// The lease is renewed whenever the recording tab writes the block (a
// segment every SEGMENT_MS, a pause, a resume); one older than this is a
// tab that is gone, and another may record.
export const LEASE_STALE_MS = SEGMENT_MS + 60 * 1000;

export const isRecordingBlock = (b) => isRecording(b?.properties);
export const isRecording = (props) => !!props && typeof props.recording === "object" && props.recording !== null && !Array.isArray(props.recording);

// The property as the code reads it: integers, segments in order, the
// total summed from them.
export function normalizeRecording(raw) {
  const rec = raw && typeof raw === "object" ? raw : {};
  const segments = (Array.isArray(rec.segments) ? rec.segments : [])
    .filter((s) => s && typeof s.url === "string" && Number.isFinite(s.started) && Number.isFinite(s.dur) && s.dur >= 0)
    .map((s) => ({ url: s.url, started: Math.round(s.started), dur: Math.round(s.dur), bytes: Math.max(0, Math.round(s.bytes || 0)),
      mime: typeof s.mime === "string" ? s.mime : "" }))
    .sort((a, b) => a.started - b.started);
  const started = Number.isFinite(rec.started) ? Math.round(rec.started) : segments[0]?.started ?? null;
  const state = ["recording", "paused", "done"].includes(rec.state) ? rec.state : "done";
  const out = { started, segments, state, dur: segments.reduce((sum, s) => sum + s.dur, 0) };
  if (state !== "done") {
    out.by = typeof rec.by === "string" ? rec.by : "";
    out.at = Number.isFinite(rec.at) ? Math.round(rec.at) : 0;
  }
  return out;
}

// Whether another tab is recording this one right now: its lease is live.
export function heldElsewhere(rec, tabId, now = Date.now()) {
  return !!rec && rec.state !== "done" && rec.by !== tabId && now - (rec.at || 0) < LEASE_STALE_MS;
}

// A recording's default title: the moment it began, in the account's
// date style.
export function defaultTitle(started, t) {
  return t("Recording · {when}", { when: fmtDate(new Date(started || Date.now()), { dateStyle: "medium", timeStyle: "short" }) });
}

// The recording whose span covers a wall-clock moment, among a page's
// recording blocks ([{id, properties}]), or null.
export function recordingAt(blocks, wall) {
  for (const b of blocks) {
    const rec = normalizeRecording(b.properties.recording);
    const first = rec.segments[0], last = rec.segments[rec.segments.length - 1];
    if (first && wall >= first.started && wall <= last.started + last.dur) return b;
  }
  return null;
}

// The stored file's extension for a MediaRecorder's container type.
export function extensionFor(mime) {
  const type = String(mime || "").toLowerCase();
  if (type.includes("webm")) return ".webm";
  if (type.includes("mp4") || type.includes("aac")) return ".m4a";
  if (type.includes("ogg") || type.includes("opus")) return ".ogg";
  return ".webm";
}
