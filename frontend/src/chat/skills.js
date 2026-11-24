// Skills in the chat (docs/dev/skills.md): a message that begins with
// "/name" runs the skill of that name, whose instructions the server puts
// ahead of the message (gamma/skills.py turn_text). These are the pure
// pieces: where the composer's "/" picker opens and what it inserts, the
// split of a message as typed into the skill it runs and its text, the
// picker's ranking, and how a message that ran one reads as text and as a
// conversation's title. The catalog is the account's effective skills
// (chat/useSkills.js effectiveSkills): rows {id, name, description,
// argument_hint, source}.
import { fuzzyIndices } from "../editor/slashCommands.js";
import { T, t } from "../shared/i18n/i18n.js";

// The built-in skills' descriptions and argument hints
// (gamma/builtin_skills/<name>/SKILL.md), named here so the catalog scanner
// finds them: the server sends the English, and a built-in's row shows them
// translated (skillDescription, skillHint). tests/chatSkills.test.mjs reads
// the shipped files, so a changed line there fails until it is changed here.
export const BUILTIN_TEXT = [
  T("A structured summary of the paper or pages in context — the question, the method, the main results with their evidence, and the limits."),
  T("[focus or length, e.g. 'for a journal club' or 'one paragraph']"),
  T("A referee-style critique of the paper in context — its claims, whether the evidence supports them, weaknesses, and questions for the authors."),
  T("[venue, focus or stance, e.g. 'NeurIPS, focus on the experiments']"),
  T("Walks through one passage, equation or concept of the paper at length and at the level asked for, from first intuition to the details. For a request to be taken through something, not for an ordinary question about the paper."),
  T("[what to explain and for whom, e.g. 'eq. 3 for an undergraduate']"),
];

// A row's description and argument hint as the picker and Settings show
// them: a built-in's translated, an account's own as written.
const shown = (row, text) => (row?.source === "builtin" ? t(text || "") : text || "");
export const skillDescription = (row) => shown(row, row?.description);
export const skillHint = (row) => shown(row, row?.argument_hint);

// The "/" being typed at the start of the message (its first non-blank
// character), with a collapsed caret inside the word that follows it:
// {start, end, query}, `start` the "/", `end` the end of that word (what a
// pick replaces), `query` the word up to the caret; null elsewhere. The
// word is any run of non-blank characters, so a query in another script
// finds a skill by its description.
export function skillAt(text, start, end = start) {
  if (start !== end) return null;
  const before = /^\s*\/(\S*)$/.exec(text.slice(0, start));
  if (!before) return null;
  const after = /^\S*/.exec(text.slice(start))[0];
  return { start: start - before[1].length - 1, end: start + after.length, query: before[1] };
}

// The skill picked at `at` (skillAt): "/name " in place of the word, the
// caret after it.
export function insertSkill(text, at, name) {
  const label = `/${name} `;
  const rest = text.slice(at.end).replace(/^[^\S\n]+/, "");
  return { text: text.slice(0, at.start) + label + rest, caret: at.start + label.length };
}

// A message as typed (the composer's raw text, before quotes or anything
// else go with it), split once as it is sent: when it begins with "/name"
// followed by a blank or its end, and `name` is a skill of `catalog`,
// {skill: {id, name, args}, text: args}, `args` the rest trimmed; else
// {skill: null, text: raw} — an unknown "/word" ("/usr/bin/env", "/ 2") is
// text.
export function splitSkill(raw, catalog = []) {
  const text = String(raw || "");
  const head = /^\s*\/(\S+)/.exec(text);
  const found = head ? (catalog || []).find((s) => s.name === head[1]) : null;
  if (!found) return { skill: null, text };
  const args = text.slice(head[0].length).trim();
  return { skill: { id: found.id, name: found.name, args }, text: args };
}

const wordStart = (s, i) => i === 0 || /[\s\-—–(/]/.test(s[i - 1]);
const range = (from, n) => Array.from({ length: n }, (_, i) => from + i);
const UNSPACED = /\p{Script=Han}|\p{Script=Hiragana}|\p{Script=Katakana}|\p{Script=Thai}/u;
// Where `q` begins a word of `s`, else -1; a query in a script that does
// not set words apart (Chinese, Japanese) matches anywhere in it.
function wordAt(s, q) {
  for (let i = s.indexOf(q); i >= 0; i = s.indexOf(q, i + 1)) if (wordStart(s, i)) return i;
  return UNSPACED.test(q) ? s.indexOf(q) : -1;
}

// How a skill matches the typed query, or null: tiers, best first, its
// name itself, a name it begins, a word of its description as shown, a
// fuzzy match of its name (editor/slashCommands.js fuzzyIndices). `name` /
// `description` are the indices to mark in each.
function skillMatch(row, q) {
  if (row.name.startsWith(q)) return { tier: row.name === q ? 0 : 1, cost: 0, name: range(0, q.length) };
  const shown = skillDescription(row).toLowerCase();
  const at = wordAt(shown, q);
  if (at >= 0) return { tier: 2, cost: at ? 1 : 0, description: range(at, q.length) };
  const fuzzy = fuzzyIndices(row.name, q);
  return fuzzy ? { tier: 3, cost: fuzzy.cost, name: fuzzy.at } : null;
}

// The picker's rows for `query`: every skill in the catalog's order for
// none, else the matches ranked by tier, then cost, then that order, each
// row with its `match`.
export function rankSkills(catalog, query = "") {
  const q = String(query || "").toLowerCase();
  const rows = catalog || [];
  if (!q) return rows.map((row) => ({ ...row, match: null }));
  return rows
    .map((row, i) => [skillMatch(row, q), i, row])
    .filter(([m]) => m)
    .sort(([a, i], [b, j]) => a.tier - b.tier || a.cost - b.cost || i - j)
    .map(([match, , row]) => ({ ...row, match }));
}

// How a message reads outside the model's turn, the same rule as the
// server's (gamma/skills.py display and title; tests/shared/skilltitles.json
// holds both to the same answers). skillText is the whole message, as Copy
// copies it: "/name", then a space and its text, or a blank line first
// when the text opens with a quoted passage; a message that ran none is its
// text. The text is the whole message (its arguments among it), so
// `skill.args` is never read here.
export function skillText(message) {
  const text = String(message?.text || "");
  const name = message?.skill?.name;
  if (!name) return text;
  const rest = text.trim();
  if (!rest) return `/${name}`;
  return `/${name}${rest.startsWith(">") ? "\n\n" : " "}${rest}`;
}

// A conversation's name when the user never gave one, from its first user
// message: its first non-blank line that is not a quote, after "/name" when
// it ran a skill, blanks folded, at most 80 characters.
export function skillTitle(message) {
  const name = message?.skill?.name;
  const text = String(message?.text || "");
  const line = text.split("\n").map((l) => l.trim()).find((l) => l && !l.startsWith(">")) || "";
  return [name ? `/${name}` : "", line].filter(Boolean).join(" ").replace(/\s+/g, " ").slice(0, 80);
}
