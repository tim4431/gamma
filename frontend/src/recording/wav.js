// The WAV files the recorder writes where it captures through Web Audio
// rather than MediaRecorder (recorder.js capturesPcm, docs/dev/recording.md
// "Capturing audio"): 16-bit mono PCM at PCM_RATE, which every browser's
// <audio> plays and transcription takes as it is. Pure; tests/recording.test.mjs.
export const PCM_RATE = 16000; // speech; an hour is about 115 MB
export const WAV_MIME = "audio/wav";
const HEADER = 44;

// The 44-byte header of a 16-bit mono WAV at `rate` holding `dataBytes` of
// samples; with `dataBytes` null, both sizes say "to the end of the file"
// (0xFFFFFFFF), for a file still being written.
export function wavHeader(rate, dataBytes) {
  const out = new Uint8Array(HEADER);
  const view = new DataView(out.buffer);
  const ascii = (at, text) => { for (let i = 0; i < text.length; i++) out[at + i] = text.charCodeAt(i); };
  const unknown = dataBytes == null;
  ascii(0, "RIFF");
  view.setUint32(4, unknown ? 0xffffffff : 36 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);       // the fmt chunk's size
  view.setUint16(20, 1, true);        // PCM
  view.setUint16(22, 1, true);        // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); // bytes a second
  view.setUint16(32, 2, true);        // bytes a frame
  view.setUint16(34, 16, true);       // bits a sample
  ascii(36, "data");
  view.setUint32(40, unknown ? 0xffffffff : dataBytes, true);
  return out;
}

const isOurs = (bytes) => bytes.length >= HEADER
  && String.fromCharCode(...bytes.subarray(0, 4)) === "RIFF"
  && String.fromCharCode(...bytes.subarray(8, 12)) === "WAVE"
  && String.fromCharCode(...bytes.subarray(36, 40)) === "data";

// The file with its sizes made true to its length, in place: a segment the
// spool put back together after a crash ends wherever its last chunk did,
// and its header still says "to the end". Bytes not laid out as wavHeader
// writes them come back as they were.
export function withWavSizes(bytes) {
  if (!isOurs(bytes)) return bytes;
  const data = (bytes.length - HEADER) & ~1; // whole samples
  const view = new DataView(bytes.buffer, bytes.byteOffset, HEADER);
  view.setUint32(4, 36 + data, true);
  view.setUint32(40, data, true);
  return bytes;
}

// A streaming downsampler from `inRate` to `outRate`: each call takes the
// next Float32 samples (-1..1) and returns the Int16 samples they complete,
// each the mean of the input samples its span covers (an area average,
// which is also the low-pass a speech recording needs). Its position runs
// on across calls, so the batches join without a seam.
export function createDownsampler(inRate, outRate) {
  const step = inRate / outRate; // input samples per output sample, at least 1
  if (!(step >= 1)) throw new Error(`cannot downsample ${inRate} Hz to ${outRate} Hz`);
  let sum = 0, weight = 0; // the output sample being summed: its input so far, in input samples
  return (input) => {
    const out = new Int16Array(Math.ceil((input.length + weight) / step) + 1);
    let n = 0;
    for (let i = 0; i < input.length; i++) {
      const v = input[i];
      // The part of this input sample that falls in the current output sample.
      const part = Math.min(1, step - weight);
      sum += v * part;
      weight += part;
      if (weight >= step - 1e-9) {
        out[n++] = Math.max(-32768, Math.min(32767, Math.round((sum / step) * 32767)));
        // The rest of this input sample starts the next one.
        sum = v * (1 - part);
        weight = 1 - part;
      }
    }
    return out.slice(0, n);
  };
}
