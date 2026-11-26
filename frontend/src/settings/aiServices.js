// The ways to connect an AI service, read off GET /api/ai/settings's
// `protocols` and `services` (the named presets, gamma/ai_protocols/
// services.py): the connect dialog's service cards, which preset a
// connection was made from, and what that says about it (a key it may go
// without, what it is billed as). Pure, so
// tests/aiServices.test.mjs imports it; the dialog's state is
// providerEditor.js.
import { T, t } from "../shared/i18n/i18n.js";

// The protocols' own names, with the chat's setup tiles' hints, on those
// tiles and the dialog's cards.
const SERVICE_TILES = {
  chatgpt: { label: t("ChatGPT"), hint: t("Sign in with your subscription. No API key.") },
  "gemini-signin": { label: t("Gemini"), hint: t("Sign in with your Google account. No API key.") },
  anthropic: { label: t("Anthropic"), hint: t("Claude models, with an API key") },
  openai: { label: t("OpenAI API"), hint: t("GPT models, with an API key") },
};

// Each sign-in protocol's own words: the connect dialog's sign-in step and
// a signed-in connection's row. `paste` is what a sign-in made in a browser
// on another machine hands back: ChatGPT's redirect address, which fails
// to load ("address"), or the code Google's page shows ("code").
export const SIGN_INS = {
  chatgpt: {
    title: t("Sign in with ChatGPT"), billed: t("No API key — usage is billed to your ChatGPT subscription."),
    open: t("Open ChatGPT sign-in"), reopen: t("Re-open ChatGPT sign-in"), plan: t("ChatGPT subscription"),
    unstarted: t("Open ChatGPT sign-in first."), unfinished: t("Finish the sign-in first, or paste the address it ended on."),
    paste: "address",
  },
  "gemini-signin": {
    title: t("Sign in with Google"), billed: t("No API key — usage counts against your organization's Gemini Code Assist license."),
    open: t("Open Google sign-in"), reopen: t("Re-open Google sign-in"), plan: t("Google account"),
    unstarted: t("Open Google sign-in first."), unfinished: t("Finish the sign-in first, or paste the code Google shows."),
    paste: "code",
  },
};
// A sign-in this browser has no words for yet reads as ChatGPT's.
export const signInWords = (protocol) => SIGN_INS[protocol] || SIGN_INS.chatgpt;

// What a sign-in hands back, as copied: the address its redirect ended on
// with its code (ChatGPT's localhost:1455/auth/callback, a loopback
// /oauth2callback), of the sign-in `state` when one is given, or the code
// Google's page shows ("4/0A…").
export function signInAddress(text, state = "") {
  if (/^4\/[\w-]{10,}$/.test(text)) return true;
  try {
    const url = new URL(text);
    return /\/(auth\/callback|oauth2callback)$/.test(url.pathname) && !!url.searchParams.get("code")
      && (!state || url.searchParams.get("state") === state);
  } catch {
    return false;
  }
}

// The ways to connect an AI service, as the chat's setup card's tiles
// ({value, label, hint}): each protocol the server offers (sign-in first,
// then the API-key ones in the server's order), then "Other service" for
// the named services and a custom endpoint, which opens the connect dialog
// on its first preset. A tile's value is a protocol id or "other". A
// protocol with a caution note (the Gemini sign-in's terms) is no first
// step to offer: it stays in the connect dialog, its note under its card.
export function aiServiceTiles(info) {
  const protocols = [...(info?.protocols || [])].filter((p) => !p.note)
    .sort((a, b) => Number(b.auth === "oauth") - Number(a.auth === "oauth"));
  const tiles = protocols.map((p) => ({
    value: p.id,
    label: SERVICE_TILES[p.id]?.label || p.label,
    hint: SERVICE_TILES[p.id]?.hint || (p.auth === "oauth" ? t("Subscription sign-in") : t("API key")),
  }));
  if (protocols.some((p) => p.auth !== "oauth")) {
    const names = serviceGroups(info?.services).map((group) => group.name).join(", ");
    tiles.push({
      value: "other",
      label: t("Other service"),
      hint: names ? t("{services} or any OpenAI-compatible endpoint", { services: names }) : t("Any OpenAI-compatible endpoint"),
    });
  }
  return tiles;
}

// The named services by vendor, in the server's order (the chat's setup
// card names them on its Other tile).
function serviceGroups(services) {
  const groups = [];
  for (const service of services || []) {
    const name = service.group || service.label;
    const group = groups.find((item) => item.name === name);
    if (group) group.services.push(service);
    else groups.push({ name, services: [service] });
  }
  return groups;
}

// The connect dialog's first step: every way to connect as a card, in three
// groups. Signing in or a subscription (the sign-in protocols, then the
// presets billed as a subscription), an API key (the key protocols, then
// the pay-per-token presets), and on this computer or network (the local
// presets, then a custom endpoint). A vendor's presets of one group are
// one card whose `plans` the dialog offers side by side (Qwen's
// international and China endpoints); a plan is a preset's id, a protocol
// id, or "custom". `only(protocolId)` keeps the cards whose protocol it
// accepts (an edit offers only the kind the entry is). Each card has its
// billing tag ("" for a custom endpoint, whose billing is the user's).
const SERVICE_SECTIONS = [
  ["signin", T("Sign in or subscription")], ["key", T("API key")], ["local", T("On your computer or network")],
];
const sectionOf = (preset) => (preset.billing === "subscription" ? "signin" : preset.billing === "local" ? "local" : "key");

export function serviceSections(info, { only = null } = {}) {
  const offered = (protocolId) => !only || only(protocolId);
  const cards = { signin: [], key: [], local: [] };
  for (const p of info?.protocols || []) {
    if (!offered(p.id)) continue;
    const oauth = p.auth === "oauth";
    cards[oauth ? "signin" : "key"].push({
      id: p.id, label: SERVICE_TILES[p.id]?.label || p.label, billing: oauth ? "subscription" : "api",
      plans: [{ id: p.id, label: p.label }],
    });
  }
  for (const preset of info?.services || []) {
    if (!offered(preset.protocol)) continue;
    const section = sectionOf(preset);
    const vendor = preset.group || preset.label;
    const plan = { id: preset.id, label: preset.plan || preset.label, note: preset.note || "" };
    const card = cards[section].find((item) => item.vendor === vendor);
    if (card) card.plans.push(plan);
    else cards[section].push({ id: `${section}:${vendor}`, vendor, label: preset.label, billing: preset.billing || "api", plans: [plan] });
  }
  const keyProtocol = (info?.protocols || []).find((p) => p.auth !== "oauth");
  if (keyProtocol && offered(keyProtocol.id)) {
    cards.local.push({ id: "custom", label: t("Custom endpoint"), billing: "", hint: t("Any compatible address"), plans: [{ id: "custom", label: t("Custom endpoint") }] });
  }
  return SERVICE_SECTIONS.map(([id, title]) => ({ id, title, cards: cards[id] })).filter((section) => section.cards.length);
}

// The named service an entry (or a form) was made from: the preset with its
// protocol and base URL (the server's service_of), else null.
function serviceOf(entry, info) {
  const base = baseOf(entry);
  return (info?.services || []).find((s) => s.protocol === entry?.protocol && s.base_url === base) || null;
}

// A base URL as the server compares it: trimmed, without trailing slashes.
const baseOf = (entry) => (entry?.base_url || "").trim().replace(/\/+$/, "");

// Which plan a form is on: the one picked in it, else what its protocol and
// base URL say (a preset, a protocol at its own endpoint, or "custom").
export function formService(form, info) {
  if (!form) return "";
  if (form.service) return form.service;
  if (form.custom) return "custom";
  const preset = serviceOf(form, info);
  if (preset) return preset.id;
  const base = baseOf(form);
  return base && base !== baseOf({ base_url: info?.protocols?.find((p) => p.id === form.protocol)?.default_base_url }) ? "custom" : form.protocol;
}

// A key connection that may be saved without a key, by the server's rule
// (gamma/ai_settings.py key_optional, case for case: the `keyless` cases of
// tests/shared/ai_models.json): its preset's `key_optional` (Ollama), or
// a custom endpoint, which may be a server on this network
// that asks for nothing. An empty base URL, or the protocol's own, is the
// provider's endpoint, which takes a key.
export function keyOptional(form, info) {
  const protocol = (info?.protocols || []).find((p) => p.id === form?.protocol);
  if (!protocol || protocol.auth === "oauth") return false;
  const base = baseOf(form);
  if (!base || base === baseOf({ base_url: protocol.default_base_url })) return false;
  const preset = serviceOf(form, info);
  return !preset || !!preset.key_optional;
}

// What "Auto" bills a key entry as (gamma/ai_settings.py, "Billing of a
// connection"): its preset's billing, else pay per token.
export const autoBilling = (form, info) => (info?.services || []).find((s) => s.id === formService(form, info))?.billing || "api";

