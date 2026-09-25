// One glyph per command, for the surfaces that list them: Settings →
// Keyboard and the command palette. Kept apart from the catalogs
// (app/appCommands.js, editor/blockCommands.js) so those stay plain
// modules the node tests import. A command missing here falls back to
// CommandIcon; FIXED_KEY_ICONS covers commands.js's fixedKeys() rows.
import {
  ArrowDownIcon, ArrowLeftIcon, ArrowUpIcon, BackspaceIcon, BoldIcon, ChevronDownIcon, ChevronRightIcon,
  ChevronUpIcon, CodeIcon, CollapseIcon, CommandIcon, CopyIcon, CornerDownLeftIcon, DeleteLineIcon,
  ExpandIcon, FileTextIcon, HighlightIcon, IndentIcon, InsertAboveIcon, ItalicIcon, KeyboardIcon,
  LinkIcon, MessageSquareIcon, OutdentIcon, OutlineIcon, PaperIcon, PencilIcon, PlusIcon, RedoIcon,
  SearchIcon, SettingsIcon, SlashIcon, SquareCheckIcon, StrikethroughIcon, TextCursorIcon,
  TextSearchIcon, UndoIcon, XIcon,
} from "../shared/ui/Icons";

const COMMAND_ICONS = {
  "app.search": SearchIcon,
  "app.searchAll": TextSearchIcon,
  "app.quickOpen": FileTextIcon,
  "app.commandPalette": CommandIcon,
  "app.back": ArrowLeftIcon,
  "app.undo": UndoIcon,
  "app.redo": RedoIcon,
  "app.renameTitle": PencilIcon,
  "app.toggleChat": MessageSquareIcon,
  "app.togglePdf": PaperIcon,
  "app.toggleNotes": OutlineIcon,
  "app.settings": SettingsIcon,
  "app.keyboardSettings": KeyboardIcon,
  "block.up": ChevronUpIcon,
  "block.down": ChevronDownIcon,
  "block.moveUp": ArrowUpIcon,
  "block.moveDown": ArrowDownIcon,
  "block.duplicateUp": CopyIcon,
  "block.duplicateDown": CopyIcon,
  "block.deleteLine": DeleteLineIcon,
  "block.newAbove": InsertAboveIcon,
  "block.indent": IndentIcon,
  "block.outdent": OutdentIcon,
  "block.fold": CollapseIcon,
  "block.unfold": ExpandIcon,
  "block.todo": SquareCheckIcon,
  "block.selectAll": TextCursorIcon,
  "block.bold": BoldIcon,
  "block.italic": ItalicIcon,
  "block.code": CodeIcon,
  "block.strike": StrikethroughIcon,
  "block.highlight": HighlightIcon,
  "block.link": LinkIcon,
};

export const commandIcon = (cmd) => COMMAND_ICONS[cmd?.id] || CommandIcon;

export const FIXED_KEY_ICONS = {
  newNote: PlusIcon,
  lineBreak: CornerDownLeftIcon,
  indent: IndentIcon,
  backspace: BackspaceIcon,
  fold: ChevronRightIcon,
  slash: SlashIcon,
  escape: XIcon,
};
