// Settings → Users: the GUI for /api/admin/users*. Two separate editors per
// account — credentials (rename/password/privilege) and storage limits —
// and, nested under each account row, its personal workspaces (from
// /api/admin/workspaces) with Open / Manage (the workspace dialog from
// settingsWorkspace.jsx in admin mode). Shared workspaces are not per
// account, so they stay in Settings → Server.
//
// Non-admins get this pane too, as "You": a single read-only row for
// themselves. /api/admin/* is admin-only, so their row is built from the
// session + /api/quota instead of the accounts listing, and there is no
// editor. Backups of anyone's workspaces live in Settings → Backups (and,
// for admins, Settings → Server), not here.
import React from "react";
import { API, apiJson, fmtBytes } from "../shared/lib/utils";
import { PaneHead, SubDialog, Field, UnitInput, Empty, QuotaMeter, PasswordInput } from "./SettingsKit";
import { CloudIdentityRow } from "./SettingsCloudSignIn";
import { ManageWorkspaceDialog, useAccounts } from "./SettingsWorkspace";
import { BookIcon, HardDriveIcon, PenIcon, PlusIcon, ShieldIcon, Trash2Icon, UserIcon, UsersIcon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

export function UsersSettings({ value, selfOnly = false }) {
  const { setStatus, confirm, onSelfRenamed, refreshQuota, isAdmin, me, isGuest, quotaInfo,
    workspaces: mine, switchWorkspace, refreshSession, closeSettings } = value;
  const [info, setInfo] = React.useState(null); // {users, me}
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [edit, setEdit] = React.useState(null); // {original, username, password, is_admin, max_upload_mb, quota_mb}
  const [addForm, setAddForm] = React.useState(null); // {username, password, is_admin}
  const [wsRows, setWsRows] = React.useState(null); // /api/admin/workspaces, personal ones only
  const [manage, setManage] = React.useState(null); // workspace id being managed
  const listWorkspaces = isAdmin && !selfOnly;

  const [defaults, setDefaults] = React.useState(null); // {max_upload_mb, quota_mb} server-wide
  React.useEffect(() => {
    if (!isAdmin) {
      refreshQuota?.(); // the self row's storage meter comes from /api/quota
      return;
    }
    apiJson(`${API}/admin/users`).then((d) => setInfo(d)).catch((err) => setError(err.message));
    apiJson(`${API}/admin/settings`).then(setDefaults).catch(() => {});
  }, [isAdmin]);
  const refreshWorkspaces = React.useCallback(() => {
    if (!listWorkspaces) return;
    apiJson(`${API}/admin/workspaces`)
      .then((d) => setWsRows((d.workspaces || []).filter((w) => w.personal)))
      .catch(() => setWsRows([]));
  }, [listWorkspaces]);
  React.useEffect(() => { refreshWorkspaces(); }, [refreshWorkspaces]);
  const openable = new Set((mine || []).map((w) => w.id));
  // An account's personal workspaces, its default first. The admin listing
  // keys them by username, so account changes re-fetch (usersCall below).
  const personalOf = (username) => (wsRows || [])
    .filter((w) => w.personal === username)
    .sort((a, b) => (b.default - a.default) || a.name.localeCompare(b.name));

  const myName = isAdmin ? info?.me : me;
  // /api/quota reports effective limits (overrides already resolved), which is
  // exactly what the self row shows — no "blank = inherit" distinction to make.
  const rows = isAdmin ? (info?.users || []) : [{
    username: me,
    is_guest: isGuest,
    is_admin: false,
    created_at: null,
    used_bytes: quotaInfo?.used_bytes,
    max_upload_mb: quotaInfo?.max_upload_mb ?? null,
    quota_mb: quotaInfo?.quota_mb ?? null,
  }];
  const lastAdmin = (u) => u.is_admin && (info?.users || []).filter((x) => x.is_admin && !x.is_guest).length <= 1;

  // Mutation responses carry the fresh users list but omit used_bytes (the
  // server only stats every account's disk on the GET listing) — carry the
  // last known usage forward. Returns the response, or null after setError.
  async function usersCall(path, method, body) {
    setBusy(true);
    setError("");
    try {
      const d = await apiJson(`${API}/admin/users${path}`, {
        method,
        ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
      });
      setInfo((prev) => {
        const usage = new Map((prev?.users || []).map((u) => [u.username, u.used_bytes]));
        if (d.renamed) usage.set(d.renamed.to, usage.get(d.renamed.from));
        return {
          ...prev,
          users: d.users.map((u) => (u.used_bytes == null ? { ...u, used_bytes: usage.get(u.username) ?? 0 } : u)),
          me: d.renamed?.from === prev?.me ? d.renamed.to : prev?.me,
        };
      });
      refreshWorkspaces(); // created / renamed / deleted accounts change the nested rows
      return d;
    } catch (err) {
      setError(err.message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  // Two separate editors per account: credentials (rename/password/privilege)
  // and storage limits — each its own button and dialog.
  // edit = {kind: "account", original, username, password, is_admin}
  //      | {kind: "storage", original, max_upload_mb, quota_mb}
  function openAccount(u) {
    setError("");
    setAddForm(null);
    setEdit({ kind: "account", original: u, username: u.username, password: "", is_admin: !!u.is_admin });
  }

  function openStorage(u) {
    setError("");
    setAddForm(null);
    setEdit({ kind: "storage", original: u, max_upload_mb: u.max_upload_mb ?? "", quota_mb: u.quota_mb ?? "" });
  }

  // "" = inherit the server default (sent as explicit null), digits = override
  function parseLimit(s) {
    const t = String(s ?? "").trim();
    if (!t) return null;
    const n = parseInt(t, 10);
    return Number.isFinite(n) ? n : NaN;
  }

  async function saveAccount() {
    const u = edit.original;
    const payload = {};
    if (edit.password) payload.password = edit.password;
    if (edit.is_admin !== !!u.is_admin) payload.is_admin = edit.is_admin;
    const newName = (edit.username || "").trim();
    const renaming = newName && newName !== u.username;
    if (!Object.keys(payload).length && !renaming) { setEdit(null); return; }
    if (Object.keys(payload).length) {
      if (!await usersCall(`/${encodeURIComponent(u.username)}`, "PUT", payload)) return;
    }
    if (renaming) {
      const d = await usersCall(`/${encodeURIComponent(u.username)}/rename`, "POST", { new_username: newName });
      if (!d) return;
      if (d.renamed) setStatus(t("Renamed {from} → {to}. Sessions keep working.", { from: d.renamed.from, to: d.renamed.to }));
      // Renamed yourself? Re-read the session so the whole app re-keys
      // (avatar, per-user prefs, synced tabs all follow the new name).
      if (d.renamed?.from === myName) onSelfRenamed?.();
    } else {
      setStatus(t("Updated {username}.", { username: u.username }));
    }
    setEdit(null);
  }

  async function saveStorage() {
    const u = edit.original;
    const maxMb = parseLimit(edit.max_upload_mb);
    const quotaMb = parseLimit(edit.quota_mb);
    if (Number.isNaN(maxMb) || Number.isNaN(quotaMb)) {
      setError(t("Storage limits must be whole numbers of MB, or blank for the server default."));
      return;
    }
    const payload = {};
    if (maxMb !== (u.max_upload_mb ?? null)) payload.max_upload_mb = maxMb;
    if (quotaMb !== (u.quota_mb ?? null)) payload.quota_mb = quotaMb;
    if (Object.keys(payload).length) {
      if (!await usersCall(`/${encodeURIComponent(u.username)}`, "PUT", payload)) return;
      setStatus(t("Storage limits updated for {username}.", { username: u.username }));
      if (u.username === myName) refreshQuota?.();
    }
    setEdit(null);
  }

  function deleteAccount(u) {
    confirm({
      title: T("Delete user"),
      message: t("Delete \"{username}\" and ALL their data (notes, PDFs, settings)? This can't be undone.", { username: u.username }),
      confirmLabel: t("Delete"),
      danger: true,
      onConfirm: async () => {
        const d = await usersCall(`/${encodeURIComponent(u.username)}`, "DELETE");
        if (!d) return;
        setStatus(d.warning || `Deleted ${u.username}.`);
        setEdit(null);
      },
    });
  }

  async function submitAdd() {
    const f = addForm;
    if (!f?.username.trim() || !f?.password) { setError(t("Username and password are required.")); return; }
    const d = await usersCall("", "POST",
      { username: f.username.trim(), password: f.password, is_admin: !!f.is_admin });
    if (!d) return;
    setStatus(t("Created {username}.", { username: f.username.trim() }));
    setAddForm(null);
  }

  const closeEdit = () => { setEdit(null); setError(""); };

  function accountDialog() {
    const u = edit.original;
    return (
      <SubDialog title={t("Edit {username}", { username: u.username })} onClose={closeEdit} draft={edit}>
        <div className="settingsForm">
          <Field label={t("Username")} hint={t("renaming keeps sessions and share links working")}>
            <input
              className="aiKeyInput" type="text" spellCheck={false}
              value={edit.username}
              onChange={(e) => setEdit((f) => ({ ...f, username: e.target.value }))}
            />
          </Field>
          <Field label={t("New password")} hint={t("blank keeps the current one")}>
            <PasswordInput
              autoComplete="new-password"
              value={edit.password}
              onChange={(e) => setEdit((f) => ({ ...f, password: e.target.value }))}
            />
          </Field>
          <label className="uiCheckRow" title={lastAdmin(u) ? t("The last admin can't be demoted") : ""}>
            <input
              type="checkbox" checked={edit.is_admin} disabled={lastAdmin(u)}
              onChange={(e) => setEdit((f) => ({ ...f, is_admin: e.target.checked }))}
            />
            <ShieldIcon size={13} /> {t("Admin privilege")}
          </label>
          {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
          <div className="reportModalBtns">
            {u.username !== myName ? (
              <button className="uiBtn danger" disabled={busy} onClick={() => deleteAccount(u)}>
                <Trash2Icon size={13} /> {t("Delete…")}
              </button>
            ) : null}
            <button className="uiBtn" onClick={closeEdit}>{t("Cancel")}</button>
            <button className="uiBtn primary" disabled={busy} onClick={saveAccount}>{t("Save")}</button>
          </div>
        </div>
      </SubDialog>
    );
  }

  function storageDialog() {
    const u = edit.original;
    // effective quota = this account's override, else the server default
    const effQuota = u.quota_mb ?? defaults?.quota_mb;
    const defUpload = defaults ? t("server default ({max_upload_mb})", { max_upload_mb: defaults.max_upload_mb }) : t("server default");
    const defQuota = defaults
      ? t("server default ({unlimited})", { unlimited: defaults.quota_mb || t("unlimited") })
      : t("server default");
    return (
      <SubDialog title={t("Storage limits — {username}", { username: u.username })} onClose={closeEdit} draft={edit}>
        <div className="settingsForm">
          <QuotaMeter usedBytes={u.used_bytes} quotaMb={effQuota} />
          <Field label={t("Max upload size")} hint={t("largest single PDF or image · blank inherits")}>
            <UnitInput
              unit="MB" min={1} placeholder={defUpload}
              value={edit.max_upload_mb}
              onChange={(max_upload_mb) => setEdit((f) => ({ ...f, max_upload_mb }))}
            />
          </Field>
          <Field label={t("Storage quota")} hint={t("total for all uploads · blank inherits · 0 = unlimited")}>
            <UnitInput
              unit="MB" min={0} placeholder={defQuota}
              value={edit.quota_mb}
              onChange={(quota_mb) => setEdit((f) => ({ ...f, quota_mb }))}
            />
          </Field>
          {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
          <div className="reportModalBtns">
            <button className="uiBtn" onClick={closeEdit}>{t("Cancel")}</button>
            <button className="uiBtn primary" disabled={busy} onClick={saveStorage}>{t("Save")}</button>
          </div>
        </div>
      </SubDialog>
    );
  }

  function userRow(u) {
    const wsList = listWorkspaces ? personalOf(u.username) : [];
    return (
      <div key={u.username} className="aiProvRow">
        <span className={`aiProvAvatar ${u.is_admin ? "active" : ""}`}>
          {u.is_admin ? <ShieldIcon size={15} /> : <UserIcon size={15} />}
        </span>
        <span className="aiProvMeta">
          <span className="aiProvName">
            {u.username}
            {u.username === myName ? <span className="uiTag">{t("you")}</span> : null}
            {u.is_admin ? <span className="uiTag admin">{t("admin")}</span> : null}
            {u.is_guest ? <span className="uiTag">{t("guest")}</span> : null}
          </span>
          {u.is_guest || u.max_upload_mb != null ? (
            <span className="aiProvDesc">
              {[u.is_guest ? t("shared demo workspace, resets daily") : "",
                u.max_upload_mb != null ? `max file ${u.max_upload_mb} MB` : ""].filter(Boolean).join(" · ")}
            </span>
          ) : null}
          <QuotaMeter usedBytes={u.used_bytes} quotaMb={u.quota_mb ?? defaults?.quota_mb} />
        </span>
        <span className="aiProvActions">
          {isAdmin ? (
            <>
              <button className="uiBtn sm" disabled={busy} title={t("Storage limits for {username}", { username: u.username })} onClick={() => openStorage(u)}>
                <HardDriveIcon size={13} /> {t("Storage")}
              </button>
              {!u.is_guest ? (
                <button className="uiBtn sm" disabled={busy} title={t("Rename {username}, set a password, or grant admin", { username: u.username })} onClick={() => openAccount(u)}>
                  <PenIcon size={13} /> {t("Edit")}
                </button>
              ) : null}
            </>
          ) : null}
        </span>
        {wsList.length ? (
          <div className="aiProvSub">
            {wsList.map((w) => (
              <div key={w.id} className="aiProvSubRow">
                <span className="aiProvSubIcon"><BookIcon size={13} /></span>
                <span className="aiProvSubMeta">
                  <span className="aiProvSubName">
                    {w.name}
                    {w.default ? <span className="uiTag">{t("default")}</span> : null}
                  </span>
                  <span className="aiProvDesc">{t("personal workspace · {size}", { size: fmtBytes(w.used_bytes) })}</span>
                </span>
                <span className="aiProvActions">
                  {openable.has(w.id) ? (
                    <button className="uiBtn sm" onClick={() => { closeSettings?.(); switchWorkspace?.(w.id); }}>{t("Open")}</button>
                  ) : null}
                  <button className="uiBtn sm" onClick={() => setManage(w.id)} title={t("Manage {name}", { name: w.name })}>
                    <PenIcon size={13} /> {t("Manage")}
                  </button>
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <>
      {isAdmin && !selfOnly ? <PaneHead icon={UsersIcon} title={t("Users")} /> : <PaneHead icon={UserIcon} title={t("Account & sync")} />}
      {isAdmin && !info && !error ? <Empty icon={UsersIcon}>{t("Loading…")}</Empty> : null}
      {(selfOnly ? rows.filter((row) => row.username === me) : rows).map(userRow)}
      {!isGuest && (selfOnly || !isAdmin) ? <CloudIdentityRow setStatus={setStatus} confirm={confirm} /> : null}
      {edit?.kind === "account" ? accountDialog() : null}
      {edit?.kind === "storage" ? storageDialog() : null}
      {manage ? (
        <PersonalWorkspaceDialog
          wsId={manage} me={myName} admin confirm={confirm} setStatus={setStatus}
          canOpen={openable.has(manage)}
          onOpen={() => { closeSettings?.(); switchWorkspace?.(manage); }}
          onClose={() => { setManage(null); refreshWorkspaces(); refreshSession?.(); }}
        />
      ) : null}
      {!isAdmin || selfOnly ? null : addForm ? (
        <SubDialog title={t("Add user")} draft={addForm} onClose={() => { setAddForm(null); setError(""); }}>
          <div className="settingsForm">
          <Field label={t("Username")} hint={t("letters, digits, _ . -")}>
            <input
              className="aiKeyInput" type="text" spellCheck={false} autoFocus
              value={addForm.username}
              onChange={(e) => setAddForm((f) => ({ ...f, username: e.target.value }))}
            />
          </Field>
          <Field label={t("Password")}>
            <PasswordInput
              autoComplete="new-password"
              value={addForm.password}
              onChange={(e) => setAddForm((f) => ({ ...f, password: e.target.value }))}
              onKeyDown={(e) => { if (e.key === "Enter") submitAdd(); }}
            />
          </Field>
          <label className="uiCheckRow">
            <input
              type="checkbox" checked={!!addForm.is_admin}
              onChange={(e) => setAddForm((f) => ({ ...f, is_admin: e.target.checked }))}
            />
            <ShieldIcon size={13} /> {t("Grant the admin privilege")}
          </label>
          {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
          <div className="reportModalBtns">
            <button className="uiBtn" onClick={() => { setAddForm(null); setError(""); }}>{t("Cancel")}</button>
            <button className="uiBtn primary" disabled={busy} onClick={submitAdd}>
              {busy ? t("Creating…") : t("Create user")}
            </button>
          </div>
          </div>
        </SubDialog>
      ) : info ? (
        <div className="reportModalBtns settingsAlignStart">
          <button className="uiBtn" onClick={() => { setError(""); setEdit(null); setAddForm({ username: "", password: "", is_admin: false }); }}>
            {t("+ Add user")}
          </button>
        </div>
      ) : null}
      {error && !edit && !addForm ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
    </>
  );
}

// The workspace dialog with the account directory it needs for ownership
// changes — fetched only while a dialog is open (the directory is admin
// territory; the self-only Account pane never asks for it).
function PersonalWorkspaceDialog(props) {
  const accounts = useAccounts();
  return <ManageWorkspaceDialog {...props} accounts={accounts} />;
}
