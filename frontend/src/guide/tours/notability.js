import { T } from "../../shared/i18n/i18n.js";
// A setup guide (`setup: true`): Notability notes into Gamma and back
// (docs/dev/import_export.md). It starts in Notability, so the first card
// shows the export there; the import step waits through the review and the
// import job (`import.finished`), with its card in the corner once the View
// menu has closed. Offered after a Notability import, it opens on the
// handwriting, the import ticked.
export default {
  id: "notability",
  version: 1,
  setup: true,
  title: T("Notability, both ways"),
  requires: { phone: false, guest: false },
  trigger: { event: "import.finished", match: { source: "notability" } },
  offer: { title: T("Your Notability notes are here"), line: T("Their handwriting is live, and they go back as .note files.") },
  steps: [
    { id: "nb-export", anchor: null, media: "notability-export",
      title: T("In Notability, export as **Note**"),
      body: T("A note's options → Export Options; in its library, press and hold a note.") },
    { id: "nb-import", anchor: "view.import", placement: "left",
      title: T("View → **Import…** → Notability notes"),
      body: T("A review sets each note beside the page it makes."),
      advanceOn: { event: "import.finished", match: { source: "notability" } } },
    { id: "nb-ink", anchor: null,
      title: T("The handwriting comes in live"),
      body: T("Open a page: the pen adds to it, and the eraser and lasso work on it.") },
    { id: "nb-back", anchor: null,
      title: T("Send a page back with Export → **Notability**"),
      body: T("It opens in Notability on the iPad or the Mac as a .note."), next: T("Done") },
  ],
};
