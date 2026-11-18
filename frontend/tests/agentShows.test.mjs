import test from "node:test";
import assert from "node:assert/strict";
import { createShows, landing } from "../src/chat/agentShows.js";

const action = (key, show) => ({ type: "action", key, action: { kind: "show", show } });

test("a show of the page the chat is beside lands at once, another waits for the reply's end", () => {
  const shows = createShows();
  assert.deepEqual(shows.action(action("p", { page_id: "p", pdf_page: 2 }), "p", "p"),
    { kind: "now", show: { page_id: "p", pdf_page: 2 } });
  // a folder chat's show of a page waits; a steer's done keeps it; the reply's own end lands it
  assert.equal(shows.action(action("f", { page_id: "q" }), "f", "")?.kind, "later");
  assert.equal(shows.done({ type: "done", key: "f" }, "f"), null);
  assert.deepEqual(shows.done({ type: "done", key: "f", ended: true }, "f"), { kind: "now", show: { page_id: "q" } });
  assert.equal(shows.done({ type: "done", key: "f", ended: true }, "f"), null); // once
});

test("a reply that was stopped, or whose chat is no longer on screen, shows nothing", () => {
  const shows = createShows();
  shows.action(action("f", { page_id: "q" }), "f", "");
  assert.equal(shows.done({ type: "done", key: "f", ended: false }, "f"), null);
  shows.action(action("f", { page_id: "q" }), "f", "");
  assert.equal(shows.done({ type: "done", key: "f", ended: true }, "other"), null);
  // a reply running on behind another chat: not even the page open beside the dock moves
  assert.equal(shows.action(action("f", { page_id: "p" }), "p", "p"), null);
  assert.equal(shows.action({ type: "action", key: "p", action: { kind: "show", error: true, show: { page_id: "p" } } },
    "p", "p"), null);
});

test("a show lands as the reply's links land", () => {
  assert.deepEqual(landing({ page_id: "p", block_id: "b" }), { block: "b", page: "p" });
  assert.deepEqual(landing({ page_id: "p", line: 40 }), { page: "p", target: { kind: "line", pageId: "p", line: 40 } });
  assert.deepEqual(landing({ page_id: "p", pdf_page: 3, quote: "the fit" }),
    { page: "p", target: { kind: "citation", pageId: "p", page: 3, quote: "the fit" } });
  assert.deepEqual(landing({ page_id: "p" }), { page: "p", target: null });
});
