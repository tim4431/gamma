// Appearance › Colors › Highlight presets (shared/model/markStyle.js): the
// account's named styles and colours, which the selection popup, a
// highlight's menu and the text highlighter's offer beside the colours. One
// card each: its look (MarkGlyph), its name, its style, its colour — a
// swatch of the Text highlights group, or any colour from the picker — up
// and down, and Delete; Add preset makes one in the first swatch.
import React from "react";
import { NameInput, Row } from "./SettingsKit";
import { MAX_PRESETS, MAX_PRESET_NAME, newPresetId, normalizePresets } from "../shared/model/markStyle.js";
import { colorName, highlightColor, pickerColors, swatchMarker } from "../shared/model/palette.js";
import { MarkGlyph } from "../shared/ui/MarkGlyph.jsx";
import { ColorPicker } from "../shared/ui/ColorPicker";
import { StyleRow, SwatchRow } from "../pdf/MarkTools.jsx";
import { BookmarkIcon, ChevronDownIcon, ChevronUpIcon, PipetteIcon, PlusIcon, TrashIcon, XIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

function PresetCard({ preset, colors, first, last, onChange, onMove, onDelete }) {
  const [custom, setCustom] = React.useState(false); // the colour picker open under the card
  const color = highlightColor(preset.color);
  const set = (patch) => onChange({ ...preset, ...patch });
  return (
    <div className="setCard presetCard" data-setting={preset.name}>
      <div className="presetHead">
        <MarkGlyph style={preset.style} color={color} size={18} />
        <NameInput className="paletteGroupName" value={preset.name} maxLength={MAX_PRESET_NAME} aria-label={t("Preset name")}
          onCommit={(name) => set({ name })} />
        <button type="button" className="uiClose uiCloseSm" title={t("Move up")} aria-label={t("Move {name} up", { name: preset.name })}
          disabled={first} onClick={() => onMove(-1)}><ChevronUpIcon size={14} /></button>
        <button type="button" className="uiClose uiCloseSm" title={t("Move down")} aria-label={t("Move {name} down", { name: preset.name })}
          disabled={last} onClick={() => onMove(1)}><ChevronDownIcon size={14} /></button>
        <button type="button" className="uiClose uiCloseSm" title={t("Delete preset")} aria-label={t("Delete preset {name}", { name: preset.name })}
          onClick={onDelete}><TrashIcon size={14} /></button>
      </div>
      <div className="presetLook">
        <StyleRow value={preset.style} color={color} onPick={(style) => set({ style })} />
        <div className="paletteSwatches" role="group" aria-label={t("Colors of {name}", { name: preset.name })}>
          <SwatchRow colors={colors} style={preset.style} selected={swatchMarker(colors, preset.color.hex, preset.color.alpha)}
            onPick={(c) => set({ color: { hex: c.hex, alpha: c.alpha } })} />
          <button type="button" className={"colorBtn inkSwatch paletteSwatch paletteAdd" + (custom ? " selected" : "")} aria-pressed={custom}
            title={t("Another colour")} aria-label={t("Another colour for {name}", { name: preset.name })}
            onClick={() => setCustom(!custom)}><PipetteIcon size={14} /></button>
        </div>
      </div>
      {custom ? (
        <div className="paletteEditor" role="group" aria-label={t("Edit color")}>
          <ColorPicker value={preset.color} onChange={(c) => set({ color: c })} />
          <div className="paletteEditorSide">
            <span className="paletteEditorName">{t(colorName(preset.color))}</span>
            <span className="settingDesc">{t("A wash uses the opacity; a line is drawn in a deeper tone of the colour, opaque.")}</span>
          </div>
          <button type="button" className="uiClose uiCloseSm" aria-label={t("Close color editor")} onClick={() => setCustom(false)}><XIcon size={14} /></button>
        </div>
      ) : null}
    </div>
  );
}

// `presets` and `onChange` are the highlightPresets preference; `palette`
// gives the Text highlights group's swatches.
export function HighlightPresets({ presets, onChange, palette }) {
  const list = normalizePresets(presets) || [];
  const colors = pickerColors(palette, "highlight");
  const save = (next) => onChange(normalizePresets(next) || []);
  const add = () => {
    if (list.length >= MAX_PRESETS) return;
    const c = colors[0];
    save([...list, { id: newPresetId(list), name: t("New preset"), style: "underline", color: { hex: c.hex, alpha: c.alpha } }]);
  };
  const move = (i, by) => {
    const next = [...list];
    [next[i], next[i + by]] = [next[i + by], next[i]];
    save(next);
  };
  return (
    <>
      <Row icon={BookmarkIcon} label={t("Highlight presets")}
        hint={t("A named style and colour, such as a blue underline for definitions; offered beside the colours when you highlight.")}>
        <button type="button" className="uiBtn sm" disabled={list.length >= MAX_PRESETS} onClick={add}>
          <PlusIcon size={14} />{t("Add preset")}
        </button>
      </Row>
      {list.map((p, i) => (
        <PresetCard key={p.id} preset={p} colors={colors} first={i === 0} last={i === list.length - 1}
          onChange={(next) => save(list.map((q) => (q.id === p.id ? next : q)))}
          onMove={(by) => move(i, by)} onDelete={() => save(list.filter((q) => q.id !== p.id))} />
      ))}
    </>
  );
}
