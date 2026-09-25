import { T } from "../../shared/i18n/i18n.js";
// Offered when an editable table first appears in the notes (a pasted
// spreadsheet, a /table). Tables are edited in place, never as markdown.
export default {
  id: "tables",
  version: 1,
  title: T("Editing tables"),
  trigger: { event: "table.shown" },
  offerAnchor: "notes.table",
  steps: [
    { id: "table-add", anchor: "notes.tableAdd", placement: "bottom", title: T("The + strips add a row or a column") },
    { id: "table-move", anchor: "notes.table", placement: "bottom", title: T("Hover a row or column: drag its handle to move it, click it for options") },
    { id: "table-cell", anchor: "notes.table", placement: "bottom", title: T("Click a cell to edit it; Tab moves to the next"), next: T("Done") },
  ],
};
