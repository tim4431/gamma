// Capturing the microphone (docs/dev/recording.md "Capturing audio"): a
// MediaRecorder over the stream, in segments. Each segment is a file of
// its own with its own header, so a crash loses at most one, and the
// recorder rolls over to a new one every SEGMENT_MS and on a pause. The
// chunks it delivers (one a second) are handed out as they come, for the
// spool; a finished segment comes with the wall-clock moment its audio
// began and its measured length. The container is the browser's: Opus in
// WebM on Chrome, Edge and Firefox, AAC in MP4 on Safari.
//   onChunk(seg, n, blob)                a chunk of segment `seg`
//   onSegment({seg, blob, started, dur, mime})   a segment finished
//   onLevel(level)                        the input level, 0..1, a few times a second
//   onState(state)                        "recording" | "paused" | "stopped"
import { SEGMENT_MS, extensionFor } from "./recording.js";
import { withDuration } from "./webm.js";

const MIMES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
const BITRATE = 48000; // speech; an hour is about 22 MB

export const canRecord = () => !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);

export function pickMime() {
  return MIMES.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

// A lecture, not a call: no echo cancellation, which eats a distant voice.
export const MIC_CONSTRAINTS = { audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: true } };

export function createRecorder(stream, { onChunk, onSegment, onLevel, onState, segmentMs = SEGMENT_MS } = {}) {
  const mime = pickMime();
  let recorder = null;   // the MediaRecorder of the segment in progress
  let seg = -1;          // the segment number of the one in progress
  let state = "stopped";
  let rollover = 0;
  let current = null;    // {chunks, started, begun (performance.now), n}
  let done = 0;          // ms of finished segments
  let ending = null;     // the segment being finalised: resolves once onSegment has it
  let analyser = null, audioCtx = null, levelTimer = 0;

  const setState = (next) => { state = next; onState?.(next); };

  function startSegment() {
    seg += 1;
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: BITRATE } : { audioBitsPerSecond: BITRATE });
    const mine = { chunks: [], started: 0, begun: 0, n: 0, seg };
    rec.onstart = () => { mine.started = Date.now(); mine.begun = performance.now(); };
    rec.ondataavailable = (e) => {
      if (!e.data?.size) return;
      mine.chunks.push(e.data);
      onChunk?.(mine.seg, mine.n++, e.data);
    };
    rec.onstop = () => {
      const dur = Math.max(0, Math.round(performance.now() - (mine.begun || performance.now())));
      done += dur;
      const settle = mine.settle;
      if (!mine.chunks.length) { settle?.(); return; }
      finishSegment(mine, dur).finally(() => settle?.());
    };
    rec.onerror = () => { stop(); };
    recorder = rec;
    current = mine;
    rec.start(1000);
    clearTimeout(rollover);
    rollover = setTimeout(() => { if (state === "recording") { endSegment(); startSegment(); } }, segmentMs);
  }

  async function finishSegment(mine, dur) {
    const type = recorder?.mimeType || mime || "audio/webm";
    let blob = new Blob(mine.chunks, { type });
    if (extensionFor(type) === ".webm") {
      try { blob = new Blob([withDuration(new Uint8Array(await blob.arrayBuffer()), dur)], { type }); } catch { /* as it came */ }
    }
    onSegment?.({ seg: mine.seg, blob, started: mine.started, dur, mime: type });
  }

  // Ends the segment in progress; the promise settles once its file has
  // been handed out (or at once when nothing was recording).
  function endSegment() {
    clearTimeout(rollover);
    rollover = 0;
    if (recorder && recorder.state !== "inactive" && current) {
      const mine = current;
      ending = new Promise((resolve) => { mine.settle = resolve; });
      recorder.stop();
    } else ending = Promise.resolve();
    recorder = null;
    current = null;
    return ending;
  }

  function startLevel() {
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      audioCtx.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      levelTimer = setInterval(() => {
        if (state !== "recording") { onLevel?.(0); return; }
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (const v of data) peak = Math.max(peak, Math.abs(v - 128) / 128);
        onLevel?.(Math.min(1, peak * 1.4));
      }, 80);
    } catch { /* no meter: recording works without it */ }
  }

  function start() {
    if (state !== "stopped") return;
    setState("recording");
    startLevel();
    startSegment();
  }

  function pause() {
    if (state !== "recording") return;
    endSegment();
    setState("paused");
  }

  function resume() {
    if (state !== "paused") return;
    setState("recording");
    startSegment();
  }

  // → a promise that settles once the last segment has been handed out.
  function stop() {
    if (state === "stopped") return Promise.resolve();
    const last = endSegment();
    clearInterval(levelTimer);
    audioCtx?.close().catch(() => {});
    stream.getTracks().forEach((track) => track.stop());
    setState("stopped");
    return last;
  }

  // ms recorded so far: the finished segments and the one in progress.
  const elapsed = () => done + (current?.begun ? performance.now() - current.begun : 0);

  return { start, pause, resume, stop, elapsed, get state() { return state; }, get mime() { return mime; } };
}
