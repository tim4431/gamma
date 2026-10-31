import { T } from "../../shared/i18n/i18n.js";
// Offered on the eighth note made in a load: by then moving and nesting
// notes pays. The ⋮⋮ handle drags (further right nests) and opens the
// note's menu; the + under it adds a note, Alt+click above. The handles show
// on hover, and while the guide points at one ([data-guide-active]). The
// steps point at the last note's handles (`pick: "last"`). Mouse only: on a
// touch screen the handle neither drags nor has a + beside every note.
export default {
  id: "notes",
  version: 1,
  title: T("Moving notes around"),
  requires: { onPage: true, editable: true, notebookView: false, touch: false },
  trigger: { event: "block.created", count: 8 },
  offerAnchor: "notes.handle",
  offerPlacement: "left",
  offer: { title: T("Your notes are growing"), line: T("Move, nest and copy them.") },
  steps: [
    { id: "notes-drag", anchor: "notes.handle", placement: "left",
      media: "notes-move",
      title: T("Drag ⋮⋮ to move a note"),
      body: T("Drop it further right to nest it.") },
    { id: "notes-menu", anchor: "notes.handle", placement: "left",
      title: T("Click ⋮⋮ for its menu"),
      body: T("Copy a link to it, duplicate it, or move it to another page."),
      scene: [{ click: "notes.handle" }] },
    { id: "notes-add", anchor: "notes.addBelow", placement: "left",
      title: T("**+** adds a note below"),
      body: T("{key:Alt}+click adds it above."),
      scene: [{ click: "notes.addBelow" }, { wait: 500 }, { click: "notes.addBelow", key: "Alt" }] },
  ],
};
