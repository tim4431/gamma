// The code view's highlights as CodeMirror decorations (docs/dev/documents.md
// "Viewers", "A range of a text file"): what textAnchors.js says each one is
// drawn as, laid on the view's text. A placed range is a mark over its text;
// a lost one, whose old lines hold other text now, is a dashed tag at the
// start of its old first line (the file's last line when it is past the
// end), never a mark over that other text; and every range, placed or lost,
// puts the line cue on each of its lines. One state field holds them, fed
// by `setHighlights` and rebuilt from what it was last fed whenever the
// text changes (Show all, a new version of the file): the server places the
// ranges again, the client only draws them where they say (TextViewer.jsx).
// Beside them, the pure parts of the popup (inCode: TextTip.jsx) and of the
// layers beside the text (hasRoom, standingLine: TextOverlay.jsx). No React
// here: tests/textMarks.test.mjs runs it in node.
import { StateEffect, StateField } from "@codemirror/state";
import { Decoration, EditorView, WidgetType } from "@codemirror/view";
import { lineCueSpec, markSpec, rangeOf } from "./textAnchors.js";
import { CARD_W, COLUMN_GAP, EDGE, marginLeft } from "../../comments/marginLayout.js";
import { textSpan } from "../../shared/model/blockModel.js";
import { t } from "../../shared/i18n/i18n.js";

// {highlights, active, flash, fileHash}: the page's ranges (app/treeDerived.js
// pageHighlights under text_position), the id of the active card's and of
// the one flashing, and the stem of the file the view shows.
export const setHighlights = StateEffect.define();

const NOTHING = Object.freeze({ highlights: [], active: null, flash: null, fileHash: "" });

const sameAttrs = (a, b) => {
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => a[k] === b[k]);
};

// A lost range's tag: an empty dashed box at its old first line, its quote
// as the title; a press on it is the range's (TextViewer's handlers), never
// a selection.
class LostTag extends WidgetType {
  constructor(spec) {
    super();
    this.spec = spec;
  }

  eq(other) {
    return other.spec.class === this.spec.class && sameAttrs(other.spec.attributes, this.spec.attributes);
  }

  toDOM() {
    const el = document.createElement("span");
    el.className = this.spec.class;
    for (const [k, v] of Object.entries(this.spec.attributes)) if (v) el.setAttribute(k, v);
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", t("Not found in the current file: {quote}", { quote: this.spec.attributes.title || "" }));
    return el;
  }

  ignoreEvent() {
    return false;
  }
}

// The decorations of `highlights` on `doc` (a CodeMirror Text): a mark per
// placed range (none for one past the file's end, measured on another
// version and not placed again yet), a tag per lost one, a line cue per
// line of each. Sorted, as a DecorationSet.
export function highlightDecorations(doc, { highlights = [], active = null, flash = null, fileHash = "" } = {}) {
  const ranges = [];
  for (const h of highlights) {
    const at = rangeOf(doc, h.position);
    if (!at) continue;
    const spec = markSpec(h, { active: !!active && h.id === active, flash: !!flash && h.id === flash, fileHash });
    let first, last;
    if (spec.kind === "lost") {
      first = Math.min(h.position.line, doc.lines);
      last = Math.max(first, Math.min(h.position.end, doc.lines));
      ranges.push(Decoration.widget({ widget: new LostTag(spec), side: -1 }).range(doc.line(first).from));
    } else {
      if (at.past) continue;
      if (at.to > at.from) ranges.push(Decoration.mark({ class: spec.class, attributes: spec.attributes }).range(at.from, at.to));
      first = doc.lineAt(at.from).number;
      last = doc.lineAt(at.to).number;
    }
    const cue = lineCueSpec(h);
    const line = Decoration.line({ class: cue.class, attributes: cue.attributes });
    for (let n = first; n <= last; n++) ranges.push(line.range(doc.line(n).from));
  }
  return Decoration.set(ranges, true);
}

// The field: {input, deco}, `input` what setHighlights last said.
export const highlightField = StateField.define({
  create: () => ({ input: NOTHING, deco: Decoration.none }),
  update(value, tr) {
    let input = value.input;
    for (const e of tr.effects) if (e.is(setHighlights)) input = e.value || NOTHING;
    if (input === value.input && !tr.docChanged) return value;
    return { input, deco: highlightDecorations(tr.state.doc, input) };
  },
  provide: (field) => EditorView.decorations.from(field, (value) => value.deco),
});

// The id of the range drawn over the character on `side` of `pos` in
// `state` (1 the one after it, -1 the one before): its mark, the innermost
// (the shortest) where marks nest; null for none, and for a range that only
// touches `pos`. What a press on the text finds when its target is no mark
// of its own.
export function highlightAt(state, pos, side = 1) {
  const [a, b] = side < 0 ? [pos - 1, pos] : [pos, pos + 1];
  let best = null;
  state.field(highlightField, false)?.deco.between(pos, pos, (from, to, deco) => {
    const id = deco.spec.attributes?.["data-hl-id"];
    if (!id || deco.spec.widget || from > a || to < b) return;
    if (!best || to - from < best.to - best.from) best = { id, from, to };
  });
  return best?.id || null;
}

// The range at a DOM event in `view`: {id, el} of the mark or the tag the
// event's target lies in, else of the mark over the character at the
// event's point (the one before the nearest boundary when the point is left
// of it); null off every range, and in the blank past a line's end or
// before its start, which is no character.
export function highlightOfEvent(view, e) {
  const own = e.target?.closest?.("[data-hl-id]");
  if (own && view.contentDOM.contains(own)) return { id: own.getAttribute("data-hl-id"), el: own };
  const pos = view.posAtCoords({ x: e.clientX, y: e.clientY });
  if (pos == null) return null;
  const line = view.state.doc.lineAt(pos);
  const edge = view.coordsAtPos(pos, 1);
  const side = edge && e.clientX < edge.left ? -1 : 1;
  if ((side > 0 && pos >= line.to) || (side < 0 && pos <= line.from)) return null;
  const id = highlightAt(view.state, pos, side);
  if (!id) return null;
  return { id, el: view.contentDOM.querySelector(`[data-hl-id="${CSS.escape(id)}"]`) };
}

// --- the popup and the layers beside the text -------------------------------

const elementOf = (node) => (node?.nodeType === 3 ? node.parentElement : node);

// Whether the document's selection `sel` is the code's text in the view
// whose root is `root` (.cm-editor): not collapsed, both ends in its
// .cm-content and neither in its panels (Find's field). The twin of the
// PDF's inTextLayer (pdf/selectionTip.js).
export function inCode(sel, root) {
  if (!sel || sel.isCollapsed || !sel.rangeCount || !root) return false;
  const range = sel.getRangeAt(0);
  const inside = (node) => {
    const el = elementOf(node);
    return !!el && root.contains(el) && !!el.closest?.(".cm-content") && !el.closest(".cm-panels");
  };
  return inside(range.startContainer) && inside(range.endContainer);
}

// The least width the text keeps beside the comment column (px, past the
// gutters): narrower, the column does not stand and the badges speak for
// the threads.
export const MIN_TEXT_W = 360;
// What the column takes at the view's right edge: the content's right
// padding while it stands, and where its track ends (viewers.css
// --doc-margin-w, which TextViewer sets from it).
export const MARGIN_W = CARD_W + COLUMN_GAP + EDGE;

// Whether a scroller `scrollerW` wide, its gutters `gutterW` of it, has
// room for the column beside MIN_TEXT_W of text (comments/marginLayout.js
// marginLeft's rule: EDGE left at both sides).
export function hasRoom(scrollerW, gutterW) {
  return marginLeft(scrollerW, gutterW + MIN_TEXT_W) != null;
}

// The 1-based line a range's card stands at in a file of `lines` lines
// shown: its first line, the last one when a lost range's old line is past
// the end (as its tag); 0 for a malformed position and for a placed range
// past the lines shown (a file read by its head: no mark is drawn there,
// and a press on its row reads the rest first).
export function standingLine(position, lines) {
  const span = textSpan(position);
  if (!span || lines < 1 || (span.line > lines && !position.lost)) return 0;
  return Math.min(span.line, lines);
}
