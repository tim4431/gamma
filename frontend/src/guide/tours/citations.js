import { T } from "../../shared/i18n/i18n.js";
// Offered when an AI reply first cites a passage: the demo follows the
// citation, and the PDF marks the quote it names.
export default {
  id: "citations",
  version: 1,
  title: T("Citations in answers"),
  trigger: { event: "chat.cited" },
  offerPlacement: "top",
  steps: [
    { id: "citation-open", anchor: "chat.citation", placement: "top", title: T("A citation opens the passage it quotes"),
      do: [{ click: "chat.citation" }, { waitFor: { event: "citation.shown" }, timeout: 15000 }, { wait: 400 }] },
    { id: "citation-mark", anchor: "pdf.citation", placement: "bottom", title: T("The quote, marked in the PDF"), next: T("Done") },
  ],
};
