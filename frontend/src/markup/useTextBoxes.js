// Text boxes, App's side (docs/dev/text_boxes.md): which box is selected
// or open in its editor (one at most in the whole app), the box a jump
// outlines, the style of new boxes, and every edit of a box. A box is a
// block, so each edit is a tree edit through setBlocks: the op diff sends
// it, the three-way merge sees its text, the block history records it. App
// wires the actions into PageToolsContext and the state into the page
// marks; markup/TextBoxLayer.jsx draws the boxes and reports their size.
//
// A box's `w` (auto width) and `h` are what it measured at on this screen.
// They are written only after an edit here changed the box or its text,
// folded into that edit's undo step (`foldBlocks`). A passive render (a
// page opening, another client's edit arriving) never writes, so two
// clients whose fonts measure differently cannot write back and forth.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { BLOCK_COMMANDS, GROUP_FORMAT } from "../editor/blockCommands.js";
import { dispatch } from "../shared/lib/hotkeys.js";
import {
  findBlock, insertSibling, makeBlockId, removeBlockTree, setBlockText, updateBlockTree,
} from "../shared/model/blockModel.js";
import { normalizeTextBox, refitBox, textBoxesBySurface, textStyle } from "./textBox.js";

// A box's editor takes the block editor's formatting keys (Ctrl+B, I, E,
// K …) but not its block commands: Enter is a new line in the box.
const FORMAT_COMMANDS = BLOCK_COMMANDS.filter((c) => c.group === GROUP_FORMAT);

// `tree` with box `id` replaced by fn(its box), the same tree when that
// changes nothing.
function withBox(tree, id, fn) {
  const box = normalizeTextBox(findBlock(tree, id)?.properties?.text_box);
  const next = box && fn(box);
  if (!next || JSON.stringify(next) === JSON.stringify(box)) return tree;
  return updateBlockTree(tree, id, (b) => ({ ...b, properties: { ...b.properties, text_box: next } }));
}

// blocks, blocksRef, setBlocks: the open page's tree; foldBlocks(fn) is
// setBlocks for a write that belongs to the edit before it. pageId: the
// open page. rowEditingId / closeRowEditor(): the notes' open row editor,
// never open beside a box's. style / setStyle: the textBoxStyle
// preference. keybindings: the account's. inert: an ink tool is armed,
// which boxes do not answer to. onSelect(): a box was selected or opened
// (App lets go of the lasso's selection: one selection in the app).
export function useTextBoxes({ blocks, blocksRef, setBlocks, foldBlocks, readOnly, pageId, rowEditingId, closeRowEditor,
  style, setStyle, keybindings, inert, onSelect }) {
  // {id, editing, at}: the selected box, or the box being edited (`at`:
  // where the tap that opened it landed, for the caret; null: at the end).
  const [sel, setSelState] = useState(null);
  const selRef = useRef(null);
  const setSel = (next) => {
    selRef.current = next;
    setSelState(next);
    if (next) onSelect();
  };
  const [flash, setFlash] = useState(null);
  // The box made here and not typed in yet: its changes (a restyle, the
  // handle, a move) fold into its making, so left empty it goes as if it
  // had never been, without an undo entry (blockHistory.js gives back the
  // redo steps its making cleared).
  const freshRef = useRef(null);
  // What was last typed into a box's editor, until the tree has it.
  const typedRef = useRef(null);
  // Boxes an edit here just changed, marked as the tree takes the edit (an
  // edit that changes nothing marks none). The layer measures them in the
  // commit that shows the edit (its layout effects run before this hook's);
  // a box it did not reach by then is not measured.
  const measureRef = useRef(new Set());
  useLayoutEffect(() => { measureRef.current.clear(); }, [blocks]);
  const measured = (id, next, prev) => {
    if (next !== prev) measureRef.current.add(id);
    return next;
  };
  const bySurface = useMemo(() => textBoxesBySurface(blocks), [blocks]);
  // The row a press landed on while a box was edited: a press on the box's
  // own row opens that row's editor, which takes the box over (its click
  // comes after the box's editor has lost the focus).
  const pressRowRef = useRef(null);
  useEffect(() => {
    if (!sel?.editing) return undefined;
    const down = (e) => { pressRowRef.current = e.target.closest?.(".blockRowWrap")?.getAttribute("data-block-id") ?? null; };
    document.addEventListener("pointerdown", down, true);
    return () => document.removeEventListener("pointerdown", down, true);
  }, [sel?.editing]);

  useEffect(() => {
    const typed = typedRef.current;
    if (typed && findBlock(blocks, typed.id)?.content === typed.text) typedRef.current = null;
    // A box that is gone (undone, deleted here or by someone else) is
    // neither selected nor edited.
    if (selRef.current && !findBlock(blocks, selRef.current.id)) {
      freshRef.current = typedRef.current = null;
      setSel(null);
    }
  }, [blocks]); // eslint-disable-line react-hooks/exhaustive-deps

  const remove = (id, fold) => (fold ? foldBlocks : setBlocks)((prev) => (findBlock(prev, id) ? removeBlockTree(prev, id) : prev));
  // Leave the selected or edited box. An edited box that is empty, with no
  // notes under it, goes (unless the notes' editor opens on it: it did, or
  // the press that closed this one is on its row); `keep` leaves any other
  // selected.
  function leave(keep, keepEmpty = false) {
    const cur = selRef.current;
    if (!cur) return;
    const block = findBlock(blocksRef.current, cur.id);
    const text = typedRef.current?.id === cur.id ? typedRef.current.text : block?.content || "";
    const toRow = pressRowRef.current === cur.id;
    pressRowRef.current = null;
    const empty = cur.editing && block && !text.trim() && !block.children?.length && !keepEmpty && !toRow;
    if (empty) remove(cur.id, freshRef.current === cur.id);
    freshRef.current = null;
    typedRef.current = null;
    setSel(keep && !empty ? { id: cur.id, editing: false, at: null } : null);
  }
  // Opening the notes' editor closes a box's; arming an ink tool lets go of any box.
  useEffect(() => {
    if (rowEditingId) leave(false, rowEditingId === selRef.current?.id);
  }, [rowEditingId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (inert) leave(false); }, [inert]); // eslint-disable-line react-hooks/exhaustive-deps

  // Box `id` becomes fn(its box) (markup/textBox.js moveBox, resizeBox …),
  // then shows at the size it measures. A box not typed in yet takes it
  // into its making.
  function change(id, fn) {
    if (readOnly) return;
    (freshRef.current === id ? foldBlocks : setBlocks)((prev) => measured(id, withBox(prev, id, fn), prev));
  }

  const actions = {
    // A new box on `surface` (a PDF page number or a sheet id), `textBox`
    // placed by the layer, open in its editor. The layer left the box
    // being edited at the press before, so that box's removal is an edit
    // of its own. A PDF page's box sits at the page's top level with its
    // pdf_page, a sheet's is the sheet's last child, as ink groups do.
    onBoxCreate(surface, textBox) {
      if (readOnly || !pageId) return;
      const id = makeBlockId();
      const onSheet = typeof surface === "string";
      const node = { id, parentId: onSheet ? surface : null, children: [], content: "",
        properties: onSheet ? { text_box: textBox } : { text_box: textBox, pdf_page: surface } };
      setBlocks((prev) => {
        if (!onSheet) return [...prev, node];
        return findBlock(prev, surface) ? updateBlockTree(prev, surface, (s) => ({ ...s, children: [...(s.children || []), node] })) : prev;
      });
      freshRef.current = id;
      measureRef.current.add(id);
      closeRowEditor();
      setSel({ id, editing: true, at: null });
    },
    onBoxOpen(id, at = null) {
      const cur = selRef.current;
      if (readOnly || (cur?.editing && cur.id === id)) return;
      if (cur?.editing) leave(false);
      closeRowEditor();
      setSel({ id, editing: true, at });
    },
    onBoxSelect(id) {
      if (selRef.current?.id === id) return;
      leave(false);
      setSel({ id, editing: false, at: null });
    },
    // The editor of `id` closes: Escape keeps the box selected, a blur does not.
    onBoxLeave(id, keep) {
      if (selRef.current?.editing && selRef.current.id === id) leave(keep);
    },
    onBoxDeselect() { leave(false); },
    // Typing in the box: the same edit as typing in its row.
    onBoxText(id, text) {
      if (readOnly) return;
      typedRef.current = { id, text };
      if (freshRef.current === id) freshRef.current = null;
      setBlocks((prev) => measured(id, setBlockText(prev, id, text), prev));
    },
    onBoxKey(e, editor) { dispatch(FORMAT_COMMANDS, e, { editor, readOnly }, keybindings); },
    // A move, the width handle, a restyle from the box's menu.
    onBoxChange: change,
    onBoxDelete(id) {
      if (readOnly) return;
      if (selRef.current?.id === id) leave(false, true);
      remove(id, false);
    },
    // A copy of box `id` beside it, fn(its box) placing it: its text and
    // place only (not the notes under it, nor what an import recorded),
    // selected.
    onBoxDuplicate(id, fn) {
      const src = findBlock(blocksRef.current, id);
      if (readOnly || !src) return;
      const copy = makeBlockId();
      const { text_box, pdf_page } = src.properties;
      const properties = { text_box: fn(normalizeTextBox(text_box)), ...(pdf_page !== undefined ? { pdf_page } : {}) };
      setBlocks((prev) => insertSibling(prev, id, { id: copy, parentId: src.parentId ?? null, children: [],
        content: src.content || "", properties }, true));
      measureRef.current.add(copy);
      leave(false);
      setSel({ id: copy, editing: false, at: null });
    },
    // Whether box `id` is to be measured now (once).
    wantsMeasure(id) { return measureRef.current.delete(id); },
    // It rendered at `size` (points) on a surface `surfaceWidth` wide.
    onBoxMeasured(id, size, surfaceWidth) {
      foldBlocks((prev) => withBox(prev, id, (box) => refitBox(box, size, surfaceWidth)));
    },
  };

  const selBox = sel ? normalizeTextBox(findBlock(blocks, sel.id)?.properties?.text_box) : null;
  return {
    sel, flash, bySurface, actions,
    editingId: sel?.editing ? sel.id : null,
    // What the Text tool's options row shows: the selected box's style,
    // else the style of new boxes.
    shownStyle: selBox ? textStyle(selBox) : style,
    // Box `id`'s text was edited in its row: measure it as it shows the edit.
    touch(id) { measureRef.current.add(id); },
    flashBox(id) { setFlash({ id, nonce: Date.now() }); },
    // The Text tool's options row: the style of new boxes, and of the
    // selected or edited box.
    restyle(patch) {
      setStyle(textStyle({ ...style, ...patch }));
      if (selRef.current) change(selRef.current.id, (box) => normalizeTextBox({ ...box, ...patch }));
    },
  };
}
