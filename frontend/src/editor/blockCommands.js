// The commands a note block answers to while its editor is open — VSCode's
// line shortcuts with the block as the line, plus Obsidian's formatting
// keys — declared once (docs/dev/hotkeys.md). Only the long-standing keys,
// the ↑ / ↓ hop, Ctrl+Shift+K and Ctrl+Shift+. have default chords; the rest are palette
// entries (Ctrl+Shift+P) until the account binds them in Settings →
// Keyboard. BlockRow's keydown dispatches this catalog
// (shared/lib/hotkeys.js) after its popups and before the outliner's
// Enter/Tab/Backspace; the command palette, the Settings → Keyboard pane and
// the touch editing bar (editor/EditBar.jsx) read the same list, so a key, a
// menu entry, a bar button and the cheat sheet can never disagree.
//
// ctx: { block, tree (the page's blocks), view (the viewer's editing /
// folding state, blockModel's `view`), folded (whether this block's
// children are folded), row (BlockTree's rowProps — onMoveBlock,
// onDuplicate, onDelete, …), editor (the open editor's facade: value,
// selectionStart/End, setSelectionRange, view — or null when run from the
// palette on the focused row), readOnly }. A command that must
// have an editor says so with `needsEditor`; `edits` ones are skipped on a
// read-only page. `run` returning false declines the key.
import { t } from "../shared/i18n/i18n.js";
import { getParentInfo } from "../shared/model/blockModel.js";
import { fenceInnerAt } from "./fences.js";
import { mathTabJump } from "./latexCompletion.js";
import { toggleQuotePlan } from "./mdLines.js";
import { toggleTodoLine } from "./mdMarks.js";
import { runInsertLink, runInsertMath, runInsertSlash, runToggleMark } from "./markCommands.js";

export const GROUP_NOTES = t("Notes");
export const GROUP_FORMAT = t("Formatting");

// Where the block stands among its siblings: what moving and nesting it can
// do (the editing bar greys a button out, the palette leaves it out).
const place = (c) => getParentInfo(c.tree, c.block.id);
const hasPrev = (c) => (place(c)?.index || 0) > 0;
const hasNext = (c) => { const p = place(c); return !!p && p.index < p.siblings.length - 1; };
const nested = (c) => !!place(c)?.parent;

// Is there no visual line above (dir -1) / below (+1) the caret in this
// editor? Wrapped lines count, so the check is by coordinates: when moving
// vertically keeps the caret on the same row, this is the edge.
function atVerticalEdge(editor, dir) {
  const view = editor?.view;
  if (!view) return true;
  const sel = view.state.selection.main;
  if (!sel.empty) return false;
  const doc = view.state.doc;
  const line = doc.lineAt(sel.head);
  if (dir < 0 ? line.number > 1 : line.number < doc.lines) return false;
  const moved = view.moveVertically(sel, dir > 0);
  const a = view.coordsAtPos(sel.head, sel.assoc || -1);
  const b = view.coordsAtPos(moved.head, -1);
  if (!a || !b) return true;
  return Math.abs(a.top - b.top) < 2;
}

// Where Tab goes from the caret in a math snippet (latexCompletion.js
// mathTabJump: the next {} slot, past a \right delimiter, out of the span),
// or null; a "$" in a code fence is no math.
function slotJump(editor) {
  if (!editor || fenceInnerAt(editor.value, editor.selectionEnd)) return null;
  return mathTabJump(editor.value, editor.selectionEnd, 1);
}

const mark = (id, label, keys, marker) => ({
  id, label, group: GROUP_FORMAT, keys, edits: true, needsEditor: true, palette: false,
  run: (c) => runToggleMark(c.editor.view, marker),
});

export const BLOCK_COMMANDS = [
  {
    id: "block.up", label: t("Previous block"), group: GROUP_NOTES, keys: "ArrowUp",
    needsEditor: true, palette: false, fixed: true,
    when: (c) => atVerticalEdge(c.editor, -1),
    run: (c) => c.row.onHop?.(c.block.id, -1) === true,
  },
  {
    id: "block.down", label: t("Next block"), group: GROUP_NOTES, keys: "ArrowDown",
    needsEditor: true, palette: false, fixed: true,
    when: (c) => atVerticalEdge(c.editor, 1),
    run: (c) => c.row.onHop?.(c.block.id, 1) === true,
  },
  {
    id: "block.moveUp", label: t("Move block up"), group: GROUP_NOTES, keys: null, edits: true, when: hasPrev,
    run: (c) => c.row.onMoveBlock?.(c.block.id, -1) === true,
  },
  {
    id: "block.moveDown", label: t("Move block down"), group: GROUP_NOTES, keys: null, edits: true, when: hasNext,
    run: (c) => c.row.onMoveBlock?.(c.block.id, 1) === true,
  },
  {
    id: "block.duplicateUp", label: t("Duplicate block above"), group: GROUP_NOTES, keys: null, edits: true,
    run: (c) => { c.row.onDuplicate?.(c.block.id, { above: true }); },
  },
  {
    id: "block.duplicateDown", label: t("Duplicate block below"), group: GROUP_NOTES, keys: null, edits: true,
    run: (c) => { c.row.onDuplicate?.(c.block.id, { above: false }); },
  },
  {
    // The caret's line of a multi-line block; a one-line block goes as a
    // whole, its children moving up into its place (indented lines under a
    // deleted line stay), and the caret lands at the end of the block above.
    id: "block.deleteLine", label: t("Delete line"), group: GROUP_NOTES, keys: "Mod-Shift-k", edits: true,
    run: (c) => {
      const view = c.editor?.view;
      if (view && view.state.doc.lines > 1) {
        const doc = view.state.doc;
        const line = doc.lineAt(view.state.selection.main.head);
        const last = line.to === doc.length;
        const from = last ? line.from - 1 : line.from;
        const to = last ? line.to : line.to + 1;
        view.dispatch({ changes: { from, to }, selection: { anchor: from }, userEvent: "delete" });
        return true;
      }
      c.row.onDelete?.(c.block.id, { keepChildren: true, focusAbove: true });
      return true;
    },
  },
  {
    id: "block.newAbove", label: t("New block above"), group: GROUP_NOTES, keys: null, edits: true,
    run: (c) => { c.row.onEnterSibling?.(c.block.id, { above: true }); },
  },
  {
    // What the new-note Enter does (Settings → Keyboard, "Enter makes"),
    // for the editing bar and anyone who wants it on another key.
    id: "block.newBelow", label: t("New block below"), group: GROUP_NOTES, keys: null, edits: true,
    run: (c) => { c.row.onEnterSibling?.(c.block.id); },
  },
  {
    // The slash menu without typing "/" (editor/SlashMenu.jsx).
    id: "block.insertMenu", label: t("Insert…"), group: GROUP_NOTES, keys: null, edits: true, needsEditor: true,
    run: (c) => runInsertSlash(c.editor.view),
  },
  {
    id: "block.indent", label: t("Indent block"), group: GROUP_NOTES, keys: null, edits: true, when: hasPrev,
    run: (c) => { c.row.onIndent?.(c.block.id); },
  },
  {
    id: "block.outdent", label: t("Outdent block"), group: GROUP_NOTES, keys: null, edits: true, when: nested,
    run: (c) => { c.row.onOutdent?.(c.block.id); },
  },
  {
    id: "block.fold", label: t("Collapse children"), group: GROUP_NOTES, keys: null,
    when: (c) => (c.block.children?.length || 0) > 0 && !c.folded,
    run: (c) => { c.row.onToggle?.(c.block.id); },
  },
  {
    id: "block.unfold", label: t("Expand children"), group: GROUP_NOTES, keys: null,
    when: (c) => (c.block.children?.length || 0) > 0 && !!c.folded,
    run: (c) => { c.row.onToggle?.(c.block.id); },
  },
  {
    id: "block.todo", label: t("Toggle to-do"), group: GROUP_NOTES, keys: null, edits: true, needsEditor: true,
    run: (c) => {
      const view = c.editor.view;
      const r = toggleTodoLine(view.state.doc.toString(), view.state.selection.main.head);
      view.dispatch({ changes: { from: r.from, to: r.to, insert: r.insert }, selection: { anchor: r.pos }, userEvent: "input" });
      return true;
    },
  },
  {
    // The selected lines in or out of a quote, every line marked so they
    // stay one box (mdLines.toggleQuotePlan); /callout on its first line
    // makes it a callout. GitHub's chord: it works on the open editor's
    // selection, so the palette cannot run it.
    id: "block.quote", label: t("Toggle quote"), group: GROUP_NOTES, keys: "Mod-Shift-.", edits: true, needsEditor: true,
    run: (c) => {
      const view = c.editor.view;
      const { from, to } = view.state.selection.main;
      const r = toggleQuotePlan(view.state.doc.toString(), from, to);
      view.dispatch({ changes: r.changes, selection: r.selection, userEvent: "input" });
      return true;
    },
  },
  {
    id: "block.selectAll", label: t("Select block text"), group: GROUP_NOTES, keys: null, needsEditor: true,
    run: (c) => { c.editor.setSelectionRange(0, c.editor.value.length); },
  },
  mark("block.bold", t("Bold"), "Mod-b", "**"),
  mark("block.italic", t("Italic"), "Mod-i", "*"),
  mark("block.code", t("Inline code"), "Mod-e", "`"),
  mark("block.strike", t("Strikethrough"), "Mod-Shift-x", "~~"),
  mark("block.highlight", t("Highlight text"), "Mod-Shift-h", "=="),
  {
    id: "block.link", label: t("Link"), group: GROUP_FORMAT, keys: "Mod-k", edits: true, needsEditor: true, palette: false,
    run: (c) => runInsertLink(c.editor.view),
  },
  {
    id: "block.math", label: t("Inline equation"), group: GROUP_FORMAT, keys: null, edits: true, needsEditor: true, palette: false,
    run: (c) => runInsertMath(c.editor.view),
  },
  {
    // What Tab does in a math snippet (\frac{}{}: the numerator, the
    // denominator, then out), for the editing bar: an on-screen keyboard
    // has no Tab. The bar shows it only while there is somewhere to go.
    id: "block.nextSlot", label: t("Next math argument"), group: GROUP_FORMAT, keys: null, needsEditor: true, palette: false,
    when: (c) => !!slotJump(c.editor),
    run: (c) => {
      const jump = slotJump(c.editor);
      if (!jump) return false;
      c.editor.setSelectionRange(jump.anchor, jump.head);
    },
  },
  // The ⋮⋮ handle menu's entries, for the palette.
  {
    id: "block.addToChat", label: t("Add block to chat"), group: GROUP_NOTES, keys: null,
    when: (c) => !!c.row.onAddToChat,
    run: (c) => { c.row.onAddToChat(c.block); },
  },
  {
    id: "block.moveToPage", label: t("Move block to page…"), group: GROUP_NOTES, keys: null, edits: true,
    when: (c) => !!c.row.onMoveToPage,
    run: (c) => { c.row.onMoveToPage(c.block.id); },
  },
  {
    id: "block.delete", label: t("Delete block with its children"), group: GROUP_NOTES, keys: null, edits: true,
    run: (c) => { c.row.onDelete?.(c.block.id); },
  },
];
