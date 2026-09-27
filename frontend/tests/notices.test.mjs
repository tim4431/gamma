import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { summarizeNotices, dotTone, noticeText, noticeAction, NOTICE_MESSAGES } from "../src/app/notices.js";

test("nothing to see: no tone, no panes, nowhere to jump", () => {
  assert.deepEqual(summarizeNotices([]), { tone: "", panes: {}, firstPane: null });
  assert.deepEqual(summarizeNotices(null), { tone: "", panes: {}, firstPane: null });
});

test("the strongest notice sets the tone and the landing pane", () => {
  const s = summarizeNotices([
    { id: "update", tone: "warn", pane: "server" },
    { id: "backup", tone: "error", pane: "backups" },
    { id: "hint", tone: "info", pane: "account" },
  ]);
  assert.equal(s.tone, "error");
  assert.equal(s.firstPane, "backups");
  assert.deepEqual(s.panes, { server: "warn", backups: "error", account: "info" });
});

test("a pane keeps its strongest notice; ties keep the server's order", () => {
  const s = summarizeNotices([
    { id: "a", tone: "warn", pane: "server" },
    { id: "b", tone: "error", pane: "server" },
    { id: "c", tone: "error", pane: "backups" },
  ]);
  assert.deepEqual(s.panes, { server: "error", backups: "error" });
  assert.equal(s.firstPane, "server");
});

test("the dot is red for anything needing attention, accent for info", () => {
  assert.equal(dotTone("error"), "alert");
  assert.equal(dotTone("warn"), "alert");
  assert.equal(dotTone("info"), "info");
  assert.equal(dotTone(""), "");
});

test("a notice's sentence is its message with the params filled in, else the server's title", () => {
  assert.equal(noticeText({ id: "storage", title: "x", message: "Your storage is nearly full ({used} of {quota} MB used)", params: { used: 95, quota: 100 } }),
    "Your storage is nearly full (95 of 100 MB used)");
  // a message this build does not know (a newer server) shows as sent
  assert.equal(noticeText({ id: "new", title: "Something new", message: "Something {new}", params: { new: 1 } }), "Something new");
  assert.equal(noticeText({ id: "old", title: "An older server's notice" }), "An older server's notice");
});

test("each notice's action names what its pane does, with a plain fallback", () => {
  assert.equal(noticeAction({ id: "storage" }), "Review storage");
  assert.equal(noticeAction({ id: "backup-failed" }), "Review backups");
  assert.equal(noticeAction({ id: "something-else" }), "Open settings");
});

// Every string literal among the arguments of the server's notice(...) calls
// that reads as a sentence (it has a space): the messages it can send.
function serverMessages(src) {
  const out = new Set();
  const re = /(?<![\w.]|def )notice\(/g;
  let m;
  while ((m = re.exec(src))) {
    let depth = 1, i = m.index + m[0].length;
    while (i < src.length && depth > 0) {
      const c = src[i];
      if (c === '"' || c === "'") {
        let j = i + 1, text = "";
        while (j < src.length && src[j] !== c) { if (src[j] === "\\") { text += src[j + 1]; j += 2; } else text += src[j++]; }
        if (text.includes(" ")) out.add(text);
        i = j + 1;
        continue;
      }
      if (c === "(") depth++;
      else if (c === ")") depth--;
      i++;
    }
  }
  return out;
}

test("the catalog lists exactly the messages gamma/notices.py sends", async () => {
  const src = await readFile(new URL("../../backend/gamma/notices.py", import.meta.url), "utf8");
  const sent = serverMessages(src);
  assert.ok(sent.size >= 10, `found the server's messages (${sent.size})`);
  assert.deepEqual([...sent].filter((m) => !NOTICE_MESSAGES.includes(m)), [], "every server message is in NOTICE_MESSAGES");
  assert.deepEqual(NOTICE_MESSAGES.filter((m) => !sent.has(m)), [], "NOTICE_MESSAGES has no stale entry");
});
