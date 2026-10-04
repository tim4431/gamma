// The "/" command menu in the block editor, Notion-style: typing "/" at the
// start of a word opens a filterable list of insertions (link, equations,
// headings, to-do, code, table, ...). Pure catalog + a presentational popup;
// editor/BlockTree.jsx owns the trigger detection, keyboard handling and state.
import React, { useEffect } from "react";
import { useCaretAnchored } from "./LatexEditor";
import { TEXT_COLORS, colorSpan } from "./mdMarks";
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

export const SLASH_COMMANDS = [
  {
    name: "link", label: T("Link to note"), glyph: "[[", hint: T("reference another block"),
    keywords: ["ref", "page", "block", "mention"],
    run: (ctx) => { replaceRange(ctx, "[["); ctx.openRefPopup(); },
  },
  {
    name: "embed", label: T("Embed note"), glyph: "⧉", hint: T("show a block inline"),
    keywords: ["transclude", "include", "block"],
    run: (ctx) => { replaceRange(ctx, "![["); ctx.openRefPopup(); },
  },
  {
    name: "highlight", label: T("Highlight text"), glyph: "==", hint: "==marked==",
    keywords: ["mark", "yellow", "emphasize"],
    run: I.highlight,
  },
  {
    name: "math", label: T("Inline equation"), glyph: "$x$", hint: T("LaTeX, rendered in place"),
    keywords: ["equation", "latex", "tex"],
    run: I.math,
  },
  {
    name: "equation", label: T("Equation block"), glyph: "$$", hint: T("display math"),
    keywords: ["display", "math", "latex"],
    run: I.equation,
  },
  { name: "h1", label: T("Heading 1"), glyph: "H1", keywords: ["heading", "title"], run: I.h1 },
  { name: "h2", label: T("Heading 2"), glyph: "H2", keywords: ["heading"], run: I.h2 },
  { name: "h3", label: T("Heading 3"), glyph: "H3", keywords: ["heading"], run: I.h3 },
  {
    name: "todo", label: T("To-do"), glyph: "☐", hint: T("checkbox item"),
    keywords: ["task", "checkbox", "check"],
    run: I.todo,
  },
  { name: "bullet", label: T("Bulleted list"), glyph: "•", keywords: ["list", "ul"], run: I.bullet },
  { name: "number", label: T("Numbered list"), glyph: "1.", keywords: ["list", "ol", "ordered"], run: I.number },
  { name: "quote", label: T("Quote"), glyph: "❝", keywords: ["blockquote", "cite"], run: I.quote },
  {
    name: "callout", label: T("Callout"), glyph: "[!]", hint: T("note · tip · warning · danger"),
    keywords: ["admonition", "aside", "banner", "note", "tip", "warning"],
    run: I.callout,
  },
  {
    name: "code", label: T("Code block"), glyph: "</>", hint: T("fenced code"),
    keywords: ["fence", "pre", "snippet"],
    run: I.code,
  },
  {
    name: "mermaid", label: T("Mermaid diagram"), glyph: "◇", hint: T("flowchart or sequence diagram"),
    keywords: ["diagram", "flowchart", "sequence", "chart"],
    run: I.mermaid,
  },
  { name: "divider", label: T("Divider"), glyph: "—", keywords: ["hr", "rule", "separator", "line"], run: I.divider },
  {
    name: "table", label: T("Table"), glyph: "▦", hint: T("2×2 markdown table"),
    keywords: ["grid"],
    run: I.table,
  },
  {
    name: "image", label: T("Image"), glyph: "▣", hint: T("upload from disk"),
    keywords: ["picture", "photo", "upload", "figure"],
    run: (ctx) => { replaceRange(ctx, ""); ctx.pickImage(); },
  },
  // Notion's /page: a new page in the library, its [[link]] put where the
  // command was typed (under an id minted here, which the page is then made
  // under), and the page opened.
  {
    name: "page", label: T("New page"), glyph: "↗", hint: T("a new library page, linked here"),
    keywords: ["subpage", "document", "create", "link"],
    needs: "newPage",
    run: (ctx) => { const id = makeBlockId(); replaceRange(ctx, `[[${id}]]`); ctx.newPage(id); },
  },
  {
    name: "note", label: T("Handwritten note"), glyph: "▯", hint: T("a sheet of paper for handwriting"),
    keywords: ["page", "paper", "sheet", "handwriting", "draw", "notebook", "ink", "pen"],
    needs: "sheet",
    run: (ctx) => { replaceRange(ctx, ""); ctx.insertSheet(); },
  },
  { name: "date", label: T("Today's date"), glyph: "@", keywords: ["today", "now", "time"], run: I.date },
  // Colored text / background tint, Notion's palette written as Obsidian-
  // compatible inline HTML (mdMarks TEXT_COLORS): an empty span with the
  // caret inside, like the `**|**` of Ctrl+B. `hidden` keeps the sixteen
  // entries out of the bare "/" list — type a color or "color".
  ...TEXT_COLORS.flatMap(({ name, color }) => [false, true].map((background) => ({
    name: `${name}-${background ? "background" : "text"}`,
    label: `${name[0].toUpperCase()}${name.slice(1)} ${background ? "background" : "text"}`,
    glyph: "A", glyphStyle: background ? { background: `${color}55`, borderRadius: 3 } : { color },
    keywords: ["color", "colour", background ? "highlight" : "font", name],
    hidden: true,
    run: (ctx) => {
      const open = colorSpan(color, background);
      replaceRange(ctx, `${open}</span>`, open.length);
    },
  }))),
];

// The bare "/" list comes in these groups, in this order; a typed query is
// one ranked list instead (below). The colours belong to Style but stay
// hidden until typed.
const SLASH_GROUPS = [
  { label: T("Text"), names: ["h1", "h2", "h3", "todo", "bullet", "number", "quote", "callout", "divider"] },
  { label: T("Math"), names: ["math", "equation"] },
  { label: T("Insert"), names: ["page", "note", "table", "code", "mermaid", "image", "date"] },
  { label: T("Link"), names: ["link", "embed"] },
  { label: T("Style"), names: ["highlight"] },
];
const GROUP_OF = new Map(SLASH_GROUPS.flatMap((g) => g.names.map((n) => [n, g])));

// `can`: the abilities this editor has ({newPage, sheet}); a command that
// needs one it lacks is left out (a share's editor makes no library page).
export function filterSlashCommands(query, can = {}) {
  const q = (query || "").toLowerCase();
  const offered = (c) => !c.needs || can[c.needs];
  if (!q) return SLASH_GROUPS.flatMap((g) => g.names.map((n) => SLASH_COMMANDS.find((c) => c.name === n))).filter(offered);
  const scored = [];
  for (const c of SLASH_COMMANDS.filter(offered)) {
    const names = [c.name, ...(c.keywords || []), ...c.label.toLowerCase().split(/\s+/), ...t(c.label).toLowerCase().split(/\s+/)];
    // a command's own name first ("/note" is the sheet, not the link or the
    // callout with "note" among their words), then any word starting with
    // the query
    const tier = c.name === q ? 0 : names.some((n) => n.startsWith(q)) ? 1
      : names.some((n) => n.includes(q)) ? 2 : -1;
    if (tier >= 0) scored.push([tier, scored.length, c]);
  }
  scored.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return scored.map((x) => x[2]);
}

// Caret-anchored command popup. Also serves the Notion-style "Paste as"
// chooser after a URL paste (same look, plus a quiet `title` line) —
// blockTree owns both triggers and passes the item lists. `grouped` puts
// the SLASH_GROUPS titles over the bare "/" list; `footer` is the "/"
// menu's key hint line.
export function SlashMenuPopup({ items, selected, anchor, onPick, title, grouped, footer }) {
  const [listRef, style] = useCaretAnchored(anchor, false, [items]);
  useEffect(() => {
    listRef.current?.querySelector(".slashMenuItem.selected")
      ?.scrollIntoView({ block: "nearest" });
  }, [selected, listRef]);
  return (
    <div ref={listRef} className="slashMenu" style={style}>
      {title ? <div className="slashMenuTitle">{title}</div> : null}
      {items.map((c, i) => {
        const group = grouped ? GROUP_OF.get(c.name) : null;
        return (
          <React.Fragment key={c.name}>
            {group && group !== GROUP_OF.get(items[i - 1]?.name) ? <div className="slashMenuTitle">{t(group.label)}</div> : null}
            <button
              type="button"
              className={`slashMenuItem${i === selected ? " selected" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onPick(c)}
            >
              <span className="slashMenuGlyph" style={c.glyphStyle}>{c.glyph}</span>
              <span className="slashMenuLabel">{t(c.label)}</span>
              {c.hint ? <span className="slashMenuHint">{t(c.hint)}</span> : null}
            </button>
          </React.Fragment>
        );
      })}
      {footer ? (
        <div className="slashMenuFooter">{t("↑↓ choose · Enter insert · type “red” for colors")}</div>
      ) : null}
    </div>
  );
}
