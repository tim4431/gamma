// A WebM file from MediaRecorder says nothing about its length: the
// recorder writes the Segment with an unknown size and no Duration, so an
// <audio> element reports Infinity until it has read to the end and seeks
// poorly meanwhile. `withDuration` writes the measured length into the
// file's Info element before upload. EBML only as far as needed: an
// element is an id (a vint whose marker bits stay), a size (a vint with
// the marker removed; all ones = unknown) and a body. Anything unexpected
// leaves the bytes as they came. Pure; tests/recording.test.mjs.
const EBML = 0x1a45dfa3, SEGMENT = 0x18538067, INFO = 0x1549a966, DURATION = 0x4489;

// → {value, width} of the vint at `at`; `keepMarker` for an id.
function vint(bytes, at, keepMarker) {
  const first = bytes[at];
  if (first === undefined || first === 0) return null;
  let width = 1;
  for (let mask = 0x80; !(first & mask); mask >>= 1) width++;
  if (at + width > bytes.length) return null;
  let value = keepMarker ? first : first & ((1 << (8 - width)) - 1);
  let unknown = !keepMarker && value === (1 << (8 - width)) - 1;
  for (let i = 1; i < width; i++) {
    value = value * 256 + bytes[at + i];
    if (bytes[at + i] !== 0xff) unknown = false;
  }
  return { value, width, unknown: !keepMarker && unknown };
}

// The element at `at`: {id, size (null when unknown), head: the bytes of
// id and size, end: where the body ends (the buffer's end when unknown)}.
function element(bytes, at) {
  const id = vint(bytes, at, true);
  if (!id) return null;
  const size = vint(bytes, at + id.width, false);
  if (!size) return null;
  const head = id.width + size.width;
  const body = at + head;
  return { id: id.value, size: size.unknown ? null : size.value, head, body, end: size.unknown ? bytes.length : body + size.value };
}

const sizeVint = (n) => { // the 8-byte form: never a width problem
  const out = new Uint8Array(8);
  out[0] = 0x01;
  for (let i = 7; i >= 1; i--) { out[i] = n & 0xff; n = Math.floor(n / 256); }
  return out;
};

const float64 = (value) => { const b = new Uint8Array(8); new DataView(b.buffer).setFloat64(0, value); return b; };

// The file with `durationMs` in its Info element (milliseconds: the
// recorder's TimecodeScale is a millisecond), or the same bytes when the
// file is not the WebM expected.
export function withDuration(bytes, durationMs) {
  const header = element(bytes, 0);
  if (!header || header.id !== EBML || header.size == null) return bytes;
  const segment = element(bytes, header.end);
  if (!segment || segment.id !== SEGMENT) return bytes;
  let at = segment.body;
  while (at < segment.end) {
    const child = element(bytes, at);
    if (!child || child.size == null) return bytes;
    if (child.id !== INFO) { at = child.end; continue; }
    // Inside Info: a Duration already there is overwritten in place.
    let inner = child.body;
    while (inner < child.end) {
      const field = element(bytes, inner);
      if (!field || field.size == null) return bytes;
      if (field.id === DURATION) {
        if (field.size === 8) bytes.set(float64(durationMs), field.body);
        else if (field.size === 4) new DataView(bytes.buffer, bytes.byteOffset + field.body, 4).setFloat32(0, durationMs);
        else return bytes;
        return bytes;
      }
      inner = field.end;
    }
    // Otherwise Info is rebuilt with one: its id, an 8-byte size, the old
    // fields, the Duration element.
    const fields = bytes.subarray(child.body, child.end);
    const duration = new Uint8Array([0x44, 0x89, 0x88, ...float64(durationMs)]);
    const info = new Uint8Array(4 + 8 + fields.length + duration.length);
    info.set([0x15, 0x49, 0xa9, 0x66], 0);
    info.set(sizeVint(fields.length + duration.length), 4);
    info.set(fields, 12);
    info.set(duration, 12 + fields.length);
    const out = new Uint8Array(bytes.length - (child.end - at) + info.length);
    out.set(bytes.subarray(0, at), 0);
    out.set(info, at);
    out.set(bytes.subarray(child.end), at + info.length);
    return out;
  }
  return bytes;
}

// The Duration an Info element carries, in ms, or null (for tests and recovery).
export function readDuration(bytes) {
  const header = element(bytes, 0);
  if (!header || header.id !== EBML || header.size == null) return null;
  const segment = element(bytes, header.end);
  if (!segment || segment.id !== SEGMENT) return null;
  let at = segment.body;
  while (at < segment.end) {
    const child = element(bytes, at);
    if (!child || child.size == null) return null;
    if (child.id === INFO) {
      let inner = child.body;
      while (inner < child.end) {
        const field = element(bytes, inner);
        if (!field || field.size == null) return null;
        if (field.id === DURATION) {
          const view = new DataView(bytes.buffer, bytes.byteOffset + field.body, field.size);
          return field.size === 8 ? view.getFloat64(0) : field.size === 4 ? view.getFloat32(0) : null;
        }
        inner = field.end;
      }
      return null;
    }
    at = child.end;
  }
  return null;
}
