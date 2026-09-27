// The red dot's model (docs/dev/settings.md "Notices"): what GET /api/notices
// returned, folded into what the UI paints. Pure, so node can test it
// (tests/notices.test.mjs); the polling and the ack live in useNotices.js.
//
// A notice is {id, fingerprint, tone, pane, title, message, params}: `tone`
// is "info" | "warn" | "error", `pane` the Settings pane that resolves it,
// `message` + `params` the sentence as a catalog key and its values (the
// English `title` is the same sentence filled in, for older servers and API
// readers). The server already dropped the ones this account has seen.
import { t, T } from "../shared/i18n/i18n.js";

// Every sentence gamma/notices.py sends, so the catalog carries them
// (tests/notices.test.mjs checks the server's list against this one).
export const NOTICE_MESSAGES = [
  T("Gamma v{version} is available — this server runs v{current}"),
  T("New errors in the server log"),
  T("The backup task “{name}” failed"),
  T("{n} backup tasks failed"),
  T("{n} sync conflict to look at in your clones"),
  T("{n} sync conflicts to look at in your clones"),
  T("{n} sync conflict to look at in your published pages"),
  T("{n} sync conflicts to look at in your published pages"),
  T("Gamma Cloud sync failed: {error}"),
  T("Gamma Cloud sync failed"),
  T("Your settings here and on Gamma Cloud differ: choose which to keep"),
  T("Microsoft's free translation keeps failing — set up Google or Youdao"),
  T("Your storage is full ({used} of {quota} MB used)"),
  T("Your storage is nearly full ({used} of {quota} MB used)"),
];

// A notice's sentence in the interface language; a message this build does
// not know (a newer server) shows as the server's English title.
export function noticeText(notice) {
  if (notice?.message && NOTICE_MESSAGES.includes(notice.message)) return t(notice.message, notice.params || {});
  return notice?.title || "";
}

// The link that opens the notice's pane: named for what the pane lets you
// do there, else just the pane.
const ACTIONS = {
  update: T("See the update"),
  "log-errors": T("Open the server log"),
  "backup-failed": T("Review backups"),
  "mirror-conflicts": T("Resolve the conflicts"),
  "publish-conflicts": T("Resolve the conflicts"),
  "cloud-sync": T("Review the sync"),
  "cloud-sync-choice": T("Choose a copy"),
  "free-translate": T("Set up translation"),
  storage: T("Review storage"),
};
export const noticeAction = (notice) => t(ACTIONS[notice?.id] || T("Open settings"));

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
