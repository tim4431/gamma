// The red dot's model (docs/dev/settings.md "Notices"): what GET /api/notices
// returned, folded into what the UI paints. Pure, so node can test it
// (tests/notices.test.mjs); the polling and the ack live in useNotices.js.
//
// A notice is {id, fingerprint, tone, pane, title}: `tone` is "info" |
// "warn" | "error", `pane` the Settings pane that resolves it. The server
// already dropped the ones this account has seen.

const RANK = { info: 0, warn: 1, error: 2 };
const strongest = (a, b) => ((RANK[b] || 0) > (RANK[a] || 0) ? b : a);

// {tone, panes: {paneId: tone}, firstPane}: `tone` is the strongest of all
// (the account button's dot), `panes` the strongest per pane (the sidebar
// dots), `firstPane` where "Settings…" should land — the pane of the
// strongest notice, the server's order breaking ties. Empty → tone "" and
// firstPane null.
export function summarizeNotices(list) {
  const panes = {};
  let tone = "";
  let firstPane = null;
  for (const notice of list || []) {
    if (!notice?.pane) continue;
    panes[notice.pane] = panes[notice.pane] ? strongest(panes[notice.pane], notice.tone) : notice.tone;
    const next = tone ? strongest(tone, notice.tone) : notice.tone;
    if (next !== tone) firstPane = notice.pane;
    tone = next;
  }
  return { tone, panes, firstPane };
}

// The dot's colour class: an info notice is the accent, anything that
// needs attention is red.
export const dotTone = (tone) => (tone === "info" ? "info" : tone ? "alert" : "");
