// How the text viewer reads a file's text (docs/dev/documents.md
// "Viewers"): its head first, through one Range request, the rest on
// request up to a cap; the bytes decoded by their byte-order mark, else as
// UTF-8 with replacement; a head cut at its last whole line and every line
// at a width a view can draw; a NUL, or too many bytes that are no text,
// meaning the file is no text after all (the server's rule too:
// gamma/documents/text.py decode, tests/shared/textdecode.json). Pure but
// for `loadText`'s fetch, so node tests the decoding
// (tests/textLoad.test.mjs).

// The head a file opens with: one Range request of this many bytes.
export const HEAD_BYTES = 2 * 1024 * 1024;
// Show all reads up to this much; past it the file is read elsewhere.
export const MAX_TEXT_BYTES = 16 * 1024 * 1024;
// A line is cut at this many characters (a minified file is one line of
// megabytes, which the view would draw whole).
export const LINE_CAP = 10000;
// More U+FFFD than this share of the characters says binary (text.py's
// BAD_SHARE).
export const BAD_SHARE = 0.05;

// The byte-order mark at the start of `bytes`, as {encoding, skip}: UTF-8,
// UTF-16LE (what Windows PowerShell 5.1 writes) or UTF-16BE; null for none.
export function byteOrderMark(bytes) {
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return { encoding: "utf-8", skip: 3 };
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) return { encoding: "utf-16le", skip: 2 };
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) return { encoding: "utf-16be", skip: 2 };
  return null;
}

// Whether decoded text is no text: a NUL anywhere in it (a UTF-16 file's
// zero bytes are no NUL once decoded by its mark), or U+FFFD past BAD_SHARE
// of its characters.
export function notText(text) {
  if (text.includes("\0")) return true;
  let bad = 0;
  for (let i = text.indexOf("\ufffd"); i >= 0; i = text.indexOf("\ufffd", i + 1)) bad++;
  return bad > BAD_SHARE * text.length;
}

// The text of `bytes`: {text, bad, binary, cut}. `bad`: some bytes were not
// the encoding's and show as U+FFFD; `binary`: what was decoded is no text
// (notText; text "" and bad false), so the pane hands over to the file pane;
// `cut`: with `head`, the partial last line was dropped (the file goes on
// past the bytes read), or a head with no line break at all kept as its one
// line, and in any read a line past LINE_CAP characters was cut, each with
// a mark at the cut.
export function decodeText(bytes, { head = false } = {}) {
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const bom = byteOrderMark(u8);
  let body = bom ? u8.subarray(bom.skip) : u8;
  let cut = false;
  if (head && body.length) {
    // the last line is partial unless the read ended on a line break: drop
    // it (by bytes; a UTF-16 line break is two bytes, found by its unit),
    // unless it is the only line (a minified file), which LINE_CAP cuts
    const last = lastLineBreak(body, bom?.encoding);
    cut = last + 1 < body.length;
    if (last >= 0) body = body.subarray(0, last + 1);
  }
  // a head's character cut at its end is left out, not shown as U+FFFD
  const opts = { stream: head };
  let text;
  let bad = false;
  try {
    text = new TextDecoder(bom?.encoding || "utf-8", { fatal: true }).decode(body, opts);
  } catch {
    text = new TextDecoder(bom?.encoding || "utf-8").decode(body, opts);
    bad = true;
  }
  if (notText(text)) return { text: "", bad: false, binary: true, cut: false };
  const capped = capLines(text);
  return { text: capped.text, bad, binary: false, cut: cut || capped.cut };
}

// The index of the last line break's final byte in `body` (a \n, or a \r:
// an old Mac file's), -1 for none.
function lastLineBreak(body, encoding) {
  if (encoding === "utf-16le" || encoding === "utf-16be") {
    const lo = encoding === "utf-16le" ? 0 : 1, hi = 1 - lo;
    for (let i = body.length - (body.length % 2) - 2; i >= 0; i -= 2) {
      if ((body[i + lo] === 0x0a || body[i + lo] === 0x0d) && body[i + hi] === 0) return i + 1;
    }
    return -1;
  }
  return Math.max(body.lastIndexOf(0x0a), body.lastIndexOf(0x0d));
}

// Every line past LINE_CAP characters cut there, with an ellipsis; a line
// ends at \r\n, \r or \n, as the server's and CodeMirror's lines do, and
// its break is kept, so the cut never moves a line's number.
function capLines(text) {
  if (text.length <= LINE_CAP) return { text, cut: false };
  const parts = text.split(/(\r\n?|\n)/);
  let cut = false;
  for (let i = 0; i < parts.length; i += 2) {
    if (parts[i].length > LINE_CAP) { parts[i] = `${parts[i].slice(0, LINE_CAP)}…`; cut = true; }
  }
  return { text: cut ? parts.join("") : text, cut };
}

// The file's whole size from a response: a 206's Content-Range total, else
// Content-Length, else the bytes read. Null for a total the server did not
// say ("*").
export function contentTotal(status, contentRange, contentLength, read) {
  if (status === 206) {
    const m = /\/\s*(\d+|\*)\s*$/.exec(contentRange || "");
    if (m) return m[1] === "*" ? null : Number(m[1]);
  }
  const n = Number(contentLength);
  return contentLength != null && contentLength !== "" && Number.isFinite(n) ? n : read;
}

// The body of `res`, at most `limit` bytes of it: {bytes, more}, `more`
// when the body went on past them, the rest left unread (the response
// cancelled), so a file too large to show is never downloaded to say so.
async function readUpTo(res, limit) {
  const reader = res.body?.getReader();
  if (!reader) return { bytes: new Uint8Array(0), more: false };
  const chunks = [];
  let n = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    n += value.length;
    if (n > limit) { reader.cancel().catch(() => {}); break; }
  }
  const bytes = new Uint8Array(Math.min(n, limit));
  let at = 0;
  for (const c of chunks) {
    if (at >= bytes.length) break;
    const part = c.subarray(0, bytes.length - at);
    bytes.set(part, at);
    at += part.length;
  }
  return { bytes, more: n > limit };
}

// The file at `fileUrl` as text: its head (`whole` false: one Range request
// of HEAD_BYTES; a server that ignores the range answers 200 with it all,
// of which as much is read) or the whole file. {text, bad, binary, cut,
// size, read, whole}: `size` the file's size when known, `read` the bytes
// read, `whole` whether that is all of it. A GET refused (404: a work
// folder's file on another computer) throws an Error with `status`; a file
// past MAX_TEXT_BYTES read whole throws one with `tooLarge`, as soon as its
// size or its bytes say so.
export async function loadText(fileUrl, { whole = false, signal } = {}) {
  const res = await fetch(fileUrl, { headers: whole ? {} : { Range: `bytes=0-${HEAD_BYTES - 1}` }, signal });
  if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status });
  const size = contentTotal(res.status, res.headers.get("content-range"), res.headers.get("content-length"), null);
  const tooLarge = () => Object.assign(new Error("too large"), { tooLarge: true, size });
  if (whole && size != null && size > MAX_TEXT_BYTES) {
    res.body?.cancel().catch(() => {});
    throw tooLarge();
  }
  const { bytes, more } = await readUpTo(res, whole ? MAX_TEXT_BYTES : HEAD_BYTES);
  if (whole && more) throw tooLarge();
  const total = size ?? (more ? null : bytes.length);
  const all = !more && (res.status !== 206 || bytes.length >= total);
  return { ...decodeText(bytes, { head: !all }), size: total, read: bytes.length, whole: all };
}
