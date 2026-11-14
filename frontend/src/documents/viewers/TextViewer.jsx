// The code view (docs/dev/documents.md "Viewers"): a text document read
// in a read-only CodeMirror view, coloured in the notes' code palette by the
// registry's language (codeLanguages.js, each grammar fetched on first use
// from @codemirror/language-data), with line numbers, Find (CodeMirror's
// search, its panel the app's find row at the top: FindPanel.jsx), Wrap,
// Download and Ctrl+wheel text size. The file's head comes first
// (textLoad.js: one Range request), the rest on Show all;
// a new version of the file (a new fileUrl) reloads and keeps the top line.
// A line link opens it at its lines, marked (textMark.js). The page's
// highlights of the file (text_position) are drawn over its text
// (textMarks.js: a mark, a lost one's tag, the line cue), a selection makes
// one through the popup (TextTip.jsx) and reaches the chat, and their
// comment cards stand in a column beside the text, or their badges at their
// ends (TextOverlay.jsx); a new version of the file draws them where the
// server placed them again, the client never placing one itself. A file
// that is binary after all, or whose bytes are elsewhere, hands over to the
// file pane. The CodeMirror packages the view brings are imported here and
// in its Find row alone, so they come in this chunk, which imports nothing
// of pdf.js (DocumentPane.jsx).
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Compartment, EditorState } from "@codemirror/state";
import { Decoration, EditorView, highlightSpecialChars, keymap, lineNumbers } from "@codemirror/view";
import { selectAll } from "@codemirror/commands";
import { LanguageDescription, syntaxHighlighting } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { gotoLine, openSearchPanel, search, searchKeymap } from "@codemirror/search";
import FindPanel, { findPanelOf } from "./FindPanel.jsx";
import { HLJS_CLASSES } from "./hljsTags.js";
import { cutLinesOf, loadText } from "./textLoad.js";
import { placeMark } from "./textMark.js";
import { rangeOf } from "./textAnchors.js";
import { MARGIN_W, highlightField, highlightOfEvent, inCode, setHighlights } from "./textMarks.js";
import TextTip from "./TextTip.jsx";
import TextOverlay from "./TextOverlay.jsx";
import { FilePane } from "./FileViews.jsx";
import { codeLanguageName } from "../codeLanguages.js";
import { fileHash } from "../pageDocument.js";
import { formatLabel } from "../../library/libraryUtils.js";
import { useMarkTools } from "../../pdf/MarkTools.jsx";
import { textSpan } from "../../shared/model/blockModel.js";
import { selectionTint } from "../../shared/model/markStyle.js";
import { useTextScale } from "../../shared/ui/Widgets";
import { MorphText } from "../../shared/ui/MotionWidgets";
import { menuPress } from "../../shared/ui/press.js";
import { DownloadIcon, SearchIcon } from "../../shared/ui/Icons";
import { chordLabel } from "../../shared/lib/hotkeys.js";
import { saveLinkClick } from "../../shared/lib/saveFile.js";
import { fmtBytes } from "../../shared/lib/format.js";
import { t, tn } from "../../shared/i18n/i18n.js";
import "./viewers.css";

const noop = () => {};
const EMPTY = Object.freeze([]);
// How long a range a jump landed on flashes (ms; the PDF's).
const FLASH_MS = 1200;
// A language's support, once per description (its chunk is fetched once;
// one that failed to come is asked again by the next file of it).
const LOADED = new Map();
const loadLanguage = (name) => {
  const desc = name ? LanguageDescription.matchLanguageName(languages, name, false) : null;
  if (!desc) return Promise.resolve(null);
  if (!LOADED.has(desc.name)) {
    LOADED.set(desc.name, desc.load().catch((err) => { LOADED.delete(desc.name); throw err; }));
  }
  return LOADED.get(desc.name);
};
// The lines CodeMirror splits the text into (\r\n, \r or \n ends one).
const countLines = (text) => {
  let n = 1;
  const breaks = /\r\n?|\n/g;
  while (breaks.exec(text)) n++;
  return n;
};
// The line at the top of the view, 1-based.
const topLine = (view) => view.state.doc.lineAt(view.lineBlockAtHeight(view.scrollDOM.scrollTop).from).number;
const showLine = (view, n) => {
  const line = view.state.doc.line(Math.max(1, Math.min(n, view.state.doc.lines)));
  view.dispatch({ effects: EditorView.scrollIntoView(line.from, { y: "start" }) });
};
const LINE_MARK = Decoration.line({ class: "docLineMark" });
// CodeMirror's search keys but Go to line, whose dialog is CodeMirror's own
// field and buttons, not the app's.
const FIND_KEYS = searchKeymap.filter((b) => b.run !== gotoLine);
const sameSpan = (a, b) => {
  const x = textSpan(a), y = textSpan(b);
  return !!x && !!y && x.line === y.line && x.col === y.col && x.end === y.end && x.end_col === y.end_col;
};
// Whether the offset `pos` stands in the part of the text in view.
const inSight = (view, pos) => view.visibleRanges.some((r) => pos >= r.from && pos <= r.to);
// What the notice says of a mark (placeMark's answer for `mark`, `count`
// lines loaded, `whole` whether that is the file): {text, shown}, `shown`
// false for the plain sentence, which a screen reader alone is told.
function markWords(got, mark, count, whole) {
  if (got.past) {
    return { shown: true, text: whole ? tn("The file has {n} line now; line {line} is past its end.",
      "The file has {n} lines now; line {line} is past its end.", count, { line: mark.line })
      : t("Line {line} is past the part of the file shown here. Download it to read it all.", { line: mark.line }) };
  }
  const said = got.to > got.from ? t("Lines {from}–{to} marked.", { from: got.from, to: got.to }) : t("Line {line} marked.", { line: got.from });
  if (got.moved) return { shown: true, text: `${said} ${t("The line moved: marked where its text is now.")}` };
  if (got.lost) return { shown: true, text: `${said} ${t("Its text is not in the file now: marked by its number.")}` };
  return { shown: false, text: said };
}

// `doc`, `fileUrl`: the document and where its bytes are read ("" for none
// here: the file pane); `phone`: a phone's layout, where lines wrap by
// default; `hidden`: the pane is closed, still mounted (display: none, which
// loses the scroll), so it shows its line again as it opens; `place` {line}
// where it opens, `onPlace` where it stands, `reportRef` read by the host
// before it leaves (App's captureScrollPos, navEntry); `mark` a line link's
// target {line, to, quote}, marked until Escape or a click away, which call
// `onMarkDone` (with `scrollOnly`, Back's return: scrolled to, not marked);
// `onReveal` the desktop's Show in folder, for the file pane it may hand
// over to.
// The highlights (docs/dev/documents.md "A range of a text file"):
// `regions` the page's ranges of this file (app/treeDerived.js
// pageHighlights under text_position, the surface's name for a document's
// placed highlights), `anchors` their comment anchors (commentAnchors),
// `flashId` the one a jump landed on; `commentMargin`, `marginRef`,
// `onMarginLost`, `scrollRef` as PdfViewer takes them (the margin's cards,
// whether a range's card has room, its loss, App's jump to a position);
// `onRegionJump(id, ctrl)`, `onRegionContext({id, x, y})`,
// `onRegionThread(id, el)` what a press on a range does, as a press on a
// PDF highlight; `onAnchor(position, quote, extras)` makes a range from a
// selection ({color, style, comment, highlighter, commentText}: App's
// finishSelection), returning its id; `onHighlightAction(id, action,
// value)` the bar over one the highlighter made (PdfViewer's; without it
// no bar); `onChatSelection(text, additive, {line, end, file})` a
// selection for the chat ("" clears); `moveMode` a range waits to be moved
// to the next selection (the popup offers Move here alone). `readOnly`: the
// ranges draw, no popup; `preview` (a tab group's view): the ranges alone.
export default function TextViewer({ doc, fileUrl, phone = false, hidden = false, place = null, onPlace = null, reportRef = null,
  mark = null, onMarkDone = null, onReveal = null, readOnly = false, preview = false, regions = EMPTY, anchors = EMPTY, flashId = null,
  commentMargin = null, marginRef = null, onMarginLost = null, scrollRef = null, onRegionJump = null, onRegionContext = null,
  onRegionThread = null, onAnchor = null, onHighlightAction = null, onChatSelection = null, moveMode = false }) {
  const host = useRef(null);
  const viewRef = useRef(null);
  const [wrap, setWrap] = useState(phone);
  // {status: "loading" | "ready" | "binary" | "gone", text, bad, cut, size, read, whole, tooLarge}
  const [load, setLoad] = useState({ status: "loading" });
  const keepLine = useRef(place?.line || 0); // the line to show once the text is in
  const standRef = useRef(place?.line || 0); // the top line as it last stood in view
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  // The top line, read off the view while it shows.
  const standing = () => {
    const v = viewRef.current;
    if (v && !hiddenRef.current) standRef.current = topLine(v);
    return standRef.current;
  };
  const compartments = useMemo(() => ({ lang: new Compartment(), wrap: new Compartment(), mark: new Compartment() }), []);
  const textScale = useTextScale();
  // What CodeMirror itself still says: a match announced to a screen reader
  // ("Current match. … on line 4."), a control character's title.
  const phrases = useMemo(() => ({
    "current match": t("Current match"), "on line": t("on line"), "Control character": t("Control character"),
  }), []);
  const [findBox, setFindBox] = useState(null); // Find's panel while it is open, FindPanel drawn into it

  // The file's head, again for a new version of the file, which keeps the
  // top line; Show all reads the rest. One read at a time: a new one (a new
  // version, Show all) cancels the one under way. A new version is read
  // whole again for a mark past its head (`wholeFor`, below). The text
  // keeps the stem of the version it is (`hash`): until a new version's
  // text is in, the view shows the old one's, which the ranges are drawn
  // and measured against.
  const readCtl = useRef(null);
  const wholeFor = useRef(null); // the mark the whole file was read for, in this version
  const docHash = fileHash(doc);
  const [readingAll, setReadingAll] = useState(false); // a whole read under way: Show all says Loading…
  const read = useCallback((whole) => {
    readCtl.current?.abort();
    const ctl = new AbortController();
    readCtl.current = ctl;
    setReadingAll(whole);
    if (viewRef.current) keepLine.current = standing();
    loadText(fileUrl, { whole, signal: ctl.signal }).then((got) => {
      if (ctl.signal.aborted) return;
      setReadingAll(false);
      setLoad(got.binary ? { status: "binary" } : { status: "ready", ...got, url: fileUrl, hash: docHash });
    }, (err) => {
      if (ctl.signal.aborted) return;
      setReadingAll(false);
      if (err?.tooLarge) setLoad((prev) => ({ ...prev, tooLarge: true }));
      else setLoad({ status: "gone", httpStatus: err?.status || 0 });
    });
  }, [fileUrl, docHash]);
  useEffect(() => {
    wholeFor.current = null;
    if (fileUrl) read(false);
    return () => readCtl.current?.abort();
  }, [read, fileUrl]);

  // The view, once the first text is in, opened at the kept line; later
  // texts (Show all, a new version) replace the document and keep it, the
  // marked lines placed on the new text again (below).
  const ready = load.status === "ready";
  const textRef = useRef("");
  textRef.current = ready ? load.text : "";
  const [cm, setCm] = useState(null); // the view, for the layers drawn over it
  const updates = useMemo(() => new Set(), []); // what hears the view's updates (TextOverlay)
  const [beside, setBeside] = useState(false); // the comment column stands
  // What a press on a range does (rangeEvents, below, reads the latest).
  const regionsRef = useRef(regions);
  regionsRef.current = regions;
  const rangeActs = useRef(null);
  rangeActs.current = { onRegionJump, onRegionContext, onRegionThread,
    carded: (id) => beside && (regionsRef.current.find((h) => h.id === id)?.threadCount || 0) > 0 };
  useEffect(() => {
    if (!ready || !host.current) return undefined;
    const view = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: textRef.current,
        extensions: [
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
          EditorView.contentAttributes.of({ tabindex: "0", "aria-label": t("The file's text") }),
          lineNumbers(),
          highlightSpecialChars(),
          compartments.lang.of([]),
          compartments.wrap.of([]),
          compartments.mark.of([]),
          syntaxHighlighting(HLJS_CLASSES),
          search({ top: true, createPanel: findPanelOf(setFindBox) }),
          keymap.of([...FIND_KEYS, { key: "Mod-a", run: selectAll }]),
          EditorState.phrases.of(phrases),
          highlightField,
          rangeEvents(rangeActs),
          EditorView.updateListener.of((u) => { for (const fn of updates) fn(u); }),
        ],
      }),
    });
    viewRef.current = view;
    setCm(view);
    textScale.ref(view.dom); // the whole editor: the text, Find's row and the comment column's band
    if (keepLine.current > 1) showLine(view, keepLine.current);
    keepLine.current = 0;
    return () => { textScale.ref(null); view.destroy(); viewRef.current = null; setCm(null); };
  }, [ready, compartments, phrases, textScale.ref, updates]);
  useLayoutEffect(() => {
    const view = viewRef.current;
    if (!view || !ready || view.state.doc.length === load.text.length && view.state.doc.toString() === load.text) return;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: load.text }, effects: compartments.mark.reconfigure([]) });
    if (keepLine.current > 1) showLine(view, keepLine.current);
    keepLine.current = 0;
  }, [ready, load.text, compartments]);
  useEffect(() => {
    viewRef.current?.dispatch({ effects: compartments.wrap.reconfigure(wrap ? EditorView.lineWrapping : []) });
  }, [wrap, ready, compartments]);

  // The ranges drawn (textMarks.js): again as they, the active card or the
  // flash change; a new text redraws them from what was last said. One the
  // server measured on another version than the text shown reads pending.
  // While a new version's text is on its way (`stale`) the popup makes no
  // range: its lines would be the old text's, stamped as the new one's.
  const hash = ready ? load.hash || "" : "";
  const stale = ready && load.url !== fileUrl;
  const cutLines = useMemo(() => (ready ? cutLinesOf(load.text) : null), [ready, load.text]);
  const target = commentMargin?.target || null;
  const activeId = target && (target.compose || (regions.find((h) => h.id === target.blockId)?.threadCount || 0) > 0) ? target.blockId : null;
  const [flash, setFlash] = useState(null); // {id, nonce}: a jump's
  useEffect(() => {
    if (!flash) return undefined;
    const timer = setTimeout(() => setFlash(null), FLASH_MS);
    return () => clearTimeout(timer);
  }, [flash]);
  const flashing = flash?.id || flashId || null;
  useLayoutEffect(() => {
    cm?.dispatch({ effects: setHighlights.of({ highlights: regions, active: activeId, flash: flashing, fileHash: hash }) });
  }, [cm, regions, activeId, flashing, hash]);

  // App's jump to a range (`scrollRef`: a thread opened, a row's dot): its
  // first line brought into view, centred, unless it is in view already,
  // and flashed. Asked before this version's text is in, while the pane is
  // closed, or past the head read so far (the whole file read first), it
  // waits and runs once the text is there; never on a timer.
  const loadRef = useRef(load);
  loadRef.current = load;
  const pendingJump = useRef(null);
  const jump = useCallback((position) => {
    const view = viewRef.current, got = loadRef.current, span = textSpan(position);
    if (!span) return;
    if (!view || got.status !== "ready" || got.url !== fileUrl || hiddenRef.current) { pendingJump.current = position; return; }
    if (span.line > view.state.doc.lines && !got.whole && !got.tooLarge) {
      pendingJump.current = position;
      read(true);
      return;
    }
    const at = rangeOf(view.state.doc, position);
    if (!at) return;
    if (!inSight(view, at.from)) view.dispatch({ effects: EditorView.scrollIntoView(at.from, { y: "center" }) });
    const hit = regionsRef.current.find((h) => sameSpan(h.position, position));
    if (hit) setFlash({ id: hit.id, nonce: Date.now() });
  }, [fileUrl, read]);
  useEffect(() => {
    if (!scrollRef) return undefined;
    scrollRef.current = (arg) => jump(arg?.position);
    return () => { scrollRef.current = null; };
  }, [scrollRef, jump]);
  useEffect(() => {
    const position = pendingJump.current;
    if (!position || !cm || !ready || hidden || load.url !== fileUrl) return;
    pendingJump.current = null;
    jump(position);
  }, [cm, ready, hidden, load.text, load.url, fileUrl, jump]);
  // A flash from App (a row's dot, a deep link): its range brought into
  // view when it is not.
  useEffect(() => {
    const h = flashId && regionsRef.current.find((x) => x.id === flashId);
    const at = h && cm ? rangeOf(cm.state.doc, h.position) : null;
    if (at && !hiddenRef.current && !inSight(cm, at.from)) cm.dispatch({ effects: EditorView.scrollIntoView(at.from, { y: "center" }) });
  }, [flashId, cm]);

  // The selection in the colour the armed text highlighter will give it
  // (markStyle.js selectionTint, as over the PDF's text).
  const markTools = useMarkTools();
  const armed = markTools.armedId ? markTools.presets.find((p) => p.id === markTools.armedId) : null;
  const tint = !readOnly && !preview && onAnchor && !moveMode ? selectionTint(armed) : "";

  // The language, by the registry's word: a grammar's chunk fetched on first
  // use; a chunk that fails to come (a deploy under an open tab) leaves the
  // text plain. The bar names it as the Format column does (formatLabel).
  const language = codeLanguageName(doc);
  useEffect(() => {
    if (!ready) return undefined;
    let on = true;
    loadLanguage(language).then((support) => {
      if (on) viewRef.current?.dispatch({ effects: compartments.lang.reconfigure(support || []) });
    }, (err) => { console.warn("Could not load the language's grammar; the text shows plain.", err); });
    return () => { on = false; };
  }, [language, ready, compartments]);

  // Where it stands: reported as it scrolls and when the host asks, the
  // line it last showed while the pane is closed; shown again as it opens.
  const onPlaceRef = useRef(onPlace);
  onPlaceRef.current = onPlace;
  useEffect(() => {
    const view = viewRef.current;
    const report = () => { if (viewRef.current && onPlaceRef.current) onPlaceRef.current({ line: standing() || 1 }); };
    if (reportRef) reportRef.current = report;
    if (!view) return undefined;
    const el = view.scrollDOM;
    let raf = 0;
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; report(); }); };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => { el.removeEventListener("scroll", onScroll); cancelAnimationFrame(raf); if (reportRef) reportRef.current = noop; };
  }, [ready, reportRef]);
  const wasHidden = useRef(hidden);
  useEffect(() => {
    const shown = wasHidden.current && !hidden;
    wasHidden.current = hidden;
    const v = viewRef.current;
    if (shown && v && standRef.current > 1) showLine(v, standRef.current);
  }, [hidden]);

  // A line link's lines: once this file's text is in and the pane shows,
  // placed (textMark.js), scrolled to once (a line centred, a range from its
  // top) and marked, the notice saying where; a line or quote past what is
  // loaded reads the whole file first (again in a new version), and only
  // then is a line past the end. A new version of the file, or the whole
  // file read for the mark, places it again without scrolling; pressing
  // Show all ends it, as any press outside the marked lines does. Back's
  // return (`scrollOnly`) scrolls to its line and ends at once. The mark
  // ends too when the view goes (the reader left the page) or hands over
  // to the file pane, which marks nothing.
  const onMarkDoneRef = useRef(onMarkDone);
  onMarkDoneRef.current = onMarkDone;
  const [marked, setMarked] = useState(null); // {text, shown}: the notice of the mark shown
  const placedRef = useRef(null); // the mark scrolled to
  useEffect(() => () => { if (placedRef.current) onMarkDoneRef.current?.(placedRef.current); }, []);
  const filePane = !fileUrl || load.status === "binary" || load.status === "gone";
  useEffect(() => { if (mark && filePane) onMarkDoneRef.current?.(mark); }, [mark, filePane]);
  const unmark = useCallback(() => {
    if (placedRef.current) viewRef.current?.dispatch({ effects: compartments.mark.reconfigure([]) });
    placedRef.current = null;
    setMarked(null);
  }, [compartments]);
  useEffect(() => {
    const view = viewRef.current;
    if (!mark) { unmark(); return; }
    if (!ready || hidden || !view) return;
    if (mark.scrollOnly) {
      unmark();
      showLine(view, mark.line);
      onMarkDoneRef.current?.();
      return;
    }
    // the file's lines, the empty one after a final line break not one of them
    const lines = view.state.doc.toJSON();
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
    const got = placeMark(lines, mark);
    if ((got.past || got.lost) && !load.whole && !load.tooLarge && wholeFor.current !== mark) {
      wholeFor.current = mark;
      read(true);
      return;
    }
    const first = placedRef.current !== mark;
    placedRef.current = mark;
    const effects = [compartments.mark.reconfigure(got.past ? [] : EditorView.decorations.of(Decoration.set(
      Array.from({ length: got.to - got.from + 1 }, (_, i) => LINE_MARK.range(view.state.doc.line(got.from + i).from)))))];
    if (first && !got.past) {
      effects.push(EditorView.scrollIntoView(view.state.doc.line(got.from).from, { y: got.to > got.from ? "start" : "center", yMargin: 48 }));
    }
    view.dispatch({ effects });
    setMarked(markWords(got, mark, lines.length, !!load.whole));
  }, [mark, ready, hidden, load.text, load.whole, load.tooLarge, read, compartments, unmark]);
  // Escape, or a press outside the marked lines, ends the mark (the PDF
  // citation's rule, PdfViewer.jsx).
  useEffect(() => {
    if (!marked) return undefined;
    const done = () => { unmark(); onMarkDoneRef.current?.(); };
    const onKey = (e) => { if (e.key === "Escape") done(); };
    const clickAway = (e) => { if (!(host.current?.contains(e.target) && e.target.closest?.(".docLineMark"))) done(); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", clickAway, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", clickAway, true);
    };
  }, [marked, unmark]);

  // A read that failed but for a 404 (the bytes elsewhere: the file pane's
  // own sentence) offers Try again, which reads the head again.
  const retry = load.status === "gone" && load.httpStatus !== 404 && fileUrl
    ? () => { setLoad({ status: "loading" }); read(false); } : null;
  if (filePane) return <FilePane doc={doc} fileUrl={fileUrl} onReveal={onReveal} onRetry={retry} />;
  const lines = ready ? countLines(load.text) : 0;
  const word = formatLabel(doc);
  const style = { ...textScale.style, "--doc-margin-w": `${MARGIN_W}px`, ...(tint ? { "--selection-color": tint } : null) };
  return (
    <div className={`docTextView${beside ? " withMargin" : ""}${tint ? " tinted" : ""}`} style={style}>
      <div className="docTextBar">
        <span className="docTextLang">{word}</span>
        {ready ? (
          <span className="docTextMeta">
            {tn("{n} line", "{n} lines", lines)}{load.size != null ? ` · ${fmtBytes(load.size)}` : ""}
          </span>
        ) : null}
        <span className="docTextSpacer" />
        <button type="button" className={`uiBtn sm ghost${wrap ? " on" : ""}`} aria-pressed={wrap} onClick={() => setWrap(!wrap)}>{t("Wrap")}</button>
        <button type="button" className="uiBtn sm ghost" disabled={!ready} title={t("{label} ({hint})", { label: t("Find"), hint: chordLabel("Mod-f") })}
          onClick={() => { const v = viewRef.current; if (v) { openSearchPanel(v); } }}>
          <SearchIcon size={14} /> {t("Find")}
        </button>
        <a className="uiBtn sm ghost" href={fileUrl} download={doc.name} onClick={saveLinkClick(fileUrl, doc.name)}>
          <DownloadIcon size={14} /> {t("Download")}
        </a>
      </div>
      {ready && load.bad ? <div className="docTextNote" role="note">{t("Some bytes are not UTF-8 and show as �.")}</div> : null}
      {ready ? <div className={`docMarkNotice${marked?.shown ? "" : " srOnly"}`} role="status" aria-live="polite">{marked?.text || ""}</div> : null}
      <div className="docTextBody">
        {textScale.badge}
        {ready ? null : <div className="docViewHint loadingHint">{t("Loading…")}</div>}
        <div className="docTextHost" ref={host} aria-busy={!ready || undefined} />
      </div>
      {ready && !load.whole ? (
        <div className="docTextFoot">
          {load.tooLarge ? t("Too large to show here. Download it to read it all.") : (
            <>
              <span>{t("Showing the first {shown} of {size}", { shown: fmtBytes(load.read), size: fmtBytes(load.size ?? load.read) })}</span>
              <button type="button" className="uiBtn sm" disabled={readingAll} onClick={() => read(true)}>
                <MorphText>{readingAll ? t("Loading…") : t("Show all")}</MorphText>
              </button>
            </>
          )}
        </div>
      ) : null}
      {cm && !preview ? (
        <>
          <TextTip view={cm} hash={hash} cutLines={cutLines} file={doc.name} highlights={regions} moveMode={moveMode}
            onAnchor={readOnly || stale ? null : onAnchor} onHighlightAction={readOnly ? null : onHighlightAction}
            onChatSelection={onChatSelection} />
          <TextOverlay view={cm} updates={updates} phone={phone} regions={regions} anchors={anchors} commentMargin={commentMargin}
            marginRef={marginRef} onMarginLost={onMarginLost} onRegionJump={onRegionJump} onRegionContext={onRegionContext}
            onRegionThread={onRegionThread} onBeside={setBeside} />
        </>
      ) : null}
      {cm && findBox ? createPortal(<FindPanel view={cm} updates={updates} />, findBox) : null}
    </div>
  );
}

// A press on a range drawn in the text (textMarks.js), read through `acts`
// (a ref of the viewer's handlers): a click jumps to it as a press on a PDF
// highlight does, and makes its card the active one when it has a card; a
// right-click or a held finger opens its menu (shared/ui/press.js
// menuPress); a press on a lost range's tag is the tag's and starts no
// selection. A click that ends a selection is the selection's. Presses on
// the cards, the badges and the popup never reach these: CodeMirror hears
// its content alone.
function rangeEvents(acts) {
  const menuAt = (view, e) => {
    const open = acts.current.onRegionContext;
    const hit = open ? highlightOfEvent(view, e) : null;
    return hit ? menuPress((ev) => { ev.preventDefault(); open({ id: hit.id, x: ev.clientX, y: ev.clientY }); }) : null;
  };
  return EditorView.domEventHandlers({
    mousedown: (e) => !!e.target.closest?.(".docHlLost"),
    click: (e, view) => {
      const { onRegionJump, onRegionThread, carded } = acts.current;
      if (e.button !== 0 || !onRegionJump || inCode(window.getSelection(), view.dom)) return false;
      const hit = highlightOfEvent(view, e);
      if (!hit) return false;
      onRegionJump(hit.id, e.ctrlKey || e.metaKey);
      if (carded(hit.id) && hit.el) onRegionThread?.(hit.id, hit.el);
      return true;
    },
    contextmenu: (e, view) => {
      const press = menuAt(view, e);
      if (!press) return false;
      press.onContextMenu(e);
      return true;
    },
    pointerdown: (e, view) => {
      menuAt(view, e)?.onPointerDown(e);
      return false;
    },
  });
}
