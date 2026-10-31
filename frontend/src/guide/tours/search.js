import { T } from "../../shared/i18n/i18n.js";
// Offered when a search first finds text inside PDFs: the search box looks
// like a title filter, and the part worth knowing is that it reads every
// paper, notes and highlights included, at once. Then the filter chip Tab
// makes from a folder or label name, and a result opening at its match.
// All three steps are inside the search popover, undimmed whole.
export default {
  id: "search",
  version: 1,
  title: T("Search"),
  trigger: { event: "search.results", match: { pdf: true } },
  offerAnchor: "search.input",
  offerPlacement: "bottom",
  offer: { title: T("Search reads inside your PDFs"), line: T("Narrow it to a folder, and open a paper at the match.") },
  steps: [
    { id: "search-all", anchor: "search.input", placement: "bottom", media: "search-filter",
      title: T("One search: titles, notes and every PDF"),
      body: T("“3000” finds “3,000-qubit” too.") },
    { id: "search-chip", anchor: "search.input", placement: "bottom",
      title: T("Type a folder or label, then {key:Tab}"),
      body: T("It becomes a chip that narrows the results."),
      advanceOn: { event: "search.filtered" } },
    { id: "search-open", anchor: "search.results", placement: "left",
      title: T("A result opens at its match"),
      body: T("{key:app.quickOpen} jumps to a page by name instead."), next: T("Done") },
  ],
};
