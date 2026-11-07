// The commands a note block answers to while its editor is open — VSCode's
// line shortcuts with the block as the line, plus Obsidian's formatting
// keys — declared once (docs/dev/hotkeys.md). Only the long-standing keys,
// the ↑ / ↓ hop, Ctrl+Shift+K and Ctrl+Shift+. have default chords; the rest are palette
// entries (Ctrl+Shift+P) until the account binds them in Settings →
// Keyboard. BlockRow's keydown dispatches this catalog
// (shared/lib/hotkeys.js) after its popups and before the outliner's
// Enter/Tab/Backspace; the command palette, the Settings → Keyboard pane,
// the touch editing bar (editor/EditBar.jsx) and the selection toolbar
// (editor/SelectionToolbar.jsx) read the same list, so a key, a menu entry,
// a bar button and the cheat sheet can never disagree.
//
// ctx: { block, tree (the page's blocks), view (the viewer's editing /
// folding state, blockModel's `view`), folded (whether this block's
// children are folded), row (BlockTree's rowProps — onMoveBlock,
// onDuplicate, onDelete, …), editor (the open editor's facade: value,
// selectionStart/End, setSelectionRange, view — or null when run from the
// palette on the focused row), readOnly }. A command that must
// have an editor says so with `needsEditor`; `edits` ones are skipped on a
// read-only page. `run` returning false declines the key. A formatting
// command's `state(ctx)` is what it is to the editor's selection — "on"
// (the selection carries its mark: a toolbar button shows pressed), "off",
// or "blocked" (it does nothing there: greyed out); it never stops the key,
// which a blocked mark swallows (markCommands.js).
import { insertNewlineAndIndent } from "@codemirror/commands";
import { t } from "../shared/i18n/i18n.js";
import { getParentInfo, isPlainNote } from "../shared/model/blockModel.js";
import { fenceInnerAt } from "./fences.js";
import { findMathAtCursor, mathTabJump } from "./latexCompletion.js";
import { enterPlan, isWholeLine, lineAt, newBlockPlan, quoteMarksAt, toggleQuotePlan } from "./mdLines.js";
import { toggleTodoLine } from "./mdMarks.js";
import { scanMathSpans } from "./mdScan.js";
import {
  linkStateAt, markStateAt, mathStateAt, runInsertLink, runInsertMath, runInsertSlash, runToggleMark,
} from "./markCommands.js";

export const GROUP_NOTES = t("Notes");
export const GROUP_FORMAT = t("Formatting");

// Where the block stands among its siblings: what moving and nesting it can
// do (the editing bar greys a button out, the palette leaves it out).
const place = (c) => getParentInfo(c.tree, c.block.id);

// Where the formula around the caret ends in its line's text, or null. A
// "$" on another line opens nothing here, so a stray one above takes no
// formula apart. The second look, a letter at the caret, reads what an
// auto-paired closer waits in and scanMathSpans refuses: "$|$" (to it a "$$"),
// "$ |$" and "$\|$" (to it an escaped dollar; findMathAtCursor's exception).
function formulaEnd(line, at) {
  const around = (text) => scanMathSpans(text).find((s) => s.from < at && at < s.to);
  const filled = around(line.slice(0, at) + "x" + line.slice(at));
  return around(line)?.to ?? (filled ? filled.to - 1 : null);
}

// mdLines.newBlockPlan with a formula never cut: once the selection has gone,
// a caret inside a closed formula (formulaEnd) splits past its closing "$" and
// the spaces after it, which go, so the formula stays here and what follows it
// goes on. An unclosed "$x" splits at the caret, and a table row stays uncut.
export function splitPlan(text, from, to = from) {
  const t = text.slice(0, from) + text.slice(to);
  const line = lineAt(t, from);
  const end = isWholeLine(line.text) ? null : formulaEnd(line.text, from - line.from);
  if (end === null) return newBlockPlan(text, from, to);
  const past = line.from + end;
  return newBlockPlan(t, past, past + /^[ \t]*/.exec(t.slice(past))[0].length);
}

// The new-block key, for the row's keydown and block.newBelow: a plain note
// with its editor open splits at the caret and goes on with its list
// (splitPlan, App's onNewBlock). Any other row gets an empty
// sibling, and so does a press in code or math, where the keydown never
// comes here and a split would break the fence (the editing bar's way out of
// one). Whether a block was made.
export function newBlockKey(c) {
  const view = c.editor?.view;
  if (view && isPlainNote(c.block)) {
    const text = view.state.doc.toString();
    const { from, to } = view.state.selection.main;
    if (!fenceInnerAt(text, from) && !findMathAtCursor(text, from)?.display) {
      const plan = splitPlan(text, from, to);
      c.row.onNewBlock?.(c.block.id, plan);
      return !plan.stay;
    }
  }
  c.row.onEnterSibling?.(c.block.id);
  return true;
}

// The line break in code or display math, null anywhere else. A fence's new
// line keeps the fence's own marks, a quote's or none: a "> x" in a plain
// fence is code, and so is the inner level of "> > x" in a quoted one. In
// math it keeps the marks of the line.
export function codeBreak(text, pos) {
  const fence = fenceInnerAt(text, pos);
  if (fence) return "\n" + (quoteMarksAt(text, pos) && fence.quote);
  return findMathAtCursor(text, pos)?.display ? "\n" + quoteMarksAt(text, pos) : null;
}

// The line-break key's edit, for block.lineBreak: what the row's keydown
// does, in its order: codeBreak, and anywhere else a list or quote goes on
// (mdLines.enterPlan). {from, to, insert, pos}, or null where the keydown
// leaves the key to CodeMirror's Enter.
function lineBreakAt(text, from, to = from) {
  const insert = codeBreak(text, from);
  if (insert) return { from, to, insert, pos: from + insert.length };
  return from === to ? enterPlan(text, from) : null;
}

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

// A state reader over the editor's text and selection (markCommands.js `…StateAt`).
const selectionState = (stateAt) => (c) => stateAt(c.editor.value, c.editor.selectionStart, c.editor.selectionEnd);

const mark = (id, label, keys, marker) => ({
  id, label, group: GROUP_FORMAT, keys, edits: true, needsEditor: true, palette: false,
  state: selectionState((doc, from, to) => markStateAt(doc, from, to, marker)),
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
    run: (c) => { newBlockKey(c); },
  },
  {
    // The other Enter, for the same: Return makes a block on an on-screen
    // keyboard, which has no dependable Shift+Return.
    id: "block.lineBreak", label: t("Line break"), group: GROUP_NOTES, keys: null, edits: true, needsEditor: true,
    run: (c) => {
      const view = c.editor.view;
      const { from, to } = view.state.selection.main;
      const plan = lineBreakAt(view.state.doc.toString(), from, to);
      if (!plan) return insertNewlineAndIndent(view);
      view.dispatch({
        changes: { from: plan.from, to: plan.to, insert: plan.insert },
        selection: { anchor: plan.pos },
        scrollIntoView: true,
        userEvent: plan.insert.startsWith("\n") ? "input" : "delete",
      });
      return true;
    },
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
  // Markdown has no underline: <u>…</u>, the tag Obsidian and GitHub draw.
  mark("block.underline", t("Underline"), "Mod-u", "<u>"),
  mark("block.code", t("Inline code"), "Mod-e", "`"),
  mark("block.strike", t("Strikethrough"), "Mod-Shift-x", "~~"),
  mark("block.highlight", t("Highlight text"), "Mod-Shift-h", "=="),
  {
    id: "block.link", label: t("Link"), group: GROUP_FORMAT, keys: "Mod-k", edits: true, needsEditor: true, palette: false,
    state: selectionState(linkStateAt),
    run: (c) => runInsertLink(c.editor.view),
  },
  {
    id: "block.math", label: t("Inline equation"), group: GROUP_FORMAT, keys: null, edits: true, needsEditor: true, palette: false,
    state: selectionState(mathStateAt),
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
  // The thread popover's way in from the keyboard: a viewer comments too,
  // so no `edits`; the focused row's control is where it opens.
  {
    id: "block.comment", label: t("Comment on this note"), group: GROUP_NOTES, keys: "Mod-Shift-m",
    when: (c) => !!c.row.onComment,
    run: (c) => { c.row.onComment(c.block.id); },
  },
  // The Properties popover reads only, so a viewer has it too; it hangs
  // under the focused row's handle.
  {
    id: "block.properties", label: t("Block properties"), group: GROUP_NOTES, keys: null,
    when: (c) => !!c.row.onProperties,
    run: (c) => { c.row.onProperties(c.block.id); },
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
