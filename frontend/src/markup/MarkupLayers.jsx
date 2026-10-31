// The layers on one page surface, a PDF page (by its number) or a sheet of
// paper (by its id), and the one place they mount: PdfPage, the notebook
// view's sheets and NoteSheet each render <MarkupLayers>. The tools come
// from PageToolsContext, the surface's marks from App's Map (useMarks).
import { useMemo, useRef } from "react";
import { InkLayer, Strokes, useHasWet } from "../ink/InkLayer";
import { usePageTools } from "./PageTools";
import { PictureLayer } from "./PictureLayer";
import { TextBoxLayer } from "./TextBoxLayer";

const EMPTY = [];
// A surface's marks: its ink groups, the lasso selection and the flashed
// group when they are on it; its text boxes (markup/textBox.js
// textBoxesBySurface), the selected box's id, the edited box's {id, at}
// and the flashed box's {id, nonce} when they are on it; its pictures
// (markup/picture.js picturesBySurface), the selected picture's id, the
// id of the one being cropped and the flashed one's {id, nonce} when they
// are on it.
export const NO_MARKS = Object.freeze({
  ink: EMPTY, inkSelection: null, inkFlash: null, boxes: EMPTY, boxSelection: null, boxEditing: null, boxFlash: null,
  pictures: EMPTY, pictureSelection: null, pictureCropping: null, pictureFlash: null,
});

// Ink items are {id, properties} (all the layer reads), rebuilt on every
// tree change: the same id with the same properties is the same group. A
// box or a picture is its block, a new object on every tree change: the
// same id, text and properties are the same box (the same picture, whose
// text is its caption).
const sameInk = (a, b) => a.length === b.length && a.every((x, i) => x.id === b[i].id && x.properties === b[i].properties);
const sameBlocks = (a, b) => a.length === b.length
  && a.every((x, i) => x.id === b[i].id && x.content === b[i].content && x.properties === b[i].properties);
const sameMarks = (a, b) => sameInk(a.ink, b.ink) && a.inkSelection === b.inkSelection && a.inkFlash === b.inkFlash
  && sameBlocks(a.boxes, b.boxes) && a.boxSelection === b.boxSelection && a.boxEditing === b.boxEditing && a.boxFlash === b.boxFlash
  && sameBlocks(a.pictures, b.pictures) && a.pictureSelection === b.pictureSelection && a.pictureCropping === b.pictureCropping
  && a.pictureFlash === b.pictureFlash;

// App's Map surface → marks, from `ink` (Map surface → its ink groups), the
// lasso selection and the ink flash, `boxes` (Map surface → its text
// boxes), `boxSel` (useTextBoxes' {id, editing, at}) and the box flash,
// `pictures` (Map surface → its pictures), `pictureSel` (usePictures'
// {id, cropping}) and the picture flash. A slice keeps its identity while
// its parts are the same, so a memoized page or sheet re-renders only for
// its own marks.
export function useMarks({ ink, inkSelection, inkFlash, boxes, boxSel, boxFlash, pictures, pictureSel, pictureFlash }) {
  const prevRef = useRef(new Map());
  return useMemo(() => {
    const surfaces = new Set([...ink.keys(), ...boxes.keys(), ...pictures.keys()]);
    if (inkSelection) surfaces.add(inkSelection.page);
    const next = new Map();
    for (const surface of surfaces) {
      const groups = ink.get(surface) || EMPTY, here = boxes.get(surface) || EMPTY, pics = pictures.get(surface) || EMPTY;
      const on = (mark) => !!mark && here.some((b) => b.id === mark.id);
      const shows = (mark) => !!mark && pics.some((b) => b.id === mark.id);
      const slice = {
        ink: groups, inkSelection: inkSelection?.page === surface ? inkSelection : null,
        inkFlash: inkFlash && groups.some((b) => b.id === inkFlash.id) ? inkFlash : null,
        boxes: here, boxSelection: on(boxSel) && !boxSel.editing ? boxSel.id : null,
        boxEditing: on(boxSel) && boxSel.editing ? boxSel : null, boxFlash: on(boxFlash) ? boxFlash : null,
        pictures: pics, pictureSelection: shows(pictureSel) ? pictureSel.id : null,
        pictureCropping: shows(pictureSel) && pictureSel.cropping ? pictureSel.id : null,
        pictureFlash: shows(pictureFlash) ? pictureFlash : null,
      };
      const old = prevRef.current.get(surface);
      next.set(surface, old && sameMarks(old, slice) ? old : slice);
    }
    prevRef.current = next;
    return next;
  }, [ink, inkSelection, inkFlash, boxes, boxSel, boxFlash, pictures, pictureSel, pictureFlash]);
}

// width / height: the surface in points (the PDF page at scale 1, the
// sheet's paper). The pictures go under the text boxes, and both under the
// ink. While the Text tool is
// armed the ink layer only draws: the pointer is the text layer's. A
// read-only surface mounts no ink layer without ink. `replay`: a drawing
// shown in the ink's place while it plays (NoteSheet's replay of its
// sheet's handwriting), with no ink layer meanwhile. `active`: the surface
// is near the view (a PDF page within its viewer's look-ahead); one that is
// not and carries no marks mounts nothing — each layer listens on the
// window and the document, and a book has hundreds of pages. A read-only
// surface mounts it for peers' strokes in progress too (useHasWet). The
// ruler (ink.ruler) goes to the layer of the surface it lies on, and so
// does the zoom window's box (ink.zoom). `chrome` false is the zoom
// window's own copy of a page's layers (ink/ZoomWindow.jsx): its ink layer
// draws and writes but shows no menu, handles, ruler or window box, opens
// no Paste here, takes no finger chords (the page's layer has them all) and
// raises no finger-scroll hint (nothing scrolls there), and there are no
// text boxes or pictures; onZoomStroke(box) hears of each stroke it commits.
export function MarkupLayers({ surface, wrapRef, width, height, marks, active = true, chrome = true, onZoomStroke }) {
  const { readOnly, ink, text, actions } = usePageTools();
  const wet = useHasWet(surface);
  const edit = readOnly || text.armed ? {} : actions;
  if (!active && !marks.ink.length && !marks.boxes.length && !marks.pictures.length) return null;
  return (
    <>
      {chrome ? <PictureLayer surface={surface} wrapRef={wrapRef} width={width} height={height} marks={marks} /> : null}
      {chrome ? <TextBoxLayer surface={surface} wrapRef={wrapRef} width={width} height={height} marks={marks} /> : null}
      {readOnly && !marks.ink.length && !wet ? null : (
        <InkLayer pageNumber={surface} wrapRef={wrapRef} width={width} height={height} blocks={marks.ink}
          // the pictures a highlighter multiplies into, which the zoom window does not draw
          pictures={chrome ? marks.pictures : undefined}
          tool={ink.tool} penTool={ink.penTool} penOnly={ink.penOnly} pressure={ink.pressure} eraserMode={ink.eraserMode}
          eraserSize={ink.eraserSize} eraserFilter={ink.eraserFilter} drawHold={ink.drawHold} scribble={ink.scribble}
          fingerUndo={ink.fingerUndo} lassoMode={ink.lassoMode} selection={marks.inkSelection} flash={marks.inkFlash}
          clip={!!ink.clip} keys={ink.keys} hoverPreview={ink.hoverPreview !== false}
          onStroke={edit.onStroke} onEraseAlong={edit.onEraseAlong} onEraseEnd={edit.onEraseEnd}
          onEraseMany={edit.onEraseMany} onSelect={edit.onSelect} onPaste={chrome ? edit.onPaste : undefined}
          onAction={edit.onAction} onMoveSelection={edit.onMoveSelection} onJump={text.armed ? undefined : actions.onJump}
          onFingerScroll={chrome ? edit.onFingerScroll : undefined} onHoldStatus={edit.onHoldStatus}
          onInkFeatures={chrome ? actions.onInkFeatures : undefined}
          onInkLive={edit.onInkLive} onHaptic={edit.onHaptic}
          // the ruler lies on the surface it was laid on; it moves while the Text tool is armed too
          ruler={chrome && ink.ruler?.surface === surface ? ink.ruler : null} rulerUnits={ink.rulerUnits}
          onRuler={readOnly ? undefined : actions.onRuler}
          // the finger chords step the block history while the Text tool has the page, so they stay wired then
          onUndoGesture={readOnly || !chrome ? undefined : actions.onUndoGesture}
          // the zoom window's box on its page; while a window is open a blank tap on any page moves it there
          chrome={chrome} onZoomStroke={onZoomStroke} zoom={chrome && ink.zoom?.page === surface ? ink.zoom : null}
          onZoom={chrome && ink.zoom && !readOnly ? actions.onZoom : undefined} />
      )}
    </>
  );
}
