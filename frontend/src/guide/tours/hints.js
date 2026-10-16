import { T } from "../../shared/i18n/i18n.js";
// Hints: one card beside a control, no dimming, "Got it" and gone. Each is
// a triggered guide with a single step (guide/triggers.js) and never
// appears in the Tours menu. The copy is one plain sentence with its
// reason; `{key:…}` shows a key as caps — a command's the way this account
// has it bound (guide/keys.js).

// Version 2: Tab, not Enter, accepts a \command suggestion (October 2026),
// so people who saw the first wording hear it again.
export const mathKeys = {
  id: "math-keys",
  version: 2,
  hint: true,
  trigger: { event: "math.previewed" },
  steps: [{ id: "math-keys", anchor: "editor.mathPreview", placement: "top", title: T("{key:Tab} accepts a suggestion, or jumps to the next { }; {key:\\} looks up a symbol.") }],
};

// Enter broke a line in a note that has text, where Enter is not the
// new-note key (Settings → Keyboard): most outliners do the reverse.
// Retired once the user has made a note with the right key.
export const newNoteKey = {
  id: "new-note-key",
  version: 1,
  hint: true,
  requires: { enterNewNote: false, editable: true },
  trigger: { event: "note.lineBreak", doneOn: { event: "block.created" } },
  steps: [{ id: "new-note-key", anchor: "notes.editor", placement: "left",
    title: T("{key:Shift-Enter} starts a new note; {key:Tab} nests it under the one above.") }],
};

// The / menu came up: opening it is already the intent, so the hint comes
// with it, as the Add tour comes with Add.
export const slashMenu = {
  id: "slash-menu",
  version: 1,
  hint: true,
  trigger: { event: "slash.opened" },
  steps: [{ id: "slash-menu", anchor: "editor.slashMenu", placement: "right",
    title: T("Keep typing to filter: `/eq` for an equation, `/red` for colour.") }],
};

// The Paste as chooser: where "Copy link to block" pays off.
export const pasteAs = {
  id: "paste-as",
  version: 1,
  hint: true,
  trigger: { event: "paste.offered" },
  steps: [{ id: "paste-as", anchor: "editor.pasteAs", placement: "right",
    title: T("Choose how it lands; a block link can become a live **Synced block**.") }],
};

// The @ menu: people and pages, and reminders, whose dates are English words.
export const mentions = {
  id: "mentions",
  version: 1,
  hint: true,
  trigger: { event: "mention.opened" },
  steps: [{ id: "mentions", anchor: "editor.mentionMenu", placement: "right",
    title: T("Mention a page or a person, or type `@fri 3pm` for a reminder.") }],
};

// A picture, table or diagram selected in the notes: its menu is a
// right-click away (a long press on a touch screen, where this copy would
// be wrong, so not there).
export const objects = {
  id: "objects",
  version: 1,
  hint: true,
  requires: { editable: true, touch: false },
  trigger: { event: "object.selected" },
  steps: [{ id: "objects", anchor: "notes.object", placement: "top",
    title: T("Right-click it to edit its source, move it or delete it.") }],
};

export const blockRefs = {
  id: "block-refs",
  version: 1,
  hint: true,
  trigger: { event: "ref.search" },
  steps: [{ id: "block-refs", anchor: "editor.refSearch", placement: "right", title: T("Pick one to link it here; start with `![[` to embed it instead.") }],
};

// Offered on the fourth trip back to the library in one sitting, unless the
// palette has been used already — never on the phone layout, where Home is
// the bottom bar's Library tab and there is no keyboard shortcut to teach.
export const quickOpen = {
  id: "quick-open",
  version: 1,
  hint: true,
  requires: { phone: false },
  trigger: { event: "home.opened", count: 4, doneOn: { event: "palette.opened" } },
  steps: [{ id: "quick-open", anchor: "header.home", placement: "bottom", title: T("{key:app.quickOpen} jumps to any page without going home.") }],
};

// Offered after the first link jump (a citation, a page link, a chat
// citation): Back returns to the exact spot. Retired silently once the user
// has gone back on their own.
export const back = {
  id: "back",
  version: 1,
  hint: true,
  trigger: { event: "nav.pushed", doneOn: { event: "nav.back" } },
  steps: [{ id: "back", anchor: "header.back", placement: "bottom", title: T("Back, or {key:app.back}, returns you to the same spot.") }],
};

// A clone conflict's versions came up (the block chip's popover, the sync
// pill's list): one calm sentence beside the choice — true for the merged
// three-column case and the diverged two-column one alike.
export const conflicts = {
  id: "conflicts",
  version: 1,
  hint: true,
  trigger: { event: "conflict.shown" },
  steps: [{ id: "conflicts", anchor: "merge.versions", placement: "left", title: T("Nothing was lost: pick the version to keep, then Apply.") }],
};

export const folders = {
  id: "folders",
  version: 1,
  hint: true,
  requires: { view: "home", unfiledLibrary: true },
  trigger: {},
  steps: [{ id: "folders", anchor: "home.listing", placement: "bottom", title: T("File pages into folders: right-click one, then Move to folder.") }],
};

// iPad and iPhone Safari: the installed app gets the whole screen.
export const install = {
  id: "install",
  version: 1,
  hint: true,
  requires: { installable: true },
  trigger: {},
  steps: [{ id: "install", anchor: null, title: T("For full screen, tap Share, then Add to Home Screen.") }],
};

// The first time the assistant asks before a change: four answers whose
// differences matter, and the one that is easy to miss (a declined call can
// say what to do instead). A card, not a tour — answering it takes the
// anchor away (docs/dev/ai.md, "Asking before a call").
export const approvals = {
  id: "approvals",
  version: 1,
  hint: true,
  trigger: { event: "approval.shown" },
  steps: [{ id: "approvals", anchor: "chat.approval", placement: "top",
    title: T("Allow once or for this chat; Don't allow can suggest another way.") }],
};

// A page with the user's own work on it, the moment they open the menu that
// holds Export — the row is only on screen while that menu is, so the hint
// comes with it rather than on the highlight that earned it.
export const exportPage = {
  id: "export-page",
  version: 1,
  hint: true,
  requires: { onPage: true, annotatedPage: true },
  trigger: { event: "popover.opened", match: { name: "menu" } },
  steps: [{ id: "export-page", anchor: "page.export", placement: "left",
    title: T("Export the PDF with your highlights drawn on it, or Markdown.") }],
};

// This workspace has an offline copy or a publication, so it carries a sync
// pill: one calm sentence about it before a conflict ever makes it loud.
export const cloneSync = {
  id: "clone-sync",
  version: 1,
  hint: true,
  requires: { clonedWorkspace: true },
  trigger: {},
  steps: [{ id: "clone-sync", anchor: "sync.pill", placement: "bottom",
    title: T("This workspace syncs with a copy: the pill shows what's pending and opens its log.") }],
};

// The first paper fetched from an address, where this browser has no Gamma
// Connector (shared/lib/connector.js asked it): the extension is the
// browser's half of that job — it saves from the tab you are already on,
// and lends the server your journal sign-ins for the PDFs it cannot get by
// itself. Never in the desktop app, whose Connector would live in the
// system browser, out of this page's reach.
export const connector = {
  id: "connector",
  version: 1,
  hint: true,
  requires: { connectorHere: false, editable: true },
  trigger: { event: "paper.fetched" },
  steps: [{ id: "connector", anchor: "header.add", placement: "bottom",
    title: T("**Gamma Connector** (`gammapdf.com/connector`) gets paywalled papers with your own journal sign-ins.") }],
};

// A setting that travels with the account was just changed, on a server
// that offers Gamma Cloud sign-in to an account that has not linked one:
// linking is what carries these settings to the user's other Gamma servers,
// and what lets it publish pages for free.
export const cloudAccount = {
  id: "cloud-account",
  version: 1,
  hint: true,
  requires: { cloudLinkable: true, prefsChanged: true },
  trigger: {},
  steps: [{ id: "cloud-account", anchor: "header.account", placement: "bottom",
    title: T("Link **Gamma Cloud** (Settings → Account & sync) to take settings to your other servers and publish pages for free.") }],
};

// A window closed with its × (desktop): it waits in the View menu, and a
// page without a layout of its own keeps it closed. Retired without showing
// if the user brings one back by themselves first; opening View answers the
// card, which would otherwise cover the menu's Windows rows.
export const windowClosed = {
  id: "window-closed",
  version: 1,
  hint: true,
  requires: { phone: false },
  trigger: { event: "window.closed", doneOn: { event: "window.shown" },
    answeredBy: { event: "popover.opened", match: { name: "menu" } } },
  steps: [{ id: "window-closed", anchor: "header.view", placement: "bottom",
    title: T("Closed windows wait in **View**: tick one to bring it back.") }],
};

// The first New chat that kept a conversation: where it went, and the
// Alt+click that throws one away instead. Opening History answers it.
export const newChat = {
  id: "new-chat",
  version: 1,
  hint: true,
  trigger: { event: "chat.archived", match: { discard: false },
    answeredBy: { event: "popover.opened", match: { name: "chathistory" } } },
  steps: [{ id: "new-chat", anchor: "chat.history", placement: "bottom",
    title: T("Your last conversation is in **History**. {key:Alt}+click **+** to discard one instead.") }],
};

// The first message that took the whole PDF with it: figures count, and so
// do the tokens.
export const pdfSwitch = {
  id: "pdf-switch",
  version: 1,
  hint: true,
  trigger: { event: "chat.sent", match: { pdf: true } },
  steps: [{ id: "pdf-switch", anchor: "chat.pdfSwitch", placement: "top",
    title: T("The whole PDF goes with each message, figures too; toggle **PDF** to send its text only.") }],
};

// The first reply that changed the library or the notes: each change can be
// taken back, which is what makes "Allow" comfortable.
export const assistantChanges = {
  id: "assistant-changes",
  version: 1,
  hint: true,
  trigger: { event: "chat.changed" },
  steps: [{ id: "assistant-changes", anchor: "chat.changes", placement: "top",
    title: T("Every change it made is listed here, each with its own undo.") }],
};

// Handwriting selected: what the box and its handles do (the handwriting
// tour stops at the lasso).
export const inkSelection = {
  id: "ink-selection",
  version: 1,
  hint: true,
  requires: { editable: true },
  trigger: { event: "ink.selected" },
  steps: [{ id: "ink-selection", anchor: "ink.selection", placement: "bottom",
    title: T("Drag to move it; the handles resize and rotate, and {key:Shift} snaps to 15°.") }],
};

// A paper whose details nothing has confirmed (the ⓘ button's red "!"),
// with the button on screen. Opening it answers the card.
export const paperDetails = {
  id: "paper-details",
  version: 1,
  hint: true,
  requires: { unverifiedMeta: true, editable: true },
  trigger: { answeredBy: { event: "popover.opened", match: { name: "meta" } } },
  steps: [{ id: "paper-details", anchor: "page.metadata", placement: "bottom",
    title: T("The **!** means unchecked details: open it to fix them and pin a cite key.") }],
};

// A folder's bibliography in the Export dialog: the link that keeps an
// Overleaf project's .bib in step with the folder.
export const bibLink = {
  id: "bib-link",
  version: 1,
  hint: true,
  trigger: { event: "bibtex.shown", match: { folder: true } },
  steps: [{ id: "bib-link", anchor: "transfer.bibLink", placement: "top",
    title: T("**Keep this .bib up to date** gives Overleaf a link that follows the folder.") }],
};

// The third Ctrl+P of a load: the same box runs every command, the unbound
// ones (the window toggles among them) included. Retired once the command
// palette has been found.
export const commandPalette = {
  id: "command-palette",
  version: 1,
  hint: true,
  requires: { phone: false },
  trigger: { event: "palette.opened", count: 3, doneOn: { event: "commands.opened" } },
  steps: [{ id: "command-palette", anchor: "palette.input", placement: "bottom",
    title: T("Type `>` here, or press {key:app.commandPalette}, to run any command.") }],
};

// A later visit of someone who finished the first tour: Settings, which no
// guide points into (it has no anchors), is a key away. Opening the account
// menu, which holds Settings…, answers the card.
export const settings = {
  id: "settings",
  version: 1,
  hint: true,
  requires: { firstRunDone: true, phone: false },
  trigger: { answeredBy: { event: "popover.opened", match: { name: "user" } } },
  steps: [{ id: "settings", anchor: "header.account", placement: "bottom",
    title: T("{key:app.settings} opens Settings: theme, language, keys and smooth scrolling.") }],
};

// The fourth tab of a load: a tab pins from its right-click menu, in the
// strip or to the library. Opening that menu answers the card.
export const tabs = {
  id: "tabs",
  version: 1,
  hint: true,
  requires: { phone: false },
  trigger: { event: "tab.opened", count: 4, answeredBy: { event: "tab.menu" } },
  steps: [{ id: "tabs", anchor: "header.tabs", placement: "bottom",
    title: T("Right-click a tab to pin it here, or to pin its page to the library.") }],
};

export default [mathKeys, blockRefs, quickOpen, back, conflicts, folders, install, approvals, exportPage,
  cloneSync, connector, cloudAccount, windowClosed, newChat, pdfSwitch, assistantChanges, inkSelection,
  newNoteKey, slashMenu, pasteAs, mentions, objects, paperDetails, bibLink, commandPalette, settings, tabs];
