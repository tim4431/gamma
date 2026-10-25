// Formula detection for the PDF translated view: which runs of a line are
// math, merged into spans the model sees as {v1}, {v2}… and the overlay draws
// back as crops of the page (pdf/PdfViewer.jsx TransMath). Pure, like
// pdf/pdfTranslate.js, which calls it per line of a block.
// docs/dev/translation.md "Formulas" describes the rules.
//
// pdf.js extracts math as flattened text — a subscript inline ("anφn"), CMEX's
// Σ as "P", fraction bars and radicals gone — so a formula sent as text comes
// back as something plausible and wrong. A run is math when:
//   - its font is a math font (by the PDF's own font name, pdf/pdfFonts.js),
//     or a typewriter/code font;
//   - most of its characters are math symbols (Greek, operators, arrows,
//     math alphanumerics, private-use glyphs of symbol fonts);
//   - it is a sub- or superscript (a citation's superscript number too);
//   - it is a one- or two-letter variable in an italic font.
// Runs of digits, operators and brackets between two math runs belong to
// the formula ("V(r) = C6/r6" is CMMI, CMR, CMMI, CMR…).

// Math fonts by family, matched on the name without its subset tag:
// Computer Modern and AMS (CMMI, CMSY, CMEX, MSAM, MSBM, the Euler and
// script fonts), Latin Modern and the OpenType math fonts (by family: a text
// family named "…Math", like Nature's GlosaMath, is none), the Times-era
// families (txfonts/newtx, pxfonts, MathTime, Mathematica, MathType's Euclid
// math fonts, Symbol, MT Extra), and KaTeX's.
const MATH_FONT = new RegExp([
  "^CM(MI|SY|EX|BSY|MIB)\\d*", "^MS(AM|BM)", "^EU(FM|FB|SM|SB|RM|RB|EX)", "^RSFS", "^WASY", "^STMARY",
  "^(N?TX|PX|NEWTX|ZX)(MI|SY|EX|S?YS|SYA|SYB|EXS)", "^MT(MI|SY|EX|GU|2)", "^MT-?EXTRA", "^MATHEMATICA",
  "^EUCLID ?(SYMBOL|MATH|EXTRA|FRAKTUR)", "^SYMBOL", "^LUCIDA.*MATH", "^STIX.*(MATH|SIZE|NONUNI|INTEGRAL|VARIANTS)",
  "^BG-?MATH", "^KATEX_(MATH|AMS|SIZE|CALIGRAPHIC|FRAKTUR|SCRIPT)", "^CAMBRIA ?MATH",
  "^(LM|LATIN ?MODERN|TEX ?GYRE\\w*|XITS|ASANA|NEW ?CM|FIRA|LIBERTINUS|DEJAVU|GARAMOND|STIX\\w*)-? ?MATH",
].join("|"), "i");
// Typewriter and code fonts, by family: identifiers and code stay as they
// are ("…Unicode" or "Monotype" text families are none).
const CODE_FONT = /^(CMTT|SFTT|LMMONO|LMTT|COURIER|CONSOLAS|MENLO|MONACO|INCONSOLATA|SOURCE ?CODE|FIRA ?(CODE|MONO)|CASCADIA|JETBRAINS ?MONO|LUCIDA ?(CONSOLE|SANS ?TYPEWRITER)|ANDALE ?MONO|NIMBUS ?MONO|FREE ?MONO|LIBERATION ?MONO|DEJAVU ?SANS ?MONO|ROBOTO ?MONO|UBUNTU ?MONO|NOTO ?SANS ?MONO|SF ?MONO|PT ?MONO)/i;
const ITALIC_FONT = /ital|oblique|^CMTI|^CMMI/i;
// "MinionPro-It", "Times-I", AIP's "AdvTT….I" / "….BI": case matters.
const ITALIC_SUFFIX = /-?It$|-I$|\.B?I$/;
const isItalic = (f) => ITALIC_FONT.test(f) || ITALIC_SUFFIX.test(f);
// Words an italic run spells without being a variable: emphasis, "et al.".
const ITALIC_WORDS = new Set(["a", "I", "an", "as", "at", "be", "by", "do", "et", "al", "go", "he", "if", "in", "is",
  "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we"]);
// Prose: two words of three letters or more in lower case. A run of prose is
// no formula whatever its font is called.
const PROSE_RUN = /\p{Ll}{3,}\S*\s+\S*\p{Ll}{3,}/u;

// Characters that only a formula writes — with the Latin-1 letters ð, Þ, þ
// and ¼, which AIP's symbol fonts map their ( ) + = to.
const MATH_CHAR = /[\u0370-\u03ff\u1f00-\u1fff\u2070-\u209f\u2100-\u214f\u2190-\u23ff\u27c0-\u27ff\u2900-\u2aff\ue000-\uf8ff\u00b1\u00d7\u00f7\u00bc\u00de\u00f0\u00fe\u2032-\u2037\u221e]|[\u{1d400}-\u{1d7ff}]/u;
// What may sit between two math runs and still be part of the formula.
const BRIDGE = /^[\s\d=+\-−–×·∙*/<>≤≥≈≃∼~()[\]{}|^_'′!.]+$/;
// How far apart (× line height) two pieces of one formula may sit: TeX's
// thick space around "=" is about half an em. Two formulas never meet
// without a word between them, so nothing wider joins.
const FORMULA_GAP = 0.65;
const ATTACH = /^[\d()[\]'′!]+$/;

function face(r) {
  return String(r.face || "").replace(/^[A-Z]{6}\+/, "");
}

function mathShare(s) {
  const chars = [...s.replace(/\s/g, "")];
  if (!chars.length) return 0;
  return chars.filter((c) => MATH_CHAR.test(c)).length / chars.length;
}

// Is this run (with its `script` flag from the line, pdf/pdfTranslate.js)
// part of a formula on its own?
export function isMathRun(r) {
  const s = r.str.trim();
  if (!s) return false;
  const f = face(r);
  if (f && CODE_FONT.test(f)) return true;
  if (f && MATH_FONT.test(f) && !PROSE_RUN.test(s)) return true;
  if (r.type3 && mathShare(s) > 0) return true;
  if (mathShare(s) >= 0.5) return true;
  if (r.script) return true;
  // A variable in an italic text font: one or two letters and maybe digits
  // or primes — not a word set in italics for emphasis.
  return !!f && isItalic(f) && /^[A-Za-z]{1,2}[\d'′]*$/.test(s) && !ITALIC_WORDS.has(s);
}

// A line's runs (left to right, with `spaced` set where the line's text puts
// a space before a run) → its parts: text, or a math span with the runs it
// covers. `h` is the line's body height.
export function lineParts(runs, h) {
  const math = runs.map(isMathRun);
  const gapBefore = (k) => (k ? runs[k].x - (runs[k - 1].x + runs[k - 1].w) : Infinity);
  // Bridges and attachments: digits, operators and brackets between two
  // formula runs (or glued to one) are the formula's.
  for (let k = 0; k < runs.length; k++) {
    if (math[k]) continue;
    const s = runs[k].str.trim();
    const prev = k > 0 && math[k - 1], next = k + 1 < runs.length && math[k + 1];
    const tight = (g) => g <= h * FORMULA_GAP;
    if (prev && next && s.length <= 8 && BRIDGE.test(s) && tight(gapBefore(k)) && tight(gapBefore(k + 1))) math[k] = true;
    else if ((prev && gapBefore(k) <= h * 0.12 || next && gapBefore(k + 1) <= h * 0.12) && ATTACH.test(s)) math[k] = true;
  }
  const parts = [];
  for (let k = 0; k < runs.length; k++) {
    const r = runs[k];
    const last = parts[parts.length - 1];
    const sep = r.spaced ? " " : "";
    if (math[k]) {
      // A run continues the span before it unless more than a formula's own
      // spacing stands between them.
      if (last?.kind === "math" && gapBefore(k) <= h * FORMULA_GAP) {
        last.runs.push(r);
        last.str += sep + r.str;
        continue;
      }
      if (last && sep) last.str += sep;
      parts.push({ kind: "math", runs: [r], str: r.str });
    } else if (last?.kind === "text") {
      last.str += sep + r.str;
    } else {
      parts.push({ kind: "text", str: (last ? sep : "") + r.str });
    }
  }
  return parts;
}

// A math span's box (scale-1 viewport units) and baseline: every glyph of
// its runs, with the font's ascent and descent.
export function spanBox(runs, base) {
  return {
    x1: Math.min(...runs.map((r) => r.x)),
    x2: Math.max(...runs.map((r) => r.x + r.w)),
    y1: Math.min(...runs.map((r) => r.y - r.h * r.asc)),
    y2: Math.max(...runs.map((r) => r.y - r.h * r.desc)),
    base,
  };
}

// Citation markers and URLs in a text part: tokens too, but drawn as their
// own text (they reflow; a crop of a URL would not). A URL stops at the
// markers pdf/pdfTranslate.js blockSource puts around a formula after it.
export const TEXT_TOKEN = /\[\d+(?:[a-z])?(?:\s*[,–-]\s*\d+[a-z]?)*\]|https?:\/\/[^\s)\]\u0001\u0002]+[^\s)\].,;:\u0001\u0002]/g;

export function textTokens(s) {
  const out = [];
  let at = 0;
  for (const m of s.matchAll(TEXT_TOKEN)) {
    if (m.index > at) out.push({ kind: "text", str: s.slice(at, m.index) });
    out.push({ kind: "literal", str: m[0] });
    at = m.index + m[0].length;
  }
  if (at < s.length) out.push({ kind: "text", str: s.slice(at) });
  return out;
}
