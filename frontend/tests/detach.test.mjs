// Stop syncing's words (explorer/detach.js): the menu row with and without
// a computer named, why one may not, the dialog's counts, choices, the
// directory's line and a computer away, and the status after each choice.
import assert from "node:assert/strict";
import { test } from "node:test";
import { detachLabel, detachRefusal, detachSaid, detachText } from "../src/explorer/detach.js";

const plan = { may: true, files: 12, in_library: 2, too_large: 1, bytes: 3 * 1048576, computer: { name: "TIM-PC", online: true } };

test("the menu row names the computer that keeps the folder, when the account is told it", () => {
  assert.equal(detachLabel(plan), "Stop syncing with TIM-PC…");
  assert.equal(detachLabel({ ...plan, computer: null }), "Stop syncing…", "another account's computer, or none");
  assert.equal(detachLabel(undefined), "Stop syncing…", "the plan not read yet");
});

test("a refusal says who may, in the rule's words", () => {
  assert.equal(detachRefusal({ may: false, why: "keeper" }),
    "Only the person whose computer keeps it, or the workspace's owner, can stop syncing it.");
  assert.equal(detachRefusal({ may: false, why: "maker" }), "Only the person whose computer keeps it can stop syncing it.");
  assert.match(detachRefusal({ may: false, why: "elsewhere" }), /^This workspace is kept in step with another/);
  assert.equal(detachRefusal({ may: false }), detachRefusal({ may: false, why: "keeper" }), "a server that names no rule");
});

test("the dialog says what it does, the files, what each choice does and that the directory stays", () => {
  const text = detachText(plan, "paper");
  assert.equal(text.title, "Stop syncing “paper” with TIM-PC?");
  assert.equal(text.counts, "Gamma stops syncing it with TIM-PC. It has 12 files. 2 of them are in your library and stay there either way.");
  assert.equal(text.keep, "Each file becomes an ordinary page, filed where it is. A copy of each file is stored in Gamma (3.0 MB). "
    + "1 file is too large to store: it stays listed by name only.");
  assert.equal(text.remove, "10 files go to Recently deleted.");
  assert.equal(text.disk, "The directory on TIM-PC stays as it is: only Gamma's own .gamma folder goes.");
  assert.equal(text.offline, "");

  const bare = detachText({ may: true, files: 1, in_library: 1, too_large: 0, bytes: 0, computer: null }, "lab");
  assert.equal(bare.title, "Stop syncing “lab”?");
  assert.equal(bare.counts, "Gamma stops syncing it. It has 1 file. 1 of them is in your library and stays there either way.");
  assert.equal(bare.keep, "Each file becomes an ordinary page, filed where it is.", "nothing to store: its server holds the bytes");
  assert.equal(bare.remove, "No file goes to Recently deleted: every one is in your library.");
  assert.equal(bare.disk, "The directory on the computer stays as it is: only Gamma's own .gamma folder goes.");
  assert.equal(detachText({ files: 0 }, "new").counts, "Gamma stops syncing it. It has no files yet.");
  assert.match(detachText({ ...plan, too_large: 2 }, "paper").keep, /2 files are too large to store: they stay listed by name only\.$/);
});

test("a computer away is said to forget the folder when it is next online", () => {
  const text = detachText({ ...plan, computer: { name: "TIM-PC", online: false } }, "paper");
  assert.equal(text.offline, "TIM-PC is offline: it stops syncing the folder when it is next online.");
});

test("the status after each choice", () => {
  assert.equal(detachSaid("paper", "keep", { ok: true, kept: 12, trashed: 0 }), "Stopped syncing “paper”: its files stay in Gamma as pages.");
  assert.equal(detachSaid("paper", "remove", { ok: true, kept: 2, trashed: 10 }), "Stopped syncing “paper”: 10 files went to Recently deleted.");
  assert.equal(detachSaid("paper", "remove", { ok: true, kept: 1, trashed: 1 }), "Stopped syncing “paper”: 1 file went to Recently deleted.");
  assert.equal(detachSaid("paper", "remove", { ok: true, kept: 1, trashed: 0 }), "Stopped syncing “paper”: no file went to Recently deleted.");
});
