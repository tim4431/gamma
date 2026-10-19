import { T } from "../../shared/i18n/i18n.js";
// Offered once the library is big enough that filing pays (20 pages,
// App's `growingLibrary`) — past the folders hint, which arrives at ten and
// says only how to move one page.
//
// The steps that matter are `lib-find` and `lib-model`: a label is a word
// you would search for, a folder groups papers by topic or project and can
// hold other folders, and one paper can sit in several. Their examples stay
// general (a method, an author), never one field's (docs/dev/onboarding.md
// "Rules"); the user guide gives research ones. That is a model rather than
// a control, so they are centred cards with no anchor, the second with the
// drawing — which also keeps them from being passed over. Around them, the
// listing bar's filter box and Select (many at once), and its Labels view, which shows
// that model on screen. The later steps that point at controls that may be
// missing are `optional`: a label view has no Labels toggle, and a library
// with nothing viewed lately, or nothing deleted, passes over those strips
// silently instead of pointing at nothing (docs/dev/home_library.md).
export default {
  id: "library",
  version: 1,
  title: T("Organize your library"),
  requires: { view: "home", editable: true },
  trigger: { requires: { growingLibrary: true } },
  offerAnchor: "home.listing",
  offerPlacement: "bottom",
  offer: { title: T("Your library is growing"), line: T("Labels and folders make papers easy to find.") },
  steps: [
    { id: "lib-menu", anchor: "home.card", placement: "right",
      title: T("Right-click a page for its menu"),
      body: T("Move, label, pin, rename or share it."),
      bodyTouch: T("Long-press a page to move, label, pin, rename or share it."),
      scene: [{ click: "home.card", button: "right", at: [0.5, 0.4] }] },
    { id: "lib-filter", anchor: "home.filter", placement: "bottom",
      title: T("Type to filter by title or label"),
      body: T("With no match, {key:Enter} searches inside notes and PDFs."),
      scene: [{ click: "home.filter", at: [0.3, 0.5] }, { type: "home.filter" }] },
    { id: "lib-select", anchor: "home.select", placement: "bottom",
      title: T("**Select** to move or label many at once"),
      body: T("{key:Mod}+click or {key:Shift}+click works too.") },
    { id: "lib-find", anchor: null,
      title: T("Labels help you find papers"),
      body: T("Use words you'd search for, like a method or an author. Click one to see its papers.") },
    { id: "lib-model", anchor: null, media: "labels-folders",
      title: T("Folders group papers by topic"),
      body: T("Like labels, but they can hold other folders. A paper can be in several at once.") },
    { id: "lib-labels", anchor: "home.labelsView", placement: "bottom", optional: true,
      title: T("**Labels** shows each label like a folder") },
    { id: "lib-recents", anchor: "home.recents", placement: "bottom", optional: true,
      title: T("Pick up where you left off"),
      body: T("Pin a page to keep it at the top.") },
    { id: "lib-trash", anchor: "home.trash", placement: "bottom", optional: true,
      title: T("Deleted pages wait here 30 days"),
      body: T("Restore one and it goes back to its folders and labels."),
      next: T("Done") },
  ],
};
