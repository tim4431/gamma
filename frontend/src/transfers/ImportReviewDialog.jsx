// The reviewed import (docs/dev/import_export.md): Upload → Review → Import
// → Summary. The upload and its review happen in this dialog; the import
// itself is a background job (POST /api/jobs/import, tasks/useTasks.js), so
// the dialog may close while it runs — the job's row in Background tasks
// opens it again: `jobId` without a file shows that job's import and its
// summary.
import React from "react";
import { Segmented, SubDialog } from "../settings/SettingsKit";
import { fmtBytes } from "../shared/lib/format.js";
import ImportTree from "./ImportTree";
import { dialogSource, discardImport, importFormat, importJobBody, requestImport } from "./importApi";
import { IMPORT_FILTERS, allItemIds, buildImportTree, filterImportPages, importSummary,
  importWarnings, itemSelected, resultPages, selectItems } from "./importReview";
import { TaskProgress } from "../tasks/TasksTray";
import { isActive } from "../tasks/taskModel.js";
import "./importReview.css";
import { T, t } from "../shared/i18n/i18n.js";

const STEPS = [T("Upload"), T("Review"), T("Import"), T("Summary")];
const PHASE_STEP = { upload: 0, scanning: 0, review: 1, importing: 2, ended: 2, complete: 3 };
const PHASE_LABEL = { upload: t("Uploading for review"), scanning: t("Checking files and library destinations") };

export default function ImportReviewDialog({ source, file, strip, folder = "", jobId: openedJob = null, tasks, onClose }) {
  const reopened = Boolean(openedJob);
  const [jobId, setJobId] = React.useState(openedJob);
  const job = jobId ? tasks.byId(jobId) : null;
  const params = job?.params || {};
  const format = importFormat(reopened ? dialogSource(params.source) : source,
    reopened ? { name: params.filename || "" } : file);
  const fileName = reopened ? params.filename || "" : file.name;
  const fileSize = reopened ? params.size : file.size;
  const [plan, setPlan] = React.useState(null);
  const [reviewPhase, setReviewPhase] = React.useState("upload");
  const [progress, setProgress] = React.useState({ loaded: 0, total: null });
  const [selected, setSelected] = React.useState(new Set());
  const [filter, setFilter] = React.useState("all");
  const [error, setError] = React.useState("");
  const [ended, setEnded] = React.useState(null); // the job once over, with its report
  const [attempt, setAttempt] = React.useState(0);
  const reviewId = React.useRef(null);
  const takenBy = React.useRef(openedJob); // the job that owns the staged upload: never discard it then
  const starting = React.useRef(false);
  const reportHeading = React.useRef(null);

  // Upload and preview (a review of a file just chosen).
  React.useEffect(() => {
    if (reopened) return undefined;
    const ctl = new AbortController();
    setError(""); setReviewPhase("upload");
    requestImport({ source, file, strip, folder }, {
      signal: ctl.signal, onProgress: update => {
        if (ctl.signal.aborted) return;
        setReviewPhase(update.phase === "processing" ? "scanning" : "upload");
        if (update.phase === "upload") setProgress(update);
      },
    }).then(data => {
      if (ctl.signal.aborted) return;
      reviewId.current = data.review_id;
      setPlan(data); setSelected(new Set(allItemIds(data.pages))); setReviewPhase("review");
    }).catch(err => {
      if (!ctl.signal.aborted) { setError(err.message); setReviewPhase("review"); }
    });
    return () => ctl.abort();
  }, [reopened, source, file, folder, strip, attempt]);
  // A review closed before it was imported goes with the dialog.
  React.useEffect(() => () => { if (!takenBy.current) discardImport(reviewId.current); }, []);

  const { watch, fetchJob } = tasks;
  React.useEffect(() => (jobId ? watch() : undefined), [jobId, watch]);
  const over = job && !isActive(job);
  React.useEffect(() => {
    if (!over) return undefined;
    let current = true;
    fetchJob(jobId).then(found => { if (current) setEnded(found); }).catch(err => { if (current) setError(err.message); });
    return () => { current = false; };
  }, [over, jobId, fetchJob]);

  const result = ended?.state === "done" ? ended.result : null;
  const phase = !jobId ? reviewPhase
    : !job || isActive(job) || (job.state === "done" && !ended) ? "importing"
      : job.state === "done" ? "complete" : "ended";
  React.useEffect(() => { if (phase === "complete") reportHeading.current?.focus(); }, [phase]);

  const pages = React.useMemo(() => result ? resultPages(result) : plan?.pages || [], [plan, result]);
  const shown = filterImportPages(pages, filter, selected);
  const chosenCount = pages.filter(page => itemSelected(page, selected)).length;
  const sourceTree = React.useMemo(() => buildImportTree(plan?.entries || []), [plan]);
  const destination = buildImportTree(shown, true);
  const warnings = importWarnings(result || plan || {});
  const reviewBusy = phase === "upload" || phase === "scanning";
  const uploading = phase === "upload";

  const submit = async () => {
    if (!plan || starting.current || !chosenCount) return;
    starting.current = true;
    setError("");
    try {
      const started = await tasks.start("import", importJobBody(plan.review_id, selected), { dialog: "import" });
      takenBy.current = started.id;
      setFilter("all");
      setJobId(started.id);
    } catch (err) {
      setError(err.message);
    } finally { starting.current = false; }
  };
  const percent = progress.total ? Math.min(100, Math.floor(progress.loaded / progress.total * 100)) : null;
  const title = result ? t("Import complete") : t("Review {label} import", { label: t(format.label) });
  const destinationLabel = `${t("Library")}${(plan?.folder || params.folder) ? ` / ${plan?.folder || params.folder}` : " / All pages"}`;
  const libraryColumn = (
    <section aria-label={t("Library after import")}><h3>{result ? t("Imported to library") : t("Library after import")}</h3>
      <p className="importDestination">{destinationLabel}</p>
      <div className="importTreeScroll">
        {shown.length ? <ImportTree node={destination} library selected={selected} complete={Boolean(result)}
          onSelect={result ? undefined : (ids, checked) => setSelected(previous => selectItems(previous, ids, checked))} />
          : <p className="importEmpty">{result ? t("No new items were added.") : t("No items match this filter.")}</p>}
      </div>
    </section>
  );
  const sourceColumn = (
    <section aria-label={t("ZIP contents")}><h3>{/\.zip$/i.test(fileName) ? t("Inside the ZIP") : t("Source file")}</h3>
      <div className="importTreeScroll"><ImportTree node={sourceTree} /></div>
    </section>
  );
  return <SubDialog title={title} onClose={onClose} className="importReviewModal">
    <nav className="importStepsNav" aria-label={t("Import progress")}>
      {STEPS.map((label, index) => <span key={label}
        aria-current={PHASE_STEP[phase] === index ? "step" : undefined}>{index + 1}. {t(label)}</span>)}
    </nav>
    <div className="importReviewHeader">
      <strong>{fileName}</strong>{fileSize != null ? <span className="importFileSize">{fmtBytes(fileSize)}</span> : null}
      {result ? <p ref={reportHeading} tabIndex={-1} role="status">{importSummary(result)}</p>
        : <p>{reviewBusy ? PHASE_LABEL[phase] : phase === "review" ? t("Choose what to import and review its destination.") : ""}</p>}
    </div>
    {error ? <p role="alert" className="importWarning">{error}</p> : null}
    {reviewBusy ? <div className="importUploadProgress" role="status">
      <div><strong>{PHASE_LABEL[phase]}{uploading && percent !== null ? `… ${percent}%` : "…"}</strong>
        {uploading ? <span>{fmtBytes(progress.loaded)}{progress.total ? ` / ${fmtBytes(progress.total)}` : ` / ${fmtBytes(file.size)}`}</span>
          : <span>{t("Upload complete. Reading the archive…")}</span>}
      </div>
      <progress aria-label={uploading ? t("Upload progress") : t("Import processing")} max={100} value={uploading ? percent ?? undefined : undefined} />
    </div> : null}
    {phase === "importing" ? <>
      <TaskProgress task={job} label={t("Import progress")} />
      <p className="reportModalHint">{t("You can close this window: the import goes on in Background tasks.")}</p>
    </> : null}
    {phase === "ended" ? (
      job?.state === "failed"
        ? <p role="alert" className="importWarning">{t("The import failed: {reason}. Choose the file again to try once more.", { reason: t(job.error) })}</p>
        : <p className="reportModalHint">{t("The import was stopped. What it imported before stays in your library.")}</p>
    ) : null}
    {phase === "review" && plan ? <>
      <div className="importSelectionToolbar">
        <Segmented value={filter} onChange={setFilter} options={IMPORT_FILTERS} />
        <div className="importSelectionActions">
          <button className="uiBtn sm" onClick={() => setSelected(new Set(allItemIds(pages)))}>{t("Select all")}</button>
          <button className="uiBtn sm" onClick={() => setSelected(new Set())}>{t("Deselect all")}</button>
        </div>
        <p>{t("{chosen} of {total} items selected · {shown} shown", { chosen: chosenCount, total: pages.length, shown: shown.length })}{filter !== "all" ? t(" · Hidden selections are kept") : ""}</p>
      </div>
      <div className="importReviewColumns">
        {sourceColumn}
        {libraryColumn}
      </div>
    </> : null}
    {phase === "complete" ? (plan
      ? <div className="importReviewColumns">
        {sourceColumn}
        {libraryColumn}
      </div>
      : <div className="importReviewColumns single">{libraryColumn}</div>) : null}
    {(phase === "review" && plan) || phase === "complete" ? (
      <details className="importWarnings" open={warnings.length > 0}>
        <summary>{warnings.length ? t("{n} warnings — review missing or omitted content", { n: warnings.length }) : t("No import warnings")}</summary>
        {warnings.length ? <ul>{warnings.map((warning, i) => <li key={i}><strong>{warning.title}</strong><span>{warning.reason}</span></li>)}</ul> : null}
      </details>
    ) : null}
    {phase === "review" && plan ? <p className="reportModalHint">{t(format.instructions)} {source === "zotero" && strip ? t("Embedded annotations will be imported and stripped from the stored PDFs.") : ""}</p> : null}
    <div className="reportModalBtns importReviewFooter">
      {phase === "upload" || phase === "scanning" || phase === "review"
        ? <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button> : null}
      {error && !plan && phase === "review" ? <button type="button" className="uiBtn" onClick={() => setAttempt(n => n + 1)}>{t("Retry preview")}</button> : null}
      {phase === "upload" || phase === "scanning" || phase === "review" ? (
        <button type="button" className="uiBtn primary" disabled={!plan || reviewBusy || !chosenCount} onClick={submit}>{t("Import to library")}</button>
      ) : null}
      {phase === "importing" && job?.stoppable ? <button type="button" className="uiBtn" onClick={() => tasks.cancel(job).catch(err => setError(err.message))}>{t("Stop")}</button> : null}
      {phase === "importing" || phase === "ended" ? <button type="button" className="uiBtn primary" onClick={onClose}>{t("Close")}</button> : null}
      {phase === "complete" ? <button type="button" className="uiBtn primary" onClick={onClose}>{t("Done")}</button> : null}
    </div>
  </SubDialog>;
}
