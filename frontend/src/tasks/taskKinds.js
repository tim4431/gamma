// What each kind of task is in Background tasks (docs/dev/tasks.md): its
// icon, its title, the line under it, what can be done with it and where it
// opens. The server's kinds are gamma/jobs.py's, each started by its own
// POST /api/jobs/<route> — `retry` names the route and body that start the
// same work again. The local kinds are this tab's own work. Pure:
// tests/tasks.test.mjs.
import { fmtDate, t, tn } from "../shared/i18n/i18n.js";
import { fmtBytes } from "../shared/lib/format.js";
import { exportFormatOf } from "../transfers/transferFormats.js";
import { elapsed, fmtElapsed, isActive } from "./taskModel.js";

const named = (value) => value || t("Untitled");

// `icon`: a key of TasksTray.jsx's icon table. `open`: what a click on the
// row opens — "export" / "import" (the dialog, on this job), "settings:<pane>".
// `quiet`: a failure that does not dot the tray button (a paper without
// findable metadata is routine).
export const TASK_KINDS = {
  export: {
    icon: "export", open: "export",
    title: ({ params }) => t("Export “{name}” as {format}", {
      name: named(params.name), format: t(exportFormatOf(params.mode)?.label || params.mode || "") }),
    retry: ({ params }) => ["export", { page_id: params.page_id || "", folder: params.folder || "", mode: params.mode,
      pdf: params.pdf, highlights: params.highlights, notes: params.notes }],
  },
  "workspace-export": {
    icon: "download",
    title: ({ params }) => (params.all
      ? (params.uploads ? t("Export of every personal workspace") : t("Database export of every personal workspace"))
      : (params.uploads ? t("Export of “{name}”", { name: named(params.name) })
        : t("Database export of “{name}”", { name: named(params.name) }))),
    retry: ({ params }) => ["workspace-export", params.all ? { all: true, uploads: params.uploads }
      : { ws: params.ws, user: params.user || "", uploads: params.uploads }],
  },
  snapshot: {
    icon: "database", open: "settings:backups",
    title: ({ params }) => ((params.workspaces || []).length > 1
      ? tn("Snapshot of {n} workspace", "Snapshots of {n} workspaces", params.workspaces.length)
      : t("Snapshot of “{name}”", { name: named(params.names?.[0]) })),
    retry: ({ params }) => ["snapshot", { workspaces: params.workspaces, uploads: params.uploads, label: params.label }],
  },
  restore: {
    icon: "import", open: "settings:backups",
    title: ({ params }) => {
      const args = { name: named(params.name), file: params.filename || "" };
      if (params.source === "snapshot") {
        return params.mode === "merge" ? t("Merge a snapshot into “{name}”", args) : t("Restore “{name}” from a snapshot", args);
      }
      return params.mode === "merge" ? t("Merge {file} into “{name}”", args) : t("Restore “{name}” from {file}", args);
    },
    // An uploaded zip is gone once its job ended; a stored snapshot is still there.
    retry: ({ params }) => (params.source === "snapshot"
      ? ["restore-snapshot", { ws: params.ws, name: params.snapshot, mode: params.mode }] : null),
  },
  import: {
    icon: "import", open: "import",
    title: ({ params }) => t("Import {file}", { file: params.filename || t("a file") }),
  },
  indexing: {
    icon: "search",
    title: () => t("Indexing PDFs for search"),
  },
  "server-backup": {
    icon: "hardDrive", open: "settings:server",
    title: ({ params }) => (params.uploads ? t("Server snapshot of databases and files") : t("Server snapshot of databases")),
    retry: ({ params }) => ["server-backup", { label: params.label, uploads: params.uploads }],
  },
  "scheduled-backup": {
    icon: "database", open: "settings:backups",
    title: ({ params }) => t("Backup task “{name}”", { name: named(params.name) }),
  },
  // This tab's own work (App.jsx adds and updates these rows).
  download: { icon: "download" },
  upload: { icon: "upload" },
  file: { icon: "file" },
  ai: { icon: "sparkles", quiet: true },
};

const UNKNOWN = { icon: "activity" };
export const kindOf = (task) => TASK_KINDS[task?.kind] || UNKNOWN;
export const quietKinds = new Set(Object.keys(TASK_KINDS).filter((kind) => TASK_KINDS[kind].quiet));

export function taskTitle(task) {
  if (task.local) return t(task.name || "");
  const kind = TASK_KINDS[task.kind];
  return kind?.title ? kind.title(task) : task.title || task.kind;
}

// The phases a job reports (gamma/jobs.py Job.progress), worded.
const PHASES = {
  packing: t("Packing the files"),
  typesetting: t("Typesetting"),
  unpacking: t("Unpacking"),
  checking: t("Checking the backup"),
  saving: t("Saving the current state first"),
  copying: t("Copying files"),
  restoring: t("Restoring — can no longer be stopped"),
  annotations: t("Importing embedded annotations"),
  databases: t("Copying databases"),
  files: t("Copying files"),
};

// "12 of 40 pages", "120 MB of 2.1 GB", "" when there are no counts.
export function progressCounts({ done, total, unit } = {}) {
  if (typeof done !== "number") return "";
  if (unit === "bytes") return total ? t("{done} of {total}", { done: fmtBytes(done), total: fmtBytes(total) }) : fmtBytes(done);
  if (!total) return String(done);
  const args = { done, total };
  switch (unit) {
    case "pages": return tn("{done} of {total} page", "{done} of {total} pages", total, args);
    case "files": return tn("{done} of {total} file", "{done} of {total} files", total, args);
    case "items": return tn("{done} of {total} item", "{done} of {total} items", total, args);
    case "papers": return tn("{done} of {total} paper", "{done} of {total} papers", total, args);
    case "workspaces": return tn("{done} of {total} workspace", "{done} of {total} workspaces", total, args);
    default: return t("{done} of {total}", args);
  }
}

// What a running job is doing: its phase, how far it got, the item at hand.
export function progressText(task) {
  if (task.stopping) return t("Stopping…");
  const p = task.progress || {};
  return [PHASES[p.phase] || "", progressCounts(p), p.item || ""].filter(Boolean).join(" · ") || t("Working…");
}

const sameDay = (a, b) => a.toDateString() === b.toDateString();

// When a task finished: the time today, else the day and the time.
export function fmtFinished(stamp, now = new Date()) {
  const at = new Date(stamp || "");
  if (Number.isNaN(at.getTime())) return "";
  return sameDay(at, now) ? fmtDate(at, { hour: "numeric", minute: "2-digit" })
    : fmtDate(at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// The line under a task's title.
export function taskInfo(task) {
  if (task.local) return task.info || "";
  switch (task.state) {
    case "queued": return t("Waiting for its turn…");
    case "running": return progressText(task);
    case "done": return task.artifact ? `${task.artifact.name} · ${fmtBytes(task.artifact.size)}` : t("Done");
    case "failed": return task.error || t("Failed");
    default: return t("Stopped");
  }
}

// The time beside it: how long it has run, or when it ended.
export function taskTime(task, now = Date.now()) {
  if (task.state === "running" && task.started_at) return fmtElapsed(elapsed(task, now));
  if (!isActive(task) && task.finished_at) return fmtFinished(task.finished_at, new Date(now));
  return "";
}

// [route, body] that starts the same work again, or null.
export function retryOf(task) {
  return (!task.local && kindOf(task).retry?.(task)) || null;
}

export const canOpen = (task) => !task.local && Boolean(kindOf(task).open);

// The row's buttons, in order: "stop" while it can be stopped, then for a
// finished one "download" (its file), "retry" and "dismiss".
export function taskActions(task) {
  if (isActive(task)) return (task.local ? typeof task.cancel === "function" : task.stoppable) ? ["stop"] : [];
  const actions = [];
  if (task.state === "done" && task.artifact) actions.push("download");
  if ((task.state === "failed" || task.state === "cancelled") && retryOf(task)) actions.push("retry");
  if (!task.readonly) actions.push("dismiss");
  return actions;
}
