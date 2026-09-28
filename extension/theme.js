// The theme attributes Gamma's tokens (tokens.css, a copy of the app's)
// derive every colour from. The app's pinned theme isn't knowable here, so
// Light or Dark follows the OS, live. A classic script in the page's head,
// before the stylesheets, so the first frame already has its colours
// (docs/dev/extension.md).
(function () {
  const root = document.documentElement;
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = () => {
    const theme = mq.matches ? "dark" : "light";
    root.setAttribute("data-scheme", theme);
    root.setAttribute("data-theme", theme);
  };
  apply();
  mq.addEventListener("change", apply);
})();
