// A strip group's editor (docs/dev/strip_groups.md), Chrome's bubble under
// a group's chip: the group's name, kept as it is typed (Enter or Esc
// closes), its colour, one of nine, and what may be done with the group:
// Ungroup, Move group to the other pane (split), Close group. A small dialog
// in a menu's surface, which is then no menu (role="none", ui-design.md
// "Menus and submenus"), so its rows are plain buttons.
import React, { useEffect, useRef, useState } from "react";
import { ContextMenu, MenuDivider, MenuItem } from "../shared/ui/Menus";
import { SplitColumnsIcon, UngroupIcon, XIcon } from "../shared/ui/Icons";
import { composing } from "../shared/lib/hotkeys.js";
import { t } from "../shared/i18n/i18n.js";
import { MAX_GROUP_TITLE, STRIP_GROUP_COLORS, groupTint } from "./stripGroups.js";

// A group's colour as a menu row's icon, one component per colour.
export const GROUP_DOTS = Object.fromEntries(STRIP_GROUP_COLORS.map(([color]) => [color, function GroupDot() {
  return <span className="stripGroupDot" style={groupTint(color)} />;
}]));

// `anchor`: where it opens, the chip's menuAnchor (placement.js).
export function StripGroupMenu({ group, anchor, onClose, onChange, onUngroup, onCloseTabs, onMoveToPane }) {
  const [name, setName] = useState(group.title);
  // Focused in the frame after it opens: the menu is laid out hidden until it
  // is placed (placement.js), and a hidden field takes no focus.
  const field = useRef(null);
  useEffect(() => {
    const frame = requestAnimationFrame(() => { field.current?.focus(); field.current?.select(); });
    return () => cancelAnimationFrame(frame);
  }, []);
  const act = (fn) => () => { onClose(); fn(); };
  return (
    <ContextMenu {...anchor} onClose={onClose} role="none" className="stripGroupMenu">
      <div role="dialog" aria-label={t("Tab group")}>
        <input ref={field} className="aiKeyInput" value={name} maxLength={MAX_GROUP_TITLE}
          placeholder={t("Name this group")} aria-label={t("Group name")}
          onChange={(event) => { setName(event.target.value); onChange({ title: event.target.value }); }}
          onKeyDown={(event) => {
            if (composing(event) || event.key !== "Enter") return;
            event.preventDefault();
            onClose();
          }} />
        <div className="colorRow" role="group" aria-label={t("Group color")}>
          {STRIP_GROUP_COLORS.map(([color, label]) => (
            <button key={color} type="button" aria-pressed={color === group.color} title={t(label)} aria-label={t(label)}
              className={`colorBtn stripGroupSwatch${color === group.color ? " selected" : ""}`} style={groupTint(color)}
              onClick={() => onChange({ color })} />
          ))}
        </div>
        <MenuDivider />
        <MenuItem icon={UngroupIcon} title={t("The tabs stay where they are, in no group")} onClick={act(onUngroup)}>{t("Ungroup")}</MenuItem>
        {onMoveToPane ? (
          <MenuItem icon={SplitColumnsIcon} onClick={act(onMoveToPane)}>{t("Move group to the other pane")}</MenuItem>
        ) : null}
        <MenuItem icon={XIcon} onClick={act(onCloseTabs)}>{t("Close group")}</MenuItem>
      </div>
    </ContextMenu>
  );
}
