// Dump each sampled page's pdf.js text content to
// $OCR_STUDY_DIR/stage1/words/<file>-p<N>.json:
//   words  - the non-space items in stream order (index into tc.items, str,
//            transform, width, height, fontName, hasEOL)
//   tokens - the words of the selection string (tokens.mjs), with the item
//            char ranges they come from; truth/*.json is indexed by these
//   tc     - the whole TextContent {items, styles, lang}, so eval.mjs can feed
//            candidates without loading pdf.js
//   vp     - the scale-1 viewport transform and size; view, rotate
// usage: node dump_words.mjs [--file id]
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { FILES, PAGES, PDFJS, STUDY } from "./files.mjs";
import { tokenize } from "./tokens.mjs";

const { getDocument } = await import(pathToFileURL(PDFJS).href);
const pages = JSON.parse(readFileSync(PAGES, "utf8"));
const only = process.argv.includes("--file") ? process.argv[process.argv.indexOf("--file") + 1] : null;
mkdirSync(`${STUDY}/words`, { recursive: true });
const r = (v) => Math.round(v * 1e4) / 1e4;
for (const [id, spec] of Object.entries(pages.files)) {
  if (only && id !== only) continue;
  const doc = await getDocument({ data: new Uint8Array(readFileSync(FILES[id])), useSystemFonts: true, verbosity: 0 }).promise;
  for (const pn of Object.keys(spec.pages).map(Number).sort((a, b) => a - b)) {
    const page = await doc.getPage(pn);
    const vp = page.getViewport({ scale: 1 });
    const tc = await page.getTextContent();
    const words = [];
    tc.items.forEach((it, i) => {
      if (typeof it.str !== "string" || !it.str.trim()) return;
      words.push({ i, str: it.str, transform: it.transform.map(r), width: r(it.width), height: r(it.height), fontName: it.fontName, hasEOL: !!it.hasEOL });
    });
    const { tokens } = tokenize(tc.items);
    const out = {
      file: id, page: pn, path: FILES[id], kind: spec.pages[pn], view: page.view, rotate: page.rotate,
      vp: { transform: vp.transform, width: vp.width, height: vp.height },
      words, tokens, tc: { items: tc.items, styles: tc.styles, lang: tc.lang ?? null },
    };
    writeFileSync(`${STUDY}/words/${id}-p${pn}.json`, JSON.stringify(out));
    console.log(`${id}-p${pn}: ${tc.items.length} items, ${words.length} word items, ${tokens.length} tokens`);
    page.cleanup();
  }
  await doc.destroy();
}
