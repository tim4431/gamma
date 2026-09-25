import { T } from "../../shared/i18n/i18n.js";
// Offered when an editable table first appears in the notes (a pasted
// spreadsheet, a /table). Started from the Tours menu on a page without one,
// the first step has the user add a table. Tables are edited in place,
// never as markdown.
export default {
  id: "tables",
  version: 2,
  title: T("Editing tables"),
  requires: { onPage: true, editable: true },
  trigger: { event: "table.shown" },
  offerAnchor: "notes.table",
  steps: [
    { id: "table-make", anchor: "dock.notes", placement: "left", creates: "notes.table",
      title: T("Type /table in a note, then click outside it"), advanceOn: { event: "table.shown" } },
    { id: "table-add", anchor: "notes.tableAdd", placement: "bottom", title: T("The + strips add a row or a column") },
    { id: "table-move", anchor: "notes.table", placement: "bottom", title: T("Hover a row or column: drag its handle to move it, click it for options") },
    { id: "table-cell", anchor: "notes.table", placement: "bottom", title: T("Click a cell to edit it; Tab moves to the next") },
    { id: "table-whole", anchor: "notes.tableCorner", placement: "bottom", title: T("The corner selects the whole table: copy or delete it"), next: T("Done") },
  ],
};
