// The [[ link picker's popup: anchored at the caret like the "/" menu, pages
// then blocks under their section titles, the typed text marked as in
// Search and Quick Open (MarkedText), and a key-hint footer. editor/BlockTree.jsx owns the trigger, the lists
// (editor/refLists.js) and the keys; `rows` are
// {kind: "page" | "block", id, title, meta, isPdf}.
import React, { useEffect } from "react";
import { useCaretAnchored } from "./LatexEditor";
import { MarkedText } from "../search/SearchPanel";
import { FileGlyph } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

export function RefPickerPopup({ rows, selected, anchor, query, onPick }) {
  const [ref, style] = useCaretAnchored(anchor, false, [rows]);
  useEffect(() => {
    ref.current?.querySelector(".refPopupItem.selected")?.scrollIntoView({ block: "nearest" });
  }, [selected, ref]);
  return (
    <div ref={ref} className="refPopup" data-guide="editor.refSearch" style={style}>
      {rows.map((r, i) => (
        <React.Fragment key={`${r.kind}:${r.id}`}>
          {r.kind !== rows[i - 1]?.kind ? <div className="refPopupHead">{r.kind === "page" ? t("Pages") : t("Blocks")}</div> : null}
          <button
            type="button"
            className={`refPopupItem${i === selected ? " selected" : ""}`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onPick(r)}
          >
            <span className="refPopupIcon">{r.kind === "page" ? <FileGlyph isPdf={r.isPdf} size={16} /> : <span className="refPopupDot" />}</span>
            <span className="refPopupBody">
              <span className="refPopupText"><MarkedText text={r.title} query={query} lead={r.kind === "block" ? 48 : 0} /></span>
              {r.meta ? <span className="refPopupMeta">{r.meta}</span> : null}
            </span>
          </button>
        </React.Fragment>
      ))}
      <div className="refPopupFooter">{t("↑↓ choose · Enter link · Esc keep text")}</div>
    </div>
  );
}
