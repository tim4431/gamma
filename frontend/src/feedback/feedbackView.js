// The feedback view's pure part (feedback/FeedbackDialog.jsx, docs/dev/
// feedback.md "In the app"): which scope the dialog reads, the entries
// grouped by their file, the requests it lists and what a person may do
// with one, what the Send says of an agent on this computer, and a Send's
// body. Node tests it (tests/feedbackView.test.mjs).
//
// The dialog is opened on a scope, {kind: "file" | "folder", id}
// (feedback/useFeedback.js), and reads GET /api/feedback?scope= for it or
// for its work folder, the person's choice; the document is the reader's
// (feedbackText.js says its entry's shape), the requests are
// GET /api/feedback/requests' records.
import { isWithdrawn } from "../comments/comments.js";

// How long the line of instruction may be (the server's cap).
export const NOTE_MAX = 2000;
// How many requests the view lists, the newest first.
export const REQUESTS_SHOWN = 10;
// How often the open dialog reads the requests and the entries again, ms.
export const POLL_MS = 5000;

// The scope id the dialog reads: the work folder's when the person chose
// it (`choice` "work_folder") and the document named it, else the scope it
// was opened on.
export function readScope(scope, choice, workFolder) {
  return choice === "work_folder" && workFolder?.id ? workFolder.id : scope.id;
}

// Whether the scope switch has two sides: the scope opened on is not the
// work folder itself.
export const hasChoice = (scope, workFolder) => !!workFolder?.id && workFolder.id !== scope.id;

// The entries by file, in the document's order: [{file, entries}].
export function groupByFile(entries) {
  const out = [];
  let last = null;
  for (const entry of entries || []) {
    if (!last || last.file !== entry.file) {
      last = { file: entry.file, entries: [] };
      out.push(last);
    }
    last.entries.push(entry);
  }
  return out;
}

// The requests the view lists: the newest `n` by `rev`.
export function newestRequests(requests, n = REQUESTS_SHOWN) {
  return [...(requests || [])].sort((a, b) => b.rev - a.rev).slice(0, n);
}

// Whether `me` (a username) may withdraw the request: their own, still
// waiting for the agent or being worked on.
export const canWithdraw = (request, me) => !!me && request?.by === me
  && (request.state === "sent" || request.state === "running") && !isWithdrawn(request);

// An entry's picture to show: the region as it is now, else as it was
// commented on (handwriting on an earlier build has only that one).
export const pictureOf = (entry) => entry?.picture_url || entry?.before_url || "";

// An entry's first comment, {who, body, deleted}: the agent that wrote it
// or its author, and its text.
export function firstComment(entry) {
  const c = entry?.comments?.[0];
  if (!c) return { who: "", body: "", deleted: false };
  return { who: c.agent || c.by || "", body: c.body || "", deleted: !!c.deleted };
}

// How many comments an agent on this computer takes in one Send, at most
// (the server's runner fails a larger one).
export const AGENT_MAX = 100; // the server's local_agents/runner.py MAX_THREADS: change both

// What the Send says of an agent on this computer (docs/dev/local_agents.md),
// read from the document's `agent` ({name, label, ready, problem, takes,
// why} or null) and `agents_found`: {mode, label, problem, why}. `mode` is
// "ready" (the folder's agent takes this person's Send), "not_ready" (it
// takes it but cannot run: `problem` says why), "checking" (not known yet),
// "other" (the folder has an agent that does not take this Send: `why`),
// "offer" (no agent, but one was found and the folder is kept here, `kept`,
// so the sync panel can turn it on) or "" for nothing to say.
export function sendAgent(doc, kept = false) {
  const agent = doc?.agent && typeof doc.agent === "object" ? doc.agent : null;
  if (agent) {
    const label = agent.label || agent.name || "";
    if (!agent.takes) return { mode: "other", label, problem: "", why: agent.why || "" };
    if (agent.ready == null) return { mode: "checking", label, problem: "", why: "" };
    return { mode: agent.ready ? "ready" : "not_ready", label, problem: agent.ready ? "" : agent.problem || "", why: "" };
  }
  return { mode: doc?.agents_found && kept ? "offer" : "", label: "", problem: "", why: "" };
}

// Whether the Send goes to the folder's agent on this computer, so it is
// named on the button and its limit holds.
export const namesAgent = (plan) => plan.mode === "ready" || plan.mode === "not_ready" || plan.mode === "checking";

// How many entries were opened by someone other than `me`: their comments
// are context to the agent, not instructions.
export const othersCount = (entries, me) => (entries || []).filter((entry) => (entry.comments?.[0]?.by || "") !== me).length;

// What a Send posts (POST /api/feedback/requests): the id the client minted
// (the same on a retry, so it is written once), the scope read and the
// instruction, trimmed and cut to NOTE_MAX.
export function sendBody(requestId, scopeId, note) {
  return { id: requestId, scope: scopeId, note: String(note || "").trim().slice(0, NOTE_MAX) };
}
