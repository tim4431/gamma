// Settings → Workspaces: every library you can open, in one place. Personal
// workspaces (just you; several per account, all under your quota) and the
// shared ones you belong to (admin-made), each row with Open, Export,
// Import and Manage — a dialog with rename, storage, members and roles
// (shared), Make default (personal), Leave, Delete. Plus your account's
// storage meter, New workspace (a personal one) and Export all. GUI for
// /api/workspaces* (docs/dev/workspaces.md).
//
// The dialog (ManageWorkspaceDialog) is shared with the admin's Server pane
// (settingsWorkspacesAdmin.jsx), which adds access, quota, ownership, kind
// conversion and join-as-owner in `admin` mode. Also exported from here:
// useAccounts, useCloudSignIn, useWorkspace (one workspace's state + every
// call on it, incl. invitations by Gamma Cloud username),
// AccessRows, StorageRow, MembersList, InviteDialog, NameDialog, the role
// tables and workspaceMeta (the switcher's one-line description).
import React from "react";
import { API, apiJson, fmtBytes } from "../shared/lib/utils";
import { ActionMenu, MenuSelect } from "../shared/ui/Menus";
import { PaneHead, Section, Row, SubDialog, Field, Empty, QuotaMeter, UnitInput, AccountPicker, Segmented } from "./SettingsKit";
import {
  CheckIcon, DatabaseIcon, ExportIcon, GlobeIcon, HardDriveIcon, ImportIcon, LogOutIcon, PenIcon,
  PlusIcon, ShieldIcon, Trash2Icon, UserIcon, UsersIcon,
} from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

// Workspace roles as the UI words them (docs/dev/workspaces.md); the account
// menu's switcher in App.jsx reads the same table.
export const ROLE_OPTIONS = [["owner", t("Owner")], ["editor", t("Can edit")], ["viewer", t("View only")]];
export const ROLE_LABEL = { owner: t("owner"), editor: t("can edit"), viewer: t("view only") };
const ROLE_TEXT = { owner: t("own it"), editor: t("can edit"), viewer: t("can view") };
// One line under a switcher entry / workspace row: what kind it is and, for
// a shared one, your role.
export function workspaceMeta(w) {
  if (w.mirror_of) return t("clone of {mirror_of}", { mirror_of: w.mirror_of });
  if (w.personal) return w.default ? t("personal · default") : "personal";
  return `${w.access === "public" ? "public · " : ""}${ROLE_LABEL[w.role] || w.role}`;
}
export const ACCESS_OPTIONS = [["private", t("Private"), UsersIcon], ["public", t("Public"), GlobeIcon]];
export const PUBLIC_ROLE_OPTIONS = [["viewer", t("Everyone can view")], ["editor", t("Everyone can edit")]];

// The account directory (GET /api/accounts) for the pickers: null while
// loading, [] when it cannot be read (the guest).
export function useAccounts() {
  const [accounts, setAccounts] = React.useState(null);
  React.useEffect(() => {
    let live = true;
    apiJson(`${API}/accounts`).then((d) => { if (live) setAccounts(d.accounts || []); }).catch(() => { if (live) setAccounts([]); });
    return () => { live = false; };
  }, []);
  return accounts;
}

// Whether this server signs people in with Gamma Cloud (GET /api/server-config)
// — what lets an owner invite someone by their cloud username.
export function useCloudSignIn() {
  const [on, setOn] = React.useState(false);
  React.useEffect(() => {
    let live = true;
    apiJson(`${API}/server-config`).then((c) => { if (live) setOn(!!c?.cloud?.enabled); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return on;
}

// One workspace as the Settings dialogs see it: GET /api/workspaces/{id}
// (members + quota), plus every call on it. After a successful mutation the
// workspace is READ AGAIN from the server (not patched from the response),
// so the dialog always shows what is stored. Calls return the response, or
// null after setError.
export function useWorkspace(wsId) {
  const [info, setInfo] = React.useState(null);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const reload = React.useCallback(async () => {
    if (!wsId) return null;
    try {
      const d = await apiJson(`${API}/workspaces/${encodeURIComponent(wsId)}`);
      setInfo(d);
      return d;
    } catch (err) {
      setError(err.message);
      return null;
    }
  }, [wsId]);

  React.useEffect(() => {
    setInfo(null);
    setError("");
    reload();
  }, [reload]);

  async function call(path, method, body, { refresh = true } = {}) {
    setBusy(true);
    setError("");
    try {
      const d = await apiJson(`${API}/workspaces/${encodeURIComponent(wsId)}${path}`, {
        method,
        ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
      });
      if (refresh) await reload();
      return d;
    } catch (err) {
      setError(err.message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  return {
    info, error, setError, busy, reload,
    update: (patch) => call("", "PUT", patch),                // {name} | {default} | {kind} | {access, public_role} | {quota_mb}
    setRole: (username, role) => call(`/members/${encodeURIComponent(username)}`, "PUT", { role }),
    removeMember: (username) => call(`/members/${encodeURIComponent(username)}`, "DELETE"),
    inviteCloud: (username, role) => call("/invites", "POST", { username, role }),   // by Gamma Cloud username
    cancelInvite: (subject) => call(`/invites/${encodeURIComponent(subject)}`, "DELETE"),
    destroy: () => call("", "DELETE", null, { refresh: false }),
  };
}

// The admin-only settings of a shared workspace: who may open it and its
// own upload cap. `canEdit` false renders them read-only (an owner sees
// what the admin decided).
export function AccessRows({ info, canEdit, onUpdate }) {
  if (!info || info.kind === "personal") return null;
  const isPublic = info.access === "public";
  return (
    <>
      <Row
        icon={isPublic ? GlobeIcon : UsersIcon} label={t("Access")}
        hint={isPublic ? t("every account on this server can open it") : t("members only, by invitation")}
        title={t("Private: only invited members can open the workspace. Public: every signed-in account on this server can open it with the role below; invited members keep their own role. Set by server admins.")}
      >
        {canEdit ? (
          <MenuSelect
            value={info.access} label={t("Access")} options={ACCESS_OPTIONS}
            onChange={(access) => { if (access !== info.access) onUpdate({ access, public_role: info.public_role }); }}
          />
        ) : <span className="settingDesc">{isPublic ? t("Public") : t("Private")}</span>}
      </Row>
      {isPublic ? (
        <Row icon={UserIcon} label={t("Public role")} hint={t("what everyone gets")}
          title={t("The role every signed-in account holds in this workspace unless they are invited with another.")}>
          {canEdit ? (
            <MenuSelect
              value={info.public_role} label={t("Public role")} options={PUBLIC_ROLE_OPTIONS}
              onChange={(public_role) => { if (public_role !== info.public_role) onUpdate({ access: "public", public_role }); }}
            />
          ) : <span className="settingDesc">{PUBLIC_ROLE_OPTIONS.find(([r]) => r === info.public_role)?.[1]}</span>}
        </Row>
      ) : null}
      {canEdit && info.quota != null ? (
        <Row icon={HardDriveIcon} label={t("Workspace quota")} hint={t("total uploads · blank or 0 = unlimited")}
          title={t("A shared workspace's own storage cap. It counts against nobody's personal quota; the per-file limit is the server default.")}>
          <UnitInput
            unit="MB" min={0} placeholder="unlimited" value={info.quota_mb ?? ""}
            onCommit={(raw) => {
              const n = raw.trim() === "" ? 0 : Number.parseInt(raw, 10);
              if (!Number.isFinite(n) || n < 0) return;
              if ((n || null) !== (info.quota_mb ?? null)) onUpdate({ quota_mb: n });
            }}
          />
        </Row>
      ) : null}
    </>
  );
}

// The storage that applies to uploads into the workspace (GET quota): a
// personal workspace shows its account's meter (all of that account's
// personal workspaces together), a shared one its own.
export function StorageRow({ quota, me }) {
  if (!quota) return null;
  const who = quota.account
    ? t("{workspace_bytes} here · counts against {s} storage", { workspace_bytes: fmtBytes(quota.workspace_bytes), s: quota.account === me ? "your" : `${quota.account}'s` })
    : quota.quota_mb ? t("this workspace's own quota · {quota_mb} MB", { quota_mb: quota.quota_mb }) : t("this workspace's own quota · unlimited");
  return (
    <Row icon={DatabaseIcon} label={t("Storage")} hint={who}
      title={t("Uploads into a personal workspace count against its account's quota, together with the account's other personal workspaces; a shared workspace has its own optional quota set by an admin.")}>
      {quota.account
        ? <QuotaMeter usedBytes={quota.used_bytes} quotaMb={quota.quota_mb} />
        : <span className="settingDesc">{fmtBytes(quota.workspace_bytes)}</span>}
    </Row>
  );
}

// The explicit members, each with a role menu and a remove button
// (owners). `me` marks "you"; the last owner cannot go; leaving yourself
// is an action row of the dialog, not a button on your own row. Pending
// invitations by Gamma Cloud username (`pending: true`) follow, tagged, with
// only a remove button (`onCancel`).
export function MembersList({ info, me, canManage, busy, onSetRole, onRemove, onCancel }) {
  const members = info?.members || [];
  const owners = members.filter((m) => m.role === "owner" && !m.pending).length;
  return members.map((m) => {
    const self = m.username === me && !m.pending;
    const stuck = m.role === "owner" && owners <= 1;
    return (
      <div key={m.pending ? `pending:${m.subject}` : m.username} className="aiProvRow">
        <span className={`aiProvAvatar ${m.role === "owner" ? "active" : ""}`}>
          {m.role === "owner" ? <ShieldIcon size={15} /> : <UserIcon size={15} />}
        </span>
        <span className="aiProvMeta">
          <span className="aiProvName">
            {m.username}
            {self ? <span className="uiTag">{t("you")}</span> : null}
            {m.pending ? <span className="uiTag" title={t("Invited by Gamma Cloud username; joins on their first sign-in to this server")}>{t("pending")}</span> : null}
          </span>
          <span className="aiProvDesc">
            {ROLE_OPTIONS.find(([r]) => r === m.role)?.[1] || m.role}
            {m.added_by && m.added_by !== m.username ? t(" · invited by {added_by}", { added_by: m.added_by }) : ""}
          </span>
        </span>
        <span className="aiProvActions">
          {m.pending && canManage ? (
            <button
              className="uiBtn sm iconSq" disabled={busy}
              title={t("Withdraw the invitation to {username}", { username: m.username })} aria-label={t("Remove")}
              onClick={() => onCancel(m)}
            >
              <Trash2Icon size={13} />
            </button>
          ) : null}
          {canManage && !m.pending ? (
            <MenuSelect
              value={m.role} label={t("Role")} options={ROLE_OPTIONS}
              onChange={(r) => { if (r !== m.role) onSetRole(m.username, r); }}
            />
          ) : null}
          {canManage && !m.pending && !self && !stuck ? (
            <button
              className="uiBtn sm iconSq" disabled={busy}
              title={t("Remove {username}", { username: m.username })} aria-label={t("Remove")}
              onClick={() => onRemove(m.username)}
            >
              <Trash2Icon size={13} />
            </button>
          ) : null}
        </span>
      </div>
    );
  });
}

// Invite: pick an account from the directory, choose a role. With `cloud`
// (this server signs in with Gamma Cloud) a person can also be named by
// their cloud username before they have an account here; that invitation
// waits for their first sign-in and grants edit or view, never ownership.
// onSubmit(username, role, via) — via is "local" or "cloud".
const INVITE_VIA = [["local", t("On this server")], ["cloud", t("Gamma Cloud username")]];
const CLOUD_ROLE_OPTIONS = ROLE_OPTIONS.filter(([r]) => r !== "owner");

export function InviteDialog({ name, accounts, exclude, cloud, busy, error, onSubmit, onClose }) {
  const [via, setVia] = React.useState("local");
  const [username, setUsername] = React.useState("");
  const [cloudName, setCloudName] = React.useState("");
  const [role, setRole] = React.useState("editor");
  const byCloud = cloud && via === "cloud";
  const who = byCloud ? cloudName.trim().replace(/^@/, "").toLowerCase() : username;
  const submit = () => { if (who && !busy) onSubmit(who, role, byCloud ? "cloud" : "local"); };
  function pickVia(next) {
    setVia(next);
    if (next === "cloud" && role === "owner") setRole("editor");
  }
  return (
    <SubDialog title={t("Invite to {name}", { name: name })} onClose={onClose} draft={{ username, cloudName, role }}>
      <div className="settingsForm">
        {cloud ? (
          <Field label={t("Find by")}>
            <Segmented value={via} onChange={pickVia} options={INVITE_VIA} disabled={busy} />
          </Field>
        ) : null}
        {byCloud ? (
          <Field label={t("Gamma Cloud username")} hint={t("they join on their first sign-in here")}>
            <input
              className="aiKeyInput" type="text" autoFocus spellCheck={false} autoCapitalize="none"
              placeholder="username" value={cloudName}
              onChange={(e) => setCloudName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
            />
          </Field>
        ) : (
          <Field label={t("Account")} hint={t("anyone with an account on this server")}>
            <AccountPicker accounts={accounts} exclude={exclude} value={username} onChange={setUsername} autoFocus />
          </Field>
        )}
        <Field label={t("Role")} hint={byCloud ? t("editors write; viewers read") : t("owners manage members; editors write; viewers read")}>
          <MenuSelect value={role} label={t("Role")} options={byCloud ? CLOUD_ROLE_OPTIONS : ROLE_OPTIONS} block onChange={setRole} />
        </Field>
        {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
        <div className="reportModalBtns">
          <button className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button className="uiBtn primary" disabled={busy || !who} onClick={submit}>{t("Invite")}</button>
        </div>
      </div>
    </SubDialog>
  );
}

// A one-field dialog: rename, or name a new workspace.
export function NameDialog({ title, label, hint, initial, submitLabel, busy, error, onSubmit, onClose }) {
  const [name, setName] = React.useState(initial || "");
  return (
    <SubDialog title={title} onClose={onClose} draft={name}>
      <div className="settingsForm">
        <Field label={label} hint={hint}>
          <input
            className="aiKeyInput" type="text" autoFocus value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) onSubmit(name.trim()); }}
          />
        </Field>
        {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
        <div className="reportModalBtns">
          <button className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button className="uiBtn primary" disabled={busy || !name.trim()} onClick={() => onSubmit(name.trim())}>{submitLabel}</button>
        </div>
      </div>
    </SubDialog>
  );
}

// One workspace, everything its owner (or, with `admin`, a server admin)
// can do to it, in the settings-row grammar: General (name, storage),
// Access (shared; admins edit, owners read), Members (shared), Actions
// (default, kind, join, leave, delete — each a labelled row with a hint).
// `canOpen` / `onOpen` wire the Open button; `onLeft` fires after the
// caller leaves or deletes it (the pane switches away if it was the open
// one). `personalCount` hides Delete on an account's last personal workspace.
function WorkspacePage({ title, onClose, children }) {
  return <>
    <button className="uiBtn sm settingsInlineLink" onClick={onClose}>{t("Back to workspaces")}</button>
    <PaneHead icon={UsersIcon} title={title}>{t("Workspace settings")}</PaneHead>
    {children}
  </>;
}

export function ManageWorkspaceDialog({ wsId, me, admin, accounts, confirm, setStatus, canOpen, onOpen, onClose, onLeft, personalCount, inline = false }) {
  const ws = useWorkspace(wsId);
  const cloudSignIn = useCloudSignIn();
  const [renaming, setRenaming] = React.useState(false);
  const [inviting, setInviting] = React.useState(false);
  const info = ws.info;
  const isPersonal = info?.kind === "personal";
  const mine = isPersonal && info?.personal_of === me;
  const isOwner = info?.role === "owner";
  const manages = isOwner || admin;
  const members = (info?.members || []).filter((m) => !m.pending); // pending invitations are not members yet
  const explicitMember = members.some((m) => m.username === me);
  const soleOwner = members.filter((m) => m.role === "owner").length <= 1 && isOwner;

  const done = (msg) => { if (msg) setStatus(msg); };

  async function saveName(name) {
    const d = await ws.update({ name });
    if (d) { setRenaming(false); done(t("Renamed to {name}.", { name: d.name })); }
  }

  async function invite(username, role, via) {
    const verb = role === "viewer" ? t("view") : role === "editor" ? t("edit") : t("manage");
    if (via === "cloud") {
      const d = await ws.inviteCloud(username, role);
      if (!d) return;
      setInviting(false);
      done(d.invited?.member
        ? t("{member} can now {verb} {name}.", { member: d.invited.member, verb, name: d.name })
        : t("Invited {username}; they can {verb} {name} once they sign in with Gamma Cloud.", { username, verb, name: d.name }));
      return;
    }
    const d = await ws.setRole(username, role);
    if (d) { setInviting(false); done(t("{username} can now {verb} {name}.", { username, verb, name: d.name })); }
  }

  function cancelInvite(m) {
    confirm({
      title: T("Withdraw invitation"),
      message: t("Withdraw the invitation to {username} for \"{name}\"?", { username: m.username, name: info?.name }),
      confirmLabel: t("Withdraw"), danger: true,
      onConfirm: async () => {
        const d = await ws.cancelInvite(m.subject);
        if (d) done(t("Withdrew the invitation to {username}.", { username: m.username }));
      },
    });
  }

  function remove(username) {
    const leaving = username === me;
    confirm({
      title: leaving ? t("Leave workspace") : t("Remove member"),
      message: leaving
        ? t("Leave \"{name}\"? You will need a new invitation to come back.", { name: info?.name })
        : t("Remove {username} from \"{name}\"? They keep nothing from it.", { username, name: info?.name }),
      confirmLabel: leaving ? t("Leave") : t("Remove"), danger: true,
      onConfirm: async () => {
        const d = await ws.removeMember(username);
        if (!d) return;
        if (leaving) { onClose(); onLeft?.(wsId); return; }
        done(`Removed ${username}.`);
      },
    });
  }

  function destroy() {
    confirm({
      title: T("Delete workspace"),
      message: t("Delete \"{name}\" with ALL its pages, PDFs, chats and backups{member}? This can't be undone.", { name: info?.name, member: isPersonal ? "" : t(", for every member") }),
      confirmLabel: t("Delete"), danger: true,
      onConfirm: async () => {
        const d = await ws.destroy();
        if (d) { done(d.warning || `Deleted ${info?.name}.`); onClose(); onLeft?.(wsId); }
      },
    });
  }

  function convert(kind) {
    const toShared = kind === "shared";
    confirm({
      title: toShared ? t("Convert to shared workspace") : t("Convert to personal workspace"),
      message: toShared
        ? t("Make \"{name}\" a shared workspace? {personal_of} stays its owner and can invite people; it stops counting against their storage. If it is their default, another personal workspace becomes the default.", { name: info?.name, personal_of: info?.personal_of })
        : t("Make \"{name}\" {username}'s personal workspace? It becomes private, its own quota is cleared, and it counts against their storage.", { name: info?.name, username: members[0]?.username }),
      confirmLabel: t("Convert"),
      onConfirm: async () => {
        const d = await ws.update({ kind });
        if (d) done(t("{name} is now a {kind} workspace.", { name: d.name, kind: d.kind === "shared" ? t("shared") : t("personal") }));
      },
    });
  }

  const title = info?.name || t("Workspace");
  const subtitle = !info ? "" : isPersonal
    ? (mine ? t("Your personal workspace{default}", { default: info.default ? t(" · your default") : "" }) : t("{personal_of}'s personal workspace{default}", { personal_of: info.personal_of, default: info.default ? t(" · their default") : "" }))
    : `${info.access === "public" ? "Public" : "Shared"} workspace · ${info.role ? `you ${ROLE_TEXT[info.role]}` : "you manage it as an admin"}`;
  const canDelete = manages && (!isPersonal || personalCount == null || personalCount > 1);

  const Surface = inline ? WorkspacePage : SubDialog;
  return (
    <Surface title={title} onClose={onClose}>
      <div className="settingsForm">
        {subtitle ? <div className="settingsPaneHint">{subtitle}</div> : null}
        {!info && !ws.error ? <Empty icon={UsersIcon}>{t("Loading…")}</Empty> : null}
        {info ? (
          <>
            <Section title={t("General")}>
              <Row icon={PenIcon} label={t("Name")} hint={info.name}>
                {manages ? <button className="uiBtn sm" disabled={ws.busy} onClick={() => setRenaming(true)}>{t("Rename")}</button> : null}
              </Row>
              <StorageRow quota={info.quota} me={me} />
            </Section>
            {!isPersonal ? (
              <Section title={t("Access")}>
                <AccessRows info={info} canEdit={!!admin} onUpdate={async (patch) => {
                  const d = await ws.update(patch);
                  if (d && patch.access) done(d.access === "public" ? t("{name} is open to everyone on this server.", { name: d.name }) : t("{name} is private.", { name: d.name }));
                  else if (d && "quota_mb" in patch) done(d.quota_mb ? t("Workspace quota set to {quota_mb} MB.", { quota_mb: d.quota_mb }) : t("Workspace quota removed."));
                }} />
                {!admin ? <div className="settingsPaneHint">{t("Access and the workspace's quota are set by a server admin.")}</div> : null}
              </Section>
            ) : null}
            {!isPersonal ? (
              <Section
                title={t("Members")}
                action={manages ? (
                  <button className="uiBtn sm" disabled={ws.busy} onClick={() => { ws.setError(""); setInviting(true); }}>
                    <PlusIcon size={13} /> {t("Invite")}
                  </button>
                ) : null}
              >
                <MembersList info={info} me={me} canManage={manages} busy={ws.busy} onSetRole={ws.setRole} onRemove={remove} onCancel={cancelInvite} />
                {info.access === "public" && !explicitMember ? (
                  <div className="settingsPaneHint">{t("You are in because the workspace is public — everyone on this server is.")}</div>
                ) : manages ? (
                  <div className="settingsPaneHint">{t("Naming someone Owner hands the workspace on; the role menu is how ownership moves.")}</div>
                ) : null}
              </Section>
            ) : null}
            <Section title={t("Actions")}>
              {mine && !info.default ? (
                <Row icon={CheckIcon} label={t("Default workspace")} hint={t("where the extension and plain links land")}
                  title={t("Requests that name no workspace — the browser extension's clips, older clients, a link without a workspace — land in your default workspace.")}>
                  <button className="uiBtn sm" disabled={ws.busy} onClick={async () => { const d = await ws.update({ default: true }); if (d) done(t("{name} is now your default workspace.", { name: d.name })); }}>
                    {t("Make default")}
                  </button>
                </Row>
              ) : null}
              {isPersonal && !admin ? (
                <Row icon={UsersIcon} label={t("Sharing")} hint={t("a personal workspace is just you")}
                  title={t("Share a page with a link, or ask a server admin for a shared workspace to work with others.")}>
                  <span className="settingDesc">{t("page links only")}</span>
                </Row>
              ) : null}
              {admin && isPersonal ? (
                <Row icon={UsersIcon} label={t("Convert to shared")} hint={t("let people in; the owner stays owner, it stops counting against them")}>
                  <button className="uiBtn sm" disabled={ws.busy} onClick={() => convert("shared")}>{t("Make shared")}</button>
                </Row>
              ) : null}
              {admin && !isPersonal && members.length === 1 ? (
                <Row icon={UserIcon} label={t("Convert to personal")} hint={t("hand it to {username} as a personal workspace", { username: members[0].username })}>
                  <button className="uiBtn sm" disabled={ws.busy} onClick={() => convert("personal")}>{t("Make personal")}</button>
                </Row>
              ) : null}
              {admin && !isPersonal && !explicitMember ? (
                <Row icon={ShieldIcon} label={t("Join as owner")} hint={info.access === "public" ? t("everyone can already open it; this adds you as an owner") : t("add yourself so you can open it")}>
                  <button className="uiBtn sm" disabled={ws.busy} onClick={async () => { const d = await ws.setRole(me, "owner"); if (d) done(t("You now own {name}.", { name: d.name })); }}>
                    {t("Join")}
                  </button>
                </Row>
              ) : null}
              {!isPersonal && explicitMember && !soleOwner ? (
                <Row icon={LogOutIcon} label={t("Leave")} hint={t("you will need a new invitation to come back")}>
                  <button className="uiBtn sm" disabled={ws.busy} onClick={() => remove(me)}>{t("Leave")}</button>
                </Row>
              ) : null}
              {canDelete ? (
                <Row icon={Trash2Icon} label={t("Delete workspace")} hint={isPersonal ? t("everything in it, and its backups") : t("everything in it, for every member")}>
                  <button className="uiBtn sm danger" disabled={ws.busy} onClick={destroy}>{t("Delete…")}</button>
                </Row>
              ) : null}
            </Section>
          </>
        ) : null}
        {ws.error && !inviting && !renaming ? <div className="settingsPaneHint aiKeysError">{ws.error}</div> : null}
        <div className="reportModalBtns">
          {canOpen || explicitMember ? <button className="uiBtn" onClick={onOpen}>{t("Open")}</button> : null}
          <button className="uiBtn primary" onClick={onClose}>{t("Done")}</button>
        </div>
      </div>
      {renaming ? (
        <NameDialog title={t("Rename workspace")} label={t("Name")} initial={info?.name} submitLabel={t("Save")}
          busy={ws.busy} error={ws.error} onSubmit={saveName} onClose={() => { setRenaming(false); ws.setError(""); }} />
      ) : null}
      {inviting ? (
        <InviteDialog
          name={title} accounts={accounts} exclude={members.map((m) => m.username)} cloud={cloudSignIn}
          busy={ws.busy} error={ws.error} onSubmit={invite} onClose={() => { setInviting(false); ws.setError(""); }}
        />
      ) : null}
    </Surface>
  );
}

// The Export / Import menus every workspace row carries. Export downloads
// an /api/export zip of that workspace; Import restores or merges one into
// it (owners restore, editors merge).
// One "Data" menu per workspace row: export (everything / databases) and,
// for editors and owners, import (merge / restore).
export function WorkspaceDataMenus({ w, exportWorkspace, importWorkspace, closeSettings }) {
  const run = (fn) => { closeSettings?.(); fn(); }; // progress shows in the status pill, the confirm wants the screen
  const items = [
    { icon: ExportIcon, label: T("Export everything (.zip)"), title: t("A zip of {name}: its databases + every uploaded PDF", { name: w.name }),
      onClick: () => run(() => exportWorkspace(w.id, true)) },
    { icon: DatabaseIcon, label: T("Export databases only (.zip)"), title: T("A small zip with just the databases — no uploaded PDFs"),
      onClick: () => run(() => exportWorkspace(w.id, false)) },
  ];
  if (w.role !== "viewer") {
    items.push({ icon: PlusIcon, label: T("Merge a backup into it…"), title: T("Add the backup's pages that are not there yet; nothing existing changes"),
      onClick: () => run(() => importWorkspace(w.id, "merge")) });
    if (w.role === "owner") items.push({ icon: ImportIcon, label: T("Restore from a backup (replace)…"), title: t("Replace {name}'s pages and chats with a backup zip", { name: w.name }),
      onClick: () => run(() => importWorkspace(w.id, "replace")) });
  }
  return <ActionMenu label={t("Data")} icon={DatabaseIcon} items={items} />;
}

export function WorkspacesSettings({ value, onServer }) {
  const { workspace, me, isAdmin, switchWorkspace, refreshSession,
          exportWorkspace, exportAll, importWorkspace, setStatus, confirm, closeSettings } = value;
  const [data, setData] = React.useState(null);  // GET /api/workspaces/mine: {workspaces, account}
  const [error, setError] = React.useState("");
  const [manage, setManage] = React.useState(null); // workspace id
  const [creating, setCreating] = React.useState(false);
  const [createError, setCreateError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const accounts = useAccounts();
  const currentId = workspace?.id;

  const refresh = React.useCallback(() => {
    apiJson(`${API}/workspaces/mine`).then(setData).catch((err) => setError(err.message));
  }, []);
  React.useEffect(() => { refresh(); }, [refresh]);

  const all = data?.workspaces || [];
  const personal = all.filter((w) => w.personal && !w.mirror_of);  // clones are listed in Settings → Account & sync
  const shared = all.filter((w) => !w.personal);

  async function submitCreate(name) {
    setBusy(true);
    setCreateError("");
    try {
      const d = await apiJson(`${API}/workspaces`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
      });
      setCreating(false);
      closeSettings?.();
      switchWorkspace(d.id); // open it right away
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setBusy(false);
    }
  }

  // After leaving or deleting the open workspace there is nothing to stay
  // in: go to the default one. Otherwise just refresh the lists.
  function left(id) {
    if (id === currentId) {
      closeSettings?.();
      switchWorkspace(all.find((w) => w.default && w.id !== id)?.id || all.find((w) => w.personal && w.id !== id)?.id);
      return;
    }
    refresh();
    refreshSession?.();
  }

  function row(w) {
    const current = w.id === currentId;
    const isPublic = w.access === "public";
    return (
      <div key={w.id} className="aiProvRow">
        <span className={`aiProvAvatar ${current ? "active" : ""}`}>
          {current ? <CheckIcon size={15} /> : w.personal ? <UserIcon size={15} /> : isPublic ? <GlobeIcon size={15} /> : <UsersIcon size={15} />}
        </span>
        <span className="aiProvMeta">
          <span className="aiProvName">
            {w.name}
            {w.default ? <span className="uiTag">{t("default")}</span> : null}
            {isPublic ? <span className="uiTag">{t("public")}</span> : null}
            {current ? <span className="uiTag">{t("open")}</span> : null}
          </span>
          <span className="aiProvDesc">
            {w.personal ? t("just you") : `${ROLE_LABEL[w.role] || w.role} · ${w.members} member${w.members === 1 ? "" : "s"}`}
            {` · ${fmtBytes(w.used_bytes)}`}
          </span>
        </span>
        <span className="aiProvActions">
          {!current ? <button className="uiBtn sm" onClick={() => { closeSettings?.(); switchWorkspace(w.id); }}>{t("Open")}</button> : null}
          <WorkspaceDataMenus w={w} exportWorkspace={exportWorkspace} importWorkspace={importWorkspace} closeSettings={closeSettings} />
          <button className="uiBtn sm" onClick={() => setManage(w.id)} title={t("Manage {name}", { name: w.name })}>
            <PenIcon size={13} /> {t("Manage")}
          </button>
        </span>
      </div>
    );
  }

  if (manage) return (
    <ManageWorkspaceDialog inline
      wsId={manage} me={me} admin={isAdmin} accounts={accounts} confirm={confirm} setStatus={setStatus}
      canOpen={manage !== currentId} personalCount={personal.length}
      onOpen={() => { closeSettings?.(); switchWorkspace(manage); }}
      onClose={() => { setManage(null); refresh(); refreshSession?.(); }}
      onLeft={left}
    />
  );

  return (
    <>
      <PaneHead icon={UsersIcon} title={t("Workspaces")} />
      {!data && !error ? <Empty icon={UsersIcon}>{t("Loading…")}</Empty> : null}
      {error ? <Empty icon={UsersIcon}>{t("Workspaces unavailable — {error}", { error: error })}</Empty> : null}
      {data ? (
        <>
          <Section title={t("Storage")}>
            <Row icon={DatabaseIcon} label={t("Your storage")} hint={data.account.max_upload_mb ? t("all personal workspaces · max {mb} MB per file", { mb: data.account.max_upload_mb }) : t("all personal workspaces")}
              title={t("Uploads into your personal workspaces count against your account's quota. Shared workspaces carry their own.")}>
              <QuotaMeter usedBytes={data.account.used_bytes} quotaMb={data.account.quota_mb} />
            </Row>
          </Section>
          <Section
            title={t("Personal")}
            action={(
              <span className="aiProvActions">
                <ActionMenu
                  label={t("Export all")} icon={ExportIcon} disabled={!personal.length}
                  items={[
                    { icon: ExportIcon, label: T("Everything (.zip)"), title: T("One zip holding an export of each personal workspace, PDFs included"),
                      onClick: () => { closeSettings?.(); exportAll(true); } },
                    { icon: DatabaseIcon, label: T("Databases only (.zip)"), title: T("One zip holding a database-only export of each personal workspace"),
                      onClick: () => { closeSettings?.(); exportAll(false); } },
                  ]}
                />
                <button className="uiBtn sm" disabled={busy} onClick={() => { setCreateError(""); setCreating(true); }}>
                  <PlusIcon size={13} /> {t("New workspace")}
                </button>
              </span>
            )}
          >
            {personal.map(row)}
          </Section>
          <Section title={t("Shared")}>
            {shared.length ? shared.map(row) : (
              <Empty icon={UsersIcon}>
                <span>{t("No shared workspaces yet.")}</span>
                {onServer ? <button className="uiBtn sm" onClick={onServer}><PlusIcon size={13} /> {t("New shared workspace")}</button>
                  : <span className="settingDesc">{t("An admin makes them.")}</span>}
              </Empty>
            )}
          </Section>
        </>
      ) : null}

      {creating ? (
        <NameDialog title={t("New personal workspace")} label={t("Name")}
          hint={t("a separate library of your own — work, life, play")}
          submitLabel={t("Create and open")} busy={busy} error={createError} onSubmit={submitCreate} onClose={() => setCreating(false)} />
      ) : null}
    </>
  );
}
