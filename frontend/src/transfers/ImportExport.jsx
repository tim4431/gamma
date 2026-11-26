import React from "react";
import { FileIcon, FileTextIcon, HighlightIcon, IndentIcon, LayoutIcon, MessageSquareIcon, PaperclipIcon, PenIcon, PinIcon,
  QuoteMarkIcon } from "../shared/ui/Icons";
import { CopyField, FormNotice, PictureChoices, Row, Segmented, Step, SubDialog, Toggle } from "../settings/SettingsKit";
import { CitationHtml } from "../citations/CitationHtml";
import { CitationLanguageRow, styleMenuProps, useAddedStyles } from "../citations/CitationChoices";
import { ExportPreview, FormatIllustration } from "../shared/illustrations";
import { CopyBox, useCopied } from "../shared/ui/Widgets";
import { MenuSelect } from "../shared/ui/Menus";
import { MorphText } from "../shared/ui/MotionWidgets";
import { copyPayload, copyText } from "../shared/lib/utils";
import { saveBlob } from "../shared/lib/saveFile.js";
import { fmtBytes } from "../shared/lib/format.js";
import { DEFAULT_STYLE } from "../citations/bundledStyles.js";
import { styleInUse, styleKindLabel } from "../citations/citeRules.js";
import { preloadCitations, render, renderPair, styleMeta } from "../citations/render.js";
import { bibliographyPreview, shareToken, sharedBibliographyUrl } from "./exportApi";
import { BIBLIOGRAPHY_FORMATS, CATEGORIES, FORMATTED_FILES, bibliographyFormat, bibliographyMode, exportFormatOf, formattedFile,
  hasReviewStep, notionFiles, offersSubPages, resolveExport, resolveImport, exportSummary } from "./transferFormats";
import { canPickFolder, folderDrop } from "./folderZip.js";
import { collectEntryFiles } from "../shared/lib/dropEntries.js";
import { TaskProgress } from "../tasks/TasksTray";
import { isActive, isFinished } from "../tasks/taskModel.js";
import { exportAgain, taskTitle } from "../tasks/taskKinds.js";
import { T, t, tn } from "../shared/i18n/i18n.js";
import { guideEvents } from "../guide/events.js";

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

// A bibliography previews itself, in the two panes the Zotero import review
// uses: the papers on the left, the entry of whichever one is picked on the
// right. The data comes from the same builder as the download
// (GET /api/bibliography), so the citation keys on screen — the a/b suffixes
// a clash gets included — are the ones the file will carry, and the pages it
// cannot cite are listed before anything is downloaded rather than after.
// `active` keeps the fetch out of the way until the step is on screen.
// `subPages`: the review and the link are the page's export with the pages
// inside it. `mode` is the bibFormat: the answer is read in its file's
// format (Formatted's in BibTeX's, for the CSL items every answer has),
// and the link serves that file. Picking another format reads that one,
// the last answer shown meanwhile (`loading`), so the review stays put.
function useBibliography(active, { pageId, folder, subPages, mode }) {
  const format = bibliographyMode(mode);
  const target = `${pageId}|${folder}|${subPages ? 1 : 0}`;
  const [state, setState] = React.useState({});  // {target, format, data | error}: the last answer
  const [token, setToken] = React.useState("");
  React.useEffect(() => {
    if (!active) return undefined;
    let live = true;
    bibliographyPreview({ pageId, folder, subPages, format })
      .then((data) => { if (live) setState({ target, format, data }); })
      .catch((err) => { if (live) setState({ target, format, error: err.message }); });
    return () => { live = false; };
  }, [active, target, format]);
  React.useEffect(() => {
    if (!active) return undefined;
    let live = true;
    // The link is a bonus: no share, no link, and nothing is created here.
    shareToken({ pageId, folder })
      .then((found) => { if (live) setToken(found); });
    return () => { live = false; };
  }, [active, pageId, folder]);
  // Inactive it reports nothing, so the format step's Next is never waiting
  // on a fetch that has not been asked for.
  const same = active && state.target === target;
  const current = same && state.format === format;
  return { data: same ? state.data : undefined, error: current ? state.error : undefined, loading: active && !current,
    url: sharedBibliographyUrl(token, { pageId, folder, subPages, mode }) };
}

// A Formatted bibliography (transferFormats.js BIBLIOGRAPHY_FORMATS): the
// review's entries rendered in a citation style in the browser
// (citations/render.js) as one list, in the review's order, so a numeric
// style numbers them so and 2020a/2020b hold across the list; the right
// pane shows the picked paper's entry from that list. Its style, language
// and file are this dialog's choices (`opts` citeStyle, citeLocale,
// citeFile, kept in gamma-export-opts with the rest), the account's style
// and language (`defaults`) until one is picked here; a style no longer on
// this server is the default style (citeRules.js styleInUse). `pair(index)`
// is the copy's {html, text}: the whole list, or the entry at `index` of
// `cited`.
function useFormatted(active, data, opts, defaults) {
  const added = useAddedStyles({ active });
  const { style } = styleInUse({ wanted: opts.citeStyle || defaults.citationStyle, list: added });
  const locale = opts.citeLocale || defaults.citationLocale;
  const file = FORMATTED_FILES.some(([id]) => id === opts.citeFile) ? opts.citeFile : "html";
  const cited = React.useMemo(() => (data?.entries || []).filter((entry) => entry.csl), [data]);
  const items = React.useMemo(() => cited.map((entry) => entry.csl), [cited]);
  const [shown, setShown] = React.useState({});  // {items, style, locale, result | error}
  React.useEffect(() => {
    if (!active || !items.length) return undefined;
    let live = true;
    render({ items, style, locale, output: "html" })
      .then((result) => { if (live) setShown({ items, style, locale, result }); })
      .catch((error) => { if (live) setShown({ items, style, locale, error }); });
    return () => { live = false; };
  }, [active, items, style, locale]);
  // The last render stays on screen while a new style's is made.
  const same = shown.items === items;
  const pair = (index = null) => renderPair({ items, style, locale }).then((both) => (index == null ? both : both.entries[index]));
  return { added, style, locale, file, cited, items, pair,
    result: same ? shown.result : null, error: same && shown.style === style && shown.locale === locale ? shown.error : null,
    loading: active && items.length > 0 && !(same && shown.style === style && shown.locale === locale) };
}

// The Formatted bibliography's choices: its style (the bundled ones and
// the account's, by class), its language (unless the style sets its own)
// and the file Download saves.
function FormattedOptions({ formatted, set }) {
  const kind = styleKindLabel(styleMeta(formatted.style).kind);
  return <>
    <Row icon={QuoteMarkIcon} label={t("Style")} hint={kind ? t("{kind} style", { kind }) : null}>
      <MenuSelect label={t("Citation style")} {...styleMenuProps(formatted.added.styles, formatted.style, (citeStyle) => set({ citeStyle }))} />
    </Row>
    <CitationLanguageRow style={formatted.style} value={formatted.locale} onChange={(citeLocale) => set({ citeLocale })} />
    <Row icon={FileIcon} label={t("File")} hint={t("HTML and RTF keep the formatting")}>
      <Segmented value={formatted.file} options={FORMATTED_FILES} onChange={(citeFile) => set({ citeFile })} />
    </Row>
  </>;
}

// The bibliography's review: its format (a server file, or Formatted with
// its own choices), the papers on the left and the picked one's entry on
// the right, the summary, and for a file the link that keeps it up to date.
// `controls`: the export's switches (for a bibliography only "Include pages
// inside", resolveExport). `onCopy(text, key)` copies a file's text,
// `onCopyFormatted(index)` a Formatted entry; `notice` is a Formatted copy's
// or download's failure, `saved` that its file was saved.
function BibliographyReview({ bib, folder, summary, copied, onCopy, onCopyFormatted, controls, payload, set, formatted, notice, saved }) {
  const { entries = [], skipped = [] } = bib.data || {};
  const [picked, setPicked] = React.useState("");
  const shown = entries.find((entry) => entry.page_id === picked) || entries[0];
  const format = bibliographyFormat(payload.bibFormat);
  const isFormatted = format.id === "formatted";
  if (bib.error) return <FormNotice>{t(bib.error)}</FormNotice>;
  if (!bib.data) return <p className="reportModalHint">{t("Reading the citation records…")}</p>;
  const paneTitle = isFormatted ? t("Reference list") : t("{format} entry", { format: format.label });
  const index = shown ? formatted.cited.indexOf(shown) : -1;
  const html = formatted.result && index >= 0 ? formatted.result.entries[index] : null;
  // The Format menu: the files the server writes, then the reference list
  // in a citation style.
  const formatOption = ({ id, label }) => [id, label];
  const pickFormat = (bibFormat) => set({ format: "bibliography", bibFormat });
  let pane;
  if (!shown) pane = <p className="bibPaneEmpty">{t("Look up a paper's metadata with the (i) button to cite it.")}</p>;
  else if (!isFormatted) {
    // the entry in the format picked, once its answer is in
    pane = bib.loading ? <p className="bibPaneEmpty loadingHint">{t("Loading…")}</p>
      : <CopyBox copied={copied === shown.page_id} onCopy={() => onCopy(shown.text, shown.page_id)}
        title={t("Copy this entry")} label={t("Copy this entry")}>
        <pre className="bibtexPre bibtexPreview">{shown.text}</pre>
      </CopyBox>;
  } else if (formatted.error) {
    pane = <FormNotice>{formatted.error.status === 404
      ? t("This style is no longer on this server. Pick another one.")
      : t("Could not format the references: {message}", { message: formatted.error.message })}</FormNotice>;
  } else if (html == null) pane = <p className="bibPaneEmpty loadingHint">{t("Formatting…")}</p>;
  else {
    // The whole list as the file will hold it (its order, numbering and
    // spacing), the picked paper's entry tinted and scrolled to; the box's
    // button copies that entry.
    const list = <CitationHtml html={formatted.result.output} mark={html || null} className="bibFormattedPreview" />;
    pane = html ? (
      <CopyBox copied={copied === shown.page_id} onCopy={() => onCopyFormatted(index, shown.page_id)}
        title={t("Copy the highlighted reference")} label={t("Copy the highlighted reference")}>{list}</CopyBox>
    ) : <>
      <p className="bibPaneEmpty">{t("This style leaves this paper out of its bibliography.")}</p>
      {list}
    </>;
  }
  return <>
    <p className="reportModalHint">{entries.length
      ? tn("{n} paper can be cited.", "{n} papers can be cited.", entries.length)
      : t("Nothing here can be cited yet: no page carries paper metadata.")}</p>
    <div className="transferControls bibOptions">
      <Row icon={FileTextIcon} label={t("Format")} hint={t(format.hint)}>
        <MenuSelect label={t("Format")} value={format.id} display={t(format.label)} onChange={pickFormat}
          heading={t("File formats")} options={BIBLIOGRAPHY_FORMATS.filter((f) => f.server).map(formatOption)}
          sections={[{ label: t("In a citation style"), value: format.id, onChange: pickFormat,
            options: BIBLIOGRAPHY_FORMATS.filter((f) => !f.server).map(formatOption) }]} />
      </Row>
      {isFormatted ? <FormattedOptions formatted={formatted} set={set} /> : null}
      {controls.map((control) => <ExportControl key={control.key} control={control} payload={payload} set={set} />)}
    </div>
    <div className="bibColumns">
      <section aria-label={t("Papers")}>
        <h3>{t("Papers")}</h3>
        <div className="bibPaperList">
          {entries.map((entry) => (
            <button key={entry.page_id} type="button" className="bibPaperRow"
              aria-pressed={entry === shown} onClick={() => setPicked(entry.page_id)}>
              <span className="bibPaperTitle">{entry.title || t("Untitled")}</span>
              <span className="bibPaperKey">
                {entry.pinned ? <PinIcon filled size={12} aria-label={t("Pinned citation key")} /> : null}
                {entry.key}
              </span>
            </button>
          ))}
          {/* The pages that could not be cited, where the question "why is my
              paper missing?" is actually asked. */}
          {skipped.map((page) => (
            <div key={page.page_id || page.title} className="bibPaperRow bibPaperSkipped">
              <span className="bibPaperTitle">{page.title}</span>
              <span className="bibPaperKey">{t(page.reason)}</span>
            </div>
          ))}
        </div>
      </section>
      <section aria-label={paneTitle}>
        <h3>{paneTitle}</h3>
        <div className="bibEntryPane">{pane}</div>
      </section>
    </div>
    <div className="reportModalHint">{summary}</div>
    {saved ? <p className="reportModalHint">{t("Saved to your downloads.")}</p> : null}
    {notice ? <FormNotice>{notice}</FormNotice> : null}
    {format.server ? <KeepUpToDate url={bib.url} folder={folder} bib={!!format.bib} /> : null}
  </>;
}

// Keeping a document's bibliography in step with the library: the export
// endpoint answers the current file on every request, so a share link
// turns it into a URL Overleaf (or a Makefile, or curl) can refresh from.
// A .bib (`bib`) gets Overleaf's steps and the guide's hint; another file
// only the link. Sharing itself stays in the share popover, where the
// audience is chosen — this only reports whether a link exists.
function KeepUpToDate({ url, folder, bib }) {
  // A folder's .bib on screen: the guide's hint names this link.
  React.useEffect(() => { if (bib) guideEvents.emit("bibtex.shown", { folder: !!folder }); }, [bib, folder]);
  return <details className="transferHelp" data-guide={bib ? "transfer.bibLink" : undefined}>
    <summary>{bib ? t("Keep this .bib up to date") : t("Keep this file up to date")}</summary><div className="importSteps">
    {url ? <>
      <CopyField label={t("Bibliography link")} value={url} action={t("Copy the bibliography link")} rows={1} />
      {bib ? <>
        <Step n={1} title={t("In Overleaf: Upload → From External URL")}
          hint={t("Paste this link and name the file references.bib. The Refresh button next to it fetches the current entries again.")} />
        <Step n={2} title={t("Cite as usual")}
          hint={t("Use the citation keys above in your document. Pin a key in the (i) metadata popover to keep citing a paper by the name your document already uses.")} />
      </> : <p className="reportModalHint">{t("The link answers with the current file each time it is read, so a script or a tool that fetches a URL keeps it up to date instead of exporting it again by hand.")}</p>}
      {/* Whole sentences, not an interpolated noun: a fragment like "the page"
          has no context for a translator (docs/dev/i18n.md). */}
      <p className="reportModalHint">{folder
        ? t("Anyone with the link can read the folder — it is the share link, so changing or stopping the share changes this too.")
        : t("Anyone with the link can read the page — it is the share link, so changing or stopping the share changes this too.")}</p>
    </> : <>
      <p className="reportModalHint">{bib
        ? t("A share link makes this bibliography available at a fixed URL, which Overleaf and build scripts can refresh from instead of re-exporting by hand.")
        : t("A share link makes this file available at a fixed URL, which scripts and other tools can fetch again instead of re-exporting by hand.")}</p>
      <Step n={1} title={folder ? t("Share the folder") : t("Share the page")}
        hint={folder ? t("Use Share in the folder's ⋮ menu and pick who may open the link.")
          : t("Use Share in the page's menu and pick who may open the link.")} />
      <Step n={2} title={t("Come back here")} hint={bib ? t("The link to paste into Overleaf appears in this step.") : t("The link appears in this step.")} />
    </>}
  </div></details>;
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
// which a Formatted bibliography starts from.
function ExportSetup({ opts, setOpts, hasPdf, pdfStored, hasMeta, pageId, folder, folderName, subPages = 0, subPagesDirect = false,
  translated = null, busy = false, startStep = 0, onCancel, onExport, citationStyle = DEFAULT_STYLE, citationLocale = "en-US" }) {
  const [step, setStep] = React.useState(startStep);
  const context = { hasPdf, pdfStored, hasMeta, folder, subPages, subPagesDirect, translated };
  const resolved = resolveExport(opts, context);
  const { definition, formats, controls, needsReview, payload } = resolved;
  const { format, highlights, bundle, bibFormat } = payload;
  const isZotero = format === "zotero";
  const isBibliography = format === "bibliography";
  const reviewing = isBibliography && step === 1;
  const isFormatted = isBibliography && bibFormat === "formatted";
  const bib = useBibliography(reviewing, { pageId, folder, subPages: payload.subPages, mode: bibFormat });
  const formatted = useFormatted(reviewing && isFormatted, bib.data, opts, { citationStyle, citationLocale });
  const [copied, flashCopied] = useCopied();
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const [notice, setNotice] = React.useState("");
  const set = (patch) => setOpts((o) => ({ ...o, ...patch }));
  const advance = (id = format) => {
    const next = resolveExport({ ...opts, format: id }, context);
    set({ format: next.payload.format });
    if (next.needsReview) setStep(1);
    else onExport(next.payload);
  };
  const summary = exportSummary(resolved, folder);
  // The engine's chunk comes as the card is picked, ahead of the review
  // that may format with it.
  React.useEffect(() => { if (isBibliography) preloadCitations(); }, [isBibliography]);
  // A file saved is the one of the choices it was made with.
  React.useEffect(() => { setSaved(false); setNotice(""); }, [bibFormat, formatted.style, formatted.locale, formatted.file]);

  // A bibliography is small and text: copying it is often the whole job, so
  // the step offers that beside the download instead of making the file the
  // only way out. The footer copies the whole bibliography, the right-hand
  // pane one entry. A Formatted one goes as HTML and plain text, built in
  // the click (copyPayload), so a browser that wants the copy made in the
  // gesture still takes it while the render comes.
  const copy = (text, key = "all") => copyText(text).then((ok) => { if (ok) flashCopied(key); });
  const copyFormatted = (index, key = "all") => {
    setNotice("");
    copyPayload(formatted.pair(index))
      .then((ok) => (ok ? flashCopied(key) : setNotice(t("The browser did not let the copy through. Press the button again."))),
        (err) => setNotice(t("Could not format the references: {message}", { message: err.message })));
  };
  const entries = bib.data?.entries || [];
  // A file's Copy all copies the file of the format picked, once it is read.
  const copyAction = reviewing && entries.length > 1 ? <button type="button" className={`uiBtn ${copied === "all" ? "on" : ""}`}
    disabled={!isFormatted && bib.loading} onClick={() => (isFormatted ? copyFormatted(null) : copy(bib.data.text))}>
    <MorphText>{copied === "all" ? t("Copied") : t("Copy all")}</MorphText></button> : null;
  // A Formatted bibliography is made here, so its last step is the review:
  // Download saves the file with no export job.
  async function download() {
    setSaving(true); setNotice("");
    try {
      // named as the export names this bibliography's file (the answer's
      // `name`), titled by the folder or the page
      const name = bib.data?.name || t("Bibliography");
      const title = folder ? folderName : (entries.find((entry) => entry.page_id === pageId) || entries[0])?.title;
      const made = await render({ items: formatted.items, style: formatted.style, locale: formatted.locale, output: formatted.file });
      const { body, type, suffix } = formattedFile(formatted.file, made.output, title);
      if (await saveBlob(new Blob([body], { type }), `${name}${suffix}`)) setSaved(true);
      else setNotice(t("The file could not be saved."));
    } catch (err) {
      setNotice(t("Could not format the references: {message}", { message: err.message }));
    } finally { setSaving(false); }
  }
  // Only the review step waits on the bibliography — nothing has been fetched
  // while the format cards are still up, so Next must not be held there.
  const bibNotReady = reviewing && (bib.loading || !!bib.error || !entries.length
    || (isFormatted && (formatted.loading || !!formatted.error || saving)));
  // The host's wait holds the button only where it exports.
  const waiting = busy && (step === 1 || !needsReview);

  return <TransferDialog title={folder ? t("Export “{folder}”", { folder: folderName }) : t("Export")} step={step} setStep={setStep}
    firstTitle={t("Choose a format")} secondTitle={t(definition.label)} needsReview={needsReview} onContinue={() => advance()}
    onCancel={onCancel} actionLabel={isFormatted ? <MorphText>{saved ? t("Download again") : t("Download")}</MorphText> : t("Export")}
    action={isFormatted ? download : () => onExport(payload)} finalTitle={isFormatted ? null : t("Export")}
    secondaryAction={copyAction} wide={reviewing} busy={bibNotReady || waiting}>
    {step === 0 ? <>
      <FormatChoices label={t("Export format")} value={format} onChange={(format) => set({ format })} onConfirm={advance} options={formats} />
      {!needsReview ? <p className="reportModalHint">{summary}</p> : null}
    </> : isBibliography ? <BibliographyReview bib={bib} folder={folder} summary={summary} copied={copied} onCopy={copy}
      onCopyFormatted={copyFormatted} controls={controls} payload={payload} set={set} formatted={formatted} notice={notice} saved={saved} />
    : <>
      <p className="reportModalHint">{t("Choose what to include.")}</p>
      <div className="transferReview">
        <ExportPreview {...payload} filesRow={payload.format === "notion" ? notionFiles(payload) : null} />
        <div className="transferControls">
          {controls.map((control) => <ExportControl key={control.key} control={control} payload={payload} set={set} />)}
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
