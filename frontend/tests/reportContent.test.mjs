// Reporting content (src/support/reportContent.js): the reasons the server
// takes, the body each kind sends, what can be reported, the quote, and
// the one-slot store the surfaces open and the dialog closes.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  closeReport, openReport, REPORT_REASONS, reportable, reportAuthor, reportBody, reportPreview, reportTarget,
  reportTitle, subscribeReport,
} from "../src/support/reportContent.js";

test("the reasons are the server's, in its order", () => {
  const server = readFileSync(new URL("../../backend/gamma/reports.py", import.meta.url), "utf8");
  const listed = server.match(/^REASONS = \(([^)]*)\)/m)[1].match(/"([a-z]+)"/g).map((s) => s.slice(1, -1));
  assert.deepEqual(REPORT_REASONS.map(([value]) => value), listed);
});

test("each kind sends what the server does not read itself", () => {
  assert.deepEqual(reportBody({ kind: "comment", commentId: "c1", text: "rude", author: "ann" }, "spam", "  again "),
    { kind: "comment", reason: "spam", details: "again", comment_id: "c1" });
  assert.deepEqual(reportBody({ kind: "page", pageId: "p1", title: "Notes", link: "https://a-pages.example/x" }, "other"),
    { kind: "page", reason: "other", details: "", page_id: "p1", link: "https://a-pages.example/x" });
  assert.deepEqual(reportBody({ kind: "page", pageId: "p1", title: "Notes", link: "" }, "other"),
    { kind: "page", reason: "other", details: "", page_id: "p1" });
  const ai = reportBody({ kind: "ai", text: "x".repeat(9000), model: "gpt-x" }, "harmful", "y".repeat(3000));
  assert.equal(ai.content.length, 8000);
  assert.equal(ai.details.length, 2000);
  assert.equal(ai.model, "gpt-x");
});

test("what can be reported, and how it is quoted", () => {
  assert.ok(reportable({ kind: "comment", commentId: "c1" }));
  assert.ok(!reportable({ kind: "comment" }));
  assert.ok(reportable({ kind: "page", pageId: "p1" }));
  assert.ok(!reportable({ kind: "ai", text: "   " }));
  assert.ok(!reportable({ kind: "user", pageId: "p1" }));
  assert.ok(!reportable(null));
  assert.equal(reportPreview({ kind: "page", title: "Reading\n notes" }), "Reading notes");
  assert.equal(reportPreview({ kind: "ai", text: "a".repeat(400) }).length, 280);
  assert.equal(reportAuthor({ kind: "ai", text: "x", model: "gpt-x" }), "Written by gpt-x");
  assert.equal(reportAuthor({ kind: "comment", author: "ann" }), "By ann");
  assert.equal(reportAuthor({ kind: "page", pageId: "p" }), "");
  assert.equal(reportTitle({ kind: "ai" }), "Report this reply");
});

test("the store opens on a reportable target and closes once", () => {
  const seen = [];
  const stop = subscribeReport(() => seen.push(reportTarget()?.kind || null));
  assert.equal(openReport({ kind: "ai", text: "" }), false);
  assert.equal(reportTarget(), null);
  const target = { kind: "comment", commentId: "c1", text: "rude" };
  assert.equal(openReport(target), true);
  assert.notEqual(reportTarget(), target, "a copy");
  assert.equal(reportTarget().commentId, "c1");
  closeReport();
  closeReport();
  stop();
  openReport({ kind: "page", pageId: "p1" });
  closeReport();
  assert.deepEqual(seen, ["comment", null]);
});
