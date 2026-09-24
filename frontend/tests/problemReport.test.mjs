import test from "node:test";
import assert from "node:assert/strict";
import {
  buildReport, describeBrowser, describeFacts, formatDiagnostics, githubIssueUrl, issueTitle,
  scrubReportText, selectEvents, withRecording, NEW_ISSUE_URL,
} from "../src/support/problemReport.js";

const CHROME_LINUX = {
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
  userAgentData: { platform: "Linux", brands: [{ brand: "Not?A_Brand", version: "8" }, { brand: "Chromium", version: "129" }, { brand: "Google Chrome", version: "129" }] },
};
const SAFARI_IPAD = {
  userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
  maxTouchPoints: 5,
};
const FIREFOX_WIN = {
  userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
};

const facts = {
  build: { version: "1.4.2", commit: "abc1234", label: "v1.4.2 (abc1234)", frozen: false },
  browser: { nav: CHROME_LINUX, width: 1440, height: 900, dpr: 2, touch: false, standalone: false, language: "zh-CN" },
  view: { mode: "page", pdf: true, readOnly: false, phone: false, theme: "dark", uiScale: 1 },
  workspace: { kind: "shared", role: "editor" },
  events: [
    { t: Date.UTC(2026, 8, 24, 12, 0, 1), msg: "Ready.", tone: "" },
    { t: Date.UTC(2026, 8, 24, 12, 0, 2), msg: "API GET /api/pages → 500 in 12 ms", tone: "error" },
  ],
  server: {
    info: { label: "v1.4.2 (abc1234)", platform: "Linux x86_64", python: "3.12.3", uptime_seconds: 7200, log_counts: { info: 40, warning: 2, error: 0 } },
    lines: [
      { seq: 1, t: 1790000000, level: "INFO", msg: "[http] GET /api/session 200" },
      { seq: 2, t: 1790000001, level: "WARNING", msg: "download failed for token=abcdef12345 (quota)" },
    ],
  },
};

test("browser description names the brand, version and platform", () => {
  assert.equal(describeBrowser(CHROME_LINUX), "Google Chrome 129 on Linux x86_64");
  assert.equal(describeBrowser(SAFARI_IPAD), "Safari 17 on iPadOS");
  assert.equal(describeBrowser(FIREFOX_WIN), "Firefox 130 on Windows x86_64");
  assert.equal(describeBrowser({}), "unknown browser on unknown OS");
});

test("facts become one line each and never name the workspace", () => {
  const d = describeFacts(facts);
  assert.equal(d.build, "Gamma v1.4.2 (abc1234) · server");
  assert.equal(d.browser, "Google Chrome 129 on Linux x86_64 · 1440×900 @2x · zh-CN");
  assert.equal(d.view, "page · with a PDF · theme dark");
  assert.equal(d.workspace, "shared, editor");
  assert.equal(describeFacts({ build: { frozen: true, version: "1.4.2" } }).build, "Gamma v1.4.2 · desktop app");
  assert.equal(describeFacts({}).build, "Gamma development build · checkout");
});

test("recent events keep every warning or error plus the newest lines", () => {
  const events = [];
  for (let i = 0; i < 300; i++) events.push({ t: i, msg: `line ${i}`, tone: i % 50 === 0 ? "warn" : "" });
  const picked = selectEvents(events);
  assert.ok(picked.length <= 60);
  assert.ok(picked.some((e) => e.msg === "line 150"), "an old warning inside the window survives");
  assert.ok(!picked.some((e) => e.msg === "line 50"), "one before the window does not");
  assert.equal(picked.at(-1).msg, "line 299");
  assert.ok(picked.every((e, i) => i === 0 || picked[i - 1].t <= e.t), "time order");
});

test("the diagnostics block carries build, browser, view, events and the admin's server lines, scrubbed", () => {
  const text = formatDiagnostics(facts);
  assert.match(text, /\*\*Build:\*\* Gamma v1\.4\.2 \(abc1234\) · server/);
  assert.match(text, /\*\*View:\*\* page · with a PDF · theme dark · workspace: shared, editor/);
  assert.match(text, /12:00:02 \[ERROR\] API GET \/api\/pages → 500/);
  assert.match(text, /\*\*Server \(seen as admin\):\*\* Gamma v1\.4\.2 \(abc1234\) · Linux x86_64 · Python 3\.12\.3 · up 120 min · 2 warnings, 0 errors since start/);
  assert.match(text, /\[WARNING\] download failed for token=\*\*\* \(quota\)/);
  assert.ok(!text.includes("[http] GET /api/session"), "info lines of the server log stay home");
  const noServer = formatDiagnostics({ ...facts, server: null });
  assert.ok(!noServer.includes("Server (seen as admin)"));
  assert.match(formatDiagnostics({}), /\(nothing logged\)/);
});

test("scrubbing mirrors the server log's rules", () => {
  // Same as gamma/logbuf.py: the key: value rule runs after the bearer rule and eats the word too.
  assert.equal(scrubReportText("Authorization: Bearer abcdefghijklmnop"), "Authorization: *** ***");
  assert.equal(scrubReportText("key sk-proj-abcdefghijklmnop failed"), "key sk-*** failed");
  assert.equal(scrubReportText("api_key=verysecret&x=1"), "api_key=***&x=1");
  assert.equal(scrubReportText("/?share=AbCdEf123&page=p1"), "/?share=***&page=p1");
  assert.equal(scrubReportText("sess " + "a".repeat(43) + " end"), "sess *** end");
  assert.equal(scrubReportText("block 0123456789abcdef01234567 stays"), "block 0123456789abcdef01234567 stays");
});

test("the report has the description first and the diagnostics only when asked", () => {
  const full = buildReport({ description: "The blue line stays.\nSecond line.", steps: "Drag a block", facts });
  assert.ok(full.startsWith("### What happened\nThe blue line stays.\nSecond line.\n\n### How to reproduce\nDrag a block\n\n### Diagnostics\n"));
  const bare = buildReport({ description: "Nothing else", facts, includeDiagnostics: false });
  assert.equal(bare, "### What happened\nNothing else");
  assert.equal(buildReport({ facts, includeDiagnostics: false }), "### What happened\n(not described)");
});

test("a screen recording is named in the steps, of the report and of the form alike", () => {
  const note = "Screen recording: `gamma-recording-20260924-1200.webm` (dropped into this issue by the reporter).";
  assert.equal(withRecording("", "gamma-recording-20260924-1200.webm"), note);
  assert.equal(withRecording("drag a block\n", "gamma-recording-20260924-1200.webm"), `drag a block\n\n${note}`);
  assert.equal(withRecording("drag a block", ""), "drag a block");
  const report = buildReport({ description: "d", facts, includeDiagnostics: false, recording: "gamma-recording-20260924-1200.webm" });
  assert.equal(report, `### What happened\nd\n\n### How to reproduce\n${note}`);
  const { url } = githubIssueUrl({ description: "d", steps: "drag", recording: "gamma-recording-20260924-1200.webm" });
  assert.equal(new URL(url).searchParams.get("steps"), `drag\n\n${note}`);
});

test("the issue title is the description's first line, shortened", () => {
  assert.equal(issueTitle("  The blue line stays \n more"), "The blue line stays");
  assert.equal(issueTitle(""), "");
  const long = "x".repeat(100);
  assert.equal(issueTitle(long).length, 78);
  assert.ok(issueTitle(long).endsWith("…"));
});

test("the GitHub URL prefills the issue form's fields and trims the diagnostics to the URL budget", () => {
  const { url, trimmed } = githubIssueUrl({ description: "Blue line\nstays", steps: "drag", diagnostics: "**Build:** x" });
  assert.ok(url.startsWith(`${NEW_ISSUE_URL}?`));
  const params = new URL(url).searchParams;
  assert.equal(params.get("template"), "bug_report.yml");
  assert.equal(params.get("title"), "Blue line");
  assert.equal(params.get("description"), "Blue line\nstays");
  assert.equal(params.get("steps"), "drag");
  assert.equal(params.get("diagnostics"), "**Build:** x");
  assert.equal(trimmed, false);
  const big = githubIssueUrl({ description: "d", diagnostics: "line\n".repeat(5000) });
  assert.ok(big.trimmed);
  assert.ok(big.url.length <= 7600, `url is ${big.url.length}`);
  assert.match(new URL(big.url).searchParams.get("diagnostics"), /trimmed — the full report is on the reporter's clipboard\)$/);
  assert.equal(new URL(githubIssueUrl({}).url).searchParams.has("title"), false);
});
