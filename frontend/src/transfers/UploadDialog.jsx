// A batch of files going into the library (docs/dev/import_export.md
// "Uploading a folder", transfers/uploadBatch.js): before a large one starts
// (`ask`: its title, folder name, plan and destination), what it holds and what would
// make it slow or not fit, with Upload; then its progress and its end, read
// from its row of Background tasks (`rowId`, App's runUploadBatch, the
// batch's state in the row's `uploads`). The batch is the tab's work, not
// the dialog's: a close leaves it running, and its row opens the dialog
// again. Once over it says what went, lists what failed with Retry, and
// shows the folder it went into.
import React from "react";
import { FormNotice, SubDialog } from "../settings/SettingsKit";
import { fmtBytes } from "../shared/lib/format.js";
import { TaskProgress } from "../tasks/TasksTray";
import { isActive } from "../tasks/taskModel.js";
import { ASK_PAST_FILES } from "./fileRouting.js";
import { ASK_PAST_BYTES, batchSummary, skippedGroups } from "./uploadBatch.js";
import "./importReview.css";
import { t, tn } from "../shared/i18n/i18n.js";

const LISTED = 100; // the rows a list shows before "and n more"

// A list of names under a summary line: what failed, what was left out.
function NameList({ summary, rows, open = false }) {
  if (!rows.length) return null;
  return (
    <details className="importWarnings" open={open}>
      <summary>{summary}</summary>
      <ul>
        {rows.slice(0, LISTED).map((row) => <li key={row.key}><strong>{row.name}</strong>{row.detail ? <span>{row.detail}</span> : null}</li>)}
      </ul>
      {rows.length > LISTED ? <p className="importEmpty">{t("and {n} more", { n: rows.length - LISTED })}</p> : null}
    </details>
  );
}

export default function UploadDialog({ ask = null, rowId = null, tasks, onStart, onRetry, onShow, onClose }) {
  const listed = rowId ? tasks.tasks.find((task) => task.id === rowId) || null : null;
  // a row cleared from Background tasks while the dialog shows it: as last seen
  const seen = React.useRef(null);
  if (listed) seen.current = listed;
  const row = listed || (seen.current?.id === rowId ? seen.current : null);
  const batch = ask || row?.uploads || null;
  if (!batch) return null;
  const { title, name, plan, destination } = batch;
  const phase = ask ? "ask" : batch.over ? "over" : "running";
  const stopping = phase === "running" && !isActive(row); // stopped: the files on their way are being let go

  const kinds = [
    plan.kinds.pdf ? tn("{n} PDF", "{n} PDFs", plan.kinds.pdf) : "",
    plan.kinds.markdown ? tn("{n} Markdown note", "{n} Markdown notes", plan.kinds.markdown) : "",
    plan.kinds.file ? tn("{n} other file", "{n} other files", plan.kinds.file) : "",
  ].filter(Boolean).join(" · ");
  const skipped = skippedGroups(plan.skipped || []).map((group) => ({ key: group.name, name: group.name,
    detail: group.files ? tn("{n} file", "{n} files", group.files) : "" }));
  const leftOut = <NameList rows={skipped}
    summary={tn("{n} item left out: hidden, temporary or unstorable", "{n} items left out: hidden, temporary or unstorable", skipped.length)} />;
  const failed = (batch.failed || []).map((f, i) => ({ key: `${i}:${f.name}`, name: f.name, detail: t(f.error) }));

  return (
    <SubDialog title={title} onClose={onClose} className="uploadModal">
      <div className="importReviewHeader">
        {name ? <strong>{name}</strong> : null}
        <span className="importFileSize">{tn("{n} file", "{n} files", plan.files)} · {fmtBytes(plan.bytes)}</span>
        {kinds ? <p>{kinds}</p> : null}
        <p>{t("Goes to {place}.", { place: destination })}{plan.folders
          ? ` ${tn("Its directory becomes a folder there.", "Its {n} directories become folders there.", plan.folders)}` : ""}</p>
      </div>

      {phase === "ask" ? <>
        {plan.files > ASK_PAST_FILES ? <FormNotice tone="warn">{t("Each of the {n} files becomes a page of its own.", { n: plan.files })}</FormNotice> : null}
        {plan.bytes > ASK_PAST_BYTES ? <FormNotice tone="warn">
          {t("{size} takes a while to upload. It runs in Background tasks for as long as this tab stays open.", { size: fmtBytes(plan.bytes) })}
        </FormNotice> : null}
        {plan.left !== null ? <FormNotice tone="warn">
          {t("The files take {size} and the storage has {left} left, so the uploads past that fail. A file already in the library takes no more room.",
            { size: fmtBytes(plan.stored), left: fmtBytes(plan.left) })}
        </FormNotice> : null}
        {leftOut}
      </> : null}

      {phase === "running" ? <>
        {stopping ? <p className="reportModalHint" role="status">{t("Stopping…")}</p> : <TaskProgress task={row} label={t("Upload progress")} />}
        {batch.current?.length ? <p className="importDestination uploadNow">{t("Now: {names}", { names: batch.current.join(", ") })}</p> : null}
        {failed.length ? <NameList rows={failed}
          summary={tn("{n} file could not be uploaded so far", "{n} files could not be uploaded so far", failed.length)} /> : null}
        <p className="reportModalHint">{t("You can close this window: the upload goes on in Background tasks while this tab stays open.")}</p>
      </> : null}

      {phase === "over" ? <>
        {batch.error ? <FormNotice>{batch.error}</FormNotice>
          : <p className="uploadSummary" role="status">
            {batchSummary({ total: batch.total, done: batch.done, failed: batch.failed || [], stopped: batch.stopped })}</p>}
        <NameList rows={failed} open summary={tn("{n} failed file", "{n} failed files", failed.length)} />
        {leftOut}
      </> : null}

      <div className="reportModalBtns importReviewFooter">
        {phase === "ask" ? <>
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="button" className="uiBtn primary" onClick={onStart}>{tn("Upload {n} file", "Upload {n} files", plan.files)}</button>
        </> : null}
        {phase === "running" ? <>
          {row && isActive(row) ? <button type="button" className="uiBtn" onClick={() => tasks.cancel(row).catch(() => {})}>{t("Stop")}</button> : null}
          <button type="button" className="uiBtn primary" onClick={onClose}>{t("Close")}</button>
        </> : null}
        {phase === "over" ? <>
          {failed.length && onRetry ? <button type="button" className="uiBtn" onClick={() => onRetry(rowId)}>
            {tn("Retry {n} failed file", "Retry {n} failed files", failed.length)}</button> : null}
          {batch.done ? <button type="button" className="uiBtn" onClick={() => onShow(batch.root || "")}>{t("Show in library")}</button> : null}
          <button type="button" className="uiBtn primary" onClick={onClose}>{t("Done")}</button>
        </> : null}
      </div>
    </SubDialog>
  );
}
