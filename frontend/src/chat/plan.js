// Plan mode's rules (the composer's Plan switch and chat/PlanCard.jsx in
// ChatDock; docs/dev/ai.md "Planning first"). A message sent with Plan on
// is a planning turn: the request carries `planning`, the server refuses
// every change in it, and the assistant proposes the change with
// update_plan, whose action carries the plan (`plan: {steps: [{step,
// status}], proposed?}`). Approve sends a normal turn that carries it out.
// What the chat knows of a plan it reads from the saved messages: a user
// message sent as a planning turn has `planning: true`, the Approve message
// `approvesPlan: true` (neither goes to the server in the history).
import { t } from "../shared/i18n/i18n.js";

export const PLAN_TOOL = "update_plan";

// A reply's plan: its last update_plan that ran, or null.
export function replyPlan(actions) {
  for (let i = (actions?.length || 0) - 1; i >= 0; i -= 1) {
    const a = actions[i];
    if (a?.tool === PLAN_TOOL && !a.error && Array.isArray(a.plan?.steps)) return a.plan;
  }
  return null;
}

export function planProgress(plan) {
  const steps = plan?.steps || [];
  return { done: steps.filter((s) => s?.status === "completed").length, total: steps.length };
}

// Where the plan of reply `i` stands: "pending" (proposed, and the
// conversation's last message), "approved" (proposed, and the next message
// is the Approve), "replaced" (proposed, and anything else came next: a
// request for changes, another question) or "progress" (a plan the
// assistant marks while it works). "" without a plan, and for a proposal
// whose reply failed: neither history sends a failed reply, so Approve
// would carry out a plan the model no longer sees; the reply offers Retry.
export function planStatus(messages, i) {
  const plan = replyPlan(messages?.[i]?.actions);
  if (!plan) return "";
  if (!plan.proposed) return "progress";
  if (messages[i].error) return "";
  const next = messages.slice(i + 1).find((m) => m?.role === "user");
  if (!next) return i === messages.length - 1 ? "pending" : "replaced";
  return next.approvesPlan ? "approved" : "replaced";
}

// Whether the card offers Approve plan and Revise: a pending plan under
// the conversation's last reply, nothing streaming, the composer empty, a
// chat that can change things (`canPlan`) — Continue's rule plus the
// editor's.
export function canActOnPlan({ status, isLast, idle, readOnly, canPlan }) {
  return status === "pending" && !!isLast && !!idle && !readOnly && !!canPlan;
}

// What Approve plan sends, as the user's message.
export function approvePlanPrompt() {
  return t("Carry out the approved plan.");
}

// Whether the conversation's last message asked for a plan: a Continue
// keeps that mode, and a conversation loaded shows the Plan switch so.
export function continuesPlanning(messages) {
  const last = [...(messages || [])].reverse().find((m) => m?.role === "user");
  return last?.planning === true;
}

// Whether a message goes as a planning turn. An explicit answer wins
// (Continue, Approve, App's asks), then a resent message that was one
// plans again, then the switch; never where the chat cannot plan.
export function sentPlanning({ planning, referenceMessage, toggle = false, canPlan = false } = {}) {
  if (!canPlan) return false;
  if (typeof planning === "boolean") return planning;
  if (referenceMessage?.planning === true) return true;
  return !!toggle;
}
