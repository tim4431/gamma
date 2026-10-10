import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { copyText } from "../lib/utils";
import { renderMermaid } from "../lib/mermaidRenderer.js";
import { sanitizeSvg } from "../lib/svgSanitize.js";
import { CheckIcon, CodeIcon, CopyIcon, DownloadIcon } from "./Icons";
import { ResizeGrips, useDragResize } from "./ResizeGrip";
import "./diagram.css";
import { t } from "../../shared/i18n/i18n.js";

// The diagram a rendered fence stands for: {kind, source, pending, width}
// from the data-diagram-* props lib/diagramMarkdown.js puts on the fence's
// <code> (and only there: an HTML <code class="language-svg"> in a note is
// code), or null for ordinary code.
export function diagramCodeProps(children) {
  for (const code of React.Children.toArray(children)) {
    const kind = code?.props?.["data-diagram-kind"];
    if (kind !== "mermaid" && kind !== "svg") continue;
    return {
      kind,
      source: String(code.props.children || "").replace(/\n$/, ""),
      pending: code.props["data-diagram-pending"] === "true",
      width: Number(code.props["data-diagram-width"]) || null,
    };
  }
  return null;
}

// What a failed render shows: the reason, translated when the catalog
// knows the sentence, cut to a readable length.
const errorText = (error) => t(String(error?.message || error)).slice(0, 800);

// color-scheme also covers Gamma Light, sepia, solarized and gray themes.
const currentTheme = () => getComputedStyle(document.documentElement).colorScheme === "light" ? "default" : "dark";

// A Mermaid fence renders asynchronously (lib/mermaidRenderer.js), in the
// app's light or dark scheme: {theme, result}, result {svg} or {error} once
// the current source has rendered in the current theme, else null.
function useMermaid(enabled, source) {
  const [theme, setTheme] = useState(currentTheme);
  const [result, setResult] = useState(null);
  useEffect(() => {
    if (!enabled) return undefined;
    setTheme(currentTheme());
    const observer = new MutationObserver(() => setTheme(currentTheme()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-scheme"] });
    return () => observer.disconnect();
  }, [enabled]);
  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    renderMermaid(source, theme, () => cancelled).then((svg) => {
      if (!cancelled) setResult({ source, theme, svg });
    }, (error) => {
      if (!cancelled) setResult({ source, theme, error: errorText(error) });
    });
    return () => { cancelled = true; };
  }, [enabled, source, theme]);
  const current = enabled && result?.source === source && result?.theme === theme;
  return { theme, result: current ? result : null };
}

// An SVG fence is checked at once (lib/svgSanitize.js) and shown as an
// image, a document of its own, so nothing in it reaches the page:
// {svg, src, width, height} or {error}. It has no theme: diagram.css paints
// its page white.
function useSvg(enabled, source) {
  return useMemo(() => {
    if (!enabled) return null;
    try {
      const { svg, width, height } = sanitizeSvg(source);
      return { svg, src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`, width, height };
    } catch (error) {
      return { error: errorText(error) };
    }
  }, [enabled, source]);
}

// A rendered diagram fence, ```mermaid or ```svg. The hover toolbar (the
// image toolbar's flat icon buttons) shows the source, copies it and
// downloads the SVG; with onResize (an editable note) the same edge grips
// as an image drag the diagram's width, written back into the fence's info
// string (`width=420`, see lib/diagramMarkdown.js) — a double-click
// restores the natural size. `tools` end the toolbar (a note's object menu,
// editor/MdObject.jsx).
export function DiagramFigure({ kind, source, pending = false, width = null, idx, onResize, tools = null }) {
  const mermaid = useMermaid(kind === "mermaid" && !pending, source);
  const drawing = useSvg(kind === "svg" && !pending, source);
  const active = kind === "svg" ? drawing : mermaid.result;
  const [showSource, setShowSource] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const figureRef = useRef(null);
  const { dragW, gripProps } = useDragResize({
    measure: () => figureRef.current?.getBoundingClientRect().width,
    bound: () => figureRef.current?.parentElement,
    onCommit: (w) => onResize?.(idx, w),
  });
  useEffect(() => { setCopyStatus(""); }, [source]);
  useEffect(() => {
    if (!copyStatus) return;
    const timer = setTimeout(() => setCopyStatus(""), 2000);
    return () => clearTimeout(timer);
  }, [copyStatus]);
  // The figure gets an explicit width even when the note stores none, so
  // the frame (and its grip) hugs the drawing instead of spanning the note:
  // an SVG's own width (or its viewBox's, as the sanitizer reports it), or
  // the max-width cap Mermaid puts on its SVG (which it sizes to 100% of
  // the container).
  const [measured, setMeasured] = useState(null);
  useLayoutEffect(() => {
    const svg = kind === "mermaid" ? figureRef.current?.querySelector("svg") : null;
    setMeasured(svg ? parseFloat(svg.style.maxWidth) || null : null);
  }, [kind, active?.svg]);
  const natural = kind === "svg" ? active?.width || null : measured;
  const w = dragW != null ? dragW : (width || natural);
  function download() {
    if (!active?.svg) return;
    const url = URL.createObjectURL(new Blob([active.svg], { type: "image/svg+xml;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "diagram.svg";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const stop = (event) => event.stopPropagation();
  return (
    <div className={`diagramCard${showSource ? " showingSource" : ""}`} data-diagram-kind={kind} data-diagram-source={source}
      data-diagram-theme={kind === "mermaid" ? mermaid.theme : undefined}>
      <div className="diagramTools" data-markdown-copy-ignore="" onMouseDown={stop} onClick={stop}>
        <button type="button" className={`ctlBtn${showSource ? " modeActive" : ""}`}
          aria-label={t("Source")} title={showSource ? t("Show diagram") : t("Show source")}
          aria-pressed={showSource} onClick={() => setShowSource(!showSource)}><CodeIcon /></button>
        <button type="button" className="ctlBtn" aria-label={t("Copy source")}
          title={copyStatus === "copied" ? t("Copied") : copyStatus === "failed" ? t("Copy failed") : t("Copy source")}
          onClick={async () => setCopyStatus(await copyText(source) ? "copied" : "failed")}>
          {copyStatus === "copied" ? <CheckIcon /> : <CopyIcon />}
        </button>
        <button type="button" className="ctlBtn" aria-label={t("Download SVG")} title={t("Download SVG")}
          disabled={!active?.svg} onClick={download}><DownloadIcon /></button>
        {tools}
      </div>
      {!active && <div className="diagramStatus" role="status">{pending ? t("Waiting for the diagram to finish…") : t("Rendering diagram…")}</div>}
      {active?.error && <div className="diagramError" role="status">{t("Could not render diagram.")}<pre>{active.error}</pre></div>}
      {active?.svg && !showSource && (
        <div className="diagramPreview">
          <div ref={figureRef} className={`diagramFigure${w ? " sized" : ""}`} style={w ? { width: w } : undefined}>
            {kind === "svg" ? (
              // Not draggable on its own: the note's object frame is the drag source.
              <img className="diagramImg" alt={t("SVG diagram")} src={active.src} draggable={false}
                width={active.width || undefined} height={active.height || undefined} />
            ) : (
              <div className="diagramSvg" role="img" aria-label={t("Mermaid diagram")} dangerouslySetInnerHTML={{ __html: active.svg }} />
            )}
            {onResize ? <ResizeGrips as="div" gripProps={gripProps} /> : null}
          </div>
        </div>
      )}
      {(showSource || pending || active?.error) && <pre className="diagramSource"><code>{source}</code></pre>}
    </div>
  );
}
