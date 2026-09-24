// How the account-scoped settings' sync reads in the Settings dialog: the
// section tag (Section's `scope` + `prefs`) and the Account pane's Gamma
// Cloud row. Pure, so node can test it (tests/syncState.test.mjs).
//
// `local` is useProfileSync's answer (app/prefs.js): {state, error} with
// state "signed-out" | "loading" | "loaded" | "pending" | "pushing" |
// "failed", plus Sets of preference names — `pending`, `inflight`, `failed`,
// `awaitingCloud`. `cloud` is GET /api/auth/cloud/sync-status — {profile:
// {state, at, error}, identity: {linked, username}} — or null until it has
// answered; it is account-wide, so it only colours the sections holding a
// name that is still on its way to the cloud.

// An error message as a sentence, so the next one can follow it.
const sentence = (text) => (/[.!?…]$/.test(text) ? text : `${text}.`);

// "14:37" today, "Sep 3, 14:37" another day, in the viewer's time zone.
export function syncClock(at, now = new Date()) {
  const t = new Date(at);
  if (!at || Number.isNaN(t.getTime())) return "";
  const time = t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return t.toDateString() === now.toDateString() ? time
    : `${t.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}

const tag = (state, icon, title, extra = {}) => ({
  state, icon, title, label: state === "browser" ? "browser" : "account", tone: "", spin: false, ...extra,
});

// The tag of a section in scope "browser" — and of an account section for
// the signed out, the guest account and share views.
export const BROWSER_TAG = tag("browser", "monitor", "Kept in this browser only.");

// The tag of an account section holding the preferences `names`:
// {state, icon, label, title, tone, spin}; `icon` names a glyph (monitor,
// check, cloudCheck, refresh, alert) or is null, `label` is one word.
// - one of `names` settling or on its way to this server → syncing;
// - one of them failed to save here → not synced;
// - one of them pushed since Gamma Cloud last said synced and the cloud
//   reports an error → not synced;
// - else synced with Gamma Cloud when the account is linked, saved here
//   when not. The server's acceptance is the commit: it delivers the change
//   to the cloud on its own schedule (coalesced, retried), so the tag never
//   waits on that hop — only a failure of it is shown.
export function profileSyncState(local, cloud, names = [], clock = syncClock) {
  const at = local?.state || "loading";
  if (at === "signed-out") return BROWSER_TAG;
  const holds = (set) => !!set && names.some((name) => set.has(name));
  if (holds(local?.pending) || holds(local?.inflight)) {
    return tag("syncing", "refresh", "Saving these settings to your account.", { spin: true });
  }
  if (holds(local?.failed)) {
    return tag("error", "alert",
      `Not saved on this server: ${sentence(local?.error || "the server did not answer")} Tried again with your next change.`,
      { tone: "error" });
  }
  if (at === "loading" || !cloud) return tag("account", null, "Saved with your account on this server.");
  const profile = cloud.profile || {};
  const linked = Boolean(cloud.identity?.linked);
  if (!linked || profile.state === "off" || !profile.state) {
    return tag("saved", "check", linked
      ? "Saved on this server. Sign in with Gamma Cloud again to carry these settings to other servers."
      : "Saved on this server. Link a Gamma Cloud account to carry these settings to other servers.");
  }
  if (holds(local?.awaitingCloud)) {
    if (profile.state === "error" || (profile.state === "pending" && profile.error)) {
      return tag("error", "alert",
        `Saved on this server, not synced with Gamma Cloud: ${sentence(profile.error || "unknown error")} Tried again at the next check.`,
        { tone: "error" });
    }
  }
  const when = profile.state === "synced" ? clock(profile.at) : "";
  return tag("synced", "cloudCheck", when ? `Synced with Gamma Cloud at ${when}` : "Synced with Gamma Cloud");
}

// The Account pane's Gamma Cloud row hint when an identity is linked:
// "Settings synced 14:37", "Settings not synced: <error>", "Settings
// syncing…", or "" when there is nothing to say.
export function cloudSyncHint(cloud, clock = syncClock) {
  const profile = cloud?.profile;
  if (!cloud?.identity?.linked || !profile) return "";
  if (profile.state === "synced") return `Settings synced ${clock(profile.at)}`.trim();
  if (profile.state === "error" || (profile.state === "pending" && profile.error)) {
    return `Settings not synced: ${profile.error || "unknown error"}`;
  }
  if (profile.state === "pending") return "Settings syncing…";
  if (profile.state === "off") return "Settings not synced: sign in with Gamma Cloud again";
  return "";
}
