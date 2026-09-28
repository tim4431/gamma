// Two copies of one conversation (two tabs, two members) never overwrite
// each other: the session's saves are conditional on the version it last
// read or wrote, a 409 brings the stored copy, the two are merged and saved
// again (routers/chats.py, docs/dev/ai.md "Chat history"). Failed saves
// are retried when worth it and otherwise surface as `saveError`.
import assert from "node:assert/strict";
import { test } from "node:test";
import { createChatSession, mergeChats } from "../src/chat/chatSession.js";

const msg = (id, text, extra = {}) => ({ id, role: id.startsWith("q") ? "user" : "ai", text, ...extra });
const conflict = (messages, updatedAt) => Object.assign(new Error("the conversation changed"),
  { status: 409, data: { messages, updated_at: updatedAt, title: "" } });
const noWait = { wait: async () => {} };

// A fake server: one stored conversation and its version.
function server(initial = [], at = "v0") {
  const s = { messages: initial, at, n: 0, calls: [] };
  s.save = async (key, messages, seen) => {
    s.calls.push({ messages, seen });
    if (seen !== s.at) throw conflict(s.messages, s.at);
    s.messages = messages;
    s.at = `v${++s.n}`;
    return { updated_at: s.at };
  };
  return s;
}

test("mergeChats keeps what both copies added, each after what it follows", () => {
  const base = [msg("q1", "shared"), msg("a1", "shared answer")];
  const ours = [...base, msg("q2", "mine"), msg("a2", "my answer")];
  const theirs = [...base, msg("q3", "theirs"), msg("a3", "their answer")];
  assert.deepEqual(mergeChats(base, ours, theirs).map((m) => m.id), ["q1", "a1", "q2", "a2", "q3", "a3"]);
  // Our newer version of a streamed reply wins; theirs of a message we left alone does.
  const partial = [...base, msg("q2", "mine"), msg("a2", "my ans", { partial: true })];
  const stored = [...partial, msg("q3", "theirs")];
  const final = [...base, msg("q2", "mine"), msg("a2", "my answer, done")];
  assert.deepEqual(mergeChats(partial, final, stored).map((m) => m.text),
    ["shared", "shared answer", "mine", "my answer, done", "theirs"]);
  const edited = [msg("q1", "shared"), msg("a1", "their edit of it")];
  assert.equal(mergeChats(base, base, edited)[1].text, "their edit of it");
  // A message we dropped since the base (edit-and-resend) stays dropped;
  // the ones only they have stay.
  const resent = [msg("q1", "shared"), msg("q9", "asked again")];
  assert.deepEqual(mergeChats(base, resent, theirs).map((m) => m.id), ["q1", "q9", "q3", "a3"]);
  // Messages without ids match by content.
  const plain = [{ role: "user", text: "old" }];
  assert.deepEqual(mergeChats(plain, [...plain, { role: "ai", text: "x" }], [...plain, { role: "user", text: "y" }]),
    [{ role: "user", text: "old" }, { role: "ai", text: "x" }, { role: "user", text: "y" }]);
});

test("a save from an older copy merges the stored one in and saves again", async () => {
  const q1 = [msg("q1", "question")];
  const srv = server(q1, "v0");
  const session = createChatSession(srv.save, noWait);
  session.seen("page", q1, "v0");
  // Meanwhile another tab asked something and saved.
  srv.messages = [...q1, msg("q7", "other tab")];
  srv.at = "v1";
  session.start("page", [...q1, msg("q2", "mine")], "", new AbortController());
  const reply = [...q1, msg("q2", "mine"), msg("a2", "answer")];
  await session.update("page", reply, true);
  session.finish("page");
  assert.deepEqual(srv.messages.map((m) => m.id), ["q1", "q2", "a2", "q7"]);
  assert.deepEqual(session.getSnapshot().replies.get("page").messages, srv.messages);
  assert.equal(session.isSaved("page"), true);
  assert.equal(session.version("page"), srv.at);
  assert.equal(session.saveError("page"), "");
});

test("a reply still streaming keeps what a merge brought in", async () => {
  const srv = server([], "v0");
  const session = createChatSession(srv.save, noWait);
  session.seen("page", [], "v0");
  srv.messages = [msg("q5", "from the other tab")];
  srv.at = "v1";
  session.start("page", [msg("q1", "mine")], "", new AbortController());
  await session.flush("page");
  // (ours follows nothing both have, so it goes after theirs)
  assert.deepEqual(session.getSnapshot().replies.get("page").messages.map((m) => m.id), ["q5", "q1"]);
  // The stream keeps handing in lists built from what it started with.
  session.update("page", [msg("q1", "mine"), msg("a1", "partial", { partial: true })]);
  assert.deepEqual(session.getSnapshot().replies.get("page").messages.map((m) => m.id), ["q5", "q1", "a1"]);
  await session.update("page", [msg("q1", "mine"), msg("a1", "final")], true);
  session.finish("page");
  assert.deepEqual(srv.messages.map((m) => [m.id, m.text]),
    [["q5", "from the other tab"], ["q1", "mine"], ["a1", "final"]]);
});

test("when the stored copy already holds everything, nothing is written again", async () => {
  const mine = [msg("q1", "question"), msg("a1", "answer")];
  const srv = server(mine, "v3");
  const session = createChatSession(srv.save, noWait);
  session.seen("page", [], "v0"); // stale: another tab saved the same messages
  session.start("page", mine, "", new AbortController());
  await session.flush("page");
  assert.equal(srv.calls.length, 1);
  assert.equal(session.version("page"), "v3");
  assert.equal(session.saveError("page"), "");
});

test("server and network failures are retried; others surface at once", async () => {
  let failures = 2;
  const saved = [];
  const waits = [];
  const session = createChatSession(async (key, messages) => {
    if (failures-- > 0) throw Object.assign(new Error("Bad gateway"), { status: 502 });
    saved.push(messages);
    return { updated_at: "v1" };
  }, { wait: async (ms) => { waits.push(ms); }, retryDelays: [5, 10, 20] });
  session.start("home", [msg("q1", "hi")], "", new AbortController());
  await session.flush("home");
  assert.deepEqual(waits, [5, 10]);
  assert.equal(saved.length, 1);
  assert.equal(session.saveError("home"), "");

  const refused = createChatSession(async () => { throw Object.assign(new Error("you can only view this workspace"), { status: 403 }); }, noWait);
  refused.start("home", [msg("q1", "hi")], "", new AbortController());
  await assert.rejects(refused.flush("home"), /only view/);
  assert.equal(refused.saveError("home"), "you can only view this workspace");
  assert.equal(refused.getSnapshot().failed.get("home"), "you can only view this workspace");
});

test("a failure that outlasts the retries stays visible until a save goes through", async () => {
  let down = true;
  const session = createChatSession(async () => {
    if (down) throw new TypeError("Failed to fetch");
    return { updated_at: "v1" };
  }, { wait: async () => {}, retryDelays: [1, 1] });
  session.start("home", [msg("q1", "hi")], "", new AbortController());
  await assert.rejects(session.update("home", [msg("q1", "hi"), msg("a1", "answer")], true), /Failed to fetch/);
  session.finish("home");
  assert.equal(session.saveError("home"), "Failed to fetch");
  assert.equal(session.isSaved("home"), false);
  down = false;
  await session.flush("home");
  assert.equal(session.saveError("home"), "");
  assert.equal(session.isSaved("home"), true);
  session.forget("home");
  assert.equal(session.version("home"), "");
});
