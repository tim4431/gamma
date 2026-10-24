// The themes and how one goes on the page. A module of its own, with no
// imports, so the page's first script (main.jsx) can put the account's
// theme on before the app's modules load; prefDefs.js declares the
// preference from these lists.

export const THEMES = ["system", "light", "dark", "gamma-light", "gamma-dark", "sepia", "solarized", "gray"];
// The themes on the dark colour scheme; every other pinned theme is light.
// The root carries the scheme as data-scheme beside data-theme: tokens.css
// derives each theme's colours with that scheme's recipe, and the
// scheme-wide rules in app.css key on it. index.html's pre-paint script and
// the desktop shell repeat the list (tests/themes.test.mjs keeps them equal).
export const DARK_THEMES = ["dark", "gamma-dark"];
export const themeScheme = (theme) => (DARK_THEMES.includes(theme) ? "dark" : "light");

// The theme the page shows for a preference value: a pinned one as it is,
// "system" (or anything unknown) the OS scheme's.
export function resolveTheme(theme) {
  if (THEMES.includes(theme) && theme !== "system") return theme;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// Puts a resolved theme on the root. Every colour changes in one frame: the
// root carries .themeSwitch while that frame is drawn, which turns every
// transition off (motion.css), so the colours of things that transition
// them (a button, a row, a panel) do not ease from the old theme's while
// the rest has changed. The class goes as the frame after that one starts,
// when the new colours are drawn and nothing is left to ease.
export function applyTheme(theme) {
  const root = document.documentElement;
  const scheme = themeScheme(theme);
  if (root.dataset.theme === theme && root.dataset.scheme === scheme) return;
  root.classList.add("themeSwitch");
  root.setAttribute("data-scheme", scheme);
  root.setAttribute("data-theme", theme);
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("themeSwitch")));
}
