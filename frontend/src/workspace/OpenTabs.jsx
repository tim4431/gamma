// The tab strip (OpenTabs): the topbar's, and each group's over its page
// (workspace/TabGroups.jsx), with Chrome's tab groups drawn in it
// (stripGroups.js, docs/dev/strip_groups.md).
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import FileIcon from "../shared/ui/FileIcon.jsx";
import { PinIcon, XIcon } from "../shared/ui/Icons";
import { useWheelPan } from "../shared/ui/wheelPan";
import { tabDrag, useTabDrag } from "../shared/ui/tabDrag.js";
import { menuPress } from "../shared/ui/press.js";
import { Popover } from "../shared/ui/Surfaces";
import { Chevron } from "../shared/ui/MotionWidgets";
import ListMotion from "../shared/ui/ListMotion";
import { composing } from "../shared/lib/hotkeys.js";
import { t } from "../shared/i18n/i18n.js";
import { collapseHeir, groupTint, stripRuns } from "./stripGroups.js";

// The topbar's tab strip (Chrome-style). Each tab leads with its page's kind
// icon (`kindOf(id)` → {format, file}: its document's format, "" for none,
// and a synced folder's file's name, whose kind's icon it is); pinned tabs come
// first, keep their title and carry a pin (click to unpin) where the close
// button would be.
// The strip keeps the active tab in view, pans with a plain mouse wheel, and
// when it overflows fades its right edge and shows a "⌄ n" button whose
// popover lists every open tab with a filter. The tabs are a list that moves
// (ListMotion, docs/dev/ui-design.md "Lists that change"): one opened rises
// in, one closed sinks back out where it stood, and the others glide to
// their places; tabs read from elsewhere (this browser's cache, the server's
// copy) come `still`.
// A run of tabs in a group (Chrome's tab groups, workspace/stripGroups.js,
// docs/dev/strip_groups.md) has its chip before it: a click collapses the
// group (`onGroupChange`, opening first the nearest tab outside it when the
// strip's page is in it), a right-click opens its editor (`onGroupMenu`,
// with the chip), a drag or Ctrl+←/→ carries the whole group. An open group
// is underlined from its chip to its last tab, each item drawing its
// stretch (.stripGroupLine), and its active tab is outlined in the colour,
// the outline flaring into the line as Chrome draws it (.stripGroupOutline).
// A collapsed run's tabs leave the strip but for the active one. A dragged
// tab hands `onReorder` the tab it is over, `{group}` for a chip, or null
// for the strip's empty end, past the last item.
const TAB_FADE = 40; // the right-edge fade the active tab keeps clear of
// Until the person has pressed or typed anything in this window, what
// changes the strip is the window loading (its tabs read back, the page it
// opened on getting its tab): nothing moves for that.
let acted = false;
const onAct = (event) => {
  if (!event.isTrusted) return;
  acted = true;
  window.removeEventListener("pointerdown", onAct, true);
  window.removeEventListener("keydown", onAct, true);
};
window.addEventListener("pointerdown", onAct, true);
window.addEventListener("keydown", onAct, true);

export function OpenTabs({
  tabs,
  activeId,
  still = false,
  kindOf,
  menuOpen = false,
  onMenuOpenChange,
  onReorder,
  onOpen,
  onClose,
  onTogglePin,
  onContext,
  groupId = null,
  onDropTab,
  onGroupChange,
  onGroupMenu,
}) {
  // Drag-reorder bookkeeping is private to the strip: the dragged tab's id,
  // or {group} for a chip, as a ref (read during dragover) with a state twin
  // for the .dragging style (a chip's as "group:<id>").
  const dragTab = useRef(null);
  const [draggingId, setDraggingId] = useState(null);
  // A tab dragged from another group's strip (workspace/TabGroups.jsx,
  // tabDrag.js): it is taken on the drop, before the tab it is over (its
  // left half) or after it, else at the end; `hint` is that index while the
  // drag is over this strip.
  const drag = useTabDrag();
  const foreign = !!drag && !!onDropTab && drag.from !== groupId;
  const [hint, setHint] = useState(null);
  useEffect(() => { if (!foreign) setHint(null); }, [foreign]);
  const dropForeign = (event, index) => {
    event.preventDefault();
    if (!foreign) return;
    const id = drag.id;
    setHint(null);
    tabDrag.end();
    onDropTab(id, index);
  };
  const stripRef = useRef(null);
  const tabElements = useRef(new Map());
  const wheelPan = useWheelPan();
  const setStrip = useCallback((el) => { stripRef.current = el; wheelPan(el); }, [wheelPan]);
  // Whether the tabs overflow the strip, and whether it is scrolled to its
  // end (nothing hidden on the right: the fade goes).
  const [overflow, setOverflow] = useState({ over: false, atEnd: true });
  const measure = useCallback(() => {
    const el = stripRef.current;
    if (!el) return;
    const over = el.scrollWidth > el.clientWidth + 1;
    const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1;
    setOverflow((o) => (o.over === over && o.atEnd === atEnd ? o : { over, atEnd }));
  }, []);
  useEffect(() => {
    const el = stripRef.current;
    if (!el) return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    el.addEventListener("scroll", measure, { passive: true });
    return () => { observer.disconnect(); el.removeEventListener("scroll", measure); };
  }, [measure]);
  // Keep the active tab in view, clear of the fade on the right. Positions
  // are in the strip's content (offsetLeft; the strip is the offset parent).
  useLayoutEffect(() => {
    const strip = stripRef.current;
    const tab = activeId ? tabElements.current.get(activeId) : null;
    if (strip && tab) {
      const left = tab.offsetLeft;
      const right = tab.offsetLeft + tab.offsetWidth + TAB_FADE - strip.clientWidth;
      if (strip.scrollLeft > left) strip.scrollLeft = Math.max(0, left);
      else if (strip.scrollLeft < right) strip.scrollLeft = right;
    }
    measure();
  }, [activeId, tabs, measure]);
  // The strip stops overflowing (tabs closed): the menu has nothing to add.
  useEffect(() => { if (!overflow.over && menuOpen) onMenuOpenChange?.(false); }, [overflow.over, menuOpen, onMenuOpenChange]);

  // The all-tabs menu: a filter over the titles, ↑↓ to pick, Enter opens.
  const [filter, setFilter] = useState("");
  const [pick, setPick] = useState(0);
  useEffect(() => { if (menuOpen) { setFilter(""); setPick(Math.max(0, tabs.findIndex((tab) => tab.id === activeId))); } }, [menuOpen]); // eslint-disable-line react-hooks/exhaustive-deps
  const listRef = useRef(null);
  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [pick, menuOpen]);
  const needle = filter.trim().toLowerCase();
  const listed = needle ? tabs.filter((tab) => (tab.title || "").toLowerCase().includes(needle)) : tabs;
  const openFromMenu = (tab) => {
    if (!tab) return;
    onMenuOpenChange?.(false);
    if (tab.id !== activeId) onOpen(tab.id);
  };
  const kindIcon = (tab, size) => {
    const kind = kindOf?.(tab.id);
    return <FileIcon path={kind?.file} format={kind?.format || ""} size={size} />;
  };
  // A press on a chip: its group collapses or opens. Collapsing the group the
  // strip's page is in opens the nearest tab outside it first (null: the
  // library), as Chrome does.
  const toggleGroup = (group) => {
    const heir = group.collapsed ? undefined : collapseHeir(tabs, group.id, activeId);
    onGroupChange?.(group.id, { collapsed: !group.collapsed });
    if (heir !== undefined) onOpen(heir);
  };
  // What the strip draws, in order: each group's chip before its run (with
  // the indexes of the run's first and last tabs: a drop before the chip
  // lands at the first, Ctrl+←/→ passes the tab before or after the run),
  // and the tabs, a collapsed run's left out but for the active one, the
  // run's last marked where its underline ends.
  const drawn = stripRuns(tabs).flatMap((run) => (run.group ? [
    { group: run.group, first: tabs.indexOf(run.tabs[0]), last: tabs.indexOf(run.tabs[run.tabs.length - 1]) },
    ...run.tabs.filter((tab) => !run.group.collapsed || tab.id === activeId).map((tab) => ({ tab, end: tab === run.tabs[run.tabs.length - 1] })),
  ] : [run]));
  // This strip's own drag over a tab, a chip ({group}) or the strip's end (null).
  const ownDragOver = (event, target) => {
    const dragged = dragTab.current;
    if (!dragged || dragged === target || (dragged.group && dragged.group === target?.group)) return;
    event.preventDefault();
    // A tab still gliding to the place a swap gave it passes under the
    // pointer on its way: it is not where it stands, and swapping with it
    // would only swap back.
    if (event.currentTarget.hasAttribute("data-moving")) return;
    onReorder(dragged, target);
  };
  // The chip: an item as tall as a tab (a drop or a drag takes it like one,
  // and its stretch of the underline stands where the tabs' do) holding the
  // group's pill, the button: the control the keyboard reaches, zoomed with
  // the Interface size as every control is, while the item is not.
  const chip = ({ group, first, last }) => {
    const key = `group:${group.id}`;
    const name = group.title.trim();
    // Ctrl+←/→ (⌘ on a Mac) carries the group past the tab beside it, as Chrome's header does
    const shift = (event) => {
      const beside = event.key === "ArrowLeft" ? tabs[first - 1] : event.key === "ArrowRight" ? tabs[last + 1] : null;
      if (!beside || !(event.ctrlKey || event.metaKey)) return false;
      onReorder({ group: group.id }, beside.id);
      return true;
    };
    return (
      <div key={key} data-motion-key={key} data-strip-group={group.id}
        className={`stripGroupChip${draggingId === key ? " dragging" : ""}${foreign && hint === first ? " dropBefore" : ""}`}
        style={groupTint(group.color)}
        title={group.collapsed ? t("{name}: click to show its tabs, right-click for more", { name: name || t("Unnamed group") })
          : t("{name}: click to hide its tabs, right-click for more", { name: name || t("Unnamed group") })}
        draggable
        onDragStart={(event) => {
          dragTab.current = { group: group.id };
          setDraggingId(key);
          event.dataTransfer.effectAllowed = "move";
          try { event.dataTransfer.setData("application/x-gamma-tab-group", group.id); } catch {}
        }}
        onDragEnd={() => { dragTab.current = null; setDraggingId(null); }}
        onDragOver={(event) => {
          if (!foreign) { ownDragOver(event, { group: group.id }); return; }
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          setHint(first);
        }}
        onDrop={(event) => dropForeign(event, hint ?? first)}
        onClick={() => toggleGroup(group)}
        onKeyDown={(event) => {
          if (shift(event)) event.preventDefault();
          else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleGroup(group); }
        }}
        {...menuPress((event) => {
          event.preventDefault();
          onGroupMenu?.(group.id, event.currentTarget);
        })}
      >
        <span role="button" tabIndex={0} aria-expanded={!group.collapsed} aria-label={name || t("Unnamed group")}
          className={`stripGroupPill${name ? "" : " unnamed"}`}>{name}</span>
        {group.collapsed ? null : <span className="stripGroupLine" aria-hidden="true" />}
      </div>
    );
  };

  const tabEl = (tab, end) => {
    const i = tabs.indexOf(tab);
    // the drop bar before a group's first tab stands before its chip
    const lead = !!tab.group && tabs[i - 1]?.group?.id !== tab.group.id;
    return (
      <div
        key={tab.id}
        data-motion-key={tab.id}
        role="tab"
        aria-selected={tab.id === activeId}
        ref={(element) => {
          if (element) tabElements.current.set(tab.id, element);
          else tabElements.current.delete(tab.id);
        }}
        className={`tab ${tab.id === activeId ? "active" : ""} ${draggingId === tab.id ? "dragging" : ""} ${tab.pinned ? "pinned" : ""}${tab.group ? " grouped" : ""}${end ? " groupEnd" : ""}${foreign && hint === i && !lead ? " dropBefore" : ""}${foreign && hint === i + 1 && i === tabs.length - 1 ? " dropAfter" : ""}`}
        style={tab.group ? groupTint(tab.group.color) : undefined}
        title={tab.title}
        draggable
        onDragStart={(event) => {
          dragTab.current = tab.id;
          setDraggingId(tab.id);
          event.dataTransfer.effectAllowed = "move";
          try { event.dataTransfer.setData("application/x-gamma-tab", tab.id); } catch {}
          tabDrag.begin(tab.id, groupId);
        }}
        onDragEnd={() => {
          dragTab.current = null;
          setDraggingId(null);
          tabDrag.end();
        }}
        onDragOver={(event) => {
          if (!foreign) { ownDragOver(event, tab.id); return; }
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          const box = event.currentTarget.getBoundingClientRect();
          setHint(event.clientX < box.left + box.width / 2 ? i : i + 1);
        }}
        onDrop={(event) => dropForeign(event, hint ?? tabs.length)}
        onClick={() => {
          if (tab.id !== activeId) onOpen(tab.id);
        }}
        {...menuPress((event) => {
          event.preventDefault();
          onContext(tab, event.clientX, event.clientY);
        })}
      >
        {tab.group && !tab.group.collapsed ? <span className={tab.id === activeId ? "stripGroupOutline" : "stripGroupLine"} aria-hidden="true" /> : null}
        <span className="tabKind">{kindIcon(tab, 14)}</span>
        <span className="tabTitle">{tab.title}</span>
        {tab.pinned ? (
          <button
            className="uiClose tabClose tabPin"
            onClick={(event) => {
              event.stopPropagation();
              onTogglePin?.(tab.id);
            }}
            title={t("Unpin tab")}
            aria-label={t("Unpin tab")}
          >
            <PinIcon filled size={14} />
          </button>
        ) : (
          <button
            className="uiClose tabClose"
            onClick={(event) => {
              event.stopPropagation();
              onClose(tab.id);
            }}
            title={t("Close tab")}
            aria-label={t("Close {title}", { title: tab.title })}
          >
            <XIcon size={14} />
          </button>
        )}
      </div>
    );
  };

  return (
    <>
      <div ref={setStrip} role="tablist" data-guide="header.tabs"
        className={`tabStrip${overflow.over && !overflow.atEnd ? " fadeEnd" : ""}${foreign && hint === tabs.length ? " dropEnd" : ""}`}
        onDragOver={(event) => {
          // the strip's own room, past the last tab
          if (event.target !== event.currentTarget) return;
          if (!foreign) {
            // its gaps and padding are the strip too: only past the last
            // item is its end
            const items = event.currentTarget.querySelectorAll("[data-motion-key]");
            const last = items[items.length - 1];
            if (last && event.clientX > last.getBoundingClientRect().right) ownDragOver(event, null);
            return;
          }
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          setHint(tabs.length);
        }}
        onDragLeave={(event) => { if (foreign && !event.currentTarget.contains(event.relatedTarget)) setHint(null); }}
        onDrop={(event) => { if (foreign && event.target === event.currentTarget) dropForeign(event, tabs.length); }}>
        {/* Measured in the window: a close that narrows a strip scrolled to
            its end scrolls it back, and the tabs before the one closed
            glide over its place from the left. */}
        <ListMotion keys={drawn.map((item) => (item.group ? `group:${item.group.id}` : item.tab.id))} still={still || !acted}
          enter="rise" leave="tab" strip={["role", "aria-selected", "data-strip-group"]}>
          {drawn.map((item) => (item.group ? chip(item) : tabEl(item.tab, item.end)))}
        </ListMotion>
      </div>
      {overflow.over ? (
        <span data-popover="tabs" className="popoverAnchor tabsMenuAnchor">
          <button
            className={`iconBtn tabsMenuBtn ${menuOpen ? "activeIcon" : ""}`}
            onClick={() => onMenuOpenChange?.(!menuOpen)}
            title={t("All open tabs")}
            aria-label={t("All open tabs")}
            aria-expanded={menuOpen}
          >
            <Chevron open={menuOpen} dir="down" size={14} />
            <span className="tabsMenuCount">{tabs.length}</span>
          </button>
          {menuOpen ? (
            <Popover className="tabsPopover" role="dialog" aria-label={t("All open tabs")}>
              <input
                autoFocus
                className="searchInput"
                value={filter}
                placeholder={t("Find an open tab")}
                aria-label={t("Find an open tab")}
                onChange={(event) => { setFilter(event.target.value); setPick(0); }}
                onKeyDown={(event) => {
                  if (composing(event)) return;
                  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                    event.preventDefault();
                    const step = event.key === "ArrowDown" ? 1 : -1;
                    setPick((i) => (listed.length ? (i + step + listed.length) % listed.length : 0));
                  } else if (event.key === "Enter") {
                    event.preventDefault();
                    openFromMenu(listed[pick]);
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    onMenuOpenChange?.(false);
                  }
                }}
              />
              <div ref={listRef} className="tabsMenuList" role="listbox" aria-label={t("All open tabs")}>
                {listed.map((tab, i) => (
                  <div
                    key={tab.id}
                    role="option"
                    aria-selected={i === pick}
                    className={`tabsMenuRow${tab.id === activeId ? " current" : ""}${i === pick ? " picked" : ""}`}
                    title={tab.title}
                    onMouseEnter={() => setPick(i)}
                    onClick={() => openFromMenu(tab)}
                  >
                    {kindIcon(tab, 14)}
                    <span className="tabsMenuTitle">{tab.title}</span>
                    {tab.group ? <span className="stripGroupDot" style={groupTint(tab.group.color)} title={tab.group.title || t("Unnamed group")} /> : null}
                    {tab.pinned ? <span className="tabsMenuPin"><PinIcon filled size={14} /></span> : (
                      <button
                        className="uiClose uiCloseSm"
                        onClick={(event) => { event.stopPropagation(); onClose(tab.id); }}
                        title={t("Close tab")}
                        aria-label={t("Close {title}", { title: tab.title })}
                      ><XIcon size={14} /></button>
                    )}
                  </div>
                ))}
                {!listed.length ? <div className="popoverHint">{t("No open tab matches.")}</div> : null}
              </div>
            </Popover>
          ) : null}
        </span>
      ) : null}
    </>
  );
}
