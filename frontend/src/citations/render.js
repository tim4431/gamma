// Citations and bibliographies in a CSL style: the front door to the
// citation engine (docs/dev/citations.md). The engine (cslCore.js, with
// citeproc and the style and locale XML) is a lazy chunk behind the one
// literal import("./cslCore.js") here; nothing else may import it or
// citeproc, or the engine lands in the startup bundle (npm run budget).
//
// preloadCitations()
//   Fetches the engine chunk; call it on pointer-enter or focus of what
//   will render (Share, the page and selection menus, the Export card), so
//   no click waits on it. Never rejects.
// preloadStyle(id)
//   Also fetches a style's XML (hovering a style's row).
// render({items, style, locale, mode, output, locators})
//   -> Promise<{output, entries, styleClass}>
//   items    CSL JSON items (the bibliography answer's `csl`), in the order
//            wanted: a numeric style numbers them so.
//   style    a style id, bundled or one the account added (default "apa").
//   locale   a CSL locale tag (default "en-US"); a style with a
//            default-locale keeps its own, a dependent imposes its own.
//   mode     "bibliography" (default) or "citation", the in-text citation
//            (a note style's note) of all the items as one cluster.
//   output   "html" (default; sanitized, with Zotero's inline layout),
//            "text" or "rtf" (a whole RTF document).
//   locators for a citation: {id: "347"}, each item's page by its id.
//   output     the whole bibliography or the citation.
//   entries    a bibliography's entry per item, in the items' order, each
//              as a whole of its own in `output` (an HTML fragment with the
//              list's layout, an RTF document): the list's numbering and
//              disambiguation hold. "" for an item the style leaves out of
//              its bibliography. [] for a citation.
//   styleClass "in-text" or "note".
//   Rejects when the style cannot be had (an added style no longer on
//   this server rejects with status 404) or the engine fails.
// renderPair(opts) -> Promise<{html, text, entries}>
//   The same render in HTML and in plain text, the two flavours a copy
//   puts on the clipboard (copyPayload in shared/lib/utils.js); `entries`
//   a bibliography's entry per item as such a pair, made when first read.
// citationLocales() -> Promise<[{tag, name, english}]>
//   The languages a citation can be set in (every CSL locale).
// styleLocale(id) -> Promise<string>
//   The locale the style sets for itself (its default-locale, or the one a
//   dependent imposes on its parent), "" when the chosen language applies.
// styleMeta(id) -> {id, title, short, kind, bundled}
//   A style's names and class: a bundled style's, or an added one's as the
//   account's style list holds it (citationApi.js); an id
//   known to neither has its id as title and kind "".
// styleLabel(id) -> string
//   A style's short name, as a trigger, a pill and the Quick Copy row name
//   it: "APA 7th", "Chicago 18th (notes)", an added style's title.
import { addedStyle } from "./citationApi.js";
import { bundledStyle } from "./bundledStyles.js";

let core = null;
function loadCore() {
  if (!core) {
    core = import("./cslCore.js");
    core.catch(() => { core = null; });
  }
  return core;
}

export function preloadCitations() {
  return loadCore().then(() => undefined, () => undefined);
}

export function preloadStyle(id) {
  return loadCore().then((loaded) => loaded.preloadStyle(id)).then(() => undefined, () => undefined);
}

export async function render(opts) {
  return (await loadCore()).render(opts);
}

export async function renderPair(opts) {
  const [html, text] = await Promise.all([render({ ...opts, output: "html" }), render({ ...opts, output: "text" })]);
  const pair = { html: html.output, text: text.output };
  let entries = null;
  Object.defineProperty(pair, "entries", {
    enumerable: true,
    get: () => (entries ??= html.entries.map((entry, i) => ({ html: entry, text: text.entries[i] }))),
  });
  return pair;
}

export async function citationLocales() {
  return (await loadCore()).localeList();
}

export async function styleLocale(id) {
  return (await loadCore()).styleLocale(id);
}

export function styleMeta(id) {
  const bundled = bundledStyle(id);
  if (bundled) return { ...bundled, bundled: true };
  const row = addedStyle(id);
  if (row) return { id, title: row.title || id, short: row.short_title || "", kind: row.kind || "", bundled: false };
  return { id, title: String(id || ""), short: "", kind: "", bundled: false };
}

// A bundled style as a trigger names it: the name and edition its title
// leads with, and the variant that tells two of a family apart (Chicago's
// notes from its author-date), where the full title would be cut off
// before saying it. Names, not interface text. The menus' rows keep the
// full titles.
const SHORT_NAMES = {
  apa: "APA 7th",
  "american-chemical-society": "ACS",
  "american-medical-association": "AMA 11th",
  "american-physics-society": "APS",
  "american-political-science-association": "APSA",
  "american-sociological-association": "ASA",
  "chicago-author-date": "Chicago 18th (author-date)",
  "chicago-notes-bibliography": "Chicago 18th (notes)",
  "chicago-shortened-notes-bibliography": "Chicago 18th (short notes)",
  "elsevier-harvard": "Elsevier Harvard",
  "harvard-cite-them-right": "Cite Them Right 12th",
  ieee: "IEEE",
  "mhra-notes": "MHRA 4th (notes)",
  "modern-language-association": "MLA 9th",
  nature: "Nature",
  "nlm-citation-sequence": "NLM/Vancouver",
};
// The longest title shown whole; a longer added style goes by its CSL
// title-short, when it has one.
const TITLE_SHOWN = 28;

export function styleLabel(id) {
  if (SHORT_NAMES[id]) return SHORT_NAMES[id];
  const meta = styleMeta(id);
  return meta.title.length > TITLE_SHOWN && meta.short && meta.short.length < meta.title.length ? meta.short : meta.title;
}
