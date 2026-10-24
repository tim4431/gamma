import React from "react";
import { FileGlyph, FileTextIcon, MarkdownIcon } from "../ui/Icons";
import { FADE_IN } from "../ui/MotionWidgets";
import obsidian from "./brands/obsidian.svg";
import logseq from "./brands/logseq.svg";
import zotero from "./brands/zotero.png";
import gamma from "./brands/gamma.svg";

const APP_ICONS = { obsidian, logseq, gamma };

// Familiar app marks and shared file icons; the card owns hover and selection.
// Zotero's mark is a picture fetched as the dialog opens: it fades in.
export function FormatIllustration({ format }) {
  const appIcon = APP_ICONS[format];
  let icon;
  if (format === "zotero") icon = <img className="formatAppImage" src={zotero} alt="" width="48" height="48" {...FADE_IN} />;
  else if (appIcon) icon = <span className="formatAppIcon" style={{ "--app-icon": `url("${appIcon}")` }} />;
  else if (format === "pdf" || format === "annots") icon = <FileGlyph isPdf />;
  else if (format === "bibtex") icon = <FileGlyph label="BIB" />;
  else if (format === "notability") icon = <FileGlyph label="NOTE" />;
  else if (format === "markdown") icon = <MarkdownIcon size={48} strokeWidth={1.5} />;
  else icon = <FileTextIcon size={48} strokeWidth={1.5} />;
  return <span className="setPicturePreview formatIllustration" aria-hidden="true">{icon}</span>;
}
