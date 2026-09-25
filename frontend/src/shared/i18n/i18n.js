// Interface text in the user's language (docs/dev/i18n.md).
//
// The English source text is the key: `t("Copy link")` looks the sentence
// up in the active catalog (locales/<locale>.json) and falls back to the
// sentence itself, so an untranslated string is never a broken one. The
// catalogs are plain JSON, one per language, kept complete by
// `npm run i18n` (tools/i18n.mjs) and tests/i18n.test.mjs.
//
// Rules for a call site:
// - The first argument is one string literal, never a variable or a
//   template, so the tool can find it. A finished sentence from elsewhere
//   (a server error) may go through `t(message)` — it translates when the
//   catalog knows it and is not extracted.
// - Placeholders are `{name}`, filled from the second argument. An argument
//   that is a React element is spliced in as an element.
// - Counts use `tn("{n} page", "{n} pages", n)`: the singular is the key.
// - A string in a module-level table (a menu catalog, a tour) is marked
//   `T("…")` where it is declared and passed through `t()` where it is
//   rendered, so it is extracted but translated at render time.
//
// The active locale is a tiny external store: main.jsx renders the app
// under `key={locale}`, so changing the language remounts it with every
// string re-read — no component needs to subscribe.
// A default import: the module also loads under plain node (the tests import
// modules that translate their tables), where React is CommonJS.
import React from "react";
import { TAGS, resolveLocale } from "./locales.js";

export { LANGUAGES, LOCALES, resolveLocale } from "./locales.js";

// Vite turns the glob call into lazy imports (the property itself never
// exists at runtime, so it cannot be tested for); under plain node the call
// throws and the catalogs stay empty.
let catalogs = {};
try { catalogs = import.meta.glob("./locales/*.json", { import: "default" }); } catch { /* node */ }

let locale = "en";
let catalog = {};
const listeners = new Set();

// Fetches a catalog (a lazy chunk; English needs none).
export async function loadLocale(code) {
  if (code === "en") return {};
  const load = catalogs[`./locales/${code}.json`];
  return load ? await load() : {};
}

// Makes `code` the active locale, with its catalog already loaded via
// loadLocale. Notifies subscribers only on a change.
export function setLocale(code, loaded) {
  if (code === locale) return;
  locale = code;
  catalog = loaded || {};
  if (typeof document !== "undefined") document.documentElement.lang = TAGS[code] || code;
  for (const fn of listeners) fn();
}

// Load and apply in one step (App's effect on the preference).
export async function applyLanguage(pref) {
  const code = resolveLocale(pref);
  if (code === locale) return;
  setLocale(code, await loadLocale(code));
}

export const getLocale = () => locale;
export const localeTag = () => TAGS[locale] || locale;
const subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
export const useLocale = () => React.useSyncExternalStore(subscribe, getLocale);

// Fills `{name}` placeholders. With an element among the values the result
// is an array of strings and elements (React renders it as children).
function fill(text, args) {
  if (!args) return text;
  const parts = text.split(/\{(\w+)\}/g);
  if (parts.length === 1) return text;
  let elements = false;
  const out = parts.map((part, i) => {
    if (i % 2 === 0) return part;
    const value = args[part];
    if (value === undefined) return `{${part}}`;
    if (value !== null && typeof value === "object") { elements = true; return React.createElement(React.Fragment, { key: i }, value); }
    return String(value);
  });
  return elements ? out.filter((part) => part !== "") : out.join("");
}

// The translation of an English sentence, placeholders filled.
export function t(text, args) {
  const found = catalog[text];
  return fill(typeof found === "string" && found ? found : text, args);
}

// A count: the singular form is the key; a catalog entry is one string
// (Chinese) or an object keyed by the Intl plural category ("one",
// "other", …) for languages that inflect. `{n}` is the count.
export function tn(one, other, n, args) {
  const found = catalog[one];
  const values = { n, ...args };
  if (found && typeof found === "object") {
    const category = new Intl.PluralRules(localeTag()).select(n);
    return fill(found[category] || found.other || one, values);
  }
  if (typeof found === "string" && found) return fill(found, values);
  return fill(n === 1 ? one : other, values);
}

// Marks a string in a static table for extraction; translated by `t()`
// where it is rendered.
export const T = (text) => text;

// Dates and numbers in the interface language, not the browser's.
export const fmtDate = (value, options) => new Intl.DateTimeFormat(localeTag(), options).format(value instanceof Date ? value : new Date(value));
export const fmtNumber = (value, options) => new Intl.NumberFormat(localeTag(), options).format(value);
