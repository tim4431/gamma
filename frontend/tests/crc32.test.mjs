import test from "node:test";
import assert from "node:assert/strict";
import { crc32 } from "../src/shared/lib/crc32.js";

const encoder = new TextEncoder();

test("crc32 is zlib's, and continues across chunks", () => {
  assert.equal(crc32(encoder.encode("123456789")), 0xCBF43926);
  assert.equal(crc32(encoder.encode("6789"), crc32(encoder.encode("12345"))), 0xCBF43926);
  assert.equal(crc32(new Uint8Array()), 0);
});
