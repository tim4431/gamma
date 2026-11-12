// The feedback view's words (docs/dev/feedback.md, feedback/FeedbackDialog.jsx):
// an entry's heading as the Markdown form names it (gamma/feedback/markdown.py,
// here in the reader's language), how its place was found again, its
// source line, and a Send's line with what became of it. Pure, so node
// tests it (tests/feedbackText.test.mjs).
//
// An entry is the reader's (GET /api/feedback): {id, file, format, page_id,
// block_id, kind ("passage" | "area" | "ink" | "note" | "file"), where:
// {page, quote, box, placed, note}, source: {file, line, end, version} |
// null, picture, before, state, sent, link, comments}. A request is a Send:
// {id, rev, work_folder, scope, by, at, note, state ("sent" | "running" |
// "done" | "failed"), state_at, state_by, detail, threads: [{id, answered}]}.
import { anchorSnippet, isWithdrawn } from "../comments/comments.js";
import { formatRelativeTime } from "../library/libraryUtils.js";
import { t, tn } from "../shared/i18n/i18n.js";

// How long a quote in a heading is, at most.
const QUOTE_MAX = 80;

// An entry's heading: "Page 3 · “the loss is defined as”" (a passage),
// "Page 3 · note on “…”" (a note under one), "Page 5 · area", "Page 2 ·
// handwriting", "A note" (a note with no place on a page) or "The whole
// file".
export function entryHeading(entry) {
  const where = entry.where || {}, n = where.page;
  if (entry.kind === "file") return t("The whole file");
  if (!n) return t("A note");
  if (entry.kind === "area") return t("Page {n} · area", { n });
  if (entry.kind === "ink") return t("Page {n} · handwriting", { n });
  const quote = anchorSnippet(where.quote, QUOTE_MAX);
  return where.note ? t("Page {n} · note on “{quote}”", { n, quote }) : t("Page {n} · “{quote}”", { n, quote });
}

// How an entry's place was found on the file the page shows now
// (`where.placed`): exactly, roughly (with how alike the text is, a
// `match` from 0 to 1, when it says), by estimate, not at all, not yet,
// or on an earlier build (handwriting, which is never placed again); ""
// for an entry with no place.
export function placedText(where) {
  const placed = where?.placed;
  if (placed === "exact") return t("placed exactly");
  if (placed === "rough") {
    return typeof where.match === "number" ? t("placed roughly ({match})", { match: `${Math.round(where.match * 100)}%` })
      : t("placed roughly");
  }
  if (placed === "estimate") return t("placed by estimate");
  if (placed === "lost") return t("not found in this build");
  if (placed === "pending") return t("not placed yet");
  if (placed === "earlier") return t("drawn on an earlier build");
  return "";
}

// An entry's source: "method.tex:118–120" (the file's name, then its line
// or lines), followed by "(as commented)" when it is the line recorded as
// the thread was opened rather than the current build's; "" for none.
export function sourceText(source) {
  if (!source?.file) return "";
  const name = source.file.split("/").pop();
  const lines = source.end && source.end !== source.line ? `${source.line}–${source.end}` : `${source.line || ""}`;
  const text = lines ? `${name}:${lines}` : name;
  return source.version === "commented" ? `${text} ${t("(as commented)")}` : text;
}

// A Send as the view lists it: {line, state, kind}. `line` is "Sent 5m ago ·
// 3 comments"; `state` what became of it: waiting for the agent, the agent
// working (with its own line of progress), done by the agent, failed with
// its reason, or withdrawn by its requester; `kind` that state's name
// ("sent", "running", "done", "failed", "withdrawn").
export function requestLine(request, now = Date.now()) {
  const n = request.threads?.length || 0;
  const line = tn("Sent {when} · {n} comment", "Sent {when} · {n} comments", n, { when: formatRelativeTime(request.at, now) });
  const { state, detail, state_by: by } = request;
  if (isWithdrawn(request)) return { line, state: t("Withdrawn"), kind: "withdrawn" };
  if (state === "running") {
    return { line, state: detail ? t("The agent is working: {detail}", { detail }) : t("The agent is working"), kind: state };
  }
  if (state === "done") return { line, state: by ? t("Done by {agent}", { agent: by }) : t("Done"), kind: state };
  if (state === "failed") return { line, state: detail ? t("Failed: {reason}", { reason: detail }) : t("Failed"), kind: state };
  return { line, state: t("Waiting for the agent"), kind: "sent" };
}
