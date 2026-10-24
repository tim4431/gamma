import test from "node:test";
import assert from "node:assert/strict";

// The page's half of the iPad app's opened files (src/transfers/openedFiles.js,
// docs/dev/ipad.md "Opened files"), against a stand-in shell that holds files
// as OpenedFiles.swift does: `files` lists them, `fileData` answers base64
// chunks (5 bytes here, so a file takes several), `fileDone` lets one go.
// The Swift half is ipad/GammaIPadTests/OpenedFilesTests.swift.
const held = [];
const posted = [];
const CHUNK = 5;
const hold = (id, name, text, type = "") => held.push({ id, name, type, bytes: new TextEncoder().encode(text) });
globalThis.document = { documentElement: { setAttribute() {} } };
globalThis.window = {
  matchMedia: () => ({ matches: false }),
  navigator: {},
  gammaNative: { platform: "ipad", version: "0.2.0", openFiles: true, queue: [], receive: () => Promise.resolve() },
  webkit: { messageHandlers: { gamma: { postMessage: (msg) => {
    posted.push(msg);
    if (msg.type === "files") {
      return Promise.resolve({ files: [...held.map(({ id, name, type, bytes }) => ({ id, name, type, size: bytes.length })),
        { id: 7, name: "not a file" }, { id: "x", name: "no size" }] });
    }
    if (msg.type === "fileData") {
      const file = held.find((f) => f.id === msg.id);
      if (!file) return Promise.resolve({});
      return Promise.resolve({ data: Buffer.from(file.bytes.subarray(msg.offset, msg.offset + CHUNK)).toString("base64") });
    }
    if (msg.type === "fileDone") {
      const i = held.findIndex((f) => f.id === msg.id);
      if (i >= 0) held.splice(i, 1);
      return Promise.resolve({});
    }
    return Promise.reject(new Error("Gamma for iPad does not understand this message."));
  } } } },
};
const opened = await import("../src/transfers/openedFiles.js");

test("the host says it holds opened files", () => {
  assert.equal(opened.hostOpensFiles(), true);
});

test("the shell's files are listed, read back whole over several chunks, and let go", async () => {
  hold("A", "Paper.pdf", "%PDF-1.4 a small paper", "application/pdf");
  hold("B", "Empty.md", "");
  const list = await opened.listOpenedFiles();
  assert.deepEqual(list, [
    { id: "A", name: "Paper.pdf", size: 22, type: "application/pdf" },
    { id: "B", name: "Empty.md", size: 0, type: "" },
  ], "an entry without its id, name or size is left out");
  posted.length = 0;
  const file = await opened.readOpenedFile(list[0]);
  assert.equal(file.name, "Paper.pdf");
  assert.equal(file.type, "application/pdf");
  assert.equal(await file.text(), "%PDF-1.4 a small paper");
  assert.deepEqual(posted.map((m) => m.offset), [0, 5, 10, 15, 20], "each read starts where the last ended");
  assert.equal(await (await opened.readOpenedFile(list[1])).text(), "", "an empty file needs no read");
  await opened.doneWithOpenedFile("A");
  await opened.doneWithOpenedFile("B");
  await opened.doneWithOpenedFile("gone"); // never throws
  assert.deepEqual(await opened.listOpenedFiles(), []);
  await assert.rejects(opened.readOpenedFile({ id: "A", name: "Paper.pdf", size: 22 }), /no longer holds/);
});

test("what an opened file becomes", () => {
  const kind = (name, type = "") => opened.openedKind({ name, type });
  assert.equal(kind("a.PDF"), "page");
  assert.equal(kind("download", "application/pdf"), "page");
  assert.equal(kind("notes.md"), "page");
  assert.equal(kind("notes.markdown"), "page");
  assert.equal(kind("README", "text/markdown"), "page");
  assert.equal(kind("Lecture.note"), "notability");
  assert.equal(kind("Lecture.ntb"), "notability");
  assert.equal(kind("vault.zip"), "archive");
  assert.equal(kind("export", "application/zip"), "archive");
  assert.equal(kind("photo.jpg", "image/jpeg"), "");
  assert.equal(kind("notes.txt", "text/plain"), "");
});

test("where opened files go: the workspace shown when writable, the personal one otherwise, and who else sees them", () => {
  const mine = { id: "p", name: "Alice", personal: true, role: "owner", members: 1, access: "private", mirror_of: "" };
  const lab = { id: "s", name: "Lab", personal: false, role: "editor", members: 3, access: "private", mirror_of: "" };
  const open = { id: "o", name: "Open", personal: false, role: "owner", members: 1, access: "public", mirror_of: "" };
  const clone = { id: "c", name: "NAS copy", personal: false, role: "owner", members: 1, access: "private", mirror_of: "nas" };
  const read = { id: "r", name: "Reading", personal: false, role: "viewer", members: 4, access: "private", mirror_of: "" };
  const all = [mine, lab, open, clone, read];
  assert.deepEqual(opened.placeOpenedFiles(mine, all), { ws: mine, audience: "", mine: null, switchTo: null });
  assert.deepEqual(opened.placeOpenedFiles(lab, all), { ws: lab, audience: "shared", mine, switchTo: null });
  assert.deepEqual(opened.placeOpenedFiles(open, all), { ws: open, audience: "shared", mine, switchTo: null }, "a public workspace is seen by every account");
  assert.deepEqual(opened.placeOpenedFiles(clone, all), { ws: clone, audience: "clone", mine, switchTo: null });
  assert.deepEqual(opened.placeOpenedFiles(read, all), { ws: mine, audience: "", mine: null, switchTo: mine }, "read-only: to the personal workspace");
  assert.equal(opened.placeOpenedFiles(read, [read]), null, "nowhere to write");
  assert.deepEqual(opened.placeOpenedFiles(lab, [lab]), { ws: lab, audience: "shared", mine: null, switchTo: null }, "no personal workspace to offer");
});
