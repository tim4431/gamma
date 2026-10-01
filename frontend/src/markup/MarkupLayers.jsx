// The layers on one page surface, a PDF page (by its number) or a sheet of
// paper (by its id), and the one place they mount: PdfPage, the notebook
// view's sheets and NoteSheet each render <MarkupLayers>. The tools come
// from PageToolsContext, the surface's marks from App's Map (useMarks).
import { useMemo, useRef } from "react";
import { InkLayer, Strokes } from "../ink/InkLayer";
import { usePageTools } from "./PageTools";
import { TextBoxLayer } from "./TextBoxLayer";

const EMPTY = [];
// A surface's marks: its ink groups, the lasso selection and the flashed
// group when they are on it; its text boxes (markup/textBox.js
// textBoxesBySurface), the selected box's id, the edited box's {id, at}
// and the flashed box's {id, nonce} when they are on it.
export const NO_MARKS = Object.freeze({
  ink: EMPTY, inkSelection: null, inkFlash: null, boxes: EMPTY, boxSelection: null, boxEditing: null, boxFlash: null,
});

// Ink items are {id, properties} (all the layer reads), rebuilt on every
// tree change: the same id with the same properties is the same group. A
// box is its block, a new object on every tree change: the same id, text
// and properties are the same box.
const sameInk = (a, b) => a.length === b.length && a.every((x, i) => x.id === b[i].id && x.properties === b[i].properties);
const sameBoxes = (a, b) => a.length === b.length
  && a.every((x, i) => x.id === b[i].id && x.content === b[i].content && x.properties === b[i].properties);
const sameMarks = (a, b) => sameInk(a.ink, b.ink) && a.inkSelection === b.inkSelection && a.inkFlash === b.inkFlash
  && sameBoxes(a.boxes, b.boxes) && a.boxSelection === b.boxSelection && a.boxEditing === b.boxEditing && a.boxFlash === b.boxFlash;

// App's Map surface → marks, from `ink` (Map surface → its ink groups), the
// lasso selection and the ink flash, `boxes` (Map surface → its text
// boxes), `boxSel` (useTextBoxes' {id, editing, at}) and the box flash. A
// slice keeps its identity while its parts are the same, so a memoized page
// or sheet re-renders only for its own marks.
export function useMarks({ ink, inkSelection, inkFlash, boxes, boxSel, boxFlash }) {
  const prevRef = useRef(new Map());
  return useMemo(() => {
    const surfaces = new Set([...ink.keys(), ...boxes.keys()]);
    if (inkSelection) surfaces.add(inkSelection.page);
    const next = new Map();
    for (const surface of surfaces) {
      const groups = ink.get(surface) || EMPTY, here = boxes.get(surface) || EMPTY;
      const on = (mark) => !!mark && here.some((b) => b.id === mark.id);
      const slice = {
        ink: groups, inkSelection: inkSelection?.page === surface ? inkSelection : null,
        inkFlash: inkFlash && groups.some((b) => b.id === inkFlash.id) ? inkFlash : null,
        boxes: here, boxSelection: on(boxSel) && !boxSel.editing ? boxSel.id : null,
        boxEditing: on(boxSel) && boxSel.editing ? boxSel : null, boxFlash: on(boxFlash) ? boxFlash : null,
      };
      const old = prevRef.current.get(surface);
      next.set(surface, old && sameMarks(old, slice) ? old : slice);
    }
    prevRef.current = next;
    return next;
  }, [ink, inkSelection, inkFlash, boxes, boxSel, boxFlash]);
}

// width / height: the surface in points (the PDF page at scale 1, the
// sheet's paper). The text boxes go under the ink. While the Text tool is
// armed the ink layer only draws: the pointer is the text layer's. A
// read-only surface mounts no ink layer without ink. `replay`: a drawing
// shown in the ink's place while it plays (NoteSheet's replay of its
// sheet's handwriting), with no ink layer meanwhile.
export function MarkupLayers({ surface, wrapRef, width, height, marks, replay = null }) {
  const { readOnly, ink, text, actions } = usePageTools();
  const edit = readOnly || text.armed ? {} : actions;
  return (
    <>
      <TextBoxLayer surface={surface} wrapRef={wrapRef} width={width} height={height} marks={marks} />
      {replay ? (
        <svg className="inkLayer" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
          <Strokes ink={replay} />
        </svg>
      ) : readOnly && !marks.ink.length ? null : (
        <InkLayer pageNumber={surface} wrapRef={wrapRef} width={width} height={height} blocks={marks.ink}
          tool={ink.tool} penTool={ink.penTool} penOnly={ink.penOnly} pressure={ink.pressure} eraserMode={ink.eraserMode}
          eraserSize={ink.eraserSize} lassoMode={ink.lassoMode} selection={marks.inkSelection} flash={marks.inkFlash}
          onStroke={edit.onStroke} onErase={edit.onErase} onErasePartial={edit.onErasePartial} onSelect={edit.onSelect}
          onAction={edit.onAction} onMoveSelection={edit.onMoveSelection} onJump={text.armed ? undefined : actions.onJump} />
      )}
    </>
  );
}
