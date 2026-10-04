// The "/" command catalog and its matching, Notion-style: typing "/" at the
// start of a word opens a list of insertions (headings, to-do, equations,
// code, table, a new page, a sheet of paper, ...). Pure, so
// tests/slashCommands.test.mjs pins the ranking; editor/SlashMenu.jsx holds
// the popup and the icons, editor/BlockTree.jsx the trigger, keys and state.
import { TEXT_COLORS, colorSpan } from "./mdMarks.js";
import { TEXT_INSERTS, replaceRange } from "./slashInserts.js";
import { makeBlockId } from "../shared/model/blockModel.js";
import { t, T } from "../shared/i18n/i18n.js";

// Every command edits through ctx:
//   { value, start, cursor, setText(newVal, selStart, selEnd),
//     openRefPopup(), pickImage(), insertSheet(), newPage(id) }
// start = index of the "/", cursor = caret (end of the typed query); commands
// replace that range with their insertion (the text-only ones are
// slashInserts.js's, which the iPad app runs too). A command that `needs`
// one of the editor's abilities is offered only where the editor has it
// (filterSlashCommands' `can`).
const I = TEXT_INSERTS;

// The bare "/" list's sections, in this order; a typed query is one ranked
// list instead (filterSlashCommands).
export const SLASH_GROUPS = { text: T("Text"), math: T("Math"), insert: T("Insert"), link: T("Link"), style: T("Style") };

// In the bare list's order: a tie in the ranking keeps it. `name` is what
// the row shows to type after the "/", `hint` its one-line description,
// `icon` the popup's icon key (SlashMenu.jsx MENU_ICONS).
export const SLASH_COMMANDS = [
  { name: "h1", group: "text", icon: "h1", label: T("Heading 1"), hint: T("Big section heading"), keywords: ["heading", "title", "header"], run: I.h1 },
  { name: "h2", group: "text", icon: "h2", label: T("Heading 2"), hint: T("Medium section heading"), keywords: ["heading", "subtitle", "header"], run: I.h2 },
  { name: "h3", group: "text", icon: "h3", label: T("Heading 3"), hint: T("Small section heading"), keywords: ["heading", "subheading", "header"], run: I.h3 },
  {
    name: "todo", group: "text", icon: "todo", label: T("To-do"), hint: T("A task with a checkbox to tick"),
    keywords: ["task", "checkbox", "check", "checklist"],
    run: I.todo,
  },
  { name: "bullet", group: "text", icon: "bullet", label: T("Bulleted list"), hint: T("A list with bullet points"), keywords: ["list", "ul", "unordered"], run: I.bullet },
  { name: "number", group: "text", icon: "number", label: T("Numbered list"), hint: T("A list numbered 1, 2, 3"), keywords: ["list", "ol", "ordered"], run: I.number },
  { name: "quote", group: "text", icon: "quote", label: T("Quote"), hint: T("A quotation, set off with a bar"), keywords: ["blockquote", "cite", "quotation"], run: I.quote },
  {
    name: "callout", group: "text", icon: "callout", label: T("Callout"), hint: T("A colored box: note, tip, warning, danger"),
    keywords: ["admonition", "aside", "banner", "note", "tip", "warning"],
    run: I.callout,
  },
  { name: "divider", group: "text", icon: "divider", label: T("Divider"), hint: T("A horizontal line between sections"), keywords: ["hr", "rule", "separator", "line"], run: I.divider },
  {
    name: "math", group: "math", icon: "math", label: T("Inline equation"), hint: T("LaTeX math inside the line of text"),
    keywords: ["equation", "latex", "tex", "formula"],
    run: I.math,
  },
  {
    name: "equation", group: "math", icon: "equation", label: T("Equation block"), hint: T("LaTeX math on a line of its own"),
    keywords: ["display", "math", "latex", "formula"],
    run: I.equation,
  },
  // Notion's /page: a new page in the library, its [[link]] put where the
  // command was typed (under an id minted here, which the page is then made
  // under), and the page opened.
  {
    name: "page", group: "insert", icon: "page", label: T("New page"), hint: T("A new library page, linked here"),
    keywords: ["subpage", "document", "create", "link"],
    needs: "newPage",
    run: (ctx) => { const id = makeBlockId(); replaceRange(ctx, `[[${id}]]`); ctx.newPage(id); },
  },
  {
    name: "note", group: "insert", icon: "note", label: T("Handwritten note"), hint: T("A sheet of paper to write on with a pen"),
    keywords: ["page", "paper", "sheet", "handwriting", "draw", "notebook", "ink", "pen"],
    needs: "sheet",
    run: (ctx) => { replaceRange(ctx, ""); ctx.insertSheet(); },
  },
  {
    name: "table", group: "insert", icon: "table", label: T("Table"), hint: T("A 2×2 table to fill in"),
    keywords: ["grid", "rows", "columns"],
    run: I.table,
  },
  {
    name: "code", group: "insert", icon: "code", label: T("Code block"), hint: T("Code with syntax highlighting"),
    keywords: ["fence", "pre", "snippet"],
    run: I.code,
  },
  {
    name: "mermaid", group: "insert", icon: "mermaid", label: T("Mermaid diagram"), hint: T("A flowchart or sequence diagram from text"),
    keywords: ["diagram", "flowchart", "sequence", "chart", "graph"],
    run: I.mermaid,
  },
  {
    name: "image", group: "insert", icon: "image", label: T("Image"), hint: T("Upload a picture from this device"),
    keywords: ["picture", "photo", "upload", "figure", "img"],
    run: (ctx) => { replaceRange(ctx, ""); ctx.pickImage(); },
  },
  {
    name: "date", group: "insert", icon: "date", label: T("Today's date"), hint: T("Written as YYYY-MM-DD"),
    keywords: ["today", "now", "time", "day"],
    run: I.date,
  },
  {
    name: "link", group: "link", icon: "link", label: T("Link to note"), hint: T("A link to another page or block"),
    keywords: ["ref", "page", "block", "mention"],
    run: (ctx) => { replaceRange(ctx, "[["); ctx.openRefPopup(); },
  },
  {
    name: "embed", group: "link", icon: "embed", label: T("Embed note"), hint: T("Another block shown here, kept in sync"),
    keywords: ["transclude", "include", "block", "synced"],
    run: (ctx) => { replaceRange(ctx, "![["); ctx.openRefPopup(); },
  },
  {
    name: "highlight", group: "style", icon: "highlight", label: T("Highlight text"), hint: T("Mark text like a highlighter pen"),
    keywords: ["mark", "yellow", "emphasize"],
    run: I.highlight,
  },
  // Colored text / background tint, Notion's palette written as Obsidian-
  // compatible inline HTML (mdMarks TEXT_COLORS): an empty span with the
  // caret inside, like the `**|**` of Ctrl+B. `hidden` keeps the sixteen
  // entries out of the bare "/" list — type a color or "color". Their icon
  // is a colored "A" (`glyph`).
  ...TEXT_COLORS.flatMap(({ name, color }) => [false, true].map((background) => ({
    name: `${name}-${background ? "background" : "text"}`,
    group: "style",
    label: `${name[0].toUpperCase()}${name.slice(1)} ${background ? "background" : "text"}`,
    hint: background ? T("Tint the background behind the text") : T("Color the text"),
    glyph: "A", glyphStyle: background ? { background: `${color}55` } : { color },
    keywords: ["color", "colour", background ? "highlight" : "font", name],
    hidden: true,
    run: (ctx) => {
      const open = colorSpan(color, background);
      replaceRange(ctx, `${open}</span>`, open.length);
    },
  }))),
];

// --- matching ---------------------------------------------------------------

const range = (from, n) => Array.from({ length: n }, (_, i) => from + i);
// A word starts a label or a name, or follows a space or a hyphen.
const wordStart = (s, i) => i === 0 || s[i - 1] === " " || s[i - 1] === "-";
// Where `q` begins a word of `s`, else -1.
function wordPrefixAt(s, q) {
  for (let i = s.indexOf(q); i >= 0; i = s.indexOf(q, i + 1)) if (wordStart(s, i)) return i;
  return -1;
}

// The typed letters as a subsequence of `text`, VS Code style, like the
// \command completion's fuzzy tail (latexCompletion.js fuzzyScore):
// anchored on the first letter, every letter in order. A letter continues
// the run, else jumps to the next word that starts with it ("hn" → the
// H and the N of "Handwritten note", "cb" → "Code block"), else takes the
// next place it occurs; when the word jumps strand a later letter, the
// plain leftmost choice is the fallback. Returns the matched indices and a
// cost (lower is better: a run costs nothing, a jump to a word start 1,
// any other jump 2 plus a tenth per skipped letter), or null.
export function fuzzyIndices(text, q) {
  if (q.length < 2 || text[0] !== q[0]) return null;
  const pick = (toWords) => {
    const at = [0];
    for (let i = 1; i < q.length; i++) {
      const last = at[i - 1];
      let j = text[last + 1] === q[i] ? last + 1 : -1;
      for (let k = last + 2; toWords && j < 0 && k < text.length; k++) if (text[k] === q[i] && wordStart(text, k)) j = k;
      if (j < 0) j = text.indexOf(q[i], last + 1);
      if (j < 0) return null;
      at.push(j);
    }
    return at;
  };
  const at = pick(true) || pick(false);
  if (!at) return null;
  let cost = 0;
  for (let i = 1; i < at.length; i++) {
    const gap = at[i] - at[i - 1] - 1;
    cost += gap === 0 ? 0 : wordStart(text, at[i]) ? 1 : 2 + gap * 0.1;
  }
  return { at, cost };
}

// How a command matches the typed query (lowercase), or null. Tiers, best
// first: the command's own name ("/note" is the sheet, not the link or the
// callout with "note" among their words) → a name it begins → a word of
// its label it begins (the label as shown, or the English one) → one of
// its keywords it begins → a fuzzy match of the name or the shown label.
// Letters inside a word match only through the fuzzy tier, so "/red"
// doesn't list the numbered list. `name` / `label` are the indices to
// mark in the row's name and label: what matched, where it is shown.
export function slashMatch(c, q) {
  if (c.name.startsWith(q)) return { tier: c.name === q ? 0 : 1, cost: 0, name: range(0, q.length) };
  const shown = t(c.label).toLowerCase();
  const at = wordPrefixAt(shown, q);
  if (at >= 0) return { tier: 2, cost: at ? 1 : 0, label: range(at, q.length) };
  const atEnglish = wordPrefixAt(c.label.toLowerCase(), q);
  if (atEnglish >= 0) return { tier: 2, cost: atEnglish ? 1 : 0 };
  if (c.keywords?.some((k) => k.startsWith(q))) return { tier: 3, cost: 0 };
  const byName = fuzzyIndices(c.name, q), byLabel = fuzzyIndices(shown, q);
  if (byName && (!byLabel || byName.cost <= byLabel.cost)) return { tier: 4, cost: byName.cost, name: byName.at };
  if (byLabel) return { tier: 4, cost: byLabel.cost, label: byLabel.at };
  return null;
}

// The rows for a "/" query. `can`: the abilities this editor has
// ({newPage, sheet}); a command that needs one it lacks is left out (a
// share's editor makes no library page). The bare "/" lists every command
// but the colors, in catalog order; a query ranks its matches by tier, then
// cost, then catalog order, each row carrying its `match`.
export function filterSlashCommands(query, can = {}) {
  const q = (query || "").toLowerCase();
  const offered = SLASH_COMMANDS.filter((c) => !c.needs || can[c.needs]);
  if (!q) return offered.filter((c) => !c.hidden);
  return offered
    .map((c, i) => [slashMatch(c, q), i, c])
    .filter(([m]) => m)
    .sort(([a, i], [b, j]) => a.tier - b.tier || a.cost - b.cost || i - j)
    .map(([match, , c]) => ({ ...c, match }));
}
