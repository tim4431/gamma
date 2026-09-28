import { T } from "../../shared/i18n/i18n.js";
// Offered when the user makes a table (/table, or a pasted spreadsheet or
// html table) and it first renders — never for opening a page that has one.
// Started from the Tours menu on a page without one, the first step has the
// user add a table. It starts with what they came to do, typing in a cell;
// tables are edited in place, never as markdown. Its anchors pick the table
// the user just made when the page has several (anchors.js, pick: "recent").
export default {
  id: "tables",
  version: 2,
  title: T("Editing tables"),
  requires: { onPage: true, editable: true },
  trigger: { event: "table.created" },
  offerAnchor: "notes.table",
  offer: { title: T("You made a table"), line: T("Cells, rows and columns are edited in place.") },
  steps: [
    { id: "table-make", anchor: "dock.notes", placement: "left", creates: "notes.table",
      title: T("Type /table in a note, then click outside it"), advanceOn: { event: "table.shown" } },
    { id: "table-cell", anchor: "notes.table", placement: "bottom",
      title: T("Click any cell and type: {key:Tab} moves to the next, {key:Enter} saves"), advanceOn: { event: "table.edited" } },
    // Both cards keep clear of the whole table (`avoid`): the add strip sits
    // under it and the corner on its top-left, so a card flipped onto the
    // strip or the corner's other side would cover the table they describe.
    { id: "table-add", anchor: "notes.tableAdd", avoid: "notes.table", placement: "bottom",
      title: T("+ adds a row or column; hover a row or column for its handle: drag to move, click for options") },
    { id: "table-whole", anchor: "notes.tableCorner", avoid: "notes.table", placement: "top",
      title: T("The corner selects the whole table: copy, move or delete it"), next: T("Done") },
  ],
};
