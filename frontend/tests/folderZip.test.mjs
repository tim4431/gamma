import test from "node:test";
import assert from "node:assert/strict";
import { crc32, folderDrop, folderPick, zipFolder } from "../src/transfers/folderZip.js";

const encoder = new TextEncoder();
// A picked folder's file: the browser sets webkitRelativePath from the folder down.
function picked(path, text) {
  const file = new File([text], path.split("/").pop(), { lastModified: Date.UTC(2026, 9, 13, 12) });
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}

test("crc32 is zlib's, and continues across chunks", () => {
  assert.equal(crc32(encoder.encode("123456789")), 0xCBF43926);
  assert.equal(crc32(encoder.encode("6789"), crc32(encoder.encode("12345"))), 0xCBF43926);
  assert.equal(crc32(new Uint8Array()), 0);
});

test("a picked folder is named after its top folder and sized by its files", () => {
  const pick = folderPick([picked("My Library/My Library.rdf", "<rdf/>"), picked("My Library/files/7/a.pdf", "%PDF-1.4")]);
  assert.equal(pick.name, "My Library");
  assert.equal(pick.size, 14);
  assert.ok(pick.folder);
});

test("a dropped folder's files keep their paths from the folder down", () => {
  const pdf = new File(["%PDF-1.4"], "a.pdf");
  const pick = folderDrop([{ file: new File(["<rdf/>"], "My Library.rdf"), folder: ["My Library"] }, { file: pdf, folder: ["My Library", "files", "7"] }]);
  assert.equal(pick.name, "My Library");
  assert.deepEqual(pick.entries.map(({ path }) => path), ["My Library/My Library.rdf", "My Library/files/7/a.pdf"]);
  assert.equal(pick.entries[1].file, pdf);
});

test("the folder packs into a stored zip whose entries read back", async () => {
  const files = [picked("Lib/Lib.rdf", "<rdf/>"), picked("Lib/files/7/Fabry–Pérot.pdf", "%PDF-1.4 body"), picked("Lib/files/8/empty.pdf", "")];
  const seen = [];
  const zip = await zipFolder(folderPick(files), { onProgress: (read, total) => seen.push([read, total]) });
  assert.equal(zip.name, "Lib.zip");
  assert.deepEqual(seen.at(-1), [19, 19]);
  const bytes = new Uint8Array(await zip.arrayBuffer());
  const view = new DataView(bytes.buffer);
  const end = bytes.length - 22;
  assert.equal(view.getUint32(end, true), 0x06054B50);
  const count = view.getUint16(end + 10, true);
  let at = view.getUint32(end + 16, true);
  assert.equal(count, 3);
  const decoder = new TextDecoder();
  for (const file of files) {
    assert.equal(view.getUint32(at, true), 0x02014B50);
    const flags = view.getUint16(at + 8, true), method = view.getUint16(at + 10, true);
    const crc = view.getUint32(at + 16, true), size = view.getUint32(at + 24, true);
    const nameLength = view.getUint16(at + 28, true), offset = view.getUint32(at + 42, true);
    const name = decoder.decode(bytes.subarray(at + 46, at + 46 + nameLength));
    assert.equal(name, file.webkitRelativePath);
    assert.equal(flags, 0x800, "UTF-8 names");
    assert.equal(method, 0, "stored");
    // The local header carries the same record, then the file's bytes.
    assert.equal(view.getUint32(offset, true), 0x04034B50);
    assert.equal(view.getUint32(offset + 14, true), crc);
    const data = bytes.subarray(offset + 30 + nameLength, offset + 30 + nameLength + size);
    assert.equal(decoder.decode(data), await file.text());
    assert.equal(crc32(data), crc);
    at += 46 + nameLength;
  }
});

test("past 65,535 files the zip ends in ZIP64 records", async () => {
  const count = 0x10000;
  const zip = await zipFolder(folderPick(Array.from({ length: count }, (_, i) => picked(`Lib/${i}.txt`, ""))));
  const bytes = new Uint8Array(await zip.arrayBuffer());
  const view = new DataView(bytes.buffer);
  const end = bytes.length - 22, locator = end - 20, record = locator - 56;
  assert.equal(view.getUint32(end, true), 0x06054B50);
  assert.equal(view.getUint16(end + 10, true), 0xFFFF, "the classic count points at ZIP64");
  assert.equal(view.getUint32(locator, true), 0x07064B50);
  assert.equal(Number(view.getBigUint64(locator + 8, true)), record);
  assert.equal(view.getUint32(record, true), 0x06064B50);
  assert.equal(Number(view.getBigUint64(record + 4, true)), 44);
  assert.equal(Number(view.getBigUint64(record + 32, true)), count);
  // the central directory ends where the ZIP64 record begins
  assert.equal(Number(view.getBigUint64(record + 48, true)) + Number(view.getBigUint64(record + 40, true)), record);
});

test("packing stops when its signal fires", async () => {
  const ctl = new AbortController();
  ctl.abort();
  await assert.rejects(zipFolder(folderPick([picked("Lib/a.pdf", "x")]), { signal: ctl.signal }), (err) => err.aborted === true);
});
