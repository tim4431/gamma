# Keyboard shortcuts (September 2026)

What VS Code, Obsidian, Logseq and Notion bind, what a browser keeps for
itself, what Gamma's editor already did by accident, and why the shortcut
system took the shape in [docs/dev/hotkeys.md](../dev/hotkeys.md).

## What was found in Gamma's own code

- The block editor loaded CodeMirror's `defaultKeymap`, which is
  `standardKeymap` (caret movement, basic editing) plus an IDE set nobody
  had chosen: Alt+↑/↓ move line, Shift+Alt+↑/↓ copy line, Ctrl+Shift+K
  delete line, Ctrl+] / [ indent, Ctrl+/ comment (a no-op without a
  language), Ctrl+Alt+↑/↓ add cursor, and Ctrl+M "tab focus mode", which
  silently turns Tab into focus navigation until pressed again. All of it
  acted on lines *within one block*, most blocks are one line, none of it
  was documented, and the outliner itself had no block-level keys at all.
- Bindings were three unrelated things: an `if` chain in App's window
  listener, an `if` chain in the row's `onKeyDown`, and CodeMirror keymap
  entries; the ⋮⋮ menu, the slash menu and the cheat sheet each named the
  same actions separately.

## What the other editors bind

| Action | VS Code | Obsidian | Logseq | Notion |
|---|---|---|---|---|
| move line / block | Alt+↑/↓ | Alt+↑/↓ (core plugin) | Alt+Shift+↑/↓ | Ctrl+Shift+↑/↓ |
| duplicate | Shift+Alt+↑/↓ | — | — | Ctrl+D |
| delete line | Ctrl+Shift+K | — | — | — |
| toggle checkbox | — | Ctrl+Enter | Ctrl+Enter | Ctrl+Enter |
| insert line above | Ctrl+Shift+Enter | — | — | — |
| fold / unfold | Ctrl+Shift+[ / ] | — (a plugin) | Ctrl+↑/↓ | — |
| command palette | Ctrl+Shift+P, `>` in Ctrl+P | Ctrl+P | Ctrl+Shift+P | Ctrl+/ (menu) |
| go to symbol / line | Ctrl+Shift+O (`@`), Ctrl+G (`:`) | — | — | — |
| rename | F2 | F2 (file explorer) | — | — |

Takeaways: the line-level set is VS Code's and is the one most people
carry between tools; note apps agree with each other, not with VS Code, on
Ctrl+Enter (checkbox) and on Ctrl+D (Notion: duplicate; VS Code: add next
match; every browser: bookmark), so Ctrl+Enter follows the note apps and
Ctrl+D is left alone. The palette-prefix idea (one input, `>` for
commands) is VS Code's and lets three "go to" features share one popup.

## What a browser will not give up

Chrome reserves Ctrl+T, Ctrl+W, Ctrl+N (and their Shift forms), Ctrl+Tab,
Ctrl+Shift+Tab and Ctrl+PageUp/PageDown: `preventDefault` does not stop
them. Firefox additionally takes Ctrl+Shift+K for its web console. Ctrl+J
(downloads), Ctrl+L (address bar), Ctrl+P (print), Ctrl+F (find) and
Ctrl+, are preventable. The Electron desktop app is not a browser and
could bind the reserved ones in its shell; Gamma itself does not.

## Decisions

- **Block = line.** VS Code's line operations become block operations in
  the outliner: moving, duplicating and deleting a block, with the caret's
  line only inside a multi-line block. Deleting a block with children lifts
  the children into its place, what deleting a line does to the indented
  lines under it. Tab / Shift+Tab stay the outliner's, and Ctrl+] / [ are
  the escape hatch where Tab means something else (fences, math snippets).
- **One catalog, key as an attribute.** A command is declared once with
  its default chord; dispatch, palette, Settings pane and the cheat-sheet
  test read the same object. The catalog replaces the library keymap
  entirely (only `standardKeymap` remains), so nothing arrives by accident.
- **Physical keys.** Chords are read from `e.code`, VS Code's approach, so
  Ctrl+Shift+[ is the `[` key whatever `e.key` reports and a letter chord is
  the same key on any layout; labels are rendered per platform (⌘ ⌥ ⇧ on a
  Mac, in the system-menu order).
- **Rebinding is a preference, not a file.** VS Code's `keybindings.json`
  is the model, but the account's overrides are a small map in the synced
  profile, edited from a pane that records a chord with the same reader
  the dispatcher matches with. Conflicts are shown, not forbidden: a block
  chord shadowing an app chord while an editor is open can be intended.
- **Few defaults.** The block and view commands exist and sit in the
  palette, but only Ctrl+Shift+P, F2 and Ctrl+Shift+K were added as
  keys: a default binding is a claim on the user's muscle memory and on
  the browser's, and each one was asked for explicitly. Anything else is
  one click away in Settings → Keyboard.
- **Not done, deliberately.** Chords (Ctrl+K Ctrl+0) — Ctrl+K is the link
  key. Multi-cursor — through the textarea facade it would not survive.
  `@` and `:` palette prefixes (go to heading / PDF page) — the palette
  is built to take them when wanted.
