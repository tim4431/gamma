// What a native reader draws from a page of the replica (the iPad app,
// docs/dev/ipad.md): the page's kind, its PDF, its sheets of paper, its ink
// groups per PDF page or sheet, and its notes as a tree; and the library's
// rows, the pages with their folders and labels named from the trees. Pure:
// the same readings the web app makes (library/libraryUtils.js
// pageAttachment, notebook/notebook.js), from replica snapshots
// (replica/tree.js).
import { inkBySheet, sheetsOf } from "../notebook/notebook.js";
import { TREES, ancestors, treeOf } from "./tree.js";

// A page's filing (`folders` / `labels`): the block ids it names.
const filing = (value) => (Array.isArray(value) ? value.filter((id) => typeof id === "string") : []);

// {id, title, folders: [folder ids], labels: [label ids], kind: "pdf" |
// "page", pdf: {docId, url, name} | null, sheets: [{id, number, paper, ink:
// [{id, url}]}] (in document order; none on a PDF's page, whose reader is
// the PDF), pdfInk: {pageNo: [{id, url}]}, tree}
export function pageView(snapshot, pageId) {
  const root = snapshot?.[pageId];
  if (!root) return null;
  const props = root.props || {};
  const tree = treeOf(snapshot, pageId);
  const docId = typeof props.doc_id === "string" ? props.doc_id : "";
  const pdf = docId || props.source_url
    ? { docId, url: props.source_url || (docId ? `/api/uploads/${docId}.pdf` : ""), name: props.original_filename || "" }
    : null;
  let sheets = [];
  if (!pdf) {
    const inks = inkBySheet(tree);
    sheets = sheetsOf(tree).map((s) => ({
      id: s.id, number: s.index + 1, paper: s.paper,
      ink: (inks.get(s.id) || []).filter((b) => b.properties.ink_url).map((b) => ({ id: b.id, url: b.properties.ink_url })),
    }));
  }
  const pdfInk = {};
  if (pdf) {
    for (const [bid, b] of Object.entries(snapshot)) {
      const page = b.props?.pdf_position?.pageNumber;
      if (bid === pageId || !b.props?.ink_url || !Number.isInteger(page)) continue;
      (pdfInk[page] ||= []).push({ id: bid, url: b.props.ink_url });
    }
  }
  return { id: pageId, title: root.content || "", folders: filing(props.folders), labels: filing(props.labels),
    kind: pdf ? "pdf" : "page", pdf, sheets, pdfInk, tree };
}

// A folder's path, its names from the top joined with " / " (a name may
// hold any character, "/" and "," too); null for an id the tree lacks. The
// twin of library/libraryUtils.js folderPath, over a flat snapshot.
function folderPath(folders, id) {
  if (id === "folders" || !folders[id]) return null;
  return [id, ...ancestors(folders, id).filter((a) => a !== "folders")].reverse().map((a) => folders[a].content).join(" / ");
}

// The library's rows from page roots [{id, content, props, position}] and
// the trees' snapshots ({folders, labels}; either is missing until a round
// brings it), newest-positioned last like the web's default order: [{id,
// title, kind, position, folders: [paths], labels: [names]}], each list in
// the page's filing order, once each. An id the trees lack (a dangling id:
// its block deleted, or not here yet) gives nothing. The trees' own roots
// are no pages and get no row.
export function libraryRows(roots, trees = {}) {
  const folders = trees.folders || {}, labels = trees.labels || {};
  const labelName = (id) => (labels[id]?.parent === "labels" ? labels[id].content : null);
  const named = (ids, name) => [...new Set(ids.map(name).filter((n) => n !== null))];
  return roots
    .filter((r) => !TREES.includes(r.id))
    .map((r) => {
      const v = pageView({ [r.id]: { parent: "root", position: r.position || "", content: r.content || "", props: r.props || {} } }, r.id);
      return { id: r.id, title: v.title || "Untitled", kind: v.kind, position: r.position || "",
        folders: named(v.folders, (id) => folderPath(folders, id)), labels: named(v.labels, labelName) };
    })
    .sort((a, b) => (a.position < b.position ? -1 : a.position > b.position ? 1 : a.id < b.id ? -1 : 1));
}
