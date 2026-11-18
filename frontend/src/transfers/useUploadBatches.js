// The library's uploads of a folder or of several files as batches
// (docs/dev/import_export.md "Uploading a folder or many files"): the owner
// of the upload dialog's state and of each batch's run, one row of
// Background tasks (local kind "uploads") however many files, which the
// dialog follows and whose click opens the dialog again. App hands it how
// one file goes up and what a batch does to the library as it lands and
// once it ends; its rules are uploadBatch.js, node-tested. The batch is the
// tab's own and holds no `loading`: the menus stay free while it runs.
import { useCallback, useRef, useState } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { askFirst, batchFraction, batchLine, batchSummary, planUpload, runUploads, uploadRoot } from "./uploadBatch.js";

// How often a running batch has the library read again (`landed`), so the
// pages show as they land rather than all at the end.
const READ_MS = 10000;

// `inputs`, read as each step runs:
// - `tasks`: the Background tasks store (useTasks).
// - `quota`: GET /quota's answer, for the plan's room.
// - `place(base)`: the words for the destination folder `base`.
// - `makeFolders(paths, base)`: the folders the directories need, made once
//   → a lookup, names → folder id (App's ensureFolders).
// - `upload(item, folder, track)`: one file up → what it made (App's uploadOne).
// - `landed(pdfs)`: every READ_MS while files land, with the PDFs that came.
// - `ended({made, pdfs, one})`: the batch over — what it made, the PDFs not
//   yet handed to `landed`, and `one` ({made, filename}) when it made one
//   page alone in a dialog watching it, to open.
// - `say(text)`: the status line.
// → {dialog, start, confirm, retry, open, close}: `dialog` is null, {ask:
// batch} before a batch that asks first starts, or {rowId} on a batch's row.
export function useUploadBatches(inputs) {
  const live = useRef(inputs);
  live.current = inputs;
  const [dialog, setDialog] = useState(null);
  const shown = useRef(null);
  shown.current = dialog;
  const runs = useRef(new Map()); // row id → its batch, with the failed items once over (retry)

  // The batch as its row: the folders its directories need made first, then
  // the files POOL at a time (runUploads), each report patching the row —
  // its line, its bar and the state the dialog reads (`uploads`). Once
  // over: the closing line in the row, and on the status line when no
  // dialog shows the batch. → the row's id.
  const run = useCallback((batch) => {
    const { tasks } = live.current;
    const { items, base, besideWork, name, title, plan, destination } = batch;
    const about = { title, name, plan, destination };
    const first = { ...about, total: items.length, bytes: plan.bytes, sent: 0, done: 0, failed: [], current: [], since: Date.now(), stopped: false };
    const kept = { ...batch, stop: () => {}, failedItems: [] };
    const rowId = tasks.addLocal({ kind: "uploads", name: title, info: t("Making folders…"), fraction: 0, cancel: () => kept.stop(), uploads: first });
    // the batches of rows gone from the list are let go
    for (const id of runs.current.keys()) if (!tasks.tasks.some((task) => task.id === id)) runs.current.delete(id);
    runs.current.set(rowId, kept);
    const watched = () => shown.current?.rowId === rowId;
    (async () => {
      let folderOf;
      try {
        folderOf = await live.current.makeFolders(items.map((it) => it.path).filter((path) => path.length), base);
      } catch (err) {
        const error = t("Upload failed: {failed}", { failed: err.message });
        tasks.updateLocal(rowId, { state: "failed", info: error, cancel: null, uploads: { ...first, over: true, error } });
        if (!watched()) live.current.say(error);
        return;
      }
      const top = uploadRoot(items);
      const root = top ? folderOf([top]) : base;
      const fresh = []; // PDFs made since `landed` last heard
      let readAt = Date.now();
      let last = first;
      const ctl = runUploads(items, async (item, track) => {
        const made = await live.current.upload(item, folderOf(item.path), track);
        if (made.kind === "pdf") fresh.push(made);
        if (Date.now() - readAt >= READ_MS) {
          readAt = Date.now();
          live.current.landed(fresh.splice(0));
        }
        return made;
      }, {
        onChange: (s) => {
          last = { ...about, ...s, root };
          if (!tasks.wasStopped(rowId)) tasks.updateLocal(rowId, { info: batchLine(s), fraction: batchFraction(s), uploads: last });
        },
      });
      kept.stop = ctl.stop;
      if (tasks.wasStopped(rowId)) ctl.stop(); // stopped while the folders were made
      const { done, failed, stopped } = await ctl.finished;
      const halted = stopped || tasks.wasStopped(rowId);
      kept.failedItems = failed.map((f) => f.item);
      const counts = { total: items.length, done: done.length, failed: last.failed, stopped: halted };
      const line = batchSummary(counts);
      const over = { ...last, ...counts, root, current: [], over: true };
      // a stopped row is "cancelled" already: a patch with a state would be refused whole
      tasks.updateLocal(rowId, halted ? { info: line, uploads: over }
        : { state: failed.length ? "failed" : "done", info: line, cancel: null, fraction: 1, uploads: over });
      // A batch watched in its dialog that made one page and failed none
      // opens it, as a single upload does.
      const one = watched() && done.length === 1 && !failed.length && !halted;
      if (one) setDialog(null);
      else if (!watched()) live.current.say(batchSummary(counts, { first: true }));
      await live.current.ended({ made: done.map((d) => d.made), pdfs: fresh.splice(0),
        one: one ? { made: done[0].made, filename: done[0].item.filename } : null });
      if (besideWork && done.length && !failed.length) live.current.say(t("Uploaded to the library: a synced folder's files come from its directory."));
    })();
    return rowId;
  }, []);

  // A directory (`folder`, with what it left out, `skipped`) or several
  // files, into the folder `base`: a large batch asks first in the dialog;
  // a directory's shows its dialog from the start; files picked together
  // that need no question show only their row.
  const start = useCallback((items, { base, besideWork = false, folder = false, skipped = [] }) => {
    const { quota, place } = live.current;
    const name = folder ? uploadRoot(items) : "";
    const batch = {
      items, base, besideWork, name,
      title: name ? t("Upload “{name}”", { name }) : tn("Upload {n} file", "Upload {n} files", items.length),
      plan: planUpload(items, { skipped, quota }),
      destination: place(base),
    };
    if (askFirst(batch.plan)) { setDialog({ ask: batch }); return; }
    const rowId = run(batch);
    if (folder) setDialog({ rowId });
  }, [run]);

  // The dialog's Upload, on a batch that asked first.
  const confirm = useCallback(() => {
    const batch = shown.current?.ask;
    if (batch) setDialog({ rowId: run(batch) });
  }, [run]);

  // A batch's failed files sent again, as a batch of their own.
  const retry = useCallback((rowId) => {
    const kept = runs.current.get(rowId);
    if (!kept?.failedItems.length) return;
    const items = kept.failedItems;
    setDialog({ rowId: run({ ...kept, items, plan: planUpload(items, { quota: live.current.quota }) }) });
  }, [run]);

  const open = useCallback((rowId) => setDialog({ rowId }), []);
  const close = useCallback(() => setDialog(null), []);
  return { dialog, start, confirm, retry, open, close };
}
