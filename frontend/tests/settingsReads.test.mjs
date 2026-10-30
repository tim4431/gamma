// The Settings dialog's read store (src/settings/settingsReads.js): one
// request per path however many sections ask, an answer older than a write
// never shown, a pane's reads together with what they lead to.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

// shared/lib/utils.js (behind the store) imports without extensions and
// reads `window` as it loads.
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    try { return await next(specifier, context); } catch (err) {
      if (err?.code === "ERR_MODULE_NOT_FOUND" && /^\\.\\.?\\//.test(specifier) && !/\\.[a-z]+$/.test(specifier)) return next(specifier + ".js", context);
      throw err;
    }
  }`));
globalThis.window = globalThis;
globalThis.location = new URL("http://gamma.test/");
globalThis.dispatchEvent = () => true;

// The server: each path's answer, a gate a request may wait on, and every
// path asked for, in order.
const answers = new Map();
const gates = new Map();
let asked = [];
globalThis.fetch = async (url) => {
  const path = new URL(String(url), location.href).pathname.replace(/^\/api/, "");
  asked.push(path);
  if (gates.has(path)) await gates.get(path);
  const body = answers.get(path);
  return body === undefined
    ? new Response(JSON.stringify({ detail: "gone" }), { status: 404, headers: { "Content-Type": "application/json" } })
    : new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
};
const gate = (path) => { let open; gates.set(path, new Promise((resolve) => { open = resolve; })); return () => { gates.delete(path); open(); }; };

const { createSettingsReads, PANE_READS, backupsOf } = await import("../src/settings/settingsReads.js");
const { LINK_BASES } = await import("../src/collaboration/syncLinks.js");

test("sections asking for one path together make one request, and a fresh answer is taken, not asked for again", async () => {
  asked = [];
  answers.set("/admin/settings", { public_url: "" });
  const reads = createSettingsReads();
  const all = await Promise.all([1, 2, 3, 4, 5].map(() => reads.read("/admin/settings")));
  assert.deepEqual(asked, ["/admin/settings"]);
  assert.ok(all.every((value) => value.public_url === ""));
  await reads.read("/admin/settings");
  assert.deepEqual(asked, ["/admin/settings"], "a fresh answer is reused");
  await reads.read("/admin/settings", { force: true });
  assert.equal(asked.length, 2, "force reads again");
});

test("a read that lands after a write brings an older copy and is dropped; a failed one keeps what was shown", async () => {
  asked = [];
  answers.set("/admin/settings", { quota_mb: 1 });
  const reads = createSettingsReads();
  await reads.read("/admin/settings");
  const open = gate("/admin/settings");
  const again = reads.read("/admin/settings", { force: true });
  reads.set("/admin/settings", { quota_mb: 5 }); // a save answered meanwhile
  open();
  await again;
  assert.equal(reads.get("/admin/settings").data.quota_mb, 5, "the write's answer stays");
  answers.delete("/admin/settings");
  await reads.read("/admin/settings", { force: true }).catch(() => {});
  assert.equal(reads.get("/admin/settings").data.quota_mb, 5, "a failed read keeps the data");
  assert.match(reads.get("/admin/settings").error, /gone/);
  assert.ok(reads.has("/admin/settings"));
});

test("a pane's reads go out together with what they lead to; a prefetch reads only what is missing, a visit reads again", async () => {
  asked = [];
  answers.set("/workspaces/mine", { workspaces: [{ id: "w1" }, { id: "w2" }] });
  answers.set("/backup-tasks", { tasks: [] });
  answers.set(backupsOf("w1"), { backups: [], max: 5 });
  answers.set(backupsOf("w2"), { backups: [], max: 5 });
  const reads = createSettingsReads();
  const who = { admin: false, signedIn: true };
  assert.equal(reads.ready("backups", who), false);
  await reads.pane("backups", who, { missing: true });
  assert.deepEqual(asked.slice(0, 2).sort(), ["/backup-tasks", "/workspaces/mine"], "the pane's reads go out together");
  assert.deepEqual(asked.slice(2).sort(), [backupsOf("w1"), backupsOf("w2")], "each workspace's list once its workspaces are in");
  assert.ok(reads.ready("backups", who));
  asked = [];
  await reads.pane("backups", who, { missing: true });
  assert.deepEqual(asked, [], "a prefetch reads nothing it has");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(!PANE_READS.backups(who).includes("/admin/offsite"), "an account that is not an admin reads no off-site copies");
  assert.ok(PANE_READS.backups({ admin: true }).includes("/admin/offsite"));
  assert.equal(PANE_READS.server().filter((path) => path === "/admin/settings").length, 1, "the Server pane reads its settings once");
});

test("every path a section reads through the store is one of a pane's reads", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const dir = path.join(path.dirname(fileURLToPath(import.meta.url)), "../src/settings");
  const who = { admin: true, signedIn: true };
  const listed = new Set(Object.values(PANE_READS).flatMap((reads) => reads(who)).filter(Boolean)
    .map((read) => (typeof read === "string" ? read : read.path)));
  for (const file of fs.readdirSync(dir).filter((name) => name.endsWith(".jsx"))) {
    const source = fs.readFileSync(path.join(dir, file), "utf8");
    for (const [, read] of source.matchAll(/useSettingsRead\((?:[^"()]*\? )?"([^"]+)"/g)) {
      assert.ok(listed.has(read), `${file} reads ${read}, which no pane names in PANE_READS`);
    }
    // the clones' and the links' lists, read by their API base (syncLinks.js LINK_BASES)
    for (const [, kind] of source.matchAll(/useSettingsRead\([^()]*LINK_BASES\.(\w+)/g)) {
      assert.ok(listed.has(LINK_BASES[kind]), `${file} reads LINK_BASES.${kind}, which no pane names in PANE_READS`);
    }
  }
  assert.deepEqual(PANE_READS.workspaces(who).filter((read) => Object.values(LINK_BASES).includes(read)), ["/mirrors", "/zotero/links"]);
});

test("a read asked for again after a change supersedes the one on its way, which lands for nothing", async () => {
  asked = [];
  answers.set("/admin/workspaces", { workspaces: [] });
  const reads = createSettingsReads();
  const open = gate("/admin/workspaces");
  const first = reads.read("/admin/workspaces");
  answers.set("/admin/workspaces", { workspaces: [{ id: "new" }] }); // made meanwhile
  gates.delete("/admin/workspaces");
  const again = await reads.read("/admin/workspaces", { force: true });
  assert.equal(again.workspaces.length, 1);
  answers.set("/admin/workspaces", { workspaces: [] });
  open();
  await first;
  assert.equal(reads.get("/admin/workspaces").data.workspaces.length, 1, "the older read does not take the newer one's place");
  answers.set("/admin/workspaces", { workspaces: [{ id: "a" }, { id: "b" }] });
  await reads.read("/admin/workspaces", { force: true });
  assert.equal(reads.get("/admin/workspaces").data.workspaces.length, 2, "and nothing stays on its way");
  reads.set("/admin/workspaces", { workspaces: [] });
  const open2 = gate("/admin/workspaces");
  const late = reads.read("/admin/workspaces", { force: true });
  reads.set("/admin/workspaces", { workspaces: [{ id: "saved" }] });
  open2();
  await late;
  assert.equal(reads.get("/admin/workspaces").data.workspaces[0].id, "saved", "a write since it was sent wins");
});
