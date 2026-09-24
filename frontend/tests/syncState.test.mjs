import test from "node:test";
import assert from "node:assert/strict";
import { BROWSER_TAG, cloudSyncHint, profileSyncState, syncClock } from "../src/settings/syncState.js";

const clock = (at) => (at ? `T(${at})` : "");
const cloud = (profile, linked = true) => ({ profile: { at: "", error: "", ...profile }, identity: linked ? { linked: true, username: "ann" } : { linked: false } });
// useProfileSync's answer: a state plus Sets of preference names
const local = (state, sets = {}, error = "") => ({
  state, error,
  ...Object.fromEntries(["pending", "inflight", "failed", "awaitingCloud"].map((k) => [k, new Set(sets[k] || [])])),
});
const NOTES = ["enterNewNote"];
const THEME = ["theme"];
const read = (tag) => [tag.state, tag.label, tag.icon];

test("signed out, guests and share views: the browser tag, a monitor and one word", () => {
  const tag = profileSyncState({ state: "signed-out" }, null, NOTES, clock);
  assert.equal(tag, BROWSER_TAG);
  assert.deepEqual(read(tag), ["browser", "browser", "monitor"]);
  assert.equal(tag.title, "Kept in this browser only.");
});

test("before the first load or the server's answer: the plain account word, no icon", () => {
  for (const [l, remote] of [[undefined, null], [local("loading"), null], [local("loaded"), null]]) {
    assert.deepEqual(read(profileSyncState(l, remote, THEME, clock)), ["account", "account", null]);
  }
});

test("unlinked: the check, and how to carry the settings further", () => {
  const tag = profileSyncState(local("loaded", { awaitingCloud: THEME }), cloud({ state: "off" }, false), THEME, clock);
  assert.deepEqual([...read(tag), tag.tone], ["saved", "account", "check", ""]);
  assert.equal(tag.title, "Saved on this server. Link a Gamma Cloud account to carry these settings to other servers.");
  // linked, but the grant is gone (or cloud sign-in is off): still saved here, sign in again
  const linkedOff = profileSyncState(local("loaded"), cloud({ state: "off" }), THEME, clock);
  assert.equal(linkedOff.state, "saved");
  assert.match(linkedOff.title, /Sign in with Gamma Cloud again/);
});

test("synced with Gamma Cloud: the cloud check and the time", () => {
  const tag = profileSyncState(local("loaded"), cloud({ state: "synced", at: "2026-09-24T14:37:00.000000Z" }), THEME, clock);
  assert.deepEqual([...read(tag), tag.spin], ["synced", "account", "cloudCheck", false]);
  assert.equal(tag.title, "Synced with Gamma Cloud at T(2026-09-24T14:37:00.000000Z)");
});

test("a change to one name spins only the sections holding it", () => {
  for (const sets of [{ pending: NOTES }, { inflight: NOTES }, { pending: NOTES, inflight: NOTES }]) {
    const l = local("pushing", sets);
    for (const remote of [null, cloud({ state: "synced", at: "x" }), cloud({ state: "off" }, false)]) {
      const notes = profileSyncState(l, remote, NOTES, clock);
      assert.deepEqual([...read(notes), notes.spin], ["syncing", "account", "refresh", true]);
      assert.notEqual(profileSyncState(l, remote, THEME, clock).state, "syncing");
    }
  }
  // a section holding several names spins for any of them
  assert.equal(profileSyncState(local("pending", { pending: NOTES }), null, ["theme", "enterNewNote"], clock).state, "syncing");
  // a section without names never spins
  assert.equal(profileSyncState(local("pending", { pending: NOTES }), null, [], clock).state, "account");
});

test("a failed save here: not synced in red, only for the names that failed", () => {
  const l = local("failed", { failed: NOTES }, "Network error");
  const notes = profileSyncState(l, cloud({ state: "synced" }), NOTES, clock);
  assert.deepEqual([...read(notes), notes.tone], ["error", "account", "alert", "error"]);
  assert.match(notes.title, /Network error\. Tried again with your next change\./);
  assert.equal(profileSyncState(l, cloud({ state: "synced" }), THEME, clock).state, "synced");
  // a change made again is pending, which wins over the failure
  assert.equal(profileSyncState(local("pending", { failed: NOTES, pending: NOTES }), null, NOTES, clock).state, "syncing");
});

test("the cloud's error applies only to names still on their way to it; its pending never spins", () => {
  const awaiting = local("loaded", { awaitingCloud: NOTES });
  const pending = cloud({ state: "pending" });
  // the server accepted the change and delivers it on its own: no spinner for that hop
  const notes = profileSyncState(awaiting, pending, NOTES, clock);
  assert.deepEqual([notes.state, notes.spin], ["synced", false]);
  assert.equal(profileSyncState(awaiting, pending, THEME, clock).state, "synced");

  const error = cloud({ state: "error", error: "cannot reach the account server" });
  const failed = profileSyncState(awaiting, error, NOTES, clock);
  assert.deepEqual([failed.state, failed.icon, failed.tone], ["error", "alert", "error"]);
  assert.match(failed.title, /cannot reach the account server\. Tried again at the next check\./);
  assert.equal(profileSyncState(awaiting, error, THEME, clock).state, "synced");
  // an error before any change this session is shown on no section
  assert.equal(profileSyncState(local("loaded"), error, NOTES, clock).state, "synced");
  // a push that failed and waits for the next check reads as not synced, not as a spinner for an hour
  const waiting = profileSyncState(awaiting, cloud({ state: "pending", error: "offline." }), NOTES, clock);
  assert.deepEqual([waiting.state, waiting.spin], ["error", false]);
  assert.match(waiting.title, /offline\. Tried/);
  // once the cloud has it, the hook clears the awaiting names and the section is synced
  assert.equal(profileSyncState(local("loaded"), cloud({ state: "synced", at: "x" }), NOTES, clock).state, "synced");
});

test("the Account pane's cloud row hint", () => {
  assert.equal(cloudSyncHint(null, clock), "");
  assert.equal(cloudSyncHint(cloud({ state: "off" }, false), clock), "");
  assert.equal(cloudSyncHint(cloud({ state: "synced", at: "x" }), clock), "Settings synced T(x)");
  assert.equal(cloudSyncHint(cloud({ state: "error", error: "boom" }), clock), "Settings not synced: boom");
  assert.equal(cloudSyncHint(cloud({ state: "pending", error: "boom" }), clock), "Settings not synced: boom");
  assert.equal(cloudSyncHint(cloud({ state: "pending" }), clock), "Settings syncing…");
});

test("the clock: the time today, the date as well on another day, nothing for no time", () => {
  const now = new Date(2026, 8, 24, 16, 0);
  const today = syncClock(new Date(2026, 8, 24, 14, 37).toISOString(), now);
  assert.match(today, /^\D*14\D37|2:37/);
  assert.ok(!today.includes(","));
  assert.ok(syncClock(new Date(2026, 8, 3, 14, 37).toISOString(), now).includes(","));
  assert.equal(syncClock("", now), "");
  assert.equal(syncClock("not a time", now), "");
});
