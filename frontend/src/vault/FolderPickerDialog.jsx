// Add to library…: the workspace and the folder an opened PDF is filed in as
// it joins a library (POST /api/folder-links/opened/{id}/add,
// linked_files.add_to_library: its bytes are stored, it leaves *Opened
// files*), shaped like the page picker of Move into page… (App's move
// dialog): a filter, the folders by path with the library's top first, Enter
// takes the first. With more than one workspace to offer, a choice of them
// over the folders: the one open here first, whose folders App hands in
// (`folders`), then each other this account edits (vaultState.js
// libraryWorkspaces), whose folders `readFolders(id)` reads when it is
// chosen. The switch under them makes the file a vault of one file, whose new
// versions keep coming (`keep_pushing`). `folders`: [{id, name}] ("" the top);
// `onPick(folder, keepPushing, workspace)`. `onCarry()`, when given (the
// desktop app, one page), asks the app for the library of another Gamma
// server it knows instead (its `carry-to-server`), which the page cannot
// list: the app asks which, or says it knows none. `opener` false: another
// account opened the file (vaultState.js openedHere), so neither another
// workspace nor keeping on pushing is offered, which are the opener's alone.
import React from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { FolderIcon, ServerIcon, UsersIcon } from "../shared/ui/Icons";
import { MenuItem, MenuSelect } from "../shared/ui/Menus";
import { backdropPress } from "../shared/ui/press.js";
import { stepList } from "../shared/ui/listKeys.js";
import { DialogOverlay } from "../shared/ui/Surfaces";
import { Row, Toggle } from "../settings/SettingsKit";
import "./vault.css";

export function FolderPickerDialog({ count = 1, workspaces: offered = [], workspace = "", opener = true, folders, readFolders, onPick,
  onCarry, onClose }) {
  const workspaces = opener ? offered : offered.filter((w) => w.id === workspace);
  const [query, setQuery] = React.useState("");
  const [keep, setKeep] = React.useState(false);
  const [ws, setWs] = React.useState(workspace);
  // another workspace's folders as read: {[id]: [{id, name}] | "failed"}
  const [read, setRead] = React.useState({});
  const here = ws === workspace;
  React.useEffect(() => {
    if (here || read[ws]) return undefined;
    let live = true;
    readFolders(ws).then((list) => { if (live) setRead((r) => ({ ...r, [ws]: list })); },
      () => { if (live) setRead((r) => ({ ...r, [ws]: "failed" })); });
    return () => { live = false; };
  }, [ws]); // eslint-disable-line react-hooks/exhaustive-deps
  const shown = here ? folders : Array.isArray(read[ws]) ? [{ id: "", name: t("All files") }, ...read[ws]] : [];
  const q = query.trim().toLowerCase();
  const matches = (q ? shown.filter((f) => f.name.toLowerCase().includes(q)) : shown).slice(0, 12);
  const name = workspaces.find((w) => w.id === ws)?.name || "";
  const pick = (folder) => onPick(folder, keep, ws);
  return (
    <DialogOverlay {...backdropPress(onClose)}>
      <div className="reportModal confirmModal folderPicker" role="dialog" aria-label={t("Add to library")}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
          stepList(e, e.currentTarget.querySelector("[data-find]"), [...e.currentTarget.querySelectorAll(".moveBlockList .ctxMenuItem")]);
        }}>
        <div className="reportModalTitle">{t("Add to library")}</div>
        <div className="reportModalHint confirmMessage">
          {tn("The PDF is stored in the library and filed in the folder you pick, with its highlights, notes and chat.",
            "The {n} PDFs are stored in the library and filed in the folder you pick, with their highlights, notes and chat.", count)}
          {here ? null : ` ${tn("It leaves this workspace for {workspace}, and opens there.",
            "They leave this workspace for {workspace}, and the first opens there.", count, { workspace: name })}`}
        </div>
        {workspaces.length > 1 ? (
          <Row icon={UsersIcon} label={t("Workspace")} className="folderPickerWorkspace">
            <MenuSelect label={t("Workspace")} value={ws} onChange={(id) => { setWs(id); setQuery(""); }}
              options={workspaces.map((w) => [w.id, w.name])} />
          </Row>
        ) : null}
        <div className="shareRow">
          <input
            autoFocus data-find
            placeholder={t("Filter folders…")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && matches.length && !e.nativeEvent.isComposing) pick(matches[0].id);
            }}
          />
        </div>
        <div className="moveBlockList">
          {matches.map((f) => (
            <MenuItem key={f.id || "top"} icon={FolderIcon} onClick={() => pick(f.id)}>{f.name}</MenuItem>
          ))}
          {read[ws] === "failed" ? <div className="confirmMessage">{t("Could not read that workspace's folders.")}</div>
            : !here && !read[ws] ? <div className="confirmMessage">{t("Reading the folders…")}</div>
            : !matches.length ? <div className="confirmMessage">{t("No matching folder.")}</div> : null}
        </div>
        {opener ? (
          <div className="folderPickerKeep">
            <Toggle label={t("Keep pushing from the file")}
              hint={t("Each new version of the file on this computer becomes the page's PDF")}
              checked={keep} onChange={setKeep} />
          </div>
        ) : (
          <div className="reportModalHint confirmMessage">
            {t("Another account opened this file: only that account can keep pushing from it or take the page to another workspace.")}
          </div>
        )}
        {onCarry ? (
          <div className="reportModalBtns folderPickerCarry">
            <span className="reportModalHint">{t("Or the library of another Gamma server added to this app; the app asks which.")}</span>
            <button type="button" className="uiBtn" onClick={onCarry}
              title={t("Store the PDF in the library of another Gamma server this app knows, with its highlights, notes and chat")}>
              <ServerIcon size={14} /> {t("Another server…")}
            </button>
          </div>
        ) : null}
      </div>
    </DialogOverlay>
  );
}
