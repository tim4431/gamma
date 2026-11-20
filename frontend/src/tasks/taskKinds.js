// What each kind of task is in Background tasks (docs/dev/tasks.md): its
// icon, its title, the line under it, what can be done with it and where it
// opens. The server's kinds are gamma/jobs.py's, each started by its own
// POST /api/jobs/<route> — `retry` names the route and body that start the
// same work again; `stop` the route that stops it when that is not the
// job's cancel. The local kinds are this tab's own work. Pure but for the
// window's hidden time a recognition read here leaves out of its pace
// (`noteHidden`): tests/tasks.test.mjs.
import { fmtDate, t, tn } from "../shared/i18n/i18n.js";
import { fmtBytes } from "../shared/lib/format.js";
import { exportFormatOf } from "../transfers/transferFormats.js";
import * as ocrRuns from "../pdf/ocrRuns.js";
import { elapsed, fmtElapsed, isActive, remainingMs } from "./taskModel.js";

const named = (value) => value || t("Untitled");

// The spans this window was hidden, [from, to] (ms; `to` null while it
// is), the last HIDDEN_KEPT of them: a recognition read in this window (kind
// `ocr-tab`) stops while a phone or a tablet locks or shows another app, so
// its pace leaves that time out. In a browser the document's visibility
// tells them from the module's first load; `noteHidden` is what it calls.
const HIDDEN_KEPT = 64;
const hiddenSpans = [];
export function noteHidden(hidden, at = Date.now()) {
  const last = hiddenSpans.at(-1);
  if (hidden && (!last || last[1] !== null)) hiddenSpans.push([at, null]);
  else if (!hidden && last && last[1] === null) last[1] = at;
  if (hiddenSpans.length > HIDDEN_KEPT) hiddenSpans.shift();
}
if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
  if (document.visibilityState === "hidden") noteHidden(true);
  document.addEventListener("visibilitychange", () => noteHidden(document.visibilityState === "hidden"));
}
// How long this window was hidden between `since` and `now` (ms).
export function hiddenSince(since, now = Date.now()) {
  let ms = 0;
  for (const [from, to] of hiddenSpans) ms += Math.max(0, Math.min(to ?? now, now) - Math.max(from, since));
  return ms;
}

// A recognition's time left while it recognizes, at the pace of this run's
// pages (`since`, ms, when the phase began, at `from` pages done: a run goes
// on from the pages an earlier one recognized), `hidden` ms of it left out.
const ocrLeft = (p, now, hidden = 0) => (p?.phase === "recognizing" && Number.isFinite(p.since)
  ? fmtRemaining(remainingMs((p.done || 0) - (p.from || 0), (p.total || 0) - (p.from || 0), p.since + hidden, now)) : "");

// `icon`: a key of TasksTray.jsx's icon table. `open`: what a click on the
// row opens — "export" / "import" (the dialog, on this job), "settings:<pane>",
// "computer" (the page's chat on the computer side). `phase`: the words of
// a phase of its own, before the shared ones (PHASES). `left(task, now)`:
// how long it still takes, after its counts ("" while it cannot tell).
// `halt(task)`: what this tab ends at once when the job's Stop is pressed
// here (useTasks' cancel), before the job is stopped.
// `quiet`: a failure that does not dot the tray button (a paper without
// findable metadata is routine).
export const TASK_KINDS = {
  export: {
    icon: "export", open: "export",
    title: ({ params }) => t("Export “{name}” as {format}", {
      name: named(params.name), format: t(exportFormatOf(params.mode)?.label || params.mode || "") }),
    // An annotated PDF that wrote the viewer's translation (params name its
    // language) names its job: the server keeps the translation it was sent
    // with the job for as long as the job is listed.
    retry: ({ id, params }) => ["export", { page_id: params.page_id || "", folder: params.folder || "",
      mode: params.mode, pdf: params.pdf, highlights: params.highlights, notes: params.notes, comments: params.comments,
      notes_at: params.notes_at, sub_pages: Boolean(params.sub_pages), ...(params.translation ? { translation_of: id } : {}) }],
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
      if (params.source === "bucket") {
        return params.into === "new" ? t("Restore “{name}” from the bucket as a new workspace", args)
          : params.mode === "merge" ? t("Merge a copy from the bucket into “{name}”", args) : t("Restore “{name}” from the bucket", args);
      }
      if (params.source === "drive") {
        return params.into === "new" ? t("Restore “{name}” from Google Drive as a new workspace", args)
          : params.mode === "merge" ? t("Merge a copy from Google Drive into “{name}”", args) : t("Restore “{name}” from Google Drive", args);
      }
      return params.mode === "merge" ? t("Merge {file} into “{name}”", args) : t("Restore “{name}” from {file}", args);
    },
    // An uploaded zip is gone once its job ended; a stored snapshot, and a copy in the bucket or in Google Drive, are still there.
    retry: ({ params }) => (params.source === "snapshot"
      ? ["restore-snapshot", { ws: params.ws, name: params.snapshot, mode: params.mode }]
      : params.source === "bucket" || params.source === "drive"
        ? ["bucket-restore", { ws: params.ws, name: params.snapshot, store: params.source, mode: params.mode, into: params.into || "" }]
        : null),
  },
  // Pages copied into another workspace (the library's Copy to workspace),
  // or every page into a new one (Settings → Workspaces → Duplicate…;
  // transfers/workspaceCopy.js). A copy of every page starts again as it
  // was asked; one of picked pages is picked again in the library.
  "workspace-copy": {
    icon: "copy",
    title: ({ params }) => (params.new
      ? t("Duplicate “{name}” as “{copy}”", { name: named(params.name), copy: named(params.to_name) })
      : tn("Copy {n} page to “{name}”", "Copy {n} pages to “{name}”", params.pages || 0, { name: named(params.to_name) })),
    retry: ({ params }) => (params.all
      ? ["workspace-copy", { ws: params.ws, ...(params.new ? { name: params.to_name } : { to: params.to }) }] : null),
  },
  // A backup task's copy in the bucket or in Google Drive (`store`),
  // downloaded (Settings → Backups, admins).
  "bucket-download": {
    icon: "download", open: "settings:backups",
    title: ({ params }) => (params.store === "drive" ? t("Download of “{name}” from Google Drive", { name: named(params.name) })
      : t("Download of “{name}” from the bucket", { name: named(params.name) })),
    retry: ({ params }) => ["bucket-download", { ws: params.ws, name: params.snapshot, store: params.store || "bucket" }],
  },
  import: {
    icon: "import", open: "import",
    title: ({ params }) => t("Import {file}", { file: params.filename || t("a file") }),
  },
  // The import's review, read from the staged upload: its row opens the
  // import dialog on it. No retry: the upload goes when it fails.
  "import-preview": {
    icon: "import", open: "import",
    title: ({ params }) => t("Review {file}", { file: params.filename || t("a file") }),
  },
  indexing: {
    icon: "search",
    title: () => t("Indexing documents for search"),
  },
  // A recording's transcript (docs/dev/recording.md): the spoken words
  // written under the recording block, each paragraph at its moment.
  transcribe: {
    icon: "sparkles",
    title: ({ params }) => t("Transcribe “{name}”", { name: named(params.title) }),
    retry: ({ params }) => ["transcribe", { block_id: params.block_id }],
  },
  // A page's highlights found again in its PDF after the file was replaced
  // (docs/dev/highlight_relocation.md): started by the replacement itself
  // or by the Document menu's "Find highlights in this PDF".
  "relocate-highlights": {
    icon: "search",
    title: ({ params }) => t("Placing the highlights of “{name}”", { name: named(params.title) }),
    retry: ({ params }) => ["relocate-highlights", { page_id: params.page_id }],
  },
  // A stored PDF's scanned pages read from their pictures (the Document
  // menu's "Recognize text in this PDF", gamma/pdf_ocr.py), one run at a
  // time on the server: one waiting its turn reads so. While it recognizes,
  // the line says how long it still takes (`ocrLeft`). The server's own run
  // of a scan uploaded with no text (`params.auto`, gamma/ocr_auto.py) is the
  // workspace's, and says so; started again, it is a run someone asked for.
  ocr: {
    icon: "scanText",
    title: ({ params }) => (params.auto ? t("Recognizing the text of “{title}” automatically", { title: named(params.title) })
      : t("Recognizing the text of “{title}”", { title: named(params.title) })),
    retry: ({ params }) => ["ocr", { page_id: params.page_id }],
    left: ({ progress }, now) => ocrLeft(progress, now),
  },
  // The same pages read in the window that asked, where the server does not
  // read them itself (pdf/ocrRuns.js; the job judges and applies the words
  // the window sends): every device sees the row and can stop it. Its time
  // left in the window reading it leaves out the time that window was hidden
  // (`here`: this window runs it). Started again, it is read in the window
  // that asks, or on a server that now reads text itself
  // (useAppJobs.retryTask, pdf/ocrState.js recognizeAgain). Its Stop here
  // ends this window's run of it at once (`halt`, before the job's cancel),
  // not at the run's next request. A run waiting its turn here has a row of
  // this tab's own of this kind (pdf/ocrRuns.js), until its job's.
  "ocr-tab": {
    icon: "scanText",
    title: ({ params }) => t("Reading the text of “{title}” in a window", { title: named(params.title) }),
    retry: ({ params }) => ["ocr", { page_id: params.page_id, window: params.window }],
    left: ({ progress, params }, now, here = ocrRuns.running(params?.doc_id)) => (
      ocrLeft(progress, now, here && Number.isFinite(progress?.since) ? hiddenSince(progress.since, now) : 0)),
    halt: ({ id }) => ocrRuns.jobStopped(id),
  },
  // The recognized text of a stored PDF forgotten (the Document menu's
  // "Forget the recognized text…", gamma/pdf_ocr.py forget_recognized): no
  // stop (it updates from its start), no retry (its confirm starts it).
  "ocr-drop": {
    icon: "scanText",
    title: ({ params }) => t("Forgetting the recognized text of “{title}”", { title: named(params.title) }),
  },
  // Settings → Maintenance's batch lookup and the one after an upload of
  // several PDFs. A start while it runs adds papers to it, so the count is
  // its progress's. No retry: the pane is where the papers are picked again.
  metadata: {
    icon: "sparkles", open: "settings:maintenance",
    title: ({ params, progress }) => {
      const n = progress?.total || params.papers || 0;
      return tn("Metadata lookup for {n} paper", "Metadata lookup for {n} papers", n);
    },
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
  // A workspace or an account deleted (Settings → Workspaces, Users,
  // Server): its final copy, which a stop still undoes, then the delete.
  // No open: the Settings row shows it, and an admin's delete of a
  // workspace outside their list has no pane that would. No retry: a
  // delete goes through its confirm.
  "workspace-delete": {
    icon: "trash",
    title: ({ params }) => t("Delete “{name}”", { name: named(params.name) }),
  },
  "account-delete": {
    icon: "trash",
    title: ({ params }) => t("Delete the account “{username}”", { username: named(params.username) }),
  },
  "db-check": {
    icon: "database", open: "settings:server",
    title: () => t("Database check"),
    retry: () => ["db-check", {}],
  },
  // A paper the AI chat could not download, waiting for the user's own
  // browser (gamma/fetch_handoff.py). Read-only here: the chat's card
  // settles it, and a click opens the publisher's page.
  "paper-handoff": {
    icon: "cloudDownload", open: "handoff",
    title: ({ params }) => t("Get “{host}” in your browser", { host: named(params.host) }),
  },
  // A turn of a computer's agent in the chat's computer side, queued or
  // running on this computer (docs/dev/local_agents.md "The chat's computer
  // side"), read-only: a click opens the chat of its bucket
  // (`params.bucket`) on the turn's conversation (`turnShown`), and Stop is
  // the turn's own. The server's title is "<agent> · <page title>". A turn
  // of a work folder another computer keeps (step 4d) names that computer
  // (`params.computer`), and while it asks there alone (phase `asking`,
  // `there`) says the answer is given on that computer; one it relays is
  // waiting for you, answered in the chat. A co-author's turn with the reader's
  // agent names the co-author (`params.person`).
  "computer-turn": {
    icon: "terminal", open: "computer",
    title: ({ title, params }) => {
      const agent = params?.label || "";
      // the page's own title (params.title, "" for an untitled page), the library's for its bucket; the
      // row's title from an older server
      const page = params?.bucket === "home" ? t("Library") : typeof params?.title === "string" ? params.title : title;
      const computer = typeof params?.computer === "string" ? params.computer : "";
      if (typeof params?.person === "string") {
        const person = params.person || t("Someone");
        return computer ? t("{agent} on {computer}, for {person}: {title}", { agent, computer, person, title: named(page) })
          : t("{agent} on this computer, for {person}: {title}", { agent, person, title: named(page) });
      }
      return computer ? t("{agent} on {computer}: {title}", { agent, computer, title: named(page) })
        : t("{agent} on this computer: {title}", { agent, title: named(page) });
    },
    phase: ({ progress, params }) => (progress?.phase === "asking" && progress.there && typeof params?.computer === "string" && params.computer
      ? t("Waiting for your answer on {computer}", { computer: params.computer }) : ""),
    stop: ({ workspace, params }) => `/local-agents/chat/turns/${encodeURIComponent(params?.turn || "")}/stop${
      workspace ? `?ws=${encodeURIComponent(workspace)}` : ""}`,
  },
  // This tab's own work (App.jsx adds and updates these rows).
  download: { icon: "download" },
  upload: { icon: "upload" },
  // A folder, or many files, going into the library (transfers/uploadBatch.js):
  // one row for the batch, which opens its dialog again (useUploadBatches' open).
  uploads: { icon: "upload", open: "uploads" },
  file: { icon: "file" },
  ai: { icon: "sparkles", quiet: true },
  batch: { icon: "file" }, // the library's bulk actions over many pages (library/pageBatch.js)
};

// What a computer turn's row opens in its bucket's chat in place of the
// chat's own (chat/computer/chatSide.js showInChat): the conversation of
// its comment thread (`params.thread`), a co-author's chat
// (`params.person`, `params.conversation`), else null, the reader's own
// chat.
export function turnShown(task) {
  const params = task?.params || {};
  if (params.thread) return { thread: params.thread };
  return typeof params.person === "string" && params.conversation ? { conversation: params.conversation } : null;
}

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
  browser: t("Waiting for you in your browser"),
  connector: t("Gamma Connector is on it"),
  transcribing: t("Transcribing"),
  reading: t("Reading the PDF"),
  recognizing: t("Recognizing text"),
  updating: t("Updating search and highlights — can no longer be stopped"),
  matching: t("Finding each highlight's text"),
  keeping: t("Keeping a final copy"),
  deleting: t("Deleting — can no longer be stopped"),
  asking: t("Waiting for you"),
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
    case "documents": return tn("{done} of {total} document", "{done} of {total} documents", total, args);
    case "workspaces": return tn("{done} of {total} workspace", "{done} of {total} workspaces", total, args);
    case "highlights": return tn("{done} of {total} highlight", "{done} of {total} highlights", total, args);
    default: return t("{done} of {total}", args);
  }
}

// "about 3 minutes left", from taskModel's remainingMs; "" while it cannot tell.
export function fmtRemaining(ms) {
  if (ms === undefined) return "";
  if (ms < 60000) return t("less than a minute left");
  const minutes = Math.round(ms / 60000);
  if (minutes < 60) return tn("about {n} minute left", "about {n} minutes left", minutes);
  return tn("about {n} hour left", "about {n} hours left", Math.round(ms / 3600000));
}

// An upload's line as its bytes go, "12.0 MB / 40.0 MB · about 1 minute
// left", the time left from `since` (ms), when it started.
export function bytesInfo(loaded, total, since, now = Date.now()) {
  const left = Number.isFinite(since) ? fmtRemaining(remainingMs(loaded, total, since, now)) : "";
  return [total ? `${fmtBytes(loaded)} / ${fmtBytes(total)}` : fmtBytes(loaded), left].filter(Boolean).join(" · ");
}

// A tasks row's byte progress, for an upload's onProgress(loaded, total):
// `patch({info, fraction})` once per visible percent, since each patch
// re-renders the app.
export function byteProgress(patch, now = Date.now) {
  const since = now();
  let shown = null;
  return (loaded, total) => {
    const pct = total ? Math.floor((loaded / total) * 100) : loaded;
    if (pct === shown) return;
    shown = pct;
    patch({ info: bytesInfo(loaded, total, since, now()), fraction: total ? loaded / total : undefined });
  };
}

// What a running job is doing: its phase, how far it got, how long it still
// takes (for a kind that can tell), the item at hand.
export function progressText(task, now = Date.now()) {
  if (task.stopping) return t("Stopping…");
  const p = task.progress || {};
  const kind = kindOf(task);
  return [kind.phase?.(task) || PHASES[p.phase] || "", progressCounts(p), kind.left?.(task, now) || "", p.item || ""]
    .filter(Boolean).join(" · ") || t("Working…");
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

// What the export dialog offers beside Close once its job failed or was
// stopped: "retry", the same job again from its params. An annotated PDF
// that wrote the viewer's translation, while its page is the one open
// (`pageId`), gets "setup" instead, the dialog's setup again, where Export
// reads the viewer afresh (the translation may have changed since, or the
// PDF, which fails the kept one); with another page open the setup would
// export that one, so it is retried with the translation it was sent.
export function exportAgain(job, pageId) {
  const page = job?.params?.page_id || "";
  if (job?.params?.translation && page && !job.params.folder && page === pageId) return "setup";
  return retryOf(job) ? "retry" : "";
}

// Whether a relocate-highlights run was taken over by a newer one for the
// same page and another file, among `jobs`: the PDF was replaced again,
// which stops the run for the file before or has its write refused. That
// run's end is no news; the newer run reports.
export function relocationSuperseded(job, jobs) {
  const page = job?.params?.page_id;
  const since = Date.parse(job?.created_at || "") || 0;
  return !!page && jobs.some((other) => other.id !== job.id && other.kind === "relocate-highlights"
    && other.params?.page_id === page && other.params?.doc_id !== job.params?.doc_id
    && (Date.parse(other.created_at || "") || 0) > since);
}

// What the tab that started a relocate-highlights run says when it is done,
// from its result (docs/dev/highlight_relocation.md): how many moved (found
// exactly, in a new place), how many are drawn dashed to be checked (approx:
// matched roughly, or an area placed by estimate), how many stayed (found
// exactly where they were) and how many were not found (lost, drawn
// hollow). One someone moved while the run read the file is left as it is
// and counted apart (changed), so the numbers add up to the page's
// highlights; a dashed one says where to check it (its review card,
// pdf/HighlightReview.jsx, and for several the Document menu's Review), a
// hollow one how to fix it. A text file's run (format "text": the inline
// run's answer, or the job's result) words it for the file: a dashed range
// is checked from its menu, a lost one is a hollow tag at its old line.
export function relocationSummary(r) {
  if (r.format === "text") {
    return [
      t("Highlights placed in the file: {moved} moved, {approx} matched roughly, {kept} stayed in place, {lost} not found.",
        { moved: r.moved || 0, approx: r.approx || 0, kept: r.kept || 0, lost: r.lost || 0 }),
      r.approx ? t("A dashed highlight moved: right-click it to accept it or move it.") : "",
      r.changed ? tn("{n} that someone moved meanwhile was left as it is.",
        "{n} that someone moved meanwhile were left as they are.", r.changed) : "",
      r.lost ? t("A hollow tag marks a highlight whose text is gone: right-click it and choose Move to a selection or Use the closest match.") : "",
    ].filter(Boolean).join(" ");
  }
  return [
    t("Highlights placed in the PDF: {moved} moved, {approx} matched roughly or estimated, {kept} stayed in place, {lost} not found.",
      { moved: r.moved || 0, approx: r.approx || 0, kept: r.kept || 0, lost: r.lost || 0 }),
    r.approx ? tn("Hover the dashed highlight to compare and accept it.",
      "Hover a dashed highlight to compare and accept it, or review all {n} from the Document menu.", r.approx) : "",
    r.changed ? tn("{n} that someone moved meanwhile was left as it is.",
      "{n} that someone moved meanwhile were left as they are.", r.changed) : "",
    r.lost ? t("A hollow highlight's text is gone: right-click it and choose Move to a selection.") : "",
  ].filter(Boolean).join(" ");
}

// What the tab that started a recognition (kind `ocr`, or `ocr-tab` read in
// a window) says when it ends, from the job and, once done, its result
// (gamma/pdf_ocr.py run, window_run): the pages
// whose text it recognized and the highlights there that took the new text
// as their quote; or that the PDF's own text already reads well (its first
// scanned pages' words match a fresh recognition, `shared` of them), so it
// was kept, with the picture pages it recognized all the same; or that it
// has no scanned pages. A run stopped while it recognized says how far it
// got: what it recognized waits for the next run, which goes on from there.
// Stopped before (while it read the PDF's pages), it recognized nothing.
export function ocrSummary(job, result) {
  const title = named(job.params?.title);
  if (job.state === "cancelled") {
    const { phase, done, total } = job.progress || {};
    return phase === "recognizing" && total > 0
      ? t("Text recognition of “{title}” stopped after {done} of {total} pages; recognizing again goes on from there.", { title, done: done || 0, total })
      : t("Text recognition of “{title}” was stopped.", { title });
  }
  const r = result || {};
  const after = [
    r.highlights_respelled ? tn("{n} highlight took the new text.", "{n} highlights took the new text.", r.highlights_respelled) : "",
    r.failed ? tn("{n} page could not be read; recognizing again tries it once more.", "{n} pages could not be read; recognizing again tries them once more.", r.failed) : "",
  ];
  if (r.good_layer) {
    const share = Number(r.shared) || 0;
    return [
      t("“{title}”'s own text already reads well ({p}% of its words match a fresh recognition), so it was kept.",
        { title, p: Math.round(share <= 1 ? share * 100 : share) }),
      r.recognized ? tn("Text recognized on its {n} picture page.", "Text recognized on its {n} picture pages.", r.recognized) : "",
      ...after,
    ].filter(Boolean).join(" ");
  }
  if (!r.scanned) return t("“{title}” has no scanned pages, so its text was left as it is.", { title });
  return [
    tn("Text recognized on {n} page of “{title}”.", "Text recognized on {n} pages of “{title}”.", r.recognized || 0, { title }),
    ...after,
  ].filter(Boolean).join(" ");
}

// What a tab says when an OCR job ends (tasks/useAppJobs.js onJobFinished):
// the tab that started it (`started`) how it went, the summary of its
// result (`{result: summary(job, result)}`, once read) or a sentence
// (`{text}`). A run read in this window says nothing of a job it goes on
// from (`running`: the window still reads, and pdf/ocrRuns.js started the
// job again: its server restarted, failing it as interrupted, or its idle
// timer stopped it while this window was hidden, `progress.idle`), nor of
// one whose end it told already (`told`: it failed here, and stopped its
// job), unless the job applied after all. A tab that did not start it says
// only that the server's own run of a scan uploaded with no text is done,
// where it shows that page (`here`: the job's workspace is the tab's,
// `pageId` the page on screen). null: nothing to say.
export function ocrEnding(job, { started = false, here = false, pageId = "", running = false, told = false } = {}) {
  if (!started) {
    const shown = here && !!job.params?.page_id && job.params.page_id === pageId;
    return job.kind === "ocr" && job.params?.auto && job.state === "done" && shown ? { result: ocrAutoSummary } : null;
  }
  if (job.kind === "ocr-tab" && job.state !== "done" && (told || (running && (job.state === "failed" || job.progress?.idle)))) {
    return null;
  }
  if (job.state === "done") return { result: job.kind === "ocr-drop" ? ocrDropSummary : ocrSummary };
  if (job.state === "cancelled") return { text: job.kind === "ocr-drop" ? t("{name} was stopped.", { name: taskTitle(job) }) : ocrSummary(job) };
  return { text: t("{name} failed: {message}", { name: taskTitle(job), message: t(job.error || "") }) };
}

// What a tab showing the page says when the server's own run of a scan
// uploaded with no text (`params.auto`) is done, from its result: the pages
// whose text it recognized; "" when it recognized none.
export function ocrAutoSummary(job, result) {
  const n = Number(result?.recognized) || 0;
  return n ? tn("Text recognized automatically on {n} page of “{title}”.", "Text recognized automatically on {n} pages of “{title}”.", n,
    { title: named(job.params?.title) }) : "";
}

// What the tab that started a forget of a PDF's recognized text (kind
// `ocr-drop`) says when it is done, from its result (gamma/pdf_ocr.py
// forget_recognized): the pages that read the PDF's own text again, and
// their highlights: those that took that text as their quote, those that
// kept theirs because it does not read like the text under them (a quote
// changed by hand), and those on a page with no text of its own, which keep
// the recognized words.
export function ocrDropSummary(job, result) {
  const r = result || {};
  const pages = Array.isArray(r.pages) ? r.pages.length : Number(r.pages) || 0;
  return [
    tn("“{title}” reads its own text again on {n} page.", "“{title}” reads its own text again on {n} pages.", pages,
      { title: named(job.params?.title) }),
    r.highlights_respelled ? tn("{n} highlight took the PDF's own text as its quote.",
      "{n} highlights took the PDF's own text as their quote.", r.highlights_respelled) : "",
    r.highlights_kept ? tn("{n} highlight kept its quote, which does not read like the PDF's own text under it.",
      "{n} highlights kept their quotes, which do not read like the PDF's own text under them.", r.highlights_kept) : "",
    r.highlights_unread ? tn("{n} highlight on a page with no text of its own keeps the recognized words as its quote.",
      "{n} highlights on pages with no text of their own keep the recognized words as their quotes.", r.highlights_unread) : "",
  ].filter(Boolean).join(" ");
}

// A row with a window or a pane of its own: a job's, or this tab's folder
// upload (the other local kinds have none).
export const canOpen = (task) => Boolean(kindOf(task).open);

// The path (under /api) a job's Stop posts to when its kind has a stop of
// its own (a computer turn's, in the workspace it runs in), else "": the
// job's cancel.
export const stopPath = (task) => (!task.local && kindOf(task).stop?.(task)) || "";

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
