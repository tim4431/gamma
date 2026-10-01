import { getSettings, login, logout, normalizeServer, originPattern, removeServer, setSettings, whoAmI } from "./api.js";
import { renderServerList } from "./serverList.js";

const $ = (id) => document.getElementById(id);
let busy = false;

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
    if (me.user) $("who").textContent = me.user;
  } catch (err) {
    status("server-status", `Can't reach ${settings.server}: ${err.message}`, "err");
  }
  chrome.runtime.sendMessage({ type: "auth-changed" }).catch(() => {});
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

async function saveDefaults() {
  await setSettings({
    folder: $("folder").value.trim(),
    labels: $("labels").value.split(",").map((s) => s.trim()).filter(Boolean),
    allowOa: $("allow-oa").checked,
    saveCopy: $("save-copy").checked,
    autoRefreshSessions: $("auto-sessions").checked,
  });
  status("save-status", "Saved.", "ok");
  setTimeout(() => status("save-status", ""), 1500);
}

document.addEventListener("DOMContentLoaded", async () => {
  const s = await getSettings();
  $("server").value = s.server;
  $("folder").value = s.folder;
  $("labels").value = (s.labels || []).join(", ");
  $("allow-oa").checked = !!s.allowOa;
  $("save-copy").checked = !!s.saveCopy;
  $("auto-sessions").checked = s.autoRefreshSessions !== false;
  $("connect").onclick = () => connect();
  $("server").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
  $("login-form").addEventListener("submit", doLogin);
  $("login").onclick = doLogin;
  $("logout").onclick = doLogout;
  for (const id of ["folder", "labels", "allow-oa", "save-copy", "auto-sessions"]) $(id).addEventListener("change", saveDefaults);
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
