// Where a page's paper metadata came from and what tied it to the document,
// worded once for every surface that shows it: the metadata popover's Source
// row, the share popover's citation header, the Settings → Library
// maintenance table (`short`). `warn` = cite with care. Labels come
// translated or marked (T) and hints marked: the surfaces render both
// through t().

import { t, T } from "../i18n/i18n.js";
import { isCheckableType, itemTypeLabel, itemTypeOf } from "./itemTypes.js";

// Metadata that nothing tied to THIS document — the UI flags it (red "!" on
// the metadata button and beside the slide citation, red cell in the
// Settings → Library maintenance table) so nobody cites it unchecked. The
// server stores `meta.unverified` (backend/gamma/routers/metadata.py,
// `_unconfirmed`); records from before the flag existed fall back to the
// old rule: AI-extracted and of a type a registry could confirm (a thesis,
// slides or notes have none to verify against; a missing kind counts as a
// journal article, the safe default).
export const isUnverifiedPaperMeta = (source, kind, unverified = null) =>
  unverified == null ? source === "ai" && isCheckableType(itemTypeOf({ kind })) : !!unverified;

const META_SOURCE_NAMES = {
  arxiv: "arXiv", doi: "doi.org", crossref: "Crossref search", isbn: "ISBN lookup",
  openlibrary: "Open Library", googlebooks: "Google Books", page: "web page tags", manual: "edited by hand",
  zotero: "Zotero",
};

// What tied a confirmed registry record to its document (`meta.match`, the
// server's MATCH_CONFIRMED): the Source row's words after the registry's
// name, and the hint.
const TIED_BY = {
  source: [T("via its link"), T("Identified by the paper's own link: the address it was opened from, the web page it was saved from, or that page's citation tags")],
  page: [T("via its web page"), T("Matches the citation tags of the web page it was saved from")],
  typed: [T("from your identifier"), T("Looked up from the DOI or arXiv id you typed")],
  title: [T("title page match"), T("Its title is this PDF's own title, with one of its authors printed by it")],
  ai: [T("confirmed by AI"), T("Confirmed by AI: picked as this document among the records the PDF's text names, or the registry holds the title the AI read from the PDF")],
};

export function metaSourceInfo(meta) {
  if (!meta?.source) return null;
  // The user checked the record against the paper (the popover's Verify);
  // the source still says where it came from.
  if (meta.user_verified) {
    const ai = meta.source === "ai";
    const name = ai ? t("AI-extracted") : META_SOURCE_NAMES[meta.source] || meta.source;
    return { label: t("{name} — verified by hand", { name: name }), short: t("{name} ✓", { name: ai ? "AI" : name }), warn: false,
             hint: T("Checked against the paper by hand") };
  }
  const unverified = isUnverifiedPaperMeta(meta.source, meta.kind, meta.unverified);
  if (meta.source === "ai") {
    const kind = t(itemTypeLabel(itemTypeOf(meta))).toLowerCase();
    return unverified
      ? { label: T("AI-extracted — unverified"), short: "AI", warn: true,
          hint: T("Read by AI from the PDF text and not confirmed by any registry (arXiv, Crossref, Open Library) — fields may be wrong, verify before citing") }
      : { label: t("AI-extracted ({kind})", { kind: kind }), short: t("AI ({kind})", { kind: kind }), warn: false,
          hint: T("Not a published paper, so there is no registry record to verify against") };
  }
  const name = META_SOURCE_NAMES[meta.source] || meta.source;
  if (unverified) {
    return { label: t("{name} — unconfirmed", { name: name }), short: t("{name} ?", { name: name }), warn: true,
             hint: meta.match === "phrase"
               ? T("Found by searching for the PDF's title, but its title only appears in the PDF as a phrase (part of a longer title, a heading or a reference) and nothing confirmed it is this document. It may be another work on the same topic. Verify before citing")
               : meta.match === "title_only"
                 ? T("Its title is on this PDF's first page, but nothing confirmed it is this document: no author of it was found by the title, or the title is not set as the PDF's title. Check the title and authors before citing")
                 : T("Resolved from an identifier printed in the PDF or on the web page it was saved from, but nothing tied the record to this document — it may belong to a work the document cites. Verify before citing") };
  }
  if (meta.source === "manual") return { label: name, short: name, warn: false, hint: T("Fields edited by hand") };
  if (meta.source === "page") {
    return { label: name, short: name, warn: false, hint: T("Read from the citation tags of the web page it was saved from") };
  }
  const tied = TIED_BY[meta.match];
  return tied
    ? { label: t("{name} · {how}", { name: name, how: t(tied[0]) }), short: name, warn: false, hint: tied[1] }
    : { label: name, short: name, warn: false,
        hint: meta.source === "zotero" ? T("Imported from Zotero") : T("A registry record; what tied it to this PDF was not recorded") };
}
