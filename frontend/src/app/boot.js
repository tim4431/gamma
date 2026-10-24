// The reads a page load starts with, sent as main.jsx begins, before the
// app's modules have loaded: the server's config (PageHostGate's) and the
// session (LibraryApp's first checkSession). Each is taken once by the code
// that would otherwise ask for it, so neither waits on the other or on the
// app's code. A share link opens without them (its view reads no session),
// and so does the assistant's consent page, which reads what it needs.
//
// The session also carries the account's theme and language (`profile`,
// docs/dev/settings.md): adoptAccountLook keeps them where the app reads
// them and puts the theme on at once, so a browser that has never seen the
// account draws its first screen in them instead of switching after the
// profile's pull, and a language other than the browser's needs no reload.
// A small module with plain fetch: it runs in the page's first script.
import { LANGUAGES } from "../shared/i18n/locales.js";
import { THEMES, applyTheme, resolveTheme } from "./theme.js";

// The profile as this browser last took it from an account or sent it
// there ({name: JSON}; prefs.js keeps it).
export const PROFILE_SEEN_KEY = "gamma-profile-seen";

const reads = {};

export function startBootReads() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("share") || params.get("gamma_oauth")) return reads;
  reads.config = getJson("/api/server-config");
  reads.session = getJson("/api/session");
  return reads;
}

// A boot read's answer, to its first taker; a later one asks for itself.
export function takeBootRead(name) {
  const read = reads[name] || null;
  delete reads[name];
  return read;
}

function getJson(url) {
  const read = fetch(url, { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${url} answered ${r.status}`))));
  read.catch(() => {}); // its taker sees a failure; one nobody takes is no error
  return read;
}

// The account's theme and language as the session carries them: each
// stored under its preference's key, where the app's state starts from, and
// the theme put on the page. Returns the language, or null without one.
export function adoptAccountLook(profile) {
  if (!profile || typeof profile !== "object") return null;
  const { theme, language } = profile;
  if (THEMES.includes(theme)) {
    store("gamma-theme", theme);
    applyTheme(resolveTheme(theme));
  }
  if (!LANGUAGES.some(([code]) => code === language)) return null;
  store("gamma-language", language);
  return language;
}

// Whether the stored language is the one this browser last took from an
// account (PROFILE_SEEN_KEY), so the account most likely still has it.
export function languageSeen(stored) {
  try {
    const seen = JSON.parse(localStorage.getItem(PROFILE_SEEN_KEY) || "null");
    return !!seen && seen.language === JSON.stringify(stored);
  } catch { return false; }
}

function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* the app keeps the value for this load */ }
}
