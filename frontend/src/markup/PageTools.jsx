// The page tools every surface's layers read (markup/MarkupLayers.jsx): a
// PDF page, a sheet in the notebook view and a sheet among the notes all
// take the same tools from here. App builds them from the owners it
// composes (ink/'s, the text boxes', the pictures') and provides PageToolsContext
// around the viewer and the notes (docs/dev/handwriting.md).
import { createContext, useContext } from "react";

// {readOnly, ink: {tool, penTool, penWritesPdf, penOnly, pressure, eraserMode,
// eraserSize, eraserFilter, drawHold, scribble, fingerUndo, lassoMode,
// lassoPictures, clip, keys, hoverPreview}, text: {armed, style, zotero},
// actions}. ink.lassoPictures: the lasso takes pictures with strokes.
// ink.tool is the armed tool and ink.penTool the stylus pen, which a stylus
// writes with when nothing is armed, on a PDF page only under penWritesPdf
// (ink/pencilActions.js penToolOn); both are null on a read-only page and
// while the Text tool is armed. drawHold, scribble and eraserFilter ('all' | 'pen' |
// 'highlighter') are the account's, fingerUndo ('off' | 'tap' | 'double')
// and hoverPreview (the hover footprint in the preset's look) this
// browser's. ink.clip: the handwriting clipboard holds strokes (a held pen
// or a right-click may then offer Paste here); ink.keys: {copy, cut,
// duplicate}, the chord labels the selection menu puts in its titles (""
// when unbound). ink.ruler: the ruler, {surface, x, y, angle, length,
// snap} in page points (ink/ruler.js), or null; ink.rulerUnits: its ticks,
// 'pt' | 'cm'. While the Shapes tool is armed ink.tool is {tool: "shape",
// kind, fill, style} (ink/shapeTool.js; `style` the stylus pen's look as an
// even Ballpoint line). ink.zoom: the zoom window, {page, x, y, w, h, mag,
// line, x0} in page points (ink/zoomWindow.js), or null. ink.aiWork: the
// handwriting Redraw as shapes or Convert to text is working on, {page,
// box, landed?} in the surface's points (ink/useInkSelection.js), or null.
// text.armed: the
// Text tool is armed; text.style: the style of new boxes ({size, color, bg});
// text.zotero: the workspace has a live Zotero link, so a new box on a PDF
// page is a Zotero box (text_box.zotero, markup/textBox.js).
// actions carries the handlers: the ink ones (onStroke(page, stroke,
// {width, height}, {label}): a stroke and its history entry's label, onEraseAlong(page,
// path, r, {mode, pass}): the path the eraser swept since its last call,
// onEraseEnd, onEraseMany, onSelect, onAction(kind, value) — style,
// transform, reshape {id, params}, delete, duplicate, copy, cut, flip
// {axis}, split, merge, select-note, show-note, transcribe, redraw,
// to-text, copy-svg, download-svg —, onMoveSelection, onPaste(page,
// pt, {width, height}): the clipboard at a page point, onFingerScroll,
// onUndoGesture, onHoldStatus, onInkFeatures(features): a drawing holds
// what this build cannot show (ink.js inkFeatures), onInkLive(page, msg): a chunk of the
// stroke in progress for the page's other peers, ink/inkLive.js,
// onHaptic(kind, at): a held shape or a pen lasso's selection, for the
// native host's haptics, onRuler(next | null): the ruler moved, turned,
// stretched or put away, onZoom(page, {x, y} | {mag} | {at, size}): the
// zoom window dragged, resized, or sent to a tapped point of a page whose
// size in points rides along),
// onJump (show a mark's block in the notes), the text boxes'
// (markup/useTextBoxes.js) and the pictures' (onPicture…,
// markup/usePictures.js) — one object for App's life whose functions call
// the latest handlers (shared/lib/stableActions.js), so the context value
// changes only with the tools and a memoized page never re-renders for a
// new closure.
export const PageToolsContext = createContext(null);
export const usePageTools = () => useContext(PageToolsContext);

// The classes a surface's container takes for the armed tool (app.css,
// markup.css): inkArmed (the crosshair, the Shapes tool's too),
// inkTouchDraw when a finger draws too, textArmed.
export const armedClasses = ({ ink, text }) => (ink.tool ? " inkArmed" : "") + (ink.tool && !ink.penOnly ? " inkTouchDraw" : "")
  + (text.armed ? " textArmed" : "");
