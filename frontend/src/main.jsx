import React from "react";
import ReactDOM from "react-dom/client";
import { loadLocale, resolveLocale, setLocale } from "./shared/i18n/i18n.js";
import "./shared/styles/app.css";
import "./library/library.css";
import "./settings/settings.css";

// iPadOS defaults to "Request Desktop Website", where Safari reports
// (hover: hover) and (pointer: fine) exactly like a Mac — so no media query
// can spot a touch device there. maxTouchPoints stays truthful in that mode,
// so hover-gated UI keys off this attribute as well as the media query.
if (navigator.maxTouchPoints > 0) document.documentElement.dataset.touch = "1";

// The interface language (docs/dev/i18n.md): its catalog is loaded before the
// app's modules are, so text in module-level constants (option lists, menu
// tables) is translated when those modules first evaluate. A later change of
// language reloads the page (app/App.jsx) for the same reason.
let stored = "system";
try { stored = localStorage.getItem("gamma-language") || "system"; } catch {}
const locale = resolveLocale(stored);
loadLocale(locale)
  .catch(() => ({}))
  .then((catalog) => {
    setLocale(locale, catalog);
    return import("./app/App.jsx");
  })
  .then(({ default: App }) => {
    ReactDOM.createRoot(document.getElementById("root")).render(
      <React.StrictMode>
        <App />
      </React.StrictMode>
    );
  });
