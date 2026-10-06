# Keyboard shortcuts

How a key becomes an action, where a shortcut is declared, how the account
changes it, and what the command palette and the Settings pane read.

## One catalog, five surfaces

A **command** is declared once, as an object in one of two catalogs:

```js
{ id: "block.deleteLine", label: t("Delete line"), group: GROUP_NOTES,
  keys: "Mod-Shift-k", edits: true, run: (ctx) => { … } }
```

- `id` — stable, `scope.name`; the account's overrides are keyed by it.
- `label` / `group` — what the palette and the pane show (`t()` at module level, like any option list).
- `keys` — the default chord, an array of them (`["Mod-y", "Mod-Shift-z"]`), or `null`: no keys until the account binds some, a palette entry meanwhile. Defaults are deliberately few: the keys Gamma always had (search, Ctrl+P, back, undo/redo, the formatting marks), plus Ctrl+Shift+P, F2, Delete, Ctrl+Shift+K, Ctrl+, (settings, the convention everywhere) and the ↑/↓ hop. F2 renames the page, or on the home library the one selected page. Delete is the file manager's key: it moves the pages selected on the home library to Recently deleted, after the confirmation, and the page menu shows it. Everything else (move, duplicate, new above, indent, fold, to-do, the view toggles, export, share…) starts unbound.
- `scope` is the catalog the command sits in:
  - [frontend/src/app/appCommands.js](../../frontend/src/app/appCommands.js) — the app commands, alive wherever focus is: search, the palettes, back, undo / redo, rename, share, metadata, attach, the exports (the Export dialog preset to a format), download, import, new page, delete the selected pages, Recently deleted, show or hide chat / PDF / notes, settings, report a problem.
  - [frontend/src/editor/blockCommands.js](../../frontend/src/editor/blockCommands.js) — the block commands, for a note whose editor is open: VSCode's line shortcuts with the block as the line (move, duplicate, delete line, new above, indent, fold, to-do, select), the hop to the neighbouring block on ↑ / ↓, and Obsidian's formatting keys.
- `when(ctx)` — applies only while this holds (a page is open, the block has children…).
- `edits: true` — skipped on a read-only page (a share view, a viewer's workspace); the dispatcher checks once, no handler repeats it.
- `needsEditor: true` (block scope) — cannot run from the palette, where no editor is open.
- `palette: false` — keyboard only (the arrow hops, the formatting marks, the palette command itself).
- `fixed: true` — shown in the pane but not rebindable (the arrow hops).
- `inDialog(dialog)` (app scope) — still dispatches while a dialog is open (see *Dialogs* below).
- `run(ctx, event)` — returning `false` declines the key: the dispatcher tries the next command, and an unhandled key keeps its default (undo in a plain input stays the browser's).

[frontend/src/app/commands.js](../../frontend/src/app/commands.js) joins the
two lists (`ALL_COMMANDS`, `GROUPS`) and, for the surfaces that show the
whole picture, lists the keys the outliner owns outright (`fixedKeys`:
Enter, Tab, Backspace on an empty note, ←/→ folding at the text's edge, `/`,
Esc). `commandChord(id, bindings)` is the one lookup of the chord a command
answers to for this account (its first effective key, `""` when unbound):
the home library's key hints (the page menu's Rename / Delete, the filter
box) and the guide's `{key:…}` tokens (`guide/keys.js`) both read it.

The five surfaces read that catalog and nothing else:

| Surface | Reads | Where |
|---|---|---|
| the key dispatchers | `keys`, `when`, `edits`, `run` | App's one window listener (app scope); a block row's `onKeyDown` (block scope) |
| the command palette (Ctrl+Shift+P, or `>` in Ctrl+P) | `label`, `group`, the effective keys, `run` | [library/QuickOpen.jsx](../../frontend/src/library/QuickOpen.jsx), fed by App's `paletteCommands()` |
| Settings → Keyboard | everything, plus `fixed` and the conflicts | [settings/SettingsKeyboard.jsx](../../frontend/src/settings/SettingsKeyboard.jsx) |
| the touch editing bar (block scope; its Undo / Redo call App's `undoBlocks`) | `label`, `when` (a button greys out, or a transient one hides), `run` | [editor/EditBar.jsx](../../frontend/src/editor/EditBar.jsx), given the row's `commandContext` ([ipad.md](ipad.md) "The editing bar") |
| a menu row (app scope: the View menu's Undo, Redo, Go to page, Command palette) | `label`, the effective keys, `when` (the row greys out), `run` | [app/CommandMenuItem.jsx](../../frontend/src/app/CommandMenuItem.jsx), given App's `appCmdRef` context ([ui-design.md](ui-design.md#menus-and-submenus)) |

So a key, a palette entry, a bar button, a menu row and the pane can never
disagree.
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

**Order.** A block row's `onKeyDown` ([editor/BlockTree.jsx](../../frontend/src/editor/BlockTree.jsx)) first serves its popups (the paste chooser, the `[[` search, the slash menu, the math autocomplete), then dispatches the block catalog, then the outliner's own keys (Tab in math and fences, Enter, Tab, ←/→ folding at the text's edge, Backspace on an empty note). A handled key never reaches the window, so a block chord shadows an app chord while an editor is open. App's window listener dispatches the app catalog and then handles Escape, which is not a command: it always closes popovers and clears selections.

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
- **New block above / below**, **indent / outdent**, **collapse / expand** (unbound) → `onEnterSibling(id, {above})`, indent / outdent (they work inside code fences and math, where Tab means something else), toggle collapse. New block below is what the new-note Enter does. Indent and outdent re-parent the block, which remounts its row: an open editor stays open with its selection (App's `keepEditorThrough`, which a move and another client's op use too), for Tab as for the command.
- **Where a block can go** is each command's `when`: move up and indent need a sibling above, move down one below, outdent a parent (`getParentInfo`). The palette leaves an inapplicable one out, and the editing bar greys its button.
- **Insert…** (unbound, needs the editor) → a `/` after the caret, with a space after a word, so the slash menu opens as if typed (`runInsertSlash`); nothing inside math or a fence. **Inline equation** (unbound, formatting) → `$…$` around the selection, else `$x$` with the x selected (`runInsertMath`). **Next math argument** (`block.nextSlot`, unbound, formatting) → what Tab does in a math snippet (`mathTabJump` in `latexCompletion.js`: the next `{}` slot, past a `\right` delimiter, then out of the span; [latex_editing.md](latex_editing.md)), for an on-screen keyboard without Tab; its `when` holds only while there is somewhere to go, never for a `$` in a code fence.
- **Toggle to-do** (unbound) → `toggleTodoLine` (mdMarks.js): `- [ ]` ↔ `- [x]` on the caret's line, a line without a box gets one after its list marker. **Select block text** (unbound) selects it. The handle menu's add-to-chat, move-to-page and delete-subtree are palette entries too.
- **Ctrl+B / I / E / Shift+X / Shift+H / K** → `runToggleMark` / `runInsertLink` in [editor/markCommands.js](../../frontend/src/editor/markCommands.js). They are plain JS so node can load the catalog, and are swallowed inside math, fences and inline code. A system keyboard's or Format menu's `beforeinput` `formatBold`, `formatItalic` and `formatStrikeThrough` (the iPad keyboard's B and I) run `block.bold`, `block.italic` and `block.strike` in every `BlockCmEditor`. Their plans of the text alone (`markPlan`, `linkPlan`, `mathInsertAt`) are also what the iPad app's editing bar applies ([ipad.md](ipad.md#editing-the-notes)).

From the palette a block command runs on the **focused row** with `editor: null` (opening the palette closes any editor); `needsEditor` commands are left out there.

## Widget keys: the markup strip and a text box

A few keys belong to a widget while it is active. They are not commands, so the palette and the pane do not list them and they cannot be rebound.

- **The markup strip** ([handwriting.md](handwriting.md)). While it is open, a capture-phase `keydown` listener on the window (App.jsx) takes `1`–`9`, `P` / `H`, `E`, `L`, `T` (the Text tool, [text_boxes.md](text_boxes.md)), `V`, Escape, Delete or Backspace on a lasso selection, and Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y for the stroke history. It runs before App's own listener, so the page's block undo never sees those keys. A focused text field keeps its keys. While the Text tool is armed or a text box is selected or edited, it leaves Ctrl+Z / Ctrl+Y to `app.undo` / `app.redo`, the block history where a box's changes are. It leaves Escape to a selected or edited box.
- **A selected text box** (`markup/TextBoxLayer.jsx`). Delete or Backspace deletes it, the arrows nudge it by 1 pt (Shift: 10 pt), Enter (outside a button) or F2 opens its editor, Escape lets go. A `keydown` listener on the document handles them and stops the keys it handles. It runs after the strip's capture-phase handler and before App's window listener, which dispatches the app catalog. As catalog commands they would collide with `app.deletePages` (Delete) and `app.renameTitle` (F2). Like Delete on a selected table, they act on the object selected on the page. The listener skips a focused text field, an open dialog (`.reportOverlay`) and any key with Ctrl, Cmd or Alt held. The box's width handle, focused, takes ← / → (1 pt, Shift: 10 pt).
- **A text box's editor** dispatches only the block catalog's formatting group (Ctrl+B / I / E / Shift+X / Shift+H / K, with the account's bindings). Enter is a new line in the box, and Escape closes the editor and leaves the box selected.

## Rebinding

The account preference `keybindings` (`PREFS` in [app/prefDefs.js](../../frontend/src/app/prefDefs.js), scope `account`, so it travels in the profile — [settings.md](settings.md)) is `{ command id → chord | null }`: a chord rebinds, `null` unbinds, an absent id keeps the default. The codec keeps only well-formed entries and does not check ids against the catalog, so an entry for a command that no longer exists is ignored rather than dropped.

Settings → Keyboard lists every command by group with its effective chord as key caps (`KeyBinding` in the settings kit, `KeyCaps` from [shared/ui/KeyCaps.jsx](../../frontend/src/shared/ui/KeyCaps.jsx), which the guide draws too). Click a chord and press the new one: the recorder uses `chordFromEvent`, the very reader the dispatcher matches with, so what it shows is what fires. Backspace or Delete alone unbinds, Escape cancels, a bare letter is refused with "Add a modifier…". A row whose chord differs from its default shows a reset button; the section's action is "Reset all". A chord two commands answer to is flagged on both rows ("Also used by …") and the caps turn red. The pane does not forbid it: a block chord shadowing an app chord can be intended. The filter box narrows by label, group or chord. A "Built in" section shows the fixed keys read-only. It leads with the Enter preference, "Enter makes" New note / New line (`enterNewNote`, an account preference, so the section carries its scope tag), and its Enter rows follow that choice. Each row carries its command's icon from [app/commandIcons.jsx](../../frontend/src/app/commandIcons.jsx), one map keyed by command id (and by the fixed rows' ids) that the palette and the editing bar read too. A command missing there shows the generic command glyph.

Guide copy never types a chord: a tour or hint names the command as `{key:app.quickOpen}` and the guide shows its effective chord the same way, rebinding included ([onboarding.md](onboarding.md)).

Browsers keep a few chords for themselves whatever the page does: Chrome's Ctrl+T / W / N, Ctrl+Tab and Ctrl+PageUp / PageDown cannot be bound. The desktop app is not a browser and could take them in its shell; Gamma itself does not.

## Adding a command

1. Add the object to the catalog of its scope, with a default chord that no command of that scope uses (`tests/commands.test.mjs` checks) and, for a block command, whether it needs an editor.
2. If it needs a new handler, add it to the `appCmdRef` context (app scope), or to the tree's `rowProps` in App.jsx and the `row` BlockRow hands the dispatcher in BlockTree.jsx (block scope); keep the mutation a pure `blockModel.js` helper.
3. Give it an icon in [app/commandIcons.jsx](../../frontend/src/app/commandIcons.jsx).
4. Add the chord's label to the cheat sheet in [docs/user_guide/user_guide.md](../user_guide/user_guide.md).
5. Cover the flow in the browser suite (`e2e/scenarios/notes.mjs` for note keys, `quickOpen.mjs` for the palette, `settings.mjs` for the pane).

Never add a loose `if (e.ctrlKey && e.key === …)` for a shortcut: it would not appear in the palette or the pane, could not be rebound, and would not be checked against the guide. The widget keys above are the exception: they act on what a widget holds, only while it holds it.
