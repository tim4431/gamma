import React from "react";
import { sampleLibraryPage } from "../shared/illustrations";
import { PageCard } from "../library/FileBrowser";
import { Toggle } from "./SettingsKit";
import { EyeIcon, FileGlyph, FolderIcon, LabelIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

export function LibraryDisplaySettings({ value }) {
  const folders = value.fileLabels === "both" || value.fileLabels === "folders";
  const labels = value.fileLabels === "both" || value.fileLabels === "labels";
  function setChips(showFolders, showLabels) {
    value.setFileLabels(showFolders ? (showLabels ? "both" : "folders") : (showLabels ? "labels" : "off"));
  }
  return <div className="libraryDisplay">
    <div className="libraryDisplayDemo">
      <figure className="libraryDisplayExample" aria-label={t("Library card preview")}>
        <figcaption>{t("Recently viewed")}</figcaption>
        <PageCard className="libraryDisplayCard" title={t("Patterns in nature")} kind="PDF" time="Just now"
          glyph={<FileGlyph />} snap={value.recentThumbs ? sampleLibraryPage : null}
          folders={["Reading list"]} labels={["Research"]} labelMode={value.fileLabels} />
      </figure>
      <div className="libraryDisplayControls" role="group" aria-label={t("Card elements")}>
        <div data-setting="Recents thumbnails">
          <Toggle icon={EyeIcon} label={t("Thumbnails")} hint={t("Preview the page you last read.")}
            checked={value.recentThumbs} onChange={value.setRecentThumbs} />
        </div>
        <div data-setting="File labels">
          <Toggle icon={FolderIcon} label={t("Folders")} hint={t("Show the folders a file belongs to.")}
            checked={folders} onChange={(on) => setChips(on, labels)} />
          <Toggle icon={LabelIcon} label={t("Labels")} hint={t("Show the labels on a file.")}
            checked={labels} onChange={(on) => setChips(folders, on)} />
        </div>
      </div>
    </div>
  </div>;
}
