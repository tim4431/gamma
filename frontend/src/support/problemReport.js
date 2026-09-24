// The problem report: what "Report a problem" (account menu, Settings →
// Diagnostics) gathers about this browser, this server and the last few
// minutes, and how it becomes a GitHub issue. Pure functions — the dialog
// (ReportProblem.jsx) collects the facts and calls these; tests feed them
// fakes. Nothing here reads notes, files or names: the report names builds,
// the browser, the kind of view open and the app's own log lines, and every
// line is scrubbed for secret-shaped text before it leaves the page.

export const REPO = "tim4431/gamma";
export const ISSUES_URL = `https://github.com/${REPO}/issues`;
export const NEW_ISSUE_URL = `${ISSUES_URL}/new`;

// GitHub accepts an issue URL of about 8 KB; the form's own fields (title,
// template name) and encoding overhead sit inside this budget.
const URL_BUDGET = 7600;
// Recent events: every warning or error among the last EVENT_WINDOW log
// lines plus the newest EVENT_TAIL lines of any tone, at most EVENT_CAP.
const EVENT_WINDOW = 200;
const EVENT_TAIL = 25;
const EVENT_CAP = 60;
const SERVER_LINES = 20;

// Mirrors gamma/logbuf.py's scrub rules: bearer tokens, provider keys,
// key=value pairs of secret-named keys, long urlsafe-base64 runs (session
// and share tokens), plus the share/token query values a URL may carry.
const SCRUB_RULES = [
  [/\bbearer\s+[a-z0-9._~+/-]{8,}={0,2}/gi, "Bearer ***"],
  [/\bsk-[A-Za-z0-9_-]{8,}/g, "sk-***"],
  [/\b(password|passwd|secret|token|api[_-]?key|authorization)(\s*[=:]\s*)[^\s&'"]+/gi, "$1$2***"],
  [/([?&](?:share|token|code|state)=)[^\s&'"]+/gi, "$1***"],
  [/\b[A-Za-z0-9_-]{40,}\b/g, "***"],
];

export function scrubReportText(text) {
  let out = String(text ?? "");
  for (const [pattern, repl] of SCRUB_RULES) out = out.replace(pattern, repl);
  return out;
}

// "Chrome 129 on Linux x86_64" from what the browser says about itself.
// userAgentData (Chromium) names the brand and platform reliably; the UA
// string is the fallback for Firefox and Safari.
export function describeBrowser(nav = {}) {
  const ua = String(nav.userAgent || "");
  const brands = nav.userAgentData?.brands?.filter((b) => !/Not.?A.?Brand|Chromium/i.test(b.brand)) || [];
  let name = brands[0] ? `${brands[0].brand} ${brands[0].version}` : "";
  if (!name) {
    const m = /(Edg|OPR|Firefox|Chrome|Version)\/(\d+)/.exec(ua);
    const map = { Edg: "Edge", OPR: "Opera", Version: "Safari" };
    if (m) name = `${map[m[1]] || m[1]} ${m[2]}`;
    else if (/Safari/.test(ua)) name = "Safari";
    else name = "unknown browser";
  }
  let os = nav.userAgentData?.platform || "";
  if (!os) {
    if (/Windows/.test(ua)) os = "Windows";
    else if (/iPad|iPhone/.test(ua) || (/Macintosh/.test(ua) && (nav.maxTouchPoints || 0) > 1)) os = "iPadOS";
    else if (/Mac OS X/.test(ua)) os = "macOS";
    else if (/Android/.test(ua)) os = "Android";
    else if (/CrOS/.test(ua)) os = "ChromeOS";
    else if (/Linux/.test(ua)) os = "Linux";
    else os = "unknown OS";
  }
  const arch = /x86_64|Win64|WOW64|x64/.test(ua) ? " x86_64" : /aarch64|arm64/i.test(ua) ? " arm64" : "";
  return `${name} on ${os}${arch}`;
}

// The gathered facts, one line each. `facts` is what ReportProblem collects:
//   build: {version, commit, label, frozen}      (from /api/session)
//   browser: {nav, width, height, dpr, touch, standalone, language}
//   view: {mode, pdf, readOnly, phone, theme, uiScale}
//   workspace: {kind, role}                      (never its name)
//   events: [{t, msg, tone}]                     (App's sysLog)
//   server: {info, lines} | null                 (admins: server-info + log)
export function describeFacts(facts = {}) {
  const b = facts.build || {};
  const build = b.version ? `Gamma v${b.version}${b.commit ? ` (${b.commit})` : ""}`
    : `Gamma development build${b.commit ? ` (${b.commit})` : ""}`;
  const runs = b.frozen ? "desktop app" : b.version ? "server" : "checkout";
  const br = facts.browser || {};
  const browser = [
    describeBrowser(br.nav || {}),
    br.width && br.height ? `${br.width}×${br.height}${br.dpr && br.dpr !== 1 ? ` @${br.dpr}x` : ""}` : "",
    br.touch ? "touch" : "",
    br.standalone ? "installed app" : "",
    br.language || "",
  ].filter(Boolean).join(" · ");
  const v = facts.view || {};
  const view = [
    v.mode || "unknown view",
    v.mode === "page" ? (v.pdf ? "with a PDF" : "notes only") : "",
    v.readOnly ? "read-only" : "",
    v.phone ? "phone layout" : "",
    v.theme ? `theme ${v.theme}` : "",
    v.uiScale && v.uiScale !== 1 ? `interface size ${v.uiScale}` : "",
  ].filter(Boolean).join(" · ");
  const w = facts.workspace;
  const workspace = w ? [w.kind, w.role].filter(Boolean).join(", ") : "";
  return { build: `${build} · ${runs}`, browser, view, workspace };
}

const fmtTime = (t) => {
  const d = new Date(t);
  return Number.isFinite(d.getTime()) ? d.toISOString().slice(11, 19) : "--:--:--";
};
const tonePrefix = (tone) => (tone === "error" ? "[ERROR] " : tone ? "[WARNING] " : "");

// The log lines worth sending: every warning/error of the recent past plus
// the newest lines whatever their tone, in time order.
export function selectEvents(events = []) {
  const recent = events.slice(-EVENT_WINDOW);
  const tail = new Set(recent.slice(-EVENT_TAIL));
  const picked = recent.filter((e) => e.tone || tail.has(e));
  return picked.slice(-EVENT_CAP);
}

export function formatEvents(events = []) {
  return selectEvents(events)
    .map((e) => `${fmtTime(e.t)} ${tonePrefix(e.tone)}${e.msg}`)
    .join("\n");
}

function formatServer(server) {
  if (!server) return "";
  const info = server.info || {};
  const counts = info.log_counts || {};
  const head = [
    info.label ? `Gamma ${info.label}` : "",
    info.platform ? `${info.platform}` : "",
    info.python ? `Python ${info.python}` : "",
    Number.isFinite(info.uptime_seconds) ? `up ${Math.round(info.uptime_seconds / 60)} min` : "",
    counts.warning != null ? `${counts.warning} warning${counts.warning === 1 ? "" : "s"}, ${counts.error || 0} error${counts.error === 1 ? "" : "s"} since start` : "",
  ].filter(Boolean).join(" · ");
  const lines = (server.lines || [])
    .filter((e) => e.level === "WARNING" || e.level === "ERROR" || e.level === "CRITICAL")
    .slice(-SERVER_LINES)
    .map((e) => `${fmtTime(e.t * 1000)} [${e.level}] ${e.msg}`)
    .join("\n");
  return { head, lines };
}

// The diagnostics block alone (what the "Include diagnostics" preview shows
// and the issue form's diagnostics field receives). Markdown.
export function formatDiagnostics(facts = {}) {
  const d = describeFacts(facts);
  const parts = [
    `**Build:** ${d.build}`,
    `**Browser:** ${d.browser}`,
    `**View:** ${d.view}${d.workspace ? ` · workspace: ${d.workspace}` : ""}`,
  ];
  const events = formatEvents(facts.events);
  parts.push(`**Recent events (this browser session):**\n\`\`\`\n${events || "(nothing logged)"}\n\`\`\``);
  const server = formatServer(facts.server);
  if (server) {
    parts.push(`**Server (seen as admin):** ${server.head || "no dashboard data"}`);
    if (server.lines) parts.push(`\`\`\`\n${server.lines}\n\`\`\``);
  }
  return scrubReportText(parts.join("\n"));
}

// The whole report as one markdown document — what "Copy report" puts on
// the clipboard, and what a reporter pastes when the GitHub URL had to be
// trimmed.
export function buildReport({ description = "", steps = "", facts, includeDiagnostics = true } = {}) {
  const parts = [`### What happened\n${description.trim() || "(not described)"}`];
  if (steps.trim()) parts.push(`### How to reproduce\n${steps.trim()}`);
  if (includeDiagnostics) parts.push(`### Diagnostics\n${formatDiagnostics(facts)}`);
  return scrubReportText(parts.join("\n\n"));
}

// A title from the description's first line.
export function issueTitle(description = "") {
  const line = description.trim().split(/\r?\n/)[0]?.trim() || "";
  if (!line) return "";
  return line.length > 80 ? `${line.slice(0, 77).trimEnd()}…` : line;
}

// The prefilled issue-form URL (.github/ISSUE_TEMPLATE/bug_report.yml: the
// query parameters are that form's field ids). Diagnostics are trimmed to
// GitHub's URL budget; `trimmed` tells the caller to say the clipboard
// holds the whole report.
export function githubIssueUrl({ description = "", steps = "", diagnostics = "" } = {}) {
  const make = (diag) => {
    const params = new URLSearchParams();
    params.set("template", "bug_report.yml");
    const title = issueTitle(description);
    if (title) params.set("title", title);
    if (description.trim()) params.set("description", description.trim());
    if (steps.trim()) params.set("steps", steps.trim());
    if (diag) params.set("diagnostics", diag);
    return `${NEW_ISSUE_URL}?${params}`;
  };
  let url = make(diagnostics);
  if (url.length <= URL_BUDGET) return { url, trimmed: false };
  const note = "\n… (trimmed — the full report is on the reporter's clipboard)";
  let keep = diagnostics.length;
  while (keep > 0 && url.length > URL_BUDGET) {
    keep = Math.floor(keep * 0.8);
    url = make(diagnostics.slice(0, keep) + note);
  }
  return { url, trimmed: true };
}
