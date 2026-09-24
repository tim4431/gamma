import React from "react";
import ReactDOM from "react-dom/client";
import App from "./app/App.jsx";
import { loadLocale, resolveLocale, setLocale, useLocale } from "./shared/i18n/i18n.js";
import "./shared/styles/app.css";
import "./library/library.css";
import "./settings/settings.css";

// iPadOS defaults to "Request Desktop Website", where Safari reports
// (hover: hover) and (pointer: fine) exactly like a Mac — so no media query
// can spot a touch device there. maxTouchPoints stays truthful in that mode,
// so hover-gated UI keys off this attribute as well as the media query.
if (navigator.maxTouchPoints > 0) document.documentElement.dataset.touch = "1";

// The interface language (docs/dev/i18n.md): its catalog is loaded before
// the first render so nothing flashes English, and a later change (the
// Settings row, the profile sync) remounts the app under the new locale.
function Root() {
  const locale = useLocale();
  return <App key={locale} />;
}

let stored = "system";
try { stored = localStorage.getItem("gamma-language") || "system"; } catch {}
const locale = resolveLocale(stored);
loadLocale(locale).catch(() => ({})).then((catalog) => {
  setLocale(locale, catalog);
  ReactDOM.createRoot(document.getElementById("root")).render(
    <React.StrictMode>
      <Root />
    </React.StrictMode>
  );
});
