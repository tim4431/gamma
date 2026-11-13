// The viewer registry (docs/dev/documents.md "Which viewer a page shows"):
// the document pane a format gets, and what each pane brings to the frame
// around it, which App reads from the pane's descriptor and never from its
// id. Pure: tests/viewers.test.mjs.
import { T } from "../shared/i18n/i18n.js";
import { isListed } from "./pageDocument.js";

// The pane a format gets; markdown has none, since a note's content is its
// blocks. A listed document (too large to keep) gets the file pane whatever
// its format.
export const FORMAT_VIEWER = { pdf: "pdf", text: "text", image: "image", audio: "media", video: "media", binary: "file" };

// What each pane brings to the frame around it:
//   tools    the strip tools that apply (app/viewerToolItems.js ids), before
//            App's own conditions (a touch screen, a read-only page)
//   host     the host renders it (DocumentPane's `render`: it takes the
//            host's state), else DocumentPane draws it from the surface
//   ink      the markup strip, Record and the player hang on it
//   regions  it takes regions (image_position)
//   margin   its threads stand in the comment margin
//   word     its name in the View menu and the phone tab (a T() key); close
//            the × button's title. "PDF" stays the untranslated format token.
export const VIEWERS = {
  pdf: { tools: ["zoom", "ink", "record", "highlighter", "translate", "selectMode"], host: true, ink: true, margin: true, word: T("PDF"), close: T("Close PDF") },
  notebook: { tools: ["zoom", "ink", "record", "paper", "notesView"], host: true, ink: true, word: T("Notebook"), close: T("Back to the notes") },
  text: { tools: [], word: T("Text"), close: T("Close the file view") },
  image: { tools: ["zoom", "selectMode"], regions: true, margin: true, word: T("Picture"), close: T("Close the picture") },
  media: { tools: [], word: T("Player"), close: T("Close the player") },
  file: { tools: [], word: T("File"), close: T("Close the file view") },
};

// The document pane a page's document gets: "file" for a listed one, the
// format's pane, or null (markdown, no document, an unknown format).
export function viewerOf(format, doc = null) {
  if (doc && isListed(doc)) return "file";
  return Object.hasOwn(FORMAT_VIEWER, format || "") ? FORMAT_VIEWER[format] : null;
}
