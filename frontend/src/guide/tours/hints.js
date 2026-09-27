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
// palette has been used already.
export const quickOpen = {
  id: "quick-open",
  version: 1,
  hint: true,
  trigger: { event: "home.opened", count: 4, doneOn: { event: "palette.opened" } },
  steps: [{ id: "quick-open", anchor: "header.home", placement: "bottom", title: T("Next time, press {key:app.quickOpen} to jump to any page without going home.") }],
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

export default [mathKeys, blockRefs, quickOpen, folders, install];
