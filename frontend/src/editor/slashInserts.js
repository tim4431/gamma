// What the "/" menu's insertions do to a note's text, by command name:
// editor/slashCommands.js lists them with their labels, and the iPad app's
// editing bar runs the same ones (ipad/core/entry.js `insert`). Pure, and
// importing only the pure mdLines.js: the iPad's JavaScriptCore loads it
// without the catalog's i18n (and the React that brings).
import { retypeLine } from "./mdLines.js";
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

// Give the current line another kind (mdLines.retypeLine): its marker is
// swapped, not stacked (/h2 on a "# heading" re-levels it), and it keeps
// its quote marks and indentation (/todo on "> - item" is "> - [ ] item").
function retype(ctx, kind) {
  const { value, start, cursor } = ctx;
  const v = value.slice(0, start) + value.slice(cursor);
  const from = v.lastIndexOf("\n", start - 1) + 1;
  const nl = v.indexOf("\n", start);
  const to = nl < 0 ? v.length : nl;
  const above = from ? v.slice(v.lastIndexOf("\n", from - 2) + 1, from - 1) : "";
  const r = retypeLine(v.slice(from, to), start - from, kind, above);
  ctx.setText(v.slice(0, from) + r.line + v.slice(to), from + r.caret, from + r.caret);
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
  h1: (ctx) => retype(ctx, "h1"),
  h2: (ctx) => retype(ctx, "h2"),
  h3: (ctx) => retype(ctx, "h3"),
  todo: (ctx) => retype(ctx, "todo"),
  bullet: (ctx) => retype(ctx, "bullet"),
  number: (ctx) => retype(ctx, "number"),
  quote: (ctx) => retype(ctx, "quote"),
  callout: (ctx) => retype(ctx, "callout"),
  code: (ctx) => blockInsert(ctx, "```\n\n```", 4),
  mermaid: (ctx) => blockInsert(ctx, MERMAID_MD, MERMAID_MD.indexOf("Start"), 5),
  divider: (ctx) => blockInsert(ctx, "---\n"),
  table: (ctx) => blockInsert(ctx, TABLE_MD, 2, 8),
  date: (ctx) => replaceRange(ctx, today()),
};
