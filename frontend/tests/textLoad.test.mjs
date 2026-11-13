// How the text viewer decodes a file (src/documents/viewers/textLoad.js):
// the byte-order marks, UTF-8 with replacement, a head's partial last line,
// the line cap, a NUL, and the size a response says; and how much of a
// response loadText reads, through a stand-in fetch.
import test from "node:test";
import assert from "node:assert/strict";
import { HEAD_BYTES, LINE_CAP, MAX_TEXT_BYTES, byteOrderMark, contentTotal, decodeText, loadText, looksBinary } from "../src/documents/viewers/textLoad.js";

const utf8 = (s) => new TextEncoder().encode(s);
const utf16 = (s, little = true) => {
  const out = new Uint8Array(2 + s.length * 2);
  out.set(little ? [0xff, 0xfe] : [0xfe, 0xff]);
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    out[2 + 2 * i] = little ? c & 0xff : c >> 8;
    out[3 + 2 * i] = little ? c >> 8 : c & 0xff;
  }
  return out;
};
const cat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
};

test("the limits: a 2 MiB head, 16 MiB in all, 10,000 characters a line", () => {
  assert.equal(HEAD_BYTES, 2 * 1024 * 1024);
  assert.equal(MAX_TEXT_BYTES, 16 * 1024 * 1024);
  assert.equal(LINE_CAP, 10000);
});

test("a byte-order mark picks UTF-8, UTF-16LE or UTF-16BE, and is not shown", () => {
  assert.deepEqual(byteOrderMark(new Uint8Array([0xef, 0xbb, 0xbf, 0x41])), { encoding: "utf-8", skip: 3 });
  assert.deepEqual(byteOrderMark(new Uint8Array([0xff, 0xfe, 0x41, 0])), { encoding: "utf-16le", skip: 2 });
  assert.deepEqual(byteOrderMark(new Uint8Array([0xfe, 0xff, 0, 0x41])), { encoding: "utf-16be", skip: 2 });
  assert.equal(byteOrderMark(utf8("plain")), null);
  assert.deepEqual(decodeText(cat(new Uint8Array([0xef, 0xbb, 0xbf]), utf8("héllo\n"))), { text: "héllo\n", bad: false, binary: false, cut: false });
  // Windows PowerShell 5.1 writes UTF-16LE logs: their NULs are text, not binary
  assert.deepEqual(decodeText(utf16("Get-Item\r\nrun 2\r\n")), { text: "Get-Item\r\nrun 2\r\n", bad: false, binary: false, cut: false });
  assert.equal(decodeText(utf16("big-endian\n", false)).text, "big-endian\n");
});

test("UTF-8 without a mark; invalid bytes show as U+FFFD and are reported", () => {
  assert.deepEqual(decodeText(utf8("a\nb\n")), { text: "a\nb\n", bad: false, binary: false, cut: false });
  const latin1 = cat(utf8("caf"), new Uint8Array([0xe9]), utf8("\n"));
  const out = decodeText(latin1);
  assert.equal(out.text, "caf�\n");
  assert.equal(out.bad, true);
  assert.equal(decodeText(utf8("日本語\n")).bad, false, "valid multi-byte text is not 'bad'");
});

test("a head drops its partial last line; a read that ends on a line break keeps every line", () => {
  assert.deepEqual(decodeText(utf8("one\ntwo\nthr"), { head: true }), { text: "one\ntwo\n", bad: false, binary: false, cut: true });
  assert.deepEqual(decodeText(utf8("one\ntwo\n"), { head: true }), { text: "one\ntwo\n", bad: false, binary: false, cut: false });
  assert.deepEqual(decodeText(utf8("no line break at all"), { head: true }), { text: "no line break at all", bad: false, binary: false, cut: true },
    "a head that is one line (a minified file) keeps it");
  // that line cut inside a character: the partial character is left out, not shown as U+FFFD
  const cutIn = utf8("aé");
  assert.deepEqual(decodeText(cutIn.subarray(0, cutIn.length - 1), { head: true }), { text: "a", bad: false, binary: false, cut: true });
  assert.equal(decodeText(utf8("one\ntwo\nthr")).cut, false, "the whole file keeps its last line");
  // a UTF-16 head is cut at a two-byte line break, never between a unit's bytes
  const le = utf16("ab\ncd");
  assert.deepEqual(decodeText(le, { head: true }), { text: "ab\n", bad: false, binary: false, cut: true });
  assert.equal(decodeText(le.subarray(0, le.length - 1), { head: true }).text, "ab\n", "an odd byte count");
});

test("a line past 10,000 characters is cut there with an ellipsis", () => {
  const long = "x".repeat(LINE_CAP + 5);
  const out = decodeText(utf8(`short\n${long}\nend\n`));
  assert.equal(out.cut, true);
  assert.deepEqual(out.text.split("\n").map((l) => l.length), [5, LINE_CAP + 1, 3, 0]);
  assert.ok(out.text.split("\n")[1].endsWith("…"));
  assert.equal(decodeText(utf8("y".repeat(LINE_CAP))).cut, false, "exactly the cap is whole");
});

test("a NUL in the first 8 KiB of a file without a mark says binary", () => {
  assert.equal(looksBinary(new Uint8Array([0x41, 0x00, 0x42])), true);
  assert.equal(looksBinary(utf8("plain text\n")), false);
  const late = cat(utf8("a".repeat(8192)), new Uint8Array([0]));
  assert.equal(looksBinary(late), false, "past the first 8 KiB it is not scanned");
  assert.deepEqual(decodeText(new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x00, 0x01])), { text: "", bad: false, binary: true, cut: false });
});

test("contentTotal reads a 206's Content-Range total, else Content-Length, else the bytes read", () => {
  assert.equal(contentTotal(206, "bytes 0-2097151/3145728", "2097152", 2097152), 3145728);
  assert.equal(contentTotal(206, "bytes 0-99/*", "100", 100), null, "an unknown total");
  assert.equal(contentTotal(200, null, "512", 512), 512);
  assert.equal(contentTotal(200, null, null, 77), 77);
  assert.equal(contentTotal(200, null, "", 77), 77);
});

// A response whose body is `chunks` chunks of `chunk` bytes of "x", made as
// they are read; `got.pulled` counts the chunks read, `got.cancelled` says
// the body was cancelled.
function served(status, headers, chunks, chunk = 1024 * 1024) {
  const got = { pulled: 0, cancelled: false };
  const body = new ReadableStream({
    pull(ctl) {
      if (got.pulled >= chunks) { ctl.close(); return; }
      got.pulled++;
      ctl.enqueue(new Uint8Array(chunk).fill(0x78));
    },
    cancel() { got.cancelled = true; },
  }, { highWaterMark: 0 });
  return { res: new Response(body, { status, headers }), got };
}
async function withFetch(res, run) {
  const real = globalThis.fetch;
  globalThis.fetch = async () => res;
  try { return await run(); } finally { globalThis.fetch = real; }
}

test("Show all past 16 MiB says too large from the size alone, the body left unread", async () => {
  const { res, got } = served(200, { "content-length": String(60 * 1024 * 1024) }, 60);
  await assert.rejects(withFetch(res, () => loadText("/f.log", { whole: true })), (err) => err.tooLarge && err.size === 60 * 1024 * 1024);
  assert.equal(got.pulled, 0);
  assert.equal(got.cancelled, true);
});

test("Show all with no size stops reading past 16 MiB", async () => {
  const { res, got } = served(200, {}, 60);
  await assert.rejects(withFetch(res, () => loadText("/f.log", { whole: true })), (err) => err.tooLarge);
  assert.ok(got.pulled <= MAX_TEXT_BYTES / (1024 * 1024) + 1, `read ${got.pulled} MiB`);
  assert.equal(got.cancelled, true);
});

test("a head the server answers whole (200) is read to 2 MiB, the rest on Show all", async () => {
  const { res, got } = served(200, { "content-length": String(5 * 1024 * 1024) }, 5);
  const out = await withFetch(res, () => loadText("/f.log"));
  assert.equal(out.whole, false);
  assert.equal(out.read, HEAD_BYTES);
  assert.equal(out.size, 5 * 1024 * 1024);
  assert.equal(got.cancelled, true);
});

test("a one-line file past the head opens at its first 10,000 characters", async () => {
  const { res } = served(206, { "content-range": `bytes 0-${HEAD_BYTES - 1}/${5 * 1024 * 1024}` }, 2);
  const out = await withFetch(res, () => loadText("/bundle.js"));
  assert.equal(out.whole, false);
  assert.equal(out.cut, true);
  assert.equal(out.text, `${"x".repeat(LINE_CAP)}…`);
});
