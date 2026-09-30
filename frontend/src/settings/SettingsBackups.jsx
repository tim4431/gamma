// Settings → Backups: snapshots of your workspaces, kept on the server —
// take one now (everything, or databases only), for one workspace or for
// all of yours at once; download, restore in place (replace or merge),
// delete. Every snapshot is a full copy that restores on its own; each
// workspace keeps at most a fixed number of manual ones, and a replace
// first keeps the current state as a "Before restore" snapshot. GUI for
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
import { TaskProgress } from "../tasks/TasksTray";
import { isActive } from "../tasks/taskModel.js";
import { taskTitle } from "../tasks/taskKinds.js";
import { T, t, tn } from "../shared/i18n/i18n.js";

// The one date format of this pane: "Sep 23, 3:00 AM PDT", in the browser's
// time zone and naming it, since task schedules are stored in UTC.
// `fallback` when there is no date yet (a snapshot named but undated, a
// task that has not run).
export function fmtWhen(iso, fallback = "") {
  return iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : fallback;
}

// The workspaces a snapshot or restore job works on.
const jobWorkspaces = (job) => (job.kind === "snapshot" ? job.params?.workspaces || [] : [job.params?.ws]);

export function WorkspaceBackups({ value }) {
  const { workspace, tasks, setStatus, confirm } = value;
  const [mine, setMine] = React.useState(null);   // GET /api/workspaces/mine → workspaces
  const [lists, setLists] = React.useState({});   // ws id → {backups, max} | {error}
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

  // Snapshots and restores run as background jobs (tasks/useTasks.js): the
  // one running for a workspace shows under its heading, and its list
  // reloads once the job ends (wherever it was started).
  const running = React.useMemo(() => tasks.jobs.filter((job) => isActive(job)
    && (job.kind === "snapshot" || job.kind === "restore")), [tasks.jobs]);
  const runningFor = (id) => running.find((job) => jobWorkspaces(job).includes(id)) || null;
  const busyIds = React.useMemo(() => new Set(running.flatMap(jobWorkspaces)), [running]);
  const wasBusy = React.useRef(new Set());
  React.useEffect(() => {
    for (const id of wasBusy.current) if (!busyIds.has(id)) loadList(id);
    wasBusy.current = busyIds;
  }, [busyIds, loadList]);

  async function startJob(route, body, meta, failure) {
    try {
      await tasks.start(route, body, meta);
    } catch (e) {
      setStatus(failure(e.message));
    }
  }
  function backUp(w, uploads) {
    return startJob("snapshot", { workspaces: [w.id], uploads, label: uploads ? "full" : "db" }, {},
      (message) => t("Backup of {name} failed: {message}", { name: w.name, message }));
  }
  // Every workspace you own, one snapshot each under the same label: one job.
  function backUpAll(uploads) {
    const targets = (mine || []).filter((w) => w.role === "owner").map((w) => w.id);
    return startJob("snapshot", { workspaces: targets, uploads, label: uploads ? "all-full" : "all-db" }, {},
      (message) => t("Backup failed: {message}", { message }));
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
      title: merging ? t("Merge backup") : t("Restore backup"),
      message: merging
        ? t("Merge the snapshot from {b} into \"{name}\"? Pages and chats it has that the workspace lacks are added; everything already there is kept.", { b: when(b), name: w.name })
        : t("Restore \"{name}\" to the snapshot from {b}? ALL of its current pages and chats are REPLACED by the snapshot's. Files the snapshot holds come back; files only the current pages use are cleaned up later. The current state is saved first as a \"Before restore\" snapshot, so you can go back.", { name: w.name, b: when(b) }),
      confirmLabel: merging ? t("Merge") : t("Replace"),
      danger: !merging,
      // A job: once it is done in the open workspace this tab reloads (App's
      // onJobFinished); elsewhere the list reloads with its "Before restore" snapshot.
      onConfirm: () => startJob("restore-snapshot", { ws: w.id, name: b.name, mode }, { restoreInto: w.id },
        (message) => (merging ? t("Merge failed: {message}", { message }) : t("Restore failed: {message}", { message }))),
    });
  }

  function remove(w, b) {
    confirm({
      title: T("Delete backup"),
      message: t("Delete the snapshot of \"{name}\" from {b} ({size_bytes})? This can't be undone.", { name: w.name, b: when(b), size_bytes: fmtBytes(b.size_bytes) }),
      confirmLabel: t("Delete"), danger: true,
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
    const full = list && list.backups.filter((b) => !b.scheduled && !b.auto).length >= list.max;
    const job = runningFor(w.id);
    return (
      <Section
        key={w.id}
        title={(
          <>
            {w.name}
            {w.personal ? <span className="uiTag">{t("personal")}</span> : <span className="uiTag">{w.access === "public" ? t("public") : t("shared")}</span>}
            {w.id === workspace?.id ? <span className="uiTag">{t("open")}</span> : null}
          </>
        )}
        action={owner ? (
          <ActionMenu
            label={t("Back up now")} icon={PlusIcon} disabled={Boolean(job) || full}
            items={[
              { icon: HardDriveIcon, label: T("Everything"), title: T("Databases plus every uploaded PDF and image — a complete copy"),
                onClick: () => backUp(w, true) },
              { icon: DatabaseIcon, label: T("Databases only"), title: T("Notes, chats and indexes — small and quick; uploaded PDFs are not copied"),
                onClick: () => backUp(w, false) },
            ]}
          />
        ) : null}
      >
        {job ? <div className="backupJob"><span>{taskTitle(job)}</span><TaskProgress task={job} label={taskTitle(job)} /></div> : null}
        {!list ? <Empty icon={DatabaseIcon}>{t("Loading…")}</Empty> : null}
        {list?.error ? <div className="settingsPaneHint aiKeysError">{list.error}</div> : null}
        {list && !list.error && !list.backups.length ? (
          <div className="settingsPaneHint">{owner ? t("No snapshots yet.") : t("No snapshots yet — only an owner takes them.")}</div>
        ) : null}
        {(list?.backups || []).map((b) => (
          <div key={b.name} className="aiProvRow">
            <span className={`aiProvAvatar ${b.uploads ? "active" : ""}`}>
              {b.uploads ? <HardDriveIcon size={16} /> : <DatabaseIcon size={16} />}
            </span>
            <span className="aiProvMeta">
              <span className="aiProvName">
                {when(b)}
                <span className="uiTag">{b.scheduled ? t("Automatic") : b.auto ? t("Before restore") : b.label || t("backup")}</span>
              </span>
              <span className="aiProvDesc">
                {[fmtBytes(b.size_bytes), b.uploads ? tn("{n} upload", "{n} uploads", b.upload_files) : t("databases only"),
                  b.missing_uploads ? tn("{n} file was missing", "{n} files were missing", b.missing_uploads) : "",
                  b.damaged?.length ? t("damaged database: {files}", { files: b.damaged.join(", ") }) : "",
                  b.by ? t("by {name}", { name: b.by }) : ""].filter(Boolean).join(" · ")}
              </span>
            </span>
            <span className="aiProvActions">
              <button className="uiBtn sm iconSq" title={t("Download as a zip")} aria-label={t("Download")} onClick={() => download(w, b)}>
                <DownloadIcon size={16} />
              </button>
              {w.role !== "viewer" ? (
                <ActionMenu
                  label={t("Restore")} icon={ImportIcon}
                  items={[
                    ...(owner ? [{ icon: ImportIcon, label: T("Replace…"), title: T("Put the workspace back exactly as it was in this snapshot"), danger: true, onClick: () => restore(w, b, "replace") }] : []),
                    { icon: PlusIcon, label: T("Merge…"), title: T("Add what the snapshot has and the workspace lacks"), onClick: () => restore(w, b, "merge") },
                  ]}
                />
              ) : null}
              {owner ? (
                <button className="uiBtn sm iconSq" title={t("Delete this snapshot")} aria-label={t("Delete")} onClick={() => remove(w, b)}>
                  <Trash2Icon size={16} />
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
              label={tn("Back up {n} workspace", "Back up all {n} workspaces", owned.length)} icon={PlusIcon}
              disabled={!owned.length || owned.some((w) => busyIds.has(w.id))}
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

export function ServerBackups({ setStatus, confirm, tasks }) {
  const [rows, setRows] = React.useState(null);
  const [error, setError] = React.useState("");

  const refresh = React.useCallback(() => {
    apiJson(`${API}/admin/backups`).then((d) => setRows([...d.backups].reverse())).catch((e) => setError(e.message));
  }, []);
  React.useEffect(() => { refresh(); }, [refresh]);
  // A snapshot is a background job (server-backup): its progress shows
  // here and in Background tasks, and the list reloads once it ends.
  const job = tasks.jobs.find((j) => j.kind === "server-backup" && isActive(j)) || null;
  const busy = Boolean(job);
  const wasBusy = React.useRef(false);
  React.useEffect(() => {
    if (wasBusy.current && !busy) refresh();
    wasBusy.current = busy;
  }, [busy, refresh]);

  async function create(uploads) {
    setError("");
    try {
      await tasks.start("server-backup", { label: uploads ? "full" : "db", uploads });
    } catch (e) {
      setError(e.message);
      setStatus(t("Backup failed: {message}", { message: e.message }));
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
      confirmLabel: t("Delete"),
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
      {job ? <div className="backupJob"><span>{taskTitle(job)}</span><TaskProgress task={job} label={taskTitle(job)} /></div> : null}
      {rows === null && !error ? <Empty icon={DatabaseIcon}>{t("Loading…")}</Empty> : null}
      {rows && !rows.length ? <Empty icon={DatabaseIcon}>{t("No snapshots yet. The server also takes one before every data upgrade.")}</Empty> : null}
      {(rows || []).map((b) => (
        <div key={b.name} className="aiProvRow">
          <span className={`aiProvAvatar ${b.uploads ? "active" : ""}`}>
            {b.uploads ? <HardDriveIcon size={16} /> : <DatabaseIcon size={16} />}
          </span>
          <span className="aiProvMeta">
            <span className="aiProvName">
              {when(b)}
              <span className="uiTag">{b.label || t("backup")}</span>
            </span>
            <span className="aiProvDesc">
              {[fmtBytes(b.size_bytes), tn("{n} database file", "{n} database files", (b.files || []).length),
                b.uploads ? tn("{n} upload", "{n} uploads", b.upload_files || 0) : t("databases only"),
                b.schema_version != null ? t("schema v{version}", { version: b.schema_version }) : "",
                b.damaged?.length ? t("damaged database: {files}", { files: b.damaged.join(", ") }) : ""].filter(Boolean).join(" · ")}
            </span>
          </span>
          <span className="aiProvActions">
            <button className="uiBtn sm iconSq" title={t("Download as a zip")} aria-label={t("Download")} onClick={() => download(b)}>
              <DownloadIcon size={16} />
            </button>
            <button className="uiBtn sm iconSq" title={t("Delete this snapshot")} aria-label={t("Delete")} disabled={busy} onClick={() => remove(b)}>
              <Trash2Icon size={16} />
            </button>
          </span>
        </div>
      ))}
      <div className="settingsPaneHint">
        {t("Snapshots live in {dir} inside the data directory. To roll back, stop the server and run {command}.", {
          dir: <code>backups/</code>, command: <code>{"manage.py backups --restore <name>"}</code> })}
      </div>
      {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
    </Section>
  );
}
