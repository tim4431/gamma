// Shared by the worker, popup and options page: settings in chrome.storage.sync
// and a fetch wrapper for the user's Gamma server. Requests carry the browser's
// cookies (credentials: "include") — the same session cookie the app tab uses,
// so signing in from either place signs in both. Chrome exempts requests from
// an extension holding a host permission for the target from SameSite rules,
// which is why the options page asks for that permission when the URL is set.

export const DEFAULTS = {
  server: "",          // e.g. "http://gamma.local:9001"
  servers: [],         // remembered origins for the options-page switcher
  workspaces: {},      // the workspace saves go to, per server: {origin: workspace id}
  defaultFolders: {},  // default folder for saves, per server and workspace (folderKey)
  folder: "",          // the default as a path, stored before folders had ids (defaultFolder)
  labels: [],          // default labels
  allowOa: true,       // open-access fallback behind paywalls
  saveCopy: true,      // store the PDF server-side
  autoRefreshSessions: true,  // re-import a connected publisher's cookies when its page is visited
};

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  const servers = [...new Set([...(stored.servers || []), stored.server]
    .map(normalizeServer).filter(Boolean))];
  return { ...DEFAULTS, ...stored, servers };
}

export async function setSettings(patch) {
  if (Object.hasOwn(patch, "server")) {
    const current = await getSettings();
    const server = normalizeServer(patch.server);
    patch = { ...patch, server, servers: [...new Set([...current.servers, server].filter(Boolean))] };
  }
  await chrome.storage.sync.set(patch);
}

// Forgetting an active address disconnects, without logging out or selecting
// another destination for saves. Write both keys together to avoid re-adding it.
export async function removeServer(origin) {
  const settings = await getSettings();
  await chrome.storage.sync.set({
    server: settings.server === origin ? "" : settings.server,
    servers: settings.servers.filter((server) => server !== origin),
  });
}

// ---------- the workspace saves go to ----------
//
// A Gamma account can open several libraries — workspaces. A request naming
// none lands in the account's default one, which is what every Connector
// before this did and what "" still means here; naming one sends it as
// X-Gamma-Workspace, the header the app's own fetch wrapper uses. The choice
// belongs to the server it was made on.

export function currentWorkspace(settings) {
  return settings.workspaces[settings.server] || "";
}

// The workspaces of GET /api/session a save could land in — a viewer's
// would refuse it.
export function writableWorkspaces(list) {
  return (list || []).filter((w) => w.role === "editor" || w.role === "owner");
}

// Forget a chosen workspace this account can no longer write to (deleted,
// left, or demoted to viewer) so saves fall back to the account's default
// instead of failing. A server that lists no workspaces changes nothing.
export async function checkedWorkspace(settings, list) {
  const workspace = currentWorkspace(settings);
  if (!workspace || !Array.isArray(list) || writableWorkspaces(list).some((w) => w.id === workspace)) return workspace;
  await setSettings({ workspaces: { ...settings.workspaces, [settings.server]: "" } });
  return "";
}

// Folders belong to one workspace, so the remembered default folder is keyed
// by server and workspace. The bare origin stays the key of the account's
// default workspace — where a default stored before this choice existed is.
function folderKey(settings) {
  const workspace = currentWorkspace(settings);
  return workspace ? `${settings.server}#${workspace}` : settings.server;
}

// The connected server's default folder, as POST /api/clip names it: `folder`
// its id ("" = the library root), or — while the setting is still the path an
// older version stored — that path as `folder_path`, which the next save sends
// (made where missing) and rememberFolder replaces by the folder's id.
export function defaultFolder(settings) {
  const folder = settings.defaultFolders[folderKey(settings)] || "";
  // The pre-ids path named a folder of the account's default workspace.
  const stored = currentWorkspace(settings) ? "" : settings.folder;
  return { folder, folder_path: folder ? "" : stored };
}

// The default folder for a save made without the popup (the shortcut, the
// context menu), checked against the library: an id the server no longer
// lists (the folder was deleted) is forgotten, and the save goes to the
// library root. A stored old path is sent as it is (made where missing).
export async function checkedDefaultFolder(settings) {
  const filing = defaultFolder(settings);
  if (!filing.folder) return filing;
  const { folders = [] } = await api("/library/folders",
    { expectedOrigin: settings.server, workspace: currentWorkspace(settings) });
  if (folders.some((f) => f.id === filing.folder)) return filing;
  await setSettings({ defaultFolders: { ...settings.defaultFolders, [folderKey(settings)]: "" } });
  return { folder: "", folder_path: "" };
}

// The id of the folder a path names ("a/b", split as POST /api/clip splits
// it: blank names left out, the rest trimmed) among GET /api/library/folders'
// `folders` ({id, path}), or "". Compared name by name, so a folder itself
// named "a/b" is not what typed "a/b" (folder a, subfolder b) files into.
// An exact match first, else one differing only in case — as the server
// (blocks_store.named) and the app (libraryUtils.findNamed) match names.
export function folderByPath(folders, path) {
  const names = path.split("/").map((name) => name.trim()).filter(Boolean);
  const matches = (same) => (f) => f.path.length === names.length
    && f.path.every((name, i) => same(name.trim(), names[i]));
  const found = folders.find(matches((a, b) => a === b))
    || folders.find(matches((a, b) => a.toLowerCase() === b.toLowerCase()));
  return found ? found.id : "";
}

// Makes a save's folder the connected server's default; one named by path (a
// typed new folder, or the stored old path) by the id the save filed it under.
export async function rememberFolder(settings, { folder, folder_path }) {
  if (folder_path) {
    const { folders = [] } = await api("/library/folders",
      { expectedOrigin: settings.server, workspace: currentWorkspace(settings) });
    folder = folderByPath(folders, folder_path);
  }
  const stored = defaultFolder(settings);
  if (folder === stored.folder && !stored.folder_path) return;
  const patch = { defaultFolders: { ...settings.defaultFolders, [folderKey(settings)]: folder } };
  if (stored.folder_path) patch.folder = "";  // the pre-ids path, replaced by this id
  await setSettings(patch);
}

// "gamma.local:9001" → "http://gamma.local:9001"; keeps an explicit scheme.
export function normalizeServer(raw) {
  let s = (raw || "").trim().replace(/\/+$/, "");
  if (!s) return "";
  if (!/^https?:\/\//i.test(s)) s = "http://" + s;
  try {
    return new URL(s).origin;
  } catch {
    return "";
  }
}

export async function serverOrigin() {
  const { server } = await getSettings();
  return normalizeServer(server);
}

// The permission pattern for an origin ("http://host:9001/*").
export function originPattern(origin) {
  return origin ? origin + "/*" : "";
}

export async function hasServerPermission(origin) {
  if (!origin) return false;
  return chrome.permissions.contains({ origins: [originPattern(origin)] });
}

async function readError(res) {
  let message = `${res.status} ${res.statusText}`;
  let detail = false;
  try {
    const data = await res.json();
    if (data && data.detail) {
      detail = true;
      message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    }
  } catch {
    try {
      const text = await res.text();
      if (text) message = text.slice(0, 200);
    } catch {}
  }
  // A 413 without Gamma's {detail} is a proxy's (Cloudflare's HTML page):
  // its request-body limit refused the bytes, not Gamma's max_upload_mb.
  if (res.status === 413 && !detail) message = "refused as too large by a proxy in front of the server, not by Gamma (HTTP 413)";
  return new ApiError(res.status, message);
}

async function request(path, { method, json, form, params, expectedUser, expectedOrigin, workspace } = {}) {
  const settings = await getSettings();
  const origin = normalizeServer(settings.server);
  if (expectedOrigin && origin !== expectedOrigin) throw new ApiError(409, "Gamma server changed. Reopen the Connector.");
  if (!origin) throw new ApiError(0, "No Gamma server configured — open the extension options.");
  const url = new URL(origin + "/api" + path);
  for (const [k, v] of Object.entries(params || {})) if (v) url.searchParams.set(k, v);
  const init = { method: method || (json || form ? "POST" : "GET"), credentials: "include", headers: {} };
  if (expectedUser != null) init.headers["X-Gamma-User"] = expectedUser;
  // Every request carries the chosen workspace: the library endpoints and
  // the uploads a save needs all resolve against it, and the account-level
  // ones (the session, publisher sessions, chat handoffs) ignore it. A save
  // passes `workspace` itself, so its uploads and its clip stay together
  // even if the choice changes while it runs.
  const chosen = workspace !== undefined ? workspace : currentWorkspace(settings);
  if (chosen) init.headers["X-Gamma-Workspace"] = chosen;
  // A session snapshot must never be forwarded to a redirected server.
  if (expectedOrigin) init.redirect = "error";
  if (json) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  } else if (form) {
    init.body = form;
  }
  let res;
  try {
    res = await fetch(url, init);
  } catch (err) {
    throw new ApiError(0, `Can't reach ${origin} (${err.message})`);
  }
  if (!res.ok) throw await readError(res);
  return { res, origin };
}

function readResponse(res) {
  const ctype = res.headers.get("content-type") || "";
  return ctype.includes("json") ? res.json() : res.text();
}

// api("/session"), api("/clip", {json: {...}}), api("/uploads", {form})
export async function api(path, options) {
  const { res } = await request(path, options);
  return readResponse(res);
}

// Session identity (user is null when signed out) plus the effective origin.
export async function whoAmI() {
  const { res, origin } = await request("/session");
  const data = await readResponse(res);
  let resolvedOrigin = origin;
  // Remember a same-host HTTPS upgrade discovered by this read-only check.
  // Publisher requests still reject redirects, especially cookie uploads.
  const upgraded = new URL(origin + "/api/session");
  if (upgraded.protocol === "http:") {
    upgraded.protocol = "https:";
    if (res.url === upgraded.href && data && typeof data === "object" && "user" in data) {
      resolvedOrigin = upgraded.origin;
    }
  }
  const currentOrigin = await serverOrigin();
  if (currentOrigin !== origin && currentOrigin !== resolvedOrigin) {
    throw new ApiError(409, "Gamma server changed. Reopen the Connector.");
  }
  if (resolvedOrigin !== currentOrigin) await setSettings({ server: resolvedOrigin });
  return { ...(data && data.user ? data : { user: null }), origin: resolvedOrigin };
}

export async function login(username, password) {
  return api("/login", { json: { username, password } });
}

export async function logout() {
  return api("/logout", { method: "POST" });
}
