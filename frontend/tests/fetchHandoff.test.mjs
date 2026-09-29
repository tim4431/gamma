import test from "node:test";
import assert from "node:assert/strict";
import {
  connectorNote, continuePrompt, handoffHint, handoffState, pollDelay, replyHandoffs, shouldContinue, wallHeadline,
  watchNote,
} from "../src/chat/fetchHandoff.js";

const card = (id, source = `doi:${id}`) => ({ id, host: "journals.example.org", wall: "captcha", source });

test("a reply's requests come once each, in call order", () => {
  const a = card("a"), b = card("b");
  const actions = [
    { kind: "fetch", handoff: a }, { kind: "read" }, { kind: "fetch", handoff: { ...a } },
    { kind: "fetch", handoff: b }, null, { kind: "fetch", handoff: {} },
  ];
  assert.deepEqual(replyHandoffs(actions).map((h) => h.id), ["a", "b"]);
  assert.deepEqual(replyHandoffs(undefined), []);
});

test("the card's state follows the server and what the user did here", () => {
  assert.equal(handoffState(null), "loading");
  assert.equal(handoffState({ status: "waiting" }), "waiting");
  assert.equal(handoffState({ status: "waiting" }, { opened: true }), "opened");
  // The Connector took the tab: that wins over a local "opened".
  assert.equal(handoffState({ status: "waiting", watched: true }, { opened: true }), "watching");
  assert.equal(handoffState({ status: "done", watched: true }), "done");
  assert.equal(handoffState({ status: "dismissed" }), "dismissed");
  assert.equal(handoffState({ status: "expired" }), "gone");
  assert.equal(handoffState({ status: "gone" }), "gone");
});

test("the card asks the server often only while the user is at the page", () => {
  assert.equal(pollDelay("watching"), 2500);
  assert.equal(pollDelay("opened", { isLast: false }), 2500);
  assert.equal(pollDelay("waiting"), 10000);
  assert.equal(pollDelay("loading"), 10000);
  assert.equal(pollDelay("waiting", { isLast: false }), null, "older replies ask on focus only");
  for (const settled of ["done", "dismissed", "gone"]) assert.equal(pollDelay(settled), null);
});

test("the chat continues by itself only when it is safe to speak for the user", () => {
  const ok = { states: ["done"], sawWaiting: true, isLast: true, idle: true };
  assert.equal(shouldContinue(ok), true);
  assert.equal(shouldContinue({ ...ok, states: ["done", "dismissed"] }), true);
  assert.equal(shouldContinue({ ...ok, states: ["done", "watching"] }), false, "another request still open");
  assert.equal(shouldContinue({ ...ok, states: ["dismissed", "gone"] }), false, "nothing arrived");
  assert.equal(shouldContinue({ ...ok, sawWaiting: false }), false, "a reload of a finished card never resends");
  assert.equal(shouldContinue({ ...ok, isLast: false }), false, "the conversation moved on");
  assert.equal(shouldContinue({ ...ok, idle: false }), false, "answering, or a draft in the composer");
  assert.equal(shouldContinue({ ...ok, states: [] }), false);
});

test("the card says whether the PDF can come back by itself, and what to fix when not", () => {
  const origin = "http://localhost:9001";
  assert.match(connectorNote("ok", { origin }), /sends the PDF back from that tab by itself/);
  assert.match(connectorNote("signed-out", { origin }), /signed out of this server/);
  assert.match(connectorNote("other-account", { origin }), /another account/);
  // No answer: say where to point it — the page's own address.
  assert.match(connectorNote("missing", { origin }), /reload it after an update, and set it to http:\/\/localhost:9001/);
  assert.match(connectorNote("desktop", { origin }), /default browser/);
  for (const s of ["missing", "signed-out", "other-account", "unreachable", "desktop"]) {
    assert.match(connectorNote(s, { origin }), /drop it here/, `${s} leaves the manual way`);
  }
  assert.equal(connectorNote("unknown", { origin }), "", "nothing while it asks");
});

test("a watched tab's card says what the Connector is doing there", () => {
  assert.match(watchNote("looking"), /doesn't see the paper's PDF link/);
  assert.match(watchNote("opening"), /opening the PDF in that tab/);
  assert.match(watchNote("signin"), /sign in on that page/);
  assert.match(watchNote("refused"), /drop it here/);
  assert.equal(watchNote(""), "");
  assert.equal(watchNote("anything else"), "");
});

test("the texts name the host, the wall and what arrived", () => {
  assert.equal(wallHeadline("captcha", "www.science.org"), "www.science.org asked for a CAPTCHA or bot check");
  assert.equal(wallHeadline("login", "h"), "h asked to sign in");
  assert.equal(wallHeadline("abstract", "h"), "h showed only the article page");
  assert.equal(wallHeadline("rate", "h"), "h is limiting Gamma's requests");
  assert.equal(wallHeadline("denied", "h"), "h refused Gamma's download");
  assert.equal(wallHeadline("script", "h"), "h only shows the paper in a browser");
  assert.match(handoffHint("done", { pages: 19 }), /19 pages/);
  assert.match(handoffHint("done", { pages: 1 }), /\(1 page\)/);
  assert.match(handoffHint("watching"), /Gamma Connector is watching/);
  assert.equal(handoffHint("waiting"), "Open the page and sign in or pass the check there.");
  assert.equal(handoffHint("opened"), "Finish in the tab that opened.");
  assert.equal(continuePrompt([card("10.1/x"), { id: "u", host: "lab.example" }]),
    "I got it in my browser — doi:10.1/x, lab.example is available now. Please continue.");
});
