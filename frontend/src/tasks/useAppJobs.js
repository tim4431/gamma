// App's side of the background tasks (docs/dev/tasks.md): starting the
// jobs its menus and dialogs ask for — workspace exports, backup restores,
// the export dialog's job, research — opening a tray row again, retrying
// one, and what to say when a job ends that no window shows. One instance,
// in App.jsx, over its `useTasks` store and the state the dialogs keep
// there; `onJobFinished` is what the store's `onFinished` calls.
import { useRef } from "react";
import { API, fmtBytes, getCurrentWorkspace, withWorkspace } from "../shared/lib/utils";
import { t } from "../shared/i18n/i18n.js";
import { exportJobBody } from "../transfers/transferFormats";
import { importSummary } from "../transfers/importReview";
import { kindOf, retryOf, taskTitle } from "./taskKinds.js";
import { isActive } from "./taskModel.js";

export function useAppJobs({
  tasks, postPill, setStatus, openBlock, setOpenPopover, setSettingsOpen,
  // the export dialog (App's state: its JSX shows it)
  exportOpen, setExportOpen, exportJobId, setExportJobId, exportFolder, setExportFolder,
  // the import review dialog
  importReview, setImportReview,
  // what an export is of, and the share view's synchronous downloads
  pageId, shareMode, exportRawPdf, downloadExport,
  // the open workspace: what a finished restore / import there refreshes
  workspaceId, refreshQuota, fetchHomeBlocks,
  // what a research job is started with
  folderFilter, chatModel, agentReadChars,
}) {
  const exportStartingRef = useRef(false); // a second press while the first start is answered starts nothing

  // Export a workspace as a backup zip, or every personal workspace at once
  // (`body`: {ws, uploads} or {all, uploads}): a background job
  // (workspace-export) whose zipping shows in the pill and Background
  // tasks, and whose file downloads by itself once it is ready — the
  // browser's download manager fetches it, however big.
  async function startWorkspaceExport(body) {
    try {
      await tasks.start("workspace-export", body, { download: "auto", pill: true });
    } catch (err) {
      setStatus(t("Export failed: {message}", { message: err.message }));
    }
  }

  // Restore (replace) or merge a backup zip into a workspace as a
  // background job (restore): the zip goes up as a row of its own with its
  // percent, then the server unzips and swaps or merges while the pill and
  // Background tasks show how far it got. Once it is done in the open
  // workspace this tab reloads (every piece of in-memory state is stale) —
  // into after.openPage when given (a shared page imported by link keeps
  // its block id, so it opens directly). `target`: another workspace of
  // mine (null = the open one), which is left alone here.
  function runBackupImport(f, mode, target, after = {}) {
    const into = target || getCurrentWorkspace();
    const form = new FormData();
    form.append("file", f);
    form.append("mode", mode);
    form.append("ws", into);
    postPill("backup-upload", { msg: t("Uploading {file}…", { file: f.name }), spinner: true });
    tasks.upload("restore", form, { name: `${mode === "merge" ? "Merge" : "Restore"} ${f.name}`.slice(0, 60),
      meta: { pill: true, restoreInto: into, after } })
      .catch((err) => { if (!err.aborted) setStatus(t("Import failed: {msg}", { msg: err.message || "failed" })); })
      .finally(() => postPill("backup-upload", null));
  }

  // Run what the export dialog was configured to do: a background job
  // (export) the dialog then follows — it may close meanwhile, and the
  // file downloads by itself while it watches. A PDF with both switches
  // off is the stored file itself, which the raw path serves without a
  // round trip (and works for PDFs that only exist behind the proxy). A
  // share view has no background tasks: it downloads the same export
  // through the download endpoints.
  async function runExport(o) {
    if (!exportFolder && !pageId) { setExportOpen(false); setStatus(t("Open a page first to export it.")); return; }
    if (!exportFolder && o.format === "pdf" && !o.highlights && !o.notes) { setExportOpen(false); await exportRawPdf(); return; }
    const body = exportJobBody(o, { pageId, folder: exportFolder || "" });
    if (shareMode) {
      setExportOpen(false);
      const query = new URLSearchParams({ mode: body.mode, pdf: body.pdf ? 1 : 0, highlights: body.highlights ? 1 : 0, notes: body.notes ? 1 : 0 });
      await downloadExport(exportFolder
        ? `/folders/${encodeURIComponent(exportFolder)}/export?${query}` : `/pages/${pageId}/export?${query}`, "export");
      return;
    }
    if (exportStartingRef.current) return;
    exportStartingRef.current = true;
    try {
      const job = await tasks.start("export", body, { download: "auto", dialog: "export" });
      setExportJobId(job.id);
    } catch (err) {
      setStatus(t("Export failed: {message}", { message: err.message }));
    } finally {
      exportStartingRef.current = false;
    }
  }
  // Closing the export dialog: a job still running goes on in Background
  // tasks, and its file is offered once ready instead of downloaded.
  function closeExport() {
    leaveExportJob();
    setExportOpen(false);
  }
  // The last step's breadcrumb walking back into the setup: the dialog stays
  // open on the format cards, and a job still running carries on in
  // Background tasks with its file offered there instead of downloaded here.
  function leaveExportJob() {
    const job = exportJobId ? tasks.byId(exportJobId) : null;
    if (job && isActive(job)) tasks.setDownload(job.id, "offer");
    setExportJobId(null);
  }

  // A row of Background tasks clicked: the export or import dialog on that
  // job again, or the Settings pane where the work is managed.
  function openTask(task) {
    const open = kindOf(task).open;
    setOpenPopover(null);
    if (open === "export") {
      if (isActive(task)) tasks.setDownload(task.id, "auto"); // watched again: fetch the file once ready
      setExportFolder(task.params?.folder || null);
      setExportJobId(task.id);
      setExportOpen(true);
    } else if (open === "import") {
      setImportReview({ jobId: task.id });
    } else if (open === "page") {
      // A finished research job: the report page it filed. The listing
      // leaves results out, so ask for this one job.
      if (isActive(task)) setStatus(t("Still working — its page appears when it finishes."));
      else {
        tasks.fetchJob(task.id)
          .then((full) => { if (full?.result?.page_id) openBlock(full.result.page_id); })
          .catch(() => {});
      }
    } else if (open === "handoff") {
      // A paper the chat could not download: its /go page leads on to the
      // publisher, and Gamma Connector knows the tab by that address.
      window.open(`${API}/ai/handoffs/${encodeURIComponent(task.params?.request || "")}/go`,
                  "_blank", "noopener");
    } else if (open?.startsWith("settings:")) {
      setSettingsOpen(open.slice("settings:".length));
    }
  }
  // Hand a question to the background researcher (gamma/paper_research.py):
  // it searches and reads for minutes and files a report page in the folder
  // being viewed. The tray follows it, and its row opens that page.
  async function startResearch(question) {
    if (!question) return;
    try {
      await tasks.start("research", { question, folder: folderFilter || "",
                                      model: chatModel || "", read_char_limit: agentReadChars || 0 },
                        { pill: true });
      setStatus(t("Researching in the background — Background tasks has it."));
    } catch (err) {
      setStatus(err.message || t("Could not start the research"));
    }
  }
  // Start a task's work again (a row's retry button, the export dialog's
  // Start again): the same route and body as the first time. `download`:
  // "auto" when the file should come as soon as it is ready (the dialog
  // watches, a workspace export was asked for from a menu), else offered.
  async function retryTask(task, download = task.kind === "workspace-export" ? "auto" : "offer") {
    const again = retryOf(task);
    if (!again) return null;
    try {
      return await tasks.start(again[0], again[1], { download, pill: task.kind === "workspace-export" });
    } catch (err) {
      setStatus(t("{name} failed: {message}", { name: taskTitle(task), message: err.message }));
      return null;
    }
  }

  // A job ended while this tab looked (tasks/useTasks.js onFinished):
  // `started` is what this tab started it with ({download, pill,
  // restoreInto, after}), null for a job started elsewhere. Whatever its
  // window shows is left to it; the rest is said here.
  function onJobFinished(job, started) {
    postPill(`job:${job.id}`, null);
    const title = taskTitle(job);
    // The export or import dialog shows it (one that ends as it starts
    // comes here before the dialog knows its id).
    const shownInDialog = (exportOpen && (job.id === exportJobId || (started?.dialog === "export" && !exportJobId)))
      || (importReview && (job.id === importReview.jobId || (started?.dialog === "import" && !importReview.jobId)));
    const here = job.workspace && job.workspace === workspaceId;
    if (job.kind === "restore") {
      if (job.state === "done" && here) {
        if (started) {
          // Every piece of in-memory state is stale now: start fresh (in the page a shared link named).
          const page = started.after?.openPage;
          window.location.href = withWorkspace(page ? `${window.location.pathname}?page=${encodeURIComponent(page)}` : window.location.pathname);
        } else {
          postPill(`job:${job.id}`, { msg: t("{name} finished — this workspace changed.", { name: title }),
            action: { label: t("Reload"), run: () => { window.location.href = withWorkspace(window.location.pathname); } } }, { after: [20000, null] });
        }
        return;
      }
      if (started) setStatus(job.state === "done" ? t("{name}: done.", { name: title }) : job.state === "failed"
        ? t("Import failed: {msg}", { msg: t(job.error) }) : t("{name} was stopped.", { name: title }));
      return;
    }
    if (job.kind === "import") {
      if (job.state === "done" && here) {
        refreshQuota?.();
        fetchHomeBlocks();
        if (!shownInDialog && started) {
          tasks.fetchJob(job.id).then((full) => full?.result && setStatus(t("Import: {summary}.", { summary: importSummary(full.result) }))).catch(() => {});
        }
      } else if (!shownInDialog && started && job.state === "failed") {
        setStatus(t("Import failed: {msg}", { msg: t(job.error) }));
      }
      return;
    }
    if (job.kind === "research") {
      if (job.state !== "done") {
        if (started && job.state === "failed") setStatus(t("Research failed: {msg}", { msg: t(job.error) }));
        return;
      }
      if (here) fetchHomeBlocks();
      // Its report is a page: offer to open it rather than open it over
      // whatever the user is reading now.
      tasks.fetchJob(job.id).then((full) => {
        const pageId = full?.result?.page_id;
        if (!pageId) return;
        postPill(`job:${job.id}`, { msg: t("Research finished: {name}.", { name: full.result.title || title }),
          action: { label: t("Open"), run: () => { openBlock(pageId); postPill(`job:${job.id}`, null); } } },
        { after: [60000, null] });
      }).catch(() => {});
      return;
    }
    if (!started || shownInDialog) return;
    if (job.state === "done" && job.artifact) {
      if (started.download === "auto") {
        setStatus(t("{name}: downloading {file} ({size}).", { name: title, file: job.artifact.name, size: fmtBytes(job.artifact.size) }));
      } else {
        postPill(`job:${job.id}`, { msg: t("{name} is ready ({size}).", { name: title, size: fmtBytes(job.artifact.size) }),
          action: { label: t("Download"), run: () => { tasks.download(job); postPill(`job:${job.id}`, null); } } }, { after: [30000, null] });
      }
    } else if (job.state === "done") {
      setStatus(t("{name}: done.", { name: title }));
    } else if (job.state === "failed") {
      setStatus(t("{name} failed: {message}", { name: title, message: t(job.error) }));
    }
  }

  return { startWorkspaceExport, runBackupImport, runExport, closeExport, leaveExportJob, openTask, retryTask, startResearch, onJobFinished };
}
