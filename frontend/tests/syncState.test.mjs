import test from "node:test";
import assert from "node:assert/strict";
import { cloudSyncHint, profileSyncState, syncClock } from "../src/settings/syncState.js";

const clock = (at) => (at ? `T(${at})` : "");
const cloud = (profile, linked = true) => ({ profile: { at: "", error: "", ...profile }, identity: linked ? { linked: true, username: "ann" } : { linked: false } });

test("signed out, guests and share views: the tag says this browser, no icon", () => {
  const tag = profileSyncState({ state: "signed-out" }, null, clock);
  assert.deepEqual([tag.state, tag.label, tag.icon], ["browser", "This browser", null]);
});

test("before the first load or the server's answer: the plain account tag", () => {
  for (const [local, remote] of [[undefined, null], [{ state: "loading" }, null], [{ state: "loaded" }, null]]) {
    const tag = profileSyncState(local, remote, clock);
    assert.deepEqual([tag.state, tag.label, tag.icon], ["account", "Your account", null]);
  }
});

test("saved on this server without a cloud identity: the check, and how to carry it further", () => {
  const tag = profileSyncState({ state: "loaded" }, cloud({ state: "off" }, false), clock);
  assert.deepEqual([tag.state, tag.label, tag.icon, tag.tone], ["saved", "Your account", "check", ""]);
  assert.equal(tag.title, "Saved on this server. Link a Gamma Cloud account to carry these settings to other servers.");
  // linked, but the grant is gone (or cloud sign-in is off): still saved here, sign in again
  const linkedOff = profileSyncState({ state: "loaded" }, cloud({ state: "off" }), clock);
  assert.equal(linkedOff.state, "saved");
  assert.match(linkedOff.title, /Sign in with Gamma Cloud again/);
});

test("synced with Gamma Cloud: the cloud check and the time", () => {
  const tag = profileSyncState({ state: "loaded" }, cloud({ state: "synced", at: "2026-09-24T14:37:00.000000Z" }), clock);
  assert.deepEqual([tag.state, tag.label, tag.icon, tag.spin], ["synced", "Your account · synced", "cloudCheck", false]);
  assert.equal(tag.title, "Synced with Gamma Cloud at T(2026-09-24T14:37:00.000000Z)");
});

test("a change on its way, here or to the cloud: syncing, the refresh glyph spinning", () => {
  for (const [local, remote] of [
    [{ state: "pending" }, null],
    [{ state: "pushing" }, cloud({ state: "synced" })],
    [{ state: "loaded" }, cloud({ state: "pending" })],
  ]) {
    const tag = profileSyncState(local, remote, clock);
    assert.deepEqual([tag.state, tag.label, tag.icon, tag.spin], ["syncing", "Your account · syncing", "refresh", true]);
  }
});

test("failures: not synced, a warning in red, the error on hover", () => {
  const here = profileSyncState({ state: "failed", error: "Network error" }, cloud({ state: "synced" }), clock);
  assert.deepEqual([here.state, here.label, here.icon, here.tone], ["error", "Your account · not synced", "alert", "error"]);
  assert.match(here.title, /Network error\. /);
  const there = profileSyncState({ state: "loaded" }, cloud({ state: "error", error: "cannot reach the account server" }), clock);
  assert.equal(there.state, "error");
  assert.match(there.title, /cannot reach the account server\. Tried again at the next check\./);
  // a push that failed and waits for the next check reads as not synced, not as a spinner for an hour
  const waiting = profileSyncState({ state: "loaded" }, cloud({ state: "pending", error: "offline." }), clock);
  assert.deepEqual([waiting.state, waiting.spin], ["error", false]);
  assert.match(waiting.title, /offline\. Tried/);
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
