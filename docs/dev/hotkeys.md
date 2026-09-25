# Keyboard shortcuts

How a key becomes an action, where a shortcut is declared, how the account
changes it, and what the command palette and the Settings pane read.

## One catalog, three surfaces

A **command** is declared once, as an object in one of two catalogs:

```js
{ id: "block.deleteLine", label: t("Delete line"), group: GROUP_NOTES,
  keys: "Mod-Shift-k", edits: true, run: (ctx) => { … } }
```

- `id` — stable, `scope.name`; the account's overrides are keyed by it.
- `label` / `group` — what the palette and the pane show (`t()` at module level, like any option list).
- `keys` — the default chord, or an array of them (`["Mod-y", "Mod-Shift-z"]`), or `null`: no keys until the account binds some, a palette entry meanwhile. Defaults are deliberately few — the keys Gamma always had (search, Ctrl+P, back, undo/redo, the formatting marks), Ctrl+Shift+P, F2 and Ctrl+Shift+K, plus the ↑/↓ hop; everything else (move, duplicate, new above, indent, fold, to-do, the view toggles, export, share…) starts unbound.
- `scope` is the catalog the command sits in:
  - [frontend/src/app/appCommands.js](../../frontend/src/app/appCommands.js) — the app commands, alive wherever focus is: search, the palettes, back, undo / redo, rename, share, metadata, attach, the exports (the Export dialog preset to a format), download, import, new page, show or hide chat / PDF / notes, settings, report a problem.
  - [frontend/src/editor/blockCommands.js](../../frontend/src/editor/blockCommands.js) — the block commands, for a note whose editor is open: VSCode's line shortcuts with the block as the line (move, duplicate, delete line, new above, indent, fold, to-do, select), the hop to the neighbouring block on ↑ / ↓, and Obsidian's formatting keys.
- `when(ctx)` — applies only while this holds (a page is open, the block has children…).
- `edits: true` — skipped on a read-only page (a share view, a viewer's workspace); the dispatcher checks once, no handler repeats it.
- `needsEditor: true` (block scope) — cannot run from the palette, where no editor is open.
- `palette: false` — keyboard only (the arrow hops, the formatting marks, the palette command itself).
- `fixed: true` — shown in the pane but not rebindable (the arrow hops).
- `run(ctx, event)` — returning `false` declines the key: the dispatcher tries the next command, and an unhandled key keeps its default (undo in a plain input stays the browser's).

[frontend/src/app/commands.js](../../frontend/src/app/commands.js) joins the
two lists (`ALL_COMMANDS`, `GROUPS`) and lists the keys the outliner owns
outright (`fixedKeys`: Enter, Tab, Backspace on an empty note, `/`, Esc) for
the surfaces that show the whole picture.

The three surfaces read that catalog and nothing else:

| Surface | Reads | Where |
|---|---|---|
| the key dispatchers | `keys`, `when`, `edits`, `run` | App's one window listener (app scope); a block row's `onKeyDown` (block scope) |
| the command palette (Ctrl+Shift+P, or `>` in Ctrl+P) | `label`, `group`, the effective keys, `run` | [library/QuickOpen.jsx](../../frontend/src/library/QuickOpen.jsx), fed by App's `paletteCommands()` |
| Settings → Keyboard | everything, plus `fixed` and the conflicts | [settings/SettingsKeyboard.jsx](../../frontend/src/settings/SettingsKeyboard.jsx) |

So a key, a palette entry and the pane can never disagree, and the cheat
sheet in [docs/user_guide.md](../user_guide.md) is checked against the
catalog by `tests/commands.test.mjs`: a command with a default chord whose
label is missing there fails the test.

## Chords and dispatch

[frontend/src/shared/lib/hotkeys.js](../../frontend/src/shared/lib/hotkeys.js)
is the pure core (no DOM, `tests/hotkeys.test.mjs`):

- A **chord** is a string in CodeMirror's spelling: `"Mod-Shift-k"`, `"Alt-ArrowUp"`, `"F2"`, `"Mod-,"`, `"Mod--"` (Mod plus the minus key). `Mod` is Ctrl on Windows and Linux and ⌘ on a Mac; `Ctrl` in a chord is the control key on a Mac. `normalizeChord` gives the canonical form (modifier order Mod, Ctrl, Alt, Shift; letters lowercase).
- `chordFromEvent(e)` reads a keydown by its **physical key** (`e.code`), so `Ctrl+Shift+[` is the `[` key with Shift held whatever `e.key` reports (`{`), and a letter chord means the same key on any layout. A bare modifier press is no chord. IME composition never dispatches.
- `dispatch(commands, e, ctx, bindings)` runs the first command whose effective chord matches and whose `when` holds; a handled event has its default prevented and its propagation stopped. `effectiveKeys(cmd, bindings)` is the account's override when the preference names the command (`null` = unbound), else the defaults.
- `chordLabel` / `chordParts` render a chord for the platform: `Ctrl+Shift+K` or `⇧⌘K` (Mac order ⌃⌥⇧⌘, like the system menus). `bindable` refuses a chord that would replace typing (a bare letter, digit or punctuation); function keys and anything with a modifier pass.
- `conflicts(commands, bindings)` maps each chord more than one command answers to onto those commands.

**Order.** A block row's `onKeyDown` ([editor/BlockTree.jsx](../../frontend/src/editor/BlockTree.jsx)) first serves its popups (the paste chooser, the `[[` search, the slash menu, the math autocomplete), then dispatches the block catalog, then the outliner's own keys (Tab in math and fences, Enter, Tab, ←/→ folding at the text's edge, Backspace on an empty note). A handled key never reaches the window, so a block chord shadows an app chord while an editor is open (the pane says so). App's window listener dispatches the app catalog and then handles Escape, which is not a command: it always closes popovers and clears selections.

**CodeMirror.** The block editor installs only `standardKeymap` (caret movement, Home/End, selection by word). Everything above that — the formatting marks, line and block operations — is a command, so nothing arrives from a library keymap by accident (the previous `defaultKeymap` brought Ctrl+M's tab-focus mode along, which silently stopped Tab from indenting).

## The block commands' plumbing

The block context is `{ block, tree, row, editor, readOnly }`: `editor` is the open editor's facade (`value`, `selectionStart/End`, `setSelectionRange`, `view`), `row` the tree's handlers. Every mutation goes through `setBlocks` with a pure helper, so the op diff ([collab.md](collab.md)) and the undo history ([blockHistory.js](../../frontend/src/editor/blockHistory.js)) see it like any edit:

- **↑ / ↓ at the editor's first / last visual line** (`atVerticalEdge`, by coordinates so wrapped lines count) → `onHop`: the neighbouring shown block (`visibleNeighbor`, collapsed subtrees skipped) opens its editor with the caret at its end / start. `pendingFocusRef` carries `{id, caret}` for that; a bare id still means "focus, caret where the editor puts it".
- **Move block up / down** (unbound) → `onMoveBlock`: `moveSibling` swaps with the neighbour, same reference at the edge (the key then does nothing). Moving a focused DOM node blurs it in some browsers, and that blur would close the editor: `keepEditRef` tells `onStartEdit` to ignore one close for that block within 500 ms, and the focus request re-opens the editor (`reopen` tries) and restores the caret offset.
- **Duplicate block above / below** (unbound) → `onDuplicate(id, {above})`, the handle menu's copy (fresh ids, highlight anchors stripped).
- **Ctrl+Shift+K** — the caret's line of a multi-line block (a CodeMirror change); a one-line block goes as a whole through `onDelete(id, {keepChildren: true, focus: prev})`: `removeBlockKeepChildren` lifts its children into its place (indented lines under a deleted line stay), and the block above gets the caret at its end. The handle menu's Delete still removes the subtree.
- **New block above**, **indent / outdent**, **collapse / expand** (unbound) → the existing `onEnterSibling(id, {above})`, indent / outdent (they work inside code fences and math, where Tab means something else), toggle collapse.
- **Toggle to-do** (unbound) → `toggleTodoLine` (mdMarks.js): `- [ ]` ↔ `- [x]` on the caret's line, a line without a box gets one after its list marker. **Select block text** (unbound) selects it. The handle menu's add-to-chat, move-to-page and delete-subtree are palette entries too.
- **Ctrl+B / I / E / Shift+X / Shift+H / K** → `runToggleMark` / `runInsertLink` in [editor/markCommands.js](../../frontend/src/editor/markCommands.js) (plain JS, moved out of BlockCmEditor.jsx so node can load the catalog; swallowed inside math, fences and inline code).

From the palette a block command runs on the **focused row** with `editor: null` (opening the palette closes any editor); `needsEditor` commands are left out there.

## Rebinding

The account preference `keybindings` (`PREFS` in [app/prefDefs.js](../../frontend/src/app/prefDefs.js), scope `account`, so it travels in the profile — [settings.md](settings.md)) is `{ command id → chord | null }`: a chord rebinds, `null` unbinds, an absent id keeps the default. The codec keeps only well-formed entries and does not check ids against the catalog, so an entry for a command that no longer exists is ignored rather than dropped.

Settings → Keyboard lists every command by group with its effective chord as key caps (`KeyBinding` / `KeyCaps` in the settings kit). Click a chord and press the new one: the recorder uses `chordFromEvent`, the very reader the dispatcher matches with, so what it shows is what fires. Backspace or Delete alone unbinds, Escape cancels, a bare letter is refused with "Add a modifier…". A row whose chord differs from its default shows a reset button; the section's action is "Reset all". A chord two commands answer to is flagged on both rows ("Also used by …") and the caps turn red; the pane does not forbid it (a block chord shadowing an app chord can be intended). The filter box narrows by label, group or chord. A "Built in" section shows the fixed keys read-only, the Enter rows following the Enter preference. Each row carries its command's icon — one map keyed by command id (and by the fixed rows' ids) in [app/commandIcons.jsx](../../frontend/src/app/commandIcons.jsx), which the palette reads too; a command missing there shows the generic command glyph, so give a new command its icon in the same change.

Browsers keep a few chords for themselves whatever the page does: Chrome's Ctrl+T / W / N, Ctrl+Tab and Ctrl+PageUp / PageDown cannot be bound. The desktop app is not a browser and could take them in its shell; Gamma itself does not.

## Adding a command

1. Add the object to the catalog of its scope, with a default chord that no command of that scope uses (`tests/commands.test.mjs` checks) and, for a block command, whether it needs an editor.
2. If it needs a new handler, add it to the tree's `rowProps` in App.jsx (block scope) or to the `appCmdRef` context (app scope); keep the mutation a pure `blockModel.js` helper.
3. Add the chord's label to the cheat sheet in [docs/user_guide.md](../user_guide.md).
4. Cover the flow in the browser suite (`e2e/scenarios/notes.mjs` for note keys, `quickOpen.mjs` for the palette, `settings.mjs` for the pane).

Never add a loose `if (e.ctrlKey && e.key === …)` for a shortcut: it would not appear in the palette or the pane, could not be rebound, and would not be checked against the guide.
