import React from "react";
import { FileIcon, HighlightIcon, LinkIcon, PaperclipIcon, PenIcon, ScissorsIcon } from "../shared/ui/Icons";
import { PictureChoices, Step, SubDialog, Toggle } from "../settings/SettingsKit";
import { ExportPreview, ImportPreview, FormatIllustration } from "../shared/illustrations";
import { CopyBox, useCopied } from "../shared/ui/Widgets";
import { copyText } from "../shared/lib/utils";
import { fmtBytes } from "../shared/lib/format.js";
import { bibliographyText, shareToken, sharedBibliographyUrl } from "./exportApi";
import { CATEGORIES, countEntries, exportFormatOf, hasReviewStep, resolveExport, resolveImport, exportSummary } from "./transferFormats";
import { TaskProgress } from "../tasks/TasksTray";
import { isActive, isFinished } from "../tasks/taskModel.js";
import { taskTitle } from "../tasks/taskKinds.js";
import { T, t, tn } from "../shared/i18n/i18n.js";

const EXPORT_CONTROLS = {
  highlights: { icon: HighlightIcon, label: T("Highlights") },
  notes: { icon: PenIcon, label: T("Notes") },
  bundle: { icon: PaperclipIcon, label: T("Bundle the files") },
};

function FormatChoices({ label, value, onChange, onConfirm, options }) {
  return <div className="transferFormats" role="group" aria-label={label}>
    {CATEGORIES.map((type) => {
      const group = options.filter((option) => option.category === type);
      if (!group.length) return null;
      return <section key={type} className={`transferFormatRow${group.length < 3 ? " transferFormatRowWide" : ""}`} aria-label={t(type)}>
        <h3>{t(type)}</h3>
        <PictureChoices label={t("{type} choices", { type: type })} value={value} onChange={onChange} columns={Math.min(group.length, 4)}
          onConfirm={onConfirm}
          options={group.map(({ id, label, hint }) => ({ value: id, label, hint,
            preview: <FormatIllustration format={id} />,
          }))} />
      </section>;
    })}
  </div>;
}

// `finalTitle`: the step after this dialog (the export job's), shown as the
// last crumb.
function TransferDialog({ title, step, setStep, firstTitle, secondTitle, onCancel, children, action, actionLabel, busy, needsReview, onContinue, finalTitle, secondaryAction }) {
  const head = React.useRef(null);
  React.useEffect(() => { head.current?.focus(); }, [step]);
  const total = 1 + (needsReview ? 1 : 0) + (finalTitle ? 1 : 0);
  return <SubDialog title={title} onClose={onCancel} className="transferModal">
    <nav className="transferProgress" aria-label={t("Step {step} of {total}", { step: step + 1, total })}>
      {step > 0 ? <button type="button" className="crumbBtn" onClick={() => setStep(0)}>1. {firstTitle}</button>
        : <span aria-current="step">{total > 1 ? "1. " : ""}{firstTitle}</span>}
      {needsReview ? <><span aria-hidden="true">/</span>
      <span aria-current={step === 1 ? "step" : undefined}>{t("2. Review")}</span></> : null}
      {finalTitle ? <><span aria-hidden="true">/</span><span>{total}. {finalTitle}</span></> : null}
    </nav>
    <div className="transferStep" key={step}>
      <h2 ref={head} tabIndex={-1}>{step === 0 ? firstTitle : secondTitle}</h2>
      {children}
    </div>
    <div className="reportModalBtns transferFooter">
      {step > 0 ? secondaryAction : null}
      <button type="button" className="uiBtn primary" disabled={busy}
        onClick={step === 0 ? onContinue : action}>{step === 0 && needsReview ? t("Next") : actionLabel}</button>
    </div>
  </SubDialog>;
}

// A bibliography previews itself. The entries come from the same endpoint the
// download uses (?mode=bibtex), so the citation keys on screen are the ones
// the file will carry — including the a/b suffixes a clash gets, which is
// exactly what a reader wants to check before citing. `active` keeps the
// fetch out of the way until the BibTeX step is actually on screen.
function useBibliography(active, { pageId, folder }) {
  const [state, setState] = React.useState({ loading: true });
  const [url, setUrl] = React.useState("");
  React.useEffect(() => {
    if (!active) return undefined;
    let live = true;
    setState({ loading: true });
    bibliographyText({ pageId, folder })
      .then((text) => { if (live) setState({ text }); })
      .catch((err) => { if (live) setState({ error: err.message }); });
    // The link is a bonus: no share, no link, and nothing is created here.
    shareToken({ pageId, folder })
      .then((token) => { if (live) setUrl(sharedBibliographyUrl(token, { pageId, folder })); });
    return () => { live = false; };
  }, [active, pageId, folder]);
  // Inactive it reports nothing, so the format step's Next is never waiting
  // on a fetch that has not been asked for.
  return { ...(active ? state : {}), url };
}

function BibtexReview({ bib, folder, summary, copied, onCopy }) {
  return <>
    {bib.loading ? <p className="reportModalHint">{t("Reading the citation records…")}</p> : null}
    {bib.error ? <p role="alert" className="importWarning">{t(bib.error)}</p> : null}
    {bib.text ? <>
      {/* How many of the folder's pages turned out to be citable — the one
          thing the entries below don't say at a glance. */}
      {folder ? <p className="reportModalHint">{tn("{n} paper in this folder can be cited.",
        "{n} papers in this folder can be cited.", countEntries(bib.text))}</p> : null}
      <CopyBox copied={copied} onCopy={onCopy}
        title={t("Copy the whole bibliography")} label={t("Copy BibTeX")}>
        <pre className="bibtexPre bibtexPreview">{bib.text}</pre>
      </CopyBox>
      <div className="reportModalHint">{summary}</div>
    </> : null}
    <BibtexSteps url={bib.url} folder={folder} />
  </>;
}

// Keeping a LaTeX document's bibliography in step with the library: the
// export endpoint answers the current .bib on every request, so a share link
// turns it into a URL Overleaf (or a Makefile, or curl) can refresh from.
// Sharing itself stays in the share popover, where the audience is chosen —
// this only reports whether a link exists.
function BibtexSteps({ url, folder }) {
  const [copied, flash] = useCopied();
  return <details className="transferHelp"><summary>{t("Keep this .bib up to date")}</summary><div className="importSteps">
    {url ? <>
      <div className="bibLinkRow">
        <LinkIcon size={14} />
        <input value={url} readOnly onFocus={(e) => e.target.select()} aria-label={t("Bibliography link")} />
        <button type="button" className={`uiBtn sm iconSq ${copied ? "on" : ""}`}
          title={t("Copy the bibliography link")} aria-label={t("Copy the bibliography link")}
          onClick={() => copyText(url).then((ok) => { if (ok) flash(); })}>
          {copied ? t("Copied") : t("Copy")}
        </button>
      </div>
      <Step n={1} title={t("In Overleaf: Upload → From External URL")}
        hint={t("Paste this link and name the file references.bib. The Refresh button next to it fetches the current entries again.")} />
      <Step n={2} title={t("Cite as usual")}
        hint={t("Use the citation keys above in your document. Pin a key in the (i) metadata popover to keep citing a paper by the name your document already uses.")} />
      {/* Whole sentences, not an interpolated noun: a fragment like "the page"
          has no context for a translator (docs/dev/i18n.md). */}
      <p className="reportModalHint">{folder
        ? t("Anyone with the link can read the folder — it is the share link, so changing or stopping the share changes this too.")
        : t("Anyone with the link can read the page — it is the share link, so changing or stopping the share changes this too.")}</p>
    </> : <>
      <p className="reportModalHint">{t("A share link makes this bibliography available at a fixed URL, which Overleaf and build scripts can refresh from instead of re-exporting by hand.")}</p>
      <Step n={1} title={folder ? t("Share the folder") : t("Share the page")}
        hint={folder ? t("Use Share in the folder's ⋮ menu and pick who may open the link.")
          : t("Use Share in the page's menu and pick who may open the link.")} />
      <Step n={2} title={t("Come back here")} hint={t("The link to paste into Overleaf appears in this step.")} />
    </>}
  </div></details>;
}

// `job`: the export job this dialog shows (then only its last step, with
// fetchJob / onDownload / onStop / onRetry); otherwise the choice of format
// and options, and onExport(payload) starts the export.
export function ExportDialog({ job, fetchJob, onDownload, onStop, onRetry, ...setup }) {
  if (job) {
    return <ExportJobStep job={job} folder={setup.folder} fetchJob={fetchJob} onClose={setup.onCancel}
      onDownload={onDownload} onStop={onStop} onRetry={onRetry} />;
  }
  return <ExportSetup {...setup} />;
}

function ExportSetup({ opts, setOpts, hasPdf, pdfStored, hasMeta, pageId, folder, onCancel, onExport }) {
  const [step, setStep] = React.useState(0);
  const context = { hasPdf, pdfStored, hasMeta, folder };
  const resolved = resolveExport(opts, context);
  const { definition, formats, controls, needsReview, payload } = resolved;
  const { format, highlights, bundle } = payload;
  const isZotero = format === "zotero";
  const isBibtex = format === "bibtex";
  const bib = useBibliography(isBibtex && step === 1, { pageId, folder });
  const [copied, flashCopied] = useCopied();
  const set = (patch) => setOpts((o) => ({ ...o, ...patch }));
  const advance = (id = format) => {
    const next = resolveExport({ ...opts, format: id }, context);
    set({ format: next.payload.format });
    if (next.needsReview) setStep(1);
    else onExport(next.payload);
  };
  const summary = exportSummary(resolved, folder);

  // BibTeX is small and text: copying it is often the whole job, so the step
  // offers that beside the download instead of making the file the only way out.
  const copyAction = isBibtex && bib.text ? <button type="button" className={`uiBtn ${copied ? "on" : ""}`}
    onClick={() => copyText(bib.text).then((ok) => { if (ok) flashCopied(); })}>
    {copied ? t("Copied") : t("Copy")}</button> : null;

  return <TransferDialog title={folder ? t("Export “{folder}”", { folder: folder }) : t("Export")} step={step} setStep={setStep}
    firstTitle={t("Choose a format")} secondTitle={t(definition.label)} needsReview={needsReview} onContinue={() => advance()}
    onCancel={onCancel} actionLabel={t("Export")} action={() => onExport(payload)} finalTitle={t("Export")}
    secondaryAction={copyAction} busy={isBibtex && (bib.loading || !!bib.error)}>
    {step === 0 ? <>
      <FormatChoices label={t("Export format")} value={format} onChange={(format) => set({ format })} onConfirm={advance} options={formats} />
      {!needsReview ? <p className="reportModalHint">{summary}</p> : null}
    </> : isBibtex ? <BibtexReview bib={bib} folder={folder} summary={summary} copied={copied}
      onCopy={() => copyText(bib.text).then((ok) => { if (ok) flashCopied(); })} />
    : <>
      <p className="reportModalHint">{t("Choose what to include.")}</p>
      <div className="transferReview">
        <ExportPreview {...payload} />
        <div className="transferControls">
          {controls.map(({ key, hint, title, disabled }) => {
            const { icon, label } = EXPORT_CONTROLS[key];
            return <Toggle key={key} icon={icon} label={label} hint={hint} title={title}
              checked={payload[key]} disabled={disabled} onChange={(value) => set({ [key]: value })} />;
          })}
          {isZotero && !bundle ? <p className="reportModalHint">{t("Highlights travel inside PDF files. Turn on file bundling to include them.")}</p> : null}
        </div>
      </div>
      {isZotero ? <ZoteroSteps folder={folder} bundle={bundle} highlights={highlights} />
        : <div className="reportModalHint">{summary}</div>}
    </>}
  </TransferDialog>;
}

// Same numbered-step guide as the Zotero import dialog — the .zip trap
// (Zotero can't read one) is worth spelling out every time.
function ZoteroSteps({ folder, bundle, highlights, open = false }) {
  return <details className="transferHelp" open={open}><summary>{t("Open this export in Zotero")}</summary><div className="importSteps">
    <Step n={1} title={t("Download the .zip")}
      hint={t("Metadata, {folders} as collections, tags, notes{images}.", { folders: folder ? "subfolders" : "folders", images: bundle ? t("; the PDF{_s}{embedded} and note images", { _s: folder ? "s" : "", embedded: highlights ? t(" with highlights embedded") : "" }) : "" })} />
    <Step n={2} title={t("Unzip it")}
      hint={t("Keep the .rdf and the files/ folder together.")} />
    <Step n={3} title={t("Import the .rdf in Zotero")}
      hint={t('File → Import… → "A file" → pick the .rdf — never the .zip (Zotero calls it an unsupported format). Untick "Place imported collections… into a new collection" to skip the extra wrapper folder.')} />
  </div></details>;
}

// What to do with an export once it is downloaded, for the formats that
// need a word (Zotero has its step guide).
const NEXT_STEP = {
  bibtex: T("Put it beside your .tex file, or upload it to Overleaf."),
  obsidian: T("Unzip it into a vault, or open the folder as one."),
  gamma: T("In the other Gamma: Import → Gamma export (.zip)."),
};
// The reasons the server gives for a page an export left out
// (routers/export.py), listed so the catalogs translate them.
const SKIP_REASONS = [T("page has no PDF"), T("PDF not stored on the server"),
  T("page has no paper metadata")];
// And the refusals a whole export fails with, shown in the BibTeX step.
const EXPORT_REFUSALS = [T("none of these pages has paper metadata to cite"),
  T("this page has no paper metadata to cite")];

const EXPORT_HEADINGS = { queued: T("Exporting…"), running: T("Exporting…"), done: T("Export ready"),
  failed: T("Export failed"), cancelled: T("Export stopped") };

// The export dialog's last step: the export job (tasks/useTasks.js) as it
// runs and once it ended — the file to download, the pages left out, what
// to do next. The dialog can close any time: the job goes on in Background
// tasks, where the row opens this step again.
function ExportJobStep({ job, folder, fetchJob, onClose, onDownload, onStop, onRetry }) {
  const [full, setFull] = React.useState(null); // the ended job with its result
  const head = React.useRef(null);
  const ended = isFinished(job);
  React.useEffect(() => {
    if (!ended) return undefined;
    let current = true;
    fetchJob(job.id).then((found) => { if (current) setFull(found); }).catch(() => {});
    return () => { current = false; };
  }, [ended, job.id, fetchJob]);
  React.useEffect(() => { head.current?.focus(); }, [job.state]);
  const result = full?.id === job.id ? full.result : null;
  const format = exportFormatOf(job.params?.mode);
  const params = job.params || {};
  const reviewed = hasReviewStep(format); // the formats with a step of their own had one
  const total = reviewed ? 3 : 2;
  return <SubDialog title={folder ? t("Export “{folder}”", { folder }) : t("Export")} onClose={onClose} className="transferModal">
    <nav className="transferProgress" aria-label={t("Step {step} of {total}", { step: total, total })}>
      <span>1. {t("Choose a format")}</span><span aria-hidden="true">/</span>
      {reviewed ? <><span>{t("2. Review")}</span><span aria-hidden="true">/</span></> : null}
      <span aria-current="step">{total}. {t("Export")}</span>
    </nav>
    <div className="transferStep">
      <h2 ref={head} tabIndex={-1}>{t(EXPORT_HEADINGS[job.state] || EXPORT_HEADINGS.running)}</h2>
      <p className="reportModalHint">{taskTitle(job)}</p>
      {isActive(job) ? <>
        <TaskProgress task={job} label={t("Export progress")} />
        <p className="reportModalHint">{t("You can close this window: the export goes on in Background tasks, and its file waits there.")}</p>
      </> : null}
      {job.state === "done" ? <>
        <div className="transferFile">
          <FileIcon size={16} />
          <strong>{job.artifact?.name}</strong>
          <span>{fmtBytes(job.artifact?.size)}</span>
        </div>
        {result ? <p className="reportModalHint">{tn("{n} page exported.", "{n} pages exported.", result.pages)}</p> : null}
        {result?.skipped?.length ? (
          <details className="importWarnings" open>
            <summary>{tn("{n} page left out", "{n} pages left out", result.skipped.length)}</summary>
            <ul>{result.skipped.map((skip, i) => <li key={i}><strong>{skip.title}</strong><span>{t(skip.reason)}</span></li>)}</ul>
          </details>
        ) : null}
        {format?.id === "zotero" ? <ZoteroSteps folder={folder} bundle={params.pdf} highlights={params.highlights} open />
          : NEXT_STEP[format?.id] ? <p className="reportModalHint">{t(NEXT_STEP[format.id])}</p> : null}
      </> : null}
      {job.state === "failed" ? <p role="alert" className="importWarning">{t(job.error)}</p> : null}
      {job.state === "cancelled" ? <p className="reportModalHint">{t("Nothing was saved. Start it again to export.")}</p> : null}
    </div>
    <div className="reportModalBtns transferFooter">
      {isActive(job) && job.stoppable ? <button type="button" className="uiBtn" onClick={onStop}>{t("Stop")}</button> : null}
      {job.state === "done" ? <button type="button" className="uiBtn" onClick={onClose}>{t("Done")}</button> : null}
      {job.state === "done" ? <button type="button" className="uiBtn primary" onClick={onDownload}>{t("Download")}</button> : null}
      {job.state === "failed" || job.state === "cancelled" ? <>
        <button type="button" className="uiBtn" onClick={onClose}>{t("Close")}</button>
        <button type="button" className="uiBtn primary" onClick={onRetry}>{t("Start again")}</button>
      </> : null}
      {isActive(job) ? <button type="button" className="uiBtn primary" onClick={onClose}>{t("Close")}</button> : null}
    </div>
  </SubDialog>;
}

// The import counterpart of ExportDialog, same shape: pick a source, flip the
// switch that source understands, confirm. Nothing here is remembered — the
// strip switch starts from the Settings preference every time, so the setting
// stays the standing policy and the dialog is only ever a one-off override.
export function ImportDialog({ hasPdf, stripDefault, busy, onCancel, onImport }) {
  const [step, setStep] = React.useState(0);
  // Zotero is the default source; with a PDF open, that PDF's own annotations
  // are the more likely intent and win instead.
  const [source, setSource] = React.useState(hasPdf ? "annots" : "zotero");
  const [strip, setStrip] = React.useState(stripDefault);
  const { definition, formats, needsReview, payload } = resolveImport(source, { hasPdf, strip });
  const src = definition.id;
  const advance = (id = src) => {
    if (busy) return;
    const next = resolveImport(id, { hasPdf, strip });
    setSource(next.definition.id);
    if (next.needsReview) setStep(1);
    else onImport(next.payload);
  };
  return <TransferDialog title={t("Import")} step={step} setStep={setStep} onCancel={onCancel} busy={busy}
    firstTitle={t("Choose a source")} secondTitle={t(definition.label)} needsReview={needsReview} onContinue={() => advance()}
    actionLabel={t(definition.actionLabel)} action={() => onImport(payload)}>
    {step === 0 ? <>
      <FormatChoices label={t("Import from")} value={src} onChange={setSource} onConfirm={advance} options={formats} />
      {!needsReview ? <p className="reportModalHint">{t(definition.instructions)}</p> : null}
    </> : <>
      <div className="transferReview">
        <ImportPreview annotations strip={payload.strip} />
        <div className="transferControls">
          <Toggle
            icon={ScissorsIcon}
            label={t("Strip the originals")}
            hint={src === "annots" ? t("Rewrite the stored PDF without them") : t("Rewrite the imported PDFs without them")}
            title={t("Rewrite the stored file without the annotations you're importing, so only Gamma's copies remain. Off: they stay in the file and the viewer hides them.")}
            checked={payload.strip}
            onChange={setStrip}
          />
        </div>
      </div>
      {src === "zotero" ? (
        // Same numbered-step guide as the add-API-key wizard.
        <div className="importSteps">
          <Step n={1} title={t("Export from Zotero")}
            hint={t('File → Export Library… (or right-click a collection), format "Zotero RDF".')} />
          <Step n={2} title={t("Include the files and notes")}
            hint={t('Check "Export Files" and "Export Notes" — the files carry your PDFs and the annotations you made in Zotero\'s reader.')} />
          <Step n={3} title={t("Zip the exported folder and pick it here")}
            hint={t("Papers arrive with their metadata; collections become folders, tags labels, notes blocks. Importing again updates instead of duplicating.")} />
        </div>
      ) : (
        <div className="reportModalHint">{t(definition.instructions)}</div>
      )}
    </>}
  </TransferDialog>;
}
