// The rows of Settings → AI → Agents → Servers this computer answers and the
// shell asks their controls send (src/settings/answeredServers.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import { ADD_SERVER, hostOf, linkRow, linkState } from "../src/settings/answeredServers.js";

const link = (over = {}) => ({ id: "L1", remote_url: "https://nas.example:8443", workspace_name: "Lab", username: "tim",
  status: { state: "connected", error: "", at: "2026-11-13T14:02:00Z" }, asks: false, ...over });

test("a link's row: its host and workspace, its address, the account there and how it stands", () => {
  const row = linkRow(link());
  assert.deepEqual([row.server, row.label, row.hint], ["nas.example", "nas.example: Lab", "https://nas.example:8443 · as tim · Connected"]);
  assert.equal(linkRow(link({ workspace_name: "", username: "" })).label, "nas.example");
  assert.equal(linkRow(link({ workspace_name: "", username: "" })).hint, "https://nas.example:8443 · Connected");
  assert.equal(hostOf("not a url"), "not a url");
  assert.equal(hostOf(""), "");
});

test("how its channel stands, since when where it is not connected", () => {
  assert.equal(linkState({ state: "connected", at: "2026-11-13T14:02:00Z" }), "Connected");
  assert.match(linkState({ state: "refused", at: "2026-11-13T14:02:00Z" }), /^Refused this computer's token since /);
  assert.equal(linkState({ state: "refused" }), "Refused this computer's token");
  assert.equal(linkState({ state: "older" }), "Older than this app: it cannot show this computer");
  assert.match(linkState({ state: "unreachable", at: "2026-11-13T14:02:00Z" }), /^Not reachable since /);
  assert.equal(linkState({ state: "unreachable" }), "Not reachable");
  assert.equal(linkState(undefined), "Connecting…");
});

test("each control's shell ask: Reconnect… on a refused link alone, Remove, the switch, Add a server…", () => {
  assert.equal(linkRow(link()).reconnect, null);
  assert.deepEqual(linkRow(link({ status: { state: "refused" } })).reconnect, ["add-server", { link: "L1" }]);
  assert.deepEqual(linkRow(link()).remove, ["remove-server", { link: "L1" }]);
  assert.deepEqual(ADD_SERVER, ["add-server", { link: "new" }]);
  const off = linkRow(link()).asks;
  assert.deepEqual([off.label, off.on, off.hint], ["nas.example may answer what the agents ask", false,
    "What an agent here asks to run is answered on this computer only."]);
  assert.deepEqual(off.set(true), ["link-asks", { link: "L1", asks: true }]);
  const on = linkRow(link({ asks: true })).asks;
  assert.equal(on.on, true);
  assert.match(on.hint, /in any window signed in to nas\.example as you, and once by a person you let use the agent, for their own turn\.$/);
  assert.deepEqual(on.set(false), ["link-asks", { link: "L1", asks: false }]);
});
