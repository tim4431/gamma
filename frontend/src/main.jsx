import React from "react";
import ReactDOM from "react-dom/client";
import { loadLocale, resolveLocale, setLocale } from "./shared/i18n/i18n.js";
// The interface font (--font-sans in tokens.css), bundled so it needs no
// third-party request: one variable woff2 per unicode subset, each fetched
// only when text in its range (or, for the italic set, italic text) shows.
import "@fontsource-variable/inter/wght.css";
import "@fontsource-variable/inter/wght-italic.css";
import "./shared/styles/app.css";
import "./library/library.css";
import "./settings/settings.css";
// How things move, after the stylesheets whose surfaces it animates.
import "./shared/styles/motion.css";
import { holdMathForFonts, warmKatexFonts } from "./shared/lib/katexFonts.js";
import { adoptAccountLook, languageSeen, startBootReads } from "./app/boot.js";

// The server's config and the session are asked for now, beside the code
// (app/boot.js): the app takes their answers as it mounts.
const boot = startBootReads();

// The formulas' fonts start loading now, beside the app's code, and a
// formula whose faces are still loading stays hidden until they are in, so
// a formula (a comment's thread, the LaTeX preview) always draws whole
// rather than piece by piece as its faces arrive (shared/lib/katexFonts.js).
warmKatexFonts();
holdMathForFonts();

// iPadOS defaults to "Request Desktop Website", where Safari reports
// (hover: hover) and (pointer: fine) exactly like a Mac — so no media query
// can spot a touch device there. maxTouchPoints stays truthful in that mode,
// so hover-gated UI keys off this attribute as well as the media query.
if (navigator.maxTouchPoints > 0) document.documentElement.dataset.touch = "1";

// The interface language (docs/dev/i18n.md): its catalog is loaded before the
// app's modules are, so text in module-level constants (option lists, menu
// tables) is translated when those modules first evaluate. A later change of
// language reloads the page (app/App.jsx) for the same reason.
//
// The session's answer carries the account's theme and language, which go
// on as it lands. A stored language this browser last took from an account
// is most likely still the account's, so the app's modules load at once;
// any other (a browser that has never seen the account) waits for the
// session, so the app comes up in the account's language instead of
// reloading into it once it has shown. (A first session that still
// disagrees reloads before the library shows: App.jsx checkSession.)
let stored = "system";
try { stored = localStorage.getItem("gamma-language") || "system"; } catch {}
const look = (boot.session || Promise.resolve(null)).then((session) => adoptAccountLook(session?.profile).language, () => null);
Promise.all([loadLocale(resolveLocale(stored)).catch(() => ({})), languageSeen(stored) ? null : look])
  .then(async ([catalog, language]) => {
    let locale = resolveLocale(stored);
    if (language && resolveLocale(language) !== locale) {
      locale = resolveLocale(language);
      catalog = await loadLocale(locale).catch(() => ({}));
    }
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
