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
  steps: [{ id: "math-keys", anchor: "editor.mathPreview", placement: "top", title: T("Press {key:Tab} to jump to the next { }. Type {key:\\} to look up a symbol.") }],
};

export const blockRefs = {
  id: "block-refs",
  version: 1,
  hint: true,
  trigger: { event: "ref.search" },
  steps: [{ id: "block-refs", anchor: "editor.refSearch", placement: "right", title: T("Pick a page or note to link it here. Start with `![[` instead to embed a live, editable copy.") }],
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
  steps: [{ id: "quick-open", anchor: "header.home", placement: "bottom", title: T("Next time, press {key:app.quickOpen} to jump to any page without going home.") }],
};

// Offered after the first link jump (a citation, a page link, a chat
// citation): Back returns to the exact spot. Retired silently once the user
// has gone back on their own.
export const back = {
  id: "back",
  version: 1,
  hint: true,
  trigger: { event: "nav.pushed", doneOn: { event: "nav.back" } },
  steps: [{ id: "back", anchor: "header.back", placement: "bottom", title: T("Back takes you to where you were, at the same spot. Or press {key:app.back}.") }],
};

// A clone conflict's versions came up (the block chip's popover, the sync
// pill's list): one calm sentence beside the choice — true for the merged
// three-column case and the diverged two-column one alike.
export const conflicts = {
  id: "conflicts",
  version: 1,
  hint: true,
  trigger: { event: "conflict.shown" },
  steps: [{ id: "conflicts", anchor: "merge.versions", placement: "left", title: T("Nothing was lost: both versions are kept here. Pick the one to keep, then Apply.") }],
};

export const folders = {
  id: "folders",
  version: 1,
  hint: true,
  requires: { view: "home", unfiledLibrary: true },
  trigger: {},
  steps: [{ id: "folders", anchor: "home.listing", placement: "bottom", title: T("Tidy up with folders: right-click a page and choose Move to folder.") }],
};

// iPad and iPhone Safari: the installed app gets the whole screen.
export const install = {
  id: "install",
  version: 1,
  hint: true,
  requires: { installable: true },
  trigger: {},
  steps: [{ id: "install", anchor: null, title: T("Install Gamma for full screen and a Home Screen icon: tap Share, then Add to Home Screen.") }],
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
    title: T("Allow it once, or for the rest of this chat. Don't allow can say what to do instead.") }],
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
    title: T("Your highlights and notes come out whole: the PDF with them drawn on it, or Markdown.") }],
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
    title: T("This workspace syncs with a copy elsewhere: the pill says what is still to go, and opens the log of what changed.") }],
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
    title: T("Papers a publisher won't hand over can come from your own browser: **Gamma Connector** (`gammapdf.com/#download`) saves from the tab you're on and keeps your journal sign-ins.") }],
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
    title: T("Settings like that one can follow you: link a **Gamma Cloud** account in Settings → Account & sync, and your other Gamma servers get them too.") }],
};

export default [mathKeys, blockRefs, quickOpen, back, conflicts, folders, install, approvals, exportPage,
  cloneSync, connector, cloudAccount];
