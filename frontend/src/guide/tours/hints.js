import { T } from "../../shared/i18n/i18n.js";
// Hints: one card beside a control, no dimming, "Got it" and gone. Each is
// a triggered guide with a single step (guide/triggers.js) and never
// appears in the Tours menu.

export const mathKeys = {
  id: "math-keys",
  version: 1,
  hint: true,
  trigger: { event: "math.previewed" },
  steps: [{ id: "math-keys", anchor: "editor.mathPreview", placement: "top", title: T("Tab jumps to the next { }; type \\ for commands") }],
};

export const blockRefs = {
  id: "block-refs",
  version: 1,
  hint: true,
  trigger: { event: "ref.search" },
  steps: [{ id: "block-refs", anchor: "editor.refSearch", placement: "right", title: T("Pick a block to mention it; ![[…]] embeds a live copy") }],
};

// Offered on the fourth trip back to the library in one sitting, unless the
// palette has been used already.
export const quickOpen = {
  id: "quick-open",
  version: 1,
  hint: true,
  trigger: { event: "home.opened", count: 4, doneOn: { event: "palette.opened" } },
  steps: [{ id: "quick-open", anchor: "header.home", placement: "bottom", title: T("Ctrl+P (⌘P) opens any page without going home") }],
};

export const folders = {
  id: "folders",
  version: 1,
  hint: true,
  requires: { view: "home", unfiledLibrary: true },
  trigger: {},
  steps: [{ id: "folders", anchor: "home.listing", placement: "bottom", title: T("Right-click a paper to move it into a folder") }],
};

// iPad and iPhone Safari: the installed app gets the whole screen.
export const install = {
  id: "install",
  version: 1,
  hint: true,
  requires: { installable: true },
  trigger: {},
  steps: [{ id: "install", anchor: null, title: T("Add Gamma to your Home Screen: Share, then Add to Home Screen") }],
};

export default [mathKeys, blockRefs, quickOpen, folders, install];
