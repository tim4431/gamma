// The rules under the copies the app makes of citations (useCitations.js
// and its menus, docs/dev/citations.md): the style a citation is made in
// where the one chosen is missing here, what a copy makes, which pages it
// takes and in what order, what the status pill says of the pages left
// out, which styles a menu offers and in what order, the style a quote is
// cited in, and what a quote with its citation is. Pure: no React, no
// network, so node tests it (tests/citeRules.test.mjs).
import { t, tn } from "../shared/i18n/i18n.js";
import { escapeHtml } from "../shared/lib/format.js";
import { BUNDLED_STYLES, DEFAULT_STYLE, STYLE_KINDS, isBundledStyle } from "./bundledStyles.js";
import { QUICK_COPY_FORMATS } from "./formats.js";

// --- The style in use -------------------------------------------------------------

// The style a citation is made in when `wanted` is chosen (the account's
// default, or the Export dialog's pick), with `list` the account's added
// styles as citationApi holds them ({loaded, styles, error, missing}):
// `wanted` while this server can render it, else APA. A bundled style
// always can; an added one unless a render found it gone (`missing`) or the
// list, once read, does not hold it. While the list is unread, or could
// not be read, the style is taken to be here: a render that finds it gone
// puts it in `missing`. {style, gone}: `gone` when APA stands in for it.
export function styleInUse({ wanted, list }) {
  const gone = !isBundledStyle(wanted) && (!!list?.missing?.includes(wanted)
    || (!!list?.loaded && !list.error && !list.styles.some((row) => row.id === wanted)));
  return { style: gone ? DEFAULT_STYLE : wanted, gone };
}

// --- What a copy makes --------------------------------------------------------------

// What a copy `as` makes, with `quickCopy` the Quick Copy format and
// `style` the style in use: "citation" and "bibliography" in the Quick Copy
// format (the style's, else that format), a format of formats.js as itself,
// "style:<id>" a bibliography in that style (`named`), and {mode, style}
// the style's whatever Quick Copy says (the share popover's boxes, the
// Reference line). {format} | {mode, style, named?}.
export function copyPlan(as, quickCopy, style) {
  if (typeof as === "object") return { mode: as.mode || "bibliography", style: as.style || style };
  const what = (as === "citation" || as === "bibliography") && quickCopy !== "style" ? quickCopy : as;
  if (what === "citation" || what === "bibliography") return { mode: what, style };
  if (what.startsWith("style:")) return { mode: "bibliography", style: what.slice(6), named: true };
  return { format: what };
}

// --- Which pages, in what order ---------------------------------------------------

// `ids` in the order the list shows them (`order`, the visible page ids), as
// Zotero copies a selection: not the order they were clicked in. An id the
// list does not show (a page acted on from the Files window) keeps its
// place after them. Once each.
export function inListOrder(ids, order = []) {
  const at = new Map(order.map((id, i) => [id, i]));
  const unique = [...new Set(ids)];
  const shown = unique.filter((id) => at.has(id)).sort((a, b) => at.get(a) - at.get(b));
  return [...shown, ...unique.filter((id) => !at.has(id))];
}

// What the pill adds after a copy of `total` pages of which `skipped` had
// nothing to cite (the bibliography answer's `skipped`): "" when none was.
export function skippedNote(total, skipped) {
  if (!skipped) return "";
  if (skipped >= total) return total === 1 ? t("This page has no paper metadata to cite.") : t("None of these pages has paper metadata to cite.");
  return tn("{n} of {total} pages has no paper metadata.", "{n} of {total} pages have no paper metadata.", skipped, { total });
}

// --- Styles in menus ----------------------------------------------------------------

// A style class's name as the menus' sections and the badges show it
// (STYLE_KINDS), "" for a class it does not know.
export function styleKindLabel(kind) {
  const label = STYLE_KINDS.find(([id]) => id === kind)?.[1];
  return label ? t(label) : "";
}

// The styles a menu may offer: the bundled ones and the account's added
// ones (citationApi rows: {id, title, kind}), as {id, title, kind, bundled},
// once each, and `current` too when neither has it (a style named by the
// profile that this server lacks: shown, so the menu says what is chosen).
function knownStyles(added = [], current = "") {
  const out = new Map(BUNDLED_STYLES.map((style) => [style.id, { id: style.id, title: style.title, kind: style.kind, bundled: true }]));
  for (const row of added || []) {
    if (row?.id && !out.has(row.id)) out.set(row.id, { id: row.id, title: row.title || row.id, kind: row.kind || "", bundled: false });
  }
  if (current && !out.has(current)) out.set(current, { id: current, title: current, kind: "", bundled: false });
  return [...out.values()];
}
const byTitle = (a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" });

// A style MenuSelect's sections, one per class in STYLE_KINDS' order
// (Author-date, Author, Numeric, Label, Footnote; a class it does not know
// last, under Other), each sorted by title: [{kind, label (translated),
// options: [[id, title]]}], the empty ones left out. MenuSelect takes the first as
// its `heading` and `options`, the rest as `sections` (CitationChoices.jsx
// styleMenuProps).
export function styleSections({ added = [], current = "" } = {}) {
  const styles = knownStyles(added, current);
  const kinds = new Set(STYLE_KINDS.map(([kind]) => kind));
  const groups = [...STYLE_KINDS.map(([kind]) => kind), ""].map((kind) => ({
    kind, label: kind ? styleKindLabel(kind) : t("Other"),
    options: styles.filter((style) => (kind ? style.kind === kind : !kinds.has(style.kind))).sort(byTitle)
      .map((style) => [style.id, style.title]),
  }));
  return groups.filter((group) => group.options.length);
}

// The rows of a Copy as ▸ flyout, in Zotero's order: the formats (every
// Quick Copy format but the style), then the default style and the added
// ones, then the other bundled styles under a "Styles" label.
// {formats: [format], mine: [{id, title}], others: [{id, title}]}.
export function copyAsRows({ style = DEFAULT_STYLE, added = [] } = {}) {
  const styles = knownStyles(added, style);
  const mine = styles.filter((s) => s.id === style || !s.bundled);
  const others = styles.filter((s) => s.bundled && s.id !== style).sort(byTitle);
  return {
    formats: QUICK_COPY_FORMATS.filter((format) => format !== "style"),
    mine: [...mine.filter((s) => s.id === style), ...mine.filter((s) => s.id !== style).sort(byTitle)],
    others,
  };
}

// --- A quote with its citation --------------------------------------------------------

// The style a quote is cited in: the default when it cites in the text by
// name (author-date, or author as MLA: "(Smith 347)"), else APA, since a
// number or a note means nothing beside a pasted quote.
export const quoteStyle = (style, kind) => (kind === "author-date" || kind === "author" ? style : DEFAULT_STYLE);

// A quote and its citation as the clipboard takes them, {text, html}:
// “quote” (Smith et al., 2020, p. 347). The quote on one line; without a
// citation, the quote alone.
export function quotePayload(quote, citation = null) {
  const words = String(quote || "").replace(/\s+/g, " ").trim();
  const text = `“${words}”`, html = `“${escapeHtml(words)}”`;
  if (!citation?.text) return { text, html };
  return { text: `${text} ${citation.text}`, html: `${html} ${citation.html || escapeHtml(citation.text)}` };
}
