import assert from "node:assert/strict";
import { test } from "node:test";
import * as q from "../src/chat/chatQueue.js";

let n = 0;
const fresh = () => `conversation-${++n}`;
const texts = (key) => q.queueOf(key).items.map((m) => `${m.text}${m.state === "steering" ? "*" : ""}`);
const queued = (key, ...words) => words.forEach((text) => q.enqueue(key, { id: text, text }));

test("the queue goes on after a reply that ended on its own, and waits after Stop until the user sends", () => {
  const key = fresh();
  queued(key, "a", "b");
  q.ended(key, true);
  assert.equal(q.next(key).text, "a");
  q.started(key);
  q.ended(key, false); // stopped
  assert.equal(q.next(key), null);
  assert.equal(q.queueOf(key).paused, true);
  q.started(key); // a message sent, from the composer or a row's Send
  q.ended(key, true);
  assert.equal(q.next(key).text, "b");
  assert.equal(q.next(key), null);
});

test("a message being steered in goes first, keeps its place, and is queued again when the reply ends without it", () => {
  const key = fresh();
  queued(key, "a", "b", "c");
  q.steer(key, "c");
  assert.deepEqual(texts(key), ["c*", "a", "b"]);
  // Nothing goes ahead of it, and it can't be moved.
  q.move(key, "b", 0);
  assert.deepEqual(texts(key), ["c*", "b", "a"]);
  q.move(key, "c", 3);
  assert.deepEqual(texts(key), ["c*", "b", "a"]);
  q.steer(key, "a");
  assert.deepEqual(texts(key), ["c*", "a*", "b"]);
  // The reply took one in; the other was still waiting when it ended.
  assert.deepEqual(q.landed(key, ["c"]).map((m) => m.text), ["c"]);
  q.ended(key, true);
  assert.deepEqual(texts(key), ["a", "b"]);
  assert.equal(q.next(key).text, "a");
});

test("a drop lands where useDragOrder says: before the slot at that index, or last", () => {
  const key = fresh();
  queued(key, "a", "b", "c");
  q.move(key, "a", 3);
  assert.deepEqual(texts(key), ["b", "c", "a"]);
  q.move(key, "a", 1);
  assert.deepEqual(texts(key), ["b", "a", "c"]);
  q.move(key, "c", 0);
  assert.deepEqual(texts(key), ["c", "b", "a"]);
});

test("Steer carries the text, the quoted passages and the pictures; files and attached blocks go as a message of their own", () => {
  const picture = { kind: "upload", url: "/api/uploads/x.png" };
  const item = {
    id: "m1", text: "focus on this ",
    parts: { images: [picture], selections: [{ text: "Line one\nline two" }], notes: [{ kind: "note", text: "my note" }] },
  };
  assert.equal(q.canSteer(item), true);
  assert.deepEqual(q.steerBody(item), {
    id: "m1", text: "focus on this\n\n> Line one\n> line two\n\n---\n\n> my note", images: [picture],
  });
  assert.equal(q.canSteer({ parts: { files: [{ name: "a.pdf" }] } }), false);
  assert.equal(q.canSteer({ parts: { notes: [{ kind: "block", text: "a block" }] } }), false);
  // The transcript shows it as a sent message reads: the passages' start
  // quoted, every line of it.
  const shown = q.steeredMessage([item, { text: "and be brief", parts: {} }], "u1");
  assert.equal(shown.text, "focus on this\n\n> Line one\n> line two\n>\n> ---\n>\n> my note\n\nand be brief");
  assert.deepEqual(shown.images, [picture]);
  assert.equal(shown.steered, true);
});

test("a quote of the transcript goes ahead of the message, as it is sent, steered and shown", () => {
  const item = { id: "m2", text: "why? ", parts: { quotes: ["It is $x^2$.\n\n- a point"] } };
  assert.equal(q.messageText(item.text, item.parts), "> It is $x^2$.\n>\n> - a point\n\nwhy?");
  assert.equal(q.steerBody(item).text, "> It is $x^2$.\n>\n> - a point\n\nwhy?");
  assert.equal(q.steeredMessage([item], "u2").text, "> It is $x^2$.\n>\n> - a point\n\nwhy?");
  assert.equal(q.canSteer(item), true);
  assert.equal(q.messageText("plain", {}), "plain");
});
