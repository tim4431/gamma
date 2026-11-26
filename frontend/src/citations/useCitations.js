// The app's citations (docs/dev/citations.md): the account's style,
// language and Quick Copy format, the bibliography answers a copy is made
// from, and the copies themselves — the page and selection menus' Copy
// citation, Copy bibliography and Copy as ▸, a folder's Copy bibliography,
// the share popover's Citation section, the metadata popover's Reference
// line and a quote's Copy with citation, all through one path (copyFrom,
// then put). App calls it once and hands the answer on (frontend-refactor.md
// "New state"); the rules are citeRules.js, the account's added styles
// citationApi.js's list.
//
// Every copy is made in the click (copyPayload): the answer and the render
// it waits on are promised to the clipboard, so Safari and the iPad keep
// the gesture. A clipboard that still refuses gets the status pill's Copy,
// a fresh gesture, over the same render. Menus prefetch their answer when
// they open; a folder is not prefetched. The engine is a lazy chunk
// (render.js): preloadCitations() on pointer-enter or focus of what will
// copy.
//
// useCitations({prefs, setStatus, account, known, selected, order,
//   homeMode, openPage, keyFolder, sharedPage, pageBibtex, pageRecord})
//   prefs       App's appPrefs (citationStyle, citationLocale, quickCopyFormat)
//   account     signed in with an account of this server (not a guest, a
//               share view): only it has added styles
//   known       the page rows the app holds (knownBlocks), whose
//               properties.meta says which pages have a record
//   selected    the library's selected page ids; order the visible page rows
//               (their _pageId), the order a selection is copied in
//   homeMode, openPage  what the commands act on: the selection, or the
//               open page; keyFolder the folder the library shows (its keys)
//   sharedPage  the page the share popover is open on, else ""; pageBibtex
//               and pageRecord the open page's stored entry and record, whose
//               change (a save) drops the answers kept
import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { copyPayload } from "../shared/lib/utils";
import { bibliographyFor, bibliographyPreview } from "../transfers/exportApi";
import { loadStyles, styleList, subscribeStyles } from "./citationApi.js";
import { DEFAULT_STYLE, isBundledStyle } from "./bundledStyles.js";
import { styleLabel } from "./CitationChoices";
import { FILE_FORMATS, formatLabel, keyCommand, pageLocator } from "./formats.js";
import { preloadCitations, renderPair, styleMeta } from "./render.js";
import { copyPlan, inListOrder, quotePayload, quoteStyle, skippedNote, styleInUse } from "./citeRules.js";

// --- Bibliography answers -----------------------------------------------------------

// The answers asked for lately, by what they cover and their format: a
// menu's prefetch and the click that follows take one request. Kept
// briefly, so a record edited elsewhere is read again; an edit of the open
// page drops them all at once (any of them may hold it).
const ANSWER_MS = 30000;
const answers = new Map(); // key -> {at, promise}
function cached(key, ask) {
  const hit = answers.get(key);
  if (hit && Date.now() - hit.at < ANSWER_MS) return hit.promise;
  const promise = ask();
  answers.set(key, { at: Date.now(), promise });
  promise.catch(() => { if (answers.get(key)?.promise === promise) answers.delete(key); });
  if (answers.size > 24) answers.delete(answers.keys().next().value);
  return promise;
}
// A page's own answer (GET), the one the share popover, the Reference line
// and a quote read: its BibTeX entry and its CSL item.
const pageAnswer = (id) => cached(`page:${id}`, () => bibliographyPreview({ pageId: id }));
// The answer a copy of `target` reads, `file` the file format whose text
// it copies ("": a render or a key command, which read the CSL items and
// the keys every answer has). `target` is {folder} (every paper filed in
// it: the folder's file), or {ids, keyFolder} (pages in that order, keyed
// as keyFolder's .bib keys them: a POST, whose text is the entries alone;
// one page with no file to copy is its own answer).
function answerFor({ folder = "", ids = [], keyFolder = "" }, file = "") {
  const format = file || "bibtex";
  if (folder) return cached(`folder:${format}:${folder}`, () => bibliographyPreview({ folder, format }));
  if (!file && ids.length === 1 && !keyFolder) return pageAnswer(ids[0]);
  return cached(`pages:${format}:${keyFolder}:${ids.join(",")}`, () => bibliographyFor({ pageIds: ids, keyFolder, format }));
}
const fileOf = (format) => (FILE_FORMATS.includes(format) ? format : "");

// What a quote's page is read from without the viewer's labelsRef (no PDF
// on screen): no labels, no length, so the PDF page itself.
const NO_LABELS = { labels: null, pageCount: 0 };
// The list of a viewer with no account: no added styles.
const NO_STYLES = Object.freeze({ loaded: true, styles: [], own: false, error: "", missing: [] });

// A citation as a pill repeats it, "" when it is too long to (a note
// style's footnote): the pill then names the style instead.
const SAID_LENGTH = 60;
function saidCitation(text) {
  const words = String(text || "").replace(/\s+/g, " ").trim();
  return words.length <= SAID_LENGTH ? words : "";
}

// What the pill says of a render copied as `mode` of `n` papers in
// `style`: the citation itself, "Bibliography of 3 papers copied (APA 7th)".
function saidOfRender(mode, n, pair) {
  const style = styleLabel(pair.style);
  if (mode !== "citation") return tn("Bibliography of {n} paper copied ({style}).", "Bibliography of {n} papers copied ({style}).", n, { style });
  const citation = saidCitation(pair.text);
  return citation ? t("Copied {citation}.", { citation }) : t("Citation copied ({style}).", { style });
}

export function useCitations({ prefs, setStatus, account = false, known = [], selected = null, order = [],
  homeMode = false, openPage = "", keyFolder = "", sharedPage = "", pageBibtex = "", pageRecord = null }) {
  const { citationStyle: wanted, citationLocale: locale, quickCopyFormat: quickCopy } = prefs;
  // The account's added styles, read once a menu opens, the share popover
  // opens or the default is no bundled one; a default the list held lacks
  // (picked on another device) has it read again first.
  const held = useSyncExternalStore(subscribeStyles, styleList);
  const list = account ? held : NO_STYLES;
  const askStyles = () => { if (account) loadStyles(); };
  useEffect(() => {
    if (account && !isBundledStyle(wanted)) loadStyles({ force: held.loaded && !held.styles.some((row) => row.id === wanted) });
  }, [wanted, account]);
  // A default this server does not hold (a profile synced from another
  // server): citations use APA, and Settings → Citations says so.
  const { style } = styleInUse({ wanted, list });
  const styleKind = styleMeta(style).kind;

  // The pages with a record: rows are offered only where one has.
  const withMeta = useMemo(() => new Set(known.filter((b) => b?.properties?.meta).map((b) => b.id)), [known]);

  // The share popover's page: its answer, whose BibTeX entry is what the
  // .bib export prints (bibtex.page_entry, valid BibTeX however the stored
  // entry was written), and whose CSL item the section renders. Asked for
  // when the popover opens and again after an edit changes the stored
  // entry or the record (a URL or a journal abbreviation is in no BibTeX
  // entry), which also drops the answers kept: in a layout effect, so
  // before any reader's own effect asks for one again (the metadata
  // popover's Reference line).
  const [shareAnswer, setShareAnswer] = useState(null);
  const recordKey = pageRecord ? JSON.stringify(pageRecord) : "";
  useLayoutEffect(() => { answers.clear(); }, [openPage, pageBibtex, recordKey]);
  useEffect(() => {
    setShareAnswer(null);
    if (!sharedPage) return undefined;
    preloadCitations();
    askStyles();
    let live = true;
    pageAnswer(sharedPage)
      .then((data) => { if (live) setShareAnswer(data); })
      .catch(() => { if (live) setShareAnswer({ failed: true }); }); // the stored entry stays on show
    return () => { live = false; };
  }, [sharedPage, pageBibtex, recordKey]);

  const labelsRef = useRef(null);

  // Both flavours in a style, and the style they were made in (`style`):
  // an added style no longer on this server renders in APA (citationApi
  // remembers it gone, so the next citation is made in APA at once).
  async function renderBoth(opts) {
    const full = { style, locale, ...opts };
    let pair;
    try {
      pair = await renderPair(full);
    } catch (err) {
      if (err?.status !== 404 || full.style === DEFAULT_STYLE) throw err;
      full.style = DEFAULT_STYLE;
      pair = await renderPair(full);
    }
    pair.style = full.style;
    return pair;
  }

  // Puts `made` (a promise of {value: {text, html?}, said, note}) on the
  // clipboard, then says what was copied (`said`) and the note, or with
  // `flash` ticks the button instead. A refusal offers Copy on the pill.
  function put(made, { html, flash = null }) {
    made.catch(() => {});
    copyPayload(made.then((m) => m.value), { html }).then((ok) => {
      if (!ok) {
        setStatus(t("The clipboard did not take the copy."), { label: t("Copy"), run: () => put(made, { html, flash }) });
        return;
      }
      made.then(({ note, said }) => {
        if (flash) flash();
        if (!flash || note) setStatus([flash ? "" : said, note].filter(Boolean).join(" "));
      });
    }, (err) => setStatus(err?.message || t("Copy failed — copy manually.")));
  }

  // Copies `target` (answerFor's) as `as` says (citeRules.copyPlan): the
  // pages asked, in that order, or every paper of a folder. The pages
  // without a record are counted in the note, and so is a style that is not
  // on this server; none with a record is the failure the pill says.
  function copyFrom(target, as, { flash = null } = {}) {
    const plan = copyPlan(as, quickCopy, style);
    const file = fileOf(plan.format);
    const made = answerFor(target, file).then(async (answer) => {
      const entries = answer?.entries || [];
      const byPage = new Map(entries.map((entry) => [entry.page_id, entry]));
      const ids = target.folder ? entries.map((entry) => entry.page_id) : [...new Set(target.ids)];
      const cited = ids.map((id) => byPage.get(id)).filter(Boolean);
      const total = target.folder ? ids.length + (answer?.skipped?.length || 0) : ids.length;
      const note = skippedNote(total, total - cited.length);
      if (!cited.length) throw new Error(note || t("None of these pages has paper metadata to cite."));
      const said = plan.format ? t("Copied as {format}.", { format: formatLabel(plan.format) }) : "";
      if (file) return { value: { text: answer.text || "" }, said, note };
      if (plan.format) return { value: { text: keyCommand(plan.format, cited.map((entry) => entry.key)) }, said, note };
      const pair = await renderBoth({ items: cited.map((entry) => entry.csl), style: plan.style, mode: plan.mode });
      const fell = pair.style === plan.style ? ""
        : t("{style} is not on this server, so it was copied in {fallback}.", { style: styleMeta(plan.style).title, fallback: styleMeta(pair.style).title });
      return { value: pair, said: saidOfRender(plan.mode, cited.length, pair), note: [fell, note].filter(Boolean).join(" ") };
    }, (err) => { throw new Error(t("Could not read the citations: {message}", { message: err?.message || "failed" })); });
    put(made, { html: !plan.format, flash });
  }

  const ordered = (ids) => inListOrder(ids, order.map((b) => b?._pageId || b?.id));
  const copyPages = (ids, as, folder = "") => copyFrom({ ids: ordered(ids), keyFolder: folder }, as);
  // One page (the share popover's, the open page's), as `as` (flash: the
  // tick of the button that copied).
  const copyPage = (id, as, flash = null) => id && copyFrom({ ids: [id] }, as, { flash });

  return {
    style, styleKind, locale, quickCopy, account, added: list.styles,
    labelsRef,
    sharedPage,
    printedBibtex: shareAnswer?.entries?.[0]?.text || "",
    shareEntry: shareAnswer?.entries?.[0] || null,
    shareLoaded: !!shareAnswer,
    shareFailed: !!shareAnswer?.failed,
    // The share popover's pick: the account's style, so the pill says so
    // and offers it back.
    setStyle(id) {
      if (id === wanted) return;
      const before = wanted;
      prefs.setCitationStyle(id);
      setStatus(t("Citations now use {style}.", { style: styleLabel(id) }), { label: t("Undo"), run: () => prefs.setCitationStyle(before) });
    },
    hasRecord: (ids) => ids.some((id) => withMeta.has(id)),
    // Whether a page filed in one of `folders` (a folder and the ones below
    // it, as its bibliography gathers them) has a record.
    hasRecordIn: (folders) => known.some((b) => withMeta.has(b.id) && (b.properties?.folders || []).some((f) => folders.has(f))),
    // A menu opening on these pages: the engine, the added styles and the
    // answer its Copy bibliography will copy from.
    prefetch(ids, folder = "") {
      preloadCitations();
      askStyles();
      answerFor({ ids: ordered(ids), keyFolder: folder }, fileOf(quickCopy)).catch(() => {});
    },
    copyPages,
    copyPage,
    copyFolder: (id, as = "bibliography") => copyFrom({ folder: id }, as),
    // The commands (app.copyCitation, app.copyBibliography): the library's
    // selection in the list's order, else the open page.
    copySelection(bibliography) {
      const as = bibliography ? "bibliography" : "citation";
      if (homeMode) { if (selected?.size) copyPages([...selected], as, keyFolder); }
      else copyPage(openPage, as);
    },
    // The entry of the open page (the metadata popover's Reference line).
    pageEntry: (id) => pageAnswer(id).then((answer) => answer?.entries?.[0] || null),
    // A render in the account's style and language (opts as render.js's).
    render: renderBoth,
    // What Copy with citation cites in, as its row's title says.
    quoteHint: quoteStyle(style, styleKind) === style
      ? t("The passage in quotes, with its citation in {style}", { style: styleMeta(style).title })
      : t("The passage in quotes, with its citation in APA: {style} does not cite by name in the text", { style: styleMeta(style).title }),
    // A quote from the open page's PDF with its citation: “quote” (Smith,
    // 2020, p. 347), in the default style when it cites by name, else APA.
    // `pageNumber` (from 1) is where it is, null for a highlight that lost
    // its place: no locator then. A page with nothing to cite (no record,
    // no stored entry) copies the quote alone and says why; one with a
    // stored entry alone is cited by its title, as the server cites it.
    copyQuote(quote, pageNumber = null) {
      const page = openPage;
      const made = (async () => {
        const answer = await pageAnswer(page).catch(() => null);
        const item = answer?.entries?.[0]?.csl;
        if (!item) return { value: quotePayload(quote), said: t("Quote copied."), note: t("This page has no paper metadata to cite.") };
        const { labels, pageCount } = (await labelsRef.current?.().catch(() => null)) || NO_LABELS;
        const locator = pageNumber ? pageLocator({ pageNumber, labels, metaPages: item.page, pageCount }) : null;
        const citation = await renderBoth({ items: [item], style: quoteStyle(style, styleKind), mode: "citation",
          ...(locator ? { locators: { [item.id]: locator } } : {}) });
        const said = saidCitation(citation.text);
        return { value: quotePayload(quote, citation), note: "",
          said: said ? t("Quote copied with {citation}.", { citation: said }) : t("Quote copied with its citation.") };
      })();
      put(made, { html: true });
    },
  };
}
