// Which document pane a page shows (docs/dev/documents.md): one rule for the
// open page (App) and a group's page (workspace/PagePane.jsx), drawn by
// DocumentPane.jsx. The panes and what each brings are viewers.js.
import { VIEWERS, viewerOf } from "./viewers.js";

export { viewerOf };

// Which viewer mounts and whether it shows, for the open page or a group's
// page. doc: pageDocument(...) or null; url: the PDF viewer's input (App's
// pdfUrl, PagePane's own); sheets: how many sheets the page has; notebook:
// its notebook view is switched on; hidden: its document pane is closed;
// open: a page is open; dock: a docked Notes view.
//   viewer             what mounts: the PDF pane once it has a url, any
//                      other pane from the document alone, "notebook", or
//                      null (a url alone is a PDF)
//   spec               the viewer's descriptor (viewers.js VIEWERS), or null
//   notebook           the notebook view shows
//   notebookAvailable  the page has sheets and no document pane
//   hidden             the pane is closed (never while the notebook shows)
//   shown              a viewer mounts and shows
//   center             the notes take the middle: docked, the pane closed, or
//                      an open page with neither a pane nor its notebook (App
//                      adds its home view)
export function documentView({ doc = null, url = "", sheets = 0, notebook = false, hidden = false, open = true, dock = false } = {}) {
  const pane = viewerOf(doc?.format, doc);
  const notebookAvailable = !pane && sheets > 0;
  const notebookOn = notebookAvailable && notebook;
  const viewer = pane === "pdf" ? (url ? "pdf" : null) : pane || (url ? "pdf" : notebookOn ? "notebook" : null);
  const hiddenNow = !notebookOn && hidden;
  const pageOnly = open && !pane && !notebookOn;
  return {
    viewer,
    spec: viewer ? VIEWERS[viewer] : null,
    notebook: notebookOn,
    notebookAvailable,
    hidden: hiddenNow,
    shown: !dock && !!viewer && !hiddenNow,
    center: dock || hiddenNow || pageOnly,
  };
}
