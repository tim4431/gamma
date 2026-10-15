import assert from "node:assert/strict";
import { test } from "node:test";
import { RECENT_SAVES, describeCandidate, describeResponse, formatBytes, saveLog, withRecent } from "../saveLog.js";

test("a save's log reads as one report: what it rests on, then each step with its time", () => {
  let t = Date.UTC(2026, 9, 15, 12, 0, 0);
  const log = saveLog({
    version: "0.2.0", browser: "Chrome/141", server: "https://gamma.example", workspace: "",
    tab: "https://www.science.org/doi/epdf/10.1126/science.adz9952",
    candidate: { kind: "doi", doi: "10.1126/science.adz9952", pdf_links: ["https://www.science.org/doi/pdf/x"] },
    title: "Fast, continuous, and coherent atom reloading",
  }, () => t);
  const info = console.info;
  console.info = () => {};
  try {
    log.step("POST /api/clip");
    t += 1850;
    log.step("← 400: no PDF");
  } finally { console.info = info; }
  assert.equal(log.text(), [
    "Gamma Connector 0.2.0 — save at 2026-10-15T12:00:00.000Z",
    "browser: Chrome/141",
    "server: https://gamma.example · workspace: (the account's default)",
    "tab: https://www.science.org/doi/epdf/10.1126/science.adz9952",
    "detected: doi · doi 10.1126/science.adz9952 · arXiv — · pdf_url — · 1 PDF link on the page",
    "title: Fast, continuous, and coherent atom reloading",
    "+0.0s POST /api/clip",
    "+1.9s ← 400: no PDF",
  ].join("\n"));
});

test("a detection and a download's answer, each on a line", () => {
  assert.equal(describeCandidate(null), "nothing");
  assert.equal(describeCandidate({ kind: "pdf", pdf_url: "https://x.example/a.pdf", is_pdf_tab: true, from_url: true }),
    "pdf · doi — · arXiv — · pdf_url https://x.example/a.pdf · the tab is a PDF · from the URL only (no content script)");
  assert.equal(describeResponse({ status: 403, type: "text/html; charset=UTF-8" }), "403 text/html");
  assert.equal(describeResponse({ status: 200, type: "application/pdf", size: 831_488, url: "https://a/x", landed: "https://a/x" }),
    "200 application/pdf · 812 KB");
  assert.equal(describeResponse({ status: 200, type: "text/html", size: 10, url: "https://a/x", landed: "https://a/login" }),
    "200 text/html · 10 B · ended at https://a/login");
  assert.equal(formatBytes(3 * 1024 * 1024), "3.0 MB");
  assert.equal(formatBytes(undefined), "");
});

test("the recent logs keep the newest few", () => {
  let recent = undefined;
  for (let i = 0; i < RECENT_SAVES + 2; i++) recent = withRecent(recent, `save ${i}`);
  assert.equal(recent.length, RECENT_SAVES);
  assert.equal(recent[0], `save ${RECENT_SAVES + 1}`);
});
