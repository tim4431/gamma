import React from "react";
import { FileIcon, HighlightIcon, IndentIcon, LayoutIcon, MessageSquareIcon, PaperclipIcon, PenIcon } from "../shared/ui/Icons";
import { FormNotice, PictureChoices, Row, Segmented, Step, SubDialog, Toggle } from "../settings/SettingsKit";
import { ExportPreview, FormatIllustration } from "../shared/illustrations";
import { MorphText } from "../shared/ui/MotionWidgets";
import { fmtBytes } from "../shared/lib/format.js";
import { CATEGORIES, exportFormatOf, hasReviewStep, notionFiles, offersSubPages, resolveExport, resolveImport, exportSummary } from "./transferFormats";
import { BibliographyReview, useBibliographyStep } from "./BibliographyReview";
import { canPickFolder, folderDrop } from "./folderZip.js";
import { collectEntryFiles } from "../shared/lib/dropEntries.js";
import { TaskProgress } from "../tasks/TasksTray";
import { isActive, isFinished } from "../tasks/taskModel.js";
import { exportAgain, taskTitle } from "../tasks/taskKinds.js";
import { T, t, tn } from "../shared/i18n/i18n.js";

const EXPORT_CONTROLS = {
  highlights: { icon: HighlightIcon, label: T("Highlights") },
  notes: { icon: PenIcon, label: T("Notes") },
  notesAt: { icon: LayoutIcon, label: T("Place notes") },
  comments: { icon: MessageSquareIcon, label: T("Comments") },
  bundle: { icon: PaperclipIcon, label: T("Bundle the files") },
  subPages: { icon: IndentIcon, label: T("Include pages inside") },
};

// One of resolveExport's controls: a switch, or for the notes' place a choice.
function ExportControl({ control: { key, hint, title, disabled, choice }, payload, set }) {
  const { icon, label } = EXPORT_CONTROLS[key];
  return choice
    ? <Row icon={icon} label={t(label)} hint={hint} title={title} className="transferChoice">
      <Segmented value={payload[key]} options={choice} disabled={disabled} onChange={(value) => set({ [key]: value })} />
    </Row>
    : <Toggle icon={icon} label={t(label)} hint={hint} title={title}
      checked={payload[key]} disabled={disabled} onChange={(value) => set({ [key]: value })} />;
}

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
// last crumb. `secondaryAction` stands beside the button that acts: the
// review step's, or the first step's when there is no review.
function TransferDialog({ title, step, setStep, firstTitle, secondTitle, onCancel, children, action, actionLabel, busy, needsReview, onContinue, finalTitle, secondaryAction, wide }) {
  const head = React.useRef(null);
  React.useEffect(() => { head.current?.focus(); }, [step]);
  const total = 1 + (needsReview ? 1 : 0) + (finalTitle ? 1 : 0);
  return <SubDialog title={title} onClose={onCancel} className={`transferModal${wide ? " transferModalWide" : ""}`}>
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
      {step > 0 || !needsReview ? secondaryAction : null}
      <button type="button" className="uiBtn primary" disabled={busy}
        onClick={step === 0 ? onContinue : action}>{step === 0 && needsReview ? t("Next") : actionLabel}</button>
    </div>
  </SubDialog>;
}

// `job`: the export job this dialog shows (then only its last step, with
// fetchJob / onDownload / onStop / onRetry); otherwise the choice of format
// and options, and onExport(payload) starts the export. `folder` is the
// exported folder's id (null for a page), `folderName` its path for the titles;
// `subPages` the number of pages inside the page, at any depth (0 for a folder),
// `subPagesDirect` when it counts only those right inside it (resolveExport).
// `onLeaveJob` lets the last step's breadcrumb walk back into the setup: the
// job itself goes on in Background tasks, as closing the window would leave it.
// `translated`: {pages, language, counting} while the viewer shows the page's
// PDF translated, which an annotated PDF of it writes (resolveExport), and
// for a folder {papers, language, counting}, its papers that go translated;
// else null. `busy`: the export cannot start yet (the translation is still being
// read, or the last press is starting it). `shareView`: a share view's
// export, whose job has no row in Background tasks (useTasks' `follow`).
export function ExportDialog({ job, fetchJob, onDownload, onStop, onRetry, onLeaveJob, shareView = false, ...setup }) {
  const [resume, setResume] = React.useState(0);
  if (job) {
    return <ExportJobStep job={job} folder={setup.folder} folderName={setup.folderName} pageId={setup.pageId} subPages={setup.subPages}
      shareView={shareView} fetchJob={fetchJob} onClose={setup.onCancel}
      onDownload={onDownload} onStop={onStop} onRetry={onRetry}
      onBack={(step) => { setResume(step); onLeaveJob(); }} />;
  }
  return <ExportSetup {...setup} startStep={resume} />;
}

// `citationStyle`, `citationLocale`: the account's (Settings → Citations),
// which a Formatted bibliography starts from (useBibliographyStep).
function ExportSetup({ opts, setOpts, hasPdf, pdfStored, hasMeta, pageId, folder, folderName, subPages = 0, subPagesDirect = false,
  translated = null, busy = false, startStep = 0, onCancel, onExport, citationStyle, citationLocale }) {
  const [step, setStep] = React.useState(startStep);
  const context = { hasPdf, pdfStored, hasMeta, folder, subPages, subPagesDirect, translated };
  const resolved = resolveExport(opts, context);
  const { definition, formats, controls, needsReview, payload } = resolved;
  const { format, highlights, bundle } = payload;
  const isZotero = format === "zotero";
  const isBibliography = format === "bibliography";
  const reviewing = isBibliography && step === 1;
  const set = (patch) => setOpts((o) => ({ ...o, ...patch }));
  const bibliography = useBibliographyStep({ card: isBibliography, reviewing, opts, set, payload, pageId, folder, folderName,
    citationStyle, citationLocale });
  const { isFormatted } = bibliography;
  const advance = (id = format) => {
    const next = resolveExport({ ...opts, format: id }, context);
    set({ format: next.payload.format });
    if (next.needsReview) setStep(1);
    else onExport(next.payload);
  };
  const summary = exportSummary(resolved, folder);
  const switches = controls.map((control) => <ExportControl key={control.key} control={control} payload={payload} set={set} />);
  // The host's wait holds the button only where it exports.
  const waiting = busy && (step === 1 || !needsReview);

  return <TransferDialog title={folder ? t("Export “{folder}”", { folder: folderName }) : t("Export")} step={step} setStep={setStep}
    firstTitle={t("Choose a format")} secondTitle={t(definition.label)} needsReview={needsReview} onContinue={() => advance()}
    onCancel={onCancel} actionLabel={isFormatted ? bibliography.downloadLabel : t("Export")}
    action={isFormatted ? bibliography.download : () => onExport(payload)} finalTitle={isFormatted ? null : t("Export")}
    secondaryAction={bibliography.copyAction} wide={reviewing} busy={bibliography.notReady || waiting}>
    {step === 0 ? <>
      <FormatChoices label={t("Export format")} value={format} onChange={(format) => set({ format })} onConfirm={advance} options={formats} />
      {!needsReview ? <p className="reportModalHint">{summary}</p> : null}
    </> : isBibliography ? <BibliographyReview {...bibliography.review} summary={summary} controls={switches} />
    : <>
      <p className="reportModalHint">{t("Choose what to include.")}</p>
      <div className="transferReview">
        <ExportPreview {...payload} filesRow={payload.format === "notion" ? notionFiles(payload) : null} />
        <div className="transferControls">
          {switches}
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
// need a word (Zotero has its step guide), a bibliography's by its file.
const NEXT_STEP = {
  bibtex: T("Put it beside your .tex file, or upload it to Overleaf."),
  biblatex: T("Put it beside your .tex file and load it with \\addbibresource, or upload it to Overleaf."),
  ris: T("Import it into a reference manager: Zotero and EndNote read it with File → Import."),
  csljson: T("Give it to Pandoc with --bibliography, or to any other tool that reads CSL JSON."),
  obsidian: T("Unzip it into a vault, or open the folder as one."),
  gamma: T("In the other Gamma: Import → Gamma export (.zip)."),
  notability: T("Open it in Notability: on the iPad or the Mac, open the .note from Files (or AirDrop it) and choose Notability. Unzip a folder's .zip first."),
  goodnotes: T("Open it in GoodNotes: on the iPad, iPhone or Mac, open the .goodnotes from Files (or AirDrop it) and choose GoodNotes. Unzip a folder's .zip first."),
  notion: T("In Notion: Settings → Import → ZIP, then choose this file. Importing it again adds the pages a second time; it does not update them."),
};
// The reasons the server gives for a page an export left out
// (routers/export.py), listed so the catalogs translate them.
// biome-ignore lint/correctness/noUnusedVariables: kept for the catalogs; nothing reads it at run time
const SKIP_REASONS = [T("page has no PDF"), T("PDF not stored on the server"),
  T("page has no paper metadata"), T("the PDF has no pages"), T("the page's PDF changed after it was translated"),
  T("page has no PDF and no sheets of paper")];
// And the refusals a bibliography's export job fails with (its failed step shows them).
// biome-ignore lint/correctness/noUnusedVariables: kept for the catalogs; nothing reads it at run time
const EXPORT_REFUSALS = [T("none of these pages has paper metadata to cite"),
  T("this page has no paper metadata to cite")];

const EXPORT_HEADINGS = { queued: T("Exporting…"), running: T("Exporting…"), done: T("Export ready"),
  failed: T("Export failed"), cancelled: T("Export stopped") };

// The export dialog's last step: the export job (tasks/useTasks.js) as it
// runs and once it ended — the file to download, the pages left out, what
// to do next. The dialog can close any time: the job goes on in Background
// tasks, where the row opens this step again. A share view's goes on with no
// row, and the tab offers its file once it is ready (useAppJobs).
function ExportJobStep({ job, folder, folderName, pageId, subPages, shareView, fetchJob, onClose, onDownload, onStop, onRetry, onBack }) {
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
  // The formats with a step of their own had one, and so had every format
  // for a page with pages inside it (its "Include pages inside" switch):
  // the job's says whether it was on, and one left off is known only while
  // the job's page is still the one open (`pageId`, `subPages`).
  const reviewed = hasReviewStep(format) || Boolean(params.sub_pages)
    || (params.page_id === pageId && offersSubPages({ folder, subPages }));
  const total = reviewed ? 3 : 2;
  // Started again from its params, or for one that wrote the translation the
  // viewer showed, from the setup, which reads the viewer afresh, while its
  // page is open (exportAgain).
  const again = exportAgain(job, pageId);
  return <SubDialog title={folder ? t("Export “{folder}”", { folder: folderName }) : t("Export")} onClose={onClose} className="transferModal">
    <nav className="transferProgress" aria-label={t("Step {step} of {total}", { step: total, total })}>
      {/* Walking back is allowed even once the file is ready: the export is
          done (or goes on in the tray) and picking another format is a
          normal second thought. */}
      <button type="button" className="crumbBtn" onClick={() => onBack(0)}>1. {t("Choose a format")}</button>
      <span aria-hidden="true">/</span>
      {reviewed ? <>
        <button type="button" className="crumbBtn" onClick={() => onBack(1)}>{t("2. Review")}</button>
        <span aria-hidden="true">/</span>
      </> : null}
      <span aria-current="step">{total}. {t("Export")}</span>
    </nav>
    <div className="transferStep">
      <h2 ref={head} tabIndex={-1}>{t(EXPORT_HEADINGS[job.state] || EXPORT_HEADINGS.running)}</h2>
      <p className="reportModalHint">{taskTitle(job)}</p>
      {isActive(job) ? <>
        <TaskProgress task={job} label={t("Export progress")} />
        <p className="reportModalHint">{shareView
          ? t("You can close this window: the export goes on, and this tab offers its file once it is ready.")
          : t("You can close this window: the export goes on in Background tasks, and its file waits there.")}</p>
      </> : null}
      {job.state === "done" ? <>
        <div className="transferFile">
          <FileIcon size={16} />
          <strong>{job.artifact?.name}</strong>
          <span>{fmtBytes(job.artifact?.size)}</span>
        </div>
        {/* A watched export downloads by itself the moment it is ready
            (tasks.start's download: "auto"), which is worth saying rather
            than leaving the Download button to imply nothing has happened. */}
        {job.downloaded ? <p className="reportModalHint">{t("Saved to your downloads.")}</p> : null}
        {result ? <p className="reportModalHint">{tn("{n} page exported.", "{n} pages exported.", result.pages)}</p> : null}
        {result?.skipped?.length ? (
          <details className="importWarnings" open>
            <summary>{tn("{n} page left out", "{n} pages left out", result.skipped.length)}</summary>
            <ul>{result.skipped.map((skip, i) => <li key={i}><strong>{skip.title}</strong><span>{t(skip.reason)}</span></li>)}</ul>
          </details>
        ) : null}
        {/* What the pages lost on the way (a Notability note keeps no
            highlights, …): counted per page by the server, as the
            import's warnings are. */}
        {result?.warnings?.length ? (
          <details className="importWarnings" open>
            <summary>{tn("{n} thing the file could not keep", "{n} things the file could not keep", result.warnings.length)}</summary>
            <ul>{result.warnings.map((warning, i) => <li key={i}><strong>{warning.title}</strong><span>{warning.reason}</span></li>)}</ul>
          </details>
        ) : null}
        {format?.id === "zotero" ? <ZoteroSteps folder={folder} bundle={params.pdf} highlights={params.highlights} open />
          : NEXT_STEP[format?.bibFormat || format?.id] ? <p className="reportModalHint">{t(NEXT_STEP[format.bibFormat || format.id])}</p> : null}
      </> : null}
      {job.state === "failed" ? <FormNotice>{t(job.error)}</FormNotice> : null}
      {job.state === "cancelled" ? <p className="reportModalHint">{t("Nothing was saved. Start it again to export.")}</p> : null}
    </div>
    <div className="reportModalBtns transferFooter">
      {isActive(job) && job.stoppable ? <button type="button" className="uiBtn" onClick={onStop}>{t("Stop")}</button> : null}
      {job.state === "done" ? <button type="button" className="uiBtn" onClick={onClose}>{t("Done")}</button> : null}
      {job.state === "done" ? <button type="button" className="uiBtn primary" onClick={onDownload}>
        <MorphText>{job.downloaded ? t("Download again") : t("Download")}</MorphText></button> : null}
      {job.state === "failed" || job.state === "cancelled" ? <>
        <button type="button" className="uiBtn" onClick={onClose}>{t("Close")}</button>
        {again === "retry" ? <button type="button" className="uiBtn primary" onClick={onRetry}>{t("Start again")}</button> : null}
        {again === "setup" ? <button type="button" className="uiBtn primary" onClick={() => onBack(reviewed ? 1 : 0)}>{t("Export again…")}</button> : null}
      </> : null}
      {isActive(job) ? <button type="button" className="uiBtn primary" onClick={onClose}>{t("Close")}</button> : null}
    </div>
  </SubDialog>;
}

// The Zotero step takes the export dropped on it as well as picked: its
// folder (one directory entry, walked and packed like a picked one) or a
// .zip. A drop here stops at the dialog — the library behind would take the
// folder's PDFs as uploads.
function ExportDrop({ busy, onDrop, children }) {
  const [over, setOver] = React.useState(false);
  const hold = (e) => {
    if (!Array.from(e.dataTransfer?.types || []).includes("Files")) return false;
    e.preventDefault();
    e.stopPropagation();
    return true;
  };
  return <div className="importDropArea"
    onDragOver={(e) => { if (hold(e)) setOver(!busy); }}
    onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(false); }}
    onDrop={(e) => {
      if (!hold(e)) return;
      setOver(false);
      if (busy) return;
      // Entries now: the DataTransfer is neutered once the handler returns.
      const entries = Array.from(e.dataTransfer.items || []).map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
      const zip = Array.from(e.dataTransfer.files || []).find((file) => /\.zip$/i.test(file.name));
      if (entries.length === 1 && entries[0].isDirectory) {
        collectEntryFiles(entries).then((found) => { if (found.length) onDrop(folderDrop(found)); });
      } else if (zip) onDrop(zip);
    }}>
    {children}
    <p className={`importDrop${over ? " over" : ""}`}>{t("Drop the export folder or its .zip here")}</p>
  </div>;
}

// The import counterpart of ExportDialog, same shape: pick a source, confirm;
// Zotero shows its export guide first. Whether the originals of embedded
// annotations are stripped is the Settings preference (`strip`), never asked
// here. `file`: a .zip already chosen, one a native app was given
// (docs/dev/ipad.md "Opened files"): the dialog asks only which export it is,
// offers the sources that read an archive, and imports it with no picker.
export function ImportDialog({ hasPdf, strip, busy, onCancel, onImport, file = null }) {
  const [step, setStep] = React.useState(0);
  // Zotero is the default source; with a PDF open, that PDF's own annotations
  // are the more likely intent and win instead.
  const [source, setSource] = React.useState(hasPdf && !file ? "annots" : "zotero");
  const scope = { hasPdf: hasPdf && !file, archive: Boolean(file) };
  const { definition, formats, needsReview, payload } = resolveImport(source, { ...scope, strip });
  const src = definition.id;
  // Where the browser picks folders, a source that reads one picks its
  // folder first, the .zip a link beside it.
  const picksFolder = (format) => !file && Boolean(format.folderLabel) && canPickFolder();
  const folderFirst = picksFolder(definition);
  // What the source's button asks for, and a double-click on a card that
  // has no review step: the file already chosen, else its folder or file.
  const request = (format, chosen) => (file ? { ...chosen, file } : picksFolder(format) ? { ...chosen, pick: "folder" } : chosen);
  const advance = (id = src) => {
    if (busy) return;
    const next = resolveImport(id, { ...scope, strip });
    setSource(next.definition.id);
    if (next.needsReview) setStep(1);
    else onImport(request(next.definition, next.payload));
  };
  // Same numbered-step guide as the add-API-key wizard.
  const zoteroGuide = <div className="importSteps">
    <Step n={1} title={t("Export from Zotero")}
      hint={t('File → Export Library… (or right-click a collection), format "Zotero RDF".')} />
    <Step n={2} title={t("Include the files and notes")}
      hint={t('Check "Export Files" and "Export Notes" — the files carry your PDFs and the annotations you made in Zotero\'s reader.')} />
    <Step n={3} title={t("Pick the exported folder, or a .zip of it")}
      hint={t("First wait until Zotero has written every PDF: it keeps writing them after its export window closes. Collections become folders, tags labels, notes blocks; importing again updates instead of duplicating.")} />
  </div>;
  return <TransferDialog title={file ? t("Import {name}", { name: file.name }) : t("Import")} step={step} setStep={setStep}
    onCancel={onCancel} busy={busy}
    firstTitle={t("Choose a source")} secondTitle={t(definition.label)} needsReview={needsReview} onContinue={() => advance()}
    actionLabel={file ? t("Import") : t(folderFirst ? definition.folderLabel : definition.actionLabel)}
    action={() => onImport(request(definition, payload))}
    secondaryAction={folderFirst
      ? <button type="button" className="uiBtn ghost" disabled={busy} onClick={() => onImport(payload)}>{t("or choose a .zip")}</button> : null}>
    {step === 0 ? <>
      {file ? <p className="reportModalHint">{t("Which kind of export is this .zip?")}</p> : null}
      <FormatChoices label={t("Import from")} value={src} onChange={setSource} onConfirm={advance} options={formats} />
      {!needsReview ? <p className="reportModalHint">{t(definition.instructions)}</p> : null}
    </> : <ExportDrop busy={busy} onDrop={(file) => onImport({ ...payload, file })}>{zoteroGuide}</ExportDrop>}
  </TransferDialog>;
}
