import React, { useEffect, useMemo, useRef, useState } from "react";
import { API, apiJson, copyText, fmtBytes } from "../shared/lib/utils";
import { Field, Row, SubDialog, Toggle } from "../settings/SettingsKit";
import { MonitorIcon } from "../shared/ui/Icons";
import { buildReport, formatDiagnostics, githubIssueUrl, ISSUES_URL } from "./problemReport.js";

// "Report a problem" (account menu, Settings → Diagnostics): what happened
// in the reporter's words, how to bring it back, the diagnostics Gamma can
// gather on its own (problemReport.js), an optional screen recording, and
// one button that opens the repository's bug form prefilled with the text.
// The whole report also lands on the clipboard, so a reporter without
// GitHub can paste it anywhere, and a form GitHub had to trim still gets
// the rest by paste. Nothing is sent until the reporter submits the form.
//
// The recording: getDisplayMedia + MediaRecorder, started from the dialog,
// which folds into a small pill while the reporter reproduces the problem
// (the dialog would sit over the app). Stop — the pill's button, the
// browser's own "stop sharing" bar, or the length cap — brings the dialog
// back with the file, which is saved to the reporter's downloads and
// dropped into the GitHub form by hand: an issue URL cannot carry a file.
//
// `facts` comes from App: build (the session's), view, workspace (kind and
// role only), events (the session log) and isAdmin, which adds the server
// dashboard and the server log's warnings — the admin sees them already.
const MAX_RECORDING_SECONDS = 180;
const RECORDING_TYPES = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm", "video/mp4"];

const fmtClock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

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
  // The capture in progress ({recorder, stream, startedAt}) and its clock;
  // then the finished file ({name, blob, url, seconds, saved}).
  const [live, setLive] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const [recording, setRecording] = useState(null);
  const liveRef = useRef(null);
  const canRecord = !!navigator.mediaDevices?.getDisplayMedia && typeof MediaRecorder !== "undefined";

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

  // The clock while capturing, and the length cap.
  useEffect(() => {
    if (!live) return undefined;
    const tick = () => {
      const s = Math.floor((Date.now() - live.startedAt) / 1000);
      setElapsed(s);
      if (s >= MAX_RECORDING_SECONDS) stopRecording();
    };
    const timer = setInterval(tick, 500);
    return () => clearInterval(timer);
  }, [live]); // eslint-disable-line react-hooks/exhaustive-deps

  // Leaving the dialog mid-capture (a reload, a route change) ends it.
  useEffect(() => () => {
    const l = liveRef.current;
    if (l) { try { l.recorder.stop(); } catch {} l.stream.getTracks().forEach((t) => t.stop()); }
  }, []);
  useEffect(() => () => { if (recording?.url) URL.revokeObjectURL(recording.url); }, [recording]);

  async function startRecording() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 15 }, audio: false, preferCurrentTab: true, selfBrowserSurface: "include",
      });
    } catch {
      setStatus("Screen recording was not allowed.");
      return;
    }
    const type = RECORDING_TYPES.find((t) => MediaRecorder.isTypeSupported(t)) || "";
    let recorder;
    try {
      recorder = new MediaRecorder(stream, { ...(type ? { mimeType: type } : {}), videoBitsPerSecond: 1500000 });
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      setStatus("This browser cannot record the screen.");
      return;
    }
    const chunks = [];
    const startedAt = Date.now();
    recorder.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: recorder.mimeType || type || "video/webm" });
      const ext = /mp4/.test(blob.type) ? "mp4" : "webm";
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:]/g, "").replace("T", "-");
      const seconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
      setRecording((prev) => {
        if (prev?.url) URL.revokeObjectURL(prev.url);
        return blob.size ? { name: `gamma-recording-${stamp}.${ext}`, blob, url: URL.createObjectURL(blob), seconds, saved: false } : null;
      });
      liveRef.current = null;
      setLive(null);
      if (!blob.size) setStatus("The recording came out empty.");
    };
    // The browser's own "Stop sharing" bar ends the capture too.
    stream.getVideoTracks()[0]?.addEventListener("ended", () => { if (recorder.state !== "inactive") recorder.stop(); });
    recorder.start(1000);
    const l = { recorder, stream, startedAt };
    liveRef.current = l;
    setElapsed(0);
    setLive(l);
  }

  function stopRecording() {
    const l = liveRef.current;
    if (l && l.recorder.state !== "inactive") l.recorder.stop();
  }

  function saveRecording() {
    if (!recording) return false;
    const a = document.createElement("a");
    a.href = recording.url;
    a.download = recording.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setRecording((r) => (r ? { ...r, saved: true } : r));
    return true;
  }

  function discardRecording() {
    setRecording(null);
  }

  const full = useMemo(() => ({ ...facts, browser, server }), [facts, browser, server]);
  const diagnostics = useMemo(() => formatDiagnostics(full), [full]);
  const recordingName = recording?.name || "";
  const report = () => buildReport({ description, steps, facts: full, includeDiagnostics, recording: recordingName });

  async function copy() {
    const ok = await copyText(report());
    setStatus(ok ? "Report copied." : "Copy failed — select the preview and copy it by hand.");
  }

  async function openIssue() {
    const copied = await copyText(report());
    if (recording && !recording.saved) saveRecording();
    const { url, trimmed } = githubIssueUrl({
      description, steps, recording: recordingName, diagnostics: includeDiagnostics ? diagnostics : "",
    });
    const win = window.open(url, "_blank", "noopener");
    if (!win && !copied) {
      setStatus(`The browser blocked the GitHub tab — open ${ISSUES_URL} and paste the report.`);
      return;
    }
    const notes = [];
    if (recording) notes.push(`drop ${recording.name} from your downloads into the form`);
    if (trimmed && copied) notes.push("the diagnostics were trimmed to fit — paste the copied report if anything is missing");
    else if (copied) notes.push("the report is on your clipboard too");
    setStatus(`GitHub form opened${notes.length ? ` — ${notes.join("; ")}` : ""}.`);
    onClose();
  }

  if (live) {
    return (
      <div className="reportRecordPill" role="status" aria-live="polite">
        <span className="reportRecordDot" aria-hidden="true" />
        <span className="reportRecordText">Recording {fmtClock(elapsed)} — show the problem, then</span>
        <button type="button" className="uiBtn sm primary" onClick={stopRecording}>Stop</button>
      </div>
    );
  }

  return (
    <SubDialog title="Report a problem" onClose={onClose} className="reportProblem" closeButton
      draft={description || steps || recordingName}>
      <Field label="What happened" hint="what you did, what you expected, what you saw">
        <textarea className="reportProblemText" rows={3} autoFocus value={description}
          onChange={(e) => setDescription(e.target.value)} placeholder="A blue line stays on the notes after…" />
      </Field>
      <Field label="How to reproduce" hint="the steps that bring it back, if you found them">
        <textarea className="reportProblemText" rows={3} value={steps}
          onChange={(e) => setSteps(e.target.value)} placeholder={"1. Open a page with notes\n2. Drag a block by its handle…\n3. …"} />
      </Field>
      {canRecord ? (
        <Row icon={MonitorIcon} label="Screen recording"
          hint={recording
            ? `${recording.name} · ${fmtClock(recording.seconds)} · ${fmtBytes(recording.blob.size)}${recording.saved ? " · saved" : ""}`
            : "Show the problem as it happens; the saved file goes into the GitHub form"}
          title={`Records your screen (or one window or tab — the browser asks which) for up to ${MAX_RECORDING_SECONDS / 60} minutes, without sound. The file stays on your computer: save it and drop it into the GitHub form, where it is uploaded as part of the issue.`}>
          <span className="setRowControls">
            {recording ? (
              <>
                <button type="button" className="uiBtn sm" onClick={saveRecording}>Save</button>
                <button type="button" className="uiBtn sm" onClick={discardRecording}>Discard</button>
              </>
            ) : (
              <button type="button" className="uiBtn sm" onClick={startRecording}>Record…</button>
            )}
          </span>
        </Row>
      ) : null}
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
