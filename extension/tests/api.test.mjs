import assert from "node:assert/strict";
import { test } from "node:test";
import {
  api, checkedDefaultFolder, checkedWorkspace, defaultFolder, folderByPath, getSettings, rememberFolder, removeServer,
  setSettings, whoAmI, writableWorkspaces,
} from "../api.js";

function settings(t, server) {
  const stored = { server };
  const writes = [];
  const previousChrome = globalThis.chrome;
  globalThis.chrome = { storage: { sync: {
    get: async () => ({ ...stored }),
    set: async (patch) => { writes.push(patch); Object.assign(stored, patch); },
  } } };
  t.after(() => {
    if (previousChrome === undefined) delete globalThis.chrome;
    else globalThis.chrome = previousChrome;
  });
  return { stored, writes };
}

function response(url, data = { user: "alice" }) {
  const res = new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  Object.defineProperty(res, "url", { value: url });
  return res;
}

test("account check remembers HTTPS before the guarded publisher-session request", async (t) => {
  const { stored, writes } = settings(t, "http://gamma.example");
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url: url.href, init });
    if (url.pathname === "/api/session") return response("https://gamma.example/api/session");
    // Model Fetch rejecting the HTTP redirect for a pinned request.
    if (url.protocol === "http:" && init.redirect === "error") throw new TypeError("Failed to fetch");
    return response(url.href, { sessions: [], publisher_roots: ["aps.org"] });
  });

  const me = await whoAmI();
  assert.deepEqual(me, { user: "alice", origin: "https://gamma.example" });
  assert.equal(stored.server, me.origin);
  assert.deepEqual(writes, [{ server: me.origin, servers: ["http://gamma.example", me.origin] }]);
  const data = await api("/publisher-sessions", { expectedUser: me.user, expectedOrigin: me.origin });
  assert.deepEqual(data.sessions, []);
  assert.equal(calls[1].url, "https://gamma.example/api/publisher-sessions");
  assert.equal(calls[1].init.redirect, "error");
  assert.equal(calls[1].init.headers["X-Gamma-User"], "alice");
  assert.equal(calls[1].init.credentials, "include");
});

test("only an exact same-host HTTPS session upgrade is remembered", async (t) => {
  for (const [server, destination, expected] of [
    ["http://gamma.example:9001", "https://gamma.example:9001/api/session", "https://gamma.example:9001"],
    ["http://localhost:9001", "http://localhost:9001/api/session", "http://localhost:9001"],
    ["http://gamma.example", "https://other.example/api/session", "http://gamma.example"],
    ["http://gamma.example", "https://gamma.example:9001/api/session", "http://gamma.example"],
    ["http://gamma.example", "https://gamma.example/login", "http://gamma.example"],
    ["http://gamma.example", "https://gamma.example/api/session?redirect=1", "http://gamma.example"],
    ["https://gamma.example", "http://gamma.example/api/session", "https://gamma.example"],
  ]) {
    await t.test(`${server} -> ${destination}`, async (t) => {
      const { stored, writes } = settings(t, server);
      t.mock.method(globalThis, "fetch", async () => response(destination));
      assert.equal((await whoAmI()).origin, expected);
      assert.equal(stored.server, expected);
      assert.equal(writes.length, server === expected ? 0 : 1);
    });
  }
});

test("a signed-out session can discover HTTPS before login", async (t) => {
  const { stored } = settings(t, "http://gamma.example");
  t.mock.method(globalThis, "fetch", async () => response("https://gamma.example/api/session", { user: null }));
  assert.deepEqual(await whoAmI(), { user: null, origin: "https://gamma.example" });
  assert.equal(stored.server, "https://gamma.example");
});

test("an unrelated response does not rewrite the server", async (t) => {
  const { writes } = settings(t, "http://gamma.example");
  t.mock.method(globalThis, "fetch", async () => response("https://gamma.example/api/session", { detail: "not a session" }));
  assert.equal((await whoAmI()).origin, "http://gamma.example");
  assert.deepEqual(writes, []);
});

test("an in-flight session check does not overwrite a changed server", async (t) => {
  const { stored, writes } = settings(t, "http://gamma.example");
  t.mock.method(globalThis, "fetch", async () => {
    stored.server = "https://other.example";
    return response("https://gamma.example/api/session");
  });
  await assert.rejects(whoAmI(), /server changed/);
  assert.equal(stored.server, "https://other.example");
  assert.deepEqual(writes, []);
});

test("guarded requests still reject redirects without retrying or changing settings", async (t) => {
  const { writes } = settings(t, "https://gamma.example");
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    calls++;
    assert.equal(init.redirect, "error");
    throw new TypeError("Failed to fetch");
  });
  await assert.rejects(api("/publisher-sessions", {
    json: { host: "journals.aps.org", cookies: [] },
    expectedUser: "alice", expectedOrigin: "https://gamma.example",
  }), /Can't reach/);
  assert.equal(calls, 1);
  assert.deepEqual(writes, []);
});

test("switching servers remembers the existing address and deduplicates normalized origins", async (t) => {
  const { stored } = settings(t, "http://localhost:9001");
  stored.defaultFolders = { "http://localhost:9001": "papers-id" };
  assert.deepEqual((await getSettings()).servers, [stored.server]);
  await setSettings({ server: "https://gamma.example/library" });
  await setSettings({ server: "localhost:9001/" });
  assert.equal(stored.server, "http://localhost:9001");
  assert.deepEqual(stored.servers, ["http://localhost:9001", "https://gamma.example"]);
  assert.deepEqual(stored.defaultFolders, { "http://localhost:9001": "papers-id" });
  await setSettings({ allowOa: false });
  assert.deepEqual(stored.servers, ["http://localhost:9001", "https://gamma.example"]);
});

test("a request pinned to the previous server cannot send data after switching", async (t) => {
  settings(t, "http://localhost:9001");
  await setSettings({ server: "https://gamma.example" });
  const fetch = t.mock.method(globalThis, "fetch", async () => { throw new Error("must not fetch"); });
  await assert.rejects(api("/clip", {
    json: { doc_id: "old-server-document" }, expectedOrigin: "http://localhost:9001",
  }), /server changed/);
  assert.equal(fetch.mock.callCount(), 0);
});

test("forgetting a server persists without re-adding it or choosing a different destination", async (t) => {
  const { stored } = settings(t, "http://localhost:9001");
  await setSettings({ server: "https://gamma.example" });
  await removeServer("http://localhost:9001");
  assert.deepEqual((await getSettings()).servers, ["https://gamma.example"]);
  assert.equal(stored.server, "https://gamma.example");
  await setSettings({ server: "http://localhost:9001" });
  await removeServer("http://localhost:9001");
  assert.equal(stored.server, "");
  assert.deepEqual((await getSettings()).servers, ["https://gamma.example"]);
  await removeServer("https://gamma.example");
  assert.deepEqual((await getSettings()).servers, []);
});

test("the default folder is the connected server's id, else a stored old path sent as folder_path", async (t) => {
  const { stored } = settings(t, "https://gamma.example");
  assert.deepEqual(defaultFolder(await getSettings()), { folder: "", folder_path: "" });
  stored.folder = "Reading/2026";
  assert.deepEqual(defaultFolder(await getSettings()), { folder: "", folder_path: "Reading/2026" });
  stored.defaultFolders = { "https://gamma.example": "f1", "http://localhost:9001": "f2" };
  assert.deepEqual(defaultFolder(await getSettings()), { folder: "f1", folder_path: "" });
  stored.server = "https://other.example";
  assert.deepEqual(defaultFolder(await getSettings()), { folder: "", folder_path: "Reading/2026" });
});

test("a folder path names the folder whose names it joins, as the clip splits it", () => {
  const folders = [{ id: "f1", path: ["Reading"] }, { id: "f2", path: ["Reading", "2026"] }, { id: "f3", path: ["a/b"] }];
  assert.equal(folderByPath(folders, "Reading/2026"), "f2");
  assert.equal(folderByPath(folders, " Reading / 2026/ "), "f2");
  assert.equal(folderByPath(folders, "Reading"), "f1");
  assert.equal(folderByPath(folders, "Missing"), "");
  // Typed "a/b" names folder a's subfolder b, as the clip files it — not a
  // top-level folder whose own name is "a/b".
  assert.equal(folderByPath(folders, "a/b"), "");
  assert.equal(folderByPath([...folders, { id: "f4", path: ["a", "b"] }], "a/b"), "f4");
  assert.equal(folderByPath([...folders, { id: "f4", path: ["a", "b"] }], "a"), "");
  assert.equal(folderByPath(folders, "Reading/2026/more"), "");
  // A path differing only in case names the folder too, as the server files
  // it — but an exactly spelled folder wins over one that differs in case.
  assert.equal(folderByPath(folders, "reading/2026"), "f2");
  assert.equal(folderByPath([...folders, { id: "f5", path: ["reading"] }], "reading"), "f5");
  assert.equal(folderByPath([...folders, { id: "f5", path: ["reading"] }], "READING"), "f1");
});

test("a save's folder becomes the server's default, a path by the id it now has", async (t) => {
  const { stored, writes } = settings(t, "https://gamma.example");
  stored.folder = "Reading/2026";
  const fetch = t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url.href, "https://gamma.example/api/library/folders");
    assert.equal(init.redirect, "error");
    return response(url.href, { folders: [{ id: "f2", path: ["Reading", "2026"] }], labels: [] });
  });
  await rememberFolder(await getSettings(), { folder: "", folder_path: "Reading/2026" });
  assert.equal(fetch.mock.callCount(), 1);
  assert.deepEqual(stored.defaultFolders, { "https://gamma.example": "f2" });
  assert.equal(stored.folder, "");
  // The same folder again writes nothing; another one, or the root, replaces it.
  await rememberFolder(await getSettings(), { folder: "f2", folder_path: "" });
  assert.equal(writes.length, 1);
  await rememberFolder(await getSettings(), { folder: "", folder_path: "" });
  assert.deepEqual(stored.defaultFolders, { "https://gamma.example": "" });
  assert.equal(fetch.mock.callCount(), 1);
});

test("a save without the popup forgets a default folder the library no longer has", async (t) => {
  const { stored, writes } = settings(t, "https://gamma.example");
  stored.defaultFolders = { "https://gamma.example": "gone", "http://localhost:9001": "f2" };
  const fetch = t.mock.method(globalThis, "fetch", async (url) => {
    assert.equal(url.href, "https://gamma.example/api/library/folders");
    return response(url.href, { folders: [{ id: "f1", path: ["Reading"] }], labels: [] });
  });
  assert.deepEqual(await checkedDefaultFolder(await getSettings()), { folder: "", folder_path: "" });
  assert.deepEqual(stored.defaultFolders, { "https://gamma.example": "", "http://localhost:9001": "f2" }, "forgotten for this server only");
  assert.equal(writes.length, 1);
  stored.defaultFolders = { "https://gamma.example": "f1" };
  assert.deepEqual(await checkedDefaultFolder(await getSettings()), { folder: "f1", folder_path: "" });
  assert.equal(fetch.mock.callCount(), 2);
  stored.defaultFolders = {};
  stored.folder = "Reading/2026";
  assert.deepEqual(await checkedDefaultFolder(await getSettings()), { folder: "", folder_path: "Reading/2026" }, "a path is sent as it is");
  assert.equal(fetch.mock.callCount(), 2, "nothing to check");
});

test("the chosen workspace rides on every request, and a save pins the one it started in", async (t) => {
  const { stored } = settings(t, "https://gamma.example");
  const sent = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    sent.push(init.headers["X-Gamma-Workspace"]);
    return response(url.href, { folders: [], labels: [] });
  });
  await api("/library/folders");
  stored.workspaces = { "https://gamma.example": "ws-lab" };
  await api("/library/folders");
  stored.workspaces = { "https://other.example": "ws-lab" };
  await api("/library/folders");
  // A save names its own workspace, so its uploads and its clip stay
  // together even if the choice changes while it runs.
  stored.workspaces = { "https://gamma.example": "ws-lab" };
  await api("/clip", { json: {}, workspace: "ws-home" });
  await api("/clip", { json: {}, workspace: "" });
  assert.deepEqual(sent, [undefined, "ws-lab", undefined, "ws-home", undefined],
    "no header for the account's default, the chosen one otherwise, another server's never");
});

test("each workspace keeps its own default folder on the same server", async (t) => {
  const { stored } = settings(t, "https://gamma.example");
  stored.folder = "Reading/2026";  // stored before a workspace could be chosen
  assert.deepEqual(defaultFolder(await getSettings()), { folder: "", folder_path: "Reading/2026" });
  stored.workspaces = { "https://gamma.example": "ws-lab" };
  assert.deepEqual(defaultFolder(await getSettings()), { folder: "", folder_path: "" },
    "the pre-ids path names a folder of the default workspace");
  const fetch = t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(init.headers["X-Gamma-Workspace"], "ws-lab");
    return response(url.href, { folders: [{ id: "f-lab", path: ["Lab"] }], labels: [] });
  });
  await rememberFolder(await getSettings(), { folder: "", folder_path: "Lab" });
  assert.deepEqual(stored.defaultFolders, { "https://gamma.example#ws-lab": "f-lab" });
  assert.equal(stored.folder, "Reading/2026", "the default workspace's own default is untouched");
  stored.workspaces = {};
  assert.deepEqual(defaultFolder(await getSettings()), { folder: "", folder_path: "Reading/2026" });
  assert.equal(fetch.mock.callCount(), 1);
});

test("a workspace this account can no longer write to is forgotten", async (t) => {
  const { stored, writes } = settings(t, "https://gamma.example");
  stored.workspaces = { "https://gamma.example": "ws-lab", "https://other.example": "ws-lab" };
  const list = [
    { id: "ws-home", name: "Home", role: "owner", default: true },
    { id: "ws-lab", name: "Lab", role: "editor" },
    { id: "ws-room", name: "Reading room", role: "viewer" },
  ];
  assert.deepEqual(writableWorkspaces(list).map((w) => w.id), ["ws-home", "ws-lab"], "a viewer cannot be saved into");
  assert.equal(await checkedWorkspace(await getSettings(), list), "ws-lab");
  assert.deepEqual(writes, []);
  // Demoted to viewer: the account's default takes over, for this server only.
  assert.equal(await checkedWorkspace(await getSettings(), [list[0], { ...list[1], role: "viewer" }]), "");
  assert.deepEqual(stored.workspaces, { "https://gamma.example": "", "https://other.example": "ws-lab" });
  // Gone from the list (deleted, or left).
  stored.workspaces = { "https://gamma.example": "ws-lab" };
  assert.equal(await checkedWorkspace(await getSettings(), [list[0]]), "");
  // A server that lists no workspaces at all is left alone.
  stored.workspaces = { "https://gamma.example": "ws-lab" };
  assert.equal(await checkedWorkspace(await getSettings(), undefined), "ws-lab");
  assert.deepEqual(stored.workspaces, { "https://gamma.example": "ws-lab" });
});
