import test from "node:test";
import assert from "node:assert/strict";
import { summarizeNotices, dotTone } from "../src/app/notices.js";

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
