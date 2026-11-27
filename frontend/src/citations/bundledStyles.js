// The citation styles Gamma ships (citations/styles/*.csl, README.md there):
// id, title, short title and class as each file's <info> gives them. The
// server reads the same list from tests/shared/citationstyles.json (a
// dependent style it stores may name one of these as its parent), and
// tests/citations.test.mjs holds this table to that file and to the XML.
// Style titles are names, not interface text: they stay untranslated.
import { T } from "../shared/i18n/i18n.js";

export const DEFAULT_STYLE = "apa";

export const BUNDLED_STYLES = Object.freeze([
  { id: "apa", title: "APA Style 7th edition", short: "Publication Manual of the American Psychological Association, with Bluebook", kind: "author-date" },
  { id: "american-chemical-society", title: "ACS Guide 2026 revision", short: "American Chemical Society", kind: "numeric" },
  { id: "american-medical-association", title: "AMA Manual of Style 11th edition", short: "American Medical Association (numeric/Vancouver/citation-sequence)", kind: "numeric" },
  { id: "american-physics-society", title: "American Physical Society", short: "APS", kind: "numeric" },
  { id: "american-political-science-association", title: "APSA Style Manual revised 2018 edition", short: "American Political Science Association", kind: "author-date" },
  { id: "american-sociological-association", title: "ASA Style Guide 6th/7th edition", short: "American Sociological Association", kind: "author-date" },
  { id: "chicago-author-date", title: "Chicago Manual of Style 18th edition (author-date)", short: "CMOS/CMS with Bluebook (author-date/AD [13.102])", kind: "author-date" },
  { id: "chicago-notes-bibliography", title: "Chicago Manual of Style 18th edition (notes and bibliography)", short: "CMOS/CMS with Bluebook (full notes and bibliography/NB [13.18])", kind: "note" },
  { id: "chicago-shortened-notes-bibliography", title: "Chicago Manual of Style 18th edition (shortened notes and bibliography)", short: "CMOS/CMS with Bluebook (shortened author-title notes [13.32])", kind: "note" },
  { id: "elsevier-harvard", title: "Elsevier (author-date/Harvard, with titles)", short: "", kind: "author-date" },
  { id: "harvard-cite-them-right", title: "Cite Them Right 12th edition (author-date/Harvard)", short: "", kind: "author-date" },
  { id: "ieee", title: "IEEE Reference Guide version 11.29.2023", short: "Institute of Electrical and Electronics Engineers", kind: "numeric" },
  { id: "mhra-notes", title: "MHRA Style Guide 4th edition (notes)", short: "Modern Humanities Research Association (notes)", kind: "note" },
  { id: "modern-language-association", title: "MLA Handbook 9th edition (in-text citations)", short: "Modern Language Association (in-text citations)", kind: "author" },
  { id: "nature", title: "Nature", short: "", kind: "numeric" },
  { id: "nlm-citation-sequence", title: "NLM/Vancouver: Citing Medicine 2nd edition (citation-sequence)", short: "National Library of Medicine, ANSI/NISO Z39.29-2005 (R2010), ICMJE Recommendations/URMs (C-S)", kind: "numeric" },
]);

const BY_ID = new Map(BUNDLED_STYLES.map((style) => [style.id, style]));
export const bundledStyle = (id) => BY_ID.get(id) || null;
export const isBundledStyle = (id) => BY_ID.has(id);

// A style's class (its <category citation-format>) as a menu section names
// it, in the order the sections come; translate with t() where shown. The
// note class is "Footnote": "Note" is the catalog's word for a note of the
// page, which another language says differently.
export const STYLE_KINDS = [
  ["author-date", T("Author-date")],
  ["author", T("Author")],
  ["numeric", T("Numeric")],
  ["label", T("Label")],
  ["note", T("Footnote")],
];
