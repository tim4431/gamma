// The open tabs' groups side by side (docs/dev/tab_groups.md): the layout
// tree of workspace/tabLayout.js drawn with the dock's resizable panels,
// each group a frame with its own tab strip over its page. The live group
// holds `live`, App's work area (the open page and its windows); every
// other group shows its active tab through `renderPane` (PagePane.jsx). One
// group alone draws no frame: the strip stays in the topbar and `live`
// fills the work area as before.
//
// A tab dragged from any strip (the browser's drag, which the strip's own
// reorder already uses; tabDrag.js says which tab) can be dropped on
// another group's strip, before the tab it is over (the strip's hint), or on
// a group's body, where a layer over the page reads the zone the pointer is
// in: the middle takes the tab into the group, an edge splits the group on
// that side, the way VS Code's editor drop target and SiYuan's tab container
// do (docs/research/tab-groups.md). A split's sizes are the layout's, kept
// through onResize.
import React, { useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { OpenTabs } from "../shared/ui/Widgets";
import { useTabDrag } from "../shared/ui/tabDrag.js";
import "./workspace.css";

// The zone of a group's body the pointer is in: an edge band (the lesser of
// 30% of the side and 220 px across, 160 px down), else the middle. The band
// the pointer is deeper into, relative to its width, wins a corner.
export function dropZoneAt(rect, x, y) {
  const w = rect.width, h = rect.height;
  if (!(w > 0) || !(h > 0)) return "center";
  const px = x - rect.left, py = y - rect.top;
  const bx = Math.min(w * 0.3, 220), by = Math.min(h * 0.3, 160);
  const dx = Math.min(px, w - px), dy = Math.min(py, h - py);
  const inX = dx < bx, inY = dy < by;
  if (inX && (!inY || dx / bx <= dy / by)) return px < w / 2 ? "left" : "right";
  if (inY) return py < h / 2 ? "top" : "bottom";
  return "center";
}

export function TabGroups({ layout, single, tabsOf, live, renderPane, activeTabId, onActivate, onMoveTab, onSplit, onResize,
  sashMargins, strip }) {
  if (single || layout.root.type === "group") {
    return (
      <div className="tabGroups">
        <GroupFrame group={layout.root} framed={false} live tabs={tabsOf(layout.root)} activeTabId={activeTabId}
          onActivate={onActivate} onMoveTab={onMoveTab} onSplit={onSplit} strip={strip}>{live}</GroupFrame>
      </div>
    );
  }
  const renderNode = (node) => {
    if (node.type === "group") {
      const isLive = node.id === layout.active;
      return (
        <GroupFrame key={node.id} group={node} framed live={isLive} tabs={tabsOf(node)} activeTabId={isLive ? activeTabId : node.active}
          onActivate={onActivate} onMoveTab={onMoveTab} onSplit={onSplit} strip={strip}>
          {isLive ? live : renderPane(node)}
        </GroupFrame>
      );
    }
    const direction = node.dir === "row" ? "horizontal" : "vertical";
    // Keyed by its children: a split that gained or lost one lays out afresh
    // from the layout's sizes, instead of the panels' remembered ones.
    return (
      <PanelGroup key={node.children.map((child) => child.id).join("|")} direction={direction} className="tabSplit"
        onLayout={(sizes) => onResize(node.id, sizes)}>
        {node.children.map((child, i) => (
          <React.Fragment key={child.id}>
            {i > 0 ? <PanelResizeHandle className={`sash sash-${direction}`} hitAreaMargins={sashMargins} /> : null}
            <Panel id={child.id} order={i + 1} defaultSize={node.sizes[i]} minSize={10} className="tabSplitPanel">
              {renderNode(child)}
            </Panel>
          </React.Fragment>
        ))}
      </PanelGroup>
    );
  };
  return <div className="tabGroups">{renderNode(layout.root)}</div>;
}

// One group: its strip (when framed) over its body, and the drop layer
// while a tab is dragged. The strip's props are App's, the group's tabs and
// what each strip does with a tab; `strip.onReorder(groupId, dragged,
// target)` is the strip's own reorder.
function GroupFrame({ group, framed, live, tabs, activeTabId, onActivate, onMoveTab, onSplit, strip, children }) {
  const drag = useTabDrag();
  const [zone, setZone] = useState(null);
  const zoneFor = (event) => {
    const z = dropZoneAt(event.currentTarget.getBoundingClientRect(), event.clientX, event.clientY);
    // The dragged tab's own group: into its middle is where it is, and the
    // only tab of a group cannot be split off it.
    if (drag?.from === group.id && (z === "center" || tabs.length === 1)) return null;
    return z;
  };
  const drop = (event) => {
    event.preventDefault();
    const z = zoneFor(event);
    setZone(null);
    if (!z || !drag) return;
    if (z === "center") onMoveTab(drag.id, group.id, tabs.length);
    else onSplit(group.id, z, drag.id);
  };
  return (
    <div className={`tabGroup${live ? " live" : ""}${framed ? " framed" : ""}`} data-group-id={group.id}>
      {framed ? (
        <div className="tabGroupHead">
          <OpenTabs
            groupId={group.id}
            tabs={tabs}
            activeId={activeTabId}
            still={strip.still}
            kindOf={strip.kindOf}
            menuOpen={strip.menuOpenFor === group.id}
            onMenuOpenChange={(open) => strip.onMenuOpenChange(open ? group.id : null)}
            onReorder={(dragged, target) => strip.onReorder(group.id, dragged, target)}
            onOpen={(id) => onActivate(group.id, id)}
            onClose={strip.onClose}
            onTogglePin={strip.onTogglePin}
            onContext={strip.onContext}
            onDropTab={(id, index) => onMoveTab(id, group.id, index)}
          />
        </div>
      ) : null}
      <div className="tabGroupBody">
        {children}
        {drag ? (
          <div className="tabDropLayer" data-drop-group={group.id}
            onDragEnter={(event) => { event.preventDefault(); setZone(zoneFor(event)); }}
            onDragOver={(event) => {
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
              const z = zoneFor(event);
              setZone((cur) => (cur === z ? cur : z));
            }}
            onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setZone(null); }}
            onDrop={drop}>
            {zone ? <div className={`tabDropZone zone-${zone}`} /> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
