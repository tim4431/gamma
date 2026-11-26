// The citation engine (src/citations: render.js over cslCore.js) with the
// bundled styles and locales read from disk, the Copy as groups, the key
// commands and the locator beside it (formats.js), and the files themselves
// against their READMEs. jsdom supplies the DOM that sanitizing and the HTML
// layout need.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { register } from "node:module";
import { test } from "node:test";
import { JSDOM } from "jsdom";

// shared/lib/utils.js (through citationApi.js) imports without extensions
// and wraps window.fetch as it loads.
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    try { return await next(specifier, context); } catch (err) {
      if (err?.code === "ERR_MODULE_NOT_FOUND" && /^\\.\\.?\\//.test(specifier) && !/\\.[a-z]+$/.test(specifier)) return next(specifier + ".js", context);
      throw err;
    }
  }`));
globalThis.window = new JSDOM("", { url: "http://gamma.test/" }).window;
window.fetch = async () => { throw new Error("no server in this test"); };

const DIR = new URL("../src/citations/", import.meta.url);
const read = (path) => fs.readFileSync(new URL(path, DIR), "utf8");
const files = (sub, pattern) => fs.readdirSync(new URL(sub, DIR)).filter((name) => pattern.test(name));
const loaders = (sub, pattern) => Object.fromEntries(files(sub, pattern).map((name) => [`./${sub}${name}`, async () => read(sub + name)]));

const { provideAssets } = await import("../src/citations/cslAssets.js");
const { render, renderPair } = await import("../src/citations/render.js");
const { sanitizeHtml } = await import("../src/citations/cslCore.js");
const { BUNDLED_STYLES } = await import("../src/citations/bundledStyles.js");
const { copyAsGroups, keyCommand, pageLocator } = await import("../src/citations/formats.js");
const { readProfile } = await import("../src/app/prefDefs.js");

// Added styles as the server would answer them (citationApi.fetchStyleXml).
const ADDED = {};
provideAssets({
  styles: loaders("styles/", /\.csl$/),
  locales: loaders("locales/", /^locales-.+\.xml$/),
  index: { "./locales/locales.json": async () => JSON.parse(read("locales/locales.json")) },
  added: async (id) => {
    if (!ADDED[id]) throw Object.assign(new Error("not on this server"), { status: 404 });
    return ADDED[id];
  },
});

// The records a library holds, as the server's CSL items give them.
const ARTICLE = {
  id: "smith2023", type: "article-journal", title: "Quantum error correction: below the surface code threshold",
  author: [{ family: "Smith", given: "Jane" }, { family: "Doe", given: "John" }],
  "container-title": "Nature", "container-title-short": "Nature", volume: "614", issue: "7949", page: "676-681",
  issued: { "date-parts": [[2023, 2, 22]] }, DOI: "10.1038/s41586-022-05434-1", URL: "https://www.nature.com/articles/s41586-022-05434-1",
};
const PREPRINT = {
  id: "vaswani2017", type: "article", title: "Attention is all you need",
  author: [{ family: "Vaswani", given: "Ashish" }, { family: "Shazeer", given: "Noam" }],
  publisher: "arXiv", number: "arXiv:1706.03762", issued: { "date-parts": [[2017]] },
  DOI: "10.48550/arXiv.1706.03762", URL: "https://arxiv.org/abs/1706.03762",
};
const BOOK = {
  id: "kahneman2011", type: "book", title: "Thinking, fast and slow", author: [{ family: "Kahneman", given: "Daniel" }],
  publisher: "Farrar, Straus and Giroux", issued: { "date-parts": [[2011]] }, ISBN: "978-0-374-27563-1",
};
const CHAPTER = {
  id: "lee2019", type: "chapter", title: "Topological phases of matter", author: [{ family: "Lee", given: "Patrick" }],
  "container-title": "Lectures on condensed matter", page: "101-140", publisher: "Springer", issued: { "date-parts": [[2019]] },
};
const THESIS = {
  id: "garcia2021", type: "thesis", title: "Superconducting qubits in the strong coupling regime", author: [{ family: "García", given: "María" }],
  genre: "PhD thesis", publisher: "Massachusetts Institute of Technology", issued: { "date-parts": [[2021]] },
};
const REPORT = {
  id: "who2020", type: "report", title: "Global tuberculosis report 2020", author: [{ literal: "World Health Organization" }],
  publisher: "World Health Organization", issued: { "date-parts": [[2020]] }, URL: "https://www.who.int/publications/i/item/9789240013131",
};
const RECORDS = [ARTICLE, PREPRINT, BOOK, CHAPTER, THESIS, REPORT];
const UNDATED = { id: "roe", type: "book", title: "Undated work", author: [{ family: "Roe", given: "Richard" }], publisher: "Example Press" };
// Its APA entry: "no date" in English, and in German (with its no-break space).
const UNDATED_EN = "Roe, R. (n.d.). Undated work. Example Press.";
const UNDATED_DE = "Roe, R. (o. J.). Undated work. Example Press.";

const entryText = async (style, items, opts = {}) => (await render({ items, style, output: "text", ...opts })).entries;

test("every bundled style renders the records as a bibliography and cites one with a page", async () => {
  for (const { id } of BUNDLED_STYLES) {
    for (const output of ["html", "text"]) {
      const result = await render({ items: RECORDS, style: id, output });
      assert.ok(result.output.length > 200, `${id} ${output}: a bibliography`);
      assert.equal(result.entries.length, RECORDS.length, `${id} ${output}`);
      result.entries.forEach((entry, i) => assert.ok(entry, `${id} ${output}: an entry for ${RECORDS[i].id}`));
    }
    const { html, text } = await renderPair({ items: [ARTICLE], style: id, mode: "citation", locators: { smith2023: "347" } });
    assert.ok(html && text, `${id}: a citation`);
  }
});

test("APA: the journal article with its issue and DOI, the list sorted, the entries in the order given", async () => {
  const result = await render({ items: RECORDS, style: "apa", output: "text" });
  assert.equal(result.entries[0], "Smith, J., & Doe, J. (2023). Quantum error correction: Below the surface code threshold. Nature, 614(7949), 676–681. https://doi.org/10.1038/s41586-022-05434-1");
  assert.match(result.entries[5], /^World Health Organization\. \(2020\)\. Global tuberculosis report 2020\./, "an organisation is one name");
  assert.match(result.output, /^García, M\. \(2021\)/, "the list is sorted, the entries are not");
  const html = (await render({ items: [ARTICLE], style: "apa" })).output;
  const body = new window.DOMParser().parseFromString(html, "text/html").querySelector("div.csl-bib-body");
  const indent = (side) => Number.parseFloat(body.style.getPropertyValue(side));
  assert.ok(indent("margin-left") > 0 && indent("text-indent") === -indent("margin-left"), "Zotero's hanging indent");
  assert.equal(body.style.getPropertyValue("line-height"), "2", "and its double spacing");
  assert.match(html, /<i>Nature<\/i>, <i>614<\/i>\(7949\), 676–681\. <a href="https:\/\/doi\.org\/10\.1038\/s41586-022-05434-1">/);
  const cite = await render({ items: [ARTICLE, PREPRINT], style: "apa", mode: "citation", output: "text", locators: { smith2023: "347" } });
  assert.equal(cite.output, "(Smith & Doe, 2023, p. 347; Vaswani & Shazeer, 2017)");
  assert.equal(cite.styleClass, "in-text");
});

test("IEEE numbers in the order given, with Zotero's label column", async () => {
  assert.match((await entryText("ieee", [PREPRINT, ARTICLE]))[0], /^\[1\] A\. Vaswani and N\. Shazeer/);
  const [article, preprint] = await entryText("ieee", [ARTICLE, PREPRINT]);
  assert.equal(article, "[1] J. Smith and J. Doe, “Quantum error correction: below the surface code threshold,” Nature, vol. 614, no. 7949, pp. 676–681, Feb. 2023, doi: 10.1038/s41586-022-05434-1.");
  assert.match(preprint, /^\[2\] /);
  const html = (await render({ items: [ARTICLE, PREPRINT], style: "ieee" })).output;
  const label = new window.DOMParser().parseFromString(html, "text/html").querySelector(".csl-entry > div.csl-left-margin");
  assert.equal(label.textContent, "[1]", "the number in a label column of its own");
  assert.equal(label.style.getPropertyValue("float"), "left");
  assert.ok(label.nextElementSibling.classList.contains("csl-right-inline"), "the entry beside it");
  const cite = await render({ items: [ARTICLE], style: "ieee", mode: "citation", output: "text", locators: { smith2023: "347" } });
  assert.equal(cite.output, "[1, p. 347]");
});

test("a note style cites in a note, with the page", async () => {
  const cite = await render({ items: [ARTICLE], style: "chicago-notes-bibliography", mode: "citation", output: "text", locators: { smith2023: "347" } });
  assert.equal(cite.styleClass, "note");
  assert.equal(cite.output, "Jane Smith and John Doe, “Quantum Error Correction: Below the Surface Code Threshold,” Nature 614, no. 7949 (2023): 347, https://doi.org/10.1038/s41586-022-05434-1.");
});

test("APS: volume in bold, first page", async () => {
  const [entry] = await entryText("american-physics-society", [ARTICLE]);
  assert.equal(entry, "[1] J. Smith and J. Doe, Quantum error correction: below the surface code threshold, Nature 614, 676 (2023).");
  assert.match((await render({ items: [ARTICLE], style: "american-physics-society" })).output, /Nature <b>614<\/b>, 676 \(2023\)/);
});

test("a particle the server split off is demoted and sorted as in Zotero", async () => {
  // The oracle's 4989916-FPEAFHZP: "Frank van Harmelen" as the server's CSL
  // item (and Zotero's itemToCSLJSON) gives it, its particle apart.
  const vanHarmelen = {
    id: "harmelen2020", type: "paper-conference", title: "Hybrid Intelligence: AI Systems That Collaborate with People Instead of Replacing Them",
    author: [{ family: "Harmelen", given: "Frank", "non-dropping-particle": "van" }], publisher: "SCITEPRESS", issued: { "date-parts": [[2020]] },
  };
  const [chicago] = await entryText("chicago-author-date", [vanHarmelen]);
  assert.match(chicago, /^Harmelen, Frank van\. 2020\. “Hybrid Intelligence: AI Systems That Collaborate with People Instead of Replacing Them\.”/);
  // APSA prints the particle first and sorts the name under H, before Smith.
  const apsa = (await render({ items: [ARTICLE, vanHarmelen], style: "american-political-science-association", output: "text" })).output;
  assert.match(apsa, /^van Harmelen, Frank\. 2020\./);
  assert.ok(apsa.indexOf("van Harmelen") < apsa.indexOf("Smith, Jane"));
});

test("a journal article with pages cites no URL, on the renderer's copy only", async () => {
  const paged = { ...ARTICLE, DOI: undefined, accessed: { "date-parts": [[2024, 1, 2]] } };
  const [withPages] = await entryText("apa", [paged]);
  assert.doesNotMatch(withPages, /nature\.com|Retrieved/);
  assert.equal(paged.URL, ARTICLE.URL, "the item given keeps its URL");
  const [withoutPages] = await entryText("apa", [{ ...paged, page: undefined }]);
  assert.match(withoutPages, /https:\/\/www\.nature\.com\/articles\/s41586-022-05434-1/);
});

test("the HTML keeps citeproc's markup and links to http(s) only", async () => {
  const sneaky = { ...REPORT, URL: 'https://example.org/a" onmouseover="alert(1)' };
  const html = (await render({ items: [sneaky], style: "apa" })).output;
  const doc = new window.DOMParser().parseFromString(html, "text/html");
  const attributes = [...doc.body.querySelectorAll("*")].flatMap((el) => [...el.attributes].map((a) => a.name));
  assert.deepEqual([...new Set(attributes)].sort(), ["class", "href", "style"], "the URL stays inside its href");
  assert.equal(doc.querySelector("a").getAttribute("href"), "https://example.org/a%22 onmouseover=%22alert(1)");
  // a DOI becomes a link too, whose address keeps its quotes inside it
  const doi = { ...ARTICLE, URL: undefined, DOI: '10.1/x" class="ctxMenu" style="position:fixed' };
  for (const style of ["apa", "ieee"]) {
    const link = new window.DOMParser().parseFromString((await render({ items: [doi], style })).output, "text/html").querySelector("a");
    assert.deepEqual([...link.attributes].map((a) => a.name), ["href"], style);
    assert.match(link.getAttribute("href"), /^https:\/\/doi\.org\/10\.1\/x%22/, style);
  }
  const clean = sanitizeHtml('<a href="javascript:alert(1)">x</a><img src="x" onerror="alert(2)"><script>alert(3)</script>'
    + '<span style="background:url(https://evil.test/)">y</span><span style="font-variant:small-caps;">z</span><sup>2</sup>');
  assert.doesNotMatch(clean, /javascript|<img|onerror|script|evil/);
  assert.equal(sanitizeHtml('<a href="https://a.test/" class="x" style="color:red">a</a><span class="ctxMenu csl-entry">b</span>'),
    '<a href="https://a.test/">a</a><span>b</span>', "a link keeps its href alone, a class is citeproc's own");
  assert.match(clean, /<a>x<\/a>/);
  assert.match(clean, /<span style="font-variant:small-caps;">z<\/span><sup>2<\/sup>/);
});

test("RTF is a document of Zotero's wrapper, per entry too", async () => {
  const apa = await render({ items: [ARTICLE, BOOK], style: "apa", output: "rtf" });
  assert.match(apa.output, /^\{\\rtf \\li720 \\fi-720 \\sl480 \\slmult1 \\sa0 /);
  assert.match(apa.output, /\}$/);
  assert.match(apa.entries[0], /^\{\\rtf \\li720 .*Smith, J\., & Doe, J\. \(2023\)/s);
  const ieee = await render({ items: [ARTICLE], style: "ieee", output: "rtf" });
  assert.match(ieee.output, /^\{\\rtf \\tx384 \\li384 \\fi-384 /);
});

test("a dependent style renders with its bundled parent in its own language", async () => {
  const dependent = (locale) => `<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0"${locale ? ` default-locale="${locale}"` : ""}>
  <info><title>APA auf Deutsch</title><id>http://www.zotero.org/styles/apa-de</id>
    <link href="http://www.zotero.org/styles/apa-de" rel="self"/>
    <link href="http://www.zotero.org/styles/apa" rel="independent-parent"/></info>
</style>`;
  // As the server's headers name the parent and the dependent's locale.
  ADDED["apa-de"] = { xml: dependent(""), parent: "apa", locale: "de-DE" };
  assert.equal((await entryText("apa-de", [UNDATED], { locale: "fr-FR" }))[0], UNDATED_DE);
  // A parent the server sent in its place renders as it is.
  ADDED["ieee-copy"] = { xml: read("styles/ieee.csl"), parent: "", locale: "" };
  assert.match((await entryText("ieee-copy", [PREPRINT, ARTICLE]))[1], /^\[2\] J\. Smith/);
  await assert.rejects(render({ items: [BOOK], style: "gone-style" }), { status: 404 });
});

test("a language without a locale of its own falls back to its primary dialect, then en-US", async () => {
  assert.equal((await entryText("apa", [UNDATED], { locale: "de-AT" }))[0], UNDATED_DE);
  assert.equal((await entryText("apa", [UNDATED], { locale: "de-LU" }))[0], UNDATED_DE);
  assert.equal((await entryText("apa", [UNDATED], { locale: "xx-YY" }))[0], UNDATED_EN);
  // A style's own default-locale wins over the chosen language.
  assert.match((await entryText("nature", [ARTICLE], { locale: "de-DE" }))[0], /Smith, J\. & Doe, J\./);
});

test("the files are the ones their READMEs name", () => {
  const sha = (text) => createHash("sha256").update(text.replace(/\r\n/g, "\n"), "utf8").digest("hex");
  for (const [sub, pattern] of [["styles/", /\.csl$/], ["locales/", /^locales.*\.(xml|json)$/]]) {
    const readme = read(`${sub}README.md`);
    const rows = [...readme.matchAll(/^\| ([\w.-]+) \| (\d+) \| `([0-9a-f]{64})` \|$/gm)];
    assert.deepEqual(rows.map((row) => row[1]).sort(), files(sub, pattern).sort(), `${sub}README.md lists every file`);
    for (const [, name, size, digest] of rows) {
      const text = read(sub + name).replace(/\r\n/g, "\n");
      assert.equal(Buffer.byteLength(text, "utf8"), Number(size), name);
      assert.equal(sha(text), digest, name);
    }
    assert.match(read(`${sub}LICENSE`), /Attribution-ShareAlike 3\.0 Unported/);
    assert.match(readme, /https:\/\/citationstyles\.org\//);
  }
});

test("the bundled list is the shared one and says what each file's <info> says", () => {
  const shared = JSON.parse(fs.readFileSync(new URL("../../tests/shared/citationstyles.json", import.meta.url), "utf8"));
  assert.deepEqual(BUNDLED_STYLES.map((style) => ({ ...style })), shared);
  assert.deepEqual(shared.map((style) => `${style.id}.csl`).sort(), files("styles/", /\.csl$/).sort());
  for (const style of shared) {
    const doc = new window.DOMParser().parseFromString(read(`styles/${style.id}.csl`), "application/xml");
    const info = doc.querySelector("info");
    assert.equal(info.querySelector("id").textContent, `http://www.zotero.org/styles/${style.id}`);
    assert.equal(info.querySelector("title").textContent, style.title, style.id);
    assert.equal(info.querySelector("title-short")?.textContent || "", style.short, style.id);
    assert.equal(info.querySelector("category[citation-format]").getAttribute("citation-format"), style.kind, style.id);
  }
});

test("key commands cite each key once and keep every key inside its command", () => {
  assert.equal(keyCommand("latex", ["smith2023", "doe99", "smith2023"]), "\\cite{smith2023,doe99}");
  assert.equal(keyCommand("latex", ["a,b}", ""]), "\\cite{ab}");
  assert.equal(keyCommand("pandoc", ["smith2023", "a:b-c", "doe99.", "_x", "-x", "a..b"]), "[@smith2023; @a:b-c; @{doe99.}; @_x; @{-x}; @{a..b}]");
  assert.equal(keyCommand("typst", ["smith2023", "a:b.c-d", "doe:", 'x"y\\z', "@x"]),
    '@smith2023 @a:b.c-d #cite(label("doe:")) #cite(label("x\\"y\\\\z")) #cite(label("@x"))');
  assert.equal(keyCommand("pandoc", []), "");
});

test("Copy as offers every Quick Copy format but the style, the files apart from the key commands", () => {
  const groups = (except) => copyAsGroups(except).map((group) => [group.label, group.formats]);
  assert.deepEqual(groups(), [["Formats", ["bibtex", "biblatex", "ris", "csljson"]], ["Cite keys", ["latex", "pandoc", "typst"]]]);
  assert.deepEqual(groups(["bibtex"]), [["Formats", ["biblatex", "ris", "csljson"]], ["Cite keys", ["latex", "pandoc", "typst"]]],
    "the share popover's, whose BibTeX has its own box");
  assert.deepEqual(groups(["latex", "pandoc", "typst"]), [["Formats", ["bibtex", "biblatex", "ris", "csljson"]]], "an empty group is left out");
});

test("a quote cites its page label, else the printed page, else the PDF page", () => {
  assert.equal(pageLocator({ pageNumber: 3, labels: ["i", "ii", "iii", "1"], metaPages: "676-681", pageCount: 4 }), "iii");
  assert.equal(pageLocator({ pageNumber: 3, labels: null, metaPages: "676–681", pageCount: 6 }), "678");
  assert.equal(pageLocator({ pageNumber: 3, metaPages: "676-681", pageCount: 6 }), "678", "a hyphen's range");
  assert.equal(pageLocator({ pageNumber: 3, metaPages: "676-681", pageCount: 8 }), "3", "pages that are not the PDF's length");
  assert.equal(pageLocator({ pageNumber: 3, labels: ["", "", ""], metaPages: "e1234", pageCount: 3 }), "3");
  assert.equal(pageLocator({ pageNumber: null }), null, "no page");
  assert.equal(pageLocator({}), null);
});

test("the citation preferences keep another server's style and drop what no style or locale can be", () => {
  assert.deepEqual(readProfile({ citationStyle: "nature-physics", citationLocale: "sr-Latn-RS", quickCopyFormat: "pandoc" }),
    { citationStyle: "nature-physics", citationLocale: "sr-Latn-RS", quickCopyFormat: "pandoc" });
  assert.deepEqual(readProfile({ citationStyle: "x".repeat(201), citationLocale: "<b>", quickCopyFormat: "docx" }), {});
});
