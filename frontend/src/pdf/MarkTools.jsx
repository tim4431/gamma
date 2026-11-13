// The tools a text highlight is made and changed with (docs/dev/settings.md
// "Text highlighters"): the colours, drawn in a style; the account's
// highlighters (a style and a colour each, shared/model/markStyle.js) as
// glyph buttons; the style row; the colour group. The selection popup
// (PdfViewer's PlainTip, through TipMarks: one row, and a fold for the
// rest), the bar over a highlight just made and a highlight's menu
// (MarkMenuRows) draw them; the strip's buttons and their menus are
// HighlighterStrip.jsx's. Nothing here writes a preference but the colour
// group and Save look as highlighter: a swatch or a style picked here marks
// or changes one highlight, never a highlighter.
//
// App provides MarkToolsContext, useTextHighlighter's `markTools`:
// `presets` (the list in effect), `shown` / `hidden`, `keyIds` (what keys
// 1–9 press), `armedId`, `saveLook(look)`, `setGroup(id)`,
// `openHighlighters()` and `keys` (whether the popup takes 1–9). Without a
// provider, no highlighters.
import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import {
  MARK_STYLES, MAX_PRESETS, STYLE_NAMES, isLineStyle, markName, presetFor, presetLook, presetName, swatchOf,
} from "../shared/model/markStyle.js";
import { colorName, highlightColor, pickerColors, swatchCss, swatchMarker } from "../shared/model/palette.js";
import { usePalette } from "../shared/ui/paletteContext.js";
import { MenuDivider, MenuItem, SubMenuItem } from "../shared/ui/Menus";
import { MarkGlyph } from "../shared/ui/MarkGlyph.jsx";
import { CheckIcon, ChevronDownIcon, HighlightIcon, PlusIcon, SettingsIcon, Trash2Icon } from "../shared/ui/Icons";
import { isTextField } from "../shared/lib/hotkeys.js";
import { t } from "../shared/i18n/i18n.js";
import { popupKey, stripPresets } from "./highlighterState.js";

export const MarkToolsContext = createContext(null);
const NO_TOOLS = Object.freeze({
  presets: [], shown: [], hidden: [], keyIds: [], armedId: null, saveLook: null, setGroup: null, openHighlighters: null, keys: false,
});
export const useMarkTools = () => useContext(MarkToolsContext) || NO_TOOLS;

// How a button acts: on its click (a menu's), unless the caller passes the
// popup's `act`, which acts on the press and keeps the PDF selection.
const onClick = (run) => ({ onClick: run });
const keyOf = (mark, id) => mark.keyIds.indexOf(id) + 1;
const sameSwatch = (a, b) => !!a && !!b && a.hex === b.hex && Math.abs(a.alpha - b.alpha) < 0.005;

// A highlighter drawn small: its line in its colour (MarkGlyph), or for the
// wash the marker icon over a bar of its colour, as the strip has always
// drawn the text highlighter.
export function HighlighterGlyph({ preset, size = 16 }) {
  const color = highlightColor(preset.color);
  return isLineStyle(preset.style) ? <MarkGlyph style={preset.style} color={color} size={size + 2} /> : (
    <><HighlightIcon size={size} /><span className="inkToolInk" style={{ background: color }} aria-hidden="true" /></>
  );
}

// The four styles, the picked one pressed, each drawn in `color` (as
// stored; the letter alone without one). In the popup a radio group, which
// the arrow keys walk; in a menu its rows.
export function StyleRow({ value, color, onPick, act = onClick, menu = false }) {
  const keys = (e) => {
    const at = MARK_STYLES.indexOf(value);
    const to = { ArrowRight: at + 1, ArrowDown: at + 1, ArrowLeft: at - 1, ArrowUp: at - 1, Home: 0, End: MARK_STYLES.length - 1 }[e.key];
    if (to === undefined || menu) return;
    e.preventDefault();
    const next = MARK_STYLES[(to + MARK_STYLES.length) % MARK_STYLES.length];
    onPick(next);
    e.currentTarget.parentElement?.querySelector(`[data-mark-style="${next}"]`)?.focus();
  };
  return (
    <div className="markStyles" role={menu ? "group" : "radiogroup"} aria-label={t("Style")}>
      {MARK_STYLES.map((s) => (
        <button key={s} type="button" role={menu ? "menuitemradio" : "radio"} aria-checked={s === value}
          tabIndex={menu || s === value ? undefined : -1}
          className={"markStyleBtn" + (s === value ? " selected" : "")} data-mark-style={s}
          title={t(STYLE_NAMES[s])} aria-label={t(STYLE_NAMES[s])} onKeyDown={keys} {...act(() => onPick(s))}>
          <MarkGlyph style={s} color={color} />
        </button>
      ))}
    </div>
  );
}

// The highlight picker's swatches in `style`: a wash as the wash it lays,
// a line style as that line in the swatch's colour (`.markLine`), so a
// swatch shows what it makes. `selected(swatch)` marks one; `label(swatch)`
// names it (markName, unless the caller says what a press does instead);
// `standIn(i)` is the highlighter a wash swatch stands for, whose name,
// key and armed ring it then carries (popupMarks).
export function SwatchRow({ colors, style, onPick, selected = null, label = null, act = onClick, menu = false, guide = undefined, standIn = null, mark = null }) {
  const line = isLineStyle(style);
  return colors.map((c, i) => {
    const on = !!selected?.(c);
    const p = standIn?.(i) || null;
    const look = label ? label(c) : markName(style, c);
    const armed = !!p && mark?.armedId === p.id;
    const key = p && mark ? keyOf(mark, p.id) : 0;
    const name = p?.name ? `${p.name} · ${look}` : look;
    return (
      <button key={c.hex + c.alpha + i} type="button" role={menu ? "menuitemradio" : undefined} aria-checked={menu ? on : undefined}
        aria-pressed={menu || !selected ? undefined : on}
        className={"colorBtn" + (line ? " markLine" : "") + (on ? " selected" : "") + (armed ? " armed" : "")} data-guide={guide}
        data-hl-preset={p?.id} aria-current={armed ? "true" : undefined} aria-keyshortcuts={key ? String(key) : undefined}
        style={line ? undefined : { background: swatchCss(c) }} title={key ? `${name} · ${t("Key {n}", { n: key })}` : name} aria-label={name}
        {...act(() => onPick(c))}>
        {line ? <MarkGlyph style={style} color={highlightColor(c)} /> : null}
      </button>
    );
  });
}

// Highlighters as glyph buttons (`.markBtn`), each its look; `label(p)`
// names a press; `current` the id to mark as the highlight's own look ("" for
// none of them; null where there is no own look to mark).
export function HighlighterButtons({ list, onPick, label, current = null, act = onClick, menu = false, mark }) {
  return list.map((p) => {
    const key = keyOf(mark, p.id);
    const armed = mark.armedId === p.id;
    const name = label(p);
    return (
      <button key={p.id} type="button" role={menu ? "menuitemradio" : undefined} aria-checked={menu ? current === p.id : undefined}
        aria-pressed={menu || current == null ? undefined : current === p.id}
        className={"markBtn" + (current === p.id ? " selected" : "") + (armed ? " armed" : "")} data-hl-preset={p.id}
        aria-current={armed ? "true" : undefined} aria-keyshortcuts={key ? String(key) : undefined} aria-label={name}
        title={[name, p.name ? presetLook(p) : "", key ? t("Key {n}", { n: key }) : ""].filter(Boolean).join(" · ")}
        {...act(() => onPick(p))}>
        <MarkGlyph style={p.style} color={highlightColor(p.color)} />
      </button>
    );
  });
}

// Which of `list` (the shown highlighters) a swatch drawn as a wash stands
// for — a wash highlighter in exactly that swatch's colour, so the row does
// not show it twice — and the rest, drawn as their own buttons.
export function popupMarks(list, colors, style) {
  const by = new Map();
  if (style === "highlight") {
    for (const p of list) {
      if (p.style !== "highlight") continue;
      const i = colors.findIndex((c, k) => !by.has(k) && sameSwatch(c, p.color));
      if (i >= 0) by.set(i, p);
    }
  }
  const taken = new Set([...by.values()].map((p) => p.id));
  return { standIn: (i) => by.get(i) || null, rest: list.filter((p) => !taken.has(p.id)) };
}

// The palette's groups with colours, as the radio rows that choose the one
// the highlight picker shows (palette.pickers.highlight), each with its
// first swatches.
export function GroupRows({ palette, onPick, act = onClick, menu = false }) {
  const current = palette.pickers?.highlight;
  return (
    <div className="markGroups" role={menu ? "group" : "radiogroup"} aria-label={t("Color group")}>
      {palette.groups.filter((g) => g.colors.length).map((g) => (
        <button key={g.id} type="button" role={menu ? "menuitemradio" : "radio"} aria-checked={g.id === current}
          className={"markGroup" + (g.id === current ? " selected" : "")} {...act(() => onPick(g.id))}>
          <span className="markGroupDots" aria-hidden="true">
            {g.colors.slice(0, 6).map((c, i) => <span key={i} style={{ background: swatchCss(c) }} />)}
          </span>
          <span className="markGroupName">{g.name}</span>
          {g.colors.length > 6 ? <span className="markGroupMore">+{g.colors.length - 6}</span> : null}
          {g.id === current ? <CheckIcon size={14} /> : null}
        </button>
      ))}
    </div>
  );
}

// The selection popup's marking (PlainTip): one row of the swatches (drawn
// in the popup's one-shot style, the wash until the fold's Style row says
// otherwise), the shown highlighters (but a wash one a swatch stands for)
// and the fold's button, then `tools` (the popup's own buttons). A press
// marks the selection: `onMark({color, style})`. On a highlight made just
// now (`made`, its look `own`) the row changes that one instead: a swatch
// its colour alone (onMark({color})), a highlighter its colour and style,
// the fold's Style row its style in place (`onStyle`), and the bar adds
// Save look as highlighter (`onSave`) and Remove (`onRemove`). An `area` is
// a box: its swatches and the tools alone. `act` is the popup's press;
// `onLeave()` puts the popup away, as Edit highlighters… opens Settings
// over it. While it is open, keys 1–9 press the highlighter at that key,
// and Enter the armed one (highlighterState.js popupKey).
export function TipMarks({ area = false, made = false, own = null, act, onMark, onStyle = null, onSave = null, onRemove = null, onLeave = null, tools = null, guide }) {
  const palette = usePalette();
  const mark = useMarkTools();
  const colors = pickerColors(palette, "highlight");
  const [fold, setFold] = useState(false);
  const [oneShot, setOneShot] = useState(made && own ? own.style : "highlight");
  const style = area ? "highlight" : oneShot;
  // the strip's own buttons: one armed from the hidden ones too, which a
  // finger's selection then marks with in one tap
  const row = stripPresets(mark.presets, mark.armedId);
  const { standIn, rest } = area ? { standIn: null, rest: [] } : popupMarks(row, colors, style);
  const more = mark.hidden.filter((p) => p.id !== mark.armedId);
  const ownSwatch = made && own ? swatchOf(own.color) : null;
  const ownPreset = made && own ? presetFor(mark.presets, own.style, own.color) : null;
  const pickPreset = (p) => onMark({ color: p.color, style: p.style });
  // The keys, in the capture phase (before the page's own), while this popup
  // is open: not in a field (a note being typed into after the highlighter
  // made its highlight keeps its digits), a dialog, nor over an area.
  const latest = useRef(null);
  latest.current = { mark, pickPreset };
  useEffect(() => {
    if (area || !mark.keys) return undefined;
    const onKey = (e) => {
      const { mark: m, pickPreset: pick } = latest.current;
      const focused = document.activeElement;
      const act2 = popupKey(e, { list: m.presets, armed: m.armedId, inField: isTextField(focused),
        onControl: !!focused?.closest?.("button, a[href], select, [role=button], [role=menuitem], [role=radio], [role=menuitemradio]"),
        dialog: !!document.querySelector(".reportOverlay, .ctxMenu") });
      const p = act2 && m.presets.find((x) => x.id === act2.id);
      if (!p) return;
      e.preventDefault();
      e.stopPropagation();
      pick(p);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [area, mark.keys]);
  return (
    <>
      <div className="colorRow" role="toolbar" aria-label={t("Mark the selection")}>
        <SwatchRow colors={colors} style={style} act={act} guide={guide} mark={mark} standIn={standIn}
          selected={ownSwatch ? (c) => sameSwatch(c, ownSwatch) : null}
          label={made ? (c) => t("Change to {color}", { color: t(colorName(c)) }) : null}
          onPick={(c) => (made ? onMark({ color: c }) : onMark({ color: c, style: area ? undefined : style }))} />
        <HighlighterButtons list={rest} act={act} mark={mark} current={made ? ownPreset?.id || "" : null} onPick={pickPreset}
          label={(p) => (made ? t("Change to {name}", { name: presetName(p) }) : t("Highlight as {name}", { name: presetName(p) }))} />
        {area ? null : (
          <button type="button" className={"colorBtn linkTipBtn markFoldBtn" + (fold ? " selected" : "") + (style !== "highlight" ? " on" : "")}
            aria-expanded={fold} title={t("More styles and highlighters")} aria-label={t("More styles and highlighters")}
            {...act(() => setFold(!fold))}>
            {style !== "highlight" ? <MarkGlyph style={style} size={14} /> : <ChevronDownIcon size={14} />}
          </button>
        )}
        {tools}
        {onRemove ? (
          <button type="button" className="colorBtn linkTipBtn" title={t("Remove highlight")} aria-label={t("Remove highlight")} {...act(onRemove)}>
            <Trash2Icon size={14} />
          </button>
        ) : null}
      </div>
      {fold && !area ? (
        <div className="markFold" role="group" aria-label={t("More styles and highlighters")}>
          <div className="markFoldRow">
            <span className="markFoldLabel">{made ? t("Style") : t("Mark as")}</span>
            <StyleRow value={style} color={made && own ? own.color : highlightColor(colors[0])} act={act}
              onPick={(s) => { setOneShot(s); if (made) onStyle?.(s); }} />
          </div>
          {more.length ? (
            <div className="markFoldRow">
              <span className="markFoldLabel">{t("More highlighters")}</span>
              <div className="markRow">
                <HighlighterButtons list={more} act={act} mark={mark} current={made ? ownPreset?.id || "" : null} onPick={pickPreset}
                  label={(p) => (made ? t("Change to {name}", { name: presetName(p) }) : t("Highlight as {name}", { name: presetName(p) }))} />
              </div>
            </div>
          ) : null}
          {mark.setGroup && palette.groups.filter((g) => g.colors.length).length > 1 ? (
            <GroupRows palette={palette} onPick={mark.setGroup} act={act} />
          ) : null}
          <div className="markFoldActions">
            {made && onSave && !ownPreset ? (
              <button type="button" className="uiBtn sm" disabled={mark.presets.length >= MAX_PRESETS}
                title={mark.presets.length >= MAX_PRESETS ? t("Up to {n} highlighters", { n: MAX_PRESETS }) : undefined} {...act(onSave)}>
                <PlusIcon size={14} />{t("Save look as highlighter")}
              </button>
            ) : null}
            {mark.openHighlighters ? (
              <button type="button" className="uiBtn sm" {...act(() => { onLeave?.(); mark.openHighlighters(); })}>
                <SettingsIcon size={14} />{t("Edit highlighters…")}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}

// A highlight's menu rows (its right-click or held finger; a translated
// paragraph's Highlight row, `nested`): the highlighters, a press giving it
// that one's look; the swatches in its own style, a press its colour; for a
// passage (`line`; not an area or a link region) the style row, which
// restyles it in place and keeps the menu open for a colour; the hidden
// highlighters as a flyout (not when `nested`: a flyout of its own would
// close the one it is in); Save look as highlighter, when no highlighter
// has its look. `own` is its {style, color}. `onPreset(p)`, `onColor(swatch)`
// and `onSave()` close the menu (`onClose`).
export function MarkMenuRows({ own, line = true, nested = false, onPreset, onColor, onStyle, onSave = null, onClose }) {
  const palette = usePalette();
  const mark = useMarkTools();
  const colors = pickerColors(palette, "highlight");
  const style = line ? own.style : "highlight";
  const mine = swatchOf(own.color);
  const ownPreset = line ? presetFor(mark.presets, own.style, own.color) : null;
  const { standIn, rest } = line ? popupMarks(mark.shown, colors, style) : { standIn: null, rest: [] };
  const change = (p) => t("Change to {name}", { name: presetName(p) });
  return (
    <>
      <div className="markMenu">
        <div className="colorRow">
          <SwatchRow colors={colors} style={style} menu mark={mark} standIn={standIn} onPick={(c) => { onColor(c); onClose(); }}
            selected={(c) => sameSwatch(c, mine)} label={(c) => t("Change to {color}", { color: t(colorName(c)) })} />
          {line ? <HighlighterButtons list={rest} menu mark={mark} current={ownPreset?.id || ""} label={change}
            onPick={(p) => { onPreset(p); onClose(); }} /> : null}
        </div>
        {line ? <StyleRow value={own.style} color={own.color} onPick={onStyle} menu /> : null}
      </div>
      {line && mark.hidden.length && !nested ? (
        <SubMenuItem id="moreHighlighters" icon={HighlightIcon} label={t("More highlighters")}>
          {mark.hidden.map((p) => {
            const key = keyOf(mark, p.id);
            return (
              <MenuItem key={p.id} role="menuitemradio" aria-checked={ownPreset?.id === p.id}
                icon={(props) => <MarkGlyph style={p.style} color={highlightColor(p.color)} size={props.size} />}
                keys={key ? String(key) : undefined} onClick={() => { onPreset(p); onClose(); }}>
                {presetName(p)}
              </MenuItem>
            );
          })}
        </SubMenuItem>
      ) : null}
      {line && onSave && !ownPreset ? (
        <MenuItem icon={PlusIcon} disabled={mark.presets.length >= MAX_PRESETS}
          title={mark.presets.length >= MAX_PRESETS ? t("Up to {n} highlighters", { n: MAX_PRESETS }) : undefined}
          onClick={() => { onSave(); onClose(); }}>{t("Save look as highlighter")}</MenuItem>
      ) : null}
      <MenuDivider />
    </>
  );
}
