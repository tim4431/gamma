// The open tabs' groups side by side (docs/dev/tab_groups.md): the layout
// tree of workspace/tabLayout.js drawn with the dock's resizable panels,
// each group a frame with its own tab strip over its page. The groups split
// the page's place only; the dock's windows stay around them. One group is
// live: the page App has open shows in it, through `live` (App's page
// view, div.main); every other group shows its active tab through
// `renderPane` (PagePane.jsx). `live` is drawn once, in a host laid over
// the live group's body, never inside it: a press that makes another group
// live, a split or a join moves the host and remounts nothing in it, so the
// viewer keeps its document, its zoom and its scroll, and the notes their
// editor. `liveId` names the group the host stands over: the group of the
// page on screen, which a press on another group's view changes only once
// that page has opened. One group alone draws no frame: the strip stays in
// the topbar and the host fills the page's place as before.
//
// The page's place splits once, into two panes side by side
// (docs/research/split-panes.md). Each pane's frame is its strip over its
// body; the live one is ringed (workspace.css). The end of a strip holds
// App's topbar items placed in the panes, acting on that pane's page:
// `strip.actions(group)` for a pane that is not live (buttons standing in
// for the items), and for the live one `liveBar`, the items themselves,
// drawn once like the live page (useLiveBar, below). `strip.headPress()`
// is a strip's right-click, App's bar menu.
//
// A tab dragged from any strip (the browser's drag, which the strip's own
// reorder already uses; tabDrag.js says which tab) can be dropped on
// another group's strip, before the tab it is over (the strip's hint), or on
// a group's body, where a layer over the page reads the zone the pointer is
// in. With one group the left or right band splits it on that side, the way
// VS Code's editor drop target and SiYuan's tab container do
// (docs/research/tab-groups.md), and the middle is where the tab already
// is; split, a pane's whole body takes the tab into it. A split's sizes are
// the layout's, kept through onResize.
import React, { useCallback, useLayoutEffect, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { OpenTabs } from "../shared/ui/Widgets";
import { useTabDrag } from "../shared/ui/tabDrag.js";
import { activeGroup, groupById } from "./tabLayout.js";
import "./workspace.css";

// The zone of a group's body the pointer is in: a side band (the lesser of
// 30% of the width and 220 px), else the middle.
export function dropZoneAt(rect, x) {
  const w = rect.width;
  if (!(w > 0)) return "center";
  const px = x - rect.left, band = Math.min(w * 0.3, 220);
  return px < band ? "left" : px > w - band ? "right" : "center";
}

const UNCLIPPED = Object.freeze({ overflow: "visible" });

export function TabGroups({ layout, liveId, single, tabsOf, live, liveBar, renderPane, activeTabId, onActivate, onMoveTab, onSplit, onResize,
  sashMargins, strip }) {
  const rootRef = useRef(null);
  const hostRef = useRef(null);
  const placeHostRef = useLiveHost(rootRef, hostRef);
  const [placeBarRef, setBar] = useLiveBar(rootRef);
  const placeRef = useRef(null);
  placeRef.current = () => { placeHostRef.current(); placeBarRef.current(); };
  const kickRef = useRef(null);
  // One group drawn alone: the layout's only group, or on a phone (which
  // keeps the arrangement without drawing it) the live one.
  const one = single || layout.root.type === "group";
  const alone = one ? (layout.root.type === "group" ? layout.root : groupById(layout, liveId) || activeGroup(layout)) : null;
  const liveGroup = one ? alone.id : liveId;
  const renderNode = (node) => {
    if (node.type === "group") {
      const isLive = node.id === liveGroup;
      return (
        <GroupFrame key={node.id} group={node} framed={!one} live={isLive} barSlot={!one && !!liveBar} tabs={tabsOf(node)}
          activeTabId={isLive && node.tabs.includes(activeTabId) ? activeTabId : node.active}
          onActivate={onActivate} onMoveTab={onMoveTab} onSplit={onSplit} strip={strip}>
          {isLive ? <div className="tabLiveSlot" data-live-slot="" /> : renderPane(node)}
        </GroupFrame>
      );
    }
    const direction = node.dir === "row" ? "horizontal" : "vertical";
    // Keyed by its children: a split that gained or lost one lays out afresh
    // from the layout's sizes, instead of the panels' remembered ones. What
    // remounts is the frames and the views to read; the live page is in the
    // host, outside the tree. The panels do not clip (the panels' own style
    // does): a popover hung from a pane's strip may reach over the other
    // pane, and each group's body clips its page instead (workspace.css).
    return (
      <PanelGroup key={node.children.map((child) => child.id).join("|")} direction={direction} className="tabSplit"
        style={UNCLIPPED} onLayout={(sizes) => { onResize(node.id, sizes); kickRef.current?.(); }}>
        {node.children.map((child, i) => (
          <React.Fragment key={child.id}>
            {i > 0 ? <PanelResizeHandle className={`sash sash-${direction}`} hitAreaMargins={sashMargins} /> : null}
            <Panel id={child.id} order={i + 1} defaultSize={node.sizes[i]} minSize={10} className="tabSplitPanel" style={UNCLIPPED}>
              {renderNode(child)}
            </Panel>
          </React.Fragment>
        ))}
      </PanelGroup>
    );
  };
  // The tree and the host are the root's first and second child whatever
  // the layout's shape, so no change of shape remounts the host's page.
  return (
    <div className={`tabGroups${one ? " single" : ""}`} ref={rootRef}>
      <div className="tabGroupsTree">{renderNode(one ? alone : layout.root)}</div>
      <div className="tabLiveHost" ref={hostRef}>{live}</div>
      {!one && liveBar ? createPortal(<div className="tabLiveBar" ref={setBar} {...strip.headPress?.()}>{liveBar}</div>, document.body) : null}
      <HostPlacer placeRef={placeRef} kickRef={kickRef} />
    </div>
  );
}

// The host laid over the live group's body (its slot, the body's only
// child): placed after every render of the groups, after every move of a
// sash between them (HostPlacer, below), and whenever the root or the slot
// changes size otherwise (the window resized, a dock window opened beside),
// from a resize observer, whose callback runs after layout and before paint.
// Returns a ref to the placing function.
function useLiveHost(rootRef, hostRef) {
  const slotRef = useRef(null);
  const observerRef = useRef(null);
  const place = () => {
    const root = rootRef.current, host = hostRef.current, slot = slotRef.current;
    if (!root || !host || !slot) return;
    const r = root.getBoundingClientRect(), b = slot.getBoundingClientRect();
    const box = { left: `${b.left - r.left}px`, top: `${b.top - r.top}px`, width: `${b.width}px`, height: `${b.height}px` };
    for (const key in box) if (host.style[key] !== box[key]) host.style[key] = box[key];
  };
  const placeRef = useRef(place);
  placeRef.current = place;
  useLayoutEffect(() => {
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => placeRef.current());
    observerRef.current = ro;
    if (rootRef.current) ro.observe(rootRef.current);
    return () => { ro.disconnect(); observerRef.current = null; slotRef.current = null; };
  }, [rootRef]);
  useLayoutEffect(() => {
    const slot = rootRef.current?.querySelector(":scope > .tabGroupsTree [data-live-slot]") || null;
    if (slot !== slotRef.current) {
      if (slotRef.current) observerRef.current?.unobserve(slotRef.current);
      slotRef.current = slot;
      if (slot) observerRef.current?.observe(slot);
    }
    place();
  });
  return placeRef;
}

// The live pane's bar (`liveBar`), drawn once on the page's body over the
// slot the live pane's strip keeps for it, as the live page is drawn once
// over its body: a change of live pane moves the bar and remounts nothing
// in it (the search keeps its query and its pinned hit), and its popovers
// hang from it unclipped by the panels around the page's place. Placed
// with the page's host (after every render, a sash's move) and whenever
// the bar or the live strip changes size; the slot takes the bar's width,
// and a strip too narrow to hold it hides it. The bar's right edge is
// `--bar-right`, the room its popovers have to the left (workspace.css).
function useLiveBar(rootRef) {
  const barRef = useRef(null);
  const headRef = useRef(null);
  const observerRef = useRef(null);
  const place = () => {
    const bar = barRef.current;
    if (!bar) return;
    const slot = rootRef.current?.querySelector(":scope > .tabGroupsTree [data-live-bar-slot]") || null;
    const head = slot?.parentElement || null;
    if (head !== headRef.current) {
      if (headRef.current) observerRef.current?.unobserve(headRef.current);
      headRef.current = head;
      if (head) observerRef.current?.observe(head);
    }
    if (!slot) { bar.style.visibility = "hidden"; return; }
    const width = bar.offsetWidth;
    if (slot.style.width !== `${width}px`) slot.style.width = `${width}px`;
    const s = slot.getBoundingClientRect();
    const box = { left: `${s.left}px`, top: `${s.top}px`, height: `${s.height}px`, visibility: s.width >= width - 1 ? "" : "hidden" };
    for (const key in box) if (bar.style[key] !== box[key]) bar.style[key] = box[key];
    bar.style.setProperty("--bar-right", `${s.left + width}px`);
  };
  const placeRef = useRef(place);
  placeRef.current = place;
  useLayoutEffect(() => {
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => placeRef.current());
    observerRef.current = ro;
    if (barRef.current) ro.observe(barRef.current);
    return () => { ro.disconnect(); observerRef.current = null; headRef.current = null; };
  }, []);
  // The bar's element, observed while it is drawn (its items come and go).
  const setBar = useCallback((el) => {
    if (barRef.current) observerRef.current?.unobserve(barRef.current);
    barRef.current = el;
    if (el) observerRef.current?.observe(el);
  }, []);
  useLayoutEffect(() => { placeRef.current(); });
  return [placeRef, setBar];
}

// A sash dragged between groups re-renders only its PanelGroup, the move
// mostly too small to change the layout (tabLayout's resizeSplit), so the
// groups do not render and only the slot's resize observer would place the
// host: from its callback, after the page in the host had laid out at the
// old size, which the browser then reports as a resize observer loop and
// the page's own observers (the viewer's fit) take a frame late. The
// split's onLayout runs in the same event as the panels' new sizes; it
// kicks this placer, whose layout effect places the host in that commit,
// before the browser lays the frame out. Only the placer renders.
function HostPlacer({ placeRef, kickRef }) {
  const [moves, kick] = useReducer((n) => n + 1, 0);
  kickRef.current = kick;
  useLayoutEffect(() => { if (moves) placeRef.current(); }, [moves, placeRef]);
  return null;
}

// One group: its strip (when framed) over its body, and the drop layer
// while a tab is dragged. The strip's props are App's, the group's tabs and
// what each strip does with a tab; `strip.onReorder(groupId, dragged,
// target)` is the strip's own reorder.
function GroupFrame({ group, framed, live, barSlot, tabs, activeTabId, onActivate, onMoveTab, onSplit, strip, children }) {
  const drag = useTabDrag();
  const [zone, setZone] = useState(null);
  const zoneFor = (event) => {
    // Split, the whole body takes the tab; one group, a side band splits it.
    const z = framed ? "center" : dropZoneAt(event.currentTarget.getBoundingClientRect(), event.clientX);
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
        <div className="tabGroupHead" {...strip.headPress?.()}>
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
          {live ? (barSlot ? <div className="tabLiveBarSlot" data-live-bar-slot="" /> : null) : strip.actions?.(group)}
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
