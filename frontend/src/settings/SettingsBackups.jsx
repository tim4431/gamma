// Settings → Backups: snapshots of your workspaces, kept on the server —
// take one now (everything, or databases only), for one workspace or for
// all of yours at once; download, restore in place (replace or merge),
// delete. Every snapshot is a full copy that restores on its own; each
// workspace keeps at most a fixed number. GUI for
// /api/workspaces/{ws}/backups* (gamma/ws_backup.py).
//
// Also here: ServerBackups — the admin's whole-data-directory snapshots
// (Settings → Server; /api/admin/backups*, gamma/backups.py). Restoring one
// of those is a stopped-server operation: `manage.py backups --restore
// <name>` (docs/dev/migrations.md).
import React from "react";
import { API, apiJson, fmtBytes } from "../shared/lib/utils";
import { BackupTasks } from "./BackupTasks";
import { ActionMenu } from "../shared/ui/Menus";
import { PaneHead, Section, Empty } from "./SettingsKit";
import { DatabaseIcon, DownloadIcon, HardDriveIcon, ImportIcon, PlusIcon, Trash2Icon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

// The one date format of this pane: "Sep 23, 3:00 AM UTC" — the time zone
// matters because tasks are scheduled in UTC. `fallback` when there is no
// date yet (a snapshot named but undated, a task that has not run).
export function fmtWhen(iso, fallback = "") {
  return iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : fallback;
}

export function WorkspaceBackups({ value }) {
  const { workspace, setStatus, confirm, closeSettings, reloadWorkspace } = value;
  const [mine, setMine] = React.useState(null);   // GET /api/workspaces/mine → workspaces
  const [lists, setLists] = React.useState({});   // ws id → {backups, max} | {error}
  const [busy, setBusy] = React.useState(null);   // ws id being backed up, or "all"
  const [error, setError] = React.useState("");

  const loadList = React.useCallback(async (id) => {
    try {
      const d = await apiJson(`${API}/workspaces/${encodeURIComponent(id)}/backups`);
      setLists((prev) => ({ ...prev, [id]: d }));
    } catch (e) {
      setLists((prev) => ({ ...prev, [id]: { backups: [], error: e.message } }));
    }
  }, []);
  React.useEffect(() => {
    apiJson(`${API}/workspaces/mine`).then((d) => {
      setMine(d.workspaces);
      d.workspaces.forEach((w) => loadList(w.id));
    }).catch((e) => setError(e.message));
  }, [loadList]);

  async function backUp(w, uploads) {
    setBusy(w.id);
    setStatus(t("Backing up {name}…", { name: w.name }));
    try {
      const b = await apiJson(`${API}/workspaces/${encodeURIComponent(w.id)}/backups`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: uploads ? "full" : "db", uploads }),
      });
      setStatus(t("Backed up {name} ({size_bytes}).", { name: w.name, size_bytes: fmtBytes(b.size_bytes) }));
      await loadList(w.id);
    } catch (e) {
      setStatus(t("Backup of {name} failed: {message}", { name: w.name, message: e.message }));
    } finally {
      setBusy(null);
    }
  }

  // Every workspace you own, one snapshot each under the same label.
  async function backUpAll(uploads) {
    const targets = (mine || []).filter((w) => w.role === "owner");
    setBusy("all");
    let done = 0;
    for (const w of targets) {
      setStatus(t("Backing up {name} ({done} of {targets})…", { name: w.name, done: done + 1, targets: targets.length }));
      try {
        await apiJson(`${API}/workspaces/${encodeURIComponent(w.id)}/backups`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label: uploads ? "all-full" : "all-db", uploads }),
        });
        done += 1;
      } catch (e) {
        setStatus(t("Backup of {name} failed: {message}", { name: w.name, message: e.message }));
      }
      await loadList(w.id);
    }
    setBusy(null);
    setStatus(`Backed up ${done} of ${targets.length} workspace${targets.length === 1 ? "" : "s"}.`);
  }

  function download(w, b) {
    // A plain navigation: the response is an attachment, so the page stays.
    const a = document.createElement("a");
    a.href = `${API}/workspaces/${encodeURIComponent(w.id)}/backups/${encodeURIComponent(b.name)}/download`;
    a.download = `gamma-backup-${b.name}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function restore(w, b, mode) {
    const merging = mode === "merge";
    confirm({
      title: merging ? "Merge backup" : "Restore backup",
      message: merging
        ? `Merge the snapshot from ${when(b)} into "${w.name}"? Pages and chats it has that the workspace lacks are added; everything already there is kept.`
        : `Restore "${w.name}" to the snapshot from ${when(b)}? ALL of its current pages and chats are REPLACED by the snapshot; uploaded PDFs are merged in. This cannot be undone.`,
      confirmLabel: merging ? "Merge" : "Replace",
      danger: !merging,
      onConfirm: async () => {
        setStatus(merging ? `Merging into ${w.name}…` : `Restoring ${w.name}…`);
        try {
          const d = await apiJson(`${API}/workspaces/${encodeURIComponent(w.id)}/backups/${encodeURIComponent(b.name)}/restore?mode=${mode}`, { method: "POST" });
          if (w.id === workspace?.id) {
            closeSettings?.();
            reloadWorkspace(); // every piece of in-memory state is stale now
            return;
          }
          setStatus(merging ? `Merged into ${w.name}: ${d.pages_added ?? 0} pages added.` : `Restored ${w.name}.`);
        } catch (e) {
          setStatus(`${merging ? "Merge" : "Restore"} failed: ${e.message}`);
        }
      },
    });
  }

  function remove(w, b) {
    confirm({
      title: T("Delete backup"),
      message: t("Delete the snapshot of \"{name}\" from {b} ({size_bytes})? This can't be undone.", { name: w.name, b: when(b), size_bytes: fmtBytes(b.size_bytes) }),
      confirmLabel: "Delete", danger: true,
      onConfirm: async () => {
        try {
          await apiJson(`${API}/workspaces/${encodeURIComponent(w.id)}/backups/${encodeURIComponent(b.name)}`, { method: "DELETE" });
          setStatus(t("Backup deleted."));
          loadList(w.id);
        } catch (e) { setStatus(t("Delete failed: {message}", { message: e.message })); }
      },
    });
  }

  const when = (b) => fmtWhen(b.created_at, b.name);
  const owned = (mine || []).filter((w) => w.role === "owner");

  function group(w) {
    const list = lists[w.id];
    const owner = w.role === "owner";
    const full = list && list.backups.filter((b) => !b.scheduled).length >= list.max;
    return (
      <Section
        key={w.id}
        title={(
          <>
            {w.name}
            {w.personal ? <span className="uiTag">personal</span> : <span className="uiTag">{w.access === "public" ? "public" : "shared"}</span>}
            {w.id === workspace?.id ? <span className="uiTag">open</span> : null}
          </>
        )}
        action={owner ? (
          <ActionMenu
            label={t("Back up now")} icon={PlusIcon} disabled={busy != null || full}
            items={[
              { icon: HardDriveIcon, label: T("Everything"), title: T("Databases plus every uploaded PDF and image — a complete copy"),
                onClick: () => backUp(w, true) },
              { icon: DatabaseIcon, label: T("Databases only"), title: T("Notes, chats and indexes — small and quick; uploaded PDFs are not copied"),
                onClick: () => backUp(w, false) },
            ]}
          />
        ) : null}
      >
        {!list ? <Empty icon={DatabaseIcon}>{t("Loading…")}</Empty> : null}
        {list?.error ? <div className="settingsPaneHint aiKeysError">{list.error}</div> : null}
        {list && !list.error && !list.backups.length ? (
          <div className="settingsPaneHint">{owner ? "No snapshots yet." : "No snapshots yet — only an owner takes them."}</div>
        ) : null}
        {(list?.backups || []).map((b) => (
          <div key={b.name} className="aiProvRow">
            <span className={`aiProvAvatar ${b.uploads ? "active" : ""}`}>
              {b.uploads ? <HardDriveIcon size={15} /> : <DatabaseIcon size={15} />}
            </span>
            <span className="aiProvMeta">
              <span className="aiProvName">
                {when(b)}
                <span className="uiTag">{b.scheduled ? "Automatic" : b.label || "backup"}</span>
              </span>
              <span className="aiProvDesc">
                {fmtBytes(b.size_bytes)}{b.uploads ? ` · ${b.upload_files} upload${b.upload_files === 1 ? "" : "s"}` : " · databases only"}{b.by ? ` · by ${b.by}` : ""}
              </span>
            </span>
            <span className="aiProvActions">
              <button className="uiBtn sm iconSq" title={t("Download as a zip")} aria-label={t("Download")} onClick={() => download(w, b)}>
                <DownloadIcon size={13} />
              </button>
              {w.role !== "viewer" ? (
                <ActionMenu
                  label={t("Restore")} icon={ImportIcon}
                  items={[
                    ...(owner ? [{ icon: ImportIcon, label: T("Replace…"), title: T("Put the workspace back exactly as it was in this snapshot"), onClick: () => restore(w, b, "replace") }] : []),
                    { icon: PlusIcon, label: T("Merge…"), title: T("Add what the snapshot has and the workspace lacks"), onClick: () => restore(w, b, "merge") },
                  ]}
                />
              ) : null}
              {owner ? (
                <button className="uiBtn sm iconSq" title={t("Delete this snapshot")} aria-label={t("Delete")} onClick={() => remove(w, b)}>
                  <Trash2Icon size={13} />
                </button>
              ) : null}
            </span>
          </div>
        ))}
        {full ? <div className="settingsPaneHint">{t("This workspace holds its maximum of {max} manual snapshots — delete one to take another. Automatic backups have their own retention.", { max: list.max })}</div> : null}
      </Section>
    );
  }

  return (
    <>
      <PaneHead icon={DatabaseIcon} title={t("Backups")}>{t("Server-kept snapshots and the tasks that take them.")}</PaneHead>
      {!mine && !error ? <Empty icon={DatabaseIcon}>{t("Loading…")}</Empty> : null}
      {error ? <Empty icon={DatabaseIcon}>{t("Backups unavailable — {error}", { error: error })}</Empty> : null}
      {mine ? (
        <>
          <BackupTasks workspaces={mine} confirm={confirm} onRefresh={loadList} />
          <Section title={t("Saved snapshots")} />
          <div className="reportModalBtns settingsAlignStart">
            <ActionMenu
              label={`Back up all ${owned.length} workspace${owned.length === 1 ? "" : "s"}`} icon={PlusIcon} disabled={busy != null || !owned.length}
              items={[
                { icon: HardDriveIcon, label: T("Everything"), title: T("One complete snapshot per workspace you own"), onClick: () => backUpAll(true) },
                { icon: DatabaseIcon, label: T("Databases only"), title: T("One small snapshot per workspace you own — no uploaded PDFs"), onClick: () => backUpAll(false) },
              ]}
            />
          </div>
          {mine.map(group)}
        </>
      ) : null}
    </>
  );
}

export function ServerBackups({ setStatus, confirm }) {
  const [rows, setRows] = React.useState(null);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const refresh = React.useCallback(() => {
    apiJson(`${API}/admin/backups`).then((d) => setRows([...d.backups].reverse())).catch((e) => setError(e.message));
  }, []);
  React.useEffect(() => { refresh(); }, [refresh]);

  async function create(uploads) {
    setBusy(true);
    setError("");
    setStatus(uploads ? "Backing up databases and uploads…" : "Backing up databases…");
    try {
      const b = await apiJson(`${API}/admin/backups`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: uploads ? "full" : "db", uploads }),
      });
      setStatus(t("Backup {name} created ({size_bytes}).", { name: b.name, size_bytes: fmtBytes(b.size_bytes) }));
      refresh();
    } catch (e) {
      setError(e.message);
      setStatus(t("Backup failed: {message}", { message: e.message }));
    } finally {
      setBusy(false);
    }
  }

  function download(b) {
    // A plain navigation: the response is an attachment, so the page stays.
    const a = document.createElement("a");
    a.href = `${API}/admin/backups/${encodeURIComponent(b.name)}/download`;
    a.download = `gamma-backup-${b.name}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function remove(b) {
    confirm({
      title: T("Delete backup"),
      message: t("Delete the snapshot {name} ({size_bytes})? This can't be undone.", { name: b.name, size_bytes: fmtBytes(b.size_bytes) }),
      confirmLabel: "Delete",
      danger: true,
      onConfirm: async () => {
        try {
          await apiJson(`${API}/admin/backups/${encodeURIComponent(b.name)}`, { method: "DELETE" });
          setStatus(t("Deleted {name}.", { name: b.name }));
          refresh();
        } catch (e) { setError(e.message); }
      },
    });
  }

  const when = (b) => fmtWhen(b.created_at, b.name);

  return (
    <Section
      title={t("Server backups")}
      action={(
        <ActionMenu
          label={t("Back up now")} icon={PlusIcon} disabled={busy}
          items={[
            { icon: DatabaseIcon, label: T("Databases only"), title: T("Every account's and workspace's database — small and quick; uploaded PDFs are not copied"),
              onClick: () => create(false) },
            { icon: HardDriveIcon, label: T("Everything"), title: T("Databases plus every uploaded PDF and image — a complete copy of the data directory"),
              onClick: () => create(true) },
          ]}
        />
      )}
    >
      {rows === null && !error ? <Empty icon={DatabaseIcon}>{t("Loading…")}</Empty> : null}
      {rows && !rows.length ? <Empty icon={DatabaseIcon}>{t("No snapshots yet. The server also takes one before every data upgrade.")}</Empty> : null}
      {(rows || []).map((b) => (
        <div key={b.name} className="aiProvRow">
          <span className={`aiProvAvatar ${b.uploads ? "active" : ""}`}>
            {b.uploads ? <HardDriveIcon size={15} /> : <DatabaseIcon size={15} />}
          </span>
          <span className="aiProvMeta">
            <span className="aiProvName">
              {when(b)}
              <span className="uiTag">{b.label || "backup"}</span>
            </span>
            <span className="aiProvDesc">
              {fmtBytes(b.size_bytes)} · {(b.files || []).length} database file{(b.files || []).length === 1 ? "" : "s"}
              {b.uploads ? ` + ${b.upload_files || 0} uploads` : " · databases only"}
              {b.schema_version != null ? ` · schema v${b.schema_version}` : ""}
            </span>
          </span>
          <span className="aiProvActions">
            <button className="uiBtn sm iconSq" title={t("Download as a zip")} aria-label={t("Download")} onClick={() => download(b)}>
              <DownloadIcon size={13} />
            </button>
            <button className="uiBtn sm iconSq" title={t("Delete this snapshot")} aria-label={t("Delete")} disabled={busy} onClick={() => remove(b)}>
              <Trash2Icon size={13} />
            </button>
          </span>
        </div>
      ))}
      <div className="settingsPaneHint">
        Snapshots live in <code>backups/</code> inside the data directory. To roll back, stop the server and run
        <code> manage.py backups --restore &lt;name&gt;</code>.
      </div>
      {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
    </Section>
  );
}
