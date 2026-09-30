import { T } from "../../shared/i18n/i18n.js";
// Offered once the library is big enough that filing pays (20 pages,
// App's `growingLibrary`) — past the folders hint, which arrives at ten and
// says only how to move one page.
//
// The step that matters is `lib-model`: a label and a folder are the same
// mechanism, and filing a paper never copies it. That is a model rather than
// a control, so it is a centred card with the drawing and no anchor — which
// also keeps it from being passed over. The steps that do point at controls
// are `optional`: a library with nothing pinned, nothing viewed lately, or a
// card showing no chips passes over them silently instead of pointing at
// nothing (docs/dev/home_library.md).
export default {
  id: "library",
  version: 1,
  title: T("Organize your library"),
  requires: { view: "home", editable: true },
  trigger: { requires: { growingLibrary: true } },
  offerAnchor: "home.listing",
  offerPlacement: "bottom",
  offer: { title: T("Your library is growing"), line: T("Folders and labels are what keep it findable.") },
  steps: [
    { id: "lib-menu", anchor: "home.card", placement: "right",
      title: T("Right-click a page for everything you can do to it"),
      body: T("Move it to a folder, label it, pin it, rename it, share it."),
      bodyTouch: T("Long-press a page for everything you can do to it: move it to a folder, label it, pin it, rename it, share it.") },
    { id: "lib-model", anchor: null, media: "labels-folders",
      title: T("Labels find papers; a folder is a label that nests"),
      body: T("A label is anything you will look for later — an author, an acronym, a project. Filing never copies the paper: one paper sits in as many folders as you like and carries any number of labels at once.") },
    { id: "lib-chips", anchor: "home.cardLabels", placement: "right", optional: true,
      title: T("A card wears the folders and labels it has"),
      body: T("Click one to see everything filed under it.") },
    { id: "lib-pinned", anchor: "home.pinned", placement: "bottom", optional: true,
      title: T("Pin what you are working on this week"),
      body: T("Pinned pages and folders stay at the top, whatever the sort says.") },
    { id: "lib-recents", anchor: "home.recents", placement: "bottom", optional: true,
      title: T("Recently viewed remembers where you were"),
      body: T("{key:app.quickOpen} reaches the same pages from anywhere, without coming back here.") },
    { id: "lib-trash", anchor: "home.trash", placement: "bottom", optional: true,
      title: T("A deleted page waits here for 30 days"),
      body: T("Restore it and it goes back to the folders and labels it had."),
      next: T("Done") },
  ],
};
