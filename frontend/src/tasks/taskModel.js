// The Background tasks list (docs/dev/tasks.md), pure: the server's jobs
// (gamma/jobs.py, GET /api/jobs) and this tab's own work (a PDF loading in
// the viewer, an upload, an AI lookup) as one list of tasks.
// tests/tasks.test.mjs.
//
// A task is a server job — {id, kind, owner, workspace, title, params,
// state, progress: {done, total, unit, phase, item}, error, created_at,
// started_at, finished_at, artifact: {name, type, size} | null, downloaded,
// stoppable, stopping, readonly?} — or a local row — {id, kind, name, state,
// info, fraction (0..1), cancel, local: true, created_at}. Both have one of
// the same five states.

export const isActive = (task) => task?.state === "queued" || task?.state === "running";
export const isFinished = (task) => task?.state === "done" || task?.state === "failed" || task?.state === "cancelled";

// The most local rows kept: the oldest finished ones go first.
export const LOCAL_LIMIT = 20;

const time = (stamp) => {
  const ms = Date.parse(stamp || "");
  return Number.isFinite(ms) ? ms : 0;
};

// The order the tray lists: what runs or waits first, the oldest on top
// (the queue's order), then what finished, the newest on top.
export function sortTasks(tasks) {
  return [...tasks].sort((a, b) => {
    const activeA = isActive(a), activeB = isActive(b);
    if (activeA !== activeB) return activeA ? -1 : 1;
    if (activeA) return time(a.started_at || a.created_at) - time(b.started_at || b.created_at);
    return time(b.finished_at || b.created_at) - time(a.finished_at || a.created_at);
  });
}

// The server's jobs and this tab's rows as one sorted list.
export function mergeTasks(jobs, local) {
  return sortTasks([...jobs.map((job) => ({ ...job, local: false })), ...local.map((row) => ({ ...row, local: true }))]);
}

// A new local row: running unless said otherwise, stamped now.
export function localRow(row, id, now = Date.now()) {
  return { state: "running", info: "", ...row, id, local: true, created_at: new Date(now).toISOString() };
}

// Local rows after one more: the newest first; past LOCAL_LIMIT the oldest
// finished rows go (a running one never does).
export function addLocalRow(rows, row) {
  const next = [row, ...rows];
  while (next.length > LOCAL_LIMIT) {
    const oldest = next.map(isFinished).lastIndexOf(true);
    if (oldest < 0) break;
    next.splice(oldest, 1);
  }
  return next;
}

// How far a task got, 0..1, or undefined when it cannot tell.
export function progressFraction(task) {
  if (typeof task?.fraction === "number") return Math.max(0, Math.min(1, task.fraction));
  const { done, total } = task?.progress || {};
  return total > 0 && typeof done === "number" ? Math.max(0, Math.min(1, done / total)) : undefined;
}

// The server's jobs that finished between two listings (or were known
// active — just started here — and come back finished).
export function newlyFinished(before, after) {
  const was = new Map(before.map((job) => [job.id, job]));
  return after.filter((job) => isFinished(job) && isActive(was.get(job.id)));
}

// What the tray button shows: "running" (a spinner) while anything runs or
// waits, else "failed" (a red dot) for a failure worth a look, "ready" (an
// accent dot) for a file not downloaded yet, or "".
export function trayBadge(tasks, quietKinds = new Set(["ai"])) {
  if (tasks.some(isActive)) return "running";
  if (tasks.some((task) => task.state === "failed" && !(task.local && quietKinds.has(task.kind)))) return "failed";
  if (tasks.some((task) => task.state === "done" && task.artifact && !task.downloaded)) return "ready";
  return "";
}

// "1:05", "12:40", "1:02:09": how long a task has been running.
export function fmtElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(sec)}` : `${m}:${two(sec)}`;
}

export function elapsed(task, now = Date.now()) {
  const since = time(task.started_at || task.created_at);
  return since ? now - since : 0;
}
