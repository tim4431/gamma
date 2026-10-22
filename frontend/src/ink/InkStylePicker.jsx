// The pen's Style picker and the Line trio (docs/dev/handwriting.md "What
// the user sees"), shared by a pen preset's options row on the strip
// (markup/MarkupToolbar.jsx) and the selection menu's Style row
// (InkLayer.jsx InkSelectionMenu). The picker is a button showing a short
// stroke drawn in the current brush's look, its name and a ▾; its press
// opens InkStyleMenu, a ContextMenu of the five brushes the caller renders
// outside its InkTooltips host (a portal's events bubble to the host, so
// every row the pointer crossed would raise a tooltip), the way the strip's
// other menus open: on the press, so the menu takes the tooltip's place in
// the same frame. Brush values: 'pen' (the plain pen, the absent key in a
// file or a preset), 'monoline' (shown as Ballpoint), 'brush', 'pencil',
// 'calligraphy'. NibPreview is a calligraphy pen's nib at its angle, for
// its options row.
import { CheckIcon, ChevronDownIcon } from "../shared/ui/Icons";
import { ContextMenu, MenuItem } from "../shared/ui/Menus";
import { BRUSH_ORDER, DEFAULT_NIB, encodeStroke, strokeNib, strokePath } from "./ink.js";
import { bakeWidths, strokeChannels } from "./finishStroke.js";
import { t, T } from "../shared/i18n/i18n.js";

// The five in the order the picker lists them and Shift+[ / ] steps them
// (ink.js BRUSH_ORDER, the plain pen there null): [value, name, title].
const NAMES = {
  pen: [T("Pen"), T("Pen: width follows stylus pressure")],
  monoline: [T("Ballpoint"), T("Ballpoint: an even line at every pressure")],
  brush: [T("Brush"), T("Brush: a felt tip that swells with pressure and tapers at the ends")],
  pencil: [T("Pencil"), T("Pencil: tilt widens the line, pressure darkens it")],
  calligraphy: [T("Calligraphy"), T("Calligraphy: a flat nib, thick across it and thin along it")],
};
const BRUSH_CHOICES = BRUSH_ORDER.map((brush) => [brush || "pen", ...NAMES[brush || "pen"]]);
// A stroke's or a preset's brush key → the picker's value.
export const brushValue = (brush) => (brush && BRUSH_CHOICES.some(([v]) => v === brush) ? brush : "pen");
// The brush's name in words: "Pen", "Ballpoint", "Brush", "Pencil", "Calligraphy".
const brushName = (brush) => t(BRUSH_CHOICES.find(([v]) => v === brushValue(brush))[1]);

// A short wave in a brush's look, drawn by the stroke renderer itself
// (ink.js strokePath over samples baked by the brush table), pressure
// rising and falling along it; the pencil leans a little and the
// calligraphy pen is its nib at 45° (ink.js nibOutline). Built once per
// brush, in a 28 × 10 box.
const previews = new Map();
function previewPath(brush) {
  let path = previews.get(brush);
  if (path) return path;
  const style = { tool: "pen", brush: brush === "pen" ? undefined : brush, pen: true, size: brush === "calligraphy" ? 3 : 1.8,
    ...(brush === "calligraphy" ? { nib: DEFAULT_NIB } : {}) };
  const samples = Array.from({ length: 17 }, (_, i) => {
    const f = i / 16;
    return { x: 3 + 22 * f, y: 5 - 2.4 * Math.sin(f * Math.PI * 2), p: 0.2 + 0.75 * Math.sin(f * Math.PI), t: 12 * i,
      ...(brush === "pencil" ? { a: 72, z: 30 } : {}) };
  });
  const baked = bakeWidths(samples, style);
  path = strokePath(encodeStroke({ id: `preview-${brush}`, ...style, samples: baked, ch: strokeChannels(style, baked) }));
  previews.set(brush, path);
  return path;
}
function BrushPreview({ brush = "pen" }) {
  const p = previewPath(brushValue(brush));
  return (
    <svg className="inkBrushPreview" width="28" height="10" viewBox="0 0 28 10" aria-hidden="true" focusable="false">
      {p.bands ? p.bands.map((b) => <path key={b.alpha} d={b.d} fill="currentColor" fillOpacity={b.alpha} />)
        : p.stroke ? <path d={p.d} fill="none" stroke="currentColor" strokeWidth={p.width} strokeLinecap="round" strokeLinejoin="round" />
          : <path d={p.d} fill="currentColor" />}
    </svg>
  );
}
// The menu rows' icons (MenuItem takes a component).
const PREVIEW_ICONS = Object.fromEntries(BRUSH_CHOICES.map(([value]) => [value, () => <BrushPreview brush={value} />]));

// The picker's button. `brush` the current brush (a key or 'pen'), `mixed`
// when the selection's pens differ (the button then says "Mixed"), `open`
// whether its menu stands open, `onOpen(button)` toggles it — on the press
// for a pointer, on the click for a keyboard (a click with no press behind
// it, e.detail 0).
export function InkStylePicker({ brush, mixed = false, open = false, title, onOpen }) {
  const name = mixed ? t("Mixed") : brushName(brush);
  return (
    <button type="button" className={"ctlBtn inkStyleBtn" + (open ? " modeActive" : "")} aria-haspopup="menu" aria-expanded={open}
      aria-label={t("Style: {brush}", { brush: name })} title={title ?? t("Pen style · Shift+[ ]")}
      onPointerDown={(e) => { if (e.button === 0) onOpen(e.currentTarget); }}
      onClick={(e) => { if (e.detail === 0) onOpen(e.currentTarget); }}>
      {mixed ? null : <BrushPreview brush={brush} />}
      <span className="inkStyleName">{name}</span>
      <ChevronDownIcon size={14} aria-hidden="true" />
    </button>
  );
}

// The picker's menu at (x, y): the five brushes, each led by its preview,
// the current one (`value`; none when mixed) checked; onPick(value) with
// 'pen' | 'monoline' | 'brush' | 'pencil' | 'calligraphy'. `ignoreRef` is the button, so
// its own press toggles the menu instead of dismissing it.
export function InkStyleMenu({ x, y, value, ignoreRef, onPick, onClose }) {
  return (
    <ContextMenu x={x} y={y} ignoreRef={ignoreRef} onClose={onClose} className="inkStripMenu inkStyleMenu">
      {/* a press here is the menu's, never the page's under it */}
      <div role="menu" aria-label={t("Pen style")} onPointerDown={(e) => e.stopPropagation()}>
        {BRUSH_CHOICES.map(([choice, label, title]) => (
          <MenuItem key={choice} role="menuitemradio" aria-checked={value === choice} icon={PREVIEW_ICONS[choice]} title={t(title)}
            trailing={value === choice ? <CheckIcon size={14} className="ctxMenuCheck" /> : null}
            onMouseDown={(e) => e.preventDefault()} onClick={() => { onClose(); onPick(choice); }}>
            {t(label)}
          </MenuItem>
        ))}
      </div>
    </ContextMenu>
  );
}

// A calligraphy pen's nib at `nib` degrees (a preset's −60..60, or a
// stroke's 0..179; counterclockwise as seen), a 16 px glyph beside the Nib
// angle control: the flat nib as a bar turned to the angle.
export function NibPreview({ nib = DEFAULT_NIB }) {
  const deg = strokeNib(nib);
  return (
    <svg className="inkNibPreview" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <rect x="2.5" y="7" width="11" height="2" rx="0.5" fill="currentColor" transform={`rotate(${-deg} 8 8)`} />
    </svg>
  );
}

// The Line trio: Solid · Dashed · Dotted, as pills with a glyph of the
// line. `value` null (solid), 'dashed' or 'dotted' — undefined when a
// selection's strokes differ, so no pill is pressed; onLine(null |
// 'dashed' | 'dotted').
const LINE_CHOICES = [
  [null, T("Solid"), T("Solid line")],
  ["dashed", T("Dashed"), T("Dashed line: dashes two widths long")],
  ["dotted", T("Dotted"), T("Dotted line: a dot every two widths")],
];
const LINE_DASH = { dashed: "4 3", dotted: "0.1 3.4" };
function LineGlyph({ dash }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M2.5 8h11" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeDasharray={LINE_DASH[dash]} />
    </svg>
  );
}
export function InkLineChoices({ value, onLine }) {
  return (
    <span className="inkLine" role="group" aria-label={t("Line")}>
      {LINE_CHOICES.map(([choice, label, title]) => {
        const on = value === choice;
        return (
          <button key={label} type="button" className={"ctlBtn inkSegBtn" + (on ? " modeActive" : "")} aria-pressed={on}
            aria-label={t(label)} title={t(title)} onClick={() => onLine(choice)}>
            <LineGlyph dash={choice} /><span>{t(label)}</span>
          </button>
        );
      })}
    </span>
  );
}
