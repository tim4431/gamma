// The lasso selection's edits (useInkSelection.js; docs/dev/handwriting.md
// "Client"), pure: the selected strokes, the changes an edit makes across
// the selection's groups, what each action of the selection menu does,
// the clipboard's shape, and where a paste lands.
// frontend/tests/selectionEdits.test.mjs pins them. A selection is {page,
// items: [{id, ids}], pictures?: [id]}: its surface, per group the block id
// and the selected stroke ids, and the picture blocks the lasso took with
// them (docs/dev/pictures.md "The lasso"), left out when none. `inkOf(id)`
// is a group's strokes now (useInkDocument.js), null for one it does not
// know.
import { mirrorPicture, normalizePicture, transformPicture, turnedBox } from "../markup/picture.js";
import { withPlaced } from "../markup/placed.js";
import { t, T, tn } from "../shared/i18n/i18n.js";
import { findBlock, removeBlockTree } from "../shared/model/blockModel.js";
import { restyleBrush } from "./finishStroke.js";   // the selection's brush change, which bakes the widths again
import { MAX_STROKES, appendStrokes, boundsOf, duplicateStrokes, flipStrokes, removeStrokes, restyleStrokes, transformStrokes, unionBox } from "./ink.js";
import { reshapeInk } from "./shapes.js";   // a shape handle's drag, the stroke written again from its params

// The selected strokes as the groups hold them, in selection order, with
// their union box and the first group's space: {strokes, box, space}, or
// null with none.
export function selectedStrokes(sel, inkOf) {
  if (!sel) return null;
  const strokes = [];
  let box = null, space = null;
  for (const item of sel.items) {
    const ink = inkOf(item.id);
    if (!ink) continue;
    const want = new Set(item.ids);
    const mine = ink.strokes.filter((s) => want.has(s.id));
    if (!mine.length) continue;
    strokes.push(...mine);
    box = unionBox(box, boundsOf(ink, item.ids));
    space = space || ink.space;
  }
  return strokes.length ? { strokes, box, space } : null;
}

// A selection kept aside while a held E or squeeze armed another tool, as
// it stands now: without the strokes erased meanwhile, its pictures kept,
// null when nothing is left.
export function selectionStill(sel, inkOf) {
  const items = (sel?.items || []).map((item) => {
    const have = new Set((inkOf(item.id)?.strokes || []).map((s) => s.id));
    return { ...item, ids: item.ids.filter((id) => have.has(id)) };
  }).filter((item) => item.ids.length);
  return items.length || sel?.pictures?.length ? { ...sel, items } : null;
}

// The selection's pictures as they are now, [{id, pic}], among `shown`, the
// picture blocks its surface draws (markup/picture.js picturesBySurface;
// the ink layer's `pictures`): one gone to another surface is no longer
// the selection's, nor one locked since it took it.
export function selectedPictures(sel, shown) {
  const want = new Set(sel?.pictures || []);
  return (shown || []).filter((b) => want.has(b.id)).map((b) => ({ id: b.id, pic: normalizePicture(b.properties?.picture) }))
    .filter(({ pic }) => pic && !pic.locked);
}

// The selection's box: its strokes' union box with its pictures' turned
// frames (`shown` as selectedPictures takes it), null with neither.
export function selectionBox(sel, inkOf, shown) {
  return selectedPictures(sel, shown).reduce((box, { pic }) => unionBox(box, turnedBox(pic)), selectedStrokes(sel, inkOf)?.box || null);
}

// The pictures' half of an edit: `tree` with each of the pictures `ids`
// replaced by fn(its picture), a locked one left as it is (markup/placed.js
// withPlaced, as markup/usePictures.js `change` edits one).
export function editPictures(tree, ids, fn) {
  return ids.reduce((out, id) => withPlaced(out, id, "picture", (pic) => (pic.locked ? pic : fn(pic))), tree);
}

// Delete's half: `tree` without the pictures `ids`, the notes under them
// too, as a picture's own Delete takes them.
export function removePictures(tree, ids) {
  return ids.reduce((out, id) => removeBlockTree(out, id), tree);
}

// An edit of the selection, group by group (`edit(ink, ids) → ink`): the
// changes of the groups it changed.
export function selectionChanges(sel, inkOf, edit) {
  const changes = [];
  for (const item of sel.items) {
    const before = inkOf(item.id);
    if (!before) continue;
    const after = edit(before, item.ids);
    if (after !== before) changes.push({ id: item.id, page: sel.page, before, after });
  }
  return changes;
}

// The selection menu's actions that edit the selected strokes in place:
// {edit(ink, ids) → ink, label}, the history entry's label in T() (kept
// in English, translated when the pill says it) or t(). A brush change
// bakes the selected pens' widths again (null: the plain pen); a line
// change sets or clears their dash; a fill sets or clears the closed
// shapes' (null: none); else a colour or a width. A shape handle's drag
// writes its stroke again from the new params. A flip turns about the
// centre of `box`, the selection's box (selectionBox). A turn, a resize
// and a flip carry the selection's pictures too: `pictures` is {label, each(pic,
// surface) → pic}, the step's label then and each picture's edit. null for
// any other action, and for a flip with no box.
export function actionEdit(action, value, box) {
  if (action === "style" && "brush" in value) return { edit: (ink, ids) => restyleBrush(ink, ids, value.brush), label: T("ink style change") };
  if (action === "style" && "dash" in value) return { edit: (ink, ids) => restyleStrokes(ink, ids, { dash: value.dash }), label: T("ink line change") };
  if (action === "style" && "fill" in value) return { edit: (ink, ids) => restyleStrokes(ink, ids, { fill: value.fill }), label: T("ink fill change") };
  if (action === "reshape") {
    return { edit: (ink, ids) => (ids.includes(value?.id) ? reshapeInk(ink, value.id, value.params) : ink), label: T("ink shape edit") };
  }
  if (action === "style") return { edit: (ink, ids) => restyleStrokes(ink, ids, value), label: value.color ? t("ink color change") : t("ink width change") };
  if (action === "transform") {
    return { edit: (ink, ids) => transformStrokes(ink, ids, value), label: value.angle ? t("ink rotation") : t("ink resize"),
      pictures: { label: value.angle ? T("selection rotation") : T("selection resize"), each: (pic, surface) => transformPicture(pic, value, surface) } };
  }
  if (action === "flip" && box) {
    const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
    return { edit: (ink, ids) => flipStrokes(ink, ids, { cx, cy, axis: value?.axis }), label: T("ink flip"),
      pictures: { label: T("selection flip"), each: (pic, surface) => mirrorPicture(pic, { cx, cy, axis: value?.axis }, surface) } };
  }
  return null;
}

// Duplicate: copies of the selected strokes `offset` ({dx, dy} in points)
// away, each in its own group → {changes, items} (the copies, to select);
// {refused} when a group would pass MAX_STROKES, before any change.
export function duplicatePlan(sel, inkOf, offset) {
  const changes = [], items = [];
  for (const item of sel.items) {
    const before = inkOf(item.id);
    if (!before) continue;
    const count = before.strokes.filter((s) => item.ids.includes(s.id)).length;
    if (before.strokes.length + count > MAX_STROKES) return { refused: t("This handwriting note is full. Start a new note before duplicating.") };
    const result = duplicateStrokes(before, item.ids, offset.dx, offset.dy);
    if (!result.ids.length) continue;
    changes.push({ id: item.id, page: sel.page, before, after: result.ink });
    items.push({ id: item.id, ids: result.ids });
  }
  return { changes, items };
}

// New note from selection: the selected strokes leave their groups for a
// new group, `makeId()`, on the same surface, their ids kept, as one entry
// across the groups and the new one → {changes, items} (the new group, to
// select). A selection that already is a whole note of its own is
// {refused}; null when nothing is selected.
export function splitPlan(sel, inkOf, makeId) {
  const picked = selectedStrokes(sel, inkOf);
  if (!picked) return null;
  const page = sel.page, changes = [];
  for (const item of sel.items) {
    const before = inkOf(item.id);
    if (!before) continue;
    const after = removeStrokes(before, item.ids);
    if (after.strokes.length !== before.strokes.length) changes.push({ id: item.id, page, before, after });
  }
  if (!changes.length) return null;
  if (changes.length === 1 && !changes[0].after.strokes.length) return { refused: t("This handwriting is already a note of its own.") };
  const id = makeId(), src = changes[0].before;
  changes.push({ id, page, before: { ...src, strokes: [] }, after: { ...src, strokes: picked.strokes } });
  return { changes, items: [{ id, ids: picked.strokes.map((s) => s.id) }] };
}

// The group a selection is the whole of: the id of its one group when
// every stroke of that group is selected, else null (part of a group,
// several groups, pictures alone).
export function wholeGroup(sel, inkOf) {
  if (sel?.items?.length !== 1) return null;
  const { id, ids } = sel.items[0];
  const strokes = inkOf(id)?.strokes;
  if (!strokes?.length || strokes.length !== ids.length) return null;
  const want = new Set(ids);
  return strokes.every((s) => want.has(s.id)) ? id : null;
}

// Merge into note: a selection of one whole group appended to the
// surface's previous group, and itself emptied, as one entry →
// {changes, items} (the strokes as they landed). The previous group is
// the nearest before it with strokes, in the order `surfaces` (the page's
// groups by surface) gives, else the first after it; a group with a
// comment thread (`threaded(id)`, comments/useInkThreads.js) takes no
// strokes, so it is passed over. A group whose block (in `tree`) holds a
// caption or notes is {refused}, since its block would stay behind empty,
// and so is one with a thread, which would lose its drawing, and a merge
// past MAX_STROKES. null when there is nothing to merge.
export function mergePlan(sel, inkOf, tree, surfaces, threaded = () => false) {
  const picked = selectedStrokes(sel, inkOf);
  if (!picked || sel.items.length !== 1) return null;
  const page = sel.page, from = sel.items[0].id, source = inkOf(from);
  if (!source || source.strokes.length !== picked.strokes.length) return null;
  if (threaded(from)) return { refused: t("Not merged: this handwriting has comments.") };
  const block = findBlock(tree, from);
  if ((block?.content || "").trim() || block?.children?.length) return { refused: t("Not merged: this note has a caption or notes under it.") };
  const groups = (surfaces.get(page) || []).map((b) => b.id)
    .filter((gid) => inkOf(gid)?.strokes?.length && (gid === from || !threaded(gid)));
  const at = groups.indexOf(from);
  const targetId = at > 0 ? groups[at - 1] : groups.find((gid) => gid !== from);
  const target = targetId ? inkOf(targetId) : null;
  if (!target) return null;
  if (target.strokes.length + picked.strokes.length > MAX_STROKES) return { refused: t("This handwriting note is full. Move part of it to a new note, or start a new one.") };
  const { ink: after, ids } = appendStrokes(target, picked.strokes);
  return { changes: [{ id: targetId, page, before: target, after }, { id: from, page, before: source, after: { ...source, strokes: [] } }],
    items: [{ id: targetId, ids }] };
}

// Select all: every stroke of a surface's `groups` (their blocks), a group
// with none left out.
export function selectAllItems(groups, inkOf) {
  return (groups || [])
    .map((b) => ({ id: b.id, ids: (inkOf(b.id)?.strokes || []).map((s) => s.id) })).filter((item) => item.ids.length);
}

// Transcribe: the selection's groups that have strokes, as {id, want (the
// selected ids), ink}, and `whole`, whether it holds every stroke of each.
export function transcribeGroups(sel, inkOf) {
  const groups = sel.items.map((item) => ({ id: item.id, want: new Set(item.ids), ink: inkOf(item.id) })).filter((g) => g.ink);
  const whole = groups.length > 0 && groups.every(({ want, ink }) => ink.strokes.length === want.size && ink.strokes.every((s) => want.has(s.id)));
  return { groups, whole };
}
// The chips the selected strokes of those groups go to the chat as, one
// per group with any: {kind: "ink", id, page_id: the open page, space,
// strokes, text}.
export function inkChips(groups, pageId) {
  return groups.map(({ id, want, ink }) => {
    const strokes = ink.strokes.filter((s) => want.has(s.id));
    return { kind: "ink", id, page_id: pageId, space: ink.space, strokes,
      text: tn("Selected handwriting · {n} stroke", "Selected handwriting · {n} strokes", strokes.length) };
  }).filter((c) => c.strokes.length);
}

// The handwriting clipboard of the selected strokes (selectedStrokes):
// {type: "gamma/ink", version: 2, space, box: {x1, y1, x2, y2}, strokes}.
export function clipOf(picked) {
  const [x1, y1, x2, y2] = picked.box;
  return { type: "gamma/ink", version: 2, space: picked.space, box: { x1, y1, x2, y2 }, strokes: picked.strokes };
}

// A paste's shift {dx, dy}: the clipboard box's top-left to `at`, then
// kept inside the surface (`size` in points); on a surface too small for
// the box, its top-left corner stays on.
export function pasteShift(clip, at, size) {
  const { box } = clip, w = box.x2 - box.x1, h = box.y2 - box.y1;
  const x = Math.max(0, Math.min(size.width - w, at.x)), y = Math.max(0, Math.min(size.height - h, at.y));
  return { dx: x - box.x1, dy: y - box.y1 };
}

// Where the clipboard box's top-left goes for the box to sit centred on a
// surface of `size`.
export function centredOn(clip, size) {
  const w = clip.box.x2 - clip.box.x1, h = clip.box.y2 - clip.box.y1;
  return { x: (size.width - w) / 2, y: (size.height - h) / 2 };
}
