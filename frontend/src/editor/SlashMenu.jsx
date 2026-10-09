// The "/" command menu's popup in the block editor, Notion-style. The
// catalog and its matching live in editor/slashCommands.js (pure,
// re-exported here); editor/BlockTree.jsx owns the trigger detection,
// keyboard handling and state.
import React, { useEffect } from "react";
import { Matched, useCaretAnchored } from "./LatexEditor";
import { SLASH_GROUPS } from "./slashCommands.js";
import { t } from "../shared/i18n/i18n.js";
import {
  AlignLeftIcon, CalendarIcon, CalloutIcon, DiagramIcon, DividerIcon, EmbedIcon, FilePlusIcon, FileTextIcon,
  GlobeIcon, Heading1Icon, Heading2Icon, Heading3Icon, HighlightIcon, ImageIcon, LinkIcon, ListIcon,
  ListOrderedIcon, NotebookPenIcon, OutlineIcon, QuoteIcon, QuoteMarkIcon, SigmaIcon, SquareCheckIcon,
  SquareCodeIcon, SquareSigmaIcon, TableIcon, TypeIcon,
} from "../shared/ui/Icons";

export { filterSlashCommands } from "./slashCommands.js";

// A row's icon by its item's `icon` key: the commands' (slashCommands.js)
// and the "Paste as" chooser's (BlockTree.jsx pasteAsItems). An item
// without one shows its text `glyph` instead (the colors' tinted "A").
const MENU_ICONS = {
  h1: Heading1Icon, h2: Heading2Icon, h3: Heading3Icon, todo: SquareCheckIcon, bullet: ListIcon,
  number: ListOrderedIcon, quote: QuoteMarkIcon, callout: CalloutIcon, divider: DividerIcon,
  math: SigmaIcon, equation: SquareSigmaIcon, page: FilePlusIcon, note: NotebookPenIcon, table: TableIcon,
  code: SquareCodeIcon, mermaid: DiagramIcon, image: ImageIcon, date: CalendarIcon, link: LinkIcon,
  embed: EmbedIcon, highlight: HighlightIcon,
  url: GlobeIcon, citation: QuoteIcon, pageCard: FileTextIcon, title: TypeIcon, text: AlignLeftIcon,
  blocks: OutlineIcon,
};

// Caret-anchored command popup. A row: the item's icon, its label over a
// one-line description, and for a command the name to type ("/note"), with
// the letters the query matched marked where they matched — in the name or
// in the label (slashCommands.js `match`). Also serves the Notion-style
// "Paste as" chooser after a URL paste (same look, plus a quiet `title`
// line) — blockTree owns both triggers and passes the item lists.
// `commands` is the "/" menu: names and the key-hint footer; `grouped`
// puts the SLASH_GROUPS titles over the bare "/" list.
export function SlashMenuPopup({ items, selected, anchor, onPick, title, grouped, commands }) {
  const [listRef, style] = useCaretAnchored(anchor, false, [items]);
  useEffect(() => {
    listRef.current?.querySelector(".slashMenuItem.selected")
      ?.scrollIntoView({ block: "nearest" });
  }, [selected, listRef]);
  return (
    <div ref={listRef} className="slashMenu" style={style}>
      {title ? <div className="slashMenuTitle">{title}</div> : null}
      {items.map((c, i) => {
        const group = grouped && c.group !== items[i - 1]?.group ? SLASH_GROUPS[c.group] : null;
        const Icon = MENU_ICONS[c.icon];
        return (
          <React.Fragment key={c.name}>
            {group ? <div className="slashMenuTitle">{t(group)}</div> : null}
            <button
              type="button"
              className={`slashMenuItem${i === selected ? " selected" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => onPick(c)}
            >
              <span className="slashMenuIcon" style={c.glyphStyle}>{Icon ? <Icon size={16} /> : c.glyph}</span>
              <span className="slashMenuText">
                <span className="slashMenuHead">
                  <span className="slashMenuLabel"><Matched text={t(c.label)} at={c.match?.label} /></span>
                  {commands ? <span className="slashMenuName">/<Matched text={c.name} at={c.match?.name} /></span> : null}
                </span>
                {c.hint ? <span className="slashMenuHint">{t(c.hint)}</span> : null}
              </span>
            </button>
          </React.Fragment>
        );
      })}
      {commands ? (
        <div className="slashMenuFooter">{t("↑↓ choose · Tab or Enter insert · type “red” for colors")}</div>
      ) : null}
    </div>
  );
}
