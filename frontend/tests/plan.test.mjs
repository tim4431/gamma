// Plan mode's rules (chat/plan.js): a reply's plan, where it stands, when
// its card offers Approve and Revise, and which messages go as planning
// turns.
import test from "node:test";
import assert from "node:assert/strict";
import { approvePlanPrompt, canActOnPlan, continuesPlanning, planProgress, planStatus, replyPlan, sentPlanning }
  from "../src/chat/plan.js";

const steps = (...statuses) => statuses.map((status, i) => ({ step: `Step ${i + 1}`, status }));
const planned = (plan, extra = {}) => ({ kind: "plan", tool: "update_plan", summary: "Plan", plan, ...extra });
const proposed = planned({ steps: steps("pending", "pending"), proposed: true });
const ask = (extra = {}) => ({ role: "user", text: "Tidy this folder", ...extra });
const reply = (...actions) => ({ role: "ai", text: "Here is the plan.", actions });

test("a reply's plan is its last update_plan that ran", () => {
  const first = planned({ steps: steps("completed", "pending") });
  const second = planned({ steps: steps("completed", "completed") });
  assert.equal(replyPlan([first, { kind: "read", tool: "read_page" }, second]), second.plan);
  assert.equal(replyPlan([first, { ...second, error: true }]), first.plan, "a failed call is no plan");
  assert.equal(replyPlan([{ kind: "read", tool: "read_page" }]), null);
  assert.equal(replyPlan(undefined), null);
  assert.deepEqual(planProgress(second.plan), { done: 2, total: 2 });
  assert.deepEqual(planProgress(first.plan), { done: 1, total: 2 });
  assert.deepEqual(planProgress(null), { done: 0, total: 0 });
});

test("a proposed plan waits, is approved, or is replaced; a marked plan shows progress", () => {
  const waiting = [ask({ planning: true }), reply(proposed)];
  assert.equal(planStatus(waiting, 1), "pending");
  const approved = [...waiting, ask({ text: approvePlanPrompt(), approvesPlan: true }),
    reply(planned({ steps: steps("completed", "in_progress") }))];
  assert.equal(planStatus(approved, 1), "approved");
  assert.equal(planStatus(approved, 3), "progress");
  assert.equal(planStatus([...waiting, ask({ text: "Use years too", planning: true }), reply(proposed)], 1), "replaced");
  assert.equal(planStatus([...waiting, ask({ text: "What is in it?" })], 1), "replaced");
  assert.equal(planStatus([ask(), reply({ kind: "read", tool: "read_page" })], 1), "");
  assert.equal(approvePlanPrompt(), "Carry out the approved plan.");
});

test("a proposal whose reply failed offers no Approve: no history sends that reply", () => {
  const failed = [ask({ planning: true }), { ...reply(proposed), text: "Error: connection lost", error: true }];
  assert.equal(planStatus(failed, 1), "");
  assert.equal(canActOnPlan({ status: planStatus(failed, 1), isLast: true, idle: true, readOnly: false,
    canPlan: true }), false);
  const failedRun = [ask(), { ...reply(planned({ steps: steps("completed", "pending") })), error: true }];
  assert.equal(planStatus(failedRun, 1), "progress", "the steps a failed run marked still show");
});

test("Approve and Revise wait for the last reply, an idle chat and an editor who can plan", () => {
  const ready = { status: "pending", isLast: true, idle: true, readOnly: false, canPlan: true };
  assert.equal(canActOnPlan(ready), true);
  for (const change of [{ status: "approved" }, { status: "replaced" }, { status: "progress" }, { isLast: false },
    { idle: false }, { readOnly: true }, { canPlan: false }]) {
    assert.equal(canActOnPlan({ ...ready, ...change }), false, JSON.stringify(change));
  }
});

test("which messages plan: an explicit answer, then a resent planning message, then the switch", () => {
  assert.equal(sentPlanning({ toggle: true, canPlan: true }), true);
  assert.equal(sentPlanning({ toggle: false, canPlan: true }), false);
  assert.equal(sentPlanning({ planning: false, toggle: true, canPlan: true }), false, "Approve and App's asks never plan");
  assert.equal(sentPlanning({ planning: true, toggle: false, canPlan: true }), true, "a planning Continue plans");
  assert.equal(sentPlanning({ referenceMessage: ask({ planning: true }), toggle: false, canPlan: true }), true,
    "a resent planning message plans again");
  assert.equal(sentPlanning({ referenceMessage: ask(), toggle: true, canPlan: true }), true,
    "a resent normal message follows the switch");
  assert.equal(sentPlanning({ referenceMessage: ask(), toggle: false, canPlan: true }), false);
  for (const asked of [{ toggle: true }, { planning: true }, { referenceMessage: ask({ planning: true }) }]) {
    assert.equal(sentPlanning({ ...asked, canPlan: false }), false, "never where the chat cannot plan");
  }
  assert.equal(sentPlanning(), false);
});

test("Continue keeps the mode of the message it continues, and a load shows the switch so", () => {
  const planning = [ask({ planning: true }), reply(proposed)];
  assert.equal(continuesPlanning(planning), true);
  // After Approve the last message is a normal turn: Continue on the run
  // never plans, and a reload shows Plan off, as Approve left it.
  const run = [...planning, ask({ text: approvePlanPrompt(), approvesPlan: true }), reply()];
  assert.equal(continuesPlanning(run), false);
  assert.equal(continuesPlanning([ask()]), false);
  assert.equal(continuesPlanning([]), false);
  assert.equal(continuesPlanning(undefined), false);
});
