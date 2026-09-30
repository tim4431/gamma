// What a native reader draws from a page of the replica (the iPad app,
// docs/dev/ipad.md): the page's kind, its PDF, its notebook sheets, its ink
// groups per PDF page or sheet, and its notes as a tree. Pure: the same
// readings the web app makes (library/libraryUtils.js pageAttachment,
// notebook/notebook.js), from a replica snapshot (replica/tree.js).
import { inkBySheet, pageNotebook, sheetsOf } from "../notebook/notebook.js";
import { treeOf } from "./tree.js";

// {id, title, folder, kind: "pdf" | "notebook" | "page", pdf: {docId, url,
// name} | null, notebook: {paper, sheets: [{id, number, paper, ink: [{id,
// url}]}]} | null, pdfInk: {pageNo: [{id, url}]}, tree}
export function pageView(snapshot, pageId) {
  const root = snapshot?.[pageId];
  if (!root) return null;
  const props = root.props || {};
  const tree = treeOf(snapshot, pageId);
  const docId = typeof props.doc_id === "string" ? props.doc_id : "";
  const pdf = docId || props.source_url
    ? { docId, url: props.source_url || (docId ? `/api/uploads/${docId}.pdf` : ""), name: props.original_filename || "" }
    : null;
  const nb = pdf ? null : pageNotebook({ properties: props });
  let notebook = null;
  if (nb) {
    const inks = inkBySheet(tree);
    notebook = { paper: nb.paper, sheets: sheetsOf(tree, nb.paper).map((s) => ({
      id: s.id, number: s.index + 1, paper: s.paper,
      ink: (inks.get(s.id) || []).filter((b) => b.properties.ink_url).map((b) => ({ id: b.id, url: b.properties.ink_url })),
    })) };
  }
  const pdfInk = {};
  if (pdf) {
    for (const [bid, b] of Object.entries(snapshot)) {
      const page = b.props?.pdf_page;
      if (bid === pageId || !b.props?.ink_url || !Number.isInteger(page)) continue;
      (pdfInk[page] ||= []).push({ id: bid, url: b.props.ink_url });
    }
  }
  return { id: pageId, title: root.content || "", folder: props.folder || "", kind: pdf ? "pdf" : notebook ? "notebook" : "page",
    pdf, notebook, pdfInk, tree };
}

// The library's rows from page roots [{id, content, props, position}],
// newest-positioned last like the web's default order.
export function libraryRows(roots) {
  return [...roots]
    .map((r) => {
      const v = pageView({ [r.id]: { parent: "root", position: r.position || "", content: r.content || "", props: r.props || {} } }, r.id);
      return { id: r.id, title: v.title || "Untitled", folder: v.folder, kind: v.kind, position: r.position || "" };
    })
    .sort((a, b) => (a.position < b.position ? -1 : a.position > b.position ? 1 : a.id < b.id ? -1 : 1));
}
