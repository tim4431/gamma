// One glyph per command, for the surfaces that list them: Settings →
// Keyboard and the command palette. Kept apart from the catalogs
// (app/appCommands.js, editor/blockCommands.js) so those stay plain
// modules the node tests import. Each glyph is the one the same action
// wears elsewhere (header, account menu, block menu); a command missing
// here falls back to TerminalIcon — the palette's own ">" prompt.
// FIXED_KEY_ICONS covers commands.js's fixedKeys() rows.
import {
  ArrowDownIcon, ArrowLeftIcon, ArrowUpIcon, BackspaceIcon, BoldIcon, BugIcon, ChevronDownIcon, ChevronRightIcon,
  ChevronUpIcon, CodeIcon, CollapseIcon, CopyIcon, CornerDownLeftIcon, DatabaseIcon, DeleteLineIcon,
  DownloadIcon, ExpandIcon, ExportIcon, FilePlusIcon, FileTextIcon, FolderFilesIcon, HighlightIcon,
  ImportIcon, IndentIcon, InfoIcon, InsertAboveIcon, ItalicIcon, KeyboardIcon, LinkIcon, MarkdownIcon,
  MessageSquareIcon, OutdentIcon, OutlineIcon, PaperclipIcon, PaperIcon, PencilIcon, PlusIcon, RedoIcon,
  SearchIcon, SettingsIcon, SlashIcon, SquareCheckIcon, StrikethroughIcon, TerminalIcon, TextCursorIcon,
  TextSearchIcon, Trash2Icon, UndoIcon, UsersIcon, XIcon,
} from "../shared/ui/Icons";

const COMMAND_ICONS = {
  "app.search": SearchIcon,
  "app.searchAll": TextSearchIcon,
  "app.quickOpen": FileTextIcon,
  "app.commandPalette": TerminalIcon,
  "app.back": ArrowLeftIcon,
  "app.undo": UndoIcon,
  "app.redo": RedoIcon,
  "app.renameTitle": PencilIcon,
  "app.toggleChat": MessageSquareIcon,
  "app.togglePdf": PaperIcon,
  "app.toggleNotes": OutlineIcon,
  "app.settings": SettingsIcon,
  "app.keyboardSettings": KeyboardIcon,
  "app.workspaces": UsersIcon,
  "app.share": LinkIcon,
  "app.metadata": InfoIcon,
  "app.attach": PaperclipIcon,
  "app.exportAnnotated": HighlightIcon,
  "app.exportNotesPdf": FileTextIcon,
  "app.exportMarkdown": MarkdownIcon,
  "app.downloadPdf": DownloadIcon,
  "app.newPage": FilePlusIcon,
  "app.import": ImportIcon,
  "app.exportObsidian": FolderFilesIcon,
  "app.exportGamma": DatabaseIcon,
  "app.reportProblem": BugIcon,
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
  "block.addToChat": MessageSquareIcon,
  "block.moveToPage": ExportIcon,
  "block.delete": Trash2Icon,
};

export const commandIcon = (cmd) => COMMAND_ICONS[cmd?.id] || TerminalIcon;

export const FIXED_KEY_ICONS = {
  newNote: PlusIcon,
  lineBreak: CornerDownLeftIcon,
  indent: IndentIcon,
  backspace: BackspaceIcon,
  fold: ChevronRightIcon,
  slash: SlashIcon,
  escape: XIcon,
};
