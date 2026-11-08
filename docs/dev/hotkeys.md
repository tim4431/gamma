# Keyboard shortcuts

How a key becomes an action, where a shortcut is declared, how the account
changes it, and what the command palette and the Settings pane read.

## One catalog, six surfaces

A **command** is declared once, as an object in one of two catalogs:

```js
{ id: "block.deleteLine", label: t("Delete line"), group: GROUP_NOTES,
  keys: "Mod-Shift-k", edits: true, run: (ctx) => { … } }
```

- `id` — stable, `scope.name`; the account's overrides are keyed by it.
- `label` / `group` — what the palette and the pane show (`t()` at module level, like any option list).
- `keys` — the default chord, an array of them (`["Mod-y", "Mod-Shift-z"]`), or `null`: no keys until the account binds some, a palette entry meanwhile. Defaults are deliberately few: the keys Gamma always had (search, Ctrl+P, back, undo/redo, the formatting marks), plus Ctrl+Shift+P, F2, Delete, Ctrl+Shift+K, Ctrl+Shift+. (Toggle quote, GitHub's chord: it needs the open editor, so the palette cannot offer it), Ctrl+Shift+M (Comment on this note), Ctrl+, (settings, the convention everywhere), the ↑/↓ hop, and Ctrl+C / X / V / D / A on handwriting (the keys every editor gives copy, cut, paste, duplicate and select all; their `when` keeps them to the markup strip's selection and clipboard, so a note's editor and the browser keep theirs). F2 renames the page, or on the home library the one selected page. Delete is the file manager's key: it moves the pages selected on the home library to Recently deleted, after the confirmation, and the page menu shows it. Everything else (move, duplicate, new above, indent, fold, to-do, the view toggles, export, share…) starts unbound.
- `scope` is the catalog the command sits in:
  - [frontend/src/app/appCommands.js](../../frontend/src/app/appCommands.js) — the app commands, alive wherever focus is: search, the palettes, back, undo / redo, rename, share, metadata, page properties (the open page's read-only Properties popover, [ui-design.md](ui-design.md#the-properties-popover)), attach, the exports (the Export dialog preset to a format), download, import, new page, delete the selected pages, Recently deleted, show or hide chat / comments / PDF / notes, settings, report a problem, the PDF group's two held keys (*Held keys* below), and the Handwriting group — `ink.copy`, `ink.cut`, `ink.paste`, `ink.duplicate`, `ink.selectAll` — alive while the markup strip holds a selection or a tool ([handwriting.md](handwriting.md) "Copy, cut and paste"; their guards under *Widget keys* below).
  - [frontend/src/editor/blockCommands.js](../../frontend/src/editor/blockCommands.js) — the block commands, for a note whose editor is open: VSCode's line shortcuts with the block as the line (move, duplicate, delete line, new above, indent, fold, to-do, select), the hop to the neighbouring block on ↑ / ↓, Obsidian's formatting keys, Comment on this note (the block's thread popover, [comments.md](comments.md) "In the app"), and Block properties (unbound; the read-only Properties popover, [ui-design.md](ui-design.md#the-properties-popover)).
- `when(ctx)` — applies only while this holds (a page is open, the block has children…).
- `edits: true` — skipped on a read-only page (a share view, a viewer's workspace); the dispatcher and the palette check once, no handler repeats it. A block command without it (Comment on this note, Block properties) is offered there too.
- `needsEditor: true` (block scope) — cannot run from the palette, where no editor is open.
- `palette: false` — keyboard only (the arrow hops, the formatting marks, the palette command itself).
- `fixed: true` — shown in the pane but not rebindable (the arrow hops).
- `held: true` — a modifier held through a gesture, not a chord that runs the command (see *Held keys* below).
- `inDialog(dialog)` (app scope) — still dispatches while a dialog is open (see *Dialogs* below).
- `state(ctx)` (the block scope's formatting commands) — what the command is to the open editor's selection: `"on"` (the selection carries its mark, which the command would take away), `"off"`, or `"blocked"` (it does nothing there: math, a code fence, inline code for every mark but code). The selection toolbar presses and greys out its buttons by it. It is no `when`: a blocked mark's key is still taken and does nothing, so Ctrl+B in math never reaches the browser. The readers are `markCommands.js`'s `…StateAt`.
- `run(ctx, event)` — returning `false` declines the key: the dispatcher tries the next command, and an unhandled key keeps its default (undo in a plain input stays the browser's).

[frontend/src/app/commands.js](../../frontend/src/app/commands.js) joins the
two lists (`ALL_COMMANDS`, `GROUPS`) and, for the surfaces that show the
whole picture, lists the keys the outliner owns outright (`fixedKeys`:
Enter, Tab, Backspace, ←/→ folding at the text's edge, `/`, Esc). `commandChord(id, bindings)` is the one lookup of the chord a command
answers to for this account (its first effective key, `""` when unbound):
the home library's key hints (the page menu's Rename / Delete, the filter
box) and the guide's `{key:…}` tokens (`guide/keys.js`) both read it.

The six surfaces read that catalog and nothing else:

| Surface | Reads | Where |
|---|---|---|
| the key dispatchers | `keys`, `when`, `edits`, `run` | App's one window listener (app scope); a block row's `onKeyDown` (block scope) |
| the command palette (Ctrl+Shift+P, or `>` in Ctrl+P) | `label`, `group`, the effective keys, `run` | [library/QuickOpen.jsx](../../frontend/src/library/QuickOpen.jsx), fed by App's `paletteCommands()` |
| Settings → Keyboard | everything, plus `fixed` and the conflicts | [settings/SettingsKeyboard.jsx](../../frontend/src/settings/SettingsKeyboard.jsx) |
| the touch editing bar (block scope; its Undo / Redo call App's `undoBlocks`) | `label`, `when` (a button greys out, or a transient one hides), `run` | [editor/EditBar.jsx](../../frontend/src/editor/EditBar.jsx), given the row's `commandContext` ([ipad.md](ipad.md) "The editing bar") |
| the selection toolbar (block scope: the formatting commands, over selected text) | `label`, the effective keys (in the titles), `state` (pressed or greyed out), `run` | [editor/SelectionToolbar.jsx](../../frontend/src/editor/SelectionToolbar.jsx), given the row's `commandContext` ([ui-design.md](ui-design.md#the-selection-toolbar)) |
| a menu row (app scope: the View menu's Undo, Redo, Go to page, Command palette; the markup strip's More menu, `ink.paste` and `ink.selectAll`) | `label`, the effective keys, `when` (the row greys out), `run` | [app/CommandMenuItem.jsx](../../frontend/src/app/CommandMenuItem.jsx), given App's `appCmdRef` context ([ui-design.md](ui-design.md#menus-and-submenus)) |

So a key, a palette entry, a bar or toolbar button, a menu row and the pane
can never disagree.
The cheat sheet in [docs/user_guide/user_guide.md](../user_guide/user_guide.md) is checked against
the catalog by `tests/commands.test.mjs`: a command with a default chord
whose label is missing there fails the test.

## Chords and dispatch

[frontend/src/shared/lib/hotkeys.js](../../frontend/src/shared/lib/hotkeys.js)
is the pure core (no DOM, `tests/hotkeys.test.mjs`):

- A **chord** is a string in CodeMirror's spelling: `"Mod-Shift-k"`, `"Alt-ArrowUp"`, `"F2"`, `"Mod-,"`, `"Mod--"` (Mod plus the minus key). `Mod` is Ctrl on Windows and Linux and ⌘ on a Mac; `Ctrl` in a chord is the control key on a Mac. `normalizeChord` gives the canonical form (modifier order Mod, Ctrl, Alt, Shift; letters lowercase).
- `chordFromEvent(e)` reads a keydown by its **physical key** (`e.code`), so `Ctrl+Shift+[` is the `[` key with Shift held whatever `e.key` reports (`{`), and a letter chord means the same key on any layout. A bare modifier press is no chord. IME composition never dispatches.
- `dispatch(commands, e, ctx, bindings)` runs the first command whose effective chord matches and whose `when` holds; a handled event has its default prevented and its propagation stopped. `effectiveKeys(cmd, bindings)` is the account's override when the preference names the command (`null` = unbound), else the defaults.
- `chordLabel` / `chordParts` render a chord for the platform: `Ctrl+Shift+K` or `⇧⌘K` (Mac order ⌃⌥⇧⌘, like the system menus). `bindable` refuses a chord that would replace typing (a bare letter, digit or punctuation); function keys and anything with a modifier pass.
- `conflicts(commands, bindings)` maps each chord more than one command answers to onto those commands.
- `isTextField(el)` tells whether the focused element takes typed text; a command or widget key leaves such a field its key (F2, Delete on a selected object).
- `composing(e)` holds while an input method is composing (Chinese, Japanese, Korean): `isComposing`, or Safari's key code 229, on a React event or a DOM one. The Enter that confirms a word is the input method's, so `dispatch` leaves it alone and every text field's Enter handler starts `if (composing(e)) return;` ([ui-design.md](ui-design.md#adding-a-control), counted by the conventions ratchet).

**Order.** A block row's `onKeyDown` ([editor/BlockTree.jsx](../../frontend/src/editor/BlockTree.jsx)) first leaves a key an input method is composing alone (`composing`, [chat/enterKey.js](../../frontend/src/chat/enterKey.js)): the Enter that confirms a Chinese word makes no block. It then serves its popups (the paste chooser, the `[[` search, the slash menu, the math autocomplete), dispatches the block catalog, then the outliner's own keys (Tab in math and fences, Enter, Tab, ←/→ folding at the text's edge, Backspace). A handled key never reaches the window, so a block chord shadows an app chord while an editor is open. App's window listener dispatches the app catalog and then handles Escape, which is not a command: it always closes popovers and clears selections.

**The new-note Enter and Backspace in a plain note.** These two work on blocks where the rest of the row's keys work on lines, and only in a note that is nothing but text: `isPlainNote` (blockModel.js), whose only properties are its fold and `t0`. A highlight, text box, picture, recording, ink group, sheet or Zotero note keeps the older keys, an empty sibling and a delete of an empty row: a split would take text off the PDF, and a first child would land inside a sheet. Comment fields, text boxes and embed cards have keys of their own.

Enter makes a new note and Shift+Enter a line break, the other way round where Settings → Keyboard has "Enter makes" on New line (`enterNewBlock`, on by default). A first load stores the whole profile, defaults included, so the off an account held under the preference this one replaced could not be told from a choice: when Enter became the new-block key the preference took a new name and a new key (`gamma-enter-new-block`), which moved every account to New note once. A choice made since stays. The old entry stays unread in stored profiles and browsers. Inside a fence or `$$` either Enter is a line break, checked first. A fence counts as the note renders it: `fenceInnerAt` (fences.js) goes by `mdCode.codeFences`, which reads ``` and `~~~` in the quotes and list items around them, closing on its own mark at least as long or where its container ends, so code in a list or indented stays code. `codeBreak` (blockCommands.js) makes the line break: the new line keeps the marks of the line the fence or `$$` opened on, a quote's or a list item's indent, and a `> x` in a plain fence stays code. A dollar in inline code is no math to `findMathAtCursor` and `scanMathSpans`, so a `$` or `$$` there neither moves a split nor turns Enter into a line break. Anywhere else the new-note Enter runs `newBlockKey` (blockCommands.js), which reads `splitPlan(text, from, to)` and hands it to `onNewBlock`. `splitPlan` is `newBlockPlan` (mdLines.js) with a formula never cut: with the caret inside a closed `$` formula, the split moves just past its closing `$`, and it makes no difference that the completion popup was open, since Enter closes it and goes on ([latex_editing.md](latex_editing.md)). `formulaEnd` looks with `scanMathSpans` on the caret's line alone, so a stray `$` on a line above takes no formula apart, and looks again with a letter at the caret, for what an auto-paired closer waits in and the scanner refuses: an empty pair (all of a formula selected leaves one), a pair holding only spaces, and a `\` before the closer, a command being started. A line like "$5 or $6" reads as a formula here as it does in the rendered note, and Undo puts a split there back. The selection goes first; then, with `|` the caret:

| Before | This block | New block | Caret |
|---|---|---|---|
| `plain\|` | same | empty | new block |
| `ab\|cd`, `# a\|b` | `ab`, `# a` | `cd`, `b` | new block, at its start |
| `\|abcd` | same | empty, above | stays |
| `- \|ab` | same | `- `, above | stays |
| `- ab\|`, `3. ab\|`, `- [x] ab\|` | same | `- `, `4. `, `- [ ] ` | new block, after the marker |
| `- ab\|cd` | `- ab` | `- cd` | new block, after the marker |
| `- \|`, `- [ ] \|`, `1. \|` alone in the block | empty | none | stays |
| `> - \|`, `> > \|` alone in the block | `> ` | none | stays |
| `- a\n- \|` | `- a` | empty | new block |
| `> ab\|`, `> [!tip] t\|` | same | empty | new block |
| `> a\|b` | `> a` | `> b` | new block, after the marks |
| `a\nb\|\nc`, `- a\|\n- b` | `a\nb`, `- a` | `c`, `- b` with its own marker | new block, after any marker |
| `$x^2\|$`, `- $x\|$` | `$x^2$`, `- $x$` | empty, `- ` | new block, after any marker |
| `see $a\|b$ here` | `see $ab$` | `here`, its leading space gone | new block, at its start |
| `costs $5\n$x\|$` | `costs $5\n$x$` | empty | new block |
| `see $\|$ here` | `see $$` | `here` | new block, at its start |
| `$x\|` with no closer on its line | `$x` | empty | new block |
| `` `$5` an\|d `$6` tail ``, dollars in code | `` `$5` an `` | `` d `$6` tail `` | new block, at its start |
| a table row, a divider, a ``` or `~~~` line outside its code, at any indent and behind a list's marker, a `$$` line | same | empty | new block |

A number counts on and never renumbers: `1.`, `1.` stays, and deleting an item leaves a gap. The new block goes first under open children, where the caret then is, after the subtree of a folded block, and otherwise right below. `addSiblingBlock` applies `keep` and the insert in one transition, inside `setBlocks`'s updater because `keep` comes from the live editor and the closure's tree can lag it, so one press is one undo step and one op set. The new block is made outside the updater, so its id is known for the focus. A row the key leaves has its tables pretty-printed in `keep` (BlockTree's `newBlockFormatted`), or the close's own pretty-print would be a second undo step. With the editing bar showing, an editor a key opens brings its caret clear of the bar as it mounts, and again where App places it (`setSelectionRange`), since focus scrolls nothing ([ipad.md](ipad.md) "The editing bar").

Enter cuts text as source, as any Markdown source editor does:

- Marks and links cut by a split stay as raw source on both sides: `**bo|ld**` leaves `**bo` and `ld**`, and `[a|b](u)`, a code span or a coloured `<span>` likewise. Only a formula is kept whole (`splitPlan`). Undo puts it back.
- A reminder marked done comes back after a split or merge moves its token to another block: `reminders_done` is keyed by block, date and time (`reminder_key`, mentions.py), so in the other block it is a new reminder. Re-keying would mean matching tokens across a batch for every account that dismissed one, which `mention_changes` (ops.py) does not do for reminders.

Backspace, with no selection, takes the first of these that applies:

1. An empty plain note goes (`onDelete` with `keepChildren`), its children taking its place, and the caret goes to the end of the block shown above. Any other empty row is deleted with its subtree, so an untitled sheet does not spill its children.
2. Right after a list marker, on any line and also inside `> ` (`- |x`, `1. |x`, `- [ ] |x`): the marker goes in one press (`backspacePlan`). Not in a fence or `$$`. Headings and quotes stay raw Markdown, and Backspace takes one character.
3. At offset 0 of a non-empty note: `mergeIntoAbove` (blockModel.js) joins it to the block shown above, the first line's list or heading marker gone, the caret at the seam. Its children keep their reading order: spliced in at its place when the block above is its parent, otherwise last under that block. An empty block above goes instead (`removeBlockKeepChildren`, which leaves this block when the empty one was its parent), and this block stays as it is, whatever its first line. Nothing happens when either block is no plain note, when the block above is folded with children or a join would fold this block's, when its last line is a fence, `$$`, table or divider or its text ends inside an open fence or display math, when this block's first line is one of those or a quote or callout, or when the block that would go holds a comment thread, open or resolved (`comments.threadsByBlock`: `countByBlock` holds open threads only).
4. Otherwise CodeMirror's default.

A merge goes to the server as one `merge` op (`blockOps.withMerges`, [collab.md](collab.md) "a block joined to another"), which joins the two blocks' texts as they are when it arrives, so two people merging neighbouring blocks at once keep every word: `onetwothree`, or a refused merge whose block stays. A split has no such op. It goes as a `set` of the text kept and an `insert` of the rest, both computed from this tab's copy, so two people pressing Enter in one block at once, or one undoing a split the other has typed into, can duplicate text in the two blocks. Nothing is lost, and the limit stands until a split needs an op of its own.

A split or a merge changes the tree's shape, and by the time the history sees it the editor has moved on, so `caretBeforeRef` carries the caret with `keyed: true` for that one transition (`observeTree`, blockHistory.js). Undo puts the caret where the key was pressed, not where the last commit saw it.

**Dialogs.** Every modal sits in a `.reportOverlay` (Settings and its sub-dialogs, Import, Export, the palette, the confirm box…). While one is open, App's listener dispatches only the app commands whose `inDialog(dialog)` holds (`liveAppCommands` in appCommands.js; the dialog is the last overlay in the DOM). The rest would act on the page behind it out of sight: Ctrl+Z undoing a note from inside Settings, F2 renaming the page, Alt+← navigating away. What stays:

- Ctrl+F goes to the dialog's own search box, the input marked `data-find` (Settings' search and the Keyboard pane's filter, the move-to-page filter, the chat's page picker), else to the browser's find. With several, the innermost comes first and pressing again moves to the one before it.
- Ctrl+P / Ctrl+Shift+P work only inside the palette itself, switching it between pages and commands or closing it.

A dialog's search box walks its results with `stepList` ([shared/ui/listKeys.js](../../frontend/src/shared/ui/listKeys.js)): ↓ from the box to the first result, ↑/↓ between them, ↑ from the first back to the box. Enter in the box takes the best match (opens the setting, moves the block, ticks the page); with an empty query the page picker's Enter is Done.

**CodeMirror.** The block editor installs only `standardKeymap` (caret movement, Home/End, selection by word). Everything above that — the formatting marks, line and block operations — is a command, so nothing arrives from a library keymap by accident. `defaultKeymap` is not used: its Ctrl+M tab-focus mode stops Tab from indenting ([research note](../research/keyboard-shortcuts.md)).

## The block commands' plumbing

The block context is `{ block, tree, row, editor, readOnly }`: `editor` is the open editor's facade (`value`, `selectionStart/End`, `setSelectionRange`, `view`), `row` the tree's handlers. Every mutation goes through `setBlocks` with a pure helper, so the op diff ([collab.md](collab.md)) and the undo history ([blockHistory.js](../../frontend/src/editor/blockHistory.js)) see it like any edit:

- **↑ / ↓ at the editor's first / last visual line** (`atVerticalEdge`, by coordinates so wrapped lines count) → `onHop`: the neighbouring shown block (`visibleNeighbor`, collapsed subtrees skipped) opens its editor with the caret at its end / start. `pendingFocusRef` carries `{id, caret}` for that; a bare id still means "focus, caret where the editor puts it".
- **Move block up / down** (unbound) → `onMoveBlock`: `moveSibling` swaps with the neighbour, same reference at the edge (the key then does nothing). Moving a focused DOM node blurs it in some browsers, and that blur would close the editor: `keepEditRef` tells `onStartEdit` to ignore one close for that block within 500 ms, and the focus request re-opens the editor (`reopen` tries) and restores the caret offset.
- **Duplicate block above / below** (unbound) → `onDuplicate(id, {above})`, the handle menu's copy (fresh ids, highlight anchors stripped).
- **Ctrl+Shift+K** — the caret's line of a multi-line block (a CodeMirror change); a one-line block goes as a whole through `onDelete(id, {keepChildren: true, focusAbove: true})`: `removeBlockKeepChildren` lifts its children into its place (indented lines under a deleted line stay), and the block above gets the caret at its end. The handle menu's Delete still removes the subtree.
- **New block above / below**, **indent / outdent**, **collapse / expand** (unbound) → `onEnterSibling(id, {above})`, indent / outdent (they work inside code fences and math, where Tab means something else), toggle collapse. New block below is what the new-note Enter does (`newBlockKey`, below): from the open editor it splits a plain note, and from the palette, with no editor, or in a fence or `$$`, where the editing bar has no other way out, it adds an empty sibling. Line break (`block.lineBreak`, unbound, needs an open editor) is the other Enter, for the editing bar, whose keyboard has no dependable Shift+Return: `lineBreakAt` (blockCommands.js) does what the row's keydown does, a list or quote going on through `enterPlan`, `codeBreak` in code or math, and CodeMirror's Enter (`insertNewlineAndIndent`) anywhere else. Indent and outdent re-parent the block, which remounts its row: an open editor stays open with its selection (App's `keepEditorThrough`, which a move and another client's op use too), for Tab as for the command.
- **Where a block can go** is each command's `when`: move up and indent need a sibling above, move down one below, outdent a parent (`getParentInfo`). The palette leaves an inapplicable one out, and the editing bar greys its button.
- **Insert…** (unbound, needs the editor) → a `/` after the caret, with a space after a word, so the slash menu opens as if typed (`runInsertSlash`); nothing inside math or a fence. **Inline equation** (unbound, formatting) → `$…$` around the selection, else `$x$` with the x selected (`runInsertMath`). **Next math argument** (`block.nextSlot`, unbound, formatting) → what Tab does in a math snippet (`mathTabJump` in `latexCompletion.js`: the next `{}` slot, past a `\right` delimiter, then out of the span; [latex_editing.md](latex_editing.md)), for an on-screen keyboard without Tab. Its `when` holds only while there is somewhere to go, never for a `$` in a code fence.
- **Toggle to-do** (unbound) → `toggleTodoLine` (mdMarks.js): `- [ ]` ↔ `- [x]` on the caret's line, a line without a box gets one after its list marker. **Toggle quote** (Ctrl+Shift+.) → `toggleQuotePlan` (mdLines.js): every line the selection touches gets `> ` (a blank one a bare `>`, so the quote stays one box), or, when they all are quoted, loses one level, and a callout's `[!type]` marker with the last one. **Select block text** (unbound) selects it. The handle menu's add-to-chat, move-to-page, delete-subtree and Properties are palette entries too.
- **Ctrl+B / I / E / Shift+X / Shift+H / K** → `runToggleMark` / `runInsertLink` in [editor/markCommands.js](../../frontend/src/editor/markCommands.js). They are plain JS so node can load the catalog, and are swallowed inside math, fences and inline code. A system keyboard's or Format menu's `beforeinput` `formatBold`, `formatItalic` and `formatStrikeThrough` (the iPad keyboard's B and I) run `block.bold`, `block.italic` and `block.strike` in every `BlockCmEditor`.

From the palette a block command runs on the **focused row** with `editor: null` (opening the palette closes any editor); `needsEditor` commands are left out there.

## Held keys

Two keys are held through a gesture instead of pressed with a key. The
area drag on a PDF page: a mouse drag with the key down boxes a region for
the chat and an area highlight. The peek at a translated page's original:
the overlays hide while the key is down. They are catalog commands of the
PDF group, `pdf.areaDrag` (default `Mod`: Ctrl, ⌘ on a Mac, whose
Ctrl+click is the right click) and `pdf.peekOriginal` (default `Alt`, ⌥ on
a Mac). Both are marked `held: true` with `palette: false` and a `run`
that declines. Their chord is a lone modifier, which `chordFromEvent` never
produces, so they never dispatch.

- `heldKey(chord)` is a held key's canonical name: `Mod`, `Ctrl` (a Mac's control key; elsewhere it is `Mod`), `Alt` or `Shift`, else `""`.
- The PDF viewer gets the effective keys from App (`commandChord(id, keybindings)`) as `areaKey` and `peekKey`. `heldAlone(e, areaKey)` starts the drag on a mouse `pointerdown` with that key down and no other modifier; `isHeldKey(e, key)` on keydown / keyup drives the crosshair cue and the peek.
- In Settings → Keyboard they are rows like the rest. Their recorder (`KeyBinding`'s `held`) takes the first modifier pressed (`heldFromEvent`: ⌘ → `Mod` and Ctrl → `Ctrl` on a Mac, Ctrl → `Mod` elsewhere, Alt, Shift; the Windows key is refused). Backspace does not unbind them. The same key on both rows is flagged like any shared chord.
- Guide copy names the area key as `{key:pdf.areaDrag}`, and the area demo (`guide/previewArea.js`) drags with `heldFlags(services.areaKey())`, the cap beside its pointer showing the same key.

## Widget keys: the markup strip and a text box

A few keys belong to a widget while it is active. They are not commands, so the palette and the pane do not list them and they cannot be rebound.

- **The markup strip** ([handwriting.md](handwriting.md) "What the user sees"). While it is open, a capture-phase `keydown` listener on the window (`ink/useStripKeys.js`; the table is `ink/stripKeys.js`) takes these keys. It matches the physical key (`e.code`), so a layout or Shift does not change which key it is:
  - `1`–`9`: the visible presets in row order. `P` / `H`: the next pen / highlighter, with Shift the previous, wrapping.
  - `[` / `]`: the armed preset thinner / thicker through its eight widths, its options row open or not. A pill names the width ("Blue pen · 2.8 pt.") and repeats it at either end of the ladder.
  - `Shift+[` / `Shift+]`: an armed pen's style back / forward through Pen, Ballpoint, Brush, Pencil and Calligraphy, wrapping, with the same pill naming the style ("Blue brush pen · 2.8 pt."). A highlighter has no styles, so on one the keys do nothing.
  - `E`: a tap arms the eraser, and it stays armed (a second tap opens its row, as a tap on the button does). Held 300 ms or more across an erasing pass, its release returns to the tool armed before it, and a lasso selection made before the hold comes back with the lasso. The handler sees the keydown (repeats ignored) and the keyup with the hold's length and whether a pass was made; a window blur ends a hold.
  - `L`, `T` and `V` arm the lasso, the Text tool ([text_boxes.md](text_boxes.md)) and the hand. `R`: the ruler on or off. `S`: the Shapes tool on or off; off gives back the tool armed before it.
  - `I` presses the Picture button: the file picker, for a picture on the page or sheet in view ([pictures.md](pictures.md#adding-a-picture)). It arms nothing. `P` stays the next pen. A read-only page has no Picture button, and there `I` does nothing.
  - `Z`: the zoom window open or closed. It does nothing on a phone, and where the viewer shows no PDF page a pill says it works on PDF pages. While the window is open, Enter is its *New line*, except on a focused button, link, menu row, comment thread (a margin card or the thread popover) or text field, or a selected text box or picture.
  - Escape, one layer at a time: a selected text box or picture, the ink selection, an open options row (the armed tool's or the ruler's), the Shapes tool (the tool armed before it comes back), the ruler, the zoom window, then the strip. A focused comment thread (a margin card or the thread popover) takes Escape first. Closing the strip hides the ruler and closes the zoom window too.
  - Delete or Backspace deletes a lasso selection, the pictures it took with it. Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y step the stroke history: each stroke or selection edit is one entry, and the history lasts one visit of the page. A selection edit that changed pictures steps their half in the block history too ([pictures.md](pictures.md#the-lasso)).

  The More menu's *Ruler*, *Shapes* and *Zoom window* rows name R, S and Z on their right: widget keys, so neither the palette nor the pane lists them. The listener runs before App's own, so the page's block undo never sees those keys. A focused text field keeps its keys. A keyup still reaches the handler there, so a held E let go after a click into a caption ends its hold. Any other chord with Ctrl, Cmd or Alt held is left alone too, except the AltGr pair (`ctrlKey && altKey` together, a third-level letter on some layouts), which counts as no modifier.

  So Ctrl+C / X / V / D / A on handwriting are not widget keys. They are the catalog's `ink.copy`, `ink.cut`, `ink.paste`, `ink.duplicate` and `ink.selectAll` (group Handwriting, `app/appCommands.js`), all `edits: true`, each with a `when`. Copy, cut and duplicate need a lasso selection. Copy and cut take its strokes and decline a selection of pictures alone, whose key stays the browser's. Duplicate copies its strokes and its pictures. Paste needs the strip open, a tool other than the hand armed, strokes on the handwriting clipboard and no text selected (`window.getSelection().isCollapsed`). Select all needs the strip open with a tool other than the hand armed. Each `run` declines (returns `false`) a key that arrived in a text field, so Ctrl+C in a caption stays the browser's. The strip's More menu draws `ink.paste` and `ink.selectAll` as `CommandMenuItem` rows. The selection menu's buttons call the same handlers and name the chord in their titles (the labels reach `InkLayer` as `ink.keys`), so a rebound chord shows on both. While the Text tool is armed, a text box is selected or edited, or a picture is selected, the strip leaves Ctrl+Z / Ctrl+Y to `app.undo` / `app.redo` (the block history, where a box's and a picture's changes are), and Escape to a selected or edited box or a selected picture.
- **A selected text box** (`markup/TextBoxLayer.jsx`). Delete or Backspace deletes it, the arrows nudge it by 1 pt (Shift: 10 pt), Enter (outside a button) or F2 opens its editor, Escape lets go. A `keydown` listener on the document handles them and stops the keys it handles. It runs after the strip's capture-phase handler and before App's window listener, which dispatches the app catalog. As catalog commands they would collide with `app.deletePages` (Delete) and `app.renameTitle` (F2). Like Delete on a selected table, they act on the object selected on the page. The listener skips a focused text field, an open dialog (`.reportOverlay`) and any key with Ctrl, Cmd or Alt held. The box's width handle, focused, takes ← / → (1 pt, Shift: 10 pt).
- **A selected picture** (`markup/PictureLayer.jsx`, [pictures.md](pictures.md#selecting-and-moving)). The arrows nudge it by 1 pt (Shift: 10 pt), Delete or Backspace deletes it, and Escape lets go. A locked picture takes the arrows and Delete and does nothing with them. A `keydown` listener on the document handles them, as a text box's does, and skips what a box's listener skips: a focused text field, an open dialog and any key with Ctrl, Cmd or Alt held.
  - **Crop mode** takes the keys instead, through a listener of its own: Enter (outside a button) applies the crop and Escape cancels it. It skips a focused text field and any key with Ctrl, Cmd or Alt held.
  - **A focused handle** (`markup/TransformHandles.jsx`) takes the arrow keys, one step a press: the resize handle scales by 1.1 (→, ↑) or 1 / 1.1 (←, ↓) about the centre, and the rotate handle turns 15°, clockwise for → and ↑. The lasso's handles step the same way.
  - Escape during a handle's drag takes back the drag alone and leaves the picture selected: the handles take it in the capture phase and stop it there.
- **A text box's editor** dispatches only the block catalog's formatting group (Ctrl+B / I / E / Shift+X / Shift+H / K, with the account's bindings). Enter is a new line in the box, and Escape closes the editor and leaves the box selected.
- **A comment's field** (`comments/CommentComposer.jsx`, the note editor with its own undo history) keeps Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z and F2 from the app commands, which would undo a note or rename the page. Its other keys are in [comments.md](comments.md) "The composer".

## Rebinding

The account preference `keybindings` (`PREFS` in [app/prefDefs.js](../../frontend/src/app/prefDefs.js), scope `account`, so it travels in the profile — [settings.md](settings.md)) is `{ command id → chord | null }`: a chord rebinds, `null` unbinds, an absent id keeps the default. The codec keeps only well-formed entries and does not check ids against the catalog, so an entry for a command that no longer exists is ignored rather than dropped.

Settings → Keyboard lists every command by group with its effective chord as key caps (`KeyBinding` in the settings kit, `KeyCaps` from [shared/ui/KeyCaps.jsx](../../frontend/src/shared/ui/KeyCaps.jsx), which the guide draws too). Click a chord and press the new one: the recorder uses `chordFromEvent`, the very reader the dispatcher matches with, so what it shows is what fires. Backspace or Delete alone unbinds, Escape cancels, a bare letter is refused with "Add a modifier…". A row whose chord differs from its default shows a reset button; the section's action is "Reset all". A chord two commands answer to is flagged on both rows ("Also used by …") and the caps turn red. The pane does not forbid it: a block chord shadowing an app chord can be intended. The filter box narrows by label, group or chord. A "Built in" section shows the fixed keys read-only. It leads with the Enter preference, "Enter makes" New note / New line (`enterNewBlock`, an account preference, so the section carries its scope tag), and its Enter rows follow that choice. Each row carries its command's icon from [app/commandIcons.jsx](../../frontend/src/app/commandIcons.jsx), one map keyed by command id (and by the fixed rows' ids) that the palette and the editing bar read too. A command missing there shows the generic command glyph.

Guide copy never types a chord: a tour or hint names the command as `{key:app.quickOpen}` and the guide shows its effective chord the same way, rebinding included ([onboarding.md](onboarding.md)).

Browsers keep a few chords for themselves whatever the page does: Chrome's Ctrl+T / W / N, Ctrl+Tab and Ctrl+PageUp / PageDown cannot be bound. The desktop app is not a browser and could take them in its shell; Gamma itself does not.

## Adding a command

1. Add the object to the catalog of its scope, with a default chord that no command of that scope uses (`tests/commands.test.mjs` checks) and, for a block command, whether it needs an editor.
2. If it needs a new handler, add it to the `appCmdRef` context (app scope), or to the tree's `rowProps` in App.jsx and the `row` BlockRow hands the dispatcher in BlockTree.jsx (block scope); keep the mutation a pure `blockModel.js` helper. A `rowProps` handler may be an inline closure; `makeStableActions` (`shared/lib/stableActions.js`) gives each row a stable wrapper. A data prop must keep its identity while unchanged. Pass the tree through `getTree` / `lookupBlock`, never as a prop.
3. Give it an icon in [app/commandIcons.jsx](../../frontend/src/app/commandIcons.jsx).
4. Add the chord's label to the cheat sheet in [docs/user_guide/user_guide.md](../user_guide/user_guide.md).
5. Cover the flow in the browser suite (`e2e/scenarios/notes.mjs` for note keys, `quickOpen.mjs` for the palette, `settings.mjs` for the pane).

Never add a loose `if (e.ctrlKey && e.key === …)` for a shortcut: it would not appear in the palette or the pane, could not be rebound, and would not be checked against the guide. The widget keys above are the exception: they act on what a widget holds, only while it holds it.
