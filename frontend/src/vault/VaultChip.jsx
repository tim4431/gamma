// What a page shows of its standing with this computer
// (docs/research/vaults.md "The words", "Reading a PDF where it is"), read by
// vaultState.js: the chip among the page header's buttons, `Synced: …` for a
// page a synced vault keeps, `On this computer` for a PDF opened where it
// lies, with its card (where the file is, Show in folder, Add to library…,
// Edit the file); and the banner over the page, in the missing-page notice's
// slot, for a note the vault could not write and for an opened PDF whose
// file is gone (Locate…, Remove). The actions are App's: it posts the
// shell's messages and opens the dialogs.
import React from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { AlertCircleIcon, CodeIcon, FolderOpenIcon, FolderPlusIcon, HardDriveIcon, LocateIcon, TrashIcon } from "../shared/ui/Icons";
import { MenuItem, MenuScope } from "../shared/ui/Menus";
import { Popover } from "../shared/ui/Surfaces";
import { addsToLibrary, directionText, editsFile, notesLocked, vaultChip } from "./vaultState.js";
import "./vault.css";

// The chip and its card. `open` / `onOpenChange`: App's popover state, so
// the outside press and Escape close it as they close the header's others.
// `onReveal()` shows the file in the system's file manager (the desktop
// app's), `onAdd()` opens Add to library… (`canAdd`: the account may file
// pages; another server's library is a choice there), `onEdit()` the
// file's editor.
export function VaultChip({ state, open, onOpenChange, onReveal, onAdd, onEdit, canAdd }) {
  const chip = vaultChip(state);
  if (!chip) return null;
  const { vault, reader } = state;
  const path = vault ? vault.path : reader.path;
  return (
    <span data-popover="vault" className="popoverAnchor">
      <button type="button" className={`uiBtn sm ghost vaultChip${chip.missing || chip.problem ? " warn" : ""}`}
        title={chip.title} aria-haspopup="menu" aria-expanded={open} onClick={() => onOpenChange(!open)}>
        {chip.missing || chip.problem ? <AlertCircleIcon size={14} /> : <HardDriveIcon size={14} />}
        <span className="vaultChipText">{chip.label}</span>
      </button>
      {open ? (
        <Popover as={MenuScope} className="addPopover vaultPopover" aria-label={chip.label}>
          <div className="popoverTitle">{vault ? t("Synced vault: {name}", { name: vault.name }) : t("On this computer")}</div>
          {path ? <div className="popoverHint vaultPath" title={path}>{path}</div> : null}
          {vault ? <div className="popoverHint">{directionText(vault)}</div> : null}
          {vault?.paused ? <div className="popoverHint">{t("The vault is paused: only a sync asked for in the sync panel runs.")}</div> : null}
          {vault?.conflicts ? (
            <div className="popoverHint">{tn("{n} conflict on this page: the file's text won, and Gamma's is on its block's chip.",
              "{n} conflicts on this page: the file's text won, and Gamma's is on each block's chip.", vault.conflicts)}</div>
          ) : null}
          {notesLocked(state) ? (
            <div className="popoverHint">{t("This vault does not pull: the notes are written in the file, and only highlights are edited here.")}</div>
          ) : null}
          {reader && !reader.missing ? (
            <div className="popoverHint">{t("Read where it is, not stored in the library. Add it to the library to keep a copy.")}</div>
          ) : null}
          {reader?.missing ? <div className="popoverHint">{t("The file is not there any more.")}</div> : null}
          {reader?.relocation_error ? (
            <div className="popoverHint">{t("Its last new version placed no highlights: {reason}", { reason: reader.relocation_error })}</div>
          ) : null}
          <div className="vaultActions">
            {path && !reader?.missing ? (
              <MenuItem icon={FolderOpenIcon} onClick={() => { onOpenChange(false); onReveal(); }}
                title={t("Show the file in this computer's file manager")}>{t("Show in folder")}</MenuItem>
            ) : null}
            {addsToLibrary(state) && canAdd ? (
              <MenuItem icon={FolderPlusIcon} onClick={() => { onOpenChange(false); onAdd(); }}
                title={t("Store the PDF in the library and file the page in a folder: its highlights, notes and chat come with it")}>
                {t("Add to library…")}
              </MenuItem>
            ) : null}
            {editsFile(state) ? (
              <MenuItem icon={CodeIcon} onClick={() => { onOpenChange(false); onEdit(); }}
                title={t("Edit the Markdown file itself; the page takes what you save")}>{t("Edit the file")}</MenuItem>
            ) : null}
          </div>
        </Popover>
      ) : null}
    </span>
  );
}

// The banner (vaultState.js vaultBanner): `onLocate()` asks the desktop app
// for the file's new place, `onRemove()` deletes the page, `onDismiss()`
// hides the banner until the page's standing changes.
export function VaultBanner({ banner, onLocate, onRemove, onDismiss }) {
  if (!banner) return null;
  return (
    <div className="missingPageNotice vaultNotice" role="status" data-vault-banner={banner.kind}>
      <AlertCircleIcon size={16} aria-hidden="true" />
      <span className="missingPageText">
        {banner.kind === "problem" ? (
          <><b>{t("Not written to disk.")}</b> {banner.reason}</>
        ) : (
          <><b>{t("This page's file is missing.")}</b> {banner.path
            ? t("It was read from {path}. Locate it where it is now, or remove the page.", { path: banner.path })
            : t("Locate it where it is now, or remove the page.")}</>
        )}
      </span>
      {banner.kind === "missing" && onLocate ? (
        <button type="button" className="uiBtn sm primary" onClick={onLocate}><LocateIcon size={14} /> {t("Locate…")}</button>
      ) : null}
      {banner.kind === "missing" && onRemove ? (
        <button type="button" className="uiBtn sm danger" onClick={onRemove}><TrashIcon size={14} /> {t("Remove")}</button>
      ) : null}
      <button type="button" className="uiBtn sm" onClick={onDismiss}>{t("Dismiss")}</button>
    </div>
  );
}
