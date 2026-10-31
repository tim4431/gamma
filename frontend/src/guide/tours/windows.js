import { T } from "../../shared/i18n/i18n.js";
// Two gestures on Chat's title, each shown by a clip on its card
// (guide/media.js) while the spotlight rings the real title: a double-click
// folds the window (and unfolds it), a drag docks it elsewhere. Doing either
// ticks its step; Next moves on without it.
export default {
  id: "windows",
  version: 1,
  title: T("Arrange windows"),
  requires: { phone: false },
  show: "windows",
  trigger: { event: "page.opened", requires: { view: "pdf" } },
  offerAnchor: "chat.grip",
  offer: {
    title: T("Make room for your paper"),
    line: T("Fold and move Chat and Notes."),
  },
  steps: [
    { id: "window-collapse", anchor: "chat.grip", placement: "left",
      media: "window-fold",
      title: T("Double-click to fold, again to unfold"),
      advanceOn: { event: "window.collapsed", match: { id: "chat" } } },
    { id: "window-move", anchor: "chat.grip", placement: "left",
      media: "window-dock",
      title: T("Drag a title to move the window"),
      advanceOn: { event: "window.moved", match: { id: "chat" } },
      next: T("Done") },
  ],
};
