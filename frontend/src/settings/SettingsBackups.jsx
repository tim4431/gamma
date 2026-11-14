// Settings → Backups: snapshots of your workspaces, kept on the server —
// take one now (everything, or databases only), for one workspace or for
// all of yours at once; download, restore in place (replace or merge),
// delete. Every snapshot is a full copy that restores on its own; each
// workspace keeps at most a fixed number of manual ones, and a replace
// first keeps the current state as a "Before restore" snapshot. GUI for
// /api/workspaces/{ws}/backups* (gamma/ws_backup.py). Between the task
// table and the snapshots, admins get the Bucket (BackupBucket.jsx) that
// tasks may copy their snapshots to; what it holds is listed with the
// snapshots (/api/admin/offsite/snapshots, gamma/bucket_snapshots.py): a
// snapshot also in the bucket is tagged so, a copy only there is a row of
// its own (download, restore, delete), and the copies of a workspace this
// server lacks (a lost disk) are restored as a new workspace.
//
// Also here: ServerBackups — the admin's whole-data-directory snapshots
// (Settings → Server; /api/admin/backups*, gamma/backups.py). Restoring one
// of those is a stopped-server operation: `manage.py backups --restore
// <name>` (docs/dev/migrations.md).
import React from "react";
import { API, apiJson, fmtBytes } from "../shared/lib/utils";
import { saveUrl } from "../shared/lib/saveFile.js";
import { BackupTasks } from "./BackupTasks";
import { BackupBucket, BUCKET_COPIES_READ, BUCKET_READ, bucketWhere } from "./BackupBucket";
import { backupsOf, useSettingsRead, useSettingsReads } from "./settingsReads.js";
import { ActionMenu } from "../shared/ui/Menus";
import { PaneHead, Section, Empty } from "./SettingsKit";
import { CloudIcon, DatabaseIcon, DownloadIcon, HardDriveIcon, ImportIcon, PlusIcon, Trash2Icon } from "../shared/ui/Icons";
import { TaskBlock } from "../tasks/TasksTray";
import { isActive } from "../tasks/taskModel.js";
import { useJobEnd } from "../tasks/useTasks";
import { T, t, tn } from "../shared/i18n/i18n.js";

// The one date format of this pane: "Sep 23, 3:00 AM PDT", in the browser's
// time zone and naming it, since task schedules are stored in UTC.
// `fallback` when there is no date yet (a snapshot named but undated, a
// task that has not run).
export function fmtWhen(iso, fallback = "") {
  return iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : fallback;
}

// The jobs that hold a workspace, and the workspaces each works on.
const WORKSPACE_JOBS = ["snapshot", "restore", "workspace-delete"];
const jobWorkspaces = (job) => (job.kind === "snapshot" ? job.params?.workspaces || [] : [job.params?.ws]);

// The workspaces and each one's snapshots are the pane's reads, kept while
// the dialog is open (settingsReads.js): a second visit shows them at once
// and reads them again in place.
export function WorkspaceBackups({ value }) {
  const { workspace, tasks, setStatus, confirm, isAdmin } = value;
  const reads = useSettingsReads();
  const minePath = "/workspaces/mine";
  const mine = reads.get(minePath).data?.workspaces || null; // GET /api/workspaces/mine → workspaces
  const error = mine ? "" : reads.get(minePath).error;
  // ws id → {backups, max} | {backups: [], error}, or undefined while it is read
  const listOf = (id) => {
    const entry = reads.get(backupsOf(id));
    return entry.data !== undefined && !entry.error ? entry.data : entry.error ? { backups: [], error: entry.error } : undefined;
  };
  const loadList = React.useCallback((id) => { reads.read(backupsOf(id), { force: true }).catch(() => {}); }, [reads]);
  React.useEffect(() => {
    reads.read(minePath).catch(() => {});
  }, [reads]);
  React.useEffect(() => {
    for (const w of mine || []) reads.read(backupsOf(w.id)).catch(() => {});
  }, [reads, mine]);
  // The bucket (admins): where it is, and what it holds, by workspace.
  const bucketAt = bucketWhere(useSettingsRead(isAdmin ? BUCKET_READ : null).data?.settings);
  const copies = useSettingsRead(bucketAt ? BUCKET_COPIES_READ : null);
  const copyGroups = (bucketAt && copies.data?.groups) || [];
  const loadCopies = React.useCallback(() => { reads.read(BUCKET_COPIES_READ, { force: true }).catch(() => {}); }, [reads]);

  // Snapshots, restores and deletes run as background jobs
  // (tasks/useTasks.js): the one running for a workspace shows under its
  // heading and holds its Back up and Restore. Once one ends (wherever it
  // was started) its list reloads, and a deleted workspace leaves the pane.
  const running = React.useMemo(() => tasks.jobs.filter((job) => isActive(job) && WORKSPACE_JOBS.includes(job.kind)), [tasks.jobs]);
  const runningFor = (id) => running.find((job) => jobWorkspaces(job).includes(id)) || null;
  const busyIds = React.useMemo(() => new Set(running.flatMap(jobWorkspaces)), [running]);
  useJobEnd(tasks, WORKSPACE_JOBS, (job) => {
    if (job.kind === "workspace-delete") reads.read(minePath, { force: true }).catch(() => {});
    else if (job.params?.into === "new") { reads.read(minePath, { force: true }).catch(() => {}); loadCopies(); }
    else jobWorkspaces(job).forEach(loadList);
  });

  // A copy in the bucket: downloaded through a job whose file this tab
  // saves, restored into its workspace or (`into` "new") into one made for it.
  function fetchCopy(ws, c) {
    return startJob("bucket-download", { ws, name: c.name }, { download: "auto", pill: true },
      (message) => t("Download failed: {message}", { message }));
  }
  function restoreCopy(ws, label, c, mode) {
    const into = mode === "new" ? "new" : "";
    const b = when(c);
    confirm({
      title: into ? t("Restore as a new workspace") : mode === "merge" ? t("Merge backup") : t("Restore backup"),
      message: into
        ? t("Restore the copy of \"{name}\" from {b} as a new workspace? You become its owner; invite its members again afterwards.", { name: label, b })
        : mode === "merge"
          ? t("Merge the bucket's copy from {b} into \"{name}\"? Pages and chats it has that the workspace lacks are added; everything already there is kept.", { b, name: label })
          : t("Restore \"{name}\" to the bucket's copy from {b}? ALL of its current pages and chats are REPLACED by the copy's. The current state is saved first as a \"Before restore\" snapshot, so you can go back.", { name: label, b }),
      confirmLabel: into ? t("Restore") : mode === "merge" ? t("Merge") : t("Replace"),
      danger: mode === "replace",
      onConfirm: () => startJob("bucket-restore", { ws, name: c.name, mode: into ? "replace" : mode, into }, into ? {} : { restoreInto: ws },
        (message) => t("Restore failed: {message}", { message })),
    });
  }
  function removeCopy(ws, label, c) {
    confirm({
      title: T("Delete from the bucket"),
      message: t("Delete the bucket's copy of \"{name}\" from {b} ({size_bytes})? This can't be undone.", { name: label, b: when(c), size_bytes: fmtBytes(c.size_bytes) }),
      confirmLabel: t("Delete"), danger: true,
      onConfirm: async () => {
        try {
          await apiJson(`${API}${BUCKET_COPIES_READ}/${encodeURIComponent(ws)}/${encodeURIComponent(c.name)}`, { method: "DELETE" });
          setStatus(t("Deleted from the bucket."));
          loadCopies();
        } catch (e) { setStatus(t("Delete failed: {message}", { message: e.message })); }
      },
    });
  }

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
    // A plain navigation (or the app's fetch): the response is an attachment, so the page stays.
    // The listing's filename is the download's Content-Disposition (gamma-backup-<workspace>-<name>.zip),
    // so a host that saves the file names it as a browser does.
    const name = b.filename || `gamma-backup-${b.name}.zip`;
    saveUrl(`${API}/workspaces/${encodeURIComponent(w.id)}/backups/${encodeURIComponent(b.name)}/download`, name)
      .catch((err) => setStatus(t("Could not save {filename}: {message}", { filename: name, message: err.message })));
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
  const copiesOf = (id) => copyGroups.find((g) => g.workspace === id)?.snapshots || [];

  // One copy only the bucket holds. `here`: the server has its workspace,
  // which it restores into; else it comes back as a new workspace.
  function copyRow(ws, label, c, { here, busy }) {
    return (
      <div key={`bucket-${c.name}`} className="aiProvRow">
        <span className="aiProvAvatar"><CloudIcon size={16} /></span>
        <span className="aiProvMeta">
          <span className="aiProvName">{when(c)}<span className="uiTag">{c.task ? t("Automatic") : t("backup")}</span></span>
          <span className="aiProvDesc">{[fmtBytes(c.size_bytes), t("only in the bucket")].join(" · ")}</span>
        </span>
        <span className="aiProvActions">
          <button className="uiBtn sm iconSq" title={t("Download as a zip")} aria-label={t("Download")} onClick={() => fetchCopy(ws, c)}>
            <DownloadIcon size={16} />
          </button>
          {here ? (
            <ActionMenu
              label={t("Restore")} icon={ImportIcon} disabled={busy}
              items={[
                { icon: ImportIcon, label: T("Replace…"), title: T("Put the workspace back exactly as it was in this snapshot"), danger: true, onClick: () => restoreCopy(ws, label, c, "replace") },
                { icon: PlusIcon, label: T("Merge…"), title: T("Add what the snapshot has and the workspace lacks"), onClick: () => restoreCopy(ws, label, c, "merge") },
              ]}
            />
          ) : (
            <button className="uiBtn sm" disabled={busy} title={t("Make a workspace of this copy, with you as its owner")}
              onClick={() => restoreCopy(ws, label, c, "new")}><ImportIcon size={14} /> {t("Restore as new…")}</button>
          )}
          <button className="uiBtn sm iconSq" title={t("Delete this copy from the bucket")} aria-label={t("Delete")} onClick={() => removeCopy(ws, label, c)}>
            <Trash2Icon size={16} />
          </button>
        </span>
      </div>
    );
  }

  // The bucket's copies of the workspaces this list has no group for: this
  // server lacks them (a lost disk), or they are another account's.
  function elsewhere(g) {
    const label = g.name || t("Unnamed workspace");
    const job = runningFor(g.workspace);
    return (
      <Section key={`bucket-${g.workspace}`} title={<>{label}<span className="uiTag">{g.here ? t("another account's") : t("not on this server")}</span></>}>
        {job ? <TaskBlock task={job} tasks={tasks} /> : null}
        {g.snapshots.map((c) => copyRow(g.workspace, label, c, { here: g.here, busy: Boolean(job) }))}
      </Section>
    );
  }

  function group(w) {
    const list = listOf(w.id);
    const owner = w.role === "owner";
    const inBucket = copiesOf(w.id);
    const kept = new Set((list?.backups || []).map((b) => b.name));
    const bucketOnly = list && !list.error ? inBucket.filter((c) => !kept.has(c.name)) : [];
    const sent = new Set(inBucket.map((c) => c.name));
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
        {job ? <TaskBlock task={job} tasks={tasks} /> : null}
        {!list ? <Empty icon={DatabaseIcon} loading>{t("Loading…")}</Empty> : null}
        {list?.error ? <div className="settingsPaneHint aiKeysError">{list.error}</div> : null}
        {list && !list.error && !list.backups.length && !bucketOnly.length ? (
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
                {sent.has(b.name) ? <span className="uiTag" title={t("A copy of this snapshot is in the bucket")}>{t("in the bucket")}</span> : null}
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
                  label={t("Restore")} icon={ImportIcon} disabled={Boolean(job)}
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
        {bucketOnly.map((c) => copyRow(w.id, w.name, c, { here: true, busy: Boolean(job) }))}
        {full ? <div className="settingsPaneHint">{t("This workspace holds its maximum of {max} manual snapshots — delete one to take another. Automatic backups have their own retention.", { max: list.max })}</div> : null}
      </Section>
    );
  }

  return (
    <>
      <PaneHead icon={DatabaseIcon} title={t("Backups")}>{t("Server-kept snapshots and the tasks that take them.")}</PaneHead>
      {!mine && !error ? <Empty icon={DatabaseIcon} loading>{t("Loading…")}</Empty> : null}
      {error ? <Empty icon={DatabaseIcon}>{t("Backups unavailable — {error}", { error: error })}</Empty> : null}
      {mine ? (
        <>
          <BackupTasks workspaces={mine} bucket={bucketAt} confirm={confirm} onRefresh={loadList} onRan={bucketAt ? loadCopies : undefined} />
          {isAdmin ? <BackupBucket setStatus={setStatus} /> : null}
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
          {copies.error ? <div className="settingsPaneHint aiKeysError">{t("The bucket could not be read: {error}", { error: copies.error })}</div> : null}
          {mine.map(group)}
          {copyGroups.filter((g) => !mine.some((w) => w.id === g.workspace)).map(elsewhere)}
        </>
      ) : null}
    </>
  );
}

export function ServerBackups({ setStatus, confirm, tasks }) {
  // the Server pane's read (settingsReads.js), newest first
  const { data, error: readError, reload } = useSettingsRead("/admin/backups");
  const rows = data ? [...(data.backups || [])].reverse() : null;
  const [ownError, setError] = React.useState("");
  const error = ownError || (!data ? readError : "");
  const refresh = React.useCallback(() => {
    reload().catch((e) => setError(e.message));
  }, [reload]);
  // A snapshot is a background job (server-backup): its progress shows
  // here and in Background tasks, and the list reloads once it ends.
  const job = tasks.jobs.find((j) => j.kind === "server-backup" && isActive(j)) || null;
  const busy = Boolean(job);
  useJobEnd(tasks, ["server-backup"], refresh);

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
    // A plain navigation (or the app's fetch): the response is an attachment, so the page stays.
    const name = `gamma-backup-${b.name}.zip`;
    saveUrl(`${API}/admin/backups/${encodeURIComponent(b.name)}/download`, name)
      .catch((err) => setStatus(t("Could not save {filename}: {message}", { filename: name, message: err.message })));
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
      {job ? <TaskBlock task={job} tasks={tasks} /> : null}
      {rows === null && !error ? <Empty icon={DatabaseIcon} loading>{t("Loading…")}</Empty> : null}
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
