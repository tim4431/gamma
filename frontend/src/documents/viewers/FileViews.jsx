// The picture, the player and the file pane (docs/dev/documents.md
// "Viewers"), one chunk of DocumentPane.jsx's, each taking the host's
// surface and reading what it needs.
//
// ImageViewer is a one-page PDF viewer in all but rendering: the shared
// zoom (shared/lib/viewerZoom.js), its fit leaving room for the comment
// margin (comments/marginLayout.js fitBeside), the regions drawn as the
// PDF draws areas and threaded through the same margin, badge and popover
// (comments/), the one region drag (shared/ui/regionDrag.js), and the
// PDF viewer's ref contracts (scrollRef, captureRef, marginRef,
// onMarginLost, reportRef), so App's jumps, flashes, cards and crops work
// on a picture with no branch. MediaViewer plays audio and video and
// pauses when its pane closes. FilePane is what a binary, a listed file, a
// file whose bytes are elsewhere or not here at all (a document at an
// address no viewer reads: `fileUrl` ""), or a file that would not play or
// load shows: its glyph, name, format, size and one sentence, with Download
// and Show in folder.
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { CommentMarginContext, CommentMarginSlot, MarginStackContext, createMarginStack } from "../../comments/marginSlot";
import NoteBadge from "../../comments/NoteBadge.jsx";
import { fitBeside, marginLeft as besideMargin } from "../../comments/marginLayout.js";
import { TipMarks } from "../../pdf/MarkTools.jsx";
import { formatLabel } from "../../library/libraryUtils.js";
import { DownloadIcon, FileGlyph, FolderOpenIcon, MessageSquareIcon, PencilIcon, SparklesIcon } from "../../shared/ui/Icons";
import { placeFloating, useFloatingPosition } from "../../shared/ui/placement.js";
import { menuPress } from "../../shared/ui/press.js";
import { beginRegionDrag } from "../../shared/ui/regionDrag.js";
import { imageFrame, regionFractions, toImagePosition } from "../../shared/ui/regionGeometry.js";
import { usePalette } from "../../shared/ui/paletteContext.js";
import { pickerColors } from "../../shared/model/palette.js";
import { clampZoom } from "../../shared/model/zoom.js";
import { SCALE_VAR, installViewerZoom, scaledPx } from "../../shared/lib/viewerZoom.js";
import { lastPointer } from "../../shared/lib/pointer.js";
import { saveLinkClick } from "../../shared/lib/saveFile.js";
import { fmtBytes } from "../../shared/lib/format.js";
import { fileHash, isListed } from "../pageDocument.js";
import { t } from "../../shared/i18n/i18n.js";
import "./viewers.css";

const EMPTY = Object.freeze([]);
const noop = () => {};
// The layer's padding at every side (viewers.css .imgLayer).
const PAD = 16;
// A crop for the notes' row or the chat is at most this wide or high.
const CAPTURE_MAX = 800;
const FLASH_MS = 1200;
const pct = (f) => `${f * 100}%`;
const sameRect = (a, b) => !!a && !!b && a.x1 === b.x1 && a.y1 === b.y1 && a.x2 === b.x2 && a.y2 === b.y2;
// Whether a zoom setting means "fit the picture" (App's "page-width").
const fitting = (value) => value == null || value === "page-width" || !Number(value);

// --- the picture ----------------------------------------------------------

// `regions`: the page's regions as app/treeDerived.js pageHighlights gives
// them under image_position ({id, note, hasNote, color, threadCount,
// position: {…, area, onVersion}}); `anchors`: its comment anchors
// (commentAnchors); `scale`: {value, onZoomTo, onEffectiveScale}, App's
// pdfScale, zoomTo and setPdfEffScale (a fit is "page-width"); `place`
// {top, left, scale} where it opens, `onPlace` where it stands, `reportRef`
// read by the host before it leaves; `regionMode`: a finger draws (the
// strip's toggle); `onRegion(position, {color, comment})` makes a region
// block (App's addRegion), null when read-only; `onRegionJump`,
// `onRegionContext`, `onRegionThread` as the PDF's highlight handlers;
// `onAskRegion(position)` the chat's picture of it; `commentMargin`,
// `marginRef`, `onMarginLost`, `scrollRef`, `captureRef` as PdfViewer takes
// them; `flashId` the region a jump landed on; `hidden` the pane is closed,
// still mounted (display: none, which loses the scroll and the box). A
// picture that will not load hands over to the file pane, and the next
// version of the file (a new `fileUrl`) mounts a viewer of its own again.
export function ImageViewer(props) {
  const [failedAt, setFailedAt] = useState(null); // the fileUrl that would not load
  if (!props.fileUrl || failedAt === props.fileUrl) return <FilePane doc={props.doc} fileUrl={props.fileUrl} onReveal={props.onReveal} />;
  return <Picture {...props} onFailed={() => setFailedAt(props.fileUrl)} />;
}

function Picture({ doc, fileUrl, readOnly = false, hidden = false, scale = null, place = null, onPlace = null, reportRef = null,
  regions = EMPTY, anchors = EMPTY, flashId = null, regionMode = false, onRegion = null, onRegionJump = null, onRegionContext = null,
  onRegionThread = null, commentMargin = null, marginRef = null, onMarginLost = null, scrollRef = null, captureRef = null,
  onAskRegion = null, onFailed }) {
  const viewerRef = useRef(null);
  const layerRef = useRef(null);
  const boxRef = useRef(null);
  const imgRef = useRef(null);
  const [natural, setNatural] = useState(null); // the loaded picture's own pixels
  const [viewer, setViewer] = useState({ w: 0, h: 0 });
  // The scroller's box, kept while the pane is closed (no box), so the fit
  // and App's zoom stay what they were.
  useEffect(() => {
    const el = viewerRef.current;
    if (!el) return undefined;
    const read = () => {
      const w = el.clientWidth, h = el.clientHeight;
      if (w || h) setViewer((v) => (v.w === w && v.h === h ? v : { w, h }));
    };
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const innerW = Math.max(0, viewer.w - 2 * PAD), innerH = Math.max(0, viewer.h - 2 * PAD);
  // The frame the regions are measured in: the picture's own pixels, else
  // (an SVG with no intrinsic size) the box at fit.
  const frame = useMemo(() => (natural ? imageFrame(natural, { width: innerW, height: innerH }) : null), [natural, innerW, innerH]);

  // The margin (comments/marginSlot.jsx): while a region has an open thread
  // or a new comment's card is open, the picture stands beside the column
  // and the fit leaves room for it.
  const target = commentMargin?.target || null;
  const composing = target?.compose ? target.blockId : "";
  const withMargin = !!commentMargin && anchors.some((a) => a.threadCount > 0 || a.id === composing);
  const fit = frame ? fitBeside(innerW, innerH, frame.width, frame.height, withMargin) : 1;
  const s = fitting(scale?.value) ? fit : clampZoom(Number(scale.value));
  const boxW = frame ? frame.width * s : 0, boxH = frame ? frame.height * s : 0;
  const ml = withMargin && frame ? besideMargin(innerW, boxW) : null;
  // Whether a card could stand beside the picture, asked by App as it opens
  // an anchor's thread, before any card shows: at the fit the column would
  // leave, else at the zoom chosen.
  const room = frame ? besideMargin(innerW, frame.width * (fitting(scale?.value) ? fitBeside(innerW, innerH, frame.width, frame.height, true) : s)) : null;
  const [stack] = useState(createMarginStack);
  const onAnchor = (id) => anchors.some((a) => a.id === id);
  if (marginRef) marginRef.current = (id) => !!commentMargin && room != null && onAnchor(id);
  useEffect(() => () => { if (marginRef) marginRef.current = null; }, [marginRef]);
  const lostRef = useRef(onMarginLost);
  lostRef.current = onMarginLost;
  const targetHere = !!target && onAnchor(target.blockId);
  useEffect(() => { if (targetHere && ml == null) lostRef.current?.(); }, [targetHere, ml]);
  const activeId = target && targetHere && (target.compose || (regions.find((h) => h.id === target.blockId)?.threadCount || 0) > 0) ? target.blockId : null;

  // The zoom (shared/lib/viewerZoom.js): a commit holds a point of the
  // picture still, the one under the cursor or the fingers (`hold`, in the
  // frame's pixels at scale 1 and the view point to put it at), else the
  // view's centre.
  const sRef = useRef(s);
  const holdRef = useRef(null);
  const onZoomToRef = useRef(null);
  onZoomToRef.current = scale?.onZoomTo || null;
  const viewerZoomRef = useRef(null);
  // A point of the view (px from the scroller's corner) as the frame's pixels.
  const framePoint = (vx, vy, sl, st, at) => {
    const el = viewerRef.current, box = boxRef.current;
    if (!el || !box) return null;
    return { cx: (sl + vx - box.offsetLeft) / at, cy: (st + vy - box.offsetTop) / at, vx, vy };
  };
  useEffect(() => {
    const el = viewerRef.current;
    if (!el) return undefined;
    const zoom = installViewerZoom(el, {
      layer: () => layerRef.current,
      clamp: clampZoom,
      held: () => false,
      onWheelZoom: (next, at) => {
        if (!onZoomToRef.current) return;
        holdRef.current = framePoint(at.x, at.y, el.scrollLeft, el.scrollTop, sRef.current);
        onZoomToRef.current(next);
      },
      onPinchZoom: (next, g) => {
        if (Math.abs(next - g.scale) > 1e-3 && onZoomToRef.current) {
          const hold = framePoint(g.mx, g.my, g.sl, g.st, g.scale);
          holdRef.current = hold ? { ...hold, vx: g.vx, vy: g.vy } : null;
          onZoomToRef.current(next);
        } else {
          holdRef.current = null;
          el.scrollLeft = g.sl + g.mx - g.vx;
          el.scrollTop = g.st + g.my - g.vy;
        }
      },
    });
    viewerZoomRef.current = zoom;
    zoom.sync(sRef.current);
    return () => { viewerZoomRef.current = null; zoom.dispose(); };
  }, []);
  const effRef = useRef(scale?.onEffectiveScale);
  effRef.current = scale?.onEffectiveScale;
  useLayoutEffect(() => {
    const el = viewerRef.current, box = boxRef.current, was = sRef.current;
    sRef.current = s;
    const hold = holdRef.current;
    holdRef.current = null;
    if (el && box && was && was !== s) {
      if (hold) {
        el.scrollLeft = box.offsetLeft + hold.cx * s - hold.vx;
        el.scrollTop = box.offsetTop + hold.cy * s - hold.vy;
      } else {
        const k = s / was;
        el.scrollLeft = (el.scrollLeft + el.clientWidth / 2) * k - el.clientWidth / 2;
        el.scrollTop = (el.scrollTop + el.clientHeight / 2) * k - el.clientHeight / 2;
      }
    }
    viewerZoomRef.current?.sync(s);
    effRef.current?.(s);
  }, [s]);

  // Where it stands: reported as it scrolls and when the host asks
  // (`reportRef`), and taken up at `place` once the picture has its size,
  // the offsets scaled to the zoom it opens at. A replaced picture (a new
  // fileUrl) keeps its scale and its scroll fractions. While the pane is
  // closed it stands where it last stood in view, and is put there again
  // as the pane opens.
  const onPlaceRef = useRef(onPlace);
  onPlaceRef.current = onPlace;
  const placeRef = useRef(place);
  const keepRef = useRef(null); // {fx, fy} of the picture being replaced
  const standRef = useRef(null); // {top, left, scale} as it last stood in view
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  const wasHidden = useRef(hidden);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const el = viewerRef.current;
    if (el?.scrollWidth) keepRef.current = { fx: el.scrollLeft / el.scrollWidth, fy: el.scrollTop / el.scrollHeight };
  }, [fileUrl]);
  useLayoutEffect(() => {
    const el = viewerRef.current;
    const shown = wasHidden.current && !hidden;
    wasHidden.current = hidden;
    if (!el || !frame || hidden) return;
    if (shown && !placeRef.current) placeRef.current = standRef.current;
    if (keepRef.current) {
      el.scrollLeft = keepRef.current.fx * el.scrollWidth;
      el.scrollTop = keepRef.current.fy * el.scrollHeight;
      keepRef.current = null;
    } else if (placeRef.current && placeRef.current.top != null) {
      const k = s / (placeRef.current.scale || 1);
      el.scrollTo({ top: placeRef.current.top * k, left: (placeRef.current.left || 0) * k, behavior: "instant" });
      placeRef.current = null;
    }
  }, [frame, s, hidden]);
  useEffect(() => {
    const el = viewerRef.current;
    const report = () => {
      const v = viewerRef.current;
      if (v && !hiddenRef.current) standRef.current = { top: v.scrollTop, left: v.scrollLeft, scale: sRef.current };
      if (standRef.current && onPlaceRef.current) onPlaceRef.current(standRef.current);
    };
    if (reportRef) reportRef.current = report;
    if (!el) return undefined;
    let raf = 0;
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; report(); }); };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => { el.removeEventListener("scroll", onScroll); cancelAnimationFrame(raf); if (reportRef) reportRef.current = noop; };
  }, [reportRef]);

  // A jump scrolls the region into view and flashes it once.
  const [flash, setFlash] = useState(null); // {id, nonce}
  useEffect(() => {
    if (!flash) return undefined;
    const timer = setTimeout(() => setFlash(null), FLASH_MS);
    return () => clearTimeout(timer);
  }, [flash]);
  const regionsRef = useRef(regions);
  regionsRef.current = regions;
  useEffect(() => {
    if (!scrollRef) return undefined;
    scrollRef.current = ({ position, behavior, offset }) => {
      const el = viewerRef.current, box = boxRef.current, f = regionFractions(position);
      if (!el || !box || !f || !frame) return;
      const top = box.offsetTop + f.top * frame.height * sRef.current - (offset ?? 80);
      el.scrollTo({ top, left: Math.max(0, box.offsetLeft + f.left * frame.width * sRef.current - 80), behavior: behavior || "smooth" });
      const hit = regionsRef.current.find((h) => sameRect(h.position?.boundingRect, position?.boundingRect));
      if (hit) setFlash({ id: hit.id, nonce: Date.now() });
    };
    return () => { scrollRef.current = null; };
  }, [scrollRef, frame]);

  // A crop of a region for the notes' row (BlockTree's AreaSnapshot) and
  // the chat: drawn from the loaded picture, same origin, so no taint.
  useEffect(() => {
    if (!captureRef) return undefined;
    captureRef.current = natural ? async (h) => {
      const img = imgRef.current, f = regionFractions(h?.position);
      if (!img?.complete || !f || !frame) return null;
      try {
        const sw = img.naturalWidth || frame.width, sh = img.naturalHeight || frame.height;
        const w = f.width * sw, h2 = f.height * sh;
        const k = Math.min(1, CAPTURE_MAX / Math.max(w, h2));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(w * k));
        canvas.height = Math.max(1, Math.round(h2 * k));
        canvas.getContext("2d").drawImage(img, f.left * sw, f.top * sh, w, h2, 0, 0, canvas.width, canvas.height);
        return canvas.toDataURL("image/png");
      } catch {
        return null;
      }
    } : null;
    return () => { captureRef.current = null; };
  }, [captureRef, natural, frame]);

  // The region drag (shared/ui/regionDrag.js): a mouse or a pen draws (a
  // picture has no text to select), a finger only in region mode (else it
  // pans; two fingers always pinch), never from a card, a badge or the tip,
  // and not at all when read-only. On release the tip asks what the region
  // is for; its box stays drawn meanwhile.
  const [marquee, setMarquee] = useState(null); // the live drag, box px
  const [pending, setPending] = useState(null); // {position, at: {left, top, above}}
  const onBoxPointerDown = (e) => {
    if (readOnly || !onRegion || !frame) return;
    if (e.target.closest?.(".commentMargin, .pdfNoteAnchor, .imgRegionTip")) return;
    if (lastPointer() === "touch" && !regionMode) return;
    const box = boxRef.current;
    if (!box) return;
    beginRegionDrag(e, {
      box: () => box.getBoundingClientRect(),
      onMove: setMarquee,
      onDone: (r, b) => {
        const position = toImagePosition(r, b, frame, fileHash(doc));
        if (position) setPending({ position, at: { left: b.left + r.x1, top: b.top + r.y2 + 8, above: b.top + r.y1 - 8 } });
      },
    });
  };
  const dismiss = useCallback(() => setPending(null), []);
  const finish = (extra) => { const p = pending; setPending(null); if (p) onRegion?.(p.position, extra); };
  useEffect(() => {
    if (!pending) return undefined;
    const down = (e) => { if (!e.target.closest?.(".imgRegionTip")) dismiss(); };
    const key = (e) => { if (e.key === "Escape") { e.preventDefault(); dismiss(); } };
    document.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", key, true);
    return () => { document.removeEventListener("pointerdown", down, true); window.removeEventListener("keydown", key, true); };
  }, [pending, dismiss]);
  useEffect(() => { setPending(null); setMarquee(null); }, [fileUrl]);
  const pendingBox = pending ? regionFractions(pending.position) : null;

  const flashing = flash?.id || flashId;
  return (
    <div ref={viewerRef} className="imgViewer">
      <CommentMarginContext.Provider value={commentMargin}>
      <MarginStackContext.Provider value={stack}>
        <div ref={layerRef} className="imgLayer" style={{ [SCALE_VAR]: String(s) }}>
          <div ref={boxRef} className={`imgBox${frame ? "" : " loading"}${regionMode && !readOnly && onRegion ? " regionMode" : ""}`}
            onPointerDown={onBoxPointerDown}
            style={frame ? { width: scaledPx(frame.width), height: scaledPx(frame.height), margin: ml != null ? `0 0 0 ${ml}px` : "0 auto" } : { margin: "0 auto" }}>
            <img ref={imgRef} src={fileUrl} alt={doc.name} draggable={false} decoding="async"
              onLoad={(e) => setNatural({ width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })}
              onError={onFailed} />
            {frame ? regions.map((h) => {
              const f = regionFractions(h.position);
              if (!f) return null;
              const carded = ml != null && (h.threadCount > 0 || composing === h.id);
              const active = activeId === h.id;
              const earlier = h.position.onVersion === false;
              const openMenu = onRegionContext ? (e) => { e.preventDefault(); onRegionContext({ id: h.id, x: e.clientX, y: e.clientY }); } : null;
              const jump = onRegionJump ? (e) => { e.stopPropagation(); onRegionJump(h.id, e.ctrlKey || e.metaKey); } : null;
              const badge = h.hasNote || (h.threadCount && !carded);
              return (
                <React.Fragment key={h.id}>
                  <div data-hl-id={h.id}
                    className={["imgRegion", h.threadCount ? "commented" : "", active ? "active" : "", earlier ? "earlier" : "",
                      flashing === h.id ? "imgRegionFlash" : "", jump ? "" : "passive"].filter(Boolean).join(" ")}
                    style={{ left: pct(f.left), top: pct(f.top), width: pct(f.width), height: pct(f.height), "--hl": h.color }}
                    title={earlier ? t("Drawn on an earlier version of this picture") : h.note || ""}
                    onClick={jump ? (e) => { jump(e); if (carded) onRegionThread?.(h.id, e.currentTarget); } : undefined}
                    {...menuPress(openMenu)} />
                  {badge ? (
                    <NoteBadge hlId={h.id} text={h.note?.trim() || ""} hasNote={!!h.hasNote} threads={carded ? 0 : h.threadCount || 0}
                      style={{ left: `calc(${pct(f.left + f.width)} + 2px)`, top: `calc(${pct(f.top)} - 8px)` }}
                      onThread={(el) => onRegionThread?.(h.id, el)} onClick={jump} onMenu={openMenu} />
                  ) : null}
                </React.Fragment>
              );
            }) : null}
            {frame && ml != null ? <CommentMarginSlot pageNumber={1} anchors={anchors} pageH={boxH} pageTop={0} /> : null}
            {marquee ? (
              <div className="regionMarquee" style={{ left: marquee.x1, top: marquee.y1, width: marquee.x2 - marquee.x1, height: marquee.y2 - marquee.y1 }} />
            ) : null}
            {pendingBox ? (
              <div className="regionMarquee" style={{ left: pct(pendingBox.left), top: pct(pendingBox.top), width: pct(pendingBox.width), height: pct(pendingBox.height) }} />
            ) : null}
          </div>
        </div>
      </MarginStackContext.Provider>
      </CommentMarginContext.Provider>
      {pending ? (
        <RegionTip at={pending.at} onMark={(color) => finish({ color })} onComment={(color) => finish({ color, comment: true })}
          onAsk={onAskRegion ? () => { const p = pending; dismiss(); onAskRegion(p.position); } : null} onDismiss={dismiss} />
      ) : null}
    </div>
  );
}

// The tip after a drag (the PDF's selection popup's look and press): the
// swatches (TipMarks area: a press makes the region in that colour), then
// Comment (a region with its thread's field open), Note (a region, its
// row's editor open) and Ask AI (the chat's picture of it). Placed under the
// drawn box, over it when the window ends there, kept inside the window.
function RegionTip({ at, onMark, onComment, onAsk, onDismiss }) {
  const ref = useRef(null);
  const palette = usePalette();
  const colors = pickerColors(palette, "highlight");
  const pos = useFloatingPosition(ref, (el) => placeFloating(el.getBoundingClientRect(), { x: at.left, y: at.top, flipY: at.above }),
    [at.left, at.top, at.above]);
  const keep = (e) => { e.preventDefault(); e.stopPropagation(); };
  // A button acts on the press, and on a click no press led to (Enter or
  // Space on it), as the PDF's tip does (shared/ui/press.js triggerPress).
  const act = (run) => ({ onMouseDown: (e) => { keep(e); run(); }, onClick: (e) => { if (!e.detail) run(); } });
  return (
    <div ref={ref} className="plainTip imgRegionTip" style={pos || { top: at.top, left: at.left, visibility: "hidden" }}>
      <TipMarks area act={act} onLeave={onDismiss} onMark={({ color }) => onMark(color)} tools={<>
        <span className="tipDivider" aria-hidden="true" />
        <button type="button" className="colorBtn linkTipBtn" title={t("Comment on this region")} aria-label={t("Comment on this region")}
          {...act(() => onComment(colors[0]))}>
          <MessageSquareIcon size={14} />
        </button>
        <button type="button" className="colorBtn linkTipBtn" title={t("Note on this region")} aria-label={t("Note on this region")}
          {...act(() => onMark(colors[0]))}>
          <PencilIcon size={14} />
        </button>
        {onAsk ? (
          <button type="button" className="colorBtn linkTipBtn" title={t("Ask AI about this region")} aria-label={t("Ask AI about this region")}
            {...act(onAsk)}>
            <SparklesIcon size={14} />
          </button>
        ) : null}
      </>} />
    </div>
  );
}

// --- the player -------------------------------------------------------------

// `<audio>` for an audio document, `<video>` for a video; a .webm (the
// registry's audio, the recordings') is drawn as a video until its metadata
// says it has no picture. Pauses when the pane closes (`hidden`; an
// unmounted element pauses itself as it leaves the document); a file the
// browser cannot play hands over to the file pane. Seeking asks the server
// for ranges (it answers 206).
export function MediaViewer({ doc, fileUrl, hidden = false, onReveal = null }) {
  const ref = useRef(null);
  const asVideo = () => doc.format === "video" || doc.ext === ".webm";
  const [video, setVideo] = useState(asVideo);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); setVideo(asVideo()); }, [fileUrl]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (hidden) ref.current?.pause(); }, [hidden]);
  if (!fileUrl) return <FilePane doc={doc} fileUrl={fileUrl} onReveal={onReveal} />;
  if (failed) return <FilePane doc={doc} fileUrl={fileUrl} onReveal={onReveal} sentence={t("This browser cannot play this file.")} />;
  const onMeta = (e) => { if (doc.format !== "video" && e.currentTarget.videoWidth === 0) setVideo(false); };
  return (
    <div className="mediaViewer">
      {video ? (
        <video ref={ref} controls playsInline preload="metadata" src={fileUrl} onLoadedMetadata={onMeta} onError={() => setFailed(true)} />
      ) : (
        <audio ref={ref} controls preload="metadata" src={fileUrl} onError={() => setFailed(true)} />
      )}
    </div>
  );
}

// --- the file pane ----------------------------------------------------------

// What a file Gamma does not show looks like: its format's glyph, its name,
// its format and size (`doc.size`, else what a HEAD says), and one sentence:
// a binary, a listed file (too large to keep: no bytes here), a document at
// an address (no `fileUrl`: nothing here to read), a file whose bytes are
// on another computer (the GET answers 404: a work folder's file of
// another machine), or the caller's (`sentence`: a file that would not
// play, a text that was binary after all). Download for a file with bytes
// here, Show in folder where the host gives `onReveal` (the desktop shell).
export function FilePane({ doc, fileUrl, onReveal = null, sentence = null }) {
  const listed = isListed(doc);
  const [probe, setProbe] = useState(null); // {status, size} of a HEAD
  useEffect(() => {
    setProbe(null);
    if (listed || !fileUrl) return undefined;
    let on = true;
    fetch(fileUrl, { method: "HEAD" }).then(
      (r) => { if (on) setProbe({ status: r.status, size: Number(r.headers.get("content-length")) || null }); },
      () => { if (on) setProbe({ status: 0, size: null }); },
    );
    return () => { on = false; };
  }, [fileUrl, listed]);
  const size = doc.size ?? probe?.size ?? null;
  const gone = probe?.status === 404;
  const words = sentence || (listed ? t("This file is too large to keep in Gamma. It stays in its folder on this computer.")
    : !fileUrl ? t("Gamma has only this file's address, so there is nothing here to show.")
    : gone ? t("This file's bytes are on another computer.")
      : t("Gamma cannot show this file. Download it to open it in another app."));
  return (
    <div className="filePane">
      <div className="filePaneCard">
        <FileGlyph format={doc.format} />
        <div className="filePaneName">{doc.name}</div>
        <div className="filePaneMeta">{[formatLabel(doc), size != null ? fmtBytes(size) : ""].filter(Boolean).join(" · ")}</div>
        <p className="filePaneHint">{words}</p>
        <div className="filePaneActions">
          {!listed && !gone && fileUrl ? (
            <a className="uiBtn primary" href={fileUrl} download={doc.name} onClick={saveLinkClick(fileUrl, doc.name)}>
              <DownloadIcon size={16} /> {t("Download")}
            </a>
          ) : null}
          {onReveal ? (
            <button type="button" className="uiBtn" onClick={onReveal}><FolderOpenIcon size={16} /> {t("Show in folder")}</button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
