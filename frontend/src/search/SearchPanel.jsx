// Workspace search (Ctrl+F / Ctrl+Shift+F): one popover covering paper
// titles, notes/highlights, reference links, the open PDF's text, and the
// server-side FTS index over the documents' text: every PDF's pages and
// every text file's lines. Extracted from App.jsx.
//
// Matching is separator-tolerant everywhere ("3000" finds "3,000-qubit"):
// textnorm.js mirrors the backend's gamma/textnorm.py rules (the cases in
// tests/shared/textnorm.json pin both), and the PDF viewer searches through
// the same normalized view of the page text (normalizeChars).
//
// Results are grouped by how directly they answer the query: matching paper
// titles first (relevance-ranked via `createTitleScorer` in library/librarySearch.js — the filter-chip listing
// sorts through the same scorer), then the open paper (its notes, then its
// PDF text with
// highlighted, navigable matches), then other notes, reference links, and
// finally the index's hits, the PDFs' pages and the files' lines (the open
// file's among them: its code view's own find is CodeMirror's). Each group
// pages with a "Show more" (search/searchPages.js).
//
// The box takes qualifiers beside its text (label:, folder:, is:pdf,
// year:>=2020, -in:pdf …; the grammar is search/queryParse.js, what each
// means search/searchFilters.js), and Tab makes a label or folder chip as
// before. A chip, or a clause naming one folder or label, goes to the
// server as its id and is resolved there, so a work folder's chip reaches
// files the library listing never carries; the other qualifiers are
// evaluated here on the listing, and the pages they keep go by id. The
// server ranks and limits inside them, and its answer for a run is shown
// whole: only the groups matched here (titles, the open PDF) keep to the
// listing. A qualifier it cannot apply is searched as
// text, under an amber line that says why. Opening a library hit
// keeps the search "pinned": once the paper renders, the query is re-found
// with pdf.js and the match is highlighted and scrolled into view — positions
// come from the same engine that draws the page, so they are always exact.
// A file's hit names its line, and opens the file there (openPageLink, the
// route of a chat's line link).

import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import {
  BanIcon, BookIcon, CalendarIcon, ChevronDownIcon, ChevronUpIcon, ClockIcon, FileGlyph, FileIcon, FolderIcon, HistoryIcon,
  LabelIcon, SearchIcon, TextSearchIcon, UserIcon, XIcon,
} from "../shared/ui/Icons";
import { Chevron, MorphText } from "../shared/ui/MotionWidgets";

import { buildSearchRegex, normalizeQuery } from "../shared/lib/textnorm";
import { createTitleScorer } from "../library/librarySearch";
import { PAGE_PATH_SEP, filingChips, pagePathLabel } from "../library/libraryUtils";
import { isPdf, pageDocument } from "../documents/pageDocument.js";
import { markedParts, plainSnippet } from "./snippets";
import { guideEvents } from "../guide/events.js";
import { DOC_PAGE, NOTES_PAGE, TITLES_PAGE, appendNew, docHitKey, searchBody } from "./searchPages";
import { tokenAt } from "./queryParse";
import { QUALIFIERS, applySuggestion, pageSet, resolveFilters, suggest } from "./searchFilters";
import { composing } from "../shared/lib/hotkeys.js";
import { t, tn } from "../shared/i18n/i18n.js";
import { Popover } from "../shared/ui/Surfaces";

export { buildSearchRegex, normalizeQuery };

// The 14 px icon of a qualifier's row (searchFilters.QUALIFIERS `icon`).
const QUALIFIER_ICONS = {
  label: LabelIcon, folder: FolderIcon, file: FileIcon, ban: BanIcon, user: UserIcon, book: BookIcon,
  calendar: CalendarIcon, clock: ClockIcon, history: HistoryIcon, search: TextSearchIcon,
};
// Every qualifier key, as the empty box's hint lists them, and the hint's
// and the placeholder's examples: syntax, the same in every language.
const KEY_LIST = "label: folder: is: no: author: venue: year: created: updated: in:"; // i18n-ignore: syntax
const HINT_EXAMPLE = "-label:read year:>=2020 is:pdf,pinned"; // i18n-ignore: syntax
const PLACEHOLDER_EXAMPLE = "label: is:pdf…"; // i18n-ignore: syntax
// A POST of a search page's body (searchPages.searchBody).
const postJson = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// `text` with the query's matches marked (search/snippets.js markedParts):
// a result row here, a row of the Ctrl+P palette (library/QuickOpen.jsx).
// `parts` ({text, mark} pieces) marks what another rule found instead: a
// file row's spans from the server (explorer/fileSearch.js fileParts).
export function MarkedText({ text, query, opts, lead = 0, parts }) {
  return (parts || markedParts(text, query, opts, lead)).map((p, i) => (
    p.mark ? <mark key={i} className="searchMark">{p.text}</mark> : <React.Fragment key={i}>{p.text}</React.Fragment>
  ));
}

// `button` false: the topbar's Search is off the bar (Settings → Appearance ›
// Top bar), so only the popover lives here, opened by its shortcut or the
// account menu's row. `openPageLink(id, target)` opens a page at a place
// (App's, the route of a Gamma link); without it a file's hit opens its
// page at the top. `indexed`: the workspace's ended indexing jobs, as a
// key; when one ends the search is asked again, so the hits the indexing
// hint promised come.
export default function SearchPanel({
  open, onOpenChange, button = true,
  focusedBlockId, homeBlocks, tree,
  openBlock, openPageLink,
  pdfSearchRef, scrollToRef, cancelCoarseRestoreRef, setPdfHidden, docNonce,
  onFindMarks, detailsDefault, wakeTasks, indexed = "", initialQuery = "",
}) {
  const [query, setQuery] = useState("");
  const [caret, setCaret] = useState(0); // where the caret is in the box: the dropdown completes the token there
  const [labels, setLabels] = useState([]); // confirmed filter chips
  const [sugIdx, setSugIdx] = useState(0);
  const inputRef = useRef(null);
  const caretRef = useRef(null); // where to put the caret once a completed query renders
  const [noteHits, setNoteHits] = useState([]); // /api/block-search
  const [notesPartial, setNotesPartial] = useState(false); // its scan stopped at the server's time budget
  const [notesNext, setNotesNext] = useState(""); // its `next`: the cursor of the following page
  const [libHits, setLibHits] = useState([]);   // /api/document-search: PDF and file hits, in the index's rank order
  const [libIndexing, setLibIndexing] = useState(0);
  const [libNext, setLibNext] = useState("");
  const [titlesShown, setTitlesShown] = useState(TITLES_PAGE);
  const [filtersShown, setFiltersShown] = useState(TITLES_PAGE);
  const [moreBusy, setMoreBusy] = useState(""); // the group ("notes" / "lib") whose next page is coming
  const runRef = useRef(0); // the search run an answer belongs to: a later one drops it
  const runAskRef = useRef(null); // the scope that run searched in: its "Show more" pages the same one
  const runKeyRef = useRef(""); // the filters (searchFilters `key`) the shown server hits were asked with
  const [pdfMatches, setPdfMatches] = useState([]); // pdf.js matches in the open document
  const [findIndex, setFindIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);
  // "Pinned" keeps the search live (and its PDF highlights visible) after the
  // popover closed because the user opened a library hit — so the match can
  // be highlighted in the paper it navigated to.
  const [pinned, setPinned] = useState(false);
  const pendingFindRef = useRef(null); // {page, sinceNonce} — jump here once the target doc renders
  // An indexing job ended while the shown answer had documents not indexed
  // yet: the search is asked again, as long as each answer has fewer (a
  // linked file out of reach stays pending and must not loop the job).
  // `seen` is the re-ask the last run was for, `below` the count to beat.
  const [reask, setReask] = useState(0);
  const reaskRef = useRef({ seen: 0, below: Infinity });
  useEffect(() => {
    if (libIndexing > 0 && libIndexing < reaskRef.current.below) {
      reaskRef.current.below = libIndexing;
      setReask((n) => n + 1);
    }
  }, [indexed]); // eslint-disable-line react-hooks/exhaustive-deps

  // The query read: its free text, its scope and the pages its predicates
  // keep, the groups, kinds and formats `in:` keeps, what is searched as
  // text and why (search/searchFilters.js). Dates are local, so a new day
  // reads it again.
  const pagesById = useMemo(() => new Map(homeBlocks.map((b) => [b.id, b])), [homeBlocks]);
  const today = new Date().toDateString();
  const filters = useMemo(() => resolveFilters(query, labels, { tree, pagesById, now: new Date() }),
    [query, labels, tree, pagesById, today]); // eslint-disable-line react-hooks/exhaustive-deps
  const q = filters.text;
  const sources = filters.sources;
  // The listing's pages ({ids, key}; ids null without a filter) the
  // predicates keep, which go to the server as `pages`, and the whole
  // filter keeps, which the groups matched here keep to: the search
  // follows both, running again when membership changes.
  const sentPages = useMemo(() => pageSet(homeBlocks, filters.pageTest), [homeBlocks, filters.pageTest]);
  const listPages = useMemo(() => pageSet(homeBlocks, filters.listTest), [homeBlocks, filters.listTest]);
  const scopeIds = useMemo(() => (listPages.ids ? new Set(listPages.ids) : null), [listPages.ids]);

  useEffect(() => { if (open) setPinned(false); }, [open]);
  // Opened with a query from elsewhere (the home listing's filter box,
  // Ctrl+P): it replaces whatever was typed last, filters included — before
  // the first paint, so the box never shows the old text.
  useLayoutEffect(() => {
    if (!open || !initialQuery) return;
    setQuery(initialQuery);
    setCaret(initialQuery.length);
    setLabels([]);
    pendingFindRef.current = null;
  }, [open, initialQuery]);
  useEffect(() => { setSugIdx(0); }, [query, caret]);
  useEffect(() => { setTitlesShown(TITLES_PAGE); setFiltersShown(TITLES_PAGE); }, [q, filters.key]);
  useEffect(() => { setFindIndex(0); }, [pdfMatches]);
  // A completed query puts the caret after what it added.
  useLayoutEffect(() => {
    const at = caretRef.current;
    if (at == null || !inputRef.current) return;
    caretRef.current = null;
    inputRef.current.setSelectionRange(at, at);
    setCaret(at);
  }, [query]);

  // ---- the dropdown under the box: label and folder names that Tab makes
  // a chip of (a label = the pages carrying it, a folder = the pages filed
  // in it or below it, {kind, id, name}), then qualifier keys and values
  // that complete the text (searchFilters.suggest). An inert row (year:'s
  // forms) is shown but not picked.
  const suggestions = useMemo(() => suggest(query, caret, { tree, chips: labels }), [query, caret, tree, labels]);
  const options = suggestions.filter((s) => !s.inert);
  function confirmLabel(opt) {
    setLabels((prev) => (prev.some((l) => l.kind === opt.kind && l.id === opt.id) ? prev : [...prev, opt]));
    setQuery("");
    setCaret(0);
    guideEvents.emit("search.filtered", { kind: opt.kind });
  }
  function applyRow(row) {
    const done = applySuggestion(query, caret, row);
    if (done.chip) { confirmLabel(done.chip); return; }
    if (done.query === query) {
      // Nothing to re-render (the clause was already whole): place the
      // caret now, so no position waits for the next edit.
      inputRef.current?.setSelectionRange(done.caret, done.caret);
      setCaret(done.caret);
      return;
    }
    caretRef.current = done.caret;
    setQuery(done.query);
    setPinned(false);
    pendingFindRef.current = null;
  }
  // The clauses searched as text, but not the one the caret is in while
  // the dropdown still offers it something.
  const caretFrom = tokenAt(query, caret).from;
  const problems = filters.problems.filter((p) => !(suggestions.length && p.from === caretFrom));
  const titleScoreOf = useMemo(() => createTitleScorer(q, { caseSensitive, wholeWord }),
    [q, caseSensitive, wholeWord, homeBlocks]);
  // The listing pages the filters keep, the "Filters:" listing: a label on
  // the page itself, a folder by where its top is filed (as the server's
  // blocks_store.pages_in_scope has them), with a query typed its matches
  // first; ties (and everything at score 0) keep library order. A work
  // folder's files the listing lacks are found by the groups below.
  const filterMatches = useMemo(() => {
    if (!scopeIds) return [];
    const members = homeBlocks.filter((b) => scopeIds.has(b.id));
    if (titleScoreOf) members.sort((a, b) => titleScoreOf(b) - titleScoreOf(a));
    return members;
  }, [scopeIds, homeBlocks, titleScoreOf]);
  // The typed clauses that keep pages, as the listing's header names them.
  const pageClauses = filters.clauses.filter((c) => c.key !== "in");
  // The groups searched: the notes request when in: keeps a block kind,
  // the open PDF and the library's PDFs when it keeps pdf, the files'
  // text when it keeps files; the documents request for either.
  const wantNotes = filters.kinds === null || filters.kinds.length > 0;
  const wantPdf = sources.has("pdf");
  const wantFiles = sources.has("files");
  const wantDocs = filters.formats === null || filters.formats.length > 0;

  // ---- find navigation in the open PDF
  function gotoFind(i, list = pdfMatches) {
    if (!list.length) return;
    const idx = ((i % list.length) + list.length) % list.length;
    setFindIndex(idx);
    const m = list[idx];
    setPdfHidden(false);
    scrollToRef.current?.({ position: { pageNumber: m.page, width: m.pageW, height: m.pageH, boundingRect: m.rects[0] } });
  }

  // Match highlights for the PDF viewer (multi-rect: a match spanning text
  // runs paints one box per run).
  const marks = useMemo(() => (
    q && (open || pinned)
      ? pdfMatches.flatMap((m, i) => m.rects.map((rect) => ({ page: m.page, rect, active: i === findIndex })))
      : []
  ), [pdfMatches, findIndex, open, pinned, q]);
  useEffect(() => { onFindMarks(marks); }, [marks, onFindMarks]);

  // ---- the search itself (debounced; re-runs when a new document renders so
  // pinned searches follow navigation, when the filters' pages change, and
  // when an indexing job it waited on ends).
  // One page per group, a POST with the filters' scope (the folder and
  // label ids, and the page ids the predicates keep), so the server ranks
  // and limits inside it; the run keeps that scope, and its "Show more"
  // pages the same one.
  const notesPage = (cursor, ask) => apiJson(`${API}/block-search`, postJson(searchBody(q, {
    limit: NOTES_PAGE, cursor, scope: ask.scope, pages: ask.pages, kinds: filters.kinds, case: caseSensitive, whole: wholeWord,
  })));
  // Full-text over the documents' text (server-side FTS index; normalized
  // word matching — the Aa/ab toggles only apply to notes and the open
  // PDF, whose matches pdf.js lists: the server leaves its document out).
  // The open file stays in: its hits are listed with the others'.
  const libPage = (cursor, ask) => apiJson(`${API}/document-search`, postJson(searchBody(q, {
    limit: DOC_PAGE, cursor, scope: ask.scope, pages: ask.pages, exclude: ask.exclude, formats: filters.formats,
  })));
  // Whether the open page shows a PDF: the listing's document, or the PDF
  // viewer's search hook (set once a PDF the listing lacks has rendered).
  const openPdf = () => !!focusedBlockId
    && (isPdf(pageDocument(pagesById.get(focusedBlockId))) || !!pdfSearchRef.current);
  useEffect(() => {
    // A new run: the last one's answers (its next pages too) are dropped,
    // and its cursors go with them.
    const run = ++runRef.current;
    const current = () => runRef.current === run;
    if (reask === reaskRef.current.seen) reaskRef.current.below = Infinity; // a run of its own, not a re-ask
    reaskRef.current.seen = reask;
    const ask = { scope: filters.scope, pages: sentPages.ids, exclude: openPdf() ? focusedBlockId : "" };
    runAskRef.current = ask;
    setMoreBusy(""); setNotesNext(""); setLibNext("");
    if (!q || !(open || pinned)) {
      setNoteHits([]); setNotesPartial(false); setLibHits([]); setLibIndexing(0); setPdfMatches([]); setBusy(false);
      runKeyRef.current = filters.key;
      return;
    }
    // The server's hits are its answer for a run's scope, shown as they
    // came: another filter's go now, not when the new answer comes (a new
    // query's stay until then, as the box is typed into).
    if (runKeyRef.current !== filters.key) {
      runKeyRef.current = filters.key;
      setNoteHits([]); setNotesPartial(false); setLibHits([]); setLibIndexing(0); setBusy(true);
    }
    const timer = setTimeout(() => {
      setBusy(true);
      let pdfText = 0; // hits inside PDFs, this one's or the library's, for the guide's search tour
      let fileText = 0; // hits inside text files
      // `partial`: the server's scan ran out of time, so the notes found
      // are only some of them (the notes group says so).
      const none = Promise.resolve({});
      const notesReq = (wantNotes ? notesPage("", ask) : none)
        .then((d) => { if (current()) { setNoteHits(d.blocks || []); setNotesPartial(!!d.partial); setNotesNext(d.next || ""); } })
        .catch(() => { if (current()) { setNoteHits([]); setNotesPartial(false); setNotesNext(""); } });
      const libReq = (wantDocs ? libPage("", ask) : none)
        .then((d) => {
          if (!current()) return;
          const results = d.results || [];
          const files = results.filter((r) => r.source === "file").length;
          fileText += files;
          pdfText += results.length - files;
          setLibHits(results);
          setLibIndexing(d.indexing || 0);
          setLibNext(d.next || "");
          // Missing documents were just scheduled for extraction: wake the
          // tasks poller so the progress button appears without waiting
          // for its idle heartbeat.
          if (d.indexing) wakeTasks?.();
        })
        .catch(() => { if (current()) { setLibHits([]); setLibIndexing(0); setLibNext(""); } });
      let pdfReq = Promise.resolve();
      const re = wantPdf && pdfSearchRef.current ? buildSearchRegex(q, { caseSensitive, wholeWord }) : null;
      if (re) {
        pdfReq = pdfSearchRef.current(re).then(async (matches) => {
          if (matches.stale) return; // a query this effect moved past: the newer run answers
          // A library hit was opened: jump to its page's first match now that
          // the document is rendered and re-searched.
          const pending = pendingFindRef.current;
          if (pending && docNonce > pending.sinceNonce) {
            if (!matches.length) {
              // The library index matches words scattered across a page (AND
              // of terms), so the exact phrase may not exist anywhere — fall
              // back to highlighting the longest word of the query.
              const terms = q.split(/\s+/).filter(Boolean).sort((a, b) => b.length - a.length);
              const re2 = terms.length > 1 && pdfSearchRef.current
                ? buildSearchRegex(terms[0], { caseSensitive, wholeWord, regex: false }) : null;
              if (re2) matches = (await pdfSearchRef.current(re2).catch(() => [])) || [];
              if (matches.stale) return;
            }
            if (matches.length) {
              pendingFindRef.current = null;
              const idx = matches.findIndex((m) => m.page === pending.page);
              // The paper opened cold, so its last-read restore may still be
              // holding the viewport — the match owns it from here.
              cancelCoarseRestoreRef?.current?.();
              gotoFind(idx >= 0 ? idx : 0, matches);
            }
          }
          pdfText += matches.length;
          setPdfMatches(matches);
        }).catch(() => setPdfMatches([]));
      } else {
        setPdfMatches([]);
      }
      Promise.allSettled([notesReq, libReq, pdfReq]).then(() => {
        if (!current()) return;
        setBusy(false);
        guideEvents.emit("search.results", { pdf: pdfText > 0, text: fileText > 0 });
      });
    }, 250);
    return () => clearTimeout(timer);
  }, [q, filters.key, sentPages.key, listPages.key, open, pinned, caseSensitive, wholeWord, docNonce, reask]); // eslint-disable-line react-hooks/exhaustive-deps

  // "Show more": the group's next page, after the hits it lists (dropped
  // when the search moved on meanwhile). The notes cursor of a scan cut
  // short by the time budget goes on from where that scan stopped.
  function showMore(group) {
    const run = runRef.current;
    const notes = group === "notes";
    setMoreBusy(group);
    (notes ? notesPage(notesNext, runAskRef.current) : libPage(libNext, runAskRef.current))
      .then((d) => {
        if (runRef.current !== run) return;
        if (notes) {
          setNoteHits((prev) => appendNew(prev, d.blocks || [], (r) => r.id));
          setNotesPartial(!!d.partial);
          setNotesNext(d.next || "");
        } else {
          setLibHits((prev) => appendNew(prev, d.results || [], docHitKey));
          setLibNext(d.next || "");
        }
      })
      .catch(() => {}) // the cursor stays: the button tries again
      .finally(() => { if (runRef.current === run) setMoreBusy(""); });
  }

  // Open a library content hit: pin the search, load the paper, and let the
  // re-run above land on the exact match (page-top scroll as a fallback while
  // the document is still loading or if the text can't be re-found).
  function openLibHit(r) {
    onOpenChange(false);
    setPinned(true);
    if (r.block_id === focusedBlockId) {
      const idx = pdfMatches.findIndex((m) => m.page === r.page);
      if (idx >= 0) gotoFind(idx);
      else scrollToRef.current?.({ position: { pageNumber: r.page } });
      return;
    }
    pendingFindRef.current = { page: r.page, sinceNonce: docNonce };
    openBlock(r.block_id).then(() => {
      let tries = 0;
      const go = () => {
        if (!pendingFindRef.current) return; // the exact-match jump already happened
        if (scrollToRef.current && document.querySelector("[data-page]")) {
          cancelCoarseRestoreRef?.current?.();
          scrollToRef.current({ position: { pageNumber: r.page } });
        } else if (tries++ < 40) setTimeout(go, 200);
      };
      setTimeout(go, 600); // let the session-restore scroll settle first
    });
  }

  function openNoteHit(r) {
    onOpenChange(false);
    if (r.page_root_id && r.page_root_id !== r.id) openBlock(r.page_root_id, { block: r.id });
    else openBlock(r.id);
  }

  // Open a file's hit at its line: the route a chat's line link takes
  // (App's openPageLink scrolls the code view to it and marks it once the
  // text is loaded), so nothing is pinned or polled here.
  function openFileHit(r) {
    onOpenChange(false);
    if (openPageLink) openPageLink(r.page_id, { kind: "line", pageId: r.page_id, line: r.line, to: null, quote: "" });
    else openBlock(r.page_id, { pushNav: true, link: true });
  }

  // ---- result grouping: titles → this page (notes, then its PDF text) →
  // other notes → reference links → other pages' PDFs → the files' lines
  // The filters' listing pages: titles and the open PDF are kept to them
  // here. The notes and document hits are the server's answer for the
  // run's scope, which reaches pages the listing lacks (a work folder's
  // files): never kept to the listing, only to the kinds and groups in:
  // keeps.
  const inScope = (pageId) => !scopeIds || scopeIds.has(pageId);
  const inKinds = (r) => !filters.kinds || filters.kinds.includes(r.kind);
  const allTitles = titleScoreOf && sources.has("title")
    ? homeBlocks
      .filter((b) => inScope(b.id) && titleScoreOf(b) > 0)
      .sort((a, b) => titleScoreOf(b) - titleScoreOf(a))
    : [];
  const titleMatches = allTitles.slice(0, titlesShown);
  const titleIds = new Set(allTitles.map((b) => b.id)); // listed here (or by its "Show more"), not as a note
  const noteRows = (wantNotes ? noteHits : [])
    .filter((r) => inKinds(r) && !(r.kind === "page" && titleIds.has(r.id)));
  const inPage = (r) => focusedBlockId && (r.page_root_id === focusedBlockId || r.id === focusedBlockId);
  const titlesExtra = noteRows.filter((r) => r.kind === "page"); // regex-mode / non-home pages
  const notesHere = noteRows.filter((r) => r.kind !== "page" && inPage(r));
  const notesElsewhere = noteRows.filter((r) => r.kind !== "page" && r.kind !== "link" && !inPage(r));
  const linkHits = noteRows.filter((r) => r.kind === "link" && !inPage(r));
  const libElsewhere = (wantPdf ? libHits : []).filter((r) => r.source !== "file" && r.block_id !== focusedBlockId);
  const fileHits = wantFiles ? libHits.filter((r) => r.source === "file") : [];
  const showPdfMatches = wantPdf && inScope(focusedBlockId);
  const filtersListed = filterMatches.slice(0, filtersShown);
  // An incomplete notes scan is no "No matches.": the notes group says why.
  const anything = titleMatches.length || titlesExtra.length || notesHere.length || notesElsewhere.length
    || linkHits.length || filterMatches.length || (showPdfMatches && pdfMatches.length) || libElsewhere.length
    || fileHits.length || notesPartial || notesNext || libNext;
  // What the details list besides the open PDF's matches (the compact bar's
  // summary line).
  const listed = titleMatches.length + titlesExtra.length + notesHere.length + notesElsewhere.length
    + linkHits.length + filterMatches.length + libElsewhere.length + fileHits.length;

  // One switch for the whole detail area (all the result lists). Its default
  // comes from Settings → Search via the detailsDefault prop (App owns the
  // per-place preference: home expanded unless turned off, paper view
  // compact unless turned on). Compact, the bar keeps the open PDF's match
  // count and one line counting the rest, which expands the details.
  // Re-applied each time the panel opens; the toggle button then only affects
  // the current panel session.
  const [showDetails, setShowDetails] = useState(detailsDefault);
  // (Before paint: the popover never shows last session's state first.)
  useLayoutEffect(() => { if (open) setShowDetails(detailsDefault); }, [open]);

  // Result rows: the page's kind glyph and title (a sub-page's as "Parent ›
  // Child", the pages above it muted; a file's after its directory in its
  // work folder), then one line of text, the query
  // marked in both (search/snippets.js maps each match back
  // through the normalized text, and marks nothing it cannot place). Notes
  // show as plain text, not markdown. The document index ignores the
  // Aa / ab toggles, so its rows are marked without them.
  // a page's document's format, the glyph's ("" for none)
  const pageFormat = (page) => pageDocument(page)?.format || "";
  const markOpts = { caseSensitive, wholeWord };
  const marked = (text, opts = markOpts, lead = 0) => <MarkedText text={text} query={q} opts={opts} lead={lead} />;
  const section = (label, count) => (
    <div className="searchSection">{label}{count ? <span className="searchSectionCount">{count}</span> : null}</div>
  );
  const pageTag = (page) => <span className="searchPageTag">{t("p. {page}", { page })}</span>;
  const lineTag = (n) => <span className="searchPageTag">{t("line {n}", { n })}</span>;
  // What precedes a page's title: the pages a sub-page is in.
  const pageLead = (pageId) => {
    const path = pagePathLabel(pagesById, pageId);
    return path ? `${path}${PAGE_PATH_SEP.trimEnd()}` : "";
  };
  const resultHead = (pageId, title, format, extra, opts, lead = pageLead(pageId)) => (
    <span className="searchResultPage">
      <FileGlyph format={format} size={14} />
      {lead ? <span className="searchResultPath">{lead}</span> : null}
      <span className="searchResultTitle">{marked(title || t("Untitled"), opts)}</span>
      {extra}
    </span>
  );
  const kindBadge = (r) => (
    r.kind === "highlight" ? <span className="searchKindBadge">{t("highlight")}</span>
      : r.kind === "link" ? <span className="searchKindBadge">{t("link")}</span>
      : r.kind === "picture" ? <span className="searchKindBadge">{t("picture")}</span> : null
  );
  const noteRow = (r) => {
    const pageId = r.page_root_id || r.id;
    return (
      <button key={r.id} className="searchResult" onClick={() => openNoteHit(r)}>
        {resultHead(pageId, r.page_title, pageFormat(pagesById.get(pageId)), kindBadge(r))}
        <span className="searchResultText">{marked(plainSnippet(r.content), markOpts, 50)}</span>
      </button>
    );
  };
  // A title row's line under it: the page's labels, then its folders.
  const filingLine = (b) => {
    const chips = filingChips(tree, b.properties);
    return [...chips.labels, ...chips.folders].map((c) => c.name).join(", ");
  };
  const titleRow = (b, subtitle) => (
    <button
      key={`title-${b.id}`}
      className="searchResult"
      onClick={() => { onOpenChange(false); openBlock(b.id, { restoreScroll: true }); }}
    >
      {resultHead(b.id, b.content, pageFormat(b))}
      {subtitle ? <span className="searchResultText">{subtitle}</span> : null}
    </button>
  );
  // A group's next page: the home feed's "load more" pill, at the group's end.
  const moreButton = (group, onClick) => (
    <button type="button" className="loadMoreBtn" disabled={moreBusy === group} onClick={onClick}>
      <MorphText>{moreBusy === group ? t("Loading…") : t("Show more")}</MorphText>
    </button>
  );
  // A file's hit: its directory in its work folder before its name (a
  // library document has no path: the pages it is in, as any page's), the
  // line it names, and that line as its text. The open file's is tagged
  // "this file" in place of its directory.
  const fileRow = (r, i) => {
    const here = r.page_id === focusedBlockId;
    const path = r.path || "";
    const lead = here ? "" : path ? path.slice(0, path.lastIndexOf("/") + 1) : pageLead(r.page_id);
    const tags = <>{lineTag(r.line)}{here ? <span className="searchKindBadge">{t("this file")}</span> : null}</>;
    return (
      <button
        key={`file-${i}`}
        className="searchResult"
        onClick={() => openFileHit(r)}
        title={t("Open \"{title}\" at line {line}", { title: path || r.title, line: r.line })}
      >
        {resultHead(r.page_id, r.title, "text", tags, {}, lead)}
        <span className="searchResultText">{marked(r.snippet, {})}</span>
      </button>
    );
  };
  const notesElsewhereLabel = focusedBlockId ? t("Other notes") : t("Notes");
  const libLabel = focusedBlockId ? t("Other PDFs") : t("Library PDFs");

  return (
    <span data-popover="search" className={`popoverAnchor${button ? "" : " offBar"}`}>
      {button ? (
        <button
          className={`iconBtn ${open ? "activeIcon" : ""}`}
          onClick={() => onOpenChange(!open)}
          data-guide="header.search"
          title={t("Search everything (Ctrl+F)")}
          aria-label={t("Search")}
        >
          <SearchIcon size={16} />
          <span className="barLabel">{t("Search")}</span>
        </button>
      ) : null}
      {open ? (
        <Popover className="searchPopover" data-guide="search.panel">
          <div className="searchRow">
            <button
              className={`searchToggle ${showDetails ? "on" : ""}`}
              onClick={() => setShowDetails((v) => !v)}
              title={showDetails
                ? t("Collapse result details (compact find — the default is in Settings → Search)") : t("Expand result details: titles, notes, and other pages")}
              aria-label={t("Toggle result details")}
            >
              <Chevron open={showDetails} dir="right" size={14} />
            </button>
            <div className="searchInputWrap">
              {labels.map((l) => (
                <span key={`${l.kind}:${l.id}`} className="categoryBadge searchChip">
                  {l.kind === "folder" ? <FolderIcon size={14} /> : <LabelIcon size={14} />}
                  {l.name}
                  <button
                    className="uiClose uiCloseSm searchChipX"
                    title={t("Remove {label} filter \"{name}\"", { label: l.kind === "folder" ? t("folder") : t("label"), name: l.name })}
                    onClick={() => setLabels((prev) => prev.filter((x) => x !== l))}
                  ><XIcon size={14} /></button>
                </span>
              ))}
              <input
                autoFocus
                ref={inputRef}
                className="searchInput"
                data-guide="search.input"
                value={query}
                // A query is typed syntax as much as words: an iPad's first
                // capital or a corrected word would turn label: into Label:
                // or a name into another.
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                onChange={(e) => {
                  caretRef.current = null; // a typed edit keeps its own caret
                  setQuery(e.target.value);
                  setCaret(e.target.selectionStart ?? e.target.value.length);
                  setPinned(false);
                  pendingFindRef.current = null;
                }}
                onSelect={(e) => setCaret(e.target.selectionStart ?? 0)}
                onKeyDown={(e) => {
                  // The keys an input method is still composing with are its own.
                  if (composing(e)) return;
                  // Only Tab takes a row (a chip, or the completed text);
                  // Enter stays the search's own key (next PDF match).
                  if (e.key === "Tab" && !e.shiftKey && options.length) {
                    e.preventDefault();
                    applyRow(options[sugIdx] || options[0]);
                  } else if (e.key === "Enter" && pdfMatches.length) {
                    e.preventDefault();
                    gotoFind(findIndex + (e.shiftKey ? -1 : 1));
                  } else if (e.key === "ArrowDown" && options.length) {
                    e.preventDefault();
                    setSugIdx((i) => (i + 1) % options.length);
                  } else if (e.key === "ArrowUp" && options.length) {
                    e.preventDefault();
                    setSugIdx((i) => (i - 1 + options.length) % options.length);
                  } else if (e.key === "Backspace" && !query && labels.length) {
                    setLabels((prev) => prev.slice(0, -1));
                  }
                }}
                placeholder={labels.length ? t("Search within labeled pages…")
                  : t("Search titles, notes, PDF and file text — Tab adds a filter, or type {example}", { example: PLACEHOLDER_EXAMPLE })}
              />
              {suggestions.length ? (
                <div className="categorySuggestions searchLabelSuggest" role="listbox" aria-label={t("Search filters")}>
                  {suggestions.map((s) => {
                    const Icon = QUALIFIER_ICONS[s.chip ? s.kind : s.icon || QUALIFIERS[s.key]?.icon] || SearchIcon;
                    const name = s.chip ? s.name : s.kind === "key" ? `${s.key}:` : s.label || s.value;
                    const head = (
                      <span className="searchSuggestName">
                        <Icon size={14} />
                        {s.kind === "value" || s.kind === "forms" ? <span className="searchSuggestKey">{s.key}:</span> : null}
                        {s.kind === "forms" ? null : name}
                        {s.desc ? <span className="searchSuggestDesc">{s.desc}</span> : null}
                      </span>
                    );
                    if (s.inert) {
                      return <div key={`${s.kind}:${s.key}`} role="option" aria-disabled="true" aria-selected="false" className="categorySuggestionItem searchSuggestInert">{head}</div>;
                    }
                    const i = options.indexOf(s);
                    return (
                      <button
                        key={s.chip ? `${s.kind}:${s.id}` : `${s.kind}:${s.key}:${s.value || ""}`}
                        type="button" role="option" tabIndex={-1}
                        aria-selected={i === sugIdx}
                        className={`categorySuggestionItem${i === sugIdx ? " selected" : ""}`}
                        onPointerDown={(e) => e.preventDefault()}
                        onMouseEnter={() => setSugIdx(i)}
                        onClick={() => applyRow(s)}
                      >
                        {head}
                        <span className="searchSuggestHint">{t("Tab")}</span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
            {showPdfMatches && pdfMatches.length ? (
              <span className="searchNavGroup">
                <span className="searchFindCount" title={t("Matches in the open PDF")}>{findIndex + 1}/{pdfMatches.length}</span>
                <button className="searchToggle searchNavBtn" onClick={() => gotoFind(findIndex - 1)} title={t("Previous match (matches are highlighted in the PDF)")}>
                  <ChevronUpIcon size={14} />
                </button>
                <button className="searchToggle searchNavBtn" onClick={() => gotoFind(findIndex + 1)} title={t("Next match (Enter)")}>
                  <ChevronDownIcon size={14} />
                </button>
              </span>
            ) : null}
            <button className={`searchToggle ${caseSensitive ? "on" : ""}`} onClick={() => setCaseSensitive((v) => !v)} title={t("Match case")}>{t("Aa")}</button>
            <button className={`searchToggle ${wholeWord ? "on" : ""}`} onClick={() => setWholeWord((v) => !v)} title={t("Match whole word")}><u>ab</u></button>
          </div>
          <div className="searchResults" data-guide="search.results">
            {problems.map((p) => (
              <div key={`${p.from}:${p.raw}`} className="searchHint warn">
                {t("“{token}” is searched as text: {reason}", { token: p.raw, reason: p.reason })}
              </div>
            ))}
            {showDetails && !query.trim() && !labels.length ? (
              <div className="searchHint">{t("Narrow with {keys} — a leading - leaves out, commas list alternatives: {example}", {
                keys: KEY_LIST, example: HINT_EXAMPLE })}</div>
            ) : null}
            {busy ? <div className="searchHint">{t("Searching…")}</div> : null}
            {!busy && q && !anything ? <div className="searchHint">{t("No matches.")}</div> : null}
            {/* The compact find bar still says what the details hold (the
                open PDF's matches are the count beside the box), and that a
                cut-short notes scan found only some. */}
            {!showDetails && !busy && q && listed ? (
              <button type="button" className="searchResult" onClick={() => setShowDetails(true)}
                title={t("Expand result details: titles, notes, and other pages")}>
                <span className="searchResultText">{showPdfMatches && pdfMatches.length
                  ? tn("{n} more result — show it", "{n} more results — show them", listed)
                  : tn("{n} result — show it", "{n} results — show them", listed)}</span>
              </button>
            ) : null}
            {!showDetails && !busy && q && notesPartial ? <div className="searchHint">{t("Stopped early — refine the search to see more.")}</div> : null}
            {showDetails ? (
              <>
                {scopeIds ? (
                  <>
                    <div className="searchSection">
                      {t("Filters:")} {[...labels.map((c) => c.name), ...pageClauses.map((c) => c.raw)].join(" + ")}
                    </div>
                    {filterMatches.length === 0 ? (
                      <div className="searchHint">{pageClauses.length || labels.some((c) => c.kind === "folder") ? t("No pages match these filters.")
                        : t("No pages carry {which}.", { which: labels.length === 1 ? t("this label") : t("all these labels") })}</div>
                    ) : filtersListed.map((b) => titleRow(b, filingLine(b)))}
                    {filterMatches.length > filtersListed.length
                      ? moreButton("filters", () => setFiltersShown((n) => n + TITLES_PAGE)) : null}
                  </>
                ) : null}
                {titleMatches.length || titlesExtra.length ? section(t("Titles"), titleMatches.length + titlesExtra.length) : null}
                {titleMatches.map((b) => titleRow(b, filingLine(b)))}
                {titlesExtra.map(noteRow)}
                {allTitles.length > titleMatches.length
                  ? moreButton("titles", () => setTitlesShown((n) => n + TITLES_PAGE)) : null}
                {notesHere.length ? section(t("Notes on this page"), notesHere.length) : null}
                {notesHere.map(noteRow)}
                {showPdfMatches && pdfMatches.length ? section(t("This PDF"), pdfMatches.length) : null}
                {(showPdfMatches ? pdfMatches : []).map((m, i) => (
                  <button
                    key={`pdf-${i}`}
                    className={`searchResult ${i === findIndex ? "active" : ""}`}
                    onClick={() => gotoFind(i)}
                  >
                    <span className="searchResultPage">{pageTag(m.page)}</span>
                    <span className="searchResultText">…{marked(m.snippet)}…</span>
                  </button>
                ))}
                {notesElsewhere.length || notesPartial || notesNext ? section(notesElsewhereLabel, notesElsewhere.length) : null}
                {notesPartial ? <div className="searchHint">{t("Stopped early — refine the search to see more.")}</div> : null}
                {notesElsewhere.map(noteRow)}
                {notesNext ? moreButton("notes", () => showMore("notes")) : null}
                {linkHits.length ? section(t("Reference links"), linkHits.length) : null}
                {linkHits.map(noteRow)}
                {libElsewhere.length ? section(libLabel, libElsewhere.length) : null}
                {libElsewhere.map((r, i) => (
                  <button
                    key={`lib-${i}`}
                    className="searchResult"
                    onClick={() => openLibHit(r)}
                    title={t("Open \"{title}\" at page {page} — the match will be highlighted", { title: r.title, page: r.page })}
                  >
                    {resultHead(r.block_id, r.title.slice(0, 60), "pdf", pageTag(r.page), {})}
                    {/* the index's snippet() already marks a cut with "…" */}
                    <span className="searchResultText">{marked(r.snippet, {})}</span>
                  </button>
                ))}
                {fileHits.length ? section(t("Files"), fileHits.length) : null}
                {fileHits.map(fileRow)}
                {libIndexing ? (
                  <div className="searchHint">{tn("Indexing {n} document in the background — results will fill in shortly.", "Indexing {n} documents in the background — results will fill in shortly.", libIndexing)}</div>
                ) : null}
                {libNext ? moreButton("lib", () => showMore("lib")) : null}
              </>
            ) : null}
          </div>
        </Popover>
      ) : null}
    </span>
  );
}
