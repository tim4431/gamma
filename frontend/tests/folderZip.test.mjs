import test from "node:test";
import assert from "node:assert/strict";
import { crc32 } from "../src/shared/lib/crc32.js";
import { filesUpload, folderDrop, folderPick, withoutHidden, zipFolder } from "../src/transfers/folderZip.js";
import { zipEntryNames } from "../src/transfers/zipNames.js";

// A picked folder's file: the browser sets webkitRelativePath from the folder down.
function picked(path, text) {
  const file = new File([text], path.split("/").pop(), { lastModified: Date.UTC(2026, 9, 13, 12) });
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}

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

test("picked files pack as a folder of the given name; a name picked twice is numbered", async () => {
  const files = [new File(["a"], "Lecture.note"), new File(["bb"], "Lecture.note"), new File(["c"], "lecture.NOTE"),
    new File(["d"], "README"), new File(["e"], "README"), new File(["f"], "Library.zip")];
  const pick = filesUpload(files, "Notability notes");
  assert.equal(pick.name, "Notability notes");
  assert.equal(pick.size, 7);
  assert.ok(pick.folder && pick.picked, "packed like a folder, shown as picked files");
  assert.deepEqual(pick.entries.map(({ path }) => path), ["Notability notes/Lecture.note", "Notability notes/Lecture (2).note",
    "Notability notes/lecture (3).NOTE", "Notability notes/README", "Notability notes/README (2)", "Notability notes/Library.zip"]);
  assert.equal(pick.entries[1].file, files[1]);
  assert.equal((await zipFolder(pick)).name, "Notability notes.zip");
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

test("a picked vault leaves out every path under a name starting with a dot, its own name aside", () => {
  const pick = withoutHidden(folderPick([picked(".vault/.obsidian/app.json", "{}"), picked(".vault/Note.md", "# Note"),
    picked(".vault/.git/HEAD", "ref"), picked(".vault/Sub/.trash/Old.md", "old"), picked(".vault/Sub/Kept.md", "kept"),
    picked(".vault/.hidden.md", "x")]));
  assert.equal(pick.name, ".vault");
  assert.deepEqual(pick.entries.map(({ path }) => path), [".vault/Note.md", ".vault/Sub/Kept.md"]);
  assert.equal(pick.size, 10);
});

test("a zip's entry names read back from its central directory, and a file that is no zip has none", async () => {
  const pick = folderPick([picked("Vault/.obsidian/app.json", "{}"), picked("Vault/Notes/Ünïcode note.md", "# Note")]);
  assert.deepEqual(await zipEntryNames(await zipFolder(pick)), ["Vault/.obsidian/app.json", "Vault/Notes/Ünïcode note.md"]);
  // after a comment the end record is still found
  const zip = new Uint8Array(await (await zipFolder(pick)).arrayBuffer());
  const comment = new TextEncoder().encode("made by hand");
  const commented = new Uint8Array(zip.length + comment.length);
  commented.set(zip);
  commented.set(comment, zip.length);
  new DataView(commented.buffer).setUint16(zip.length - 2, comment.length, true);
  assert.deepEqual(await zipEntryNames(new File([commented], "c.zip")), ["Vault/.obsidian/app.json", "Vault/Notes/Ünïcode note.md"]);
  assert.deepEqual(await zipEntryNames(new File(["not a zip at all, only text"], "x.zip")), []);
  assert.deepEqual(await zipEntryNames(new File([zip.slice(0, zip.length - 30)], "cut.zip")), [], "a cut zip holds nothing");
  assert.deepEqual(await zipEntryNames(null), []);
});
