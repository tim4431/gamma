// The tools a text highlight is made and changed with (docs/dev/settings.md
// "Highlight styles and presets"): the style row, the colours drawn in that
// style, the account's presets and the colour group they come from. The
// selection popup (PdfViewer's PlainTip, through TipMarks: the rows, and a
// fold with the groups, the way to Settings and a new preset), a
// highlight's menu and the text highlighter's (App's MarkMenuRows) draw
// them. App provides MarkToolsContext: `style` and `setStyle` (the
// highlightStyle preference, the style a new highlight takes), `color` (the
// colour the highlighter takes, as stored), `presets` and `setPresets`
// (highlightPresets), `setGroup(id)` (the group the highlight picker
// shows) and `openColors()` (Settings → Appearance › Colors). Without a
// provider, the wash and no presets.
import React, { createContext, useContext, useState } from "react";
import { MARK_STYLES, MAX_PRESETS, MAX_PRESET_NAME, STYLE_NAMES, isLineStyle, newPresetId, presetFor, swatchOf } from "../shared/model/markStyle.js";
import { colorName, highlightColor, lineColor, pickerColors, swatchCss, swatchMarker } from "../shared/model/palette.js";
import { usePalette } from "../shared/ui/paletteContext.js";
import { MenuDivider, MenuItem, SubMenuItem } from "../shared/ui/Menus";
import { MarkGlyph } from "../shared/ui/MarkGlyph.jsx";
import { CheckIcon, PaletteIcon, PlusIcon, SettingsIcon } from "../shared/ui/Icons";
import { composing } from "../shared/lib/hotkeys.js";
import { t } from "../shared/i18n/i18n.js";

export const MarkToolsContext = createContext(null);
const NO_TOOLS = Object.freeze({ style: "highlight", setStyle: () => {}, color: "", presets: [], setPresets: null, setGroup: null, openColors: null });
export const useMarkTools = () => useContext(MarkToolsContext) || NO_TOOLS;

// How a button acts: on its click (a menu's), unless the caller passes the
// popup's `act`, which acts on the press and keeps the PDF selection.
const onClick = (run) => ({ onClick: run });

// What a swatch, a style or a preset is called: "Underline in Blue".
export const markName = (style, color) => (style === "highlight"
  ? t("Highlight in {color}", { color: t(colorName(color)) })
  : t("{style} in {color}", { style: t(STYLE_NAMES[style]), color: t(colorName(color)) }));

// The four styles, the picked one pressed, each drawn in `color` (as
// stored; the letter alone without one). `menu`: rows of a menu.
export function StyleRow({ value, color, onPick, act = onClick, menu = false }) {
  return (
    <div className="markStyles" role={menu ? "group" : "radiogroup"} aria-label={t("Style")}>
      {MARK_STYLES.map((s) => (
        <button key={s} type="button" role={menu ? "menuitemradio" : "radio"} aria-checked={s === value}
          className={"markStyleBtn" + (s === value ? " selected" : "")} data-mark-style={s}
          title={t(STYLE_NAMES[s])} aria-label={t(STYLE_NAMES[s])} {...act(() => onPick(s))}>
          <MarkGlyph style={s} color={color} />
        </button>
      ))}
    </div>
  );
}

// The highlight picker's swatches in `style`: a wash as the wash it lays,
// a line style as the line's colour (lineColor). `selected(swatch)` marks
// one; `label(swatch)` names it (markName, unless the caller says what a
// press does instead).
export function SwatchRow({ colors, style, onPick, selected = null, label = null, act = onClick, menu = false, guide = undefined }) {
  const line = isLineStyle(style);
  return colors.map((c, i) => {
    const on = !!selected?.(c);
    const name = label ? label(c) : markName(style, c);
    return (
      <button key={c.hex + c.alpha + i} type="button" role={menu ? "menuitemradio" : undefined} aria-checked={menu ? on : undefined}
        className={"colorBtn" + (on ? " selected" : "")} data-guide={guide}
        style={{ background: line ? lineColor(c.hex) : swatchCss(c) }} title={name} aria-label={name}
        {...act(() => onPick(c))} />
    );
  });
}

// The presets as chips, each its style in its colour and its name; `current`
// the id of the one to mark.
export function PresetRow({ presets, onPick, current = "", act = onClick, menu = false }) {
  if (!presets?.length) return null;
  return (
    <div className="markPresets" role="group" aria-label={t("Presets")}>
      {presets.map((p) => {
        const color = highlightColor(p.color);
        return (
          <button key={p.id} type="button" role={menu ? "menuitem" : undefined}
            className={"markPreset" + (current === p.id ? " selected" : "")}
            title={`${p.name} · ${markName(p.style, p.color)}`} {...act(() => onPick(p))}>
            <MarkGlyph style={p.style} color={color} size={14} />
            <span className="markPresetName">{p.name}</span>
          </button>
        );
      })}
    </div>
  );
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

// The selection popup's marking (PlainTip): the style row (not for an
// area, which is a box), the swatches in that style, the fold's button,
// then `tools` (the popup's own buttons), the presets under them, and the
// fold. `onMark({color, style})` makes the highlight in a swatch (`style`
// the one picked) or a preset; on a highlight the highlighter made
// (`recolor`) it changes that one, and `onStyle(style)` restyles it as the
// style row is pressed. `act` is the popup's press; `onLeave()` puts the
// popup away, as the fold's way to Settings opens the dialog over it.
export function TipMarks({ area = false, recolor = false, act, onMark, onStyle = null, onLeave = null, tools = null, guide }) {
  const palette = usePalette();
  const mark = useMarkTools();
  const colors = pickerColors(palette, "highlight");
  const [fold, setFold] = useState(false);
  const [draft, setDraft] = useState(null); // a preset being named: {name, color: swatch}
  const style = area ? "highlight" : mark.style;
  const pickStyle = (s) => { mark.setStyle(s); onStyle?.(s); };
  // On a highlight made already its colour is the highlighter's; a preset
  // being named marks the one it takes.
  const current = draft ? draft.color : recolor ? swatchOf(mark.color) : null;
  const isCurrent = current ? swatchMarker(colors, current.hex, current.alpha) : null;
  const canPreset = !!mark.setPresets && !area;
  const save = () => {
    const name = draft.name.trim();
    if (!name || mark.presets.length >= MAX_PRESETS) return;
    mark.setPresets([...mark.presets, { id: newPresetId(mark.presets), name: name.slice(0, MAX_PRESET_NAME), style, color: draft.color }]);
    setDraft(null);
  };
  const shownPreset = recolor ? presetFor(mark.presets, style, mark.color)?.id : "";
  return (
    <>
      <div className="colorRow">
        {area ? null : (
          <>
            <StyleRow value={style} color={draft ? highlightColor(draft.color) : mark.color || highlightColor(colors[0])} onPick={pickStyle} act={act} />
            <span className="tipDivider" aria-hidden="true" />
          </>
        )}
        <SwatchRow colors={colors} style={style} act={act} guide={guide} selected={isCurrent}
          label={draft ? (c) => t("Use {color} for the preset", { color: t(colorName(c)) })
            : recolor ? (c) => t("Change to {color}", { color: t(colorName(c)) }) : null}
          onPick={(c) => (draft ? setDraft({ ...draft, color: c }) : onMark({ color: c, style: area ? undefined : style }))} />
        <button type="button" className={"colorBtn linkTipBtn markFoldBtn" + (fold ? " selected" : "")} aria-expanded={fold}
          title={t("Color groups and presets")} aria-label={t("Color groups and presets")} {...act(() => { setFold(!fold); setDraft(null); })}>
          <PaletteIcon size={14} />
        </button>
        {tools}
      </div>
      {draft || area ? null : (
        <PresetRow presets={mark.presets} act={act} current={shownPreset}
          onPick={(p) => onMark({ color: p.color, style: p.style })} />
      )}
      {fold ? (
        <div className="markFold">
          <div className="markFoldHead">
            <span>{t("Colors")}</span>
            {mark.openColors ? (
              <button type="button" className="uiBtn sm markFoldEdit" {...act(() => { onLeave?.(); mark.openColors(); })}>
                <SettingsIcon size={14} />{t("Edit colors and presets…")}
              </button>
            ) : null}
          </div>
          {mark.setGroup ? <GroupRows palette={palette} onPick={mark.setGroup} act={act} /> : null}
          {canPreset && !draft ? (
            <button type="button" className="uiBtn sm markFoldNew" disabled={mark.presets.length >= MAX_PRESETS}
              title={mark.presets.length >= MAX_PRESETS ? t("Up to {n} presets", { n: MAX_PRESETS }) : undefined}
              {...act(() => setDraft({ name: "", color: swatchOf(mark.color) || colors[0] }))}>
              <PlusIcon size={14} />{t("New preset…")}
            </button>
          ) : null}
          {draft ? (
            <form className="markDraft" onSubmit={(e) => { e.preventDefault(); save(); }}>
              <span className="settingDesc">{t("Pick its style and colour above, then name it.")}</span>
              <div className="markDraftRow">
                <MarkGlyph style={style} color={highlightColor(draft.color)} />
                {/* the field takes the focus; the popup keeps the PDF selection it opened for */}
                <input className="aiKeyInput" type="text" autoFocus maxLength={MAX_PRESET_NAME} value={draft.name}
                  placeholder={t("Name, e.g. Definition")} aria-label={t("Preset name")}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  onKeyDown={(e) => { if (e.key === "Escape" && !composing(e)) { e.stopPropagation(); setDraft(null); } }} />
                <button type="button" className="uiBtn sm" onClick={() => setDraft(null)}>{t("Cancel")}</button>
                <button type="submit" className="uiBtn sm primary" disabled={!draft.name.trim()}>{t("Save")}</button>
              </div>
            </form>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

// A menu's marking rows (a highlight's menu, the text highlighter's): the
// style row (`style`, null for an area, which has none), the swatches in
// that style and the presets, then the colour group as a flyout (with two
// groups or more) and the way to Settings, closing the menu with
// `onClose`. `onStyle` keeps the menu open (a colour may follow);
// `onColor(swatch)` and `onPreset(preset)` are the menu's to close.
// `selected` marks a swatch, `label` names one; `divider` the line under
// the rows, before the menu's own. `nested`: the rows are a flyout's
// already (a translated paragraph's Highlight row), where a flyout of
// theirs would close the one they are in; they offer the group in Settings.
export function MarkMenuRows({ style, color, onStyle, onColor, onPreset, selected = null, label = null, onClose, divider = true, nested = false }) {
  const palette = usePalette();
  const mark = useMarkTools();
  const colors = pickerColors(palette, "highlight");
  const groups = palette.groups.filter((g) => g.colors.length);
  return (
    <>
      <div className="markMenu">
        {style ? <StyleRow value={style} color={color} onPick={onStyle} menu /> : null}
        <div className="colorRow">
          <SwatchRow colors={colors} style={style || "highlight"} onPick={onColor} selected={selected} label={label} menu />
        </div>
        {style ? <PresetRow presets={mark.presets} onPick={onPreset} menu
          current={presetFor(mark.presets, style, color)?.id || ""} /> : null}
      </div>
      {groups.length > 1 && mark.setGroup && !nested ? (
        <SubMenuItem id="markGroup" icon={PaletteIcon} label={t("Color group")}>
          <GroupRows palette={palette} onPick={mark.setGroup} menu />
        </SubMenuItem>
      ) : null}
      {mark.openColors ? (
        <MenuItem icon={SettingsIcon} onClick={() => { onClose(); mark.openColors(); }}>{t("Edit colors and presets…")}</MenuItem>
      ) : null}
      {divider ? <MenuDivider /> : null}
    </>
  );
}
