import { T } from "../../shared/i18n/i18n.js";
// Offered the first time Add is opened: the ways into the library, and what
// the two blank page kinds have to do with each other. Unlike the other
// triggered tours this one comes with the control rather than after it —
// opening Add is already the intent to add something, and the rows it
// explains are only on screen while the popover is open
// (docs/dev/onboarding.md).
//
// The address box shows typing into it (`scene`); the last step carries the
// drawing of the one page behind both views (`media`, guide/media.js).
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
      title: T("Paste a link, DOI or arXiv id"),
      body: T("Gamma fetches the PDF, its title and authors."),
      scene: [{ click: "add.urlInput", at: [0.12, 0.5] }, { type: "add.urlInput" }],
    },
    {
      id: "add-upload",
      anchor: "add.upload",
      placement: "right",
      title: T("Or upload PDFs and Markdown"),
      body: T("**Upload folder…** keeps its subfolders as folders."),
    },
    {
      id: "add-notebook",
      anchor: "add.newNotebook",
      placement: "right",
      media: "page-notebook",
      title: T("Or start blank: notes or a notebook"),
      body: T("It's one kind of page: any page can hold both."),
      next: T("Done"),
    },
  ],
};
