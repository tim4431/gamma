// The code view (docs/dev/documents.md "Viewers"): a text document read
// in a read-only CodeMirror view, coloured in the notes' code palette by the
// registry's language (codeLanguages.js, each grammar fetched on first use
// from @codemirror/language-data), with line numbers, Find (CodeMirror's
// panel, at the top), Wrap, Download and Ctrl+wheel text size. The file's
// head comes first (textLoad.js: one Range request), the rest on Show all;
// a new version of the file (a new fileUrl) reloads and keeps the top line.
// A file that is binary after all, or whose bytes are elsewhere, hands over
// to the file pane. The CodeMirror packages the view brings are imported
// here alone, so they come in this chunk (DocumentPane.jsx).
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView, highlightSpecialChars, keymap, lineNumbers } from "@codemirror/view";
import { selectAll } from "@codemirror/commands";
import { LanguageDescription, syntaxHighlighting } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { openSearchPanel, search, searchKeymap } from "@codemirror/search";
import { HLJS_CLASSES } from "./hljsTags.js";
import { loadText } from "./textLoad.js";
import { FilePane } from "./FileViews.jsx";
import { codeLanguageName } from "../codeLanguages.js";
import { formatLabel } from "../../library/libraryUtils.js";
import { useTextScale } from "../../shared/ui/Widgets";
import { DownloadIcon, SearchIcon } from "../../shared/ui/Icons";
import { saveLinkClick } from "../../shared/lib/saveFile.js";
import { fmtBytes } from "../../shared/lib/format.js";
import { t, tn } from "../../shared/i18n/i18n.js";
import "./viewers.css";

const noop = () => {};
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
const countLines = (text) => {
  let n = 1;
  for (let i = text.indexOf("\n"); i >= 0; i = text.indexOf("\n", i + 1)) n++;
  return n;
};
// The line at the top of the view, 1-based.
const topLine = (view) => view.state.doc.lineAt(view.lineBlockAtHeight(view.scrollDOM.scrollTop).from).number;
const showLine = (view, n) => {
  const line = view.state.doc.line(Math.max(1, Math.min(n, view.state.doc.lines)));
  view.dispatch({ effects: EditorView.scrollIntoView(line.from, { y: "start" }) });
};

// `doc`, `fileUrl`: the document and where its bytes are read ("" for none
// here: the file pane); `phone`: a phone's layout, where lines wrap by
// default; `hidden`: the pane is closed, still mounted (display: none, which
// loses the scroll), so it shows its line again as it opens; `place` {line}
// where it opens, `onPlace` where it stands, `reportRef` read by the host
// before it leaves (App's captureScrollPos); `onReveal` the desktop's Show
// in folder, for the file pane it may hand over to.
export default function TextViewer({ doc, fileUrl, phone = false, hidden = false, place = null, onPlace = null, reportRef = null,
  onReveal = null }) {
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
  const compartments = useMemo(() => ({ lang: new Compartment(), wrap: new Compartment() }), []);
  const textScale = useTextScale();
  const phrases = useMemo(() => ({
    Find: t("Find"), next: t("next"), previous: t("previous"), all: t("all"), "match case": t("match case"), regexp: t("regexp"),
    "by word": t("by word"), close: t("close"), "current match": t("current match"), "on line": t("on line"), "Go to line": t("Go to line"),
    go: t("go"), "Control character": t("Control character"),
  }), []);

  // The file's head, again for a new version of the file, which keeps the
  // top line; Show all reads the rest. One read at a time: a new one (a new
  // version, Show all) cancels the one under way.
  const readCtl = useRef(null);
  const read = useCallback((whole) => {
    readCtl.current?.abort();
    const ctl = new AbortController();
    readCtl.current = ctl;
    if (viewRef.current) keepLine.current = standing();
    loadText(fileUrl, { whole, signal: ctl.signal }).then((got) => {
      if (ctl.signal.aborted) return;
      setLoad(got.binary ? { status: "binary" } : { status: "ready", ...got });
    }, (err) => {
      if (ctl.signal.aborted) return;
      if (err?.tooLarge) setLoad((prev) => ({ ...prev, tooLarge: true }));
      else setLoad({ status: "gone", httpStatus: err?.status || 0 });
    });
  }, [fileUrl]);
  useEffect(() => {
    if (fileUrl) read(false);
    return () => readCtl.current?.abort();
  }, [read, fileUrl]);

  // The view, once the first text is in, opened at the kept line; later
  // texts (Show all, a new version) replace the document and keep it.
  const ready = load.status === "ready";
  const textRef = useRef("");
  textRef.current = ready ? load.text : "";
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
          syntaxHighlighting(HLJS_CLASSES),
          search({ top: true }),
          keymap.of([...searchKeymap, { key: "Mod-a", run: selectAll }]),
          EditorState.phrases.of(phrases),
        ],
      }),
    });
    viewRef.current = view;
    textScale.ref(view.scrollDOM);
    if (keepLine.current > 1) showLine(view, keepLine.current);
    keepLine.current = 0;
    return () => { textScale.ref(null); view.destroy(); viewRef.current = null; };
  }, [ready, compartments, phrases, textScale.ref]);
  useLayoutEffect(() => {
    const view = viewRef.current;
    if (!view || !ready || view.state.doc.length === load.text.length && view.state.doc.toString() === load.text) return;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: load.text } });
    if (keepLine.current > 1) showLine(view, keepLine.current);
    keepLine.current = 0;
  }, [ready, load.text]);
  useEffect(() => {
    viewRef.current?.dispatch({ effects: compartments.wrap.reconfigure(wrap ? EditorView.lineWrapping : []) });
  }, [wrap, ready, compartments]);

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

  if (!fileUrl || load.status === "binary" || load.status === "gone") return <FilePane doc={doc} fileUrl={fileUrl} onReveal={onReveal} />;
  const lines = ready ? countLines(load.text) : 0;
  const word = formatLabel(doc);
  return (
    <div className="docTextView" style={textScale.style}>
      <div className="docTextBar">
        <span className="docTextLang">{word}</span>
        {ready ? (
          <span className="docTextMeta">
            {tn("{n} line", "{n} lines", lines)}{load.size != null ? ` · ${fmtBytes(load.size)}` : ""}
          </span>
        ) : null}
        <span className="docTextSpacer" />
        <button type="button" className={`uiBtn sm ghost${wrap ? " on" : ""}`} aria-pressed={wrap} onClick={() => setWrap(!wrap)}>{t("Wrap")}</button>
        <button type="button" className="uiBtn sm ghost" disabled={!ready} onClick={() => { const v = viewRef.current; if (v) { openSearchPanel(v); } }}>
          <SearchIcon size={14} /> {t("Find")}
        </button>
        <a className="uiBtn sm ghost" href={fileUrl} download={doc.name} onClick={saveLinkClick(fileUrl, doc.name)}>
          <DownloadIcon size={14} /> {t("Download")}
        </a>
      </div>
      {ready && load.bad ? <div className="docTextNote" role="note">{t("Some bytes are not UTF-8 and show as �.")}</div> : null}
      <div className="docTextBody">
        {textScale.badge}
        <div className="docTextHost" ref={host} aria-busy={!ready || undefined} />
      </div>
      {ready && !load.whole ? (
        <div className="docTextFoot">
          {load.tooLarge ? t("Too large to show here. Download it to read it all.") : (
            <>
              <span>{t("Showing the first {shown} of {size}", { shown: fmtBytes(load.read), size: fmtBytes(load.size ?? load.read) })}</span>
              <button type="button" className="uiBtn sm" onClick={() => read(true)}>{t("Show all")}</button>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
