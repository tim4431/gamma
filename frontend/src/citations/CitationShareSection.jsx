// The share popover's Citation section for a page (docs/dev/ui-design.md
// "The share popover", citations.md), which App hands the popover. Every
// part starts at the section's one left edge:
// - Reference: the page's entry in the default style, copied rich (HTML and
//   text); its style a MenuSelect over the installed styles (your styles,
//   then the bundled ones by class: CitationChoices.jsx styleMenuProps),
//   named short on its trigger and ending, for an account, in More
//   styles… (Settings → Citations › Your styles, the entry point
//   citation-styles). The line wraps on a phone, the picker under the
//   label. Under the box Copy citation, the in-text citation beside it,
//   except for a numeric style, whose [1] means nothing alone. A page whose
//   answer has no entry (a stored entry that is no valid BibTeX, and no
//   record) says it has nothing to cite.
// - Slide citation: the AI-made one, with its own Regenerate; with no AI
//   service connected, a line saying so and Set up AI….
// - BibTeX: the entry the .bib export prints.
// The section's action, Copy as ▾, copies the page in the other formats
// (BibLaTeX, RIS, CSL JSON and the key commands, in the menus' groups).
// The record's provenance, and the red "!" of an unverified one, sit by
// the Reference, where it gets copied.
import React, { useEffect, useState } from "react";
import { T, t } from "../shared/i18n/i18n.js";
import { ChatMarkdown, CopyBox, useCopied } from "../shared/ui/Widgets";
import { CheckIcon, QuoteMarkIcon, RefreshIcon, SettingsIcon } from "../shared/ui/Icons";
import { ActionMenu, MenuSelect } from "../shared/ui/Menus";
import { IconSwap } from "../shared/ui/MotionWidgets";
import { Section } from "../settings/SettingsKit";
import { CitationHtml } from "./CitationHtml";
import { styleMenuProps } from "./CitationChoices";
import { copyAsGroups, formatName } from "./formats.js";

// What Copy as ▾ offers here: every format but BibTeX's, which has its
// box, under the menus' group labels.
const COPY_AS = copyAsGroups(["bibtex"]);

// `cite` is useCitations' answer (its shareEntry the page's); `pageBibtex`
// the stored entry; `pptCite`, `pptCiteBusy`, `onRegenerate` the slide
// citation, which `aiReady` (an AI service connected) can make, else
// `onSetUpAi` opens Settings at it; `metaSrc` the record's provenance
// (utils.metaSourceInfo); `copied` and `onCopy(kind, text)` App's copy with
// its tick for the slide citation ("ppt") and BibTeX ("bibtex");
// `onMoreStyles` opens Settings at Citations › Your styles (offered to an
// account alone, cite.account).
export function CitationShareSection({ cite, pageBibtex, pptCite, pptCiteBusy, onRegenerate, aiReady = true, onSetUpAi = null,
  metaSrc, copied, onCopy, onMoreStyles }) {
  const entry = cite.shareEntry;
  const none = cite.shareLoaded && !entry && !cite.shareFailed;  // the answer has nothing to cite
  const copyAs = (as, flash = null) => cite.copyPage(cite.sharedPage, as, flash);
  // The entry in the style, and its in-text citation; kept while the next
  // style renders, so the box does not blink.
  const [shown, setShown] = useState(null); // {html, citation} | {failed: true}
  const [tick, flash] = useCopied();
  useEffect(() => {
    if (!entry?.csl) { setShown(null); return undefined; }
    let live = true;
    Promise.all([cite.render({ items: [entry.csl] }), cite.render({ items: [entry.csl], mode: "citation" })])
      .then(([bibliography, inText]) => { if (live) setShown({ html: bibliography.html, citation: inText.text }); })
      .catch(() => { if (live) setShown({ failed: true }); });
    return () => { live = false; };
  }, [entry, cite.style, cite.locale]);
  const printed = cite.printedBibtex || pageBibtex;
  const copyItems = COPY_AS.flatMap((group) => [{ heading: group.label },
    ...group.formats.map((format) => ({ label: formatName(format), onClick: () => copyAs(format) }))]);
  // The slide citation: made, being made, waiting for a model, or not yet.
  const slide = pptCite ? (
    <CopyBox copied={copied === "ppt"} onCopy={() => onCopy("ppt", pptCite)}
      title={t("Copy — pastes with real italics/bold into PowerPoint")} label={t("Copy slide citation")}>
      <div className="pptCitePreview"><ChatMarkdown text={pptCite} /></div>
    </CopyBox>
  ) : (
    <div className="settingsPaneHint citeHint">
      {pptCiteBusy ? t("Generating…") : aiReady ? t("Not made yet.") : t("An AI service writes it once one is connected.")}
      {!aiReady && !pptCiteBusy && onSetUpAi ? (
        <button type="button" className="uiBtn ghost sm" onClick={onSetUpAi}>{t("Set up AI…")}</button>
      ) : null}
    </div>
  );
  return (
    <Section
      title={t("Citation")}
      action={none ? null : <ActionMenu label={T("Copy as")} items={copyItems} />}
    >
      <div className="citeHead">
        <span className="citeLabel">{t("Reference")}</span>
        {/* Provenance right where the citation gets copied: a registry
            name, or a red "!" when nothing tied the record to this
            document. */}
        {metaSrc ? (
          <span className={`citeSourceTag${metaSrc.warn ? " warn" : ""}`} title={t(metaSrc.hint)}>
            {metaSrc.warn ? <span className="metaWarnDot inline" aria-hidden="true">!</span> : null}
            {t(metaSrc.label)}
          </span>
        ) : null}
        <span className="citeHeadEnd">
          <MenuSelect label={t("Citation style")} {...styleMenuProps(cite.added, cite.style, cite.setStyle)}
            actions={cite.account && onMoreStyles ? [{ icon: SettingsIcon, label: T("More styles…"),
              title: T("Add a style from the CSL repository or a file"), onClick: onMoreStyles }] : []} />
        </span>
      </div>
      {metaSrc?.warn ? <div className="settingsPaneHint citeWarnHint">{t(metaSrc.hint)}.</div> : null}
      {shown?.html ? (
        <CopyBox copied={tick === "ref"} onCopy={() => copyAs({ mode: "bibliography" }, () => flash("ref"))}
          title={t("Copy the reference — pastes formatted into a document")} label={t("Copy reference")}>
          <CitationHtml html={shown.html} className="citeRefPreview" />
        </CopyBox>
      ) : (
        <div className={`settingsPaneHint citeHint${none || shown?.failed || cite.shareFailed ? "" : " loadingHint"}`}>
          {none ? t("This page has no paper metadata to cite.")
            : shown?.failed || cite.shareFailed ? t("The reference could not be formatted.") : t("Formatting…")}
        </div>
      )}
      {shown?.citation && cite.styleKind !== "numeric" ? (
        <div className="citeInText">
          <button type="button" className="uiBtn sm" onClick={() => copyAs({ mode: "citation" }, () => flash("cite"))}
            title={t("The citation in the text, in your citation style")}>
            <IconSwap swap={tick === "cite"} from={<QuoteMarkIcon size={14} />} to={<CheckIcon size={14} />} />
            {t("Copy citation")}
          </button>
          <span className="citeInTextPreview" title={shown.citation}>{shown.citation}</span>
        </div>
      ) : null}
      <div className="citeHead">
        <span className="citeLabel">{t("Slide citation")}</span>
        {aiReady || pptCite ? (
          <span className="citeHeadEnd">
            <button type="button" className="uiBtn sm iconSq" title={t("Regenerate the citation")} aria-label={t("Regenerate the citation")}
              disabled={pptCiteBusy || !aiReady} onClick={onRegenerate}>{pptCiteBusy ? "…" : <RefreshIcon size={16} />}</button>
          </span>
        ) : null}
      </div>
      {slide}
      {pageBibtex ? (
        <>
          <div className="citeHead"><span className="citeLabel">{t("BibTeX")}</span></div>
          <CopyBox copied={copied === "bibtex"} onCopy={() => onCopy("bibtex", printed)}
            title={t("Copy the BibTeX entry")} label={t("Copy BibTeX")}>
            <pre className="bibtexPre">{printed}</pre>
          </CopyBox>
        </>
      ) : null}
    </Section>
  );
}
