// A selection popup's own buttons, beside its marks: the PDF's (pdf/
// PdfViewer.jsx PlainTip), the code view's (documents/viewers/TextTip.jsx)
// and a picture's region tip (documents/viewers/FileViews.jsx RegionTip)
// are each the row of marks (pdf/MarkTools.jsx TipMarks) and these, in
// TipFrame. Every button acts on the press, which keeps the selection
// (press.js tipPress; TipMarks takes it as its `act`). Imports no viewer,
// so the code view's chunk takes it without pdf.js.
import React from "react";
import { CheckIcon, CopyIcon, LocateIcon, MessageSquareIcon } from "./Icons";
import { IconSwap } from "./MotionWidgets";
import { useCopied } from "./Widgets";
import { usePalette } from "./paletteContext.js";
import { keepSelection, tipPress } from "./press.js";
import { pickerColors } from "../model/palette.js";
import { copyText } from "../lib/utils";
import { t } from "../i18n/i18n.js";

// One of the popup's buttons: its icon; `label` its name, and its title
// unless `title` says more; `onPress(event)` what a press does; `selected`
// while what it opened shows (the translation).
export function TipTool({ label, title = label, onPress, selected = false, className = "", children, ...rest }) {
  return (
    <button type="button" className={["colorBtn linkTipBtn", className, selected ? "selected" : ""].filter(Boolean).join(" ")}
      title={title} aria-label={label} {...rest} {...tipPress(onPress)}>
      {children}
    </button>
  );
}

// The line between the marks and the tools.
export function TipDivider() {
  return <span className="tipDivider" aria-hidden="true" />;
}

// The popup's whole row while a highlight waits to be moved (App's move
// mode): this selection is its new place, nothing else is offered.
export function TipMoveHere({ onMove }) {
  return (
    <div className="colorRow">
      <TipTool className="tipMoveBtn" label={t("Move the highlight here")} onPress={onMove}>
        <LocateIcon size={14} />
      </TipTool>
    </div>
  );
}

// Copy `text` (what was selected), where the host's own edit menu, which
// has one, is off: on the click, as the system's Copy; a check for a moment.
export function TipCopy({ text }) {
  const [copied, flashCopied] = useCopied(1200);
  return (
    <button type="button" className="colorBtn linkTipBtn" title={t("Copy")} aria-label={t("Copy")} onMouseDown={keepSelection}
      onClick={async () => { if (await copyText(text)) flashCopied(); }}>
      <IconSwap swap={copied} from={<CopyIcon size={14} />} to={<CheckIcon size={14} />} />
    </button>
  );
}

// Comment: `onComment(color)` with the first swatch's colour, the look of
// the highlight a comment on a selection makes. `label` says on what.
export function TipComment({ label = t("Comment on this passage"), onComment }) {
  const colors = pickerColors(usePalette(), "highlight");
  return (
    <TipTool label={label} onPress={() => onComment(colors[0])}>
      <MessageSquareIcon size={14} />
    </TipTool>
  );
}
