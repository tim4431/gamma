// A batch of files going into the library (docs/dev/import_export.md
// "Uploading a folder"): a picked or dropped directory, or several files at
// once. `planUpload` says what it holds and `askFirst` whether it is large
// enough to ask before it starts (UploadDialog shows the plan);
// `runUploads` runs it, POOL files at a time, each file's bytes counted
// into the batch's, and its Stop aborts the files on their way and starts
// no more. The batch lives in the tab: a reload or a closed tab ends it
// part way, keeping what already went. tests/uploadBatch.test.mjs.
import { t, tn } from "../shared/i18n/i18n.js";
import { fmtBytes } from "../shared/lib/format.js";
import { fmtRemaining, progressCounts } from "../tasks/taskKinds.js";
import { remainingMs } from "../tasks/taskModel.js";
import { ASK_PAST_FILES, hiddenName } from "./fileRouting.js";

// Past this many bytes a batch asks first as well: it takes a while, and
// it lasts only as long as the tab.
export const ASK_PAST_BYTES = 500 * 1024 * 1024;
export const POOL = 3;
const MB = 1024 * 1024;

// An item of the batch ({file, filename, path, route}, App's uploadFiles)
// by its path, "thesis/figures/setup.gif".
export const itemLabel = (item) => [...(item.path || []), item.filename].filter(Boolean).join("/");

// What the batch holds, for its dialog: how many files of each route and
// their bytes, how many folders their directories make, what the drop left
// out (`skipped`: paths, a directory never read ending in "/"), and `left`,
// the room the storage has when the stored files take more (else null):
// `quota` is GET /quota's {quota_mb, used_bytes}, quota_mb 0 for none. A
// note is no stored file, and a file the library has already is not stored
// twice, so `left` may warn of more than will fail.
export function planUpload(items, { skipped = [], quota = null } = {}) {
  const kinds = { pdf: 0, markdown: 0, file: 0 };
  const dirs = new Set();
  let bytes = 0, stored = 0;
  for (const item of items) {
    const size = item.file?.size || 0;
    kinds[item.route] = (kinds[item.route] || 0) + 1;
    bytes += size;
    if (item.route !== "markdown") stored += size;
    for (let i = 1; i <= item.path.length; i++) dirs.add(item.path.slice(0, i).join("\n"));
  }
  const room = quota?.quota_mb > 0 ? Math.max(0, quota.quota_mb * MB - (quota.used_bytes || 0)) : null;
  return { files: items.length, bytes, stored, kinds, folders: dirs.size, skipped,
    left: room !== null && stored > room ? room : null };
}

// Whether a batch asks before it starts: past ASK_PAST_FILES pages (each
// file is one, and a hosted plan counts them), past ASK_PAST_BYTES, or
// more than the storage has room for.
export const askFirst = (plan) => plan.files > ASK_PAST_FILES || plan.bytes > ASK_PAST_BYTES || plan.left !== null;

// The directory every item is in — a picked or dropped folder's own — or ""
// when there is none (loose files, or two folders dropped together).
export function uploadRoot(items) {
  const first = items[0]?.path?.[0] || "";
  return first && items.every((item) => item.path[0] === first) ? first : "";
}

// What the drop left out, as the dialog lists it: a hidden directory once,
// ending in "/", with how many of its files it held ("thesis/.git/", 4812)
// or `files` null when it was never read; every other path alone, `files`
// null (a temporary or an unstorable file, a dot file). Most files first,
// then by name.
export function skippedGroups(paths) {
  const groups = new Map();
  for (const path of paths) {
    const parts = path.split("/");
    const at = parts.findIndex((part, i) => i < parts.length - 1 && hiddenName(part));
    if (path.endsWith("/") || at < 0) { if (!groups.has(path)) groups.set(path, null); continue; }
    const key = `${parts.slice(0, at + 1).join("/")}/`;
    groups.set(key, (groups.get(key) || 0) + 1);
  }
  return [...groups].map(([name, files]) => ({ name, files }))
    .sort((a, b) => (b.files || 0) - (a.files || 0) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

// Runs `run(item, track)` over the items, POOL at a time, the next only
// while the batch is not stopped. `run` resolves what the item made and
// reports to `track` its bytes (`progress(loaded, total)`) and how to abort
// it (`abortable(abort)`). → {stop, finished}: `stop()` aborts what is on
// its way and starts nothing more; `finished` resolves {done: [{item,
// made}], failed: [{item, error}], unsent, stopped}, `unsent` the items
// aborted or never started. `onChange(state)` hears the batch's state as
// files start and end, and as bytes go once per visible percent:
// {total, bytes, sent, done, failed: [{name, error}], current: [names],
// since, stopped}, `failed` a list the run keeps adding to.
export function runUploads(items, run, { pool = POOL, onChange = () => {}, now = Date.now } = {}) {
  const total = items.length;
  const bytes = items.reduce((n, item) => n + (item.file?.size || 0), 0);
  const since = now();
  const done = [], failed = [], failedRows = [], unsent = [];
  const live = new Map(); // item → its bytes sent so far
  const aborts = new Set();
  let through = 0, next = 0, stopped = false, shown = null;
  const state = () => {
    let sent = through;
    for (const n of live.values()) sent += n;
    return { total, bytes, sent: Math.min(sent, bytes), done: done.length, failed: failedRows,
      current: [...live.keys()].map(itemLabel), since, stopped };
  };
  const emit = (force) => {
    const s = state();
    const pct = s.bytes ? Math.floor((s.sent / s.bytes) * 100) : s.done + s.failed.length;
    if (!force && pct === shown) return;
    shown = pct;
    onChange(s);
  };
  const worker = async () => {
    while (!stopped && next < total) {
      const item = items[next++];
      const size = item.file?.size || 0;
      let abort = null;
      live.set(item, 0);
      emit(true);
      const track = {
        progress: (loaded, all) => {
          if (!live.has(item)) return;
          live.set(item, all ? Math.min(size, (size * loaded) / all) : Math.min(size, loaded));
          emit(false);
        },
        abortable: (fn) => {
          if (abort) aborts.delete(abort);
          abort = fn;
          aborts.add(fn);
          if (stopped) fn();
        },
      };
      try {
        done.push({ item, made: await run(item, track) });
      } catch (err) {
        if (err?.aborted) unsent.push(item);
        else {
          const error = String(err?.message || err || "failed");
          failed.push({ item, error });
          failedRows.push({ name: itemLabel(item), error });
        }
      }
      if (abort) aborts.delete(abort);
      live.delete(item);
      through += size;
      emit(true);
    }
  };
  const finished = Promise.all(Array.from({ length: Math.min(pool, total) }, worker)).then(() => {
    unsent.push(...items.slice(next));
    return { done, failed, unsent, stopped };
  });
  const stop = () => {
    if (stopped) return;
    stopped = true;
    for (const fn of aborts) { try { fn(); } catch { /* already over */ } }
    emit(true);
  };
  return { stop, finished };
}

// The batch's line in its row and its dialog: "12 of 340 files · 120.0 MB
// of 1.2 GB · about 3 minutes left".
export function batchLine(s, now = Date.now()) {
  const counted = progressCounts({ done: s.done + s.failed.length, total: s.total, unit: "files" });
  const sent = s.bytes ? t("{done} of {total}", { done: fmtBytes(s.sent), total: fmtBytes(s.bytes) }) : "";
  const left = s.stopped ? "" : fmtRemaining(remainingMs(s.sent, s.bytes, s.since, now));
  return [counted, sent, left].filter(Boolean).join(" · ");
}

// How far the batch got, 0..1: by its bytes, by its files when it has none.
export const batchFraction = (s) => (s.bytes ? s.sent / s.bytes
  : s.total ? (s.done + s.failed.length) / s.total : 0);

// What the batch says once it is over ({done, failed, stopped}, the
// counts), and with `first` the first failure, for the status line when
// no dialog shows the list.
export function batchSummary({ total, done, failed, stopped }, { first = false } = {}) {
  const went = stopped
    ? tn("Stopped after {done} of {n} file: what went up stays in the library.",
      "Stopped after {done} of {n} files: what went up stays in the library.", total, { done })
    : done === total ? tn("Uploaded {n} file.", "Uploaded {n} files.", total)
      : tn("Uploaded {done} of {n} file.", "Uploaded {done} of {n} files.", total, { done });
  if (!failed.length) return went;
  const why = first
    ? tn("{n} could not be uploaded: {name} ({error}).", "{n} could not be uploaded, among them {name} ({error}).",
      failed.length, { name: failed[0].name, error: failed[0].error })
    : tn("{n} file could not be uploaded.", "{n} files could not be uploaded.", failed.length);
  return `${went} ${why}`;
}

// The page an upload made (App's uploadOne: a PDF's block, a document's
// page, a note's import answer), or "".
export const uploadedPage = (made) => made?.block?.id || made?.page?.id || made?.data?.block_id || "";
