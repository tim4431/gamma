// Chrome's tab groups in the strip (docs/dev/strip_groups.md): what App does
// with them. The groups are on the synced tabs themselves (stripGroups.js);
// this owner holds only which group's editor is open, and gives App:
// - `tabMenuItems(tabId, close)`: a tab menu's rows, Add tab to (new) group and
//   Remove from group;
// - `strip`: what a strip's chips do, for OpenTabs;
// - `editor`: the open editor (StripGroupMenu), drawn once by App.
// A page picked while its tab is in a collapsed group opens the group.
//
// `stripOf(tabId)`: the tabs of the strip a tab is in, as it shows them (a
// group's other tabs are the ones in its strip); `closeTabs(ids)`;
// `otherPane(tabId)`: the pane a tab is not in, while split, else null;
// `moveTabsToPane(ids, paneId)`.
import React, { useEffect, useRef, useState } from "react";
import { MenuDivider, MenuItem, SubMenuItem } from "../shared/ui/Menus";
import { PlusIcon, TabGroupIcon, UngroupIcon } from "../shared/ui/Icons";
import { menuAnchor } from "../shared/ui/placement.js";
import { arriving } from "../shared/ui/motion.js";
import { makeId } from "../shared/model/blockModel.js";
import { t } from "../shared/i18n/i18n.js";
import { groupTab, groupsIn, joinGroup, leaveGroup, membersOf, setGroup, ungroup } from "./stripGroups.js";
import { GROUP_DOTS, StripGroupMenu } from "./StripGroupMenu.jsx";

export function useStripGroups({ tabs, updateTabs, shownId, stripOf, closeTabs, otherPane, moveTabsToPane }) {
  const [editor, setEditor] = useState(null); // {groupId, anchor}: the chip's menuAnchor
  // Chrome opens a collapsed group whose tab is picked from elsewhere (the
  // all-tabs list, quick open, a link). A collapse moves off the group first,
  // so it never comes here; and the page a load restores was not picked: a
  // group another device collapsed stays so, its tab in sight in the strip.
  const restored = useRef(false);
  useEffect(() => {
    if (!shownId) return;
    if (!restored.current) { restored.current = true; return; }
    const group = tabs.find((tab) => tab.id === shownId)?.group;
    if (group?.collapsed) updateTabs((prev) => setGroup(prev, group.id, { collapsed: false }));
  }, [shownId]); // eslint-disable-line react-hooks/exhaustive-deps

  const openEditor = (groupId, chip) => setEditor({ groupId, anchor: menuAnchor(chip.getBoundingClientRect()) });
  // A new group's editor opens under its chip once the strip has drawn it
  // and the chip has risen into its place (ListMotion).
  const editNew = (groupId) => requestAnimationFrame(async () => {
    const chip = document.querySelector(`[data-strip-group="${CSS.escape(groupId)}"]`);
    if (!chip) return;
    await Promise.allSettled(arriving(chip).map((a) => a.finished));
    if (chip.isConnected) openEditor(groupId, chip);
  });

  const tabMenuItems = (tabId, close) => {
    const tab = tabs.find((t) => t.id === tabId);
    if (!tab || tab.pinned) return null; // Chrome's rule: a pinned tab is in no group
    const strip = stripOf(tab.id);
    const others = groupsIn(strip).filter((group) => group.id !== tab.group?.id);
    const make = () => {
      close();
      const id = makeId();
      updateTabs((prev) => groupTab(prev, tab.id, id, strip));
      editNew(id);
    };
    return (
      <>
        <MenuDivider />
        {others.length ? (
          <SubMenuItem id="strip-group" icon={TabGroupIcon} label={t("Add tab to group")}>
            <MenuItem icon={PlusIcon} onClick={make}>{t("New group")}</MenuItem>
            <MenuDivider />
            {others.map((group) => (
              <MenuItem key={group.id} icon={GROUP_DOTS[group.color]}
                onClick={() => { close(); updateTabs((prev) => joinGroup(prev, tab.id, group.id)); }}>
                {group.title.trim() || t("Unnamed group")}
              </MenuItem>
            ))}
          </SubMenuItem>
        ) : (
          <MenuItem icon={TabGroupIcon} onClick={make}>{t("Add tab to new group")}</MenuItem>
        )}
        {tab.group ? (
          <MenuItem icon={UngroupIcon} onClick={() => { close(); updateTabs((prev) => leaveGroup(prev, tab.id)); }}>
            {t("Remove from group")}
          </MenuItem>
        ) : null}
      </>
    );
  };

  const strip = {
    onGroupChange: (groupId, patch) => updateTabs((prev) => setGroup(prev, groupId, patch)),
    onGroupMenu: openEditor,
  };

  // the editor's group, while there is one
  const group = editor && groupsIn(tabs).find((g) => g.id === editor.groupId);
  const members = group ? membersOf(tabs, group.id) : [];
  const pane = group ? otherPane(members[0]) : null;
  const editorEl = group ? (
    <StripGroupMenu key={group.id} group={group} anchor={editor.anchor} onClose={() => setEditor(null)}
      onChange={(patch) => updateTabs((prev) => setGroup(prev, group.id, patch))}
      onUngroup={() => updateTabs((prev) => ungroup(prev, group.id))}
      onCloseTabs={() => closeTabs(members)}
      onMoveToPane={pane ? () => moveTabsToPane(members, pane.id) : null} />
  ) : null;

  return { tabMenuItems, strip, editor: editorEl };
}
