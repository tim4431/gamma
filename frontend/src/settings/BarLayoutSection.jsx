// A bar's layout in Settings (docs/dev/settings.md "Bars: order and modes"):
// one row per item of a defineBar bar (shared/model/barLayout.js), in the
// bar's order, each a grip, the item's icon, label and hint, and a
// Segmented of the modes it offers. Appearance › Top bar and › Viewer tools
// are two of these; a new bar is a registry and one more.
//
// The grip drags its row up or down the list (shared/ui/useDragOrder.js,
// "handle": any pointer, at once; a release lands anywhere across the pane
// and up to a row past either end), and moves it a slot with the arrow keys
// or to either end with Home / End; either way the new place is said in a
// polite live region ("Search: 2 of 7") and the focus stays on that grip.
// `filter` drops the rows that do not apply here (the Inbox without one):
// they keep their places in the order while the others move round them.
// A hidden item's row reads muted, so the list reads as the bar does.
// Reset puts the bar's default order and modes back.
import React from "react";
import { createPortal } from "react-dom";
import { Section, Row, Segmented } from "./SettingsKit";
import { useDragOrder } from "../shared/ui/useDragOrder.js";
import { t } from "../shared/i18n/i18n.js";

const everyItem = () => true;

export function BarLayoutSection({ bar, value, onChange, icons, title, scope, prefs, resetTitle, filter = everyItem }) {
  const layout = bar.read(value);
  const rows = layout.order.map(bar.item).filter(filter);
  const listRef = React.useRef(null);
  const moved = React.useRef(null);   // the id whose grip takes the focus back once the move renders
  const [said, setSaid] = React.useState("");
  const move = (id, to) => {
    const next = bar.move(layout, id, to, filter);
    if (next.order.join() === layout.order.join()) return;
    const ids = next.order.filter((x) => filter(bar.item(x)));
    moved.current = id;
    onChange(next);
    setSaid(t("{label}: {n} of {count}", { label: t(bar.item(id).label), n: ids.indexOf(id) + 1, count: ids.length }));
  };
  // A drop counts across the pane's width, since only the height picks
  // the slot, and a row's height past either end of the list (first or
  // last there), so a finger drifting off the narrow grip still lands.
  const bounds = () => {
    const list = listRef.current;
    if (!list) return null;
    const l = list.getBoundingClientRect();
    const p = list.closest(".settingsPane")?.getBoundingClientRect() || l;
    const reach = list.firstElementChild?.offsetHeight || 0;
    return { left: p.left, right: p.right, top: l.top - reach, bottom: l.bottom + reach };
  };
  const { sourceProps, keyProps, drag } = useDragOrder({ containerRef: listRef, axis: "y", start: "handle", onMove: move, onStatus: setSaid, bounds });
  // React moves the row's node, which can drop the focus from its grip.
  React.useEffect(() => {
    const id = moved.current;
    if (!id) return;
    moved.current = null;
    listRef.current?.querySelector(`[data-order-id="${id}"] .barLayoutGrip`)?.focus();
  });
  const dragged = drag ? bar.item(drag.id) : null;
  const Ghost = dragged ? icons[dragged.id] : null;
  return (
    <Section title={title} scope={scope} prefs={prefs}
      action={(
        <button type="button" className="uiBtn sm" disabled={bar.isDefault(layout)} title={resetTitle} onClick={() => onChange(bar.DEFAULT)}>
          {t("Reset")}
        </button>
      )}>
      <div className="barLayoutList" ref={listRef}>
        {rows.map((item) => {
          const label = t(item.label);
          const mode = layout.modes[item.id];
          return (
            <div key={item.id} data-order-id={item.id}
              className={`barLayoutRow${mode === "hidden" ? " off" : ""}${drag?.id === item.id ? " lifted" : ""}`}>
              <button type="button" className="barLayoutGrip" aria-label={t("Move {label}", { label })}
                title={t("Drag to reorder, or use the arrow keys")} {...sourceProps(item.id)} {...keyProps(item.id)}>⋮⋮</button>
              <Row icon={icons[item.id]} label={label} hint={t(item.hint)}>
                <Segmented value={mode} onChange={(next) => onChange(bar.setMode(layout, item.id, next))}
                  options={item.modes.map((m) => [m, bar.modeLabel(item, m), null, bar.modeTip(item, m)])} />
              </Row>
            </div>
          );
        })}
        {drag?.bar ? <span className="dropBar" style={drag.bar} aria-hidden="true" /> : null}
      </div>
      <span className="srOnly" aria-live="polite">{said}</span>
      {dragged ? createPortal(
        <div className="dragGhost barLayoutGhost" style={{ left: drag.x, top: drag.y }} aria-hidden="true">
          {Ghost ? <Ghost size={14} /> : null}{t(dragged.label)}
        </div>,
        document.body,
      ) : null}
    </Section>
  );
}
