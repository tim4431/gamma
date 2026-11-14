// A turn's steps in one pill, which unfolds to each step (`children`): the
// pill of Gamma AI's replies (ChatDock's AgentSteps) and of the computer
// side's turns (computer/ComputerChat.jsx), each of which words it. It is
// one element from the turn's start to its end, so its `label` morphs
// through every step: while `live` a spinner leads it and its words
// shimmer, while the turn is `waiting` for the person (an approval, a paper
// from their browser, an ask) a shield stands there and nothing spins, and
// once done a check, with `counts` ({failed, declined, held}) of how its
// steps went. `steps`: how many it unfolds to; until one lands it opens
// nothing and takes no focus, and a turn that ends without one folds the
// pill away (`chatStepsFold`). `title` what opening it shows; `meter`
// what ticks beside the label while it is live, not read out (the token
// count); `under` the rows under the pill (the helpers at work). `still`:
// a turn loaded or read again, not this tab's own: it folds and relabels
// at once and is not read out.
import { Chevron, Collapse, MorphText } from "../shared/ui/MotionWidgets";
import { CheckIcon, ShieldIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

export default function StepsPill({
  label, live = false, waiting = false, still = false, steps = 0, counts = {}, title = "", meter = null, under = null, open, onToggle,
  children,
}) {
  const { failed = 0, declined = 0, held = 0 } = counts;
  const done = !live && steps > 0;
  return (
    <Collapse open={live || steps > 0} className="chatStepsFold" still={still}>
      <div className="chatStepsWrap">
        {/* Only the label is a live region, and only on this tab's own
            turns: neither the meter ticking beside it nor a label a load
            changes is read out. */}
        <button type="button" className={`chatPill chatSteps${live ? " live" : ""}`} disabled={!steps}
          onClick={steps ? onToggle : undefined} aria-expanded={steps ? open : undefined}
          title={!steps ? undefined : open ? t("Hide the steps") : title || undefined}>
          {waiting ? <ShieldIcon size={14} /> : done ? <CheckIcon size={14} /> : <span className="transferSpin inline" aria-hidden="true" />}
          <span className={`chatPillText${live && !waiting ? " shimmerText" : ""}`} aria-live={still ? "off" : "polite"} aria-atomic="true">
            <MorphText still={still}>{label}</MorphText>
          </span>
          {live && meter ? <span aria-hidden="true">{meter}</span> : null}
          {failed && done ? <span className="chatStepsFailed">{t("{n} failed", { n: failed })}</span> : null}
          {declined && done ? <span className="chatStepsDeclined">{t("{n} not allowed", { n: declined })}</span> : null}
          {held && done ? <span className="chatStepsDeclined">{t("{n} kept for the plan", { n: held })}</span> : null}
          {steps ? <Chevron open={open} dir="down" size={14} /> : null}
        </button>
        {under}
        <Collapse open={open}>{children}</Collapse>
      </div>
    </Collapse>
  );
}
