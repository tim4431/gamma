// The interface languages Gamma has catalogs for (docs/dev/i18n.md). Pure
// (no React, no Vite), so prefDefs.js and node tests can read it.

// [preference value, native name]. "system" follows the browser.
export const LANGUAGES = [["system", "System"], ["en", "English"], ["zh", "中文"]];
export const LOCALES = LANGUAGES.map(([code]) => code).filter((code) => code !== "system");
// The BCP 47 tag for the <html lang> attribute and Intl formatters.
export const TAGS = { en: "en", zh: "zh-CN" };

// The locale a preference value means: a catalog's code as given, else the
// first of the browser's languages that has one, else English.
export function resolveLocale(pref, languages = typeof navigator !== "undefined" ? navigator.languages : []) {
  if (LOCALES.includes(pref)) return pref;
  for (const tag of languages || []) {
    const base = String(tag).toLowerCase().split("-")[0];
    if (LOCALES.includes(base)) return base;
  }
  return "en";
}
