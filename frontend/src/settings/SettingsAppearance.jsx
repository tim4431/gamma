import React from "react";
import { PaneHead, Section, Row, Toggle, Stepper, PictureChoices } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { MenuSelect } from "../shared/ui/Menus";
import { ContrastIcon, GlobeIcon, LayoutIcon, MaximizeIcon, MoonIcon } from "../shared/ui/Icons";
import { ThemePreview, PdfPreview } from "../shared/illustrations";
import { UI_SCALE } from "../app/prefs";
import { LANGUAGES, T, t } from "../shared/i18n/i18n.js";

const THEMES = [
  ["system", T("System"), T("Match your device"), "#eef0f3", "#ffffff", "#353b45", "#6089bb"],
  ["light", T("Light"), T("Bright & crisp"), "#f5f5f5", "#ffffff", "#1a1a1a", "#3a7bd5"],
  ["dark", T("Dark"), T("A quieter backdrop"), "#181818", "#292929", "#eeeeee", "#5b9bd5"],
  ["gamma-light", "Gamma Light", T("Warm gray & amber"), "#e7e5de", "#efeee9", "#292822", "#92620e"],
  ["gamma-dark", "Gamma Dark", T("Charcoal & soft gold"), "#1b1b1a", "#272725", "#f0ede6", "#e8b451"],
  ["sepia", T("Sepia"), T("Warm paper, deep ink"), "#e9e1cb", "#fdf6e3", "#073642", "#1b6fa3"],
  ["solarized", "Solarized Light", T("Warm paper, softer ink"), "#eee8d5", "#fdf6e3", "#657b83", "#268bd2"],
  ["gray", T("Gray"), T("Soft & neutral"), "#e3e3e3", "#f4f4f4", "#2d2d2d", "#3a7bd5"],
];
const DARK = THEMES.find((theme) => theme[0] === "dark");

export function AppearanceSettings({ value, diagnostics }) {
  return (
    <div className="appearanceSettings">
      <PaneHead icon={ContrastIcon} title={t("Appearance")} />

      <Section title={t("Theme")} scope="account" prefs={SECTION_PREFS.appearance["Theme"]}>
        <PictureChoices label={t("Theme")} value={value.theme} onChange={value.setTheme}
          options={THEMES.map((theme) => ({ value: theme[0], label: t(theme[1]), hint: t(theme[2]), preview: <ThemePreview theme={theme} dark={DARK} /> }))} />
      </Section>

      <Section title={t("Language")} scope="account" prefs={SECTION_PREFS.appearance["Language"]}>
        <Row icon={GlobeIcon} label={t("Language")} hint={t("Interface text only.")}
          title={t("Menus, settings and messages. Your notes, PDFs and the AI's replies are not affected; System follows the browser's language.")}>
          <MenuSelect label={t("Language")} value={value.language} onChange={value.setLanguage}
            options={LANGUAGES.map(([code, name]) => [code, code === "system" ? t("System") : name])} />
        </Row>
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
    </div>
  );
}
