// The Background tasks store (docs/dev/tasks.md): the server's jobs, polled
// from GET /api/jobs, and this tab's own rows (a PDF loading, an upload, an
// AI lookup), as one list. One instance, in App.jsx; the tray, the dialogs
// that show a job and the Settings panes that start one all get it.
//
// Polling is fast while a job runs or waits, or while something watches
// (the tray open, a dialog on a job — `watch()`), slow otherwise so work
// started elsewhere (another tab, a scheduled backup) still turns up; a
// hidden tab asks nothing and catches up when it is shown again.
//
// `onFinished(job, started)` hears each job that ends while this tab looks
// — `started` is what `start` / `upload` were given for a job started in
// this tab ({download, …}), else null. A finished job whose file was asked
// for with download: "auto" is downloaded here first.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtBytes, makeId } from "../shared/lib/utils";
import { t } from "../shared/i18n/i18n.js";
import { cancelJob, clearJobs, dismissJob, downloadJob, getJob, listJobs, startJob, uploadJob } from "./tasksApi";
import { addLocalRow, isActive, isFinished, localRow, mergeTasks, newlyFinished, trayBadge } from "./taskModel.js";
import { quietKinds } from "./taskKinds.js";
import { guideEvents } from "../guide/events.js";

const FAST_MS = 1500;
const SLOW_MS = 30000;

// Whether two listings carry the same jobs in the same state (plain JSON rows).
function sameListing(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i] && JSON.stringify(a[i]) !== JSON.stringify(b[i])) return false;
  }
  return true;
}

export function useTasks({ enabled, onFinished }) {
  const [jobs, setJobs] = useState([]);
  const [local, setLocal] = useState([]);
  const [watching, setWatching] = useState(0);
  const [wakeNonce, setWakeNonce] = useState(0);
  // Mirrors of the state, read synchronously: the last listing (what just
  // finished is measured against it) and the local rows.
  const jobsRef = useRef([]);
  const localRef = useRef([]);
  const listingSeq = useRef(0);
  const started = useRef(new Map());  // job id → what start/upload were given
  const notified = useRef(new Set()); // jobs onFinished already heard of
  const fetched = useRef(new Set());  // jobs whose file this tab downloaded (before the server hears of it)
  const stopped = useRef(new Set());  // local rows the person stopped
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  const wake = useCallback(() => setWakeNonce((n) => n + 1), []);

  const download = useCallback((job) => {
    downloadJob(job);
    fetched.current.add(job.id);
    const list = jobsRef.current.map((j) => (j.id === job.id ? { ...j, downloaded: true } : j));
    jobsRef.current = list;
    setJobs(list);
  }, []);

  // A job that ended: its file fetched when that was asked for, then
  // onFinished — once per job.
  const settle = useCallback((job) => {
    if (notified.current.has(job.id)) return;
    notified.current.add(job.id);
    const meta = started.current.get(job.id) || null;
    if (job.state === "done" && job.artifact && meta?.download === "auto") download(job);
    finishedRef.current?.(job, meta);
  }, [download]);

  // A new picture of the jobs: settle the ones that ended since the last.
  // (A file this tab fetched stays fetched, whatever a listing answered
  // before the server saw the download.)
  const take = useCallback((incoming) => {
    const list = incoming.map((j) => (!j.downloaded && fetched.current.has(j.id) ? { ...j, downloaded: true } : j));
    // A listing that reads like the last one changes nothing: the idle poll
    // every half minute would otherwise re-render the app for no reason.
    if (sameListing(jobsRef.current, list)) return;
    const ended = newlyFinished(jobsRef.current, list);
    jobsRef.current = list;
    setJobs(list);
    ended.forEach(settle);
  }, [settle]);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    const seq = ++listingSeq.current;
    try {
      const { jobs: list } = await listJobs();
      if (seq === listingSeq.current) take(list || []); // a slower, older answer never undoes a newer one
    } catch { /* the next round asks again */ }
  }, [enabled, take]);

  // A job the server just answered with (started, stopped): known at once,
  // before the next listing — a quick job may end before it.
  const know = useCallback((job) => {
    listingSeq.current += 1; // a listing asked before this answer is older than it
    const list = jobsRef.current;
    take(list.some((j) => j.id === job.id) ? list.map((j) => (j.id === job.id ? { ...j, ...job } : j)) : [job, ...list]);
    wake();
    return job;
  }, [take, wake]);

  const fast = jobs.some(isActive) || watching > 0;
  useEffect(() => {
    if (!enabled) {
      jobsRef.current = [];
      setJobs([]);
      return undefined;
    }
    const tick = () => { if (!document.hidden) refresh(); };
    tick();
    const timer = setInterval(tick, fast ? FAST_MS : SLOW_MS);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [enabled, fast, wakeNonce, refresh]);

  // --- this tab's own rows ---------------------------------------------------------
  const setLocalRows = useCallback((change) => {
    localRef.current = change(localRef.current);
    setLocal(localRef.current);
  }, []);
  const addLocal = useCallback((row) => {
    const id = makeId();
    setLocalRows((rows) => addLocalRow(rows, localRow(row, id)));
    return id;
  }, [setLocalRows]);
  // A late report of work the person stopped (an abort's error, a "done"
  // that raced the stop) never overwrites "cancelled".
  const updateLocal = useCallback((id, patch) => {
    if (patch.state && stopped.current.has(id)) return;
    setLocalRows((rows) => rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }, [setLocalRows]);
  // The same work starting over (a PDF downloaded again): its row runs
  // again. False when the row is gone (cleared), so the caller adds one.
  const reviveLocal = useCallback((id, patch) => {
    if (!localRef.current.some((row) => row.id === id)) return false;
    stopped.current.delete(id);
    setLocalRows((rows) => rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
    return true;
  }, [setLocalRows]);
  const removeLocal = useCallback((id) => setLocalRows((rows) => rows.filter((row) => row.id !== id)), [setLocalRows]);
  const stopLocal = useCallback((id) => {
    const row = localRef.current.find((r) => r.id === id);
    if (!row || !isActive(row)) return;
    stopped.current.add(id);
    try { row.cancel?.(); } catch { /* already over */ }
    setLocalRows((rows) => rows.map((r) => (r.id === id
      ? { ...r, state: "cancelled", info: t("stopped"), cancel: null, fraction: undefined } : r)));
  }, [setLocalRows]);
  const wasStopped = useCallback((id) => stopped.current.has(id), []);

  // --- the server's jobs -------------------------------------------------------------
  // A job the server answers as over already (quick work, or the same
  // work asked for again) is settled at once: no listing sees it run.
  const adopt = useCallback((job, meta) => {
    const fresh = !started.current.has(job.id);
    started.current.set(job.id, { download: "none", ...meta });
    if (fresh) guideEvents.emit("job.started", { kind: job.kind });
    know(job);
    if (fresh && isFinished(job)) settle(job);
    return job;
  }, [know, settle]);

  // `meta`: remembered for this tab — download: "auto" (fetch the file when
  // it is ready), "offer" (say it is ready) or "none", plus what the caller
  // wants back in onFinished.
  const start = useCallback(async (route, body, meta = {}) => adopt(await startJob(route, body), meta), [adopt]);

  // A job over a file this tab uploads (a restore's zip): the upload is a
  // row of its own until the server answers with the job.
  const upload = useCallback(async (route, form, { name = "", meta = {} } = {}) => {
    const ctl = new AbortController();
    const id = addLocal({ kind: "upload", name, info: t("uploading…"), cancel: () => ctl.abort() });
    let lastPct = -1;
    try {
      const job = await uploadJob(route, form, {
        signal: ctl.signal,
        onProgress: (loaded, total) => {
          const pct = Math.floor((loaded / total) * 100);
          if (pct === lastPct) return; // one re-render per visible step
          lastPct = pct;
          updateLocal(id, { info: `${fmtBytes(loaded)} / ${fmtBytes(total)}`, fraction: loaded / total });
        },
        onProcessing: () => updateLocal(id, { info: t("starting…"), fraction: undefined }),
      });
      removeLocal(id);
      return adopt(job, meta);
    } catch (err) {
      updateLocal(id, { state: "failed", info: String(err.message || t("failed")), cancel: null });
      throw err;
    }
  }, [addLocal, updateLocal, removeLocal, adopt]);

  const cancel = useCallback(async (task) => {
    if (task.local) { stopLocal(task.id); return; }
    know(await cancelJob(task.id));
  }, [stopLocal, know]);

  const dismiss = useCallback(async (task) => {
    if (task.local) { removeLocal(task.id); return; }
    await dismissJob(task.id);
    listingSeq.current += 1;
    take(jobsRef.current.filter((j) => j.id !== task.id));
  }, [removeLocal, take]);

  const clear = useCallback(async () => {
    setLocalRows((rows) => rows.filter(isActive));
    try { await clearJobs(); } finally { refresh(); }
  }, [setLocalRows, refresh]);

  // What this tab does once a job it started ends (a dialog closed while it
  // ran wants its file offered instead of fetched).
  const setDownload = useCallback((id, mode) => {
    const meta = started.current.get(id);
    if (meta) meta.download = mode;
  }, []);
  const startedHere = useCallback((id) => started.current.get(id) || null, []);

  // Fast polls while the caller is mounted: the tray open, a dialog on a job.
  const watch = useCallback(() => {
    setWatching((n) => n + 1);
    return () => setWatching((n) => n - 1);
  }, []);

  const tasks = useMemo(() => mergeTasks(jobs, local), [jobs, local]);
  return useMemo(() => ({
    tasks, jobs,
    badge: trayBadge(tasks, quietKinds),
    running: tasks.filter(isActive).length,
    byId: (id) => jobs.find((job) => job.id === id) || null,
    fetchJob: getJob,
    start, upload, cancel, dismiss, clear, download, setDownload, startedHere, watch, wake,
    addLocal, updateLocal, reviveLocal, removeLocal, wasStopped,
  }), [tasks, jobs, start, upload, cancel, dismiss, clear, download, setDownload, startedHere, watch, wake,
    addLocal, updateLocal, reviveLocal, removeLocal, wasStopped]);
}
