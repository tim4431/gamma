// A state as a small tag (`uiTag` in its `tone`): what became of something
// that changes while it is shown, as a Send's (comments/comments.js
// sendState: the thread's chip, the Comments window's row, the feedback
// view's entries and request rows). `state` is {label, title, tone, live}:
// while `live` (work under way) a spinner leads it and its words shimmer,
// and a new label morphs in over the last (MorphText). The rest goes on
// the tag (`data-sent`, `className`).
import { MorphText } from "./MotionWidgets";

export function StateTag({ state, className = "", ...rest }) {
  const { label, title = "", tone = "", live = false } = state;
  return (
    <span className={["uiTag stateTag", tone, className].filter(Boolean).join(" ")} title={title || undefined} {...rest}>
      {live ? <span className="transferSpin inline" aria-hidden="true" /> : null}
      <span className={live ? "shimmerText" : undefined}><MorphText>{label}</MorphText></span>
    </span>
  );
}
