// Capturing the microphone (docs/dev/recording.md "Capturing audio"), in
// segments. Each segment is a file of its own with its own header, so a
// crash loses at most one, and the recorder rolls over to a new one every
// SEGMENT_MS and on a pause. The chunks it delivers (one a second) are
// handed out as they come, for the spool; a finished segment comes with the
// wall-clock moment its audio began and its measured length.
//
// Two ways in, one interface. Apple's WebKit on a touch screen (the iPad
// app, Safari on an iPad or an iPhone) takes the samples through Web Audio,
// from an AudioWorklet, and writes them as WAV (wav.js): a MediaRecorder
// there saves silence or stalls the microphone when it starts after the
// tap's handler has returned, as it must after getUserMedia and at every
// rollover (docs/research/audio-recording.md "Apple's WebKit"). Everywhere
// else a MediaRecorder writes the browser's container: Opus in WebM on
// Chrome, Edge and Firefox, AAC in MP4 on Safari on a Mac.
//   onChunk(seg, n, blob)                a chunk of segment `seg`
//   onSegment({seg, blob, started, dur, mime})   a segment finished
//   onLevel(level)                        the input level, 0..1, a few times a second
//   onState(state)                        "recording" | "paused" | "stopped"
// `context` is an AudioContext made in the user's gesture (gestureContext:
// iOS runs one only then), which the recorder closes when it stops;
// `segmentMs` 0 never rolls over (dictation's one file).
import { touchScreen } from "../shared/lib/pointer.js";
import { SEGMENT_MS, extensionFor } from "./recording.js";
import { PCM_RATE, WAV_MIME, createDownsampler, wavHeader } from "./wav.js";
import { withDuration } from "./webm.js";

const MIMES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
const BITRATE = 48000; // speech; an hour is about 22 MB

export const capturesPcm = () => /^Apple/.test(globalThis.navigator?.vendor || "") && touchScreen();

export const canRecord = () => !!(navigator.mediaDevices?.getUserMedia && (capturesPcm() ? window.AudioContext : window.MediaRecorder));

export function pickMime() {
  return MIMES.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

// A lecture, not a call: no echo cancellation, which eats a distant voice.
export const MIC_CONSTRAINTS = { audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: true } };

// An AudioContext made now, from the tap that starts a recording and before
// anything is awaited: iOS lets one run only from a user's gesture.
export function gestureContext() {
  try {
    const context = new AudioContext();
    context.resume?.().catch(() => {});
    return context;
  } catch {
    return null;
  }
}

// The worklet the PCM capture runs on the audio thread: the input mixed to
// mono, posted in batches of 2048 frames (43 ms at 48 kHz), so the samples
// queue up on the page rather than being lost while it is busy.
const WORKLET = `registerProcessor("gamma-capture", class extends AudioWorkletProcessor {
  constructor() { super(); this.batch = new Float32Array(2048); this.n = 0; }
  process(inputs) {
    const channels = inputs[0];
    if (channels && channels.length) {
      for (let i = 0; i < channels[0].length; i++) {
        let v = 0;
        for (const channel of channels) v += channel[i];
        this.batch[this.n++] = v / channels.length;
        if (this.n === this.batch.length) {
          this.port.postMessage(this.batch, [this.batch.buffer]);
          this.batch = new Float32Array(2048);
          this.n = 0;
        }
      }
    }
    return true;
  }
});`;

// Samples in, WAV segments out: the PCM capture's half that needs no audio
// graph. `push(samples, sampleRate)` takes the next Float32 batch at the
// context's rate, downsampled to PCM_RATE (or the context's own rate when
// lower); a segment that reaches `segmentMs` ends there and the next begins
// at the same sample, its `started` where the last one's audio ended. A
// segment's first chunk carries the header, saying "to the end" (wav.js
// withWavSizes puts a crashed one right); the segment itself is the header
// with its sizes and every sample. `cut()` ends the segment in progress.
export function createPcmSegmenter({ segmentMs = SEGMENT_MS, onChunk, onSegment, now = Date.now } = {}) {
  let rate = 0, down = null, perSegment = Infinity;
  let seg = -1, cur = null, total = 0;
  let rolled = null; // where the segment after a rollover begins, until its first samples come

  function flush(c) {
    if (!c.pendingSamples) return;
    const parts = c.n === 0 ? [wavHeader(rate, null), ...c.pending] : c.pending;
    onChunk?.(c.seg, c.n++, new Blob(parts, { type: WAV_MIME }));
    c.pending = [];
    c.pendingSamples = 0;
  }

  // → the wall-clock moment the ended segment's audio ends, or null.
  function end() {
    const c = cur;
    cur = null;
    if (!c?.samples) return null;
    flush(c);
    const dur = Math.round((c.samples * 1000) / rate);
    const blob = new Blob([wavHeader(rate, c.samples * 2), ...c.parts], { type: WAV_MIME });
    onSegment?.({ seg: c.seg, blob, started: c.started, dur, mime: WAV_MIME });
    return c.started + dur;
  }

  function push(input, sampleRate) {
    if (!down) {
      rate = Math.min(PCM_RATE, Math.round(sampleRate));
      down = createDownsampler(sampleRate, rate);
      perSegment = segmentMs > 0 ? Math.round((segmentMs * rate) / 1000) : Infinity;
    }
    let out = down(input);
    // A segment opened by this batch began when its first sample was taken.
    const batchStart = Math.round(now() - (input.length * 1000) / sampleRate);
    while (out.length) {
      if (!cur) {
        seg += 1;
        cur = { seg, started: rolled ?? batchStart, parts: [], pending: [], pendingSamples: 0, samples: 0, n: 0 };
        rolled = null;
      }
      const take = out.subarray(0, Math.min(out.length, perSegment - cur.samples));
      cur.parts.push(take);
      cur.pending.push(take);
      cur.samples += take.length;
      cur.pendingSamples += take.length;
      total += take.length;
      if (cur.pendingSamples >= rate) flush(cur);
      out = out.subarray(take.length);
      if (cur.samples >= perSegment) rolled = end();
    }
  }

  return {
    push,
    cut() { rolled = null; return end(); },
    recordedMs: () => (rate ? Math.round((total * 1000) / rate) : 0),
  };
}

export function createRecorder(stream, { context = null, onChunk, onSegment, onLevel, onState, segmentMs = SEGMENT_MS } = {}) {
  let state = "stopped";
  let source = null;  // the stream in the audio graph: the meter's, and the PCM capture's
  let analyser = null, levelTimer = 0;

  const setState = (next) => { state = next; onState?.(next); };

  function streamSource() {
    context = context || new AudioContext();
    source = source || context.createMediaStreamSource(stream);
    return source;
  }

  // A MediaRecorder per segment, over the stream.
  function mediaRecorderEngine() {
    const mime = pickMime();
    let recorder = null;   // the MediaRecorder of the segment in progress
    let seg = -1;          // the segment number of the one in progress
    let rollover = 0;
    let current = null;    // {chunks, started, begun (performance.now), n}
    let done = 0;          // ms of finished segments

    function begin() {
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
        finishSegment(rec, mine, dur).finally(() => settle?.());
      };
      rec.onerror = () => { stop(); };
      recorder = rec;
      current = mine;
      rec.start(1000);
      clearTimeout(rollover);
      if (segmentMs > 0) rollover = setTimeout(() => { if (state === "recording") { end(); begin(); } }, segmentMs);
    }

    async function finishSegment(rec, mine, dur) {
      const type = rec.mimeType || mime || "audio/webm";
      let blob = new Blob(mine.chunks, { type });
      if (extensionFor(type) === ".webm") {
        try { blob = new Blob([withDuration(new Uint8Array(await blob.arrayBuffer()), dur)], { type }); } catch { /* as it came */ }
      }
      onSegment?.({ seg: mine.seg, blob, started: mine.started, dur, mime: type });
    }

    // Ends the segment in progress; the promise settles once its file has
    // been handed out (or at once when nothing was recording).
    function end() {
      clearTimeout(rollover);
      rollover = 0;
      let ending = Promise.resolve();
      if (recorder && recorder.state !== "inactive" && current) {
        const mine = current;
        ending = new Promise((resolve) => { mine.settle = resolve; });
        recorder.stop();
      }
      recorder = null;
      current = null;
      return ending;
    }

    return {
      mime,
      begin,
      end,
      close() {},
      elapsed: () => done + (current?.begun ? performance.now() - current.begun : 0),
    };
  }

  // The samples through Web Audio, into WAV segments (createPcmSegmenter).
  // The graph is built on the first begin and stays until the stop; a
  // pause drops what comes meanwhile.
  function pcmEngine() {
    const segmenter = createPcmSegmenter({ segmentMs, onChunk, onSegment });
    let node = null, sink = null;
    const take = (samples) => { if (state === "recording") segmenter.push(samples, context.sampleRate); };

    function connect() {
      try {
        streamSource();
        // A node the destination pulls, kept silent: the capture node runs only when pulled.
        sink = context.createGain();
        sink.gain.value = 0;
        sink.connect(context.destination);
      } catch {
        stop();
        return;
      }
      const viaProcessor = () => {
        if (state === "stopped") return;
        try {
          node = context.createScriptProcessor(16384, 1, 1);
          node.onaudioprocess = (e) => take(new Float32Array(e.inputBuffer.getChannelData(0)));
          source.connect(node);
          node.connect(sink);
        } catch {
          stop();
        }
      };
      if (!context.audioWorklet || typeof AudioWorkletNode === "undefined") { viaProcessor(); return; }
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
      context.audioWorklet.addModule(url).then(() => {
        if (state === "stopped") return;
        node = new AudioWorkletNode(context, "gamma-capture");
        node.port.onmessage = (e) => take(e.data);
        source.connect(node);
        node.connect(sink);
      }).catch(viaProcessor).finally(() => URL.revokeObjectURL(url));
    }

    return {
      mime: WAV_MIME,
      begin() { if (!sink) connect(); },
      end() { segmenter.cut(); return Promise.resolve(); },
      close() {
        try { node?.port?.close(); node?.disconnect(); sink?.disconnect(); } catch { /* the context closes anyway */ }
      },
      elapsed: () => segmenter.recordedMs(),
    };
  }

  const engine = capturesPcm() ? pcmEngine() : mediaRecorderEngine();

  function startLevel() {
    try {
      streamSource();
      analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
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
    context?.resume?.().catch(() => {});
    startLevel();
    engine.begin();
  }

  function pause() {
    if (state !== "recording") return;
    engine.end();
    setState("paused");
  }

  function resume() {
    if (state !== "paused") return;
    setState("recording");
    engine.begin();
  }

  // → a promise that settles once the last segment has been handed out.
  function stop() {
    if (state === "stopped") return Promise.resolve();
    const last = engine.end();
    clearInterval(levelTimer);
    engine.close();
    context?.close().catch(() => {});
    stream.getTracks().forEach((track) => track.stop());
    setState("stopped");
    return last;
  }

  return {
    start, pause, resume, stop,
    // ms recorded so far: the finished segments and the one in progress.
    elapsed: () => engine.elapsed(),
    get state() { return state; },
    get mime() { return engine.mime; },
    get analyser() { return analyser; },
  };
}
