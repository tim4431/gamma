// A capacity ring, Claude Code's context ring: the used share of a whole drawn
// as an arc over a faint track, amber from 80% and red from 95%. The chat's
// context counter draws it small in the icon colour; the account popover's
// storage and server-AI meters draw it larger in the accent. `fraction` null
// draws the track alone (no limit to measure against).
export function UsageRing({ fraction, size = 15, className = "" }) {
  const f = fraction == null ? 0 : Math.max(0, Math.min(1, fraction));
  const state = fraction == null ? "" : f >= 0.95 ? " full" : f >= 0.8 ? " warn" : "";
  const c = 2 * Math.PI * 6;
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" className={`usageRing${state}${className ? ` ${className}` : ""}`} aria-hidden="true">
      <circle cx="8" cy="8" r="6" className="usageRingTrack" />
      {f ? <circle cx="8" cy="8" r="6" className="usageRingFill" strokeDasharray={`${Math.max(f * c, 0.6)} ${c}`} transform="rotate(-90 8 8)" /> : null}
    </svg>
  );
}
