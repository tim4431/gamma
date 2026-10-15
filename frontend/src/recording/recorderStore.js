// The recording in progress in this tab (docs/dev/recording.md "Capturing
// audio"): one at a time, as module state with listeners like
// replay/playback.js, so the pill that shows its timer and level re-renders
// on its own and App only hears of the coarse changes. The store runs the
// recorder (recording/recorder.js), spools its chunks (recording/spool.js),
// uploads each finished segment and writes the recording block's property
// through PUT /api/blocks/{id} — a server-side writer, so the change fans
// out over the page socket and reaches every tab and device like a remote
// op, the block growing as the lecture goes on. The block itself is
// inserted through the tree once, by App's `insertBlock`.
//
// state: null, or {blockId, pageId, status: "asking" | "recording" |
//   "paused" | "finishing", started, segments, pendingUploads, elapsed, level}
import { API, apiJson, getCurrentWorkspace } from "../shared/lib/utils.js";
import { postFile } from "../transfers/FileChip.jsx";
import { CLIENT_ID } from "../collaboration/usePageCollab.js";
import { t } from "../shared/i18n/i18n.js";
import { fmtClock } from "../replay/clock.js";
import { defaultTitle, extensionFor, normalizeRecording } from "./recording.js";
import { MIC_CONSTRAINTS, canRecord, createRecorder } from "./recorder.js";
import * as spool from "./spool.js";

let state = null;
let recorder = null;
let timer = 0;
let settings = { setStatus: () => {}, leadIn: () => 3000, insertBlock: null, onDone: null };
const listeners = new Set();
const emit = () => { for (const fn of listeners) fn(); };
// The coarse snapshot App reads: a new object only when what it shows changes.
let coarse = null;

export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function now() { return state; }
export function coarseNow() { return coarse; }

function set(patch) {
  state = state ? { ...state, ...patch } : null;
  const next = state ? { blockId: state.blockId, pageId: state.pageId, status: state.status, segments: state.segments.length,
    pendingUploads: state.pendingUploads } : null;
  if (JSON.stringify(next) !== JSON.stringify(coarse)) coarse = next;
  emit();
}

// App's side: how to say something, the lead-in a bookmark is backdated
// by, how a block is inserted into the open page, and what to do with a
// recording once it is saved (its transcription).
export function configure(next) { settings = { ...settings, ...next }; }

// What the block holds: this tab's lease while it records.
function property(status) {
  const s = state;
  const live = status !== "done";
  return {
    recording: {
      started: s.started, segments: s.segments, state: status, dur: s.segments.reduce((sum, seg) => sum + seg.dur, 0),
      ...(live ? { by: CLIENT_ID, at: Date.now() } : {}),
    },
  };
}

async function writeBlock(status, tries = 0) {
  if (!state) return;
  try {
    await apiJson(`${API}/blocks/${state.blockId}`, { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ properties: property(status) }) });
  } catch (err) {
    // The block's insert may still be queued (404): try again shortly.
    if (tries < 5 && state) setTimeout(() => writeBlock(status, tries + 1), 2000);
    else settings.setStatus(t("Recording not saved yet: {err}", { err: err.message || err }));
  }
}

// A finished segment: spooled already, chunk by chunk; now its file goes
// up and the block learns of it. A failed upload stays spooled and is
// tried again until it lands or the recording is discarded.
async function uploadSegment(blockId, seg) {
  if (!state || state.blockId !== blockId) return;
  set({ pendingUploads: state.pendingUploads + 1 });
  const ext = extensionFor(seg.mime);
  const file = new File([seg.blob], `recording-${seg.seg + 1}${ext}`, { type: seg.mime });
  let data = null;
  for (let attempt = 0; !data && state?.blockId === blockId; attempt++) {
    data = await postFile("/api/upload-file", file);
    if (!data) await new Promise((r) => setTimeout(r, Math.min(30_000, 2000 * 2 ** attempt)));
  }
  if (!data || state?.blockId !== blockId) return;
  const segments = [...state.segments, { url: data.url, started: seg.started, dur: seg.dur, bytes: seg.blob.size, mime: seg.mime }]
    .sort((a, b) => a.started - b.started);
  set({ segments, pendingUploads: state.pendingUploads - 1 });
  await spool.dropSegment(blockId, seg.seg);
  await writeBlock(state.status === "finishing" && !state.pendingUploads ? "done" : state.status === "paused" ? "paused" : "recording");
  if (state?.status === "finishing" && !state.pendingUploads) finish();
}

function finish() {
  clearInterval(timer);
  timer = 0;
  recorder = null;
  const had = state;
  state = null;
  coarse = null;
  emit();
  if (!had) return;
  settings.setStatus(t("Recording saved: {length}.", { length: fmtClock(had.segments.reduce((sum, s) => sum + s.dur, 0)) }));
  if (had.segments.length) settings.onDone?.(had.blockId);
}

// Why the microphone could not be had, in words that say where to look:
// the browser's own refusal (the address bar's site settings), the
// system's (Windows and macOS let the user keep desktop apps away from the
// microphone), no device, or a device another program holds.
function microphoneProblem(err) {
  const name = err?.name || "", message = String(err?.message || "");
  if (name === "NotAllowedError" || name === "SecurityError") {
    return /system|denied by system/i.test(message)
      ? t("The system blocks the microphone for this browser: allow it in the system's privacy settings (Windows: Privacy & security → Microphone → let desktop apps access it).")
      : t("The browser blocked the microphone. Allow it for this site in the address bar's site settings, then try again.");
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") return t("No microphone was found.");
  if (name === "NotReadableError" || name === "AbortError") return t("The microphone is in use by another program.");
  if (name === "OverconstrainedError") return t("The microphone does not support the recording settings.");
  return t("The microphone could not be opened: {err}", { err: message || name || "unknown error" });
}

// Start recording into `pageId`: asks for the microphone, inserts the
// block, runs the recorder. Says why when it cannot.
export async function start(pageId) {
  if (state) return;
  if (!canRecord()) {
    settings.setStatus(window.isSecureContext
      ? t("This browser cannot record audio.")
      : t("Recording needs HTTPS or localhost — the browser blocks the microphone on plain HTTP."));
    return;
  }
  state = { blockId: null, pageId, status: "asking", started: 0, segments: [], pendingUploads: 0, elapsed: 0, level: 0 };
  set({});
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
  } catch (err) {
    state = null; coarse = null; emit();
    settings.setStatus(microphoneProblem(err));
    return;
  }
  if (!state) { stream.getTracks().forEach((track) => track.stop()); return; }
  const started = Date.now();
  const blockId = settings.insertBlock?.({
    content: defaultTitle(started, t),
    properties: { t0: started, recording: { started, segments: [], state: "recording", dur: 0, by: CLIENT_ID, at: started } },
  });
  if (!blockId) { stream.getTracks().forEach((track) => track.stop()); state = null; coarse = null; emit(); return; }
  set({ blockId, started, status: "recording" });
  recorder = createRecorder(stream, {
    onChunk: (seg, n, blob) => {
      if (n === 0) spool.openSegment(blockId, seg, { started: Date.now(), mime: recorder?.mime || "", ws: getCurrentWorkspace() || "", page: pageId, block: blockId }).catch(() => {});
      spool.putChunk(blockId, seg, n, blob).catch(() => {});
    },
    onSegment: (seg) => { uploadSegment(blockId, seg); },
    onLevel: (level) => { if (state && state.level !== level) { state = { ...state, level }; emit(); } },
    onState: (status) => { if (state && status !== "stopped") set({ status }); },
  });
  recorder.start();
  timer = setInterval(() => { if (state && recorder) { state = { ...state, elapsed: recorder.elapsed() }; emit(); } }, 250);
}

export function pause() {
  if (recorder?.state !== "recording") return;
  recorder.pause();
  set({ status: "paused" });
  writeBlock("paused");
}

export function resume() {
  if (recorder?.state !== "paused") return;
  recorder.resume();
  set({ status: "recording" });
  writeBlock("recording");
}

export function toggle() { if (state?.status === "paused") resume(); else pause(); }

// Stop: the last segment finalises and uploads, then the block is marked
// done and the store empties (uploadSegment, once the upload lands); with
// no segment on its way (stopped while paused, or at once), done now.
export async function stop() {
  if (!recorder || !state) return;
  const rec = recorder;
  set({ status: "finishing" });
  await rec.stop();
  if (state?.status === "finishing" && !state.pendingUploads) { await writeBlock("done"); finish(); }
}

// A bookmark: an empty note stamped with the moment just before, so a star
// in the margin is no longer the way to mark a point of the lecture.
export function bookmark() {
  if (!state || !settings.insertBlock) return;
  const at = Math.max(state.started, Date.now() - settings.leadIn());
  settings.insertBlock({ content: `★ ${fmtClock(elapsedAt(at))}`, properties: { t0: at } });
}

// ms into the recording at wall-clock `wall`: the finished segments before
// it, and the one in progress.
function elapsedAt(wall) {
  if (!state) return 0;
  let ms = 0;
  for (const seg of state.segments) ms += Math.max(0, Math.min(seg.dur, wall - seg.started));
  if (recorder?.state === "recording") ms += Math.max(0, recorder.elapsed() - state.segments.reduce((sum, s) => sum + s.dur, 0) - (Date.now() - wall));
  return ms;
}

// --- recovery ------------------------------------------------------------------------
// Segments a closed or crashed tab left in the spool, for the workspace
// open now: [{block, page, segments: [seg numbers]}].
export async function leftBehind() {
  const rows = await spool.leftBehind();
  const ws = getCurrentWorkspace() || "";
  const byBlock = new Map();
  for (const row of rows) {
    if (row.ws !== ws || (state && row.block === state.blockId)) continue;
    const entry = byBlock.get(row.block) || { block: row.block, page: row.page, rows: [] };
    entry.rows.push(row);
    byBlock.set(row.block, entry);
  }
  return [...byBlock.values()];
}

// Put a left-behind recording's segments up and mark its block done. The
// length of a segment comes from decoding its file; a file the browser
// cannot measure (a WebM cut short) counts its chunks, one a second.
export async function recover(entry) {
  const r = await apiJson(`${API}/blocks/${entry.block}`).catch(() => null);
  if (!r) { await discard(entry); return { ok: false, reason: "gone" }; }
  const rec = normalizeRecording(r.properties?.recording);
  const segments = [...rec.segments];
  for (const row of entry.rows) {
    const chunks = await spool.chunksOf(entry.block, row.seg);
    if (!chunks.length) { await spool.dropSegment(entry.block, row.seg); continue; }
    const blob = new Blob(chunks, { type: row.mime || "audio/webm" });
    const dur = await measure(blob) ?? chunks.length * 1000;
    const file = new File([blob], `recording-${row.seg + 1}${extensionFor(row.mime)}`, { type: blob.type });
    const data = await postFile("/api/upload-file", file);
    if (!data) return { ok: false, reason: "upload" };
    segments.push({ url: data.url, started: row.started, dur, bytes: blob.size, mime: blob.type });
    await spool.dropSegment(entry.block, row.seg);
  }
  segments.sort((a, b) => a.started - b.started);
  await apiJson(`${API}/blocks/${entry.block}`, { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ properties: { recording: { started: rec.started ?? segments[0]?.started ?? Date.now(), segments, state: "done",
      dur: segments.reduce((sum, s) => sum + s.dur, 0) } } }) });
  return { ok: true, segments: segments.length };
}

export async function discard(entry) {
  await spool.dropRecording(entry.block);
}

function measure(blob) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(blob);
    const audio = new Audio();
    const done = (value) => { URL.revokeObjectURL(url); resolve(value); };
    audio.onloadedmetadata = () => done(Number.isFinite(audio.duration) ? Math.round(audio.duration * 1000) : null);
    audio.onerror = () => done(null);
    audio.src = url;
    setTimeout(() => done(null), 5000);
  });
}
