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
//   locators for a citation: [{id, locator, label}] or {id: "347" |
//            {locator, label}}, by the item's id; the label defaults to
//            "page".
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
