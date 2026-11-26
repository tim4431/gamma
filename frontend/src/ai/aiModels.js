// The rules under the account's models for each task (useAiModels.js owns
// the state; docs/dev/ai.md "Models for each task"). The server resolves
// every task's model (/api/ai/models `roles`: {pick, model, state}); the
// client applies the same rule (resolveRoles) to show a pick before the
// server answers and to say what removing a connection does. The rest is
// what the client says about the tasks, and the one-time move of this
// browser's old picks. Pure, so node tests it (tests/aiModels.test.mjs,
// with the server's own cases in tests/shared/ai_models.json).
import { T, t } from "../shared/i18n/i18n.js";
import { FREE_TRANSLATE_ENGINE } from "../app/prefDefs.js";
import { billingLabel } from "../chat/tokenUsage.js";

// The tasks, in the order Settings lists them. Dictation is no task model:
// it keeps its own transcription pick.
export const ROLES = ["chat", "metadata", "translate", "ink"];
export const ROLE_LABELS = {
  chat: T("Chat"),
  metadata: T("Metadata & citations"),
  translate: T("Translation"),
  ink: T("Handwriting"),
};
export const roleLabel = (role) => t(ROLE_LABELS[role] || role);

// Each task's model, {role: {pick, model, state}}: gamma/ai_roles.py
// resolve_roles, case for case (tests/shared/ai_models.json). `models` the
// registry's ([{id}]), `picks` the account's ({role: id}, "" or none for
// no pick), `engines` the ids of the translation services set up. `state`
// is ok (the pick is offered), follows (no pick) or missing (a pick no
// longer offered); `model` is the pick when ok, else the chat model (for
// the chat itself the first model offered), else "". Translation may pick
// a service too, and with no model offered at all it falls to the free
// service when that is set up.
export function resolveRoles(models, picks, engines = []) {
  const offered = new Set((models || []).map((m) => m.id));
  const services = new Set(engines || []);
  const resolved = (role, fallback) => {
    const pick = picks?.[role] || "";
    const state = !pick ? "follows" : offered.has(pick) || (role === "translate" && services.has(pick)) ? "ok" : "missing";
    let model = state === "ok" ? pick : fallback;
    if (role === "translate" && state !== "ok" && !offered.size) model = services.has(FREE_TRANSLATE_ENGINE) ? FREE_TRANSLATE_ENGINE : "";
    return { pick, model, state };
  };
  const roles = { chat: resolved("chat", models?.[0]?.id || "") };
  for (const role of ROLES.slice(1)) roles[role] = resolved(role, roles.chat.model);
  return roles;
}

// The picks under a list's roles, and the ids of its translation services.
const picksOf = (roles) => Object.fromEntries(ROLES.map((role) => [role, roles?.[role]?.pick || ""]));
const engineIds = (info) => (info?.translate_engines || []).map((e) => e.id);

// The list (/api/ai/models' answer) with task `role` picking `id` ("" for
// the chat model), resolved as the server will: what a pick shows until the
// server answers. A chat pick is the user's own, no longer the migrated one.
export function withPick(info, role, id) {
  return { ...info, roles: resolveRoles(info.models, { ...picksOf(info.roles), [role]: id }, engineIds(info)),
    ...(role === "chat" ? { chat_migrated: false } : {}) };
}

// What task `role` uses without a pick of its own: the model its menu's
// first row names (the chat model, or for translation with no model at all
// the free service).
export const followed = (info, role) => resolveRoles(info?.models, { ...picksOf(info?.roles), [role]: "" }, engineIds(info))[role].model;

// What a task's requests go to now: the server's effective model for it.
export const modelFor = (info, role) => info?.roles?.[role]?.model || "";

// A model's own name: an offered one's from the registry, a translation
// service's label, one no longer offered as /api/ai/models `missing` names
// it, else the id as it is.
export function modelName(info, id) {
  const offered = (info?.models || []).find((m) => m.id === id);
  if (offered) return offered.model;
  return (info?.translate_engines || []).find((e) => e.id === id)?.label || info?.missing?.[id]?.model || id || "";
}

// The connection a model goes through, by name: an offered one's, else
// the entry `missing` names ("" once the entry itself is gone).
export function connectionName(info, id) {
  const offered = (info?.models || []).find((m) => m.id === id);
  return offered ? offered.provider_name || offered.provider : info?.missing?.[id]?.connection || "";
}

// A model as a sentence names it: "claude-sonnet-4-6 (Work key)", a
// service by its label; one whose connection is gone is on "a removed
// connection".
export function modelTitle(info, id) {
  if ((info?.translate_engines || []).some((e) => e.id === id)) return modelName(info, id);
  return `${modelName(info, id)} (${connectionName(info, id) || t("a removed connection")})`;
}

// What removing connection `connectionId` does to the tasks, given the
// list as it is now (/api/ai/models' answer): `affected` lists each task
// whose model changes, {role, from, to}, `to` the model it then resolves
// to ("" for none). `noneLeft`: no model is left at all, so the AI
// features stop.
export function tasksAffectedBy(connectionId, info) {
  const left = (info?.models || []).filter((m) => m.provider !== connectionId);
  const after = resolveRoles(left, picksOf(info?.roles), engineIds(info));
  const affected = ROLES.map((role) => ({ role, from: modelFor(info, role), to: after[role].model }))
    .filter((a) => a.from !== a.to);
  return { affected, noneLeft: !left.length };
}

// What a task's row in Settings says of it ({state, text}): the
// connection its model goes through and how that is paid for (`ok`), that
// it uses the chat model (`follows`), or, in the warning's words, that its
// pick is no longer offered and what serves it instead (`missing`). A
// translation service picked is no connection's: its key was removed.
export function roleStatus(role, info) {
  const r = info?.roles?.[role];
  if (!r) return { state: "follows", text: "" };
  if (r.state === "missing") {
    const fallback = r.model ? modelTitle(info, r.model) : "";
    if (r.pick.startsWith("engine:")) {
      return { state: "missing", text: fallback ? t("The translation service picked is no longer set up. Using {fallback} instead.", { fallback })
        : t("The translation service picked is no longer set up.") };
    }
    const words = { model: modelName(info, r.pick), connection: connectionName(info, r.pick) || t("a removed connection"), fallback };
    return { state: "missing", text: fallback ? t("{model} ({connection}) is no longer available. Using {fallback} instead.", words)
      : t("{model} ({connection}) is no longer available.", words) };
  }
  if (r.state === "follows" && role !== "chat") return { state: "follows", text: t("Uses chat model") };
  const m = (info.models || []).find((x) => x.id === r.model);
  if (m) return { state: "ok", text: [m.provider_name, billingLabel(m.billing)].filter(Boolean).join(" · ") };
  return { state: "ok", text: (info.translate_engines || []).some((e) => e.id === r.model) ? t("A translation service") : "" };
}

// A task's Settings row (Settings → AI → Models, Language & translation's
// Translate with): the line under its name, roleStatus's, or `hint` (the
// row's own words) while its pick is offered; and the row's class, red
// while it is not.
export function taskRow(info, role, hint = "") {
  const status = roleStatus(role, info);
  const missing = status.state === "missing";
  return { hint: missing || !hint ? status.text : hint, className: missing ? "aiTaskMissing" : "" };
}

// The sentences the remove confirm adds after its question: what each task
// that loses its model will use instead, and, with no model left, that the
// AI features stop. `info` is /api/ai/models' answer.
export function removalNotice(connectionId, info) {
  const { affected, noneLeft } = tasksAffectedBy(connectionId, info);
  const lines = affected.filter((a) => a.to).map((a) => t("{task} will use {model} instead.",
    { task: roleLabel(a.role), model: modelTitle(info, a.to) }));
  if (noneLeft) lines.push(t("AI features stop until you add a connection."));
  return lines;
}

// The connections the tasks' models go through, each once, in the tasks'
// order: what the connection check at login asks about.
export function connectionsInUse(info) {
  const models = info?.models || [];
  const ids = ROLES.map((role) => models.find((m) => m.id === modelFor(info, role))?.provider).filter(Boolean);
  return [...new Set(ids)];
}

// The model a probe of connection `providerId` asks (the Test button, the
// login check's test request): the first task's model on it, the
// metadata's first (the cheap utility model), as its name on the
// connection; "" leaves it to the entry's own test model.
export function probeModel(info, providerId) {
  const models = info?.models || [];
  for (const role of ["metadata", "chat", "translate", "ink"]) {
    const m = models.find((x) => x.id === modelFor(info, role));
    if (m && m.provider === providerId) return m.model;
  }
  return "";
}

// This browser's picks from before the account kept them (localStorage),
// moved once into the account's roles (POST /api/ai/models/import), then
// forgotten. The server takes each only where the account has none of its
// own, and skips one it does not offer (an old chat pick of a bare model
// name among them).
export const LEGACY_KEYS = { chat: "gamma-chat-model", metadata: "gamma-meta-model", translate: "gamma-translate-model" };
const LEGACY_LEFTOVERS = ["gamma-chat-model-by-key"];

// `storage` is localStorage's shape, `post(body)` the import request.
// Resolves to its answer, or null when there was nothing to send. The keys
// go only once the request succeeded: one that fails (offline, a server
// that restarts) leaves them for the next load. Nothing stored: nothing
// asked.
export async function importLegacyPicks(storage, post) {
  const read = (key) => { try { return storage.getItem(key); } catch { return null; } };
  const keys = [...Object.values(LEGACY_KEYS), ...LEGACY_LEFTOVERS].filter((key) => read(key) !== null);
  if (!keys.length) return null;
  const body = Object.fromEntries(Object.entries(LEGACY_KEYS).map(([field, key]) => [field, read(key) || ""]).filter(([, value]) => value));
  const answer = Object.keys(body).length ? await post(body) : null;
  for (const key of keys) { try { storage.removeItem(key); } catch {} }
  return answer;
}
