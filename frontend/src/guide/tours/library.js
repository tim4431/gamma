import { T } from "../../shared/i18n/i18n.js";
// Offered once the library is big enough that filing pays (20 pages,
// App's `growingLibrary`) — past the folders hint, which arrives at ten and
// says only how to move one page.
//
// The step that matters is `lib-model`: a label and a folder are the same
// mechanism, and filing a paper never copies it. That is a model rather than
// a control, so it is a centred card with the drawing and no anchor — which
// also keeps it from being passed over. The later steps that point at
// controls are `optional`: a library with nothing viewed lately, or nothing
// deleted, passes over them silently instead of pointing at nothing
// (docs/dev/home_library.md).
export default {
  id: "library",
  version: 1,
  title: T("Organize your library"),
  requires: { view: "home", editable: true },
  trigger: { requires: { growingLibrary: true } },
  offerAnchor: "home.listing",
  offerPlacement: "bottom",
  offer: { title: T("Your library is growing"), line: T("Folders and labels keep it findable.") },
  steps: [
    { id: "lib-menu", anchor: "home.card", placement: "right",
      title: T("Right-click a page for its menu"),
      body: T("Move, label, pin, rename or share it."),
      bodyTouch: T("Long-press a page to move, label, pin, rename or share it."),
      scene: [{ click: "home.card", button: "right", at: [0.5, 0.4] }] },
    { id: "lib-model", anchor: null, media: "labels-folders",
      title: T("A folder is a label that nests"),
      body: T("Filing never copies: one paper can sit in many folders.") },
    { id: "lib-recents", anchor: "home.recents", placement: "bottom", optional: true,
      title: T("Pick up where you left off"),
      body: T("Pin a page to keep it at the top.") },
    { id: "lib-trash", anchor: "home.trash", placement: "bottom", optional: true,
      title: T("Deleted pages wait here 30 days"),
      body: T("Restored, they return to their folders and labels."),
      next: T("Done") },
  ],
};
