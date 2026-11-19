import { API, apiJson } from "../shared/lib/utils.js";

// Where a chat citation's quote stands in its page's PDF, as the server
// finds it (GET /api/pages/{id}/citation; docs/dev/pdf_citations.md "AI
// citation links"): the finder a replaced PDF's highlights are placed by,
// over the MuPDF text the model quoted from, on the cited page or one beside
// it. URL parsing for citation links lives in shared/model/gammaLinks.js.
//
//   {status: "matched", page, match, rects}  match 1 for an exact copy, else
//                                            how alike (0.8 to 0.99); rects
//                                            [x1, y1, x2, y2] fractions of
//                                            the page, top-left origin
//   {status: "ambiguous", page}              two places read as alike
//   {status: "missing"}                      nothing reads alike enough
//
// A failed request (a PDF not stored, a page the file lacks, the network)
// answers "missing": the viewer has opened the page either way.
export function locateCitation({ pageId, page, quote }, { signal } = {}) {
  const params = new URLSearchParams({ pdf_page: String(page), quote });
  return apiJson(`${API}/pages/${encodeURIComponent(pageId)}/citation?${params}`, { signal })
    .catch(() => ({ status: "missing" }));
}

// A matched place's marks in the page's percentages, one per rectangle.
export function citationMarks(place) {
  return place?.status === "matched" && Array.isArray(place.rects)
    ? place.rects.map(([x1, y1, x2, y2]) => ({ left: x1 * 100, top: y1 * 100, width: (x2 - x1) * 100, height: (y2 - y1) * 100 }))
    : [];
}
