// The object layer of the rendered notes: every image, table and Mermaid
// diagram sits in an MdObject frame. Pressing anywhere in the frame (the
// picture's margins, a table's gutter) selects the object instead of opening
// the block's raw editor; a right-click (or the table's corner handle) opens
// one menu for all three kinds — edit the markdown source, move it to a new
// block or another page, copy it, delete it — and Delete removes a selected
// object. The frame is also the drag source: dragging it carries the
// object's source range (`_dragState.fragment`, read by App's block drop
// handlers), which lands between two blocks as a new block or inside a block
// at the gap the pointer is nearest to. Nothing here touches the stored
// text: every outcome is a source transform in mdObjects.js.
import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import { ContextMenu, MenuItem, SubMenuItem } from "../shared/ui/Menus";
import {
  ArrowDownIcon, ArrowUpIcon, CodeIcon, CopyIcon, FileTextIcon, MoveVerticalIcon, Trash2Icon,
} from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import { scanImages, scanTables } from "./MdTools";
import { scanMermaidFences } from "../shared/lib/mermaidMarkdown.js";
import { scanMathSpans } from "./BlockCmEditor";
import { scanFences } from "./codeHighlight";
import { blockStartInSource, gapInSource, renderedGaps } from "./clickToSource";

// ------------------------------------------------------------ source scan

// The objects of a note in source order: {kind, idx, from, to, editable}.
// `idx` counts per kind in render order (the nth rendered image is the nth
// scanned one — the idiom the hover tools already rely on). Not editable:
// a table inside a blockquote, a diagram inside a list or quote, an
// unfinished fence — they still render in a frame (menu: source + copy).
export function scanObjects(content) {
  const out = [];
  scanImages(content).forEach((im, idx) =>
    out.push({ kind: "image", idx, from: im.from, to: im.to, editable: true }));
  scanTables(content).forEach((tb, idx) =>
    out.push({ kind: "table", idx, from: tb.from, to: tb.to, editable: tb.editable }));
  scanMermaidFences(content).forEach((f, idx) =>
    out.push({ kind: "mermaid", idx, from: f.from, to: f.end, editable: f.closed && !f.prefix.trim() }));
  return out.sort((a, b) => a.from - b.from);
}

export function findObject(content, kind, idx) {
  return scanObjects(content || "").find((o) => o.kind === kind && o.idx === idx) || null;
}

// The gap of a block's rendered view nearest to clientY, as a drop target:
// {offset, y} — the source offset a dropped object is inserted at (a line
// start; null = the block's end) and the gap's screen y for the drop line.
// The gaps are those between the view's top-level constructs (renderedGaps)
// plus one above the first and one below the last. Null when the gap's
// place in the source can't be found.
export function dropGapAtPoint(rendered, content, clientY) {
  const kids = [...rendered.children].filter((k) =>
    !k.hasAttribute("data-markdown-copy-ignore") && !k.classList.contains("mdGapLine") && k.getClientRects().length);
  if (!kids.length) return { offset: null, y: rendered.getBoundingClientRect().bottom };
  const gaps = [{ y: kids[0].getBoundingClientRect().top, below: kids[0] }];
  for (const g of renderedGaps(rendered, null)) gaps.push({ y: g.y, below: g.below });
  gaps.push({ y: kids[kids.length - 1].getBoundingClientRect().bottom, below: null });
  let best = gaps[0];
  for (const g of gaps) if (Math.abs(clientY - g.y) < Math.abs(clientY - best.y)) best = g;
  if (!best.below) return { offset: null, y: best.y };
  const start = constructStartInSource(rendered, content, best.below);
  if (start == null) return null;
  const spans = [...scanMathSpans(content), ...scanFences(content)];
  return { offset: gapInSource(content, start, spans).offset, y: best.y };
}

// Where a top-level construct of the rendered view begins in the source: an
// object frame (or a paragraph opening with one) by its scanned range, so a
// picture without text is found too; anything else by its first text.
function constructStartInSource(rendered, content, el) {
  const frame = el.matches(".mdObject") ? el
    : el.firstElementChild?.matches?.(".mdObject") ? el.firstElementChild : null;
  if (frame) {
    const obj = findObject(content, frame.dataset.kind, Number(frame.dataset.idx));
    if (obj) return obj.from;
  }
  return blockStartInSource(rendered, content, el);
}

// ------------------------------------------------------------- the frame

// Lets a control inside the frame (the table's corner handle) open the
// object menu instead of a menu of its own.
const MdObjectCtx = createContext(null);
export const useObjectMenu = () => useContext(MdObjectCtx);

const KIND_LABELS = {
  image: { delete: () => t("Delete image") },
  table: { delete: () => t("Delete table") },
  mermaid: { delete: () => t("Delete diagram") },
};

// `onAction(kind, idx, action, event)`: editRaw · copy · delete ·
// moveNewAbove · moveNewBelow · moveToPage · dragStart · dragEnd. Without it
// (read-only, an embed card) the frame is inert layout.
export function MdObject({ as: Tag = "div", kind, idx, editable = true, onAction, children }) {
  const ref = useRef(null);
  const [selected, setSelected] = useState(false);
  const [menu, setMenu] = useState(null); // {x, y}
  const active = !!onAction;
  const act = (action, e) => { setMenu(null); onAction?.(kind, idx, action, e); };

  // Selected: a press anywhere else (the menu excepted) or Escape drops it;
  // Delete / Backspace remove the object unless a field has the keyboard.
  useEffect(() => {
    if (!selected) return undefined;
    const onDown = (e) => {
      if (ref.current?.contains(e.target) || e.target.closest?.(".ctxMenu")) return;
      setSelected(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { setSelected(false); setMenu(null); return; }
      if (e.key !== "Delete" && e.key !== "Backspace") return;
      const a = document.activeElement;
      if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable)) return;
      if (!editable) return;
      e.preventDefault();
      e.stopPropagation();
      setSelected(false);
      act("delete", e);
    };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  });

  const openMenu = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setSelected(true);
    setMenu({ x: e.clientX, y: e.clientY });
  };

  const cls = `mdObject mdObject-${kind}${selected ? " mdObjectSelected" : ""}`;
  if (!active) return <Tag className={cls} data-kind={kind} data-idx={idx}>{children}</Tag>;
  return (
    <Tag
      ref={ref}
      className={cls}
      data-kind={kind}
      data-idx={idx}
      draggable={editable ? "true" : undefined}
      // The block row opens its raw editor on mousedown; a press on the
      // object (its margins included) selects it instead.
      onMouseDown={(e) => { e.stopPropagation(); if (e.button === 0) setSelected(true); }}
      onContextMenu={openMenu}
      onDragStart={(e) => {
        e.stopPropagation();
        // The resize grips and the table's handle pills drag with pointer
        // events of their own; a native drag starting there would cancel
        // them (pointercancel) — so it doesn't start.
        if (e.target.closest?.(".mdResizeGrip, .mdTableHandle, .mdTableAdd, button, input, a")) {
          e.preventDefault();
          return;
        }
        onAction(kind, idx, "dragStart", e);
      }}
      onDragEnd={(e) => { setSelected(false); onAction(kind, idx, "dragEnd", e); }}
    >
      <MdObjectCtx.Provider value={{ openMenu }}>{children}</MdObjectCtx.Provider>
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)}>
          <MenuItem icon={CodeIcon} title={t("Open the block's editor with the caret on this object's markdown")}
            onClick={(e) => act("editRaw", e)}>{t("Edit markdown source")}</MenuItem>
          {editable ? (
            <SubMenuItem id="move" icon={MoveVerticalIcon} label={t("Move to")}>
              <MenuItem icon={ArrowUpIcon} onClick={(e) => act("moveNewAbove", e)}>{t("New block above")}</MenuItem>
              <MenuItem icon={ArrowDownIcon} onClick={(e) => act("moveNewBelow", e)}>{t("New block below")}</MenuItem>
              <MenuItem icon={FileTextIcon} onClick={(e) => act("moveToPage", e)}>{t("Another page…")}</MenuItem>
            </SubMenuItem>
          ) : null}
          <MenuItem icon={CopyIcon} onClick={(e) => act("copy", e)}>{t("Copy as markdown")}</MenuItem>
          {editable ? (
            <MenuItem danger icon={Trash2Icon} onClick={(e) => act("delete", e)}>{KIND_LABELS[kind].delete()}</MenuItem>
          ) : null}
        </ContextMenu>
      ) : null}
    </Tag>
  );
}
