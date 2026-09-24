// How the account-scoped settings' sync reads in the Settings dialog: the
// section tag (Section's `scope="account"`) and the Account pane's Gamma
// Cloud row. Pure, so node can test it (tests/syncState.test.mjs).
//
// `local` is useProfileSync's state (app/prefs.js): {state, error} with
// state "signed-out" | "loading" | "loaded" | "pending" | "pushing" | "failed".
// `cloud` is GET /api/auth/cloud/sync-status — {profile: {state, at, error},
// identity: {linked, username}} — or null until it has answered.

const ACCOUNT = "Your account";

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

// {state, icon, label, title, tone, spin}; `icon` names a glyph (check,
// cloudCheck, refresh, alert) or is null.
export function profileSyncState(local, cloud, clock = syncClock) {
  const at = local?.state || "loading";
  if (at === "signed-out") {
    return { state: "browser", icon: null, label: "This browser", title: "Kept in this browser only.", tone: "", spin: false };
  }
  if (at === "failed") {
    return { state: "error", icon: "alert", label: `${ACCOUNT} · not synced`, tone: "error", spin: false,
      title: `Not saved on this server: ${sentence(local.error || "the server did not answer")} Tried again with your next change.` };
  }
  if (at === "pending" || at === "pushing") {
    return { state: "syncing", icon: "refresh", label: `${ACCOUNT} · syncing`, tone: "", spin: true,
      title: "Saving your changes to your account." };
  }
  if (at !== "loaded" || !cloud) {
    return { state: "account", icon: null, label: ACCOUNT, title: "Saved with your account on this server.", tone: "", spin: false };
  }
  const profile = cloud.profile || {};
  const linked = Boolean(cloud.identity?.linked);
  if (!linked || profile.state === "off" || !profile.state) {
    return { state: "saved", icon: "check", label: ACCOUNT, tone: "", spin: false,
      title: linked
        ? "Saved on this server. Sign in with Gamma Cloud again to carry these settings to other servers."
        : "Saved on this server. Link a Gamma Cloud account to carry these settings to other servers." };
  }
  if (profile.state === "error" || (profile.state === "pending" && profile.error)) {
    return { state: "error", icon: "alert", label: `${ACCOUNT} · not synced`, tone: "error", spin: false,
      title: `Saved on this server, not synced with Gamma Cloud: ${sentence(profile.error || "unknown error")} Tried again at the next check.` };
  }
  if (profile.state === "pending") {
    return { state: "syncing", icon: "refresh", label: `${ACCOUNT} · syncing`, tone: "", spin: true,
      title: "Saved on this server; sending to Gamma Cloud." };
  }
  const when = clock(profile.at);
  return { state: "synced", icon: "cloudCheck", label: `${ACCOUNT} · synced`, tone: "", spin: false,
    title: when ? `Synced with Gamma Cloud at ${when}` : "Synced with Gamma Cloud" };
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
