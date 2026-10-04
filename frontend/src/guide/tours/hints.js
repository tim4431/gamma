import { T } from "../../shared/i18n/i18n.js";
// Hints: one card beside a control, no dimming, "Got it" and gone. Each is
// a triggered guide with a single step (guide/triggers.js) and never
// appears in the Tours menu. The copy is one plain sentence with its
// reason; `{key:…}` shows a key as caps — a command's the way this account
// has it bound (guide/keys.js).

export const mathKeys = {
  id: "math-keys",
  version: 1,
  hint: true,
  trigger: { event: "math.previewed" },
  steps: [{ id: "math-keys", anchor: "editor.mathPreview", placement: "top", title: T("{key:Tab} jumps to the next { }; {key:\\} looks up a symbol.") }],
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
    title: T("**Gamma Connector** (`gammapdf.com/#download`) gets paywalled papers with your own journal sign-ins.") }],
};

// A setting that travels with the account was just changed, on a server
// that offers Gamma Cloud sign-in to an account that has not linked one:
// linking is what carries these settings to the user's other Gamma servers.
export const cloudAccount = {
  id: "cloud-account",
  version: 1,
  hint: true,
  requires: { cloudLinkable: true, prefsChanged: true },
  trigger: {},
  steps: [{ id: "cloud-account", anchor: "header.account", placement: "bottom",
    title: T("Link **Gamma Cloud** (Settings → Account & sync) to take settings to your other servers.") }],
};

export default [mathKeys, blockRefs, quickOpen, back, conflicts, folders, install, approvals, exportPage,
  cloneSync, connector, cloudAccount];
