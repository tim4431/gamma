// node --test tests/sha256.test.mjs (from frontend/) — the pure SHA-256
// (shared/lib/sha256.js) gives hashlib's hex, and utils.js's sha256 and
// getDocIdForUrl, which name a PDF opened by URL and an ink upload, give the
// server's names where crypto.subtle is missing (plain HTTP).
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { register } from "node:module";
import { test } from "node:test";
import { sha256Hex } from "../src/shared/lib/sha256.js";

const utf8 = (s) => new TextEncoder().encode(s);
const nodeHex = (bytes) => createHash("sha256").update(bytes).digest("hex");

test("sha256Hex: the FIPS 180-4 vectors, every padding edge, UTF-8, a large body and random bytes match node:crypto", () => {
  assert.equal(sha256Hex(utf8("")), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  assert.equal(sha256Hex(utf8("abc")), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  // 56 bytes: the length no longer fits the first block, so a second one carries it
  assert.equal(sha256Hex(utf8("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")),
    "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  for (const n of [1, 55, 56, 57, 63, 64, 65, 119, 120, 128]) {
    const bytes = utf8("x".repeat(n));
    assert.equal(sha256Hex(bytes), nodeHex(bytes), `${n} bytes`);
  }
  const url = utf8("https://例え.jp/論文 ✓ 🦉.pdf?q=é");
  assert.equal(sha256Hex(url), nodeHex(url));
  const large = utf8("stroke ".repeat(150_000) + "末");   // past 1 MB, as an ink body can be
  assert.ok(large.length > 1_000_000);
  assert.equal(sha256Hex(large), nodeHex(large));
  for (let i = 0; i < 40; i++) {
    const bytes = new Uint8Array(randomBytes(i * 37));
    assert.equal(sha256Hex(bytes), nodeHex(bytes), `${bytes.length} random bytes`);
  }
});

test("utils' sha256 and getDocIdForUrl give the server's names with or without crypto.subtle", async (t) => {
  // shared/lib/utils.js imports without extensions and reads `window` as it loads
  register("data:text/javascript," + encodeURIComponent(`
    export async function resolve(specifier, context, next) {
      try { return await next(specifier, context); } catch (err) {
        if (err?.code === "ERR_MODULE_NOT_FOUND" && /^\\.\\.?\\//.test(specifier) && !/\\.[a-z]+$/.test(specifier)) return next(specifier + ".js", context);
        throw err;
      }
    }`));
  globalThis.window = globalThis;
  globalThis.location = new URL("http://192.168.1.20:9001/");
  globalThis.dispatchEvent = () => true;
  const { sha256, getDocIdForUrl } = await import("../src/shared/lib/utils.js");
  // storage.digest_id(hashlib.sha256(url.encode())), the id routers/pdf.py gives a PDF it fetches by URL
  const server = {
    "https://arxiv.org/pdf/1706.03762v7": "849b16adf0d34091048b5128",
    "https://例え.jp/論文 ✓.pdf?q=é": "76853c16445f4524f5492510",
  };
  const ink = '{"v":2,"strokes":["✓"]}';
  const check = async () => {
    for (const [url, id] of Object.entries(server)) assert.equal(await getDocIdForUrl(url), id);
    assert.equal(await sha256(ink), nodeHex(utf8(ink)));   // an ink upload's name, inkStore's uploadName
  };
  assert.ok(globalThis.crypto?.subtle);
  await check();
  // an insecure context: crypto is there (getRandomValues works), crypto.subtle is not
  const saved = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  t.after(() => Object.defineProperty(globalThis, "crypto", saved));
  const { getRandomValues } = globalThis.crypto;
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: { getRandomValues: getRandomValues.bind(globalThis.crypto) } });
  assert.equal(globalThis.crypto.subtle, undefined);
  await check();
});
