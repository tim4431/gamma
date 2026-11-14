// The reviewed import (docs/dev/import_export.md): Upload → Review → Import
// → Summary. The upload happens in this dialog; the review is read by a
// background job (POST /api/jobs/import-preview) and the import is another
// (POST /api/jobs/import, tasks/useTasks.js), so the dialog may close while
// either runs — the job's row in Background tasks opens it again:
// `previewJobId` without a file shows that review once it is read
// (`previewJob`, the job as the row or pill had it, stands in once no
// listing carries it), `jobId` that job's import and its summary. A close
// would lose the upload while it goes up, and the review once it shows, so
// the backdrop, Escape and × ask first then (SubDialog `guard`); Cancel
// stays direct and drops the review, also one still being read. While the
// dialog shows a review it keeps it on the server (keepReview); a review
// gone anyway (404: swept after a sleep past its two hours) leaves an
// error with Retry preview, which uploads the held file again and keeps
// the choices; so does a review a server restart cut short.
import React from "react";
import { FormNotice, Segmented, SubDialog } from "../settings/SettingsKit";
import { fmtBytes } from "../shared/lib/format.js";
import ImportTree from "./ImportTree";
import { dialogSource, discardImport, fetchReview, importFormat, importJobBody, keepReview, requestImport } from "./importApi";
import { IMPORT_FILTERS, allItemIds, buildImportTree, filterImportPages, firstRow, importLinks, importSummary,
  importWarnings, itemSelected, orderLikeSource, pageKey, resultPages, reviewRefused, selectItems } from "./importReview";
import { TaskProgress } from "../tasks/TasksTray";
import { isActive } from "../tasks/taskModel.js";
import "./importReview.css";
import { T, t, tn } from "../shared/i18n/i18n.js";

const STEPS = [T("Upload"), T("Review"), T("Import"), T("Summary")];
const PHASE_STEP = { packing: 0, upload: 0, scanning: 0, review: 1, importing: 2, ended: 2, complete: 3 };
const PHASE_LABEL = { packing: t("Reading the folder"), upload: t("Uploading for review"), scanning: t("Checking files and library destinations") };
// The answers that refuse the file itself (400: nothing this source reads,
// such as a Supernote .note; 413: too large; 415: a type it does not take):
// sending it again cannot help, so the dialog offers no retry.
const REFUSED = [400, 413, 415];
// The server holds a review two hours since its last activity: the dialog
// says it is still open well within that, and when the window comes back.
const KEEP_ALIVE_MS = 10 * 60 * 1000;
const GONE = [404, 410]; // the staged review is no longer on the server
const NONE = new Set();

// `folder`: the destination folder's id ("" = the library root);
// `folderPath(id)` names it, for a review opened again from its job too.
export default function ImportReviewDialog({ source, file, strip, folder = "", folderPath, jobId: openedJob = null,
  previewJobId: openedPreview = null, previewJob: openedPreviewJob = null, tasks, onClose }) {
  const reopened = Boolean(openedJob || openedPreview);
  const [jobId, setJobId] = React.useState(openedJob);
  const [previewId, setPreviewId] = React.useState(openedPreview); // the job reading the review
  const job = jobId ? tasks.byId(jobId) : null;
  // The review's job as the listing has it; once none does (its row
  // cleared, or dismissed by its import from another tab), as the server
  // answers it (readPreview), else as it was last seen.
  const listedPreview = previewId ? tasks.byId(previewId) : null;
  const [readPreview, setReadPreview] = React.useState(null);
  const seenPreview = React.useRef(openedPreviewJob);
  if (listedPreview) seenPreview.current = listedPreview;
  const preview = listedPreview || [readPreview, seenPreview.current].find(known => known?.id === previewId) || null;
  const params = job?.params || preview?.params || {};
  const sourceKey = reopened ? dialogSource(params.source) : source;
  const stripping = reopened ? Boolean(params.strip) : strip;
  const format = importFormat(sourceKey, reopened ? { name: params.filename || "" } : file);
  const fileName = reopened ? params.filename || "" : file.name;
  const fileSize = reopened ? params.size : file.size;
  const [plan, setPlan] = React.useState(null);
  const [reviewPhase, setReviewPhase] = React.useState(openedPreview ? "scanning" : "upload");
  const [progress, setProgress] = React.useState({ loaded: 0, total: null });
  const [selected, setSelected] = React.useState(new Set());
  const [filter, setFilter] = React.useState("all");
  // The row whose match the columns show: the column clicked, the row's
  // key and name there, and the choice's number (each choice shows its
  // match anew).
  const [link, setLink] = React.useState(null);
  const picks = React.useRef(0);
  const [error, setError] = React.useState("");
  const [refused, setRefused] = React.useState(false); // the preview's error is the server's refusal
  const [ended, setEnded] = React.useState(null); // the job once over, with its report
  const [attempt, setAttempt] = React.useState(0);
  const reviewId = React.useRef(null);
  const takenBy = React.useRef(openedJob); // the job that owns the staged upload: never discard it then
  const starting = React.useRef(false);
  const closed = React.useRef(false);
  const reading = React.useRef(false); // the review's job still reads it: a close leaves it to the job
  const dropping = React.useRef(false); // Cancel: the review goes, also one still being read
  const keepChoices = React.useRef(false); // the next review keeps the selection (the last one is gone)
  const reportHeading = React.useRef(null);

  const { watch, fetchJob, adopt } = tasks;
  // Upload a file just chosen; its review is read by a job, followed below.
  React.useEffect(() => {
    if (reopened) return undefined;
    const ctl = new AbortController();
    setError(""); setRefused(false); setReviewPhase("upload"); setPreviewId(null);
    requestImport({ source, file, strip, folder }, {
      signal: ctl.signal, onProgress: update => {
        if (ctl.signal.aborted) return;
        setReviewPhase(update.phase === "processing" ? "scanning" : update.phase);
        if (update.phase !== "processing") setProgress(update);
      },
    }).then(started => {
      if (ctl.signal.aborted) return;
      reviewId.current = started.params?.review_id || null;
      adopt(started, { dialog: "import" });
      setReviewPhase("scanning");
      setPreviewId(started.id);
    }).catch(err => {
      if (!ctl.signal.aborted) { setError(err.message); setRefused(REFUSED.includes(err.status)); setReviewPhase("review"); }
    });
    return () => ctl.abort();
  }, [reopened, source, file, folder, strip, attempt, adopt]);
  // The review's job: followed closely while it reads, then its review is
  // read from the server (a job ended before any listing saw it too).
  const previewLive = Boolean(preview && isActive(preview));
  const previewState = preview?.state || "";
  const previewReview = preview?.params?.review_id || "";
  const previewError = preview?.error || "";
  reading.current = previewLive;
  React.useEffect(() => (previewLive ? watch() : undefined), [previewLive, watch]);
  const readFor = React.useRef(null); // the review job whose ending was taken (once each)
  // A job no listing carries is asked for by its id until its end is taken
  // (an import's start dismisses it, and nothing needs it then).
  const unlisted = Boolean(previewId) && !listedPreview && readFor.current !== previewId;
  React.useEffect(() => {
    if (!unlisted) return undefined;
    let current = true;
    setReadPreview(null);
    fetchJob(previewId).then(found => { if (current) setReadPreview(found); }).catch(err => {
      if (!current) return;
      // Gone from the server, so ended: its review is read if it is still kept.
      if (err.status === 404) setReadPreview({ ...seenPreview.current, id: previewId, state: "done" });
      else { setError(err.message); setReviewPhase("review"); }
    });
    return () => { current = false; };
  }, [unlisted, previewId, fetchJob]);
  // The review its job reads is the dialog's from the start, so Cancel drops
  // it before it is read too (a dialog opened on the job's row).
  const heldFor = React.useRef(null);
  React.useEffect(() => {
    if (!previewReview || heldFor.current === previewId) return;
    heldFor.current = previewId; reviewId.current = previewReview;
  }, [previewId, previewReview]);
  React.useEffect(() => {
    if (!previewId || !previewState || isActive({ state: previewState }) || readFor.current === previewId) return;
    readFor.current = previewId;
    if (previewState !== "done") {
      // The server read the file and said no, or the review was stopped:
      // sending it again would not help, and its end dropped the upload. A
      // restart that cut the review short left the upload, which a close or
      // Retry preview drops (a bug's end dropped it: its DELETE finds none).
      const retry = previewState === "failed" && !reviewRefused(previewError);
      if (!retry && reviewId.current === previewReview) reviewId.current = null;
      setError(previewState === "failed" ? t("The review failed: {reason}", { reason: t(previewError) })
        : t("The review was stopped. Choose the file again to import it."));
      setRefused(!retry); setReviewPhase("review");
      return;
    }
    const read = previewId;
    fetchReview(previewReview).then(data => {
      if (closed.current || readFor.current !== read) return; // closed, or another review since
      reviewId.current = data.review_id;
      const ids = allItemIds(data.pages), keep = keepChoices.current;
      keepChoices.current = false;
      setPlan(data); setReviewPhase("review"); setLink(null);
      setSelected(previous => new Set(keep ? ids.filter(id => previous.has(id)) : ids));
    }).catch(err => {
      if (closed.current || readFor.current !== read) return;
      if (GONE.includes(err.status)) { reviewId.current = previewReview; gone(previewReview); }
      else { setError(err.message); setReviewPhase("review"); }
    });
  }, [previewId, previewState, previewReview, previewError]);
  // A review closed before it was imported goes with the dialog, unless its
  // job still reads it (its row opens it again) and Cancel did not drop it;
  // one an import is being started on stays for it (that start discards it
  // if it fails). The discard waits a microtask, by which StrictMode has
  // mounted the dialog again (closed is false then) and keeps the review.
  React.useEffect(() => {
    closed.current = false; // mounted again (StrictMode)
    return () => {
      closed.current = true;
      queueMicrotask(() => {
        if (closed.current && !takenBy.current && !starting.current && (dropping.current || !reading.current)) discardImport(reviewId.current);
      });
    };
  }, []);
  // The review is gone from the server: the error offers Retry preview.
  const gone = id => {
    if (takenBy.current || reviewId.current !== id) return; // a job's now, or an older review
    keepChoices.current = Boolean(file); reviewId.current = null;
    setPlan(null); setReviewPhase("review");
    setError(file ? t("The server no longer holds this review. Retry preview uploads {name} again and keeps your choices.", { name: fileName })
      : t("The server no longer holds this review. Choose {name} again to import it.", { name: fileName }));
  };

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
  // The review on screen is kept: every KEEP_ALIVE_MS, which a hidden tab
  // still runs, and when the window comes back (a sleep stops the timer).
  // While an import starts, its own answer says whether the review is gone.
  const shownReview = phase === "review" && plan ? plan.review_id : null;
  React.useEffect(() => {
    if (!shownReview) return undefined;
    const ping = () => keepReview(shownReview).catch(err => { if (GONE.includes(err.status) && !starting.current) gone(shownReview); });
    const onVisible = () => { if (!document.hidden) ping(); };
    const timer = setInterval(ping, KEEP_ALIVE_MS);
    window.addEventListener("focus", ping);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", ping);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [shownReview]);

  const pages = React.useMemo(() => result ? resultPages(result) : plan?.pages || [], [plan, result]);
  const sourceTree = React.useMemo(() => buildImportTree(plan?.entries || []), [plan]);
  // The library column lists its pages in the source column's order, and
  // each side's rows find their match on the other (importLinks).
  const ordered = React.useMemo(() => orderLikeSource(pages, sourceTree), [pages, sourceTree]);
  const links = React.useMemo(() => importLinks(plan?.entries || [], pages), [plan, pages]);
  const shown = filterImportPages(ordered, filter, selected);
  const chosenCount = pages.filter(page => itemSelected(page, selected)).length;
  const destination = buildImportTree(shown, true, result ? null : selected, ordered);
  const matches = link ? (link.side === "source" ? links.toPages : links.toFiles).get(link.key) : null;
  const pick = (side, key, name) => {
    if (link?.side === side && link.key === key) { setLink(null); return; }
    picks.current += 1;
    setLink({ side, key, name, n: picks.current });
    if (side === "source" && filter !== "all") { // a match the filter hides brings back All
      const visible = new Set(shown.map(pageKey));
      if ([...links.toPages.get(key)].some(match => !visible.has(match))) setFilter("all");
    }
  };
  const columnLink = (side, tree, rowKey, toOther) => {
    const own = Boolean(matches) && link.side === side;
    const at = matches && !own ? firstRow(tree, matches, rowKey) : null;
    return { rowKey, linked: key => toOther.has(key), lit: !matches ? NONE : own ? new Set([link.key]) : matches,
      pressed: own ? link.key : null, reveal: at && { ...at, n: link.n }, onPick: (key, name) => pick(side, key, name) };
  };
  // Read out on each choice: it names the row, so the next one, with as
  // many matches, is not the same sentence.
  const matchStatus = !matches ? "" : link.side === "source"
    ? tn("{name}: {n} matching page in the library", "{name}: {n} matching pages in the library", matches.size, { name: link.name })
    : tn("{name}: {n} matching file in the upload", "{name}: {n} matching files in the upload", matches.size, { name: link.name });
  const warnings = importWarnings(result || plan || {});
  const reviewBusy = phase === "packing" || phase === "upload" || phase === "scanning";
  const previewing = phase === "scanning" && Boolean(preview); // the review's job is known: it shows its progress
  const phaseLabel = phase === "packing" && file?.picked ? t("Reading the files") : PHASE_LABEL[phase];
  const uploading = phase === "packing" || phase === "upload"; // bytes counted

  const submit = async () => {
    if (!plan || starting.current || !chosenCount) return;
    starting.current = true;
    setError("");
    try {
      const started = await tasks.start("import", importJobBody(plan.review_id, selected), { dialog: "import" });
      takenBy.current = started.id;
      setFilter("all"); setLink(null);
      setJobId(started.id);
    } catch (err) {
      if (closed.current) discardImport(plan.review_id);
      else if (GONE.includes(err.status)) gone(plan.review_id);
      else setError(err.message);
    } finally { starting.current = false; }
  };
  // What a close before Import would lose: the upload, or the review (or
  // the choices a review gone from the server left for Retry preview). A
  // review its job reads is no loss: its row opens it again.
  const guard = reviewBusy && !previewId ? {
    question: t("Stop uploading {name}? Importing it later means uploading it again.", { name: fileName }),
    keep: t("Keep uploading"), leave: t("Stop upload"),
  } : phase === "review" && (plan || keepChoices.current) ? {
    question: t("Close without importing? Importing later means uploading {name} again.", { name: fileName }),
    keep: t("Keep reviewing"), leave: t("Discard upload"),
  } : null;
  const percent = progress.total ? Math.min(100, Math.floor(progress.loaded / progress.total * 100)) : null;
  const title = result ? t("Import complete") : t("Review {label} import", { label: t(format.label) });
  const destinationFolder = folderPath(plan?.folder || params.folder || "");
  const destinationLabel = `${t("Library")} / ${destinationFolder || t("All pages")}`;
  const libraryColumn = (
    <section aria-label={t("Library after import")}><h3>{result ? t("Imported to library") : t("Library after import")}</h3>
      <p className="importDestination">{destinationLabel}</p>
      <div className="importTreeScroll">
        {shown.length ? <ImportTree node={destination} library selected={selected} complete={Boolean(result)}
          link={columnLink("library", destination, pageKey, links.toFiles)}
          onSelect={result ? undefined : (ids, checked) => setSelected(previous => selectItems(previous, ids, checked))} />
          : <p className="importEmpty">{result ? t("No new items were added.") : t("No items match this filter.")}</p>}
      </div>
    </section>
  );
  const sourceColumn = (
    <section aria-label={t("ZIP contents")}><h3>{file?.picked ? t("Selected files") : file?.folder ? t("Inside the folder")
      : /\.zip$/i.test(fileName) ? t("Inside the ZIP") : t("Source file")}</h3>
      <div className="importTreeScroll"><ImportTree node={sourceTree} link={columnLink("source", sourceTree, entry => entry.path, links.toPages)} /></div>
    </section>
  );
  const bothColumns = <>
    <div className="importReviewColumns">
      {sourceColumn}
      {libraryColumn}
    </div>
    <p className="srOnly" role="status">{matchStatus}</p>
  </>;
  return <SubDialog title={title} onClose={onClose} guard={guard} className="importReviewModal">
    <nav className="importStepsNav" aria-label={t("Import progress")}>
      {STEPS.map((label, index) => <span key={label}
        aria-current={PHASE_STEP[phase] === index ? "step" : undefined}>{index + 1}. {t(label)}</span>)}
    </nav>
    <div className="importReviewHeader">
      <strong>{fileName}</strong>{fileSize != null ? <span className="importFileSize">{fmtBytes(fileSize)}</span> : null}
      {result ? <p ref={reportHeading} tabIndex={-1} role="status">{importSummary(result)}</p>
        : <p>{reviewBusy ? phaseLabel : phase === "review" && plan ? t("Choose what to import and review its destination. Click a name to find its match in the other column.") : ""}</p>}
    </div>
    {error ? <FormNotice>{error}</FormNotice> : null}
    {previewing ? <>
      <TaskProgress task={preview} label={t("Review progress")} />
      <p className="reportModalHint">{t("You can close this window: the review goes on in Background tasks.")}</p>
    </> : reviewBusy ? <div className="importUploadProgress" role="status">
      <div><strong>{phaseLabel}{uploading && percent !== null ? `… ${percent}%` : "…"}</strong>
        {uploading ? <span>{fmtBytes(progress.loaded)}{progress.total ? ` / ${fmtBytes(progress.total)}` : ` / ${fmtBytes(file.size)}`}</span>
          : <span>{t("Upload complete. Reading the archive…")}</span>}
      </div>
      <progress aria-label={phase === "packing" ? phaseLabel : uploading ? t("Upload progress") : t("Import processing")} max={100} value={uploading ? percent ?? undefined : undefined} />
    </div> : null}
    {phase === "importing" ? <>
      <TaskProgress task={job} label={t("Import progress")} />
      <p className="reportModalHint">{t("You can close this window: the import goes on in Background tasks.")}</p>
    </> : null}
    {phase === "ended" ? (
      job?.state === "failed"
        ? <FormNotice>{t("The import failed: {reason}. Choose the file again to try once more.", { reason: t(job.error) })}</FormNotice>
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
      {bothColumns}
    </> : null}
    {phase === "complete" ? (plan ? bothColumns
      : <div className="importReviewColumns single">{libraryColumn}</div>) : null}
    {(phase === "review" && plan) || phase === "complete" ? (
      <details className="importWarnings" open={warnings.length > 0}>
        <summary>{warnings.length ? t("{n} warnings — review missing or omitted content", { n: warnings.length }) : t("No import warnings")}</summary>
        {warnings.length ? <ul>{warnings.map((warning, i) => <li key={i}><strong>{warning.title}</strong><span>{warning.reason}</span></li>)}</ul> : null}
      </details>
    ) : null}
    {phase === "review" && plan ? <p className="reportModalHint">{t(format.instructions)} {sourceKey === "zotero" && stripping ? t("Embedded annotations will be imported and stripped from the stored PDFs.") : ""}</p> : null}
    <div className="reportModalBtns importReviewFooter">
      {reviewBusy || phase === "review"
        ? <button type="button" className="uiBtn" onClick={() => { dropping.current = true; onClose(); }}>{t("Cancel")}</button> : null}
      {error && !plan && !refused && file && phase === "review" ? <button type="button" className="uiBtn" onClick={() => {
        discardImport(reviewId.current); reviewId.current = null; // what a review cut short left
        setAttempt(n => n + 1);
      }}>{t("Retry preview")}</button> : null}
      {reviewBusy || phase === "review" ? (
        <button type="button" className="uiBtn primary" disabled={!plan || reviewBusy || !chosenCount} onClick={submit}>{t("Import to library")}</button>
      ) : null}
      {phase === "importing" && job?.stoppable ? <button type="button" className="uiBtn" onClick={() => tasks.cancel(job).catch(err => setError(err.message))}>{t("Stop")}</button> : null}
      {phase === "importing" || phase === "ended" ? <button type="button" className="uiBtn primary" onClick={onClose}>{t("Close")}</button> : null}
      {phase === "complete" ? <button type="button" className="uiBtn primary" onClick={onClose}>{t("Done")}</button> : null}
    </div>
  </SubDialog>;
}
