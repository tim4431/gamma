// KaTeX's fonts (katex.min.css, imported by shared/styles/app.css) are
// declared with font-display: block: a glyph stays invisible until its font
// has loaded, and the browser fetches each face only when a formula on screen
// first needs it. A formula that is the first in its view (a comment's
// popover, the LaTeX preview, the chat) therefore appears piece by piece, as
// its faces arrive one after another. warmKatexFonts asks for the faces most
// formulas use once, while the browser is idle after startup, so the first
// formula draws whole. About 130 KB of woff2, once per browser: the files
// are cached.
const FACES = [
  "16px KaTeX_Main", "italic 16px KaTeX_Main", "bold 16px KaTeX_Main",
  "italic 16px KaTeX_Math",
  "16px KaTeX_Size1", "16px KaTeX_Size2", "16px KaTeX_Size3", "16px KaTeX_Size4",
  "16px KaTeX_AMS",
];

let warmed = false;

// The faces' loads, started once; a face that fails is left for the first
// formula that needs it to fetch, as before.
export function warmKatexFonts() {
  if (warmed || typeof document === "undefined" || !document.fonts?.load) return;
  warmed = true;
  for (const face of FACES) document.fonts.load(face).catch(() => {});
}

// warmKatexFonts once the page has drawn and the browser is idle.
export function warmKatexFontsWhenIdle(delay = 800) {
  if (typeof window === "undefined") return;
  setTimeout(() => {
    if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(warmKatexFonts, { timeout: 3000 });
    else warmKatexFonts();
  }, delay);
}
