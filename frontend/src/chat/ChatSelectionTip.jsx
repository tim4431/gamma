// Text selected in the chat's transcript (docs/dev/ai.md "Selecting the
// transcript"): a popup under it, as over a PDF's or a code view's text
// (shared/ui/TipFrame.jsx, pdf/selectionTip.js: a mouse's waits for the hand
// to move on from the release, a finger's once its handles settle), whose
// buttons are every action at once: Ask about this, Copy, and where a note
// can be made Create note and Structured note. A right-click on the
// selection opens the same as a menu at the pointer: Ask about this, Copy,
// and Create note ▸ As it is / Structured by AI.
//
// What every action takes is the selection's Markdown source
// (shared/ui/Widgets.jsx selectionMarkdown), read when the popup or the
// menu opens, so it acts on it even after the press took the selection
// away. `box`: the transcript's scroll box (null while none); `onAsk(md)` quotes
// it into the composer, `onCopy(md, html)` copies it (true once it has),
// `note` how a note is made here — {where: where it goes, structured: "" or
// why the AI can't, make(md, structured)} — or null where no note can be made.
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { selectionMarkdown } from "../shared/ui/Widgets";
import { TipFrame } from "../shared/ui/TipFrame.jsx";
import { TipCopy, TipDivider, TipTool } from "../shared/ui/SelectionTip.jsx";
import { ContextMenu, MenuItem, SubMenuItem, menuGroups } from "../shared/ui/Menus";
import { keepSelection } from "../shared/ui/press.js";
import { nearRelease, tipAnchor, tipWaiter } from "../pdf/selectionTip.js";
import { lastPointer } from "../shared/lib/pointer.js";
import { AlignLeftIcon, CopyIcon, MessageSquareIcon, NotebookPenIcon, SparklesIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

// A finger's selection is read once it has been still this long (ms), as
// the code view's is.
const SETTLE_MS = 350;
const TIP = "chatSelTip";
const inTip = (node) => {
  const el = node?.nodeType === 3 ? node.parentElement : node;
  return !!el?.closest?.(`.${TIP}, .chatSelMenu`);
};

// The transcript's selection: a range that starts in a message of `box`
// (not in an edited message's field), cut at the transcript's end when it
// runs past it (a triple click on the last message's last line ends it in
// what follows), or null.
export function transcriptRange(box, sel = window.getSelection()) {
  if (!box || !sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  const el = (node) => (node?.nodeType === 3 ? node.parentElement : node);
  const start = el(range.startContainer), end = el(range.endContainer);
  if (!start || !end || !box.contains(start)) return null;
  if (!start.closest(".chatBubble") || start.closest("textarea, input, .chatEditBubble")) return null;
  if (box.contains(end)) return range;
  const cut = range.cloneRange();
  cut.setEnd(box, box.childNodes.length);
  return cut;
}

export default function ChatSelectionTip({ box, onAsk, onCopy, note = null }) {
  const [tip, setTip] = useState(null); // {md, html, rect, point, scroll}
  const [menu, setMenu] = useState(null); // {md, html, x, y}
  const latest = useRef(null);
  latest.current = { menu };

  useEffect(() => {
    if (!box) return undefined;
    const at = () => box.scrollTop;
    const waiter = tipWaiter((next) => {
      if (next.scroll !== at() || !transcriptRange(box)) return;
      setTip(next);
    });
    // `point` where a mouse let go of it.
    function sync(point = null) {
      waiter.drop();
      const range = transcriptRange(box);
      if (!range) { setTip(null); return; }
      const { md, html } = selectionMarkdown(range);
      if (!md) { setTip(null); return; }
      const r = range.getBoundingClientRect();
      const rect = { top: r.top, left: r.left, width: r.width, bottom: r.bottom };
      const next = { md, html, rect, point: nearRelease(point, rect), scroll: at() };
      if (next.point && lastPointer() === "mouse") waiter.wait(next);
      else setTip(next);
    }
    function onMouseDown(e) {
      waiter.press();
      if (!inTip(e.target)) setTip(null);
    }
    function onMouseMove(e) { if (!e.buttons) waiter.move(e.clientX, e.clientY); }
    function onMouseUp(e) {
      if (e.button !== 0 || inTip(e.target)) return;
      const point = { x: e.clientX, y: e.clientY };
      setTimeout(() => sync(point), 10);
    }
    let timer = null;
    function onSelectionChange() {
      if (latest.current.menu) return;
      if (!transcriptRange(box)) { clearTimeout(timer); waiter.drop(); setTip(null); return; }
      if (lastPointer() !== "mouse") { clearTimeout(timer); timer = setTimeout(() => sync(), SETTLE_MS); }
    }
    function onScroll() { setTip((p) => (p && p.scroll !== at() ? null : p)); }
    // A right-click on the selection opens its menu where the system's would.
    function onContextMenu(e) {
      const range = transcriptRange(box);
      if (!range || !Array.from(range.getClientRects()).some((r) => e.clientX >= r.left - 2 && e.clientX <= r.right + 2
        && e.clientY >= r.top - 2 && e.clientY <= r.bottom + 2)) return;
      const { md, html } = selectionMarkdown(range);
      if (!md) return;
      e.preventDefault();
      setTip(null);
      setMenu({ md, html, x: e.clientX, y: e.clientY });
    }
    document.addEventListener("mousedown", onMouseDown, true);
    document.addEventListener("mousemove", onMouseMove, { passive: true });
    document.addEventListener("mouseup", onMouseUp);
    document.addEventListener("selectionchange", onSelectionChange);
    box.addEventListener("scroll", onScroll, { passive: true });
    // ui-allow context-menu: a hold on the text is the system's selection, so a finger or a pen has these rows as the popup's buttons
    box.addEventListener("contextmenu", onContextMenu);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("mousedown", onMouseDown, true);
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.removeEventListener("selectionchange", onSelectionChange);
      box.removeEventListener("scroll", onScroll);
      box.removeEventListener("contextmenu", onContextMenu);
    };
  }, [box]);

  // A row's act: the menu and the popup put away, the selection with them
  // when it was quoted or made a note of.
  const done = (clear) => {
    setMenu(null);
    setTip(null);
    if (clear) window.getSelection()?.removeAllRanges();
  };
  const ask = (md) => { done(true); onAsk(md); };
  const makeNote = (md, structured) => { done(true); note.make(md, structured); };
  const structuredTitle = note?.structured || t("Rewritten by the AI into headings and bullet points, with nothing added");
  return (
    <>
      {tip ? createPortal(
        <TipFrame anchor={tipAnchor(tip)}>
          <div className={`plainTip ${TIP}`} onMouseDown={keepSelection}>
            <div className="colorRow">
              <TipTool label={t("Ask about this")} title={t("Quote the selection in your next message")} onPress={() => ask(tip.md)}>
                <MessageSquareIcon size={14} />
              </TipTool>
              <TipCopy copy={() => onCopy(tip.md, tip.html)} />
              {note ? (
                <>
                  <TipDivider />
                  <TipTool label={t("Create note")} title={`${t("Create note")}\n${note.where}`} onPress={() => makeNote(tip.md, false)}>
                    <NotebookPenIcon size={14} />
                  </TipTool>
                  <TipTool label={t("Structured note")} title={`${t("Structured note")}\n${structuredTitle}`} disabled={!!note.structured}
                    onPress={() => makeNote(tip.md, true)}>
                    <SparklesIcon size={14} />
                  </TipTool>
                </>
              ) : null}
            </div>
          </div>
        </TipFrame>, document.body) : null}
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} className="chatSelMenu">
          {menuGroups([
            <MenuItem key="ask" icon={MessageSquareIcon} onClick={() => ask(menu.md)}>{t("Ask about this")}</MenuItem>,
            <MenuItem key="copy" icon={CopyIcon} onClick={() => { done(false); onCopy(menu.md, menu.html); }}>{t("Copy")}</MenuItem>,
          ], [
            note ? (
              <SubMenuItem key="note" id="note" icon={NotebookPenIcon} label={t("Create note")} title={note.where}>
                <MenuItem icon={AlignLeftIcon} title={t("The selected text as it reads here")}
                  onClick={() => makeNote(menu.md, false)}>{t("As it is")}</MenuItem>
                <MenuItem icon={SparklesIcon} disabled={!!note.structured} title={structuredTitle}
                  onClick={() => makeNote(menu.md, true)}>{t("Structured by AI")}</MenuItem>
              </SubMenuItem>
            ) : null,
          ])}
        </ContextMenu>
      ) : null}
    </>
  );
}
