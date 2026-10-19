// A save's log: what it tried, step by step, as plain text to paste into a bug
// report. The popup's "Copy the save log" copies the tab's last save; the
// options page's "Copy recent saves" copies the last few, including those of
// the shortcut and the context menu, which only notify. Each step also goes
// to the service worker's console, which most people never open. Pure,
// tested in tests/.

export const RECENT_KEY = "log:saves"; // session storage: the last RECENT_SAVES logs, newest first
export const RECENT_SAVES = 5;

export function formatBytes(n) {
  if (!Number.isFinite(n) || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// One detection as a line: what the save rests on, "—" for what is missing.
export function describeCandidate(c) {
  if (!c) return "nothing";
  const links = (c.pdf_links || []).length;
  const record = c.record || null;
  return [
    c.kind || "none",
    `doi ${c.doi || "—"}${c.kind === "maybe" ? " (only in the page's text)" : ""}`,
    c.dois_in_text > 1 ? `${c.dois_in_text} DOIs in the page's text, none taken` : "",
    `arXiv ${c.arxiv_id || "—"}`,
    `pdf_url ${c.pdf_url || "—"}`,
    record ? `citation record: ${record.kind || "no type"}, ${(record.authors || []).length} author${(record.authors || []).length === 1 ? "" : "s"}` : "",
    c.is_pdf_tab ? "the tab is a PDF" : "",
    links ? `${links} PDF link${links === 1 ? "" : "s"} on the page` : "",
    c.from_url ? "from the URL only (no content script)" : "",
  ].filter(Boolean).join(" · ");
}

// A download's answer: "403 text/html", "200 application/pdf · 812 KB",
// with where it ended when that was elsewhere.
export function describeResponse({ status, type = "", size, url = "", landed = "" }) {
  const parts = [`${status}${type ? ` ${type.split(";")[0]}` : ""}`];
  const bytes = formatBytes(size);
  if (bytes) parts.push(bytes);
  if (landed && landed !== url) parts.push(`ended at ${landed}`);
  return parts.join(" · ");
}

// `head`: {version, browser, server, workspace, tab, candidate, title}.
export function saveLog(head = {}, now = () => Date.now()) {
  const start = now();
  const lines = [];
  const header = [
    `Gamma Connector ${head.version || "?"} — save at ${new Date(start).toISOString()}`,
    `browser: ${head.browser || "?"}`,
    `server: ${head.server || "(none)"} · workspace: ${head.workspace || "(the account's default)"}`,
    `tab: ${head.tab || "(none)"}`,
    `detected: ${describeCandidate(head.candidate)}`,
  ];
  if (head.title) header.push(`title: ${head.title}`);
  return {
    step(text) {
      lines.push(`+${((now() - start) / 1000).toFixed(1)}s ${text}`);
      console.info(`[gamma] ${text}`);
    },
    text() { return [...header, ...lines].join("\n"); },
  };
}

// The recent logs with `text` in front, at most RECENT_SAVES.
export function withRecent(recent, text) {
  return [text, ...(Array.isArray(recent) ? recent : [])].slice(0, RECENT_SAVES);
}
