// Which document pane a page shows (docs/dev/documents.md): one rule for the
// open page (App) and a group's page (workspace/PagePane.jsx), drawn by
// DocumentPane.jsx.

const FORMAT_VIEWER = { pdf: "pdf" };

// The document pane a format gets, or null (markdown: its notes are the file).
export function viewerOf(format) {
  return FORMAT_VIEWER[format] || null;
}

// Which viewer mounts and whether it shows, for the open page or a group's
// page. doc: pageDocument(...) or null; url: the viewer's input (App's
// pdfUrl, PagePane's own); sheets: how many sheets the page has; notebook:
// its notebook view is switched on; hidden: its document pane is closed;
// open: a page is open; dock: a docked Notes view.
//   viewer             "pdf", "notebook" or null: what mounts (a url first)
//   notebook           the notebook view shows
//   notebookAvailable  the page has sheets and no document pane
//   hidden             the pane is closed (never while the notebook shows)
//   shown              a viewer mounts and shows
//   center             the notes take the middle: docked, the pane closed, or
//                      an open page with neither a pane nor its notebook (App
//                      adds its home view)
export function documentView({ doc = null, url = "", sheets = 0, notebook = false, hidden = false, open = true, dock = false } = {}) {
  const pane = viewerOf(doc?.format);
  const notebookAvailable = !pane && sheets > 0;
  const notebookOn = notebookAvailable && notebook;
  const viewer = url ? pane || "pdf" : notebookOn ? "notebook" : null;
  const hiddenNow = !notebookOn && hidden;
  const pageOnly = open && !pane && !notebookOn;
  return {
    viewer,
    notebook: notebookOn,
    notebookAvailable,
    hidden: hiddenNow,
    shown: !dock && !!viewer && !hiddenNow,
    center: dock || hiddenNow || pageOnly,
  };
}
