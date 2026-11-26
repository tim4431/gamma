// What a citation copy can be besides a style's output, and the words for
// it (docs/dev/citations.md). Pure: no React, no network, so node tests it.
//
// - The file formats the server writes (routers/export.py), the one list
//   of them and their names (the Export dialog's Bibliography card reads
//   it too, transferFormats.js): the bibliography answer asked for in one
//   (GET or POST /api/bibliography ?format=) carries its text, per entry
//   and laid out as the file lays it out.
// - The key commands Better BibTeX's Quick Copy gives: \cite{a,b} for
//   LaTeX, [@a; @b] for Pandoc, @a @b for Typst, each with the keys the
//   .bib export gives the same pages.
// - Where a quote's citation points: pageLocator.
import { T, t } from "../shared/i18n/i18n.js";

// What Copy bibliography and its shortcut give (the quickCopyFormat
// preference): the style, a file format, or a key command.
export const QUICK_COPY_FORMATS = ["style", "bibtex", "biblatex", "ris", "csljson", "latex", "pandoc", "typst"];
export const FILE_FORMATS = ["bibtex", "biblatex", "ris", "csljson"];

// [format, label] for a MenuSelect (which translates the labels): the names
// of formats are names and stay as they are.
export const QUICK_COPY_OPTIONS = [
  ["style", T("Citation style")],
  ["bibtex", "BibTeX"],
  ["biblatex", "BibLaTeX"],
  ["ris", "RIS"],
  ["csljson", "CSL JSON"],
  ["latex", T("LaTeX \\cite{…}")],
  ["pandoc", T("Pandoc [@…]")],
  ["typst", T("Typst @…")],
];
const LABELS = Object.fromEntries(QUICK_COPY_OPTIONS);

// The format's name, untranslated (for a table translated where it is
// shown), and as a Copy as ▸ row shows it.
export const formatName = (format) => LABELS[format] || format;
export const formatLabel = (format) => t(formatName(format));

// The label of the Copy bibliography row (and command) for a Quick Copy
// format: "Copy bibliography" for the style, else what it copies.
export function quickCopyLabel(format) {
  switch (format) {
    case "bibtex": case "biblatex": case "ris": case "csljson":
      return t("Copy as {format}", { format: LABELS[format] });
    case "latex": return t("Copy {command}", { command: "\\cite{…}" });
    case "pandoc": return t("Copy {command}", { command: "[@…]" });
    case "typst": return t("Copy {command}", { command: "@…" });
    default: return t("Copy bibliography");
  }
}

// --- Key commands ---------------------------------------------------------------

// Pandoc's citekey: a letter, digit or _ first, then those or punctuation
// from :.#$%&-+?<>~/ followed by one of them (so "doe99." cites doe99 and
// leaves the period). Anything else needs Pandoc's braced form @{key}.
const PANDOC_KEY = /^[\p{L}\p{N}_](?:[\p{L}\p{N}_]|[:.#$%&+?<>~/-](?=[\p{L}\p{N}_]))*$/u;
// Typst's reference: label characters (identifier characters, - _ : .),
// where a trailing . or : is read as the text's. Anything else is cited
// as #cite(label("key")).
const TYPST_KEY = /^[\p{L}\p{N}_](?:[\p{L}\p{N}_.:-]*[\p{L}\p{N}_-])?$/u;

const keysOf = (keys) => [...new Set((keys || []).map((key) => String(key ?? "").trim()).filter(Boolean))];

// The command that cites `keys` in `format` ("latex" | "pandoc" | "typst"),
// "" when there is no key. Keys come from the bibliography answer, already
// cleaned to what BibTeX carries (bibtex.clean_key); the characters a
// command would break on are still removed here, so nothing pasted can end
// the command early.
export function keyCommand(format, keys) {
  const list = keysOf(keys);
  if (!list.length) return "";
  switch (format) {
    case "latex": {
      const clean = list.map((key) => key.replace(/[\s,{}\\%#~]/g, "")).filter(Boolean);
      return clean.length ? `\\cite{${clean.join(",")}}` : "";
    }
    case "pandoc":
      return `[${list.map((key) => (PANDOC_KEY.test(key) ? `@${key}` : `@{${key.replace(/[{}]/g, "")}}`)).join("; ")}]`;
    case "typst":
      return list.map((key) => (TYPST_KEY.test(key)
        ? `@${key}`
        // biome-ignore lint/suspicious/noControlCharactersInRegex: a control character has no place in a Typst string
        : `#cite(label("${key.replace(/[\u0000-\u001f\u007f]/g, "").replace(/[\\"]/g, "\\$&")}"))`)).join(" ");
    default:
      return "";
  }
}

// --- Locator --------------------------------------------------------------------

// The page a quote from a PDF cites, as a string for a CSL locator (label
// "page"), or null when there is no page (a lost or unplaced highlight).
// `pageIndex` is the PDF page, counted from 0 (or `pageNumber`, from 1, as
// a highlight's position holds it); `labels` the PDF's page labels
// (pdf.js getPageLabels(), null without); `metaPages` the record's pages;
// `pageCount` the PDF's. In order:
// - the page's label, when the PDF has labels ("iv", "347");
// - else, when the record's pages are a numeric range exactly as long as
//   the PDF, the printed page: a journal article's PDF starting at 676
//   cites its third page as 678;
// - else the PDF page number.
export function pageLocator({ pageIndex, pageNumber, labels = null, metaPages = "", pageCount = 0 } = {}) {
  const index = Number.isInteger(pageIndex) ? pageIndex : Number.isInteger(pageNumber) ? pageNumber - 1 : -1;
  if (index < 0) return null;
  const label = labels?.[index];
  if (typeof label === "string" && label.trim()) return label.trim();
  const range = /^\s*(\d+)\s*[-‐‑‒–—]+\s*(\d+)\s*$/.exec(String(metaPages || ""));
  if (range && pageCount > 0) {
    const first = Number(range[1]);
    if (Number(range[2]) - first + 1 === pageCount) return String(first + index);
  }
  return String(index + 1);
}
