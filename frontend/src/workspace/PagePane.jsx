// A page shown in a tab group that is not the live one
// (docs/dev/tab_groups.md "What a group shows"): a view to read, not the
// workbench. A page with a PDF shows the PDF with its highlights, scrolled
// and zoomed like the viewer; a page without one shows its notes rendered.
// A press on the pane makes its group live, and the page opens in the
// workbench where the view left it: the pane reports its scroll through
// `onScroll(pageId, entry)` in the shape App's tab scroll store keeps ({top, scale}), and
// `scroll` is that store's entry when the pane mounts. The tree is read
// once per page shown, laid over with the page's own unsaved edits
// (`overlay`, the collab session's), so a page just left live shows what
// was typed.
import React, { useEffect, useMemo, useRef, useState } from "react";
import PdfViewer from "../pdf/PdfViewer";
import { PageToolsContext } from "../markup/PageTools.jsx";
import { BlockMarkdown } from "../editor/BlockTree";
import { NO_LABELS } from "../editor/refLabels.js";
import { blockQuote, blocksToHighlights, isHighlightBlock, isPlainNote } from "../shared/model/blockModel.js";
import { pageAttachment } from "../library/libraryUtils.js";
import { API, apiJson, attachmentViewerUrl } from "../shared/lib/utils";
import { highlightColor } from "../shared/model/palette.js";
import { t } from "../shared/i18n/i18n.js";

// The tools a read-only surface takes: no pen armed, nothing to act on.
const READ_TOOLS = Object.freeze({
  readOnly: true,
  ink: { tool: null, penTool: null, penOnly: false, clip: false, keys: {}, ruler: null, zoom: null, aiWork: null },
  text: { armed: false, style: null, zotero: false },
  actions: {},
});
const NO_MARKS = new Map();
const PRESS_SLOP = 8;

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

export default function PagePane({ pageId, title, overlay, scroll, onScroll, onActivate }) {
  const [page, setPage] = useState({ id: null, block: null, children: [], error: "" });
  useEffect(() => {
    let on = true;
    setPage({ id: null, block: null, children: [], error: "" });
    apiJson(`${API}/blocks/${pageId}/subtree`).then((data) => {
      if (!on) return;
      const children = data.block?.children || [];
      setPage({ id: pageId, block: data.block, children: overlay ? overlay(pageId, children) : children, error: "" });
    }, (err) => { if (on) setPage({ id: pageId, block: null, children: [], error: err?.message || String(err) }); });
    return () => { on = false; };
  }, [pageId]); // eslint-disable-line react-hooks/exhaustive-deps
  const attachment = pageAttachment(page.block);
  const url = attachment ? attachmentViewerUrl(attachment.url) : "";
  const highlights = useMemo(() => blocksToHighlights(page.children), [page.children]);

  // A press (down and up within a few pixels) makes the group live; a drag,
  // a scroll or a text selection does not. It is the primary button's or
  // the first finger's, and never one on a scrollbar: Windows and Linux
  // draw classic scrollbars that take presses. It is decided on the click,
  // which a right button does not send (Windows sends its contextmenu after
  // the release, which would then open over the page made live) and which
  // the pane takes before it gives way, so the click does not land on the
  // workbench that replaces it.
  const pressRef = useRef(null);
  const onPointerDown = (event) => {
    pressRef.current = event.button === 0 && event.isPrimary && !onScrollbar(event) ? { x: event.clientX, y: event.clientY } : null;
  };
  const onClick = (event) => {
    const p = pressRef.current;
    pressRef.current = null;
    if (p && Math.hypot(event.clientX - p.x, event.clientY - p.y) <= PRESS_SLOP) onActivate();
  };

  // The viewer's scroll, reported as the tab's reading position, and the
  // position the pane opens at, applied as the document's boxes are laid
  // out, scaled from the scale they were measured at.
  const wrapRef = useRef(null);
  const [scale, setScale] = useState("page-width");
  const effRef = useRef(1);
  const restoreRef = useRef(scroll || null);
  useEffect(() => { restoreRef.current = scroll || null; }, [pageId]); // eslint-disable-line react-hooks/exhaustive-deps
  const onLoadState = (loadUrl, st) => {
    if (loadUrl !== url || (st.phase !== "layout" && st.phase !== "rendered")) return;
    const entry = restoreRef.current;
    const scroller = wrapRef.current?.querySelector(".pdfViewer");
    if (!entry || entry.top == null || !scroller) return;
    const top = entry.top * ((st.scale || effRef.current || 1) / (entry.scale || 1));
    if (scroller.scrollHeight > top) { scroller.scrollTo({ top, behavior: "instant" }); if (st.phase === "rendered") restoreRef.current = null; }
  };
  useEffect(() => {
    const scroller = wrapRef.current?.querySelector(".pdfViewer");
    if (!scroller || !onScroll) return undefined;
    let raf = 0;
    const report = () => { raf = 0; onScroll(pageId, { top: scroller.scrollTop, scale: effRef.current }); };
    const onScrollEvent = () => { if (!raf) raf = requestAnimationFrame(report); };
    scroller.addEventListener("scroll", onScrollEvent, { passive: true });
    return () => { scroller.removeEventListener("scroll", onScrollEvent); cancelAnimationFrame(raf); };
  }, [url, page.id, pageId, onScroll]);

  let body;
  if (page.error) body = <div className="pagePaneHint">{t("Could not open this page: {message}", { message: page.error })}</div>;
  else if (!page.id) body = <div className="pagePaneHint" aria-busy="true" />;
  else if (url) {
    body = (
      <div className="viewerWrap pagePaneViewer" ref={wrapRef}>
        <PageToolsContext.Provider value={READ_TOOLS}>
          <PdfViewer url={url} highlights={highlights} marks={NO_MARKS} pdfScaleValue={scale}
            onEffectiveScale={(s) => { effRef.current = s; }} onZoomTo={setScale} onLoadState={onLoadState}
            pageId={pageId} />
        </PageToolsContext.Provider>
      </div>
    );
  } else {
    body = (
      <div className="pagePaneNotes">
        <h3>{title || page.block?.content || t("Untitled")}</h3>
        <ReadBlocks blocks={page.children} depth={0} />
      </div>
    );
  }
  return (
    <div className="pagePane" data-page-id={pageId} onPointerDown={onPointerDown} onClick={onClick}>
      {body}
    </div>
  );
}

// The notes rendered: each block's text as a note shows it, a highlight's
// quote over its note, children under their parent. An empty plain note is a
// blank line, the gap it is in the live view; handwriting, sheets and other
// blocks without text are left out.
function ReadBlocks({ blocks, depth }) {
  return blocks.map((block) => {
    const quote = isHighlightBlock(block) ? blockQuote(block) : "";
    const text = block.content || "";
    const rows = block.children?.length ? <ReadBlocks blocks={block.children} depth={depth + 1} /> : null;
    const gap = !quote && !text.trim() && isPlainNote(block);
    if (!quote && !text.trim() && !rows && !gap) return null;
    return (
      <div key={block.id} className="readBlock" data-block-id={block.id} style={{ "--depth": depth }}>
        {quote ? <blockquote className="readQuote" style={{ "--hl-color": highlightColor(block.properties?.color) }}>{quote}</blockquote> : null}
        {text.trim() ? <div className="blockRendered"><BlockMarkdown content={text} blockId={`pane:${block.id}`} refLabels={NO_LABELS} nested /></div> : null}
        {gap ? <div className="blockRendered"><p>&nbsp;</p></div> : null}
        {rows}
      </div>
    );
  });
}
