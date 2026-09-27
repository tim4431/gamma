// A tour's finish card (`finishCard` on the tour, docs/dev/onboarding.md):
// what the run made, read from what happened during it. Pure, so node
// tests it.
//
// finishCard: { title, lead, made: [item], next: ["ai", "tours"], footnote }
// item: { icon, text, plural?, args?, event? | step? } — shown when the run
//   saw `event` (its count is {n}, its first payload fills the rest: the
//   paper's {title}) or completed `step` (a demo that ran to its end, a
//   practice step whose event fired); an item with neither always shows.

// A run's record, filled while it goes: event counts, each event's first
// payload, the steps completed.
export const createRunLog = () => ({ counts: {}, first: {}, completed: new Set() });

export function recordEvent(log, name, payload) {
  if (!log) return;
  log.counts[name] = (log.counts[name] || 0) + 1;
  if (!(name in log.first)) log.first[name] = payload || {};
}

// The finish card's "made" lines: { icon, text (a catalog key), plural?, args }.
export function madeItems(finish, log) {
  return (finish?.made || []).flatMap((item) => {
    const base = { icon: item.icon, text: item.text, plural: item.plural, args: { ...item.args } };
    if (item.event) {
      const n = log?.counts[item.event] || 0;
      return n ? [{ ...base, args: { ...log.first[item.event], ...base.args, n } }] : [];
    }
    if (item.step) return log?.completed.has(item.step) ? [base] : [];
    return [base];
  });
}
