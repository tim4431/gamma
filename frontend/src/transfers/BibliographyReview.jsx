// The Export dialog's Bibliography card past its format step (ExportSetup in
// ImportExport.jsx): the review of the papers and their entries, a
// reference list rendered in a citation style, its copies and its download,
// and the link that keeps a file up to date (docs/dev/import_export.md
// "Bibliography").
import React from "react";
import { FileIcon, FileTextIcon, PinIcon, QuoteMarkIcon } from "../shared/ui/Icons";
import { CopyField, FormNotice, Row, Segmented, Step } from "../settings/SettingsKit";
import { CitationHtml } from "../citations/CitationHtml";
import { CitationLanguageRow, styleMenuProps, useAddedStyles } from "../citations/CitationChoices";
import { CopyBox, useCopied } from "../shared/ui/Widgets";
import { MenuSelect } from "../shared/ui/Menus";
import { MorphText } from "../shared/ui/MotionWidgets";
import { copyPayload, copyText } from "../shared/lib/utils";
import { saveBlob } from "../shared/lib/saveFile.js";
import { DEFAULT_STYLE } from "../citations/bundledStyles.js";
import { styleInUse, styleKindLabel } from "../citations/citeRules.js";
import { preloadCitations, render, renderPair, styleMeta } from "../citations/render.js";
import { bibliographyPreview, shareToken, sharedBibliographyUrl } from "./exportApi";
import { BIBLIOGRAPHY_FORMATS, FORMATTED_FILES, bibliographyFormat, bibliographyMode, formattedFile } from "./transferFormats";
import { t, tn } from "../shared/i18n/i18n.js";
import { guideEvents } from "../guide/events.js";

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

// The Bibliography card's state, which the dialog's footer reads as well as
// its review. `card`: the card is picked; `reviewing`: its review is on
// screen. `set` patches the dialog's choices (`opts`), `payload` is
// resolveExport's. `citationStyle`, `citationLocale`: the account's
// (Settings → Citations), which a Formatted bibliography starts from.
// Answers `isFormatted`, the footer's `copyAction`, a Formatted
// bibliography's `download` and its `downloadLabel`, `notReady` (the
// review holds the button that acts), and `review`, BibliographyReview's
// props but the summary and the switches.
export function useBibliographyStep({ card, reviewing, opts, set, payload, pageId, folder, folderName,
  citationStyle = DEFAULT_STYLE, citationLocale = "en-US" }) {
  const { bibFormat } = payload;
  const isFormatted = card && bibFormat === "formatted";
  const bib = useBibliography(reviewing, { pageId, folder, subPages: payload.subPages, mode: bibFormat });
  const formatted = useFormatted(reviewing && isFormatted, bib.data, opts, { citationStyle, citationLocale });
  const [copied, flashCopied] = useCopied();
  const [saving, setSaving] = React.useState(false);
  const [saved, setSaved] = React.useState(false);
  const [notice, setNotice] = React.useState("");
  // The engine's chunk comes as the card is picked, ahead of the review
  // that may format with it.
  React.useEffect(() => { if (card) preloadCitations(); }, [card]);
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
  const notReady = reviewing && (bib.loading || !!bib.error || !entries.length
    || (isFormatted && (formatted.loading || !!formatted.error || saving)));
  return { isFormatted, copyAction, download, notReady,
    downloadLabel: <MorphText>{saved ? t("Download again") : t("Download")}</MorphText>,
    review: { bib, folder, copied, onCopy: copy, onCopyFormatted: copyFormatted, payload, set, formatted, notice, saved } };
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
// `controls`: the export's switch rows (ExportControl; for a bibliography
// only "Include pages inside", resolveExport). `onCopy(text, key)` copies a
// file's text, `onCopyFormatted(index)` a Formatted entry; `notice` is a
// Formatted copy's or download's failure, `saved` that its file was saved.
export function BibliographyReview({ bib, folder, summary, copied, onCopy, onCopyFormatted, controls, payload, set, formatted, notice, saved }) {
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
      {controls}
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
