import React from "react";
import { PaneHead, Section, Row, Toggle, Stepper, PictureChoices, Segmented } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { LibraryDisplaySettings } from "./SettingsLibraryDisplay";
import { CloudIcon, ContrastIcon, HelpCircleIcon, HighlightIcon, LayoutIcon, MaximizeIcon, MoonIcon, PenIcon } from "../shared/ui/Icons";
import { ThemePreview, PdfPreview } from "../shared/illustrations";
import { UI_SCALE, themeScheme } from "../app/prefs";
import { DEFAULT_PALETTE, colorName } from "../shared/model/palette.js";
import { T, t } from "../shared/i18n/i18n.js";

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

// One role of the palette (Appearance › Colors): its swatches, each a native
// colour picker under the colour it holds, named like the tool strip names
// them. onChange(index, hex) replaces one slot.
function PaletteSwatches({ label, colors, onChange }) {
  return (
    <div className="paletteSwatches">
      {colors.map((c, i) => (
        <label key={i} className="colorBtn inkSwatch paletteSwatch" style={{ background: c }} title={t(colorName(c))}>
          <input type="color" value={c} aria-label={t("{label} color {n}: {name}", { label, n: i + 1, name: t(colorName(c)) })}
            onChange={(e) => onChange(i, e.target.value.toLowerCase())} />
        </label>
      ))}
    </div>
  );
}

export function AppearanceSettings({ value, diagnostics }) {
  const palette = value.palette || DEFAULT_PALETTE;
  const setSlot = (role) => (i, hex) => value.setPalette({ ...palette, [role]: palette[role].map((c, k) => (k === i ? hex : c)) });
  const paletteChanged = ["wash", "ink"].some((role) => palette[role].some((c, i) => c !== DEFAULT_PALETTE[role][i]));
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
          <button type="button" className="uiBtn sm" disabled={!paletteChanged}
            title={t("Put every swatch back to its default colour")} onClick={() => value.setPalette(DEFAULT_PALETTE)}>
            {t("Reset colors")}
          </button>
        )}>
        <Row icon={HighlightIcon} label={t("Highlights")} hint={t("Text highlights and highlighter strokes. Click a swatch to change it.")}>
          <PaletteSwatches label={t("Highlights")} colors={palette.wash} onChange={setSlot("wash")} />
        </Row>
        <Row icon={PenIcon} label={t("Pens")} hint={t("Pens and text boxes. Existing highlights and strokes keep their colours.")}>
          <PaletteSwatches label={t("Pens")} colors={palette.ink} onChange={setSlot("ink")} />
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
