// What a page shows of its standing with this computer
// (docs/research/vaults.md "The words", "Reading a PDF where it is"), read by
// vaultState.js: the chip among the page header's buttons, one icon for how
// the page stands (vaultState's `kind`), with its card (where the file is,
// a work folder's agent on this computer and what it is at, Show in folder,
// Add to library…, Feedback…, Edit the file); on any other server, the
// computer that keeps the page in a synced vault of the account's, online
// or since when not, and the assistants there (computerChip, no action yet);
// and the banner over the page, in the missing-page notice's slot, for a
// note the vault could not write and for an opened file whose file is gone
// (Locate…, Remove). An opened Markdown file read as text (openedNote) is
// never added to the library: its card says how to make it a note instead.
// The actions are App's: it posts the shell's messages and opens the dialogs.
import React from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { AlertCircleIcon, BotIcon, CodeIcon, CopyIcon, FolderMinusIcon, FolderOpenIcon, FolderPlusIcon, HardDriveIcon, LocateIcon, MonitorIcon, PauseIcon, SquareCheckIcon,
  SyncIcon, TrashIcon, UploadIcon } from "../shared/ui/Icons";
import { MenuItem, MenuScope } from "../shared/ui/Menus";
import { Popover } from "../shared/ui/Surfaces";
import { addsToLibrary, agentLine, computerChip, directionText, editsFile, notesLocked, openedNote, opensAsPdf, vaultChip } from "./vaultState.js";
import "./vault.css";

// The chip's icon, by what the page's standing is (vaultState's `kind`):
// kept in step, pushed from a file of its own, read where it lies, paused,
// or something to see to.
const CHIP_ICONS = { sync: SyncIcon, push: UploadIcon, disk: HardDriveIcon, paused: PauseIcon, alert: AlertCircleIcon };

// The chip and its card. `open` / `onOpenChange`: App's popover state, so
// the outside press and Escape close it as they close the header's others.
// `onReveal()` shows the file in the system's file manager (the desktop
// app's), `onAdd()` opens Add to library… (`canAdd`: the account may file
// pages; another server's library is a choice there), `onEdit()` the
// file's editor. `file`, for a work folder's file ({library, pdf}, from the
// page's row: docs/dev/work_folders.md): Add to library… while it is not
// in the library, `onRemove()` (Remove from library) while it is, and
// `onCopy()` (Save a copy to library…) for its PDF while it is not (in the
// library, the copy would be its own page); `onChoose()`, when
// given, opens Choose files… of its work folder, and `onFeedback()`, when
// given, the file's feedback view (docs/dev/feedback.md). `computers`: the
// page's computers (usePageComputer.js), whose chip shows when the page
// has no standing with this computer to show.
export function VaultChip({ state, computers = [], open, onOpenChange, onReveal, onAdd, onEdit, canAdd, file = null, onRemove, onCopy, onChoose,
  onFeedback }) {
  const chip = vaultChip(state);
  if (!chip) return <ComputerChip computers={computers} open={open} onOpenChange={onOpenChange} />;
  const { vault, reader } = state;
  const agent = agentLine(state);
  const path = vault ? vault.path : reader.path;
  const Icon = CHIP_ICONS[chip.kind];
  return (
    <span data-popover="vault" className="popoverAnchor">
      <button type="button" className={`pageActionBtn vaultChip${chip.kind === "alert" ? " warn" : ""}`}
        title={`${chip.label}\n${chip.title}`} aria-label={chip.label}
        aria-haspopup="menu" aria-expanded={open} onClick={() => onOpenChange(!open)}>
        <Icon size={16} />
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
            <div className="popoverHint">{openedNote(reader)
              ? t("To edit it as a note, open it with Gamma again and pick Edit in Gamma; to keep a copy as a note, drop the file on the library.")
              : t("Read where it is, not stored in the library. Add it to the library to keep a copy.")}</div>
          ) : null}
          {file ? (
            <div className="popoverHint">{file.library
              ? t("A work folder's file, in the library too: its name on disk and its directory follow the file.")
              : t("A work folder's file, not in the library: its name and place follow the file.")}</div>
          ) : null}
          {agent ? (
            <div className="popoverHint vaultAgent" data-agent={vault.agent.name || ""}>
              <div>{agent.line}</div>
              {agent.problem ? <div className="vaultAgentProblem">{agent.problem}</div> : null}
              {agent.working ? <div>{agent.working}</div> : null}
            </div>
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
            {(file ? !file.library : addsToLibrary(state) && !openedNote(reader)) && canAdd ? (
              <MenuItem icon={FolderPlusIcon} onClick={() => { onOpenChange(false); onAdd(); }}
                title={file ? t("Add the file's page to the library, filed in a library folder too: it stays in its work folder")
                  : opensAsPdf(reader) ? t("Store the PDF in the library and file the page in a folder: its highlights, notes and chat come with it")
                  : t("Store the file in the library and file the page in a folder: its notes and chat come with it")}>
                {t("Add to library…")}
              </MenuItem>
            ) : null}
            {file?.library && canAdd ? (
              <MenuItem icon={FolderMinusIcon} onClick={() => { onOpenChange(false); onRemove(); }}
                title={t("Take the file's page out of the library: it stays in its work folder, with its notes")}>
                {t("Remove from library")}
              </MenuItem>
            ) : null}
            {file?.pdf && !file.library && canAdd ? (
              <MenuItem icon={CopyIcon} onClick={() => { onOpenChange(false); onCopy(); }}
                title={t("Store the PDF as it is now in the library, as a page of its own")}>
                {t("Save a copy to library…")}
              </MenuItem>
            ) : null}
            {onChoose ? (
              <MenuItem icon={SquareCheckIcon} onClick={() => { onOpenChange(false); onChoose(); }}
                title={t("Choose which files of its directory the work folder tracks")}>{t("Choose files…")}</MenuItem>
            ) : null}
            {onFeedback ? (
              <MenuItem icon={BotIcon} onClick={() => { onOpenChange(false); onFeedback(); }}
                title={t("The file's open comments, to read or send to an agent")}>{t("Feedback…")}</MenuItem>
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

// The chip of a page another computer keeps (vaultState.js computerChip):
// a dot on its icon for whether the computer is online, its card the
// computer's name, its state, the folder or file it keeps, the
// assistants there and the agent that runs the folder's Sends. Nothing to do from here yet: Show in folder needs a
// window on that computer.
function ComputerChip({ computers, open, onOpenChange }) {
  const chip = computerChip(computers);
  if (!chip) return null;
  const state = chip.online ? "online" : "offline";
  return (
    <span data-popover="vault" className="popoverAnchor">
      <button type="button" className="pageActionBtn vaultChip vaultComputerChip" data-computer={state}
        title={`${chip.label}\n${chip.title}`} aria-label={`${chip.label}: ${chip.state}`}
        aria-haspopup="menu" aria-expanded={open} onClick={() => onOpenChange(!open)}>
        <MonitorIcon size={16} />
        <span className={`vaultComputerDot ${state}`} aria-hidden="true" />
      </button>
      {open ? (
        <Popover as={MenuScope} className="addPopover vaultPopover" aria-label={chip.label}>
          <div className="popoverTitle">{chip.label}</div>
          <div className="popoverHint vaultComputerState" data-computer={state}>
            <span className={`vaultComputerDot ${state}`} aria-hidden="true" />{chip.state}
          </div>
          {chip.vault ? <div className="popoverHint">{chip.vault}</div> : null}
          {chip.paused ? <div className="popoverHint">{chip.paused}</div> : null}
          {chip.assistants ? <div className="popoverHint vaultAgent">{chip.assistants}</div> : null}
          {chip.agent ? <div className="popoverHint vaultAgent">{chip.agent}</div> : null}
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
    <div className="missingPageNotice" role="status" data-vault-banner={banner.kind}>
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
