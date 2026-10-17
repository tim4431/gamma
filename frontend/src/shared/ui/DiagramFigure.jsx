import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { copyText } from "../lib/utils";
import { shownBackground, shownSvg } from "../lib/copyImage.js";
import { renderMermaid } from "../lib/mermaidRenderer.js";
import { sanitizeSvg } from "../lib/svgSanitize.js";
import { CheckIcon, CodeIcon, CopyIcon, DownloadIcon, ZoomInIcon } from "./Icons";
import { Lightbox, lightboxFit } from "./Lightbox";
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

// The drawing as one picture for the lightbox: an SVG document of its own
// on the background it is shown on (lib/copyImage.js, as "Copy image"
// makes it), sized to fill the window, so it stays sharp at any zoom. Both
// kinds' markup is well-formed XML (the sanitizer and the Mermaid renderer
// serialize it).
function zoomedSrc(picture, svg) {
  const box = picture.getBoundingClientRect();
  if (!box.width || !box.height) return null;
  const zoomed = shownSvg(svg, { ...lightboxFit(box.width, box.height), background: shownBackground(picture) });
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(zoomed)}`;
}

// A rendered diagram fence, ```mermaid or ```svg. The hover toolbar (the
// image toolbar's flat icon buttons) zooms the drawing, shows the source,
// copies it and downloads the SVG; with onResize (an editable note) the
// same edge grips as an image drag the diagram's width, written back into
// the fence's info string (`width=420`, see lib/diagramMarkdown.js) — a
// double-click restores the natural size. Without it (a chat reply, a
// read-only note) a click on the drawing zooms it, like a read-only note
// image; an editable note's click selects the object instead. `tools` end
// the toolbar (a note's object menu, editor/MdObject.jsx).
export function DiagramFigure({ kind, source, pending = false, width = null, idx, onResize, tools = null }) {
  const mermaid = useMermaid(kind === "mermaid" && !pending, source);
  const drawing = useSvg(kind === "svg" && !pending, source);
  const active = kind === "svg" ? drawing : mermaid.result;
  const [showSource, setShowSource] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const [zoomed, setZoomed] = useState(null); // the lightbox's src
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
  const drawn = !!active?.svg && !showSource;
  function zoom(event) {
    event.stopPropagation();
    const picture = figureRef.current?.querySelector(".diagramImg, .diagramSvg > svg");
    const src = picture && zoomedSrc(picture, active.svg);
    if (src) setZoomed(src);
  }
  const stop = (event) => event.stopPropagation();
  return (
    <div className={`diagramCard${showSource ? " showingSource" : ""}`} data-diagram-kind={kind} data-diagram-source={source}
      data-diagram-theme={kind === "mermaid" ? mermaid.theme : undefined}>
      <div className="diagramTools" data-markdown-copy-ignore="" onMouseDown={stop} onClick={stop}>
        {/* First, so the strip's other buttons keep their place while the source hides it. */}
        {drawn ? <button type="button" className="ctlBtn" aria-label={t("Zoom")} title={t("Zoom")}
          onClick={zoom}><ZoomInIcon /></button> : null}
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
      {drawn && (
        <div className="diagramPreview">
          <div ref={figureRef} className={`diagramFigure${w ? " sized" : ""}${onResize ? "" : " zoomable"}`}
            style={w ? { width: w } : undefined} onClick={onResize ? undefined : zoom}>
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
      {zoomed ? <Lightbox src={zoomed} alt={kind === "svg" ? t("SVG diagram") : t("Mermaid diagram")} onClose={() => setZoomed(null)} /> : null}
    </div>
  );
}
