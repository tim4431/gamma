// Moving a rendered object (an image, a table, a Mermaid diagram) around the
// notes as a piece of markdown source. An object is a source range
// {from, to} in its block's content (scanObjects in MdObject.jsx finds
// them); a move is "cut it here, insert it there", so the stored text stays
// the same markdown whatever the object did on screen. Pure: no DOM, no
// scanners — `insertObject` takes a line-start offset (a gap between two
// rendered constructs, clickToSource's gapInSource) or null for the end.
// `moveObjectInTree` is the one tree edit behind every move (the drop, the
// menu's "new block above / below"), so a cross-block move is a single
// transition: one undo step, one op batch.
import { findBlock, insertChild, insertSibling, makeBlockId, setBlockText } from "../shared/model/blockModel.js";

// The object's own lines. `whole` when nothing else shares them (a list
// marker alone doesn't count: the item WAS the picture).
function ownLines(content, obj) {
  const ls = content.lastIndexOf("\n", obj.from - 1) + 1;
  let le = content.indexOf("\n", obj.to);
  if (le === -1) le = content.length;
  const rest = content.slice(ls, obj.from) + content.slice(obj.to, le);
  const whole = !rest.trim() || /^\s*(?:[-+*]|\d+[.)])\s*$/.test(rest);
  return { ls, le, whole };
}

// The object's markdown, `content` without it, and mapOffset: where an
// offset of the old content sits in the new one (null inside the removed
// zone — dropping an object onto itself). Whole-line objects take their
// lines with them and the blank lines around them close up to the wider of
// the two gaps, so the neighbours keep their paragraph / line-break
// relation; an image inside a text line leaves the text alone.
export function cutObject(content, obj) {
  const md = content.slice(obj.from, obj.to);
  const { ls, le, whole } = ownLines(content, obj);
  if (!whole) {
    const out = content.slice(0, obj.from) + content.slice(obj.to);
    const mapOffset = (o) => (o <= obj.from ? o : o >= obj.to ? o - (obj.to - obj.from) : null);
    return { md, content: out, mapOffset };
  }
  const before = content.slice(0, ls), after = content.slice(le);
  const tb = before.match(/\s*$/)[0], ta = after.match(/^\s*/)[0];
  const nb = (tb.match(/\n/g) || []).length, na = (ta.match(/\n/g) || []).length;
  const beforeT = before.slice(0, before.length - tb.length);
  const afterT = after.slice(ta.length);
  const join = beforeT && afterT ? "\n".repeat(Math.min(2, Math.max(nb, na))) : "";
  const zoneFrom = beforeT.length, zoneTo = le + ta.length;
  const out = beforeT + join + afterT;
  const mapOffset = (o) => (o < zoneFrom ? o : o >= zoneTo ? o - zoneTo + zoneFrom + join.length : null);
  return { md, content: out, mapOffset };
}

// `md` inserted as a paragraph of its own at `offset`, a line start (null or
// past the end: appended). Blank lines around the seam are normalized to one
// paragraph break; the next line's indentation is kept (a nested list).
export function insertObject(content, offset, md) {
  if (offset == null || offset >= content.length) {
    const base = content.replace(/\s+$/, "");
    return base ? `${base}\n\n${md}` : md;
  }
  const beforeT = content.slice(0, offset).replace(/\s+$/, "");
  const afterT = content.slice(offset).replace(/^(?:[ \t]*\n)+/, "");
  return [beforeT, md, afterT].filter(Boolean).join("\n\n");
}

// The same block with `obj` moved to `offset` (an offset of the CURRENT
// content); null when the drop lands on the object itself or changes nothing.
export function moveObject(content, obj, offset) {
  const cut = cutObject(content, obj);
  const at = offset == null ? null : cut.mapOffset(offset);
  if (offset != null && at == null) return null;
  const out = insertObject(cut.content, at, cut.md);
  return out === content ? null : out;
}

// The block tree with `obj` (a range of block `sourceId`) moved to `target`:
// {type: "inside", id, offset} — into that block at a line start (null: its
// end); {type: "sibling", id, above} / {type: "child", id} — a new block
// holding just the object, next to / under that block. Null when the move
// changes nothing or a block is gone.
export function moveObjectInTree(blocks, { sourceId, obj, target }) {
  const src = findBlock(blocks, sourceId);
  if (!src) return null;
  const content = src.content || "";
  if (target.type === "inside") {
    if (target.id === sourceId) {
      const v = moveObject(content, obj, target.offset);
      return v == null ? null : setBlockText(blocks, sourceId, v);
    }
    const dst = findBlock(blocks, target.id);
    if (!dst) return null;
    const cut = cutObject(content, obj);
    const out = setBlockText(blocks, sourceId, cut.content);
    return setBlockText(out, target.id, insertObject(dst.content || "", target.offset, cut.md));
  }
  const cut = cutObject(content, obj);
  const block = { id: makeBlockId(), content: cut.md, properties: {}, collapsed: false, editMode: false, children: [] };
  const out = setBlockText(blocks, sourceId, cut.content);
  if (target.type === "child") return insertChild(out, target.id, block, false);
  return insertSibling(out, target.id, block, !target.above);
}
