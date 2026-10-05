// What the "/" menu's insertions do to a note's text, by command name:
// editor/slashCommands.js lists them with their labels, and the iPad app's
// editing bar runs the same ones (ipad/core/entry.js `insert`). Pure, with
// no imports: the iPad's JavaScriptCore loads it without the catalog's
// i18n (and the React that brings).
//
// Each takes the menu's ctx, { value, start, cursor, setText(newVal,
// selStart, selEnd) }: start is the index of the "/", cursor the caret (the
// end of the typed query), and the insertion replaces that range.

export function replaceRange(ctx, text, caretRel, selLen = 0) {
  const { value, start, cursor } = ctx;
  const newVal = value.slice(0, start) + text + value.slice(cursor);
  const caret = start + (caretRel != null ? caretRel : text.length);
  ctx.setText(newVal, caret, caret + selLen);
}

// Turn the current line into `prefix` + its text (swapping out an existing
// markdown line prefix, so /h2 on a "# heading" re-levels instead of stacking).
const LINE_PREFIX_RE = /^(#{1,6} |> |[-*+] \[[ xX]\] |[-*+] |\d+\. )/;
function applyLinePrefix(ctx, prefix) {
  const { value, start, cursor } = ctx;
  let v = value.slice(0, start) + value.slice(cursor);
  const lineStart = v.lastIndexOf("\n", start - 1) + 1;
  const rest = v.slice(lineStart);
  const m = rest.match(LINE_PREFIX_RE);
  const stripped = m ? rest.slice(m[0].length) : rest;
  v = v.slice(0, lineStart) + prefix + stripped;
  const caret = Math.max(lineStart + prefix.length, start - (m ? m[0].length : 0) + prefix.length);
  ctx.setText(v, caret, caret);
}

// Insertions that want their own line (divider, code block, table) prepend a
// newline unless the "/" already sat at a line start.
function blockInsert(ctx, body, caretRelInBody, selLen = 0) {
  const atLineStart = ctx.start === 0 || ctx.value[ctx.start - 1] === "\n";
  const lead = atLineStart ? "" : "\n";
  replaceRange(ctx, lead + body, caretRelInBody != null ? lead.length + caretRelInBody : null, selLen);
}

// The local calendar day (toISOString would give UTC's, a day off in the
// evening west of Greenwich).
function today() {
  const d = new Date(), pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const TABLE_MD = "| Column 1 | Column 2 |\n| --- | --- |\n|   |   |";
const MERMAID_MD = "```mermaid\nflowchart LR\n  A[Start] --> B[Finish]\n```";

// The insertions that change only the text (the rest open a popup, pick a
// file or make a page or a sheet).
export const TEXT_INSERTS = {
  highlight: (ctx) => replaceRange(ctx, "==x==", 2, 1),
  math: (ctx) => replaceRange(ctx, "$x$", 1, 1),
  equation: (ctx) => replaceRange(ctx, "$$x$$", 2, 1),
  h1: (ctx) => applyLinePrefix(ctx, "# "),
  h2: (ctx) => applyLinePrefix(ctx, "## "),
  h3: (ctx) => applyLinePrefix(ctx, "### "),
  todo: (ctx) => applyLinePrefix(ctx, "- [ ] "),
  bullet: (ctx) => applyLinePrefix(ctx, "- "),
  number: (ctx) => applyLinePrefix(ctx, "1. "),
  quote: (ctx) => applyLinePrefix(ctx, "> "),
  callout: (ctx) => applyLinePrefix(ctx, "> [!note] "),
  code: (ctx) => blockInsert(ctx, "```\n\n```", 4),
  mermaid: (ctx) => blockInsert(ctx, MERMAID_MD, MERMAID_MD.indexOf("Start"), 5),
  divider: (ctx) => blockInsert(ctx, "---\n"),
  table: (ctx) => blockInsert(ctx, TABLE_MD, 2, 8),
  date: (ctx) => replaceRange(ctx, today()),
};
