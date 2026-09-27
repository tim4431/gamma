// Shared menu primitives. One dismissal + positioning story for every
// cursor-anchored menu in the app (right-click page/folder menu, highlight
// menu, attach-highlight menu), so they can't drift apart again. Rows
// (MenuItem), section headings (MenuLabel), group separators (MenuDivider)
// and nested flyouts (SubMenuItem) live here too, so every menu gets the
// same iconed row and the same submenu-hover behaviour for free — the
// topbar's popover menus (account, View, Add) included, through MenuScope.
import React, { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CheckIcon, ChevronDownIcon, ChevronRightIcon } from "./Icons";
import { useMenuAim } from "./menuAim";
import { t } from "../../shared/i18n/i18n.js";

// Every ContextMenu publishes its submenu state so the rows inside it — at
// any nesting depth — can open/close flyouts without the caller wiring state.
const MenuCtx = createContext(null);

// Shared geometry: minimum gap a menu keeps to the viewport edge, and how far
// a flyout rides above its trigger row so their first items line up.
const VIEWPORT_PAD = 8;
const SUB_TOP_NUDGE = -4;

// A context menu positioned at a screen point (x, y). Rendered through a
// portal so it escapes the window-stack's overflow/stacking contexts, clamps
// itself inside the viewport, and dismisses on outside-pointerdown or Escape.
// Menu items still close the menu themselves via their own onClick.
// anchorRight treats x as the menu's RIGHT edge (dropdowns opening from a
// right-aligned control, e.g. the settings selects).
// anchorBottom treats y as the menu's BOTTOM edge (a dropdown opening upward
// from a control at the bottom of the window, e.g. the chat composer's).
// ignoreRef: element whose pointerdowns must NOT dismiss the menu — the
// dropdown trigger, so its own click can toggle instead of fighting the
// outside-pointerdown dismissal.
function ContextMenu({ x, y, onClose, className = "", anchorRight = false, anchorBottom = false, ignoreRef, children }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ left: x, top: y });

  // Clamp inside the viewport once we know the menu's size.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const pad = VIEWPORT_PAD;
    const place = () => {
      const { width, height } = el.getBoundingClientRect();
      let left = anchorRight ? x - width : x, top = anchorBottom ? y - height : y;
      if (left + width > window.innerWidth - pad) left = Math.max(pad, window.innerWidth - width - pad);
      if (left < pad) left = pad;
      if (top + height > window.innerHeight - pad) top = Math.max(pad, window.innerHeight - height - pad);
      if (top < pad) top = pad;
      setPos((old) => old.left === left && old.top === top ? old : { left, top });
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(el);
    window.addEventListener("resize", place);
    return () => { observer.disconnect(); window.removeEventListener("resize", place); };
  }, [x, y, anchorRight, anchorBottom]);

  useEffect(() => {
    function onDown(e) {
      if (!ref.current || ref.current.contains(e.target)) return;
      if (ignoreRef?.current && ignoreRef.current.contains(e.target)) return;
      onClose();
    }
    function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }
    // Capture phase so we see the click before it lands on other handlers.
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return createPortal(
    <MenuScope
      ref={ref}
      className={`ctxMenu ${className}`}
      style={{ left: pos.left, top: pos.top }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {children}
    </MenuScope>,
    document.body,
  );
}

// The element a menu's rows live in, with the flyout state they share: the
// open flyout (SubMenuItem id) and the pointer-intent guard that keeps a
// diagonal move into it from being read as "hovered the row below". The
// ContextMenu is one; a popover menu under a topbar button (the account
// menu) renders its panel as one so its rows can open flyouts too.
const MenuScope = React.forwardRef(function MenuScope({ children, ...rest }, ref) {
  const [openSub, setOpenSub] = useState(null);
  const aim = useMenuAim();
  // Hovering anywhere in the menu that is NOT the open flyout (or its own
  // trigger) closes it — but only once the cursor stops aiming at it, so the
  // rows the diagonal path crosses don't snatch the hover.
  function onPointerOver(e) {
    if (!openSub) return;
    if (e.target.closest(".ctxSubMenu")) { aim.keep(); return; }
    if (e.target.closest(`[data-submenu="${CSS.escape(openSub)}"]`)) { aim.keep(); return; }
    aim.guard(() => setOpenSub(null));
  }
  return (
    <div ref={ref} {...rest} onPointerOver={onPointerOver}>
      <MenuCtx.Provider value={{ openSub, setOpenSub, aim }}>{children}</MenuCtx.Provider>
    </div>
  );
});

// One menu row: optional leading glyph, label, optional trailing node.
// `danger` tints destructive actions; `keys` shows the row's shortcut on the
// right (a chord label, e.g. "F2"). Everything that isn't a row prop is
// forwarded, so callers keep their own title/disabled/onClick.
function MenuItem({ icon: Icon, children, trailing, keys, danger = false, className = "", ...rest }) {
  return (
    <button
      type="button"
      className={`ctxMenuItem ctxMenuItemIconed ${danger ? "danger" : ""} ${className}`}
      {...rest}
    >
      <span className="ctxMenuIcon">{Icon ? <Icon size={14} /> : null}</span>
      <span className="ctxMenuText">{children}</span>
      {trailing ?? (keys ? <span className="ctxMenuKey">{keys}</span> : null)}
    </button>
  );
}

// Uppercase section heading inside a menu.
function MenuLabel({ children }) {
  return <div className="ctxMenuLabel">{children}</div>;
}

// A thin rule between a menu's groups of rows.
function MenuDivider() {
  return <div className="ctxMenuDivider" role="separator" />;
}

// A menu's groups — arrays of rows; falsy rows and empty groups dropped —
// with a MenuDivider between each two, so a group a viewer cannot use
// leaves no doubled rule behind.
function menuGroups(...groups) {
  const kept = groups.map((g) => g.filter(Boolean)).filter((g) => g.length);
  return kept.flatMap((g, i) => (i ? [<MenuDivider key={`divider-${i}`} />, ...g] : g));
}

// A row that opens a nested flyout on hover (or click/Enter/→ for keyboard
// and touch). The panel renders INSIDE the parent menu's DOM — portalling it
// would put it outside the parent's outside-pointerdown test, and the parent
// would dismiss itself before a click on a flyout row could land. `id` just
// has to be unique within its menu. A click opens it and never toggles it
// shut (the hover that led there has opened it already). The panel is a
// menu named `menuLabel`, else `label`; other props go to the trigger row.
function SubMenuItem({ id, icon: Icon, label, title, menuLabel, children, ...rest }) {
  const { openSub, setOpenSub, aim } = useContext(MenuCtx) || {};
  const open = openSub === id;
  const panelRef = useRef(null);
  const wrapRef = useRef(null);
  const [style, setStyle] = useState({ left: "100%", top: SUB_TOP_NUDGE });

  // Side-flip + vertical clamp: prefer opening right, fall back to left when
  // that would leave the viewport, and slide up so the panel always fits.
  useLayoutEffect(() => {
    if (!open) return;
    const panel = panelRef.current, wrap = wrapRef.current;
    if (!panel || !wrap) return;
    const pad = VIEWPORT_PAD;
    const w = wrap.getBoundingClientRect();
    const p = panel.getBoundingClientRect();
    const flip = w.right + p.width > window.innerWidth - pad && w.left - p.width > pad;
    let top = SUB_TOP_NUDGE;
    if (w.top + top + p.height > window.innerHeight - pad) top = window.innerHeight - pad - p.height - w.top;
    if (w.top + top < pad) top = pad - w.top;
    setStyle(flip ? { right: "100%", top } : { left: "100%", top });
  }, [open]);

  // Hand the settled rect to the pointer-intent guard (and take it back when
  // the flyout closes, so nothing is protected that isn't on screen).
  useLayoutEffect(() => {
    if (!open) { aim?.setTarget(null); return undefined; }
    aim?.setTarget(panelRef.current?.getBoundingClientRect() || null);
    return () => aim?.setTarget(null);
  }, [open, style, aim]);

  return (
    <div className="ctxSubWrap" ref={wrapRef}>
      <MenuItem
        icon={Icon}
        className={`ctxSubTrigger ${open ? "open" : ""}`}
        data-submenu={id}
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
        onPointerEnter={() => aim?.guard(() => setOpenSub(id))}
        onClick={(e) => { e.stopPropagation(); aim?.keep(); setOpenSub(id); }}
        onKeyDown={(e) => { if (e.key === "ArrowRight") { e.preventDefault(); setOpenSub(id); } }}
        trailing={<ChevronRightIcon size={13} className="ctxSubChev" />}
        {...rest}
      >
        {label}
      </MenuItem>
      {open ? (
        <div ref={panelRef} className="ctxMenu ctxSubMenu" style={style} role="menu"
          aria-label={menuLabel || (typeof label === "string" ? label : undefined)}>
          {children}
        </div>
      ) : null}
    </div>
  );
}

// One dropdown story for every choice control (Codex-style): a pill trigger
// showing the current value, opening a ContextMenu below it. The hook gives
// the trigger props (including its ref — passed to ContextMenu as ignoreRef
// so the trigger's own click toggles) plus the open state; MenuSelect and
// ActionMenu are the two shapes built on it.
// `up` opens the menu above the trigger (y is then the menu's bottom edge).
function useDropdown(up = false) {
  const [menu, setMenu] = useState(null); // {x (right edge), y}
  const triggerRef = useRef(null);
  const close = () => setMenu(null);
  const triggerProps = {
    ref: triggerRef,
    onClick: () => {
      if (menu) { close(); return; }
      const r = triggerRef.current.getBoundingClientRect();
      setMenu({ x: r.right, y: up ? r.top - 4 : r.bottom + 4 });
    },
  };
  return [menu, close, triggerProps, triggerRef];
}

// A <select> replacement: options are [value, label] pairs; the menu marks
// the current one with a check. `block` stretches the trigger into a field.
// Options are [value, label] or [value, label, Icon] tuples — with an Icon the
// pill and the menu items lead with the glyph (same look as ActionMenu items).
// `icon` collapses the trigger to that fixed glyph + chevron (no value label) —
// the current choice rides in the tooltip instead. `iconOnly` also removes the
// chevron for especially tight toolbars. `display` replaces the trigger's
// text (the current label by default). A menu holding more than one choice
// gives the main options a `heading` and lists the others as `sections`,
// [{label, value, onChange, options}], each under its own heading — the chat
// composer's model chip with its reasoning effort. `up` opens above the
// trigger; `className` is added to the trigger.
function MenuSelect({ value, onChange, options, label, block, icon: TriggerIcon, iconOnly = false,
  display, heading, sections = [], up = false, className = "" }) {
  const [menu, close, triggerProps, triggerRef] = useDropdown(up);
  const current = options.find(([v]) => v === value) || options[0];
  const CurrentIcon = current?.[2];
  const title = TriggerIcon ? `${t(current?.[1])} — ${label}` : label;
  const choice = (val, lab, OptIcon, selected, pick) => (
    <button key={val} className="ctxMenuItem ctxMenuItemIconed"
      onClick={() => { close(); pick(val); }}>
      {OptIcon ? <span className="ctxMenuIcon"><OptIcon size={14} /></span> : null}
      {t(lab)}
      {selected ? <CheckIcon size={14} className="ctxMenuCheck" /> : null}
    </button>
  );
  return (
    <>
      <button type="button" className={`uiBtn sm uiSelectBtn ${block ? "block" : ""} ${TriggerIcon && iconOnly ? "iconSq" : ""} ${className}`}
        aria-label={title} title={title} {...triggerProps}>
        {TriggerIcon ? (
          <TriggerIcon size={13} />
        ) : (
          <>
            {CurrentIcon ? <CurrentIcon size={13} /> : null}
            <span className="uiSelectLabel">{display ?? t(current?.[1])}</span>
          </>
        )}
        {!iconOnly ? <ChevronDownIcon size={13} className="uiSelectChev" /> : null}
      </button>
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} anchorRight anchorBottom={up} onClose={close} ignoreRef={triggerRef} className="uiSelectMenu">
          {heading ? <MenuLabel>{heading}</MenuLabel> : null}
          {options.map(([val, lab, OptIcon]) => choice(val, lab, OptIcon, val === value, onChange))}
          {sections.map((section) => (
            <React.Fragment key={section.label}>
              <MenuLabel>{section.label}</MenuLabel>
              {section.options.map(([val, lab, OptIcon]) => choice(val, lab, OptIcon, val === section.value, section.onChange))}
            </React.Fragment>
          ))}
        </ContextMenu>
      ) : null}
    </>
  );
}

// A button that opens a small action menu (the Users rows' Export / Import,
// a clone row's "more"). items: [{icon, label, title, onClick, danger?,
// disabled?}]; `iconOnly` makes the trigger a square icon button whose
// label is its tooltip.
function ActionMenu({ label, icon: Icon, items, disabled, iconOnly = false }) {
  const [menu, close, triggerProps, triggerRef] = useDropdown();
  return (
    <>
      <button type="button" className={`uiBtn sm uiSelectBtn ${iconOnly ? "iconSq" : ""}`} disabled={disabled}
        aria-label={t(label)} title={iconOnly ? t(label) : undefined} {...triggerProps}>
        {Icon ? <Icon size={13} /> : null}{iconOnly ? null : t(label)}
        {iconOnly ? null : <ChevronDownIcon size={13} className="uiSelectChev" />}
      </button>
      {menu ? (
        <ContextMenu x={menu.x} y={menu.y} anchorRight onClose={close} ignoreRef={triggerRef}>
          {items.map(({ icon: ItemIcon, label: lab, title, onClick, danger, disabled: off }) => (
            <MenuItem key={lab} icon={ItemIcon} title={t(title)} danger={danger} disabled={off}
              onClick={() => { close(); onClick(); }}>
              {t(lab)}
            </MenuItem>
          ))}
        </ContextMenu>
      ) : null}
    </>
  );
}

export { ContextMenu, MenuScope, MenuItem, MenuLabel, MenuDivider, menuGroups, SubMenuItem, MenuSelect, ActionMenu };
