// The feedback view's words (docs/dev/feedback.md, feedback/FeedbackDialog.jsx):
// an entry's heading as the Markdown form names it (gamma/feedback/markdown.py,
// here in the reader's language), how its place was found again, its
// source line, a Send's line with what became of it, and what the Send
// block says of an agent on this computer. Pure, so node
// tests it (tests/feedbackText.test.mjs).
//
// An entry is the reader's (GET /api/feedback): {id, file, format, page_id,
// block_id, kind ("passage" | "area" | "ink" | "region" | "lines" | "note" |
// "file"), where: {page, quote, box, placed, note; a region's rect and size
// in the picture's pixels; a text file's range its line, end and, placed
// roughly, was: the quote it was commented on}, source: {file, line, end,
// version} | null, picture,
// before, state, sent, link, comments}. A request is a Send:
// {id, rev, work_folder, scope, by, at, note, state ("sent" | "running" |
// "done" | "failed"), state_at, state_by, detail, threads: [{id, answered}]}.
import { anchorSnippet, isWithdrawn } from "../comments/comments.js";
import { agentAway } from "../editor/mentions.js";
import { formatRelativeTime } from "../library/libraryUtils.js";
import { t, tn } from "../shared/i18n/i18n.js";
import { AGENT_MAX, namesAgent } from "./feedbackView.js";

// How long a quote in a heading is, at most.
const QUOTE_MAX = 80;

// An entry's heading: "Page 3 · “the loss is defined as”" (a passage),
// "Page 3 · note on “…”" (a note under one), "Page 5 · area", "Page 2 ·
// handwriting", "Region · 320×180 px at (40, 60)" (a region of a picture,
// by its rect in the picture's pixels; "Note on a region · …" under one),
// "Lines 118–120 · “…”" or "Line 118 · “…”" (a range of a text file; "Line
// 118 · note on “…”" under one), "A note" (a note with no place on a page)
// or "The whole file".
export function entryHeading(entry) {
  const where = entry.where || {}, n = where.page;
  if (entry.kind === "file") return t("The whole file");
  if (entry.kind === "region") {
    const r = where.rect;
    if (!r) return where.note ? t("Note on a region") : t("Region");
    const at = { w: r[2] - r[0], h: r[3] - r[1], x: r[0], y: r[1] };
    return where.note ? t("Note on a region · {w}×{h} px at ({x}, {y})", at) : t("Region · {w}×{h} px at ({x}, {y})", at);
  }
  if (entry.kind === "lines" && where.line) {
    const quote = anchorSnippet(where.quote, QUOTE_MAX), from = where.line, to = where.end || where.line;
    if (to > from) {
      return where.note ? t("Lines {from}–{to} · note on “{quote}”", { from, to, quote })
        : t("Lines {from}–{to} · “{quote}”", { from, to, quote });
    }
    return where.note ? t("Line {n} · note on “{quote}”", { n: from, quote }) : t("Line {n} · “{quote}”", { n: from, quote });
  }
  if (!n) return t("A note");
  if (entry.kind === "area") return t("Page {n} · area", { n });
  if (entry.kind === "ink") return t("Page {n} · handwriting", { n });
  const quote = anchorSnippet(where.quote, QUOTE_MAX);
  return where.note ? t("Page {n} · note on “{quote}”", { n, quote }) : t("Page {n} · “{quote}”", { n, quote });
}

// How an entry's place was found on the file the page shows now
// (`where.placed`): exactly, roughly (with how alike the text is, a
// `match` from 0 to 1, when it says), by estimate, not at all, not yet,
// or on an earlier version (handwriting, which is never placed again): the
// Markdown's words for every format (gamma/feedback/markdown.py PLACED); ""
// for an entry with no place. A region (`kind`) is placed exactly, drawn
// on an earlier version of the picture (it stays where it was on the new
// one), or lost when the file is no longer a picture.
export function placedText(where, kind = "") {
  const placed = where?.placed;
  if (kind === "region") {
    if (placed === "exact") return t("placed exactly");
    if (placed === "earlier") return t("drawn on an earlier version of the picture");
    if (placed === "lost") return t("the file is no longer a picture");
    return "";
  }
  if (placed === "exact") return t("placed exactly");
  if (placed === "rough") {
    return typeof where.match === "number" ? t("placed roughly ({match})", { match: `${Math.round(where.match * 100)}%` })
      : t("placed roughly");
  }
  if (placed === "estimate") return t("placed by estimate");
  if (placed === "lost") return t("not found in the current version");
  if (placed === "pending") return t("not placed yet");
  if (placed === "earlier") return t("drawn on an earlier version");
  return "";
}

// An entry's source: "method.tex:118–120" (the file's name, then its line
// or lines), followed by "(as commented)" when it is the line recorded as
// the thread was opened rather than the current version's; "" for none.
export function sourceText(source) {
  if (!source?.file) return "";
  const name = source.file.split("/").pop();
  const lines = source.end && source.end !== source.line ? `${source.line}–${source.end}` : `${source.line || ""}`;
  const text = lines ? `${name}:${lines}` : name;
  return source.version === "commented" ? `${text} ${t("(as commented)")}` : text;
}

// A Send as the view lists it: {line, state, detail, kind}. `line` is "Sent
// 5m ago · 3 comments"; `state` what became of it, short: waiting for the
// agent, the agent working (named by the request's `state_by` when it
// says), done by the agent, failed, or withdrawn by its requester; `detail`
// the agent's own words beside it, its line of progress, its summary or
// the reason it failed ("" for none), shown on a line of its own; `kind`
// the state's name ("sent", "running", "done", "failed", "withdrawn").
// `computer`, the computer that keeps the folder and runs the reader's
// Sends (sendAgent's), names what a Send of theirs waits for.
export function requestLine(request, now = Date.now(), computer = "") {
  const n = request.threads?.length || 0;
  const line = tn("Sent {when} · {n} comment", "Sent {when} · {n} comments", n, { when: formatRelativeTime(request.at, now) });
  const { state, state_by: agent } = request;
  const detail = request.detail || "";
  if (isWithdrawn(request)) return { line, state: t("Withdrawn"), detail: "", kind: "withdrawn" };
  if (state === "running") {
    return { line, state: agent ? t("{agent} is working", { agent }) : t("The agent is working"), detail, kind: state };
  }
  if (state === "done") return { line, state: agent ? t("Done by {agent}", { agent }) : t("Done"), detail, kind: state };
  if (state === "failed") return { line, state: t("Failed"), detail, kind: state };
  const waiting = computer ? t("Waiting for {computer}", { computer }) : t("Waiting for the agent");
  return { line, state: waiting, detail: "", kind: "sent" };
}

// The Withdraw button's title: on a request an agent on this computer is
// working on (`local`, the server says), that withdrawing stops it and
// leaves what it changed; "" for the plain button (an outside agent is
// told by the request's state alone, and nothing here stops it).
export function withdrawTitle(request) {
  return request?.state === "running" && request.local === true && request.state_by
    ? t("Stop {agent} and withdraw: what it changed already stays in the files.", { agent: request.state_by })
    : "";
}

// What the Send block says (feedbackView.js sendAgent's `plan`, `n` the
// entries sent, `others` how many of them someone else opened): {button,
// notes: [{tone ("hint" | "warn"), text}], blocked}. A Send the folder's
// agent takes names it on the button, says where it works (this computer,
// or the computer that keeps the folder, `plan.computer`, and that the Send
// waits while that one is offline), warns while it cannot run and is
// blocked past AGENT_MAX comments; the server's own sentences (`problem`,
// `why`) are English and shown as they are.
export function sendWords(plan, n, others = 0) {
  const agent = plan.label, computer = plan.computer || "";
  const notes = [];
  if (!namesAgent(plan)) {
    if (plan.mode === "other" && plan.why) notes.push({ tone: "hint", text: plan.why });
    if (plan.mode === "offer") {
      notes.push({ tone: "hint", text: t("To have Codex or Claude Code answer here, turn one on in the sync panel: the sync button in the title bar, then ⋯ on this folder.") });
    }
    return { button: tn("Send {n} comment", "Send {n} comments", n), notes, blocked: false };
  }
  if (plan.mode === "checking") notes.push({ tone: "hint", text: t("Checking {agent}…", { agent }) });
  else if (plan.mode === "not_ready") notes.push({ tone: "warn", text: [plan.problem, t("This Send fails until then.")].filter(Boolean).join(" ") });
  else if (plan.mode === "away") notes.push({ tone: "hint", text: agentAway(plan) });
  else if (computer) {
    notes.push({ tone: "hint", text: t("{agent} works on {computer}, in this folder's directory there, and answers each comment.", { agent, computer }) });
  } else notes.push({ tone: "hint", text: t("{agent} works on this computer, in this folder's directory, and answers each comment.", { agent }) });
  const blocked = n > AGENT_MAX;
  if (blocked) {
    notes.push({ tone: "warn", text: t("{agent} takes at most {max} comments at a time: send a file or a directory of this work folder.", { agent, max: AGENT_MAX }) });
  }
  if (others) {
    notes.push({ tone: "hint", text: tn("{n} of these comments is by someone else: {agent} treats it as context.",
      "{n} of these comments are by someone else: {agent} treats them as context.", others, { agent }) });
  }
  return { button: tn("Send {n} comment to {agent}", "Send {n} comments to {agent}", n, { agent }), notes, blocked };
}
