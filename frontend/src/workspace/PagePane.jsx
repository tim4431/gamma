// A page shown in a tab group that is not the live one
// (docs/dev/tab_groups.md "What a group shows"): a view of the page drawn
// with the components the live page view draws it with, at the place and
// zoom that view would open it at, so a press that makes the group live
// changes nothing on screen but the tools that come with editing. A page
// with a PDF shows the PDF with its highlights and its handwriting, text
// boxes and pictures; a page with a document of another format its pane
// (documents/DocumentPane.jsx's own viewers, read-only: the code, the
// picture with its regions, the player, the file pane); a page in its
// notebook view shows its sheets; any other page, or one whose document
// pane is closed, its notes, as the notes tree draws them (BlockTree's
// `preview`: the editor's look, nothing to edit).
// The docked Notes window shows a page's notes through the same view
// (`dock`) while the page live has its notes in its own group.
//
// A press makes the page live (`onActivate(target)`): on a note it opens
// that note's editor with the caret where the press was ({edit: {id,
// clickPos}}), on the title its title editor ({title: true}); the notes'
// folds in the view go with it (`folds`), so the live notes fold alike.
// The view reports where it stands as it moves, in the shapes App's stores
// keep: the PDF's and the picture's scroll ({top, left, scale}, onScroll)
// and zoom setting (onZoom), the code view's top line ({line}, onScroll),
// the notes' top row ({id, offset}, onNotesPlace), and a page's place on
// its sheets ({place}, onScroll), whichever view shows them. The
// tree is read once per page shown, from what the page held when it was last live
// if this session had it (keepPaneTree), then from the server, laid over
// with its unsent edits (`overlay`, the collab session's).
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import PdfViewer from "../pdf/PdfViewer";
import { NotebookViewer, readPlace } from "../notebook/NotebookViewer";
import { NoteSheetContext } from "../notebook/NoteSheet";
import { PageToolsContext } from "../markup/PageTools.jsx";
import { useMarks } from "../markup/MarkupLayers";
import { textBoxesBySurface } from "../markup/textBox.js";
import { picturesBySurface } from "../markup/picture.js";
import { groupsBySurface } from "../ink/inkSurfaces.js";
import { inkBySheet, sheetsOf } from "../notebook/notebook.js";
import { BlockTree } from "../editor/BlockTree";
import { refLabelsByBlock } from "../editor/refLabels.js";
import { FileChipContext } from "../transfers/FileChip";
import { blocksToHighlights, flattenBlocks, isFolded, isInkGroup, positionKeyOf } from "../shared/model/blockModel.js";
import { pageHighlights } from "../app/treeDerived.js";
import { filingChips } from "../library/libraryUtils.js";
import { documentFileUrl, documentViewerUrl, fileHash, isPdf, pageDocument } from "../documents/pageDocument.js";
import { documentView, viewerOf } from "../documents/documentView.js";
import DocumentPane from "../documents/DocumentPane.jsx";
import { API, apiJson, assetUrl } from "../shared/lib/utils";
import { InfoIcon, LabelIcon, MessageSquareIcon, NotebookIcon, PaperclipIcon, TrashIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

const PRESS_SLOP = 8;
const EMPTY = Object.freeze([]);
const NO_COUNTS = new Map(); // a view shows no comment counts
const noop = () => {};

// The trees of pages this session had open, as they were when left: a view
// of one draws at once, before the server's answer.
const TREES = new Map(); // pageId -> {block, children}
const TREES_MAX = 24;
export function keepPaneTree(pageId, block, children) {
  if (!pageId || !block) return;
  TREES.delete(pageId);
  TREES.set(pageId, { block, children: children || [] });
  while (TREES.size > TREES_MAX) TREES.delete(TREES.keys().next().value);
}

// A press on the scrollbar of the element it lands on: past that element's
// client box, on a side it overflows. An inline element has no client box
// and overflows nothing.
export function onScrollbar(event) {
  const el = event.target;
  if (!el || typeof el.getBoundingClientRect !== "function") return false;
  const box = el.getBoundingClientRect();
  const x = event.clientX - box.left - el.clientLeft, y = event.clientY - box.top - el.clientTop;
  return (el.scrollHeight > el.clientHeight && x >= el.clientWidth) || (el.scrollWidth > el.clientWidth && y >= el.clientHeight);
}

// The notes' top row and how far into it they are scrolled, as App's
// captureScrollPos reads the live notes ({id: "" } at the top).
export function notesPlaceOf(list) {
  let place = { id: "", offset: 0 };
  if (!list || list.scrollTop <= 8) return place;
  const top = list.getBoundingClientRect().top;
  for (const row of list.querySelectorAll(".blockRowWrap[data-block-id]")) {
    const r = row.getBoundingClientRect();
    if (r.bottom > top + 4) { place = { id: row.dataset.blockId || "", offset: top - r.top }; break; }
  }
  return place;
}

// `shared`: what App lends every view, {refCache, onFetchRefs, onCacheRef,
// libTree, fileChipCtx, commentsOn, keybindings, enterNewBlock, pages,
// textScaleStyle, darkPage, hideEmbeddedAnnots, canWrite, labels}.
export default function PagePane({ pageId, title, dock = false, overlay, scroll, startPage = 0, zoom: zoomIn = "page-width",
  pdfHidden = false, notebookView = false, notesPlace = null, shared, onScroll, onZoom, onNotesPlace, onActivate }) {
  const [page, setPage] = useState(() => {
    const kept = TREES.get(pageId);
    return kept ? { id: pageId, ...kept, error: "" } : { id: null, block: null, children: EMPTY, error: "" };
  });
  useEffect(() => {
    let on = true;
    apiJson(`${API}/blocks/${pageId}/subtree`).then((data) => {
      if (!on) return;
      const children = data.block?.children || [];
      setPage({ id: pageId, block: data.block, children: overlay ? overlay(pageId, children) : children, error: "" });
    }, (err) => { if (on) setPage((p) => (p.id ? p : { id: pageId, block: null, children: EMPTY, error: err?.message || String(err) })); });
    return () => { on = false; };
  }, [pageId]); // eslint-disable-line react-hooks/exhaustive-deps
  const doc = useMemo(() => pageDocument(page.block), [page.block]);
  const url = isPdf(doc) ? documentViewerUrl(doc) : "";
  const sheets = useMemo(() => (viewerOf(doc?.format, doc) ? EMPTY : sheetsOf(page.children)), [doc, page.children]);
  // What shows, by the live page's rule (documents/documentView.js): a
  // closed pane is not mounted here, and the dock shows the notes.
  const view = documentView({ doc, url, sheets: sheets.length, notebook: notebookView, hidden: pdfHidden, dock });

  // A press (down and up within a few pixels) makes the page live; a drag,
  // a scroll or a text selection does not. It is the primary button's or
  // the first finger's, and never one on a scrollbar: Windows and Linux
  // draw classic scrollbars that take presses. It is decided on the click,
  // which a right button does not send (Windows sends its contextmenu after
  // the release, which would then open over the page made live). A press
  // on a control (a note's row, a sheet's buttons, the outline) is that
  // control's: the notes tree hands its own presses over with their target.
  const pressRef = useRef(null);
  const reportRef = useRef(noop);
  const onPointerDown = (event) => {
    pressRef.current = event.button === 0 && event.isPrimary && !onScrollbar(event) ? { x: event.clientX, y: event.clientY } : null;
    // where the view stands, written now: the page opens there
    reportRef.current();
  };
  const onClick = (event) => {
    const p = pressRef.current;
    pressRef.current = null;
    if (!p || Math.hypot(event.clientX - p.x, event.clientY - p.y) > PRESS_SLOP) return;
    // a control's press is its own, but a highlight's note badge with nothing
    // to do here (PdfViewer lets it through) is the page's
    const control = event.target.closest?.("button, a, input, textarea, select, [role='button'], [role='menuitem']");
    if (control && !control.classList.contains("pdfNoteBadge")) return;
    press();
  };
  const activateRef = useRef(onActivate);
  activateRef.current = onActivate;
  // What a press hands over besides its target: the notes' folds and the
  // zoom the view shows, which the page opens with.
  const foldsRef = useRef(null);
  const zoomRef = useRef(null);
  const press = useCallback((target) => {
    reportRef.current();
    const with_ = { ...target };
    if (foldsRef.current) with_.folds = foldsRef.current;
    if (zoomRef.current) with_.zoom = zoomRef.current;
    activateRef.current(with_);
  }, []);

  // The page's marks, read-only: its handwriting, text boxes and pictures,
  // by surface (a PDF page's number, a sheet's id), as the live page builds
  // them (ink/useInkSurfaces.js).
  const flat = useMemo(() => flattenBlocks(page.children), [page.children]);
  const inkBlocks = useMemo(() => flat.filter(isInkGroup).map((b) => ({ id: b.id, properties: b.properties })), [flat]);
  const marks = useMarks({
    ink: useMemo(() => groupsBySurface(inkBlocks, sheets.length ? inkBySheet(page.children) : new Map()), [inkBlocks, sheets.length, page.children]),
    inkSelection: null, inkFlash: null,
    boxes: useMemo(() => textBoxesBySurface(page.children), [page.children]), boxSel: null, boxFlash: null,
    pictures: useMemo(() => picturesBySurface(page.children), [page.children]), pictureSel: null, pictureFlash: null,
  });
  // The tools a view takes: nothing armed, nothing to act on but a press,
  // and none of the live page's journal, peers' strokes or surface sizes
  // (`preview`, MarkupLayers.jsx).
  const tools = useMemo(() => ({
    readOnly: true, preview: true, canWrite: !!shared?.canWrite,
    ink: { tool: null, penTool: null, penOnly: false, clip: false, keys: {}, ruler: null, zoom: null, aiWork: null },
    text: { armed: false, style: null, zotero: false },
    actions: { onJump: () => press(), onActivate: () => press() },
  }), [press, shared?.canWrite]);

  let body;
  if (page.error) body = <div className="pagePaneHint">{t("Could not open this page: {message}", { message: page.error })}</div>;
  else if (!page.id) body = <div className="pagePaneHint" aria-busy="true" />;
  else if (view.shown && view.spec?.host) {
    body = <DocumentPane view={view} render={{
      pdf: () => <PdfView pageId={pageId} url={url} children_={page.children} marks={marks} zoomIn={zoomIn} scroll={scroll} startPage={startPage}
        shared={shared} onScroll={onScroll} onZoom={onZoom} reportRef={reportRef} zoomRef={zoomRef} />,
      notebook: () => <BookView pageId={pageId} sheets={sheets} marks={marks} zoomIn={zoomIn} place={scroll?.place}
        onScroll={onScroll} onZoom={onZoom} reportRef={reportRef} zoomRef={zoomRef} />,
    }} />;
  } else if (view.shown) {
    body = <FileView view={view} pageId={pageId} doc={doc} children_={page.children} zoomIn={zoomIn} scroll={scroll}
      onScroll={onScroll} onZoom={onZoom} reportRef={reportRef} zoomRef={zoomRef} />;
  } else {
    body = <NotesView pageId={pageId} block={page.block} children_={page.children} title={title} dock={dock} doc={doc}
      hasSheets={sheets.length > 0} notebookView={notebookView} marks={marks} url={url} notesPlace={notesPlace} shared={shared}
      press={press} foldsRef={foldsRef} onNotesPlace={onNotesPlace} onScroll={onScroll} reportRef={reportRef} />;
  }
  return (
    <PageToolsContext.Provider value={tools}>
      <div className={`pagePane${dock ? " dock" : ""}`} data-page-id={pageId} data-read-view="" onPointerDown={onPointerDown} onClick={onClick}>
        {body}
      </div>
    </PageToolsContext.Provider>
  );
}

// The page's PDF in the viewer the live page uses, at the zoom App opens
// the page at (`zoomIn`: the page's own, else the one on screen), and at
// its place: the exact one it was left at this session (`scroll`), else
// the top of the page it was last read at (`startPage`), as App's coarse
// restore would land it.
function PdfView({ pageId, url, children_, marks, zoomIn, scroll, startPage, shared, onScroll, onZoom, reportRef, zoomRef }) {
  const wrapRef = useRef(null);
  const [zoom, setZoom] = useState(zoomIn);
  // The page opens at the zoom the view shows (a press hands it over), and
  // keeps one the reader chose here as its own (pageLayouts).
  zoomRef.current = zoom;
  const zoomTo = (z) => { setZoom(z); onZoom?.(pageId, z); };
  const effRef = useRef(1);
  const restoreRef = useRef(scroll?.top != null ? scroll : startPage > 1 ? { page: startPage } : null);
  const highlights = useMemo(() => pageHighlights(children_, flattenBlocks(children_), NO_COUNTS), [children_]);
  const scroller = () => wrapRef.current?.querySelector(".pdfViewer");
  const onLoadState = (loadUrl, st) => {
    if (loadUrl !== url || (st.phase !== "layout" && st.phase !== "rendered")) return;
    const entry = restoreRef.current, el = scroller();
    if (!entry || !el) return;
    if (entry.page) {
      const box = el.querySelector(`[data-page="${entry.page}"]`);
      if (!box) return;
      el.scrollTo({ top: Math.max(0, box.offsetTop - 80), behavior: "instant" });
    } else {
      const ratio = (st.scale || effRef.current || 1) / (entry.scale || 1);
      const top = entry.top * ratio;
      if (el.scrollHeight <= top) return;
      el.scrollTo({ top, left: (entry.left || 0) * ratio, behavior: "instant" });
    }
    if (st.phase === "rendered") restoreRef.current = null;
  };
  useEffect(() => {
    const el = scroller();
    const report = () => {
      const s = scroller();
      if (!s || restoreRef.current) return;
      onScroll?.(pageId, { top: s.scrollTop, left: s.scrollLeft, scale: effRef.current });
    };
    reportRef.current = report;
    if (!el) return undefined;
    let raf = 0;
    const onScrollEvent = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; report(); }); };
    el.addEventListener("scroll", onScrollEvent, { passive: true });
    return () => { el.removeEventListener("scroll", onScrollEvent); cancelAnimationFrame(raf); reportRef.current = noop; };
  }, [url, pageId, onScroll, reportRef]);
  return (
    <div className="viewerWrap pagePaneViewer" ref={wrapRef}>
      <PdfViewer url={url} highlights={highlights} marks={marks} pdfScaleValue={zoom}
        onEffectiveScale={(s) => { effRef.current = s; }} onZoomTo={zoomTo} onLoadState={onLoadState}
        darkPage={shared?.darkPage} hideEmbeddedAnnots={shared?.hideEmbeddedAnnots} pageId={pageId} />
    </div>
  );
}

// A page's document of any other format in the pane the live page uses
// (DocumentPane's own viewers), read-only and with no margin or tool: at
// the zoom App opens the page at (the picture's) and at its place
// (`scroll`: the picture's {top, left, scale}, the code view's {line}),
// reporting where it stands through onScroll and keeping a zoom chosen here
// as the page's own (onZoom). The picture's regions are the page's, under
// image_position (app/treeDerived.js pageHighlights), drawn as made on the
// version the page shows now.
function FileView({ view, pageId, doc, children_, zoomIn, scroll, onScroll, onZoom, reportRef, zoomRef }) {
  const [zoom, setZoom] = useState(zoomIn);
  zoomRef.current = zoom;
  const [place] = useState(scroll);
  const fileUrl = documentFileUrl(doc, { asset: assetUrl });
  const regions = useMemo(() => pageHighlights(children_, flattenBlocks(children_), NO_COUNTS, positionKeyOf(doc), fileHash(doc)), [children_, doc]);
  const surface = useMemo(() => ({
    doc, fileUrl, pageId, readOnly: true, preview: true, place, onPlace: (p) => onScroll?.(pageId, p), reportRef, regions,
    scale: { value: zoom, onZoomTo: (z) => { setZoom(z); onZoom?.(pageId, z); }, onEffectiveScale: noop },
  }), [doc, fileUrl, pageId, place, onScroll, reportRef, regions, zoom, onZoom]);
  return (
    <div className="viewerWrap pagePaneViewer">
      <DocumentPane view={view} surface={surface} />
    </div>
  );
}

// The page's sheets in the notebook view, read-only: at the zoom and the
// place App opens the page's notebook view at, keeping its own zoom (the
// page's, onZoom) and reporting its place ({place}, onScroll).
function BookView({ pageId, sheets, marks, zoomIn, place, onScroll, onZoom, reportRef, zoomRef }) {
  const wrapRef = useRef(null);
  const [zoom, setZoom] = useState(zoomIn);
  zoomRef.current = zoom;
  useEffect(() => {
    const el = wrapRef.current?.querySelector(".nbViewer");
    const report = () => { const at = el && readPlace(el); if (at) onScroll?.(pageId, { place: at }); };
    reportRef.current = report;
    if (!el) return undefined;
    let raf = 0;
    const onScrollEvent = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; report(); }); };
    el.addEventListener("scroll", onScrollEvent, { passive: true });
    return () => { el.removeEventListener("scroll", onScrollEvent); cancelAnimationFrame(raf); reportRef.current = noop; };
  }, [pageId, onScroll, reportRef]);
  return (
    <div className="viewerWrap pagePaneViewer" ref={wrapRef}>
      <NotebookViewer key={pageId} place={place} sheets={sheets} marks={marks} scaleValue={zoom}
        onEffectiveScale={noop} onZoomTo={(z) => { setZoom(z); onZoom?.(pageId, z); }} readOnly />
    </div>
  );
}

// The page's notes as the live notes window draws them: the title row
// (crumbs left out: they need the page's family), its labels and its
// buttons at rest, and the tree in BlockTree's preview. Inside a group it
// stands where the live notes stand when a page has no viewer (div.main
// pdfHidden); in the dock it is the window's body.
function NotesView({ pageId, block, children_, title, dock, doc, hasSheets, notebookView, marks, url, notesPlace, shared, press, foldsRef,
  onNotesPlace, onScroll, reportRef }) {
  const listRef = useRef(null);
  const canWrite = !!shared?.canWrite;
  const [folds, setFolds] = useState(() => ({}));
  foldsRef.current = Object.keys(folds).length ? folds : null;
  const view = useMemo(() => ({ editingId: null, folds }), [folds]);
  const byId = useMemo(() => new Map(flattenBlocks(children_).map((b) => [b.id, b])), [children_]);
  const prevLabelsRef = useRef(null);
  const refLabelsById = useMemo(() => {
    const next = refLabelsByBlock(children_, byId, shared?.refCache, prevLabelsRef.current);
    prevLabelsRef.current = next;
    return next;
  }, [children_, byId, shared?.refCache]);
  const sheetNumbers = useMemo(() => new Map(sheetsOf(children_).map((s) => [s.id, s.index + 1])), [children_]);
  const highlightColors = useMemo(() => Object.fromEntries(blocksToHighlights(children_).map((h) => [h.id, h.color])), [children_]);
  // Every handler a row may call: a press, or nothing. The rows' write
  // paths are never reached (no editor mounts), but each is a function, so
  // none is called on undefined.
  const rowProps = useMemo(() => {
    const toRow = (id) => press({ edit: { id, clickPos: null } });
    const handlers = {};
    for (const name of ["onShowOnPage", "onInsertSheet", "onNewPage", "onInvite", "onReminderSet", "onAddSheetAfter", "onEnterAttachMode",
      "onUnlinkHighlight", "onMoveHighlight", "onOpenLinkTarget", "onUndo", "onChangeText", "onCaret", "onResolveMerge", "onMergeOpen",
      "onAddChild", "onNewBlock", "onMergeAbove", "onIndent", "onOutdent", "onHop", "onMoveBlock", "onTranscribe", "onCommentPreload", "onProperties",
      "onPropertiesPreload", "onDuplicate", "onMoveToPage", "onMoveObject", "onPasteBlocks", "onStatus", "onBlockDragOver",
      "onBlockDragLeave", "onBlockDrop"]) handlers[name] = noop;
    return {
      ...handlers,
      // the editor's look for an account that may edit, the viewer's for one that may not
      preview: canWrite, readOnly: !canWrite, rootId: pageId, view, focusedId: null, flashingId: null, foldMoving: null,
      aiMarks: null, aiLive: null, aiScan: null, pages: shared?.pages, sheetNumbers, inlineSheets: !(notebookView && hasSheets),
      captureArea: () => null, docNonce: 0, docKey: url, refLabelsById, highlightColors, refCache: shared?.refCache,
      peers: EMPTY, merges: null, mergeOpen: null, enterNewBlock: shared?.enterNewBlock, keybindings: shared?.keybindings,
      commentCounts: null, commentOpenFor: null,
      setFocusedId: noop, registerRef: noop, getTree: () => children_, lookupBlock: (id) => byId.get(id), mergeNav: () => null,
      onFetchRefs: shared?.onFetchRefs || noop, onCacheRef: shared?.onCacheRef || noop,
      onStartEdit: (id, on, clickPos) => { if (on) press({ edit: { id, clickPos: clickPos || null } }); },
      // A fold is the view's own, as the live notes keep theirs.
      onToggle: (id) => { const b = byId.get(id); if (b) setFolds((f) => ({ ...f, [id]: !isFolded(b, { folds: f }) })); },
      onJump: noop, onBlockRefClick: () => press(), onShowOnPage: () => press(), onDelete: toRow, onEnterSibling: toRow,
      onComment: shared?.commentsOn ? toRow : undefined, onAddToChat: null,
    };
  }, [pageId, view, shared, canWrite, sheetNumbers, notebookView, hasSheets, url, refLabelsById, highlightColors, children_, byId, press]);
  const sheetCtx = useMemo(() => ({
    marks, inkOpen: false, onPen: noop, onPaper: noop, onPaperAll: noop, onAddAfter: noop,
    onNotebookView: !doc ? noop : undefined, onActivate: (id) => press({ edit: { id, clickPos: null } }),
  }), [marks, doc, press]);

  // At the place the page was left at, as the live notes land it (App's
  // landing), and reporting it as the notes scroll.
  const landedRef = useRef(false);
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list || landedRef.current || !children_.length) return;
    landedRef.current = true;
    const row = notesPlace?.id ? list.querySelector(`.blockRowWrap[data-block-id="${notesPlace.id}"]`) : null;
    if (row) list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top + (notesPlace.offset || 0);
  });
  useEffect(() => {
    const list = listRef.current;
    const report = () => {
      onNotesPlace?.(pageId, notesPlaceOf(listRef.current));
      // the sheets' place, when they are in the notes (App lands them there too)
      const at = hasSheets && !notebookView && listRef.current ? readPlace(listRef.current) : null;
      if (at) onScroll?.(pageId, { place: at });
    };
    reportRef.current = report;
    if (!list) return undefined;
    let raf = 0;
    const onScrollEvent = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; report(); }); };
    list.addEventListener("scroll", onScrollEvent, { passive: true });
    return () => { list.removeEventListener("scroll", onScrollEvent); cancelAnimationFrame(raf); reportRef.current = noop; };
  }, [pageId, hasSheets, notebookView, onNotesPlace, onScroll, reportRef]);

  const props = block?.properties || {};
  const chips = shared?.libTree ? filingChips(shared.libTree, { folders: props.folders || [], labels: props.labels || [] }) : { folders: [], labels: [] };
  const sidebar = (
    <div className="sidebar">
      <div className="pageTitleRow">
        <div className="pageTitleMain">
          <h3 className={canWrite ? "titleText editable" : "titleText"} onClick={canWrite ? () => press({ title: true }) : undefined}>
            {block?.content || title || t("Untitled")}
          </h3>
          {shared?.labels ? (
            <div className="categoryFrontmatter">
              <span className="categoryIcon" title={t("Labels")}><LabelIcon size={14} /></span>
              <span className={`categoryFrontmatterValue ${chips.folders.length || chips.labels.length ? "" : "empty"}`}
                onClick={() => press({ labels: true })}>
                {chips.folders.length || chips.labels.length ? (
                  <>
                    {chips.folders.map((f) => <span key={`f:${f.id}`} className="categoryBadge folderChip">{f.name}</span>)}
                    {chips.labels.map((l) => <span key={`l:${l.id}`} className="categoryBadge">{l.name}</span>)}
                  </>
                ) : t("Add labels...")}
              </span>
            </div>
          ) : null}
        </div>
        <div className="pageHeadSide">
          <div className="pageActionCol">
            {shared?.commentsOn ? <button type="button" className="pageActionBtn pageCommentBtn" tabIndex={-1} onClick={() => press()}><MessageSquareIcon size={16} /></button> : null}
            {shared?.labels ? (<>
              {hasSheets && !doc ? (
                <button type="button" className="uiBtn sm ghost" tabIndex={-1} onClick={() => press()}><NotebookIcon size={16} /> {t("Notebook")}</button>
              ) : null}
              {doc || canWrite ? (
                <button type="button" className={`pageActionBtn ${doc ? "active" : ""}`} tabIndex={-1} onClick={() => press()}><PaperclipIcon size={16} /></button>
              ) : null}
              {isPdf(doc) || props.meta ? <button type="button" className="pageActionBtn" tabIndex={-1} onClick={() => press()}><InfoIcon size={16} /></button> : null}
              <button type="button" className="pageActionBtn pageDeleteBtn" tabIndex={-1} disabled={!canWrite} onClick={() => press()}><TrashIcon size={16} /></button>
            </>) : null}
          </div>
        </div>
      </div>
      <div className="blockList" ref={listRef} style={shared?.textScaleStyle}>
        <FileChipContext.Provider value={shared?.fileChipCtx || null}>
          <NoteSheetContext.Provider value={sheetCtx}>
            <BlockTree blocks={children_} readOnly={!canWrite} rowProps={rowProps} zoteroLock={props.zotero_readonly ? pageId : ""} />
          </NoteSheetContext.Provider>
        </FileChipContext.Provider>
      </div>
    </div>
  );
  return dock ? sidebar : <div className="main pdfHidden">{sidebar}</div>;
}
