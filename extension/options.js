import {
  api, checkedWorkspace, chooseWorkspace, defaultFolder, folderByPath, getSettings, login, logout, normalizeServer,
  originPattern, rememberFolder, removeServer, setSettings, whoAmI, writableWorkspaces,
} from "./api.js";
import { renderServerList } from "./serverList.js";
import { folderPicker, workspacePicker } from "./ui.js";

const $ = (id) => document.getElementById(id);
let busy = false;
let workspaceSelect = null;  // the workspace picker (ui.js), filled from the signed-in account
let folderSelect = null;  // the default folder's picker (ui.js), filled from the signed-in library

function setBusy(value) {
  busy = value;
  for (const id of ["server", "connect", "login", "logout"]) $(id).disabled = value;
  for (const button of $("saved-servers").querySelectorAll("button")) button.disabled = value;
}

function renderServers(settings) {
  renderServerList($("saved-servers"), settings, { select: connect, remove: forgetServer, busy });
  $("saved-servers").classList.toggle("hidden", !settings.servers.length);
}

async function forgetServer(origin) {
  if (busy) return;
  setBusy(true);
  try {
    await removeServer(origin);
    $("server").value = (await getSettings()).server;
    $("user").value = "";
    $("pass").value = "";
    await refreshAccount();
  } catch (err) { status("server-status", err.message, "err"); }
  finally { setBusy(false); }
  ($("saved-servers").querySelector(".serverPick") || $("server")).focus();
}

function status(id, text, cls = "") {
  const el = $(id);
  el.textContent = text;
  el.className = "status " + cls;
}

async function refreshAccount() {
  const settings = await getSettings();
  renderServers(settings);
  $("signed-out").classList.remove("hidden");
  $("signed-in").classList.add("hidden");
  $("who").textContent = "";
  status("account-status", "");
  folderSelect.set([], "");
  $("folder-btn").disabled = true;
  $("workspace-btn").disabled = true;
  $("workspace-row").classList.add("hidden");
  if (!settings.server) {
    status("server-status", "Not connected.");
    return;
  }
  try {
    const me = await whoAmI();
    $("server").value = me.origin;
    renderServers(await getSettings());
    status("server-status", `Connected to ${me.origin}.`, "ok");
    $("signed-out").classList.toggle("hidden", !!me.user);
    $("signed-in").classList.toggle("hidden", !me.user);
    if (me.user) {
      $("who").textContent = me.user;
      await fillWorkspaces(me.workspaces);
      await fillFolders();
    }
  } catch (err) {
    status("server-status", `Can't reach ${settings.server}: ${err.message}`, "err");
  }
  chrome.runtime.sendMessage({ type: "auth-changed" }).catch(() => {});
}

// The libraries this account can save into, the chosen one among them. One
// library is no choice, so the row stays hidden; a choice the account lost
// is forgotten here and the default workspace takes over.
async function fillWorkspaces(list) {
  const workspaces = writableWorkspaces(list);
  workspaceSelect.set(workspaces, await checkedWorkspace(await getSettings(), list));
  $("workspace-btn").disabled = false;
  $("workspace-row").classList.toggle("hidden", workspaces.length <= 1);
}

// The connected server's folders, the default among them; a path stored
// before folders had ids shows as the folder of that path.
async function fillFolders() {
  const settings = await getSettings();
  let folders = [];
  try { ({ folders } = await api("/library/folders")); } catch {}
  const { folder, folder_path } = defaultFolder(settings);
  folderSelect.set(folders, folder_path ? folderByPath(folders, folder_path) : folder);
  $("folder-btn").disabled = false;
}

async function connect(raw = $("server").value) {
  if (busy) return;
  const origin = normalizeServer(raw);
  if (!origin) { status("server-status", "Enter a valid URL.", "err"); return; }
  setBusy(true);
  try {
    const granted = await chrome.permissions.request({ origins: [originPattern(origin)] });
    if (!granted) {
      renderServers(await getSettings());
      status("server-status", "Permission to talk to that server was declined.", "err");
      return;
    }
    await setSettings({ server: origin });
    $("server").value = origin;
    $("user").value = "";
    $("pass").value = "";
    status("server-status", `Connecting to ${origin}...`);
    await refreshAccount();
  } catch (err) {
    renderServers(await getSettings());
    status("server-status", err.message, "err");
  } finally { setBusy(false); }
}

async function doLogin(e) {
  if (e) e.preventDefault();
  if (busy) return;
  setBusy(true);
  try {
    await login($("user").value.trim(), $("pass").value);
    $("pass").value = "";
    await refreshAccount();
  } catch (err) {
    status("account-status", err.status === 401 ? "Wrong username or password." : err.message, "err");
  } finally { setBusy(false); }
}

async function doLogout() {
  if (busy) return;
  setBusy(true);
  try {
    await logout();
    await refreshAccount();
  } catch (err) { status("account-status", err.message, "err"); }
  finally { setBusy(false); }
}

function saved() {
  status("save-status", "Saved.", "ok");
  setTimeout(() => status("save-status", ""), 1500);
}

async function saveDefaults() {
  await setSettings({
    labels: $("labels").value.split(",").map((s) => s.trim()).filter(Boolean),
    allowOa: $("allow-oa").checked,
    saveCopy: $("save-copy").checked,
    autoRefreshSessions: $("auto-sessions").checked,
  });
  saved();
}

// Each library has its own folders, so the picker below is refilled — and
// with it the default folder this server remembers for this workspace.
async function saveWorkspace(workspace) {
  if (!await chooseWorkspace(await getSettings(), workspace)) return;
  await fillFolders();
  saved();
}

async function saveFolder(folder) {
  await rememberFolder(await getSettings(), { folder, folder_path: "" });
  saved();
}

document.addEventListener("DOMContentLoaded", async () => {
  const s = await getSettings();
  $("server").value = s.server;
  workspaceSelect = workspacePicker($("workspace-btn"), $("workspace-menu"), saveWorkspace);
  folderSelect = folderPicker($("folder-btn"), $("folder-menu"), saveFolder);
  $("labels").value = (s.labels || []).join(", ");
  $("allow-oa").checked = !!s.allowOa;
  $("save-copy").checked = !!s.saveCopy;
  $("auto-sessions").checked = s.autoRefreshSessions !== false;
  $("connect").onclick = () => connect();
  $("server").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
  $("login-form").addEventListener("submit", doLogin);
  $("login").onclick = doLogin;
  $("logout").onclick = doLogout;
  for (const id of ["labels", "allow-oa", "save-copy", "auto-sessions"]) $(id).addEventListener("change", saveDefaults);
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "sync" || !(changes.server || changes.servers) || busy) return;
    setBusy(true);
    getSettings().then(async (settings) => {
      $("server").value = settings.server;
      $("user").value = "";
      $("pass").value = "";
      await refreshAccount();
    }).finally(() => setBusy(false));
  });
  setBusy(true);
  try { await refreshAccount(); } finally { setBusy(false); }
});
