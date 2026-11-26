// The citation engine: citeproc-js (npm citeproc, pinned; AGPL-3.0-or-later
// per its LICENSE) over a CSL style and its locales, and the presentation
// Zotero gives its output (docs/dev/citations.md). Lazy: only render.js
// reaches it, through its literal import("./cslCore.js"), so citeproc and the
// XML stay out of the startup bundle. Node tests import it directly, with a
// DOM from jsdom (DOMPurify and the HTML presentation need one) and the
// assets from disk (cslAssets.provideAssets).
//
// What it does as Zotero does (chrome/content/zotero/xpcom/cite.js, style.js,
// quickCopy.js):
// - an engine per style, locale and output format, with URLs and DOIs
//   wrapped as links and names taken as given (the server split them);
//   a dependent style renders with its parent and its own default-locale;
//   APA-like styles capitalize subtitles;
// - a bibliography is one list: every item registered in the order given
//   (so a numeric style numbers in that order, and disambiguation and
//   2020a/2020b hold across the list); a style without a bibliography gives
//   its citations as a list instead;
// - HTML gets Zotero's inline presentation from makeBibliography's
//   parameters (line height, hanging indent, second-field-align columns,
//   entry spacing), RTF Zotero's wrapper, text the entries as they are;
// - a journal, magazine or newspaper article with pages cites no URL and
//   no accessed date (Zotero's export.citePaperJournalArticleURL off).
// And on top: the HTML is sanitized before anything shows, copies or saves
// it, and an engine that throws is dropped, since citeproc's state after a
// failure cannot be trusted.
import CSL from "citeproc";
import DOMPurify from "dompurify";
import { CSS_LOADER } from "../shared/lib/svgSanitize.js";
import { styleRevision } from "./citationApi.js";
import { defaultLocaleOf, heldLocale, loadLocales, localeList, styleSource } from "./cslAssets.js";

export { localeList };

// A style's XML fetched ahead (its row hovered).
export const preloadStyle = (id) => styleSource(id);

// The locale a style sets for itself: a dependent's imposed one, else its
// own default-locale; "" when the chosen language applies.
export async function styleLocale(id) {
  const source = await styleSource(id);
  return source.locale || defaultLocaleOf(source.xml);
}

// --- Sanitizing -----------------------------------------------------------------

// What citeproc's HTML may keep: its own markup (entries, the margin
// columns, italics, bold, small caps and the like as inline style, super-
// and subscripts) and links to http(s) only. Its escaper leaves quotes in
// attribute values alone, so a link's address (an item's URL or DOI) is the
// way an item could reach the markup: renderItem escapes their quotes, and
// what gets past anyway goes here: a link keeps its href alone, a class is
// citeproc's own (csl-…, nocase), and CSS that could load something goes
// (svgSanitize.js CSS_LOADER).
const SANITIZE = {
  ALLOWED_TAGS: ["div", "span", "i", "b", "sup", "sub", "br", "a"],
  ALLOWED_ATTR: ["href", "class", "style"],
  ALLOWED_URI_REGEXP: /^https?:\/\//i,
  ALLOW_DATA_ATTR: false,
};
const OWN_CLASS = /^(csl-[a-z-]+|nocase)$/;

let purifier = null;
function purify() {
  if (purifier) return purifier;
  purifier = DOMPurify(window);
  purifier.addHook("uponSanitizeAttribute", (node, data) => {
    const value = data.attrValue || "";
    const link = node.localName === "a";
    const keep = data.attrName === "href" ? link && /^https?:\/\//i.test(value)
      : data.attrName === "class" ? !link && value.split(/\s+/).filter(Boolean).every((name) => OWN_CLASS.test(name))
        : data.attrName === "style" ? !link && !CSS_LOADER.test(value) : true;
    if (!keep) data.keepAttr = false;
  });
  return purifier;
}

export const sanitizeHtml = (html) => purify().sanitize(String(html || ""), SANITIZE);

// --- Presentation (Zotero's makeFormattedBibliography) ---------------------------

// A bibliography's HTML with Zotero's inline styles, so a word processor
// that drops stylesheets keeps the layout. `entries` are citeproc's.
function bibliographyHtml(params, entries) {
  const doc = new window.DOMParser().parseFromString(
    `<!DOCTYPE html><html><body>${sanitizeHtml(params.bibstart + entries.join("") + params.bibend)}</body></html>`, "text/html");
  const body = doc.querySelector(".csl-bib-body");
  if (!body) return doc.body.innerHTML;
  // One of a label's characters is usually a period: the column can be
  // that much narrower.
  const maxOffset = Math.max(1, (Number.parseInt(params.maxoffset, 10) || 0) - 2);
  const entrySpacing = Number.parseInt(params.entryspacing, 10) || 0;
  const lineSpacing = Math.max(1.35, Number.parseInt(params.linespacing, 10) || 0);
  const hanging = !!params.hangingindent;
  const secondField = params["second-field-align"];
  const leftMargins = [...doc.querySelectorAll(".csl-left-margin")];
  const multiField = leftMargins.length > 0;
  // Padding on the label column, counted into the text column's offset.
  const rightPadding = 0.5;

  let style = `${body.getAttribute("style") || ""}line-height: ${lineSpacing}; `;
  // Zotero refuses a hanging indent over label columns that are not
  // aligned; the entries go without it here instead.
  if (hanging && !multiField) style += "margin-left: 2em; text-indent:-2em;";
  body.setAttribute("style", style);

  const cslEntries = [...doc.querySelectorAll(".csl-entry")];
  cslEntries.forEach((entry, i) => {
    let entryStyle = entry.getAttribute("style") || "";
    if (multiField) entryStyle += "clear: left; ";
    if (entrySpacing && i !== cslEntries.length - 1) entryStyle += `margin-bottom: ${entrySpacing}em;`;
    if (entryStyle) entry.setAttribute("style", entryStyle);
  });
  for (const div of leftMargins) {
    // Right-aligned labels when the second line aligns with the text.
    div.setAttribute("style", `float: left; padding-right: ${rightPadding}em;${secondField ? `text-align: right; width: ${maxOffset}em;` : ""}`);
  }
  for (const div of doc.querySelectorAll(".csl-right-inline")) {
    div.setAttribute("style", `margin: 0 .4em 0 ${secondField ? maxOffset + rightPadding : "0"}em;${hanging ? "padding-left: 2em; text-indent:-2em;" : ""}`);
  }
  for (const div of doc.querySelectorAll(".csl-indent")) {
    div.setAttribute("style", "margin: .5em 0 0 2em; padding: 0 0 .2em .5em; border-left: 5px solid #ccc;");
  }
  return doc.body.innerHTML;
}

// Zotero's getBibliographyFormatParameters, in twips.
function rtfParameters(params) {
  const out = { tabStops: [], indent: 0, firstLineIndent: 0, lineSpacing: 240 * params.linespacing, entrySpacing: 240 * params.entryspacing };
  if (params.hangingindent) {
    out.indent = 720;
    out.firstLineIndent = -720;
  } else if (params["second-field-align"]) {
    // Right for labels like "[1]" and "1."; Zotero's own comment says it
    // is good enough 99% of the time.
    const alignAt = 24 + params.maxoffset * 120;
    out.firstLineIndent = -alignAt;
    if (params["second-field-align"] === "margin") out.tabStops = [0];
    else { out.indent = alignAt; out.tabStops = [alignAt]; }
  }
  return out;
}

function bibliographyRtf(params, entries) {
  const bib = rtfParameters(params);
  let preamble = bib.tabStops.length ? `\\tx${bib.tabStops.join(" \\tx")} ` : "";
  preamble += `\\li${bib.indent} \\fi${bib.firstLineIndent} \\sl${bib.lineSpacing} \\slmult1 \\sa${bib.entrySpacing} `;
  return `${params.bibstart}${preamble}${entries.join("\\\r\n")}\\\r\n${params.bibend}`;
}

const bibliographyText = (params, entries) => `${params.bibstart}${entries.join("")}${params.bibend}`.trimEnd();

function present(output, params, entries) {
  if (output === "html") return bibliographyHtml(params, entries);
  if (output === "rtf") return bibliographyRtf(params, entries);
  return bibliographyText(params, entries);
}

// A style without a bibliography lists its citations (Zotero's
// makeFormattedBibliographyOrCitationList): numbered for a note style.
function citationList(output, styleClass, citations) {
  if (styleClass === "note") {
    if (output === "html") return `<ol>\n\t<li>${citations.join("</li>\n\t<li>")}</li>\n</ol>`;
    if (output === "rtf") {
      return "{\\rtf \n{\\*\\listtable{\\list\\listtemplateid1\\listhybrid{\\listlevel"
        + "\\levelnfc0\\levelnfcn0\\leveljc0\\leveljcn0\\levelfollow0\\levelstartat1"
        + "\\levelspace360\\levelindent0{\\*\\levelmarker \\{decimal\\}.}{\\leveltext"
        + "\\leveltemplateid1\\'02\\'00.;}{\\levelnumbers\\'01;}\\fi-360\\li720\\lin720 }"
        + "{\\listname ;}\\listid1}}\n{\\*\\listoverridetable{\\listoverride\\listid1"
        + "\\listoverridecount0\\ls1}}\n\\tx720\\li720\\fi-480\\ls1\\ilvl0\n"
        + citations.map((citation, i) => `{\\listtext ${i + 1}.\t}${citation}\\\n`).join("") + "}";
    }
    return citations.map((citation, i) => `${i + 1}. ${citation}\r\n`).join("").trimEnd();
  }
  if (output === "html") return citations.join("<br />");
  if (output === "rtf") return `{\\rtf \n${citations.join("\\\n")}\n}`;
  return citations.join("\r\n");
}

// --- Items ----------------------------------------------------------------------

const NO_URL_WITH_PAGES = new Set(["article-journal", "article-magazine", "article-newspaper"]);

// The renderer's copy of a CSL item (the CSL JSON file keeps its URL):
// an internal id, a type, a URL only when it is http(s), none on a paged
// article, and the event as an older style names it. The URL and the DOI
// become a link's href, which citeproc writes between double quotes
// without escaping them: theirs are %22.
function renderItem(item, id, oldEvent) {
  const out = { ...item, id, type: item?.type || "document" };
  const url = typeof out.URL === "string" ? out.URL.trim() : "";
  if (/^https?:\/\//i.test(url)) out.URL = url.replace(/"/g, "%22");
  else delete out.URL;
  const doi = typeof out.DOI === "string" ? out.DOI.trim() : "";
  if (doi) out.DOI = doi.replace(/"/g, "%22");
  else delete out.DOI;
  if (NO_URL_WITH_PAGES.has(out.type) && out.page) {
    delete out.URL;
    delete out.accessed;
  }
  if (oldEvent && out["event-title"] && !out.event) out.event = out["event-title"];
  return out;
}

// --- Engines --------------------------------------------------------------------

// Styles that capitalize the first word of a subtitle (Zotero's list).
const UPPERCASE_SUBTITLES = /^apa($|-)|^academy-of-management($|-)|^(freshwater-science)/;
const MAX_ENGINES = 24;
const engines = new Map();  // style@revision|locale|output → Promise<engine>

// Every locale citeproc may ask the engine for, as it names them: base and
// dialect of the language chosen, the style's default-locale and each
// locale= it tests (and en-US, which loadLocales adds).
function wantedLocales(xml, lang) {
  const tags = [lang, defaultLocaleOf(xml)];
  for (const match of xml.matchAll(/\slocale="([^"]+)"/g)) tags.push(...match[1].split(/\s+/));
  const out = new Set();
  for (const tag of tags.filter(Boolean)) {
    const spec = CSL.localeResolve(tag);
    out.add(spec.base);
    out.add(spec.best);
  }
  return [...out];
}

async function buildEngine(style, locale, output) {
  const source = await styleSource(style);
  const lang = source.locale || locale || "en-US";
  await loadLocales(wantedLocales(source.xml, lang));
  const sys = {
    items: new Map(),
    retrieveItem: (id) => sys.items.get(String(id)),
    retrieveLocale: (tag) => heldLocale(tag),
  };
  const parentId = /<id>\s*https?:\/\/www\.zotero\.org\/styles\/([a-z0-9-]+)\s*<\/id>/.exec(source.xml)?.[1] || "";
  if (UPPERCASE_SUBTITLES.test(style) || UPPERCASE_SUBTITLES.test(parentId)) sys.uppercase_subtitles = true;
  // The style's default-locale wins over the chosen language, except a
  // dependent's, which citeproc only learns of by force.
  const engine = new CSL.Engine(sys, source.xml, lang, !!source.locale);
  engine.setOutputFormat(output);
  engine.opt.development_extensions.wrap_url_and_doi = true;
  engine.opt.development_extensions.parse_names = false;
  engine.gammaSys = sys;
  engine.gammaOldEvent = !/event-title/.test(source.xml);
  return engine;
}

function engineFor(style, locale, output) {
  const key = `${style}@${styleRevision(style)}|${locale}|${output}`;
  if (!engines.has(key)) {
    const promise = buildEngine(style, locale, output);
    promise.catch(() => engines.delete(key));
    engines.set(key, promise);
    if (engines.size > MAX_ENGINES) engines.delete(engines.keys().next().value);
  }
  return { key, promise: engines.get(key) };
}

// --- Rendering ------------------------------------------------------------------

// An item's page in a citation, from `locators` ({id: "347"}, by the
// item's own id).
function locatorOf(locators, id) {
  const locator = String(locators?.[id] ?? "").trim();
  return locator ? { locator, label: "page" } : null;
}

const OUTPUTS = new Set(["html", "text", "rtf"]);

// See render.js for the options and the answer.
export async function render({ items = [], style = "apa", locale = "en-US", mode = "bibliography", output = "html", locators = null } = {}) {
  if (!OUTPUTS.has(output)) throw new Error(`Unknown citation output "${output}"`);
  const { key, promise } = engineFor(style, locale, output);
  const engine = await promise;
  try {
    return run(engine, { items, mode, output, locators });
  } catch (err) {
    engines.delete(key);
    throw err;
  }
}

// One synchronous pass over a cached engine (nothing awaited, so two
// renders never interleave on it). The registry is emptied first: ids are
// positions, so a new list must not meet the last one's items.
function run(engine, { items, mode, output, locators }) {
  const sys = engine.gammaSys;
  const ids = items.map((_, i) => `item-${i}`);
  sys.items = new Map(items.map((item, i) => [ids[i], renderItem(item, ids[i], engine.gammaOldEvent)]));
  engine.updateItems([]);
  engine.updateItems(ids);
  const styleClass = engine.opt.class === "note" ? "note" : "in-text";
  if (!items.length) return { output: "", entries: [], styleClass };

  const cite = (citationItems) => engine.previewCitationCluster(
    { citationItems, properties: { noteIndex: 0 } }, [], [], output);
  const clean = (text) => (output === "html" ? sanitizeHtml(text) : text).trim();

  if (mode === "citation") {
    const citationItems = items.map((item, i) => ({ id: ids[i], ...locatorOf(locators, item?.id) }));
    return { output: clean(cite(citationItems)), entries: [], styleClass };
  }

  const bib = engine.makeBibliography();
  if (!bib) {
    const citations = ids.map((id) => cite([{ id }]));
    const shown = output === "html" ? citations.map(sanitizeHtml) : citations;
    return { output: citationList(output, styleClass, shown), entries: shown.map((text) => text.trim()), styleClass };
  }
  const [params, rendered] = bib;
  const byId = new Map();
  params.entry_ids.forEach((group, i) => { for (const id of group) byId.set(String(id), rendered[i]); });
  // An item the style leaves out of its bibliography (Chicago's notes
  // style has no entry for a talk) has "". Made when first read: a copy
  // of a whole folder's list needs none of them.
  const result = { output: present(output, params, rendered), styleClass };
  let entries = null;
  Object.defineProperty(result, "entries", {
    enumerable: true,
    get: () => (entries ??= ids.map((id) => (byId.has(id) ? present(output, params, [byId.get(id)]) : ""))),
  });
  return result;
}
