// A reply's plan, in the approval card's look (chat/plan.js has the rules,
// docs/dev/ai.md "Planning first"): the steps the assistant proposed with
// update_plan, numbered, and a badge for where the plan stands. A pending
// plan under the conversation's last reply offers Approve plan, which
// carries it out in a normal turn, and Revise, which keeps Plan on and goes
// to the message box. A plan the assistant marks while it works shows each
// step's state and "2 of 4 done". It never raises the approvals hint and
// carries no guide anchor. The buttons never take the focus by themselves.
import React from "react";
import { t } from "../shared/i18n/i18n.js";
import { CheckIcon, ListOrderedIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import ListMotion from "../shared/ui/ListMotion";
import { planProgress } from "./plan.js";

// Each step by its text (a second step of the same text by its count; a
// step's text has no line break), so a step the assistant adds or drops
// while it works moves the others.
function stepKeys(steps) {
  const seen = new Map();
  return steps.map((s) => {
    const n = seen.get(s.step) || 0;
    seen.set(s.step, n + 1);
    return n ? `${s.step}\n${n}` : s.step;
  });
}

function StepMark({ status, n, live }) {
  if (status === "completed") {
    return <span className="chatPlanMark done" role="img" aria-label={t("Done")}><CheckIcon size={14} /></span>;
  }
  if (status === "in_progress") {
    return <span className="chatPlanMark" role="img" aria-label={t("In progress")}>
      {live ? <span className="transferSpin inline" /> : <span className="chatPlanDot" />}
    </span>;
  }
  return <span className="chatPlanMark" role="img" aria-label={t("Step {n}, not started", { n })}>{n}</span>;
}

// `status`: planStatus's. `live`: this tab's reply, still streaming.
export default function PlanCard({ plan, status, live = false, still = false, canAct = false, onApprove, onRevise }) {
  const steps = plan?.steps || [];
  const { done, total } = planProgress(plan);
  const badge = status === "pending" ? t("Awaiting your approval")
    : status === "approved" ? t("Approved")
      : status === "progress" ? t("{done} of {total} done", { done, total }) : "";
  const keys = stepKeys(steps);
  return (
    <div className={`chatApproval chatPlan${status === "pending" ? "" : " sent"}`} role="group" aria-label={t("Plan")}>
      <div className="chatApprovalCaption">
        <ListOrderedIcon size={14} />{t("Plan")}
        {badge ? <span className="chatPlanBadge" aria-live={live ? "polite" : "off"} aria-atomic="true">
          · <MorphText still={still}>{badge}</MorphText>
        </span> : null}
      </div>
      <ListMotion keys={keys} still={still}>
        <ol className="chatPlanSteps">
          {steps.map((s, i) => (
            <li key={keys[i]} data-motion-key={keys[i]} className={s.status === "completed" ? "done" : ""}>
              <StepMark status={s.status} n={i + 1} live={live} />
              <span className="chatPlanStep">{s.step}</span>
            </li>
          ))}
        </ol>
      </ListMotion>
      {canAct ? (
        <div className="chatApprovalActions">
          <button type="button" className="uiBtn sm primary" onClick={onApprove}
            title={t("Carry out this plan. Changes still ask where your settings say Ask")}>{t("Approve plan")}</button>
          <button type="button" className="uiBtn sm" onClick={onRevise}
            title={t("Ask for changes to the plan")}>{t("Revise")}</button>
        </div>
      ) : null}
    </div>
  );
}
