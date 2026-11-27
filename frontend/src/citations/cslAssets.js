// The XML the citation engine reads (cslCore.js): the bundled styles and
// every CSL locale, each a lazy raw chunk of its own (styles/, locales/,
// their README.md), and the styles an account added, from the server
// (citationApi.fetchStyleXml). Part of the engine's lazy chunk: only
// cslCore imports it.
//
// citeproc asks for locales synchronously (sys.retrieveLocale), so they are
// loaded ahead (loadLocales) and answered from what is held (heldLocale).
// A tag with no file of its own answers with the closest file: the same
// tag in another case, a script form's file ("sr-Cyrl" → sr-Cyrl-RS), the
// language's primary dialect from locales.json ("de-LU" → de-DE), else
// en-US, which every engine reads.
import { T } from "../shared/i18n/i18n.js";
import { isBundledStyle } from "./bundledStyles.js";
import { fetchStyleXml, styleRevision } from "./citationApi.js";

// Vite turns the glob calls into lazy imports; under plain node they throw
// and the tests hand in their own loaders (provideAssets).
let styleFiles = {};
let localeFiles = {};
let indexFile = {};
try { styleFiles = import.meta.glob("./styles/*.csl", { query: "?raw", import: "default" }); } catch { /* node */ }
try { localeFiles = import.meta.glob("./locales/locales-*.xml", { query: "?raw", import: "default" }); } catch { /* node */ }
try { indexFile = import.meta.glob("./locales/locales.json", { import: "default" }); } catch { /* node */ }

// Node tests: loaders keyed as the globs key them ("./styles/apa.csl",
// "./locales/locales-en-US.xml", "./locales/locales.json"), and `added`
// in place of the server for added styles (id → {xml, parent, locale}).
let addedSource = fetchStyleXml;
export function provideAssets({ styles, locales, index, added } = {}) {
  if (styles) styleFiles = styles;
  if (locales) localeFiles = locales;
  if (index) indexFile = index;
  if (added) addedSource = added;
  held.clear();
  pending.clear();
  sources.clear();
  localeIndex = null;
}

const tagOf = (path) => /locales-(.+)\.xml$/.exec(path)?.[1];
export const localeTags = () => Object.keys(localeFiles).map(tagOf).filter(Boolean);

// locales.json: {"primary-dialects": {lang: tag}, "language-names": {tag: [native, English]}}.
let localeIndex = null;
async function loadIndex() {
  if (!localeIndex) {
    const load = indexFile["./locales/locales.json"];
    localeIndex = load ? Promise.resolve(load()).catch(() => ({})) : Promise.resolve({});
  }
  return localeIndex;
}
let index = {};

// The tag of the file that answers for `tag` (see the top of the file).
export function localeFileFor(tag) {
  const tags = localeTags();
  const wanted = String(tag || "").replace(/_/g, "-").toLowerCase();
  const exact = tags.find((have) => have.toLowerCase() === wanted);
  if (exact) return exact;
  const parts = wanted.split("-");
  if (parts[1]?.length === 4) {
    const scripted = tags.find((have) => have.toLowerCase().startsWith(`${parts[0]}-${parts[1]}-`));
    if (scripted) return scripted;
  }
  const primary = index["primary-dialects"]?.[parts[0]];
  if (primary && tags.includes(primary)) return primary;
  return "en-US";
}

const held = new Map();     // file tag → XML
const pending = new Map();  // file tag → Promise<XML>

function loadFile(fileTag) {
  if (!pending.has(fileTag)) {
    const load = localeFiles[`./locales/locales-${fileTag}.xml`];
    const promise = Promise.resolve(load ? load() : "").then((xml) => {
      if (xml) held.set(fileTag, xml);
      return xml;
    });
    promise.catch(() => pending.delete(fileTag));
    pending.set(fileTag, promise);
  }
  return pending.get(fileTag);
}

// Loads the files that answer for `tags` (and en-US).
export async function loadLocales(tags) {
  index = await loadIndex();
  const files = new Set(["en-US", ...tags.filter(Boolean).map(localeFileFor)]);
  await Promise.all([...files].map(loadFile));
}

// The XML for `tag` among the files loaded, en-US when its own is not in.
export const heldLocale = (tag) => held.get(localeFileFor(tag)) || held.get("en-US") || "";

// The languages a citation can be set in: [{tag, name, english}] in
// locales.json's order, name in the language itself.
export async function localeList() {
  const names = (await loadIndex())["language-names"] || {};
  const tags = new Set(localeTags());
  return Object.entries(names).filter(([tag]) => tags.has(tag))
    .map(([tag, [name, english] = []]) => ({ tag, name: name || tag, english: english || name || tag }));
}

// --- Styles ---------------------------------------------------------------------

// The style element's default-locale, "" when it has none.
export const defaultLocaleOf = (xml) => /<style\b[^>]*\sdefault-locale="([^"]+)"/.exec(xml || "")?.[1] || "";

// A bundled style's XML. Missing only for a dependent's parent the server
// named as bundled (X-Csl-Parent) that this build no longer carries: a 404,
// as a style gone from the server is.
async function bundledXml(id) {
  const load = styleFiles[`./styles/${id}.csl`];
  if (!load) throw Object.assign(new Error(T("The style this one depends on is not available.")), { status: 404 });
  return load();
}

const sources = new Map();  // id@revision → Promise<{xml, locale}>

// The XML a style renders with and the locale it imposes ("" for none):
// a bundled style as it is; an added one as the server answers it, which
// resolves a dependent style: a bundled parent named (`parent`, with the
// dependent's default-locale as `locale`, which it imposes on the parent,
// as Zotero does: citeproc knows nothing of the dependent), any other
// parent sent as the body in its place.
export function styleSource(id) {
  const key = `${id}@${styleRevision(id)}`;
  if (!sources.has(key)) {
    const promise = (async () => {
      if (isBundledStyle(id)) return { xml: await bundledXml(id), locale: "" };
      const { xml, parent = "", locale = "" } = await addedSource(id);
      return { xml: parent ? await bundledXml(parent) : xml, locale };
    })();
    promise.catch(() => sources.delete(key));
    sources.set(key, promise);
  }
  return sources.get(key);
}
