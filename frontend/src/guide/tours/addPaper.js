import { T } from "../../shared/i18n/i18n.js";
// Offered the first time Add is opened: four ways into the library, and what
// the two page kinds have to do with each other. Unlike the other triggered
// tours this one comes with the control rather than after it — opening Add is
// already the intent to add something, and the rows it explains are only on
// screen while the popover is open (docs/dev/onboarding.md).
//
// Two steps carry a drawing (`media`, guide/media.js): the fetch that turns
// an address into a saved paper, and the one page behind both views. Neither
// is a picture of a control — the spotlight still points at the real row.
export default {
  id: "add-paper",
  version: 1,
  title: T("Adding to your library"),
  requires: { editable: true },
  trigger: { event: "popover.opened", match: { name: "add" } },
  offerAnchor: "add.urlInput",
  offerPlacement: "bottom",
  offer: { title: T("Four ways to start a page"), line: T("A link, your own files, notes, or blank paper.") },
  steps: [
    {
      id: "add-url",
      anchor: "add.urlInput",
      placement: "right",
      media: "add-paper",
      title: T("Paste a paper's address"),
      body: T("A URL, a DOI or an arXiv id: Gamma fetches the PDF, looks up the title and authors, and files it here."),
    },
    {
      id: "add-upload",
      anchor: "add.upload",
      placement: "right",
      title: T("Or bring in files you already have"),
      body: T("PDFs and Markdown notes. **Upload folder…** takes a whole tree, and its subfolders become folders here."),
    },
    {
      id: "add-page",
      anchor: "add.newPage",
      placement: "right",
      title: T("A page with no PDF is just notes"),
      body: T("The same nested notes you write beside a paper, on their own."),
    },
    {
      id: "add-notebook",
      anchor: "add.newNotebook",
      placement: "right",
      media: "page-notebook",
      title: T("A notebook is that same page, with paper to write on"),
      body: T("There is one kind of page underneath: sheets of paper can join any page, and any page holding them can show them in the notebook view."),
      next: T("Done"),
    },
  ],
};
