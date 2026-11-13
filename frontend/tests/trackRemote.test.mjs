// What the tracking dialog says of a work folder of another server
// (explorer/trackRemote.js): what the choice uploads there, the secrets
// files ticked, and whether the dialog hands the window back to the shell.
import assert from "node:assert/strict";
import { test } from "node:test";
import { handsBack, secretsWarning, serverOf, uploadsText } from "../src/explorer/trackRemote.js";

const here = { id: "l1", kind: "folder", remote_url: "" };
const nas = { id: "l2", kind: "folder", remote_url: "https://nas.example:8443/" };

test("the server a link names, by its host", () => {
  assert.equal(serverOf("https://nas.example:8443/"), "nas.example:8443");
  assert.equal(serverOf("not a url"), "not a url");
  assert.equal(serverOf(undefined), "");
});

test("what the choice uploads to the other server and that its members read it, said only for its vault", () => {
  const preview = { files: 14, bytes: 9_000_000, large: 2, uploads: { files: 12, bytes: 3_565_158 } };
  assert.equal(uploadsText(nas, preview),
    "12 files, 3.4 MB, are uploaded to nas.example:8443 and stored there, where every member of the workspace can read them");
  assert.equal(uploadsText(nas, { uploads: { files: 1, bytes: 800 } }),
    "1 file, 800 B, is uploaded to nas.example:8443 and stored there, where every member of the workspace can read it");
  assert.match(uploadsText(nas, { uploads: { files: 3, bytes: 900 }, secrets: [] }), /every member of the workspace can read them$/,
    "said with no secrets file ticked too");
  assert.equal(uploadsText(here, preview), "", "a vault of this server uploads nothing");
  assert.equal(uploadsText(nas, { files: 3 }), "", "a preview that does not say");
  assert.equal(uploadsText(nas, null), "");
});

test("each secrets file ticked is named, for the other server's vault only", () => {
  const warning = secretsWarning(nas, { secrets: [".env", "config/id_rsa"] });
  assert.equal(warning.text, "2 ticked files may hold secrets, and every member of the workspace on nas.example:8443 can read them:");
  assert.deepEqual(warning.files, [".env", "config/id_rsa"]);
  assert.equal(secretsWarning(nas, { secrets: [".env.local"] }).text,
    "1 ticked file may hold secrets, and every member of the workspace on nas.example:8443 can read it:");
  assert.equal(secretsWarning(nas, { secrets: [] }), null);
  assert.equal(secretsWarning(nas, {}), null);
  assert.equal(secretsWarning(here, { secrets: [".env"] }), null);
});

test("the dialog hands the window back to the shell for another server's vault in the desktop app", () => {
  assert.equal(handsBack(nas, true), true);
  assert.equal(handsBack(nas, false), false, "a browser has no shell");
  assert.equal(handsBack(here, true), false, "this server's vault refreshes the page itself");
});
