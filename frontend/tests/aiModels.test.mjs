// The account's models for each task (src/ai/aiModels.js): how a pick
// resolves, the server's rule case for case (tests/shared/ai_models.json),
// what removing a connection does to the tasks, what a task's row says, the
// one-time move of this browser's old picks into the account; and which
// answers useAiModels lets land (src/ai/modelsSync.js).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { importLegacyPicks, removalNotice, resolveRoles, roleStatus, tasksAffectedBy, withPick } from "../src/ai/aiModels.js";
import { createModelsSync } from "../src/ai/modelsSync.js";

const SHARED = JSON.parse(readFileSync(new URL("../../tests/shared/ai_models.json", import.meta.url), "utf8"));
const ROLES = ["chat", "metadata", "translate", "ink"];

test("each task's model resolves as the server resolves it", () => {
  for (const c of SHARED.roles.cases) {
    assert.deepEqual(resolveRoles(c.models, c.picks, c.engines), c.roles, c.note);
  }
});

test("removing a connection names the tasks whose model changes and what each falls to", () => {
  for (const c of SHARED.roles.cases.filter((c) => c.without)) {
    const left = c.models.filter((m) => m.provider !== c.without.provider);
    assert.deepEqual(resolveRoles(left, c.picks, c.engines), c.without.roles, c.note);
    const info = { models: c.models, roles: c.roles, translate_engines: c.engines.map((id) => ({ id, label: id })) };
    const { affected, noneLeft } = tasksAffectedBy(c.without.provider, info);
    assert.deepEqual(affected, ROLES.filter((role) => c.roles[role].model !== c.without.roles[role].model)
      .map((role) => ({ role, from: c.roles[role].model, to: c.without.roles[role].model })), c.note);
    assert.equal(noneLeft, !left.length, c.note);
  }
});

const model = (provider, name, extra = {}) => ({ id: `${provider}:${name}`, provider, provider_name: `${provider} key`, model: name, ...extra });
const A1 = model("work", "gpt-6", { billing: "api" }), A2 = model("work", "gpt-mini"), B1 = model("home", "qwen");

test("a pick shows at once as the server will resolve it: the tasks that follow the chat follow a new chat pick", () => {
  const info = { models: [A1, A2, B1], roles: resolveRoles([A1, A2, B1], { metadata: B1.id }), translate_engines: [], chat_migrated: true };
  const next = withPick(info, "chat", A2.id);
  assert.deepEqual([next.roles.chat.model, next.roles.metadata.model, next.roles.ink.model], [A2.id, B1.id, A2.id]);
  assert.equal(next.chat_migrated, false, "the chat's pick is the user's own now");
  assert.equal(withPick(info, "ink", B1.id).roles.ink.state, "ok");
});

test("the remove confirm names what each task will use instead, and that the AI stops with nothing left", () => {
  const info = { models: [A1], roles: resolveRoles([A1], {}, ["engine:microsoft"]), translate_engines: [{ id: "engine:microsoft", label: "Microsoft" }] };
  const lines = removalNotice(A1.provider, info);
  assert.equal(lines.length, 2);
  assert.ok(lines[0].includes("Microsoft"), "translation falls to the free service");
  assert.ok(!removalNotice(B1.provider, { ...info, models: [A1, B1] }).length, "a connection no task uses changes nothing");
});

test("a task's row names its model's connection; a pick no longer offered its model, its connection and what serves instead", () => {
  const gone = "work:claude-old", revoked = "server:lab:lab-m";
  const info = {
    models: [A1], translate_engines: [],
    roles: { chat: { pick: A1.id, model: A1.id, state: "ok" }, metadata: { pick: gone, model: A1.id, state: "missing" },
      translate: { pick: "engine:google", model: A1.id, state: "missing" }, ink: { pick: revoked, model: A1.id, state: "missing" } },
    missing: { [gone]: { model: "claude-old", connection: "" }, [revoked]: { model: "lab-m", connection: "Lab" } },
  };
  const chat = roleStatus("chat", info);
  assert.equal(chat.state, "ok");
  assert.ok(chat.text.includes("work key"));
  const metadata = roleStatus("metadata", info);
  assert.equal(metadata.state, "missing");
  for (const name of ["claude-old", "a removed connection", "gpt-6", "work key"]) assert.ok(metadata.text.includes(name), `${name} in ${metadata.text}`);
  const ink = roleStatus("ink", info);
  assert.ok(ink.text.includes("lab-m") && ink.text.includes("Lab"), "a connection still there is named");
  const translate = roleStatus("translate", info);
  assert.equal(translate.state, "missing");
  assert.ok(translate.text.includes("gpt-6"), "what translates instead");
  assert.equal(roleStatus("ink", { ...info, roles: { ...info.roles, ink: { pick: "", model: A1.id, state: "follows" } } }).state, "follows");
});

// localStorage's shape over a Map.
function storage(entries) {
  const map = new Map(Object.entries(entries));
  return { map, getItem: (k) => (map.has(k) ? map.get(k) : null), removeItem: (k) => map.delete(k) };
}

test("the old picks move once, and their keys go only after the import succeeded", async () => {
  const keys = { "gamma-chat-model": "work:gpt-6", "gamma-meta-model": "home:qwen", "gamma-translate-model": "engine:google",
    "gamma-chat-model-by-key": "{}", "gamma-theme": "dark" };
  const failing = storage(keys);
  await assert.rejects(importLegacyPicks(failing, async () => { throw new Error("offline"); }));
  assert.equal(failing.map.size, 5, "a failed import keeps every key for the next load");

  const ok = storage(keys);
  const sent = [];
  const answer = await importLegacyPicks(ok, async (body) => { sent.push(body); return { taken: ["metadata"] }; });
  assert.deepEqual(sent, [{ chat: "work:gpt-6", metadata: "home:qwen", translate: "engine:google" }]);
  assert.deepEqual(answer, { taken: ["metadata"] });
  assert.deepEqual([...ok.map.keys()], ["gamma-theme"], "the old keys are gone, nothing else");
});

test("empty old picks are not sent; nothing stored asks nothing", async () => {
  const old = storage({ "gamma-chat-model-by-key": "{}", "gamma-meta-model": "" });
  let asked = 0;
  assert.equal(await importLegacyPicks(old, async () => { asked += 1; }), null);
  assert.equal(asked, 0);
  assert.equal(old.map.size, 0, "the leftovers go without a request");
  assert.equal(await importLegacyPicks(storage({}), async () => { asked += 1; }), null);
  assert.equal(asked, 0);
});

// useAiModels' requests over a fake server: each request waits until the
// test answers it (or its abort rejects it), and the hook's state is a
// plain variable.
function sync({ store = null } = {}) {
  const sent = [];
  let info = null;
  const errors = [];
  const it = createModelsSync({
    request: (path, init) => new Promise((resolve, reject) => {
      sent.push({ path, body: init.body ? JSON.parse(init.body) : null, signal: init.signal, resolve, reject });
      init.signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
    }),
    setInfo: (next) => { info = typeof next === "function" ? next(info) : next; },
    setFailed: () => {},
    onError: (message) => errors.push(message),
    canWrite: () => true,
    storage: () => store,
  });
  return { it, sent, errors, info: () => info, pick: () => info?.roles?.chat?.pick };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const LIST = (pick) => ({ models: [A1, A2], roles: resolveRoles([A1, A2], { chat: pick }), favorites: [] });
const ROLES_OF = (pick) => ({ roles: LIST(pick).roles, favorites: [], chat_migrated: false });

test("a read sent before a write answers with the old roles: it is dropped, and read again once the write is in", async () => {
  const s = sync();
  s.it.switchTo("ann");
  s.it.refresh();
  s.sent[0].resolve(LIST(A1.id));
  await settle();
  s.it.refresh();
  s.it.write("/ai/models/roles", "PUT", { chat: A2.id }, (info) => withPick(info, "chat", A2.id));
  assert.equal(s.pick(), A2.id, "the pick shows at once");
  s.sent[1].resolve(LIST(A1.id));
  await settle();
  assert.equal(s.pick(), A2.id, "the read from before the write does not put the old pick back");
  s.sent[2].resolve(ROLES_OF(A2.id));
  await settle();
  assert.equal(s.sent.length, 4, "the dropped read goes again");
  assert.equal(s.sent[3].path, "/ai/models");
  s.sent[3].resolve(LIST(A2.id));
  await settle();
  assert.equal(s.pick(), A2.id);
});

test("a write answered after a later one is dropped; a refused one says why and reads again", async () => {
  const s = sync();
  s.it.switchTo("ann");
  s.it.refresh();
  s.sent[0].resolve(LIST(A1.id));
  await settle();
  s.it.write("/ai/models/roles", "PUT", { chat: A2.id });
  s.it.write("/ai/models/roles", "PUT", { chat: A1.id });
  s.sent[2].resolve(ROLES_OF(A1.id));
  await settle();
  s.sent[1].resolve(ROLES_OF(A2.id));
  await settle();
  assert.equal(s.pick(), A1.id, "the later write's answer stays");
  s.it.write("/ai/models/roles", "PUT", { chat: "gone:model" });
  s.sent[3].reject(new Error("gone:model is not offered"));
  await settle();
  assert.deepEqual(s.errors, ["gone:model is not offered"]);
  assert.equal(s.sent[4].path, "/ai/models", "the roles are read again");
});

test("another account aborts what is in flight, and nothing of the last one's lands", async () => {
  const s = sync();
  s.it.switchTo("ann");
  s.it.refresh();
  s.sent[0].resolve(LIST(A1.id));
  await settle();
  s.it.refresh();
  s.it.write("/ai/models/roles", "PUT", { chat: A2.id });
  s.it.switchTo("bob");
  assert.ok(s.sent[1].signal.aborted && s.sent[2].signal.aborted, "ann's read and write are aborted");
  assert.equal(s.info(), null, "ann's list is gone");
  await settle();
  assert.equal(s.info(), null);
  assert.deepEqual(s.errors, [], "an abort is no refusal");
  s.it.refresh();
  s.sent[3].resolve(LIST(A2.id));
  await settle();
  assert.equal(s.pick(), A2.id, "bob's own list lands");
});

test("an account's first read moves the old picks once, however many reads land meanwhile", async () => {
  const store = storage({ "gamma-meta-model": "home:qwen" });
  const s = sync({ store });
  s.it.switchTo("ann");
  s.it.refresh();
  s.sent[0].resolve(LIST(A1.id));
  await settle();
  assert.equal(s.sent[1].path, "/ai/models/import");
  assert.deepEqual(s.sent[1].body, { metadata: "home:qwen" });
  s.it.refresh();
  s.sent[2].resolve(LIST(A1.id));
  await settle();
  s.sent[1].resolve({ ...ROLES_OF(A1.id), taken: ["metadata"] });
  await settle();
  assert.deepEqual(s.sent.map((r) => r.path), ["/ai/models", "/ai/models/import", "/ai/models"], "one import");
  assert.equal(store.map.size, 0, "its keys go once it succeeded");
});
