import React from "react";
import { PaneHead, Section, Row, Toggle, Stepper, PictureChoices, Segmented } from "./SettingsKit";
import { MenuSelect } from "../shared/ui/Menus";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { LibraryDisplaySettings } from "./SettingsLibraryDisplay";
import {
  CloudIcon, ContrastIcon, CopyIcon, HelpCircleIcon, HighlightIcon, LayoutIcon, MaximizeIcon, MoonIcon, PaletteIcon, PenIcon,
  PlusIcon, TrashIcon, TypeIcon, XIcon,
} from "../shared/ui/Icons";
import { ThemePreview, PdfPreview } from "../shared/illustrations";
import { UI_SCALE, themeScheme } from "../app/prefs";
import {
  DEFAULT_PALETTE, MAX_GROUPS, MAX_GROUP_COLORS, MAX_GROUP_NAME, WASH_ALPHA, WASH_COLORS,
  colorName, isDefaultPalette, newGroupId, normalizePalette, swatchCss,
} from "../shared/model/palette.js";
import { ColorPicker } from "../shared/ui/ColorPicker";
import { T, t, tn } from "../shared/i18n/i18n.js";

// The swatches paint from each theme's own tokens (ThemePreview).
const THEMES = [
  ["system", T("System"), T("Match your device")],
  ["light", T("Light"), T("Bright & crisp")],
  ["dark", T("Dark"), T("A quieter backdrop")],
  ["gamma-light", T("Gamma Light"), T("Warm gray & amber")],
  ["gamma-dark", T("Gamma Dark"), T("Charcoal & soft gold")],
  ["sepia", T("Sepia"), T("Warm paper, deep ink")],
  ["solarized", T("Solarized Light"), T("Warm paper, softer ink")],
  ["gray", T("Gray"), T("Soft & neutral")],
];

// One colour group of the palette (Appearance › Colors): its name, its
// swatches (each drawn at its alpha; the selected one opens the editor
// below: a native colour picker, the alpha, Remove), a "+" swatch, and
// Duplicate / Delete. onChange(group) replaces the group; onDelete /
// onDuplicate act on it.
function PaletteGroup({ group, canAdd, canDelete, onChange, onDelete, onDuplicate }) {
  const [editing, setEditing] = React.useState(null); // the index of the swatch open in the editor
  const open = editing !== null && group.colors[editing] ? group.colors[editing] : null;
  const setColor = (i, patch) => onChange({ ...group, colors: group.colors.map((c, k) => (k === i ? { ...c, ...patch } : c)) });
  const add = () => {
    if (group.colors.length >= MAX_GROUP_COLORS) return;
    const last = group.colors.at(-1);
    onChange({ ...group, colors: [...group.colors, { hex: last?.hex || WASH_COLORS[0], alpha: last?.alpha ?? 1 }] });
    setEditing(group.colors.length);
  };
  const remove = (i) => { onChange({ ...group, colors: group.colors.filter((_, k) => k !== i) }); setEditing(null); };
  const name = (c) => t(colorName(c));
  return (
    <div className="setCard paletteGroup" data-setting={group.name}>
      <div className="paletteGroupHead">
        <input className="paletteGroupName" value={group.name} maxLength={MAX_GROUP_NAME} aria-label={t("Group name")}
          onChange={(e) => onChange({ ...group, name: e.target.value })}
          onBlur={(e) => { if (!e.target.value.trim()) onChange({ ...group, name: t("Group") }); }} />
        <span className="paletteGroupCount">{tn("{n} color", "{n} colors", group.colors.length)}</span>
        <button type="button" className="uiClose uiCloseSm" title={t("Duplicate group")} aria-label={t("Duplicate group {name}", { name: group.name })}
          disabled={!canAdd} onClick={onDuplicate}><CopyIcon size={14} /></button>
        <button type="button" className="uiClose uiCloseSm" title={t("Delete group")} aria-label={t("Delete group {name}", { name: group.name })}
          disabled={!canDelete} onClick={onDelete}><TrashIcon size={14} /></button>
      </div>
      <div className="paletteSwatches" role="group" aria-label={t("Colors of {name}", { name: group.name })}>
        {group.colors.map((c, i) => (
          <button key={i} type="button" className={"colorBtn inkSwatch paletteSwatch" + (editing === i ? " selected" : "")}
            style={{ background: swatchCss(c) }} title={`${name(c)} · ${Math.round(c.alpha * 100)}%`}
            aria-label={t("{name}, {pct}% opaque", { name: name(c), pct: Math.round(c.alpha * 100) })} aria-pressed={editing === i}
            onClick={() => setEditing(editing === i ? null : i)} />
        ))}
        <button type="button" className="colorBtn inkSwatch paletteSwatch paletteAdd" title={t("Add color")} aria-label={t("Add color to {name}", { name: group.name })}
          disabled={group.colors.length >= MAX_GROUP_COLORS} onClick={add}><PlusIcon size={14} /></button>
      </div>
      {open ? (
        <div className="paletteEditor" role="group" aria-label={t("Edit color")}>
          <ColorPicker value={open} onChange={(c) => setColor(editing, c)} />
          <div className="paletteEditorSide">
            <span className="paletteEditorName">{name(open)}</span>
            <span className="settingDesc">{t("Text highlights use the opacity; pens take it as their stroke opacity.")}</span>
            <button type="button" className="uiBtn sm" onClick={() => remove(editing)}>{t("Remove")}</button>
          </div>
          <button type="button" className="uiClose uiCloseSm" aria-label={t("Close color editor")} onClick={() => setEditing(null)}><XIcon size={14} /></button>
        </div>
      ) : null}
    </div>
  );
}

export function AppearanceSettings({ value, diagnostics }) {
  const palette = normalizePalette(value.palette) || DEFAULT_PALETTE;
  const setPalette = (next) => value.setPalette(normalizePalette(next) || DEFAULT_PALETTE);
  const setGroup = (id, group) => setPalette({ ...palette, groups: palette.groups.map((g) => (g.id === id ? group : g)) });
  const addGroup = (from) => {
    if (palette.groups.length >= MAX_GROUPS) return;
    const group = from
      ? { ...from, id: newGroupId(palette.groups), name: t("{name} copy", { name: from.name }).slice(0, MAX_GROUP_NAME), colors: from.colors.map((c) => ({ ...c })) }
      : { id: newGroupId(palette.groups), name: t("New group"), colors: [{ hex: WASH_COLORS[0], alpha: WASH_ALPHA }] };
    const at = from ? palette.groups.findIndex((g) => g.id === from.id) + 1 : palette.groups.length;
    setPalette({ ...palette, groups: [...palette.groups.slice(0, at), group, ...palette.groups.slice(at)] });
  };
  const deleteGroup = (id) => setPalette({ ...palette, groups: palette.groups.filter((g) => g.id !== id) });
  const setPicker = (picker) => (id) => setPalette({ ...palette, pickers: { ...palette.pickers, [picker]: id } });
  const groupOptions = palette.groups.map((g) => [g.id, g.name]); // MenuSelect translates a label; a user's name is its own fallback
  return (
    <div className="appearanceSettings">
      <PaneHead icon={ContrastIcon} title={t("Appearance")} />

      <Section title={t("Theme")} scope="account" prefs={SECTION_PREFS.appearance["Theme"]}>
        <PictureChoices label={t("Theme")} value={value.theme} onChange={value.setTheme}
          options={THEMES.map(([id, label, hint]) => ({ value: id, label, hint, preview: <ThemePreview theme={id} scheme={themeScheme(id)} /> }))} />
      </Section>

      <Section title={t("PDF pages")} scope="account" prefs={SECTION_PREFS.appearance["PDF pages"]}>
        <div className="appearancePdf">
          <PdfPreview dark={value.pdfDarkPage || value.theme === "gamma-dark"} />
          <div className="appearancePdfControls">
            <Toggle icon={MoonIcon} label={t("Dark PDF pages")}
              hint={value.theme === "gamma-dark" ? t("Gamma Dark already uses dark pages.") : t("Light text on a dark page; figures invert too.")}
              title={t("Display only — your files and exports keep their original colors.")}
              checked={value.pdfDarkPage} onChange={value.setPdfDarkPage} />
          </div>
        </div>
      </Section>

      <Section title={t("Colors")} scope="account" prefs={SECTION_PREFS.appearance["Colors"]}
        action={(
          <button type="button" className="uiBtn sm" disabled={isDefaultPalette(palette)}
            title={t("Put the default groups and colours back")} onClick={() => value.setPalette(DEFAULT_PALETTE)}>
            {t("Reset colors")}
          </button>
        )}>
        <Row icon={PaletteIcon} label={t("Color groups")}
          hint={t("Click a swatch to change its colour or opacity. Existing highlights and strokes keep their colours.")}>
          <button type="button" className="uiBtn sm" disabled={palette.groups.length >= MAX_GROUPS} onClick={() => addGroup(null)}>
            <PlusIcon size={14} />{t("Add group")}
          </button>
        </Row>
        {palette.groups.map((g) => (
          <PaletteGroup key={g.id} group={g} canAdd={palette.groups.length < MAX_GROUPS} canDelete={palette.groups.length > 1}
            onChange={(group) => setGroup(g.id, group)} onDelete={() => deleteGroup(g.id)} onDuplicate={() => addGroup(g)} />
        ))}
        <Row icon={HighlightIcon} label={t("Text highlights")} hint={t("Highlighting selected PDF text, and recolouring a highlight from its menu.")}>
          <MenuSelect label={t("Text highlights")} value={palette.pickers.highlight} onChange={setPicker("highlight")} options={groupOptions} />
        </Row>
        <Row icon={HighlightIcon} label={t("Ink highlights")} hint={t("A highlighter pen in the handwriting tool strip: the colours its options row offers.")}>
          <MenuSelect label={t("Ink highlights")} value={palette.pickers.highlighter} onChange={setPicker("highlighter")} options={groupOptions} />
        </Row>
        <Row icon={PenIcon} label={t("Ink pens")} hint={t("A pen in the handwriting tool strip: the colours its options row offers.")}>
          <MenuSelect label={t("Ink pens")} value={palette.pickers.pen} onChange={setPicker("pen")} options={groupOptions} />
        </Row>
        <Row icon={TypeIcon} label={t("Text boxes")} hint={t("Typed text boxes on a page; boxes are opaque.")}>
          <MenuSelect label={t("Text boxes")} value={palette.pickers.text} onChange={setPicker("text")} options={groupOptions} />
        </Row>
      </Section>

      <Section title={t("Library")} scope="account" prefs={SECTION_PREFS.appearance["Library"]}>
        <LibraryDisplaySettings value={value} />
      </Section>

      <Section title={t("Interface")} scope="browser">
        <div className="appearanceInterface">
          <Row icon={MaximizeIcon} label={t("Interface size")} hint={t("Text, buttons, icons and switches.")}
            title={t("PDF zoom stays separate. To further resize notes or chat text, hold Ctrl (⌘ on Mac) and scroll over that panel.")}>
            <Stepper value={value.uiScale} onChange={value.setUiScale}
              min={UI_SCALE.min} max={UI_SCALE.max} step={UI_SCALE.step} reset={UI_SCALE.default}
              format={(v) => `${Math.round(v * 100)}%`} />
          </Row>
          <Toggle icon={LayoutIcon} label={t("Status bar")} hint={t("Show the latest activity below your tabs.")}
            checked={diagnostics.statusBarVisible} onChange={diagnostics.setStatusBarVisible} />
        </div>
      </Section>

      <Section title={t("Sync status")} scope="account" prefs={SECTION_PREFS.appearance["Sync status"]}>
        <Row icon={CloudIcon} label={t("Sync pill")} hint={t("Where the header shows a page's sync with Gamma Cloud.")}
          title={t("Synced pages: the pill appears only on pages that sync with Gamma Cloud. Every page: it stays in the header on every page of a workspace that syncs some. A clone of another server shows it on every page either way.")}>
          <Segmented value={value.syncPillScope} onChange={value.setSyncPillScope}
            options={[["synced", t("Synced pages")], ["all", t("Every page")]]} />
        </Row>
      </Section>

      <Section title={t("Tours")} scope="account" prefs={SECTION_PREFS.appearance["Tours"]}>
        <Toggle icon={HelpCircleIcon} label={t("Suggest tours")} hint={t("A short tour the first time a feature comes up.")}
          title={t("Off, nothing is offered by itself; every tour stays under Account › Tours.")}
          checked={value.suggestTours} onChange={value.setSuggestTours} />
      </Section>
    </div>
  );
}
