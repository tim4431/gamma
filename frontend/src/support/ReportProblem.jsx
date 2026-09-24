import React, { useEffect, useMemo, useState } from "react";
import { API, apiJson, copyText } from "../shared/lib/utils";
import { Field, SubDialog, Toggle } from "../settings/SettingsKit";
import { buildReport, formatDiagnostics, githubIssueUrl, ISSUES_URL } from "./problemReport.js";

// "Report a problem" (account menu, Settings → Diagnostics): what happened
// in the reporter's words, the diagnostics Gamma can gather on its own
// (problemReport.js), and one button that opens the repository's bug form
// prefilled with both. The whole report also lands on the clipboard, so a
// reporter without GitHub can paste it anywhere, and a form GitHub had to
// trim still gets the rest by paste. Nothing is sent until the reporter
// submits the form on GitHub.
//
// `facts` comes from App: build (the session's), view, workspace (kind and
// role only), events (the session log) and isAdmin, which adds the server
// dashboard and the server log's warnings — the admin sees them already.
export default function ReportProblem({ facts, onClose, setStatus }) {
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState("");
  const [includeDiagnostics, setIncludeDiagnostics] = useState(true);
  const [server, setServer] = useState(null);
  const [browser] = useState(() => ({
    nav: navigator,
    width: window.innerWidth,
    height: window.innerHeight,
    dpr: Math.round((window.devicePixelRatio || 1) * 100) / 100,
    touch: navigator.maxTouchPoints > 0,
    standalone: !!(window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone),
    language: navigator.language || "",
  }));

  useEffect(() => {
    if (!facts?.isAdmin) return undefined;
    let stale = false;
    Promise.all([
      apiJson(`${API}/admin/server-info`).catch(() => null),
      apiJson(`${API}/admin/logs`).catch(() => null),
    ]).then(([info, logs]) => {
      if (!stale && (info || logs)) setServer({ info: info || {}, lines: logs?.entries || [] });
    });
    return () => { stale = true; };
  }, [facts?.isAdmin]);

  const full = useMemo(() => ({ ...facts, browser, server }), [facts, browser, server]);
  const diagnostics = useMemo(() => formatDiagnostics(full), [full]);
  const report = () => buildReport({ description, steps, facts: full, includeDiagnostics });

  async function copy() {
    const ok = await copyText(report());
    setStatus(ok ? "Report copied." : "Copy failed — select the preview and copy it by hand.");
  }

  async function openIssue() {
    const copied = await copyText(report());
    const { url, trimmed } = githubIssueUrl({ description, steps, diagnostics: includeDiagnostics ? diagnostics : "" });
    const win = window.open(url, "_blank", "noopener");
    if (!win && !copied) {
      setStatus(`The browser blocked the GitHub tab — open ${ISSUES_URL} and paste the report.`);
      return;
    }
    setStatus(trimmed && copied
      ? "GitHub form opened; the diagnostics were trimmed to fit — paste the copied report if anything is missing."
      : copied ? "GitHub form opened — the report is on your clipboard too." : "GitHub form opened.");
    onClose();
  }

  return (
    <SubDialog title="Report a problem" onClose={onClose} className="reportProblem" draft={description || steps}>
      <Field label="What happened" hint="what you did, what you expected, what you saw">
        <textarea className="reportProblemText" rows={3} autoFocus value={description}
          onChange={(e) => setDescription(e.target.value)} placeholder="A blue line stays on the notes after…" />
      </Field>
      <Field label="How to reproduce" hint="optional">
        <textarea className="reportProblemText" rows={2} value={steps}
          onChange={(e) => setSteps(e.target.value)} placeholder="1. Open a page… 2. …" />
      </Field>
      <Toggle label="Include diagnostics" hint="build, browser, view and recent log lines — no notes, files or names"
        title="The version and build of this server, your browser and screen, which kind of view is open, and the app's own log lines from this session (secrets masked). Never the content of your notes or files."
        checked={includeDiagnostics} onChange={setIncludeDiagnostics} />
      {includeDiagnostics ? (
        <details className="reportPreview">
          <summary>Preview the report</summary>
          <pre>{diagnostics}</pre>
        </details>
      ) : null}
      <div className="reportModalBtns">
        <button type="button" className="uiBtn" onClick={copy} title="Copy the whole report as text — to paste in an e-mail, a chat or an issue you write by hand">Copy report</button>
        <button type="button" className="uiBtn primary" onClick={openIssue} disabled={!description.trim()}
          title="Open the bug form on GitHub with the report filled in; you review it there before it is posted">Open GitHub issue</button>
      </div>
    </SubDialog>
  );
}
