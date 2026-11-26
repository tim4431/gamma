// Settings → Citations (docs/dev/citations.md), Zotero's Cite pane: the
// account's citation style (the bundled ones and those it added, in
// sections by class), with a preview of a fixed sample in it; the language
// its terms and dates are set in, unless the style sets its own (Zotero
// 9.0.6); what Copy bibliography and its shortcut give (Quick Copy); and
// the styles the account added on this server, with Add style… (the CSL
// repository, or a .csl file). The three choices are account preferences
// (app/prefDefs.js, `value` is App's); the styles are rows on this server
// (gamma/routers/citations.py), so a style the profile names may be missing
// here: citations then use the default style (citeRules.js styleInUse, the
// rule every citation follows), which the Style row says, with Add it again
// where the repository can be asked.
import React from "react";
import { addStyle, loadStyles, removeStyle } from "../citations/citationApi.js";
import { DEFAULT_STYLE } from "../citations/bundledStyles.js";
import { styleInUse, styleKindLabel } from "../citations/citeRules.js";
import { QUICK_COPY_FORMATS, formatLabel } from "../citations/formats.js";
import { render, styleMeta } from "../citations/render.js";
import { copyAsGroups } from "../citations/CitationMenuRows";
import { MenuSelect } from "../shared/ui/Menus";
import { ClipboardIcon, PlusIcon, QuoteMarkIcon, TrashIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { Empty, FormNotice, Row, Section } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { CitationHtml } from "../citations/CitationHtml";
import { CitationLanguageRow, styleLabel, styleMenuProps, useAddedStyles } from "../citations/CitationChoices";
import { AddStyleDialog } from "./CitationStyles";
import { T, t } from "../shared/i18n/i18n.js";

// What the preview cites: a journal article by one author (with a title
// in German, which a style's own locale leaves as it is), one by two, and
// a book, so the preview shows a style's names, its "and", its container
// and its numbering.
const SAMPLE = [
  { id: "einstein1905", type: "article-journal", title: "Zur Elektrodynamik bewegter Körper", language: "de",
    author: [{ family: "Einstein", given: "Albert" }], "container-title": "Annalen der Physik",
    volume: "322", issue: "10", page: "891-921", issued: { "date-parts": [[1905]] }, DOI: "10.1002/andp.19053221004" },
  { id: "watson1953", type: "article-journal",
    title: "Molecular structure of nucleic acids: A structure for deoxyribose nucleic acid",
    author: [{ family: "Watson", given: "James D." }, { family: "Crick", given: "Francis H. C." }],
    "container-title": "Nature", volume: "171", issue: "4356", page: "737-738", issued: { "date-parts": [[1953, 4, 25]] },
    DOI: "10.1038/171737a0" },
  { id: "kuhn1962", type: "book", title: "The structure of scientific revolutions", author: [{ family: "Kuhn", given: "Thomas S." }],
    publisher: "University of Chicago Press", "publisher-place": "Chicago", issued: { "date-parts": [[1962]] } },
];

// The sample in `style` and `locale`: its citation (a note style's note)
// and its bibliography, a card under the Style row level with the rows'
// text. The last render stays on screen while the next one is made, so
// changing the style does not make the box jump.
function StylePreview({ style, locale }) {
  const [shown, setShown] = React.useState(null);  // {citation, bibliography, note} | {error}
  React.useEffect(() => {
    let live = true;
    Promise.all([render({ items: SAMPLE, style, locale, mode: "citation" }), render({ items: SAMPLE, style, locale })])
      .then(([cite, list]) => { if (live) setShown({ citation: cite.output, bibliography: list.output, note: cite.styleClass === "note" }); })
      .catch((err) => { if (live) setShown({ error: err.message }); });
    return () => { live = false; };
  }, [style, locale]);
  if (shown?.error) return <FormNotice>{t("Could not show this style: {message}", { message: shown.error })}</FormNotice>;
  return (
    <div className="setSubRows citeSampleRows">
      <div className={`setCard citeSample${shown ? "" : " loadingHint"}`} aria-label={t("Preview of the style")} role="group">
        {shown ? <>
          <span className="citeLabel">{shown.note ? t("In a note") : t("In the text")}</span>
          <CitationHtml html={shown.citation} />
          <span className="citeLabel">{t("In the bibliography")}</span>
          <CitationHtml html={shown.bibliography} />
        </> : <span className="settingsPaneHint">{t("Loading…")}</span>}
      </div>
    </div>
  );
}

// The project and the license the credit names: names, not interface text.
const CSL_PROJECT = "Citation Style Language"; // i18n-ignore: a name
const CSL_LICENSE = "CC BY-SA 3.0"; // i18n-ignore: a name

// The Quick Copy MenuSelect's props, its menu as Zotero's Item Format is:
// the style in use under Citation style, by its short name (`style`),
// apart from the file formats and the key commands, each under its label
// (CitationMenuRows copyAsGroups). Translated here: the menu shows names.
function quickCopyProps(style, value, onChange) {
  const label = (format) => (format === "style" ? styleLabel(style) : formatLabel(format));
  const groups = copyAsGroups(QUICK_COPY_FORMATS.filter((format) => format !== "style"));
  return {
    value, onChange, names: true, display: label(value),
    heading: t("Citation style"), options: [["style", label("style")]],
    sections: groups.map((group) => ({ label: t(group.label), value, onChange,
      options: group.formats.map((format) => [format, label(format)]) })),
  };
}

// "Numeric · from the CSL repository": an added style's class and source.
function addedHint(row) {
  const kind = styleKindLabel(row.kind) || styleKindLabel("author-date");
  return row.source === "file" ? t("{kind} · from a .csl file", { kind }) : t("{kind} · from the CSL repository", { kind });
}

export function CitationSettings({ value, setStatus }) {
  const { citationStyle, setCitationStyle, citationLocale, setCitationLocale, quickCopyFormat, setQuickCopyFormat } = value;
  // Read again on each visit (another device may have changed it), the
  // list held shown meanwhile; an add or a removal here changes it itself.
  const added = useAddedStyles({ fresh: true });
  const [adding, setAdding] = React.useState(false);
  const [busy, setBusy] = React.useState("");
  const [error, setError] = React.useState("");  // a removal's, in Your styles
  const [againError, setAgainError] = React.useState("");  // Add it again's, under the Style row
  // The profile's style, or the default where this server lacks it.
  const { style, gone: missing } = styleInUse({ wanted: citationStyle, list: added });
  const fallback = styleMeta(DEFAULT_STYLE).title;

  async function addAgain() {
    setBusy("again"); setAgainError("");
    try {
      const row = await addStyle(citationStyle);
      if (row.id !== citationStyle) setCitationStyle(row.id);  // renamed in the repository
      setStatus?.(t("Added {style}.", { style: row.title }));
    } catch (err) { setAgainError(err.message); }
    finally { setBusy(""); }
  }
  async function remove(row) {
    setBusy(row.id); setError("");
    try {
      await removeStyle(row.id);
      if (row.id === citationStyle) {
        setCitationStyle(DEFAULT_STYLE);
        setStatus?.(t("Removed {style}. Citations use {fallback} again.", { style: row.title, fallback }));
      } else setStatus?.(t("Removed {style}.", { style: row.title }));
    } catch (err) { setError(err.message); }
    finally { setBusy(""); }
  }
  const onAdded = (row) => {
    setStatus?.(t("Added {style}.", { style: row.title }), { label: t("Use it"), run: () => setCitationStyle(row.id) });
  };
  // Add style… waits for the list, which says whether the repository may
  // be asked here and how many styles the account may still add.
  const addButton = added.own ? (
    <button type="button" className="uiBtn sm" disabled={!added.loaded} onClick={() => setAdding(true)}><PlusIcon size={14} />{t("Add style…")}</button>
  ) : null;

  return (<>
    <Section title={t("Citations")} scope="account" prefs={SECTION_PREFS.citations["Citations"]}>
      <Row icon={QuoteMarkIcon} label={t("Style")} hint={t("Used for every citation and bibliography you copy")}
        title={t("Copy citation and Copy bibliography, the share popover's reference and a quote copied with its citation all use it. Add more styles under Your styles.")}>
        <MenuSelect label={t("Citation style")} {...styleMenuProps(added.styles, style, setCitationStyle)}
          actions={added.own ? [{ icon: PlusIcon, label: T("Add style…"), disabled: !added.loaded, onClick: () => setAdding(true) }] : []} />
      </Row>
      {missing ? (
        <FormNotice tone="warn">
          {added.downloads
            ? t("“{style}” is not on this server, so citations use {fallback}.", { style: citationStyle, fallback })
            : t("“{style}” is not on this server, so citations use {fallback}. Add its .csl file under Your styles to use it here.", { style: citationStyle, fallback })}
          {added.downloads ? <>{" "}<button type="button" className="uiBtn sm" disabled={busy === "again"} onClick={addAgain}>
            <MorphText>{busy === "again" ? t("Adding…") : t("Add it again")}</MorphText></button></> : null}
        </FormNotice>
      ) : null}
      {missing && againError ? <FormNotice>{againError}</FormNotice> : null}
      <StylePreview style={style} locale={citationLocale} />
      <CitationLanguageRow style={style} value={citationLocale} onChange={setCitationLocale}
        title={t("The words a style prints (and, edited by, accessed) and how it writes dates. A style made for one language, as many journals' are, sets its own, and this choice does not change it.")} />
      <Row icon={ClipboardIcon} label={t("Quick Copy")} hint={t("What Copy bibliography and its shortcut give")}
        title={t("The style gives the formatted bibliography, its formatting kept for a word processor. BibTeX, BibLaTeX, RIS and CSL JSON give the entries as text, and the LaTeX, Pandoc and Typst commands cite the papers' keys.")}>
        <MenuSelect label={t("Quick Copy")} {...quickCopyProps(style, quickCopyFormat, setQuickCopyFormat)} />
      </Row>
    </Section>

    <Section title={t("Your styles")} action={addButton}>
      {added.error ? (
        <FormNotice>
          {t("Could not read your styles: {message}", { message: added.error })}{" "}
          <button type="button" className="uiBtn sm" onClick={() => loadStyles({ force: true })}>{t("Try again")}</button>
        </FormNotice>
      ) : null}
      {error ? <FormNotice>{error}</FormNotice> : null}
      {!added.loaded ? <Empty icon={QuoteMarkIcon} loading>{t("Loading…")}</Empty>
        : !added.own ? <Empty icon={QuoteMarkIcon}>{t("Styles of your own need an account. The styles Gamma carries are in the Style menu.")}</Empty>
          : !added.styles.length && !added.error ? (
            <Empty icon={QuoteMarkIcon}>{t("Add any of the CSL repository's 10,000 styles, or a style's .csl file, and it joins the Style menu.")}</Empty>
          ) : null}
      {added.styles.map((row) => (
        <Row key={row.id} icon={QuoteMarkIcon} label={row.title} hint={addedHint(row)} title={row.short_title || row.id}>
          <span className="setRowControls">
            {row.id === citationStyle ? <span className="uiTag">{t("In use")}</span> : (
              <button type="button" className="uiBtn sm" title={t("Make it your citation style")}
                onClick={() => setCitationStyle(row.id)}>{t("Use")}</button>
            )}
            <button type="button" className="uiBtn sm iconSq danger" disabled={busy === row.id}
              aria-label={t("Remove {style}", { style: row.title })} title={t("Remove {style}", { style: row.title })}
              onClick={() => remove(row)}><TrashIcon size={14} /></button>
          </span>
        </Row>
      ))}
      {/* The styles' and locales' license (CC BY-SA 3.0) asks for the
          credit; citations/styles/README.md says this is where it is. */}
      <p className="settingsPaneHint citeCredit">{t("The styles and their languages come from the {project} project, under {license}.", {
        project: <a href="https://citationstyles.org/" target="_blank" rel="noreferrer">{CSL_PROJECT}</a>,
        license: <a href="https://creativecommons.org/licenses/by-sa/3.0/" target="_blank" rel="noreferrer">{CSL_LICENSE}</a>,
      })}</p>
    </Section>
    {adding ? (
      <AddStyleDialog added={added.styles} limit={added.limit} downloads={added.downloads}
        onAdded={onAdded} onClose={() => setAdding(false)} />
    ) : null}
  </>);
}
