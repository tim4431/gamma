import test from "node:test";
import assert from "node:assert/strict";
import {
  AGENT_MAX, NOTE_MAX, canWithdraw, firstComment, groupByFile, hasChoice, namesAgent, newestRequests, othersCount, pictureOf, readScope,
  sendAgent, sendBody,
} from "../src/feedback/feedbackView.js";

const W = { id: "w1", name: "paper" };

test("readScope reads the scope opened on, or its work folder once chosen and known", () => {
  const file = { kind: "file", id: "p1" };
  assert.equal(readScope(file, "scope", W), "p1");
  assert.equal(readScope(file, "work_folder", W), "w1");
  assert.equal(readScope(file, "work_folder", null), "p1", "the work folder is not known yet");
  assert.equal(hasChoice(file, W), true);
  assert.equal(hasChoice({ kind: "folder", id: "w1" }, W), false, "opened on the work folder itself: one side");
  assert.equal(hasChoice(file, null), false);
});

test("groupByFile keeps the document's order, one group per run of a file", () => {
  const entries = [{ id: "a", file: "main.pdf" }, { id: "b", file: "main.pdf" }, { id: "c", file: "figures/fig1.pdf" }];
  assert.deepEqual(groupByFile(entries).map((g) => [g.file, g.entries.map((e) => e.id)]),
    [["main.pdf", ["a", "b"]], ["figures/fig1.pdf", ["c"]]]);
  assert.deepEqual(groupByFile([]), []);
  assert.deepEqual(groupByFile(undefined), []);
});

test("newestRequests lists the newest by rev, at most n", () => {
  const list = [{ id: "a", rev: 1 }, { id: "b", rev: 3 }, { id: "c", rev: 2 }];
  assert.deepEqual(newestRequests(list).map((r) => r.id), ["b", "c", "a"]);
  assert.deepEqual(newestRequests(list, 2).map((r) => r.id), ["b", "c"]);
  assert.deepEqual(list.map((r) => r.id), ["a", "b", "c"], "the list given is not sorted in place");
});

test("canWithdraw: the requester's own request while it waits or runs", () => {
  assert.equal(canWithdraw({ by: "tim", state: "sent" }, "tim"), true);
  assert.equal(canWithdraw({ by: "tim", state: "running" }, "tim"), true);
  assert.equal(canWithdraw({ by: "tim", state: "done" }, "tim"), false);
  assert.equal(canWithdraw({ by: "tim", state: "failed", detail: "withdrawn" }, "tim"), false);
  assert.equal(canWithdraw({ by: "ann", state: "sent" }, "tim"), false, "another member's");
  assert.equal(canWithdraw({ by: "tim", state: "sent" }, ""), false, "no one signed in");
});

test("pictureOf prefers the region now, else as commented on", () => {
  assert.equal(pictureOf({ picture_url: "/now", before_url: "/before" }), "/now");
  assert.equal(pictureOf({ picture_url: null, before_url: "/before" }), "/before");
  assert.equal(pictureOf({ picture_url: null, before_url: null }), "");
});

test("firstComment names the agent over the author, and a tombstone", () => {
  assert.deepEqual(firstComment({ comments: [{ by: "tim", agent: "", body: "fix this" }] }), { who: "tim", body: "fix this", deleted: false });
  assert.deepEqual(firstComment({ comments: [{ by: "tim", agent: "Claude Code", body: "done", deleted: false }] }),
    { who: "Claude Code", body: "done", deleted: false });
  assert.deepEqual(firstComment({ comments: [{ by: "tim", agent: "", body: "", deleted: true }] }), { who: "tim", body: "", deleted: true });
  assert.deepEqual(firstComment({ comments: [] }), { who: "", body: "", deleted: false });
});

test("sendBody carries the minted id, the scope read and the trimmed note, cut to the cap", () => {
  assert.deepEqual(sendBody("r1", "w1", "  fix these  "), { id: "r1", scope: "w1", note: "fix these" });
  assert.equal(sendBody("r1", "w1", "x".repeat(NOTE_MAX + 5)).note.length, NOTE_MAX);
  assert.equal(sendBody("r1", "w1", undefined).note, "");
});

test("sendAgent reads the document's agent on this computer: ready, not ready, checking, not this Send's, or one to turn on", () => {
  const agent = (extra) => ({ agent: { name: "codex", label: "Codex", ready: true, problem: "", takes: true, why: "", ...extra }, agents_found: true });
  assert.deepEqual(sendAgent(agent()), { mode: "ready", label: "Codex", problem: "", why: "" });
  assert.equal(namesAgent(sendAgent(agent())), true);
  assert.deepEqual(sendAgent(agent({ ready: false, problem: "Codex is not on this computer." })),
    { mode: "not_ready", label: "Codex", problem: "Codex is not on this computer.", why: "" });
  assert.equal(sendAgent(agent({ ready: null })).mode, "checking");
  assert.equal(namesAgent(sendAgent(agent({ ready: null }))), true);
  const other = sendAgent(agent({ takes: false, why: "Only the person who keeps this folder on this computer can send to Codex." }));
  assert.equal(other.mode, "other");
  assert.equal(other.why, "Only the person who keeps this folder on this computer can send to Codex.");
  assert.equal(namesAgent(other), false);
  assert.equal(sendAgent({ agent: null, agents_found: true }, true).mode, "offer", "kept here, an agent found");
  assert.equal(sendAgent({ agent: null, agents_found: true }, false).mode, "", "not kept here");
  assert.equal(sendAgent({ agent: null, agents_found: false }, true).mode, "", "no agent on this computer");
  assert.equal(sendAgent({ entries: [] }, true).mode, "", "a server without local agents");
  assert.equal(sendAgent(null).mode, "");
  assert.equal(AGENT_MAX, 100);
});

test("othersCount counts the entries someone else opened", () => {
  const e = (by) => ({ id: by, comments: by === null ? [] : [{ by, agent: "", body: "x" }] });
  assert.equal(othersCount([e("tim"), e("ann"), e("tim"), e("ben")], "tim"), 2);
  assert.equal(othersCount([e("tim")], "tim"), 0);
  assert.equal(othersCount([], "tim"), 0);
  assert.equal(othersCount(undefined, "tim"), 0);
});
