// What the model menu lists (ModelMenu.jsx), apart from how it draws it:
// the groups in order, a model's facts and their words. Pure, so node tests
// it (tests/modelMenu.test.mjs).
import { t } from "../shared/i18n/i18n.js";
import { fmtPrice, fmtTokens } from "../chat/tokenUsage.js";

// Whether every word typed (`query`, split at spaces, any case) is in one
// of `fields`: the model menu's search, the connect dialog's service cards
// and its model checklist.
export function matchesWords(query, ...fields) {
  const words = String(query || "").toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const text = fields.filter(Boolean).join(" ").toLocaleLowerCase();
  return words.every((w) => text.includes(w));
}

// The menu's groups, in order, from the registry's models (/api/ai/models
// `models`). Each group is {key, label?, billing?, rows}; a row is {id,
// model (the registry row, null for one no longer offered), label (a
// service's, or the name `missing` gives one no longer offered: a service
// no longer set up by its id's own), unavailable, favorite, engine}. In
// order:
// - `missing`: the current pick (`value`) when it is no longer offered,
//   marked unavailable, so the menu says what is picked;
// - `follow`: a task's "Use chat model" row (id ""), when `follow` is set;
// - `favorites`, in the order they were starred; one no longer offered
//   stays, unavailable, so its star can be taken off;
// - one group per connection, in the registry's order (the account's own
//   first, then the shared ones), named by the connection with its billing;
// - `engines`: the translation services (`engines`, [{id, label}]), for the
//   translation task.
// `query` keeps the rows matching every word typed, across every group;
// a group left empty goes. `missing` is /api/ai/models' ({id: {model,
// connection}}).
export function menuGroups(models, { favorites = [], query = "", value = "", follow = false, engines = [], missing = {} } = {}) {
  const list = models || [];
  const starred = new Set(favorites || []);
  const byId = new Map(list.map((m) => [m.id, m]));
  const row = (m) => ({ id: m.id, model: m, unavailable: false, favorite: starred.has(m.id), engine: false });
  const gone = (id) => ({ id, model: null, label: missing?.[id]?.model || id.replace(/^engine:/, ""), unavailable: true, favorite: starred.has(id),
    engine: id.startsWith("engine:") });
  const keep = (r) => matchesWords(query, r.model?.model, r.model?.provider_name, r.label, r.id);
  const groups = [];
  const push = (group) => {
    const rows = group.rows.filter(keep);
    if (rows.length) groups.push({ ...group, rows });
  };
  const offered = (id) => byId.has(id) || (engines || []).some((e) => e.id === id);
  if (value && !offered(value)) push({ key: "missing", rows: [gone(value)] });
  if (follow && !String(query || "").trim()) groups.push({ key: "follow", rows: [{ id: "", model: null, unavailable: false, favorite: false, engine: false }] });
  push({ key: "favorites", rows: (favorites || []).map((id) => (byId.has(id) ? row(byId.get(id)) : gone(id))) });
  const connections = new Map();
  for (const m of list) {
    if (!connections.has(m.provider)) connections.set(m.provider, { key: `connection:${m.provider}`, label: m.provider_name || m.provider, billing: m.billing || "", rows: [] });
    connections.get(m.provider).rows.push(row(m));
  }
  for (const group of connections.values()) push(group);
  push({ key: "engines", rows: (engines || []).map((e) => ({ id: e.id, model: null, label: e.label, unavailable: false, favorite: false, engine: true })) });
  return groups;
}

// The rows a keyboard steps through, in the order shown: every row that
// can be picked (not one no longer offered), keyed by its group, since a
// favorite is listed twice.
export const pickableRows = (groups) => groups.flatMap((g) => g.rows.filter((r) => !r.unavailable).map((r) => ({ ...r, key: `${g.key}|${r.id}` })));

// A model's facts as a row shows them (the model menu's, the connect
// dialog's checklist), in order: it can't call tools (the model's own
// check, its reason on hover), it sees pictures, its context window, its
// price per million tokens (a connection paid per token only). For the
// handwriting task (`task` "ink") a model that sees no pictures says it
// can't convert handwriting to text; Redraw as shapes still works with it.
// An unknown fact is left out. `m` is a registry row, or what the catalogs
// know of a model ({context, images, price}) with the connection's billing.
export function modelFacts(m, { task = "" } = {}) {
  if (!m) return [];
  const facts = [];
  if (m.tools === false) facts.push({ kind: "tools", text: t("no tools"), title: m.tools_reason || t("This model can't call tools on its connection") });
  if (task === "ink" && m.images === false) {
    facts.push({ kind: "ink", text: t("can't convert to text"), title: t("This model sees no pictures: it can redraw handwriting as shapes, but not convert it to text") });
  }
  if (m.images === true) facts.push({ kind: "vision", text: "", title: t("Sees pictures") });
  const context = Number(m.context);
  if (Number.isFinite(context) && context > 0) {
    facts.push({ kind: "context", text: fmtTokens(context), title: t("Context window: {n} tokens", { n: context.toLocaleString() }) });
  }
  const input = fmtPrice(m.price?.input), output = fmtPrice(m.price?.output);
  if (m.billing === "api" && input && output) facts.push({ kind: "price", text: `${input} / ${output}`, title: t("Per million tokens, input / output") });
  return facts;
}

// A model's name where two connections offer one of that name: the name,
// and the connection's after it. `models` the registry's.
export function modelChoiceLabel(m, models) {
  if (!m) return "";
  const twin = (models || []).some((x) => x.id !== m.id && x.model === m.model);
  return twin ? `${m.model} · ${m.provider_name || m.provider}` : m.model;
}
