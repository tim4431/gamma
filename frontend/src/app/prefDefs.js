// Every user preference behind the Settings dialog, declared once: its
// localStorage key, default, codec and scope. Pure (no React, no network) so
// it runs under node --test; the hooks live in app/prefs.js.
//
// Scope says where a preference lives:
// - "account": follows the signed-in account. All of them travel together as
//   one JSON object, keyed by preference name, under the account-wide
//   /api/prefs/profile key (useProfileSync in prefs.js): the server copy
//   wins on load, localStorage stays the instant-paint cache.
// - "browser": describes this device and stays in this browser.
// The AI provider entries and the active AI key are not preferences here:
// they keep their own account-wide keys (ai-settings, ai-provider).
//
// A codec clamps a stored string back into range (parse returns undefined
// to fall back to the default), so a stale or hand-edited value never
// breaks the app. Plain strings need no codec.
import { DEFAULT_TOOLS, normalizeTools } from "../ink/ink.js";
import { LANGUAGES } from "../shared/i18n/locales.js";
import { normalizeChord } from "../shared/lib/hotkeys.js";

export const ACCOUNT = "account";
export const BROWSER = "browser";

const FLAG = { parse: (raw) => raw === "1", serialize: (v) => (v ? "1" : "0") };
const oneOf = (values) => ({ parse: (raw) => (values.includes(raw) ? raw : undefined) });
const intIn = (min, max) => ({
  parse: (raw) => {
    const value = Number.parseInt(raw, 10);
    return Number.isFinite(value) && value >= min && value <= max ? value : undefined;
  },
});
const json = (normalize) => ({
  parse: (raw) => { try { return normalize(JSON.parse(raw)); } catch { return undefined; } },
  serialize: JSON.stringify,
});

// AI context-size preferences (chars of extracted PDF text).
const CONTEXT_CHARS = intIn(100, 1000000);

// Chip-display modes for home cards/rows; the codec and the Settings
// MenuSelect both derive from this list.
export const FILE_LABEL_MODES = ["off", "labels", "folders", "both"];

// Target languages for the PDF translated view. Codes mirror the backend's
// allowlist (TRANSLATE_LANGS in gamma/routers/ai.py) — keep the two in sync.
export const TRANSLATE_LANGS = [
  ["zh-CN", "中文（简体）"], ["zh-TW", "中文（繁體）"], ["en", "English"],
  ["ja", "日本語"], ["ko", "한국어"], ["de", "Deutsch"], ["fr", "Français"],
  ["es", "Español"], ["pt", "Português"], ["it", "Italiano"], ["ru", "Русский"],
];

// Agent per-tool permissions (Settings → Assistant → Tool configuration),
// one map per chat KIND: "folder" (the home/folder chat), "pdf" (a page with
// a PDF attached) and "notes" (a page without one). The chat picks its
// kind's map (chat/ChatDock.jsx) and sends it as the request's `permissions`.
// Missing keys mean allowed, so new tools default on for existing users;
// a pre-kind flat map ({list, read, …}) is applied to every kind.
export const CHAT_KINDS = ["folder", "pdf", "notes"];
const TOOL_PERMS_DEFAULT = {
  list: true, read: true, block_read: true, view: true, search: true,
  web_search: true, web_read: true,
  rename: true, move: true, block_edit: true,
};
const AGENT_PERMS = json((value) => {
  if (!value || typeof value !== "object") return undefined;
  const perKind = CHAT_KINDS.some((k) => value[k] && typeof value[k] === "object");
  return Object.fromEntries(CHAT_KINDS.map((k) => [
    k, { ...TOOL_PERMS_DEFAULT, ...(perKind ? value[k] || {} : value) },
  ]));
});

export const THEMES = ["system", "light", "dark", "gamma-light", "gamma-dark", "sepia", "solarized", "gray"];

// Interface size (Settings → Appearance): text and control boxes share
// --ui-scale in app.css. Ctrl+scroll further resizes notes/chat text in place
// per panel and never persists. PDF zoom remains independent. The
// index.html pre-paint script repeats the bounds — keep them in step.
export const UI_SCALE = { min: 0.7, max: 1.6, step: 0.1, default: 1 };

// Fingers never draw by default where the primary pointer is coarse (tablets).
const COARSE = typeof window !== "undefined" && !!window.matchMedia?.("(pointer: coarse)").matches;

const pref = (key, scope, def, codec = {}) => ({ key, scope, default: def, ...codec });
const flag = (key, scope, def) => pref(key, scope, def, FLAG);

// name → definition. useAppPrefs() returns `name` and `setName` for each.
export const PREFS = {
  // --- Appearance (Settings → Appearance) ---
  // System follows the OS; the other themes pin the appearance. Sepia,
  // Solarized Light and Gray also tint PDF pages (app.css). index.html
  // applies a pinned theme from localStorage before first paint.
  theme: pref("gamma-theme", ACCOUNT, "system", oneOf(THEMES)),
  // Interface language (docs/dev/i18n.md): "system" follows the browser.
  // main.jsx reads the stored value before the first render.
  language: pref("gamma-language", ACCOUNT, "system", oneOf(LANGUAGES.map(([code]) => code))),
  // Flip page colors: display-only inverted (night) rendering of the PDF canvas.
  pdfDarkPage: flag("gamma-pdf-dark", ACCOUNT, false),
  // Where the header's sync pill shows for a publication (pages published
  // to Gamma Cloud): on the published pages only, or on every page of the
  // workspace. A clone's pill is on every page regardless (it syncs them all).
  syncPillScope: pref("gamma-sync-pill", ACCOUNT, "synced", oneOf(["synced", "all"])),
  // Interface size: index.html applies the stored value before first paint,
  // App.jsx keeps `--ui-scale` on the root in sync afterwards. Screens
  // differ, so it stays with the device.
  uiScale: pref("gamma-ui-scale", BROWSER, 1, {
    parse: (raw) => {
      const n = Number.parseFloat(raw);
      return Number.isFinite(n) && n >= UI_SCALE.min && n <= UI_SCALE.max ? Math.round(n * 100) / 100 : undefined;
    },
  }),
  // The always-on status bar under the tabs — off by default, the floating
  // pill carries user-facing messages; the bar is a debugging aid.
  statusBarVisible: flag("gamma-status-bar", BROWSER, false),
  // Tours offered by themselves the first time a feature comes up
  // (guide/triggers.js); off leaves only Account › Tours.
  suggestTours: flag("gamma-suggest-tours", ACCOUNT, true),

  // --- Library display (Settings → Library) ---
  // Recently-viewed cards on the home page (only — library cards always use
  // the glyph): cover thumbnails (a snapshot of the PDF at the last-read
  // spot). Off shows the file icon instead and stops capturing new ones.
  recentThumbs: flag("gamma-recent-thumbs", ACCOUNT, true),
  // Folder/label chips on home file cards and list rows.
  fileLabels: pref("gamma-home-file-labels", ACCOUNT, "both", oneOf(FILE_LABEL_MODES)),

  // --- Papers (Settings → Library) ---
  oaFallback: flag("gamma-oa-fallback", ACCOUNT, true),
  metaAutoFetch: flag("gamma-meta-auto", ACCOUNT, true),
  pdfSaveLocal: flag("gamma-pdf-save", ACCOUNT, true),

  // --- PDF viewer (Settings → Reading) ---
  // Embedded PDF annotations (burned in by a Gamma export or another viewer)
  // would render twice once imported as blocks — canvas + overlay. "hide"
  // keeps them out of the canvas; "strip" removes them from the stored file
  // at import time.
  embAnnots: pref("gamma-embedded-annots", ACCOUNT, "hide", oneOf(["hide", "strip"])),

  // --- Translation (Settings → Reading, AI → Advanced) ---
  // Master switch: off removes the translate button from the viewer.
  translateEnabled: flag("gamma-translate-enabled", ACCOUNT, true),
  // Target language for the translated view (the 文A button in the viewer).
  translateLang: pref("gamma-translate-lang", ACCOUNT, "zh-CN", oneOf(TRANSLATE_LANGS.map(([code]) => code))),
  // Parallel translation requests: chunks of a page are translated this many
  // at a time — the whole-document queue never exceeds it either. Clamped to
  // 1–32 (a chunk is ~1200 chars, so even 32 stays well under provider rate
  // limits for most accounts).
  translateParallel: pref("gamma-translate-parallel", ACCOUNT, 3, intIn(1, 32)),
  // Reasoning effort for translation calls. "" = provider default (param
  // omitted — some models reject it outright, so that stays the safe
  // default); Low/Minimal is the speed lever for reasoning models, which
  // otherwise spend their thinking budget before the first output token.
  translateEffort: pref("gamma-translate-effort", ACCOUNT, "", oneOf(["", "minimal", "low", "medium", "high"])),

  // --- Search (Settings → Reading) ---
  // Whether the search popover's result-detail lists start expanded, one
  // default per place. Home page: expanded unless turned off — with no open
  // PDF the compact find bar shows nothing. Page view: compact unless on.
  searchDetailsHome: flag("gamma-search-details-home", ACCOUNT, true),
  searchDetailsPaper: flag("gamma-search-details", ACCOUNT, false),

  // --- Notes (Settings → Reading) ---
  // Enter key in the note editor: off (default) = Enter types a line break and
  // Shift+Enter starts a new note; on = the Logseq-style swap of the two.
  enterNewNote: flag("gamma-enter-new-note", ACCOUNT, false),
  // Keyboard shortcuts (Settings → Keyboard, docs/dev/hotkeys.md): command
  // id → chord ("Mod-Shift-k") or null for unbound; a command not named
  // keeps its default. Unknown shapes are dropped, the ids are not checked
  // here (the catalog lives in app/commands.js) so a removed command's
  // entry is simply ignored.
  keybindings: pref("gamma-keybindings", ACCOUNT, {}, json((value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
    const out = {};
    for (const [id, chord] of Object.entries(value)) {
      if (chord === null) out[id] = null;
      else if (typeof chord === "string" && normalizeChord(chord)) out[id] = normalizeChord(chord);
    }
    return out;
  })),

  // --- Models (Settings → AI → Connections) ---
  // Model picks name entries of this server's provider list ("<entry>:<model>"),
  // which never leave the server, so they stay with the browser like the chat
  // model itself (App.jsx `gamma-chat-model`). "" = follow the chat model; a
  // stale pick (provider/model removed) also falls back.
  metaModel: pref("gamma-meta-model", BROWSER, ""),
  translateModel: pref("gamma-translate-model", BROWSER, ""),
  // Voice dictation (mic button): transcription model + spoken language
  // ("" = the display language, "auto" = let the model detect it); the same
  // Models section.
  dictationModel: pref("gamma-dictation-model", BROWSER, "gpt-4o-transcribe"),
  dictationLang: pref("gamma-dictation-lang", BROWSER, ""),

  // --- Chat behaviour (Settings → AI → Chat / Advanced) ---
  chatEffort: pref("gamma-chat-effort", ACCOUNT, ""),
  // Connection check of the active provider at login (POST /api/ai/health):
  // "ping" (default) is the free credential check — OAuth entries hit the
  // usage endpoint, API keys list /v1/models, both 401 on a dead credential
  // without spending tokens; "test" runs the tiny live completion (the same
  // probe as the settings Test button, through the entry's test model).
  aiLoginCheck: pref("gamma-ai-login-check", ACCOUNT, "ping", oneOf(["off", "ping", "test"])),
  // Master kill-switch for tool use in every chat (folder and page alike).
  agentEnabled: flag("gamma-ai-agent-enabled", ACCOUNT, true),
  agentPerms: pref("gamma-ai-agent-perms", ACCOUNT,
    Object.fromEntries(CHAT_KINDS.map((k) => [k, { ...TOOL_PERMS_DEFAULT }])), AGENT_PERMS),
  // Organizer tool-round budget (home/folder chat agent loop), 1–100.
  toolRounds: pref("gamma-ai-tool-rounds", ACCOUNT, 32, intIn(1, 100)),
  // Per-read_page-call cap on document text the folder/paper agent may pull.
  // The default matches the backend's fallback (READ_CHARS_CAP in
  // gamma/ai_tools.py) — keep the two in sync.
  agentReadChars: pref("gamma-ai-read-chars", ACCOUNT, 20000, CONTEXT_CHARS),
  // Off by default: rectangle snapshots stay attached until removed or sent.
  // On, a plain click elsewhere in the PDF drops them — the same gesture that
  // clears quoted text selections.
  chatImgAutoClear: flag("gamma-chat-img-autoclear", ACCOUNT, false),

  // --- Context budgets (Settings → AI → Advanced) ---
  chatContextChars: pref("gamma-chat-context-chars", ACCOUNT, 60000, CONTEXT_CHARS),
  metaContextChars: pref("gamma-meta-context-chars", ACCOUNT, 6000, CONTEXT_CHARS),
  multiContextChars: pref("gamma-multi-context-chars", ACCOUNT, 120000, CONTEXT_CHARS),

  // --- Prompts (Settings → AI → Prompts; "" = built-in default from /api/ai/models) ---
  chatSystem: pref("gamma-chat-system", ACCOUNT, ""),
  agentSystem: pref("gamma-agent-system", ACCOUNT, ""),
  metaPrompt: pref("gamma-meta-prompt", ACCOUNT, ""),
  citePrompt: pref("gamma-cite-prompt", ACCOUNT, ""),

  // --- Handwriting (Settings → Reading; docs/dev/handwriting.md) ---
  // Input rules and tools belong to the device's pen and screen. inkPenOnly:
  // fingers never draw (they scroll and pinch). inkAutoPen: a stylus draws
  // with the pen even when no tool is armed. inkPressure: use the stylus
  // pressure for stroke width.
  inkPenOnly: flag("gamma-ink-pen-only", BROWSER, COARSE),
  inkAutoPen: flag("gamma-ink-auto-pen", BROWSER, true),
  inkPressure: flag("gamma-ink-pressure", BROWSER, true),
  // The strip's tool presets: [{id, kind: pen|highlighter, color, size}],
  // the user's own row of pens and highlighters (ink.js DEFAULT_TOOLS).
  inkTools: pref("gamma-ink-tools", BROWSER, DEFAULT_TOOLS, json(normalizeTools)),
  // "stroke" erases whole strokes, "partial" cuts through them; the size
  // is an S/M/L index (ink/InkLayer.jsx ERASER_SIZES).
  inkEraserMode: pref("gamma-ink-eraser", BROWSER, "stroke", oneOf(["stroke", "partial"])),
  inkEraserSize: pref("gamma-ink-eraser-size", BROWSER, 1, { ...intIn(0, 2), serialize: String }),
  // The lasso draws a freeform loop or a box.
  inkLassoMode: pref("gamma-ink-lasso", BROWSER, "free", oneOf(["free", "box"])),
};

export const setterName = (name) => `set${name[0].toUpperCase()}${name.slice(1)}`;

// The preferences stored in the account's profile, in declaration order.
export const ACCOUNT_PREFS = Object.keys(PREFS).filter((name) => PREFS[name].scope === ACCOUNT);

// The profile object for a set of preference values ({name: value, …}).
export function profileOf(values) {
  return Object.fromEntries(ACCOUNT_PREFS.map((name) => [name, values[name]]));
}

// One stored profile value checked the way a localStorage value is: it must
// have the default's JSON type and survive the codec's round trip.
function profileValue(def, value) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== typeof def.default || Array.isArray(value) !== Array.isArray(def.default)) return undefined;
  if (!def.parse) return value;
  try { return def.parse(def.serialize ? def.serialize(value) : String(value)); } catch { return undefined; }
}

// The usable entries of a profile read from the server, in declaration
// order: unknown names, browser-scoped names and invalid values are dropped,
// so a profile written by an older or newer Gamma applies what it can.
export function readProfile(value) {
  const out = {};
  if (!value || typeof value !== "object" || Array.isArray(value)) return out;
  for (const name of ACCOUNT_PREFS) {
    const parsed = profileValue(PREFS[name], value[name]);
    if (parsed !== undefined) out[name] = parsed;
  }
  return out;
}
