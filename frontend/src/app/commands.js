// Every command in one list, for the surfaces that show them all: the
// command palette (library/QuickOpen.jsx, ">" mode), Settings → Keyboard
// (settings/SettingsKeyboard.jsx) and the tests that keep the cheat sheet
// in docs/user_guide.md honest. The catalogs themselves live next to their
// dispatchers: app/appCommands.js (App's window listener) and
// editor/blockCommands.js (a block row's keydown).
import { t } from "../shared/i18n/i18n.js";
import { APP_COMMANDS, GROUP_LIBRARY, GROUP_NAVIGATION, GROUP_PAGE, GROUP_VIEW } from "./appCommands.js";
import { BLOCK_COMMANDS, GROUP_FORMAT, GROUP_NOTES } from "../editor/blockCommands.js";

export const ALL_COMMANDS = Object.freeze([...APP_COMMANDS, ...BLOCK_COMMANDS]);

// The pane's order.
export const GROUPS = Object.freeze([GROUP_NAVIGATION, GROUP_PAGE, GROUP_VIEW, GROUP_LIBRARY, GROUP_NOTES, GROUP_FORMAT]);

export const commandById = (id) => ALL_COMMANDS.find((c) => c.id === id) || null;

// Keys the outliner and its popups own outright — no command, no rebinding —
// listed so the pane and the cheat sheet can show the whole picture. Each
// is [chords, what it does, id] (the id names its icon in
// app/commandIcons.jsx); `enterNewNote` (Settings → Reading) swaps the
// two Enter rows, so the caller picks with `fixedKeys(enterNewNote)`.
export function fixedKeys(enterNewNote) {
  return [
    [[enterNewNote ? "Enter" : "Shift-Enter"], t("New note"), "newNote"],
    [[enterNewNote ? "Shift-Enter" : "Enter"], t("Line break (continues a list)"), "lineBreak"],
    [["Tab", "Shift-Tab"], t("Indent / outdent · hop between {} arguments in math · hop table cells"), "indent"],
    [["Backspace"], t("On an empty note: delete it"), "backspace"],
    [["ArrowLeft", "ArrowRight"], t("At the text's edge: collapse / expand the children"), "fold"],
    [["/"], t("Command menu in a note"), "slash"],
    [["Escape"], t("Close popups, clear selections, cancel modes"), "escape"],
  ];
}
