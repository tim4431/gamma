// Reporting content (docs/dev/api.md "Reports"): a comment from its "⋯"
// menu, a shared or published page from the share view's menu, an AI reply
// from its actions in the chat. The surface that offers Report calls
// `openReport(target)`; the one dialog App mounts (ReportContentHost.jsx)
// opens on it and posts `reportBody` to POST /api/reports, which keeps the
// report for this server's admins and, where the server signs in with
// Gamma Cloud, sends it on to Gamma's developer.
//
// A target is {kind: "comment", commentId, text, author},
// {kind: "page", pageId, title, link} (`link` a published page's address)
// or {kind: "ai", text, model}. Pure but for the one-slot store, so node
// can test it (tests/reportContent.test.mjs).
import { T, t } from "../shared/i18n/i18n.js";

// The reasons, as the server takes them (gamma/reports.py REASONS).
export const REPORT_REASONS = [
  ["harassment", T("Harassment, bullying or hate")],
  ["sexual", T("Sexual content")],
  ["violence", T("Violence or threats")],
  ["spam", T("Spam, a scam or misleading")],
  ["illegal", T("Illegal, or someone else's copyright")],
  ["privacy", T("Someone's private information")],
  ["harmful", T("Harmful or dangerous")],
  ["other", T("Something else")],
];
const REPORT_KINDS = ["comment", "page", "ai"];
export const MAX_DETAILS = 2000;
const MAX_CONTENT = 8000;
const PREVIEW = 280;

const TITLES = { comment: T("Report this comment"), page: T("Report this page"), ai: T("Report this reply") };
export const reportTitle = (target) => t(TITLES[target?.kind] || TITLES.page);

// The line the dialog quotes: what is reported, cut short.
export function reportPreview(target) {
  const text = String((target?.kind === "page" ? target.title : target?.text) || "").replace(/\s+/g, " ").trim();
  return text.length > PREVIEW ? `${text.slice(0, PREVIEW - 1)}…` : text;
}

// Who wrote it, for the line under the quote ("" when unknown).
export function reportAuthor(target) {
  if (!target?.author && !target?.model) return "";
  return target.kind === "ai" ? t("Written by {model}", { model: target.model })
    : t("By {name}", { name: target.author });
}

// The request body for POST /api/reports. The server reads a comment and
// a page itself; only an AI reply's text and model come from here.
export function reportBody(target, reason, details = "") {
  const body = { kind: target.kind, reason, details: String(details || "").trim().slice(0, MAX_DETAILS) };
  if (target.kind === "comment") body.comment_id = target.commentId;
  if (target.kind === "page") {
    body.page_id = target.pageId;
    if (target.link) body.link = target.link;
  }
  if (target.kind === "ai") {
    body.content = String(target.text || "").slice(0, MAX_CONTENT);
    body.model = String(target.model || "");
  }
  return body;
}

// Whether `target` can be reported: a kind the server takes and what it names.
export function reportable(target) {
  if (!target || !REPORT_KINDS.includes(target.kind)) return false;
  if (target.kind === "comment") return !!target.commentId;
  if (target.kind === "page") return !!target.pageId;
  return !!String(target.text || "").trim();
}

// The one dialog's target, a store any surface writes and the host reads.
let current = null;
const listeners = new Set();
const emit = () => listeners.forEach((fn) => fn());
export function openReport(target) {
  if (!reportable(target)) return false;
  current = { ...target };
  emit();
  return true;
}
export function closeReport() {
  if (!current) return;
  current = null;
  emit();
}
export const reportTarget = () => current;
export function subscribeReport(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
