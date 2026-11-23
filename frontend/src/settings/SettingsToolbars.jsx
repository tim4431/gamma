// Settings → Toolbars: what the bars offer and in which order — the top
// bar's buttons and the viewer strip's tools (BarLayoutSection, docs/dev/
// settings.md "Bars: order and modes"), whether the page header shows an
// Export button, then the text highlighters, the
// strip's and the selection bar's buttons (TextHighlighters). How things
// look stays in Appearance; the `topbar`, `viewer-tools` and `highlighters`
// entry points open this pane at their section (settingsNavigation.js).
import React from "react";
import { PaneHead, Section, Toggle } from "./SettingsKit";
import { TextHighlighters } from "./TextHighlighters";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { BarLayoutSection } from "./BarLayoutSection";
import {
  ExportIcon, FileTextIcon, HighlightIcon, LanguagesIcon, MicIcon, PanelTopIcon, PenIcon, SheetIcon, TextCursorIcon, ZoomInIcon,
} from "../shared/ui/Icons";
import { TOPBAR } from "../app/topbarItems.js";
import { TOPBAR_ICONS } from "../app/topbarIcons.js";
import { VIEWER_TOOLS } from "../app/viewerToolItems.js";
import { DEFAULT_PALETTE, normalizePalette } from "../shared/model/palette.js";
import { t } from "../shared/i18n/i18n.js";

// The two bars (BarLayoutSection): a glyph per item of app/topbarItems.js
// and app/viewerToolItems.js, the ones the bars draw; a new item there is a
// new row here, given its glyph below. The Inbox's row is only for an
// account that has one.
const VIEWER_TOOL_ICONS = {
  zoom: ZoomInIcon, ink: PenIcon, record: MicIcon, highlighter: HighlightIcon, translate: LanguagesIcon, paper: SheetIcon, notesView: FileTextIcon,
  selectMode: TextCursorIcon,
};

export function ToolbarSettings({ value }) {
  const palette = normalizePalette(value.palette) || DEFAULT_PALETTE;
  return (
    <div className="toolbarSettings">
      <PaneHead icon={PanelTopIcon} title={t("Toolbars")} />

      <BarLayoutSection title={t("Top bar")} scope="account" prefs={SECTION_PREFS.toolbars["Top bar"]}
        bar={TOPBAR} value={value.topbar} onChange={value.setTopbar} icons={TOPBAR_ICONS}
        filter={(item) => item.id !== "inbox" || value.hasInbox}
        resetTitle={t("Every button back on the bar in the default order, the sync pill on synced pages, and the page's own buttons in each pane while pages are side by side")} />

      {/* the buttons beside a page's title, of which only Export may be turned off */}
      <Section title={t("Page header")} scope="account" prefs={SECTION_PREFS.toolbars["Page header"]}>
        <Toggle icon={ExportIcon} label={t("Export button")}
          hint={t("A button beside the page's title that opens Export…")}
          title={t("Shows an Export button among the buttons beside a page's title. It opens the Export dialog for that page, as Export… in the View menu does, which stays there either way.")}
          checked={value.pageExportButton} onChange={value.setPageExportButton} />
      </Section>

      <BarLayoutSection title={t("Viewer tools")} scope="account" prefs={SECTION_PREFS.toolbars["Viewer tools"]}
        bar={VIEWER_TOOLS} value={value.viewerTools} onChange={value.setViewerTools} icons={VIEWER_TOOL_ICONS}
        resetTitle={t("Every tool back on the viewer's strip, in the default order")} />

      <TextHighlighters hl={value.textHighlighter} presets={value.highlightPresets} palette={palette} />
    </div>
  );
}
