// A state as a small tag (`uiTag` in its `tone`): what became of something
// that changes while it is shown, as a thread's turn with an agent
// (comments/comments.js turnChip: the thread's chip, the Comments window's
// row) and a synced folder's agent at work (vault/vaultState.js agentLine:
// the vault chip's card). `state` is {label, title, tone, live, icon}:
// while `live` (work under way) a spinner leads it and its words shimmer,
// and a new label morphs in over the last (MorphText); at rest `icon` (an
// Icons.jsx component, drawn at 14) leads it instead: ShieldIcon while an
// agent waits for the person, AlertCircleIcon for a failure, StopIcon for
// a stop, ClockIcon for a turn in the queue. `tone` is the uiTag's colour:
// "" (neutral), "pending" (accent), "warn", "failed", "ok". The rest goes
// on the tag (`data-turn`, `className`).
import { MorphText } from "./MotionWidgets";

export function StateTag({ state, className = "", ...rest }) {
  const { label, title = "", tone = "", live = false, icon: Icon = null } = state;
  return (
    <span className={["uiTag stateTag", tone, className].filter(Boolean).join(" ")} title={title || undefined} {...rest}>
      {live ? <span className="transferSpin inline" aria-hidden="true" />
        : Icon ? <Icon size={14} aria-hidden="true" /> : null}
      <span className={live ? "shimmerText" : undefined}><MorphText>{label}</MorphText></span>
    </span>
  );
}

// The words behind a state, on a line of their own under what the tag is
// on: its `detail` (an agent's progress, its summary, why it failed), so
// they are read without a hover, in the tag's `tone` (red for failed).
// Nothing for none.
export function StateDetail({ state, className = "" }) {
  if (!state?.detail) return null;
  return <div className={["stateDetail", state.tone, className].filter(Boolean).join(" ")}>{state.detail}</div>;
}
