// KaTeX's fonts (katex.min.css, imported by shared/styles/app.css) are
// declared with font-display: block: a glyph stays invisible until its font
// has loaded, and the browser fetches each face only when a formula on screen
// first needs it. A formula that is the first in its view (a comment's
// popover, the LaTeX preview, the chat) would therefore appear piece by
// piece: its box, fraction rules and radicals (CSS and SVG) at once, then its
// glyphs as the faces arrive one after another, over about 100 ms even from
// the browser's cache. Two measures, both installed by main.jsx as soon as
// the styles are in (docs/dev/latex_editing.md):
//
// - warmKatexFonts asks for the faces most formulas use once, at startup, so
//   they load beside the app's own code and are in before the first formula
//   shows. About 130 KB of woff2, once per browser: the files are cached.
// - holdMathForFonts keeps a formula whose faces are still loading invisible
//   until they are in, so whatever the timing (a face outside the warmed
//   set, a thread opened at startup over a slow link) it draws whole.
const FACES = [
  "16px KaTeX_Main", "italic 16px KaTeX_Main", "bold 16px KaTeX_Main",
  "italic 16px KaTeX_Math",
  "16px KaTeX_Size1", "16px KaTeX_Size2", "16px KaTeX_Size3", "16px KaTeX_Size4",
  "16px KaTeX_AMS",
];

let loading = null;

// The faces' loads, started once; a face that fails is left for the first
// formula that needs it to fetch.
export function warmKatexFonts() {
  if (loading || typeof document === "undefined" || !document.fonts?.load) return loading;
  loading = Promise.all(FACES.map((face) => document.fonts.load(face).catch(() => null)));
  return loading;
}

// How long a formula is held at most: font-display: block's own block
// period, after which the browser draws a face's text in a fallback anyway.
const HOLD_MS = 3000;

// Whether a KaTeX face is loading.
const facesLoading = () => {
  for (const face of document.fonts) if (face.status === "loading" && face.family.startsWith("KaTeX_")) return true;
  return false;
};

// A formula added to the page (a `.katex`, rendered by rehype-katex in a
// note or a comment, or by katex itself in the editor's in-place widget and
// the preview) is caught in a mutation callback: a microtask after the
// change, before the frame paints. A layout forced there makes the browser
// ask for the faces the formula needs, whose status turns "loading" at once
// (Chromium, Firefox and WebKit set it as the load starts). While any KaTeX
// face loads, the new formulas are marked `data-fonts="loading"`, which
// app.css hides; the mark comes off once no face is loading (document.fonts
// .ready, checked again each frame in case it settles early), or after
// HOLD_MS. React and CodeMirror leave the attribute alone: neither set it.
export function holdMathForFonts() {
  if (typeof MutationObserver === "undefined" || typeof document === "undefined" || !document.fonts || !document.body) return;
  let held = [];
  let releasing = false;
  const release = () => {
    for (const el of held) el.removeAttribute("data-fonts");
    held = [];
    releasing = false;
  };
  const hold = (formulas) => {
    for (const el of formulas) el.setAttribute("data-fonts", "loading");
    held.push(...formulas);
    if (releasing) return;
    releasing = true;
    const deadline = Date.now() + HOLD_MS;
    const settle = () => {
      if (facesLoading() && Date.now() < deadline) requestAnimationFrame(settle);
      else release();
    };
    document.fonts.ready.then(settle, settle);
  };
  const observer = new MutationObserver((records) => {
    const formulas = [];
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.classList.contains("katex")) formulas.push(node);
        else if (node.firstElementChild) formulas.push(...node.querySelectorAll(".katex"));
      }
    }
    if (!formulas.length) return;
    formulas[0].getBoundingClientRect(); // the layout that starts the loads
    if (facesLoading()) hold(formulas);
  });
  observer.observe(document.body, { childList: true, subtree: true });
}
