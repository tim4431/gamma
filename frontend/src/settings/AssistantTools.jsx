import React from "react";
import { t } from "../shared/i18n/i18n.js";
import { MenuSelect } from "../shared/ui/Menus";
import { BanIcon, BookIcon, CheckIcon, CloudDownloadIcon, EyeIcon, FileTextIcon, FolderIcon, GlobeIcon,
  HelpCircleIcon, ListIcon, OutlineIcon, PenIcon, PencilIcon, SearchIcon, ShieldIcon } from "../shared/ui/Icons";
import { permGroup, permState, permStates, permissionPreset, presetPermissions, toolsForKind } from "../chat/chatSettings.js";

// One catalog for the comparison table, the current chat's quick settings
// and the approval card (chat/ApprovalCard.jsx names a permission by it).
const GROUPS = [
  ["library", t("Read your library")],
  ["web", t("Web research")],
  ["edit", t("Make changes")],
];
const PERMISSIONS = [
  ["list", ListIcon, t("List pages"), t("Browse page titles, folders and metadata")],
  ["read", BookIcon, t("Read pages"), t("Read PDF text, highlights and notes")],
  ["block_read", OutlineIcon, t("Read note blocks"), t("Read individual notes and their outline")],
  ["view", EyeIcon, t("View PDF pages"), t("Look at figures, tables and scanned pages")],
  ["search", SearchIcon, t("Search library"), t("Find text in your notes and PDFs")],
  ["web_search", GlobeIcon, t("Search papers online"), t("Find papers on Crossref, arXiv and OpenAlex, follow their citations, and search the web")],
  ["web_read", CloudDownloadIcon, t("Fetch documents"), t("Read a DOI or URL without saving it to your library")],
  ["publisher_cookies", ShieldIcon, t("Use journal sign-ins"), t("Use connected publisher cookies when fetching documents")],
  ["rename", PenIcon, t("Rename pages"), t("Change page titles on request")],
  ["move", FolderIcon, t("Move pages"), t("Organize pages into folders")],
  ["block_edit", PencilIcon, t("Edit note blocks"), t("Create, edit and move notes on request")],
];
export const permissionLabel = (key) => PERMISSIONS.find((row) => row[0] === key)?.[2] || key;
export const permissionIcon = (key) => PERMISSIONS.find((row) => row[0] === key)?.[1] || ShieldIcon;

// [kind, icon, label, hint, agent scope, short column label]
const CHAT_KIND_ROWS = [
  ["folder", FolderIcon, t("Folder chat"), t("Home and folder views"), "folder", t("Folder")],
  ["pdf", FileTextIcon, t("PDF chat"), t("Pages with a PDF"), "page", t("PDF")],
  ["notes", OutlineIcon, t("Notes chat"), t("Note pages"), "page", t("Notes")],
];
export const chatKindName = (kind) => CHAT_KIND_ROWS.find((row) => row[0] === kind)?.[2] || t("Chat");

// What a permission's state menu offers: Allow runs the tools without
// asking, Ask shows an approval card before each call, Off never offers them.
const STATE_OPTIONS = [
  ["allow", t("Allow"), CheckIcon],
  ["ask", t("Ask"), HelpCircleIcon],
  ["off", t("Off"), BanIcon],
];

export function changePermission(setPerms, kind, key, state) {
  setPerms((previous) => ({ ...previous, [kind]: { ...previous?.[kind], [key]: state } }));
}

// Journal sign-ins are part of fetching: nothing to choose while fetching is off.
const needsFetch = (key, permissions) => key === "publisher_cookies" && permState(permissions, "web_read") === "off";

function PermissionState({ kind, permKey, label, hint, perms, setPerms, disabled }) {
  const state = permState(perms?.[kind], permKey);
  const blocked = needsFetch(permKey, perms?.[kind]);
  return <span className={`agentPermState ${state}`} title={blocked ? t("Enable Fetch documents first") : hint}>
    <MenuSelect value={state} label={label} disabled={disabled || blocked}
      options={STATE_OPTIONS.filter(([value]) => permStates(permKey).includes(value))}
      onChange={(next) => { if (!disabled) changePermission(setPerms, kind, permKey, next); }} />
  </span>;
}

function PermissionPreset({ kind, perms, setPerms, disabled }) {
  const value = permissionPreset(kind, perms?.[kind]);
  return <MenuSelect value={value} label={t("{kind} permissions", { kind: chatKindName(kind) })}
    display={value === "custom" ? t("Custom") : undefined} disabled={disabled}
    options={[["library", t("Read library")], ["read", t("Read & search")],
      ["ask", t("Ask before changes")], ["all", t("Allow all")]]}
    onChange={(preset) => {
      if (!disabled) setPerms((previous) => ({ ...previous,
        [kind]: { ...previous?.[kind], ...presetPermissions(kind, preset) } }));
    }} />;
}

export function AgentToolMatrix({ perms, setPerms, disabled }) {
  const id = React.useId();
  return <fieldset className="agentToolMatrix" disabled={disabled}>
    <legend className="srOnly">{t("Tool permissions by chat type")}</legend>
    <table className="agentToolTable">
      <caption className="srOnly">{t("Choose what each kind of chat can do")}</caption>
      <colgroup><col />{CHAT_KIND_ROWS.map(([kind]) => <col key={kind} className="agentToolColumn" />)}</colgroup>
      <thead><tr>
        <th scope="col" className="agentToolIntro">
          <span className="settingLabel">{t("Choose access for each chat")}</span>
          <span className="settingDesc">{t("Allow runs a tool without asking. Ask shows you each call first, with what it would change. Off never offers it.")}</span>
        </th>
        {CHAT_KIND_ROWS.map(([kind, Icon, label, hint, , short]) => <th key={kind} scope="col" data-setting={label}>
          <span className="agentToolKind" title={`${label} — ${hint}`}><Icon size={15} />{short}</span>
          <PermissionPreset kind={kind} perms={perms} setPerms={setPerms} disabled={disabled} />
        </th>)}
      </tr></thead>
      {GROUPS.map(([group, groupLabel]) => <tbody key={group}>
        <tr className="agentToolGroup"><th colSpan={4} scope="colgroup">{groupLabel}</th></tr>
        {PERMISSIONS.filter(([key]) => permGroup(key) === group).map(([key, Icon, label, hint]) => <tr key={key}>
          <th scope="row" className="agentToolDescription" data-setting={label}>
            <Icon size={16} />
            <span><span className="settingLabel">{label}</span><span className="settingDesc" id={`${id}-${key}`}>{hint}</span></span>
          </th>
          {CHAT_KIND_ROWS.map(([kind, , kindLabel, , , short]) => <td key={kind}>
            <span className="agentToolMobileLabel" aria-hidden="true">{short}</span>
            {toolsForKind(kind).includes(key)
              ? <PermissionState kind={kind} permKey={key} label={t("{tool} — {kind}", { tool: label, kind: kindLabel })}
                hint={hint} perms={perms} setPerms={setPerms} disabled={disabled} />
              : <span className="agentToolUnavailable" aria-label={t("Not available for this chat type")}>—</span>}
          </td>)}
        </tr>)}
      </tbody>)}
    </table>
    <p className="settingDesc agentToolNote">{t("Manage journal sign-ins in the Gamma Connector. Turning access off here keeps your connections saved. Fetched documents are temporary; save them with the Connector to keep a copy.")}</p>
  </fieldset>;
}

// The narrow chat popover shows the same permissions as grouped, named rows.
export function AgentToolPicker({ kind, perms, setPerms, disabled }) {
  const available = toolsForKind(kind);
  return <fieldset className="agentToolPicker" disabled={disabled}>
    <legend className="srOnly">{t("Tool permissions")}</legend>
    <PermissionPreset kind={kind} perms={perms} setPerms={setPerms} disabled={disabled} />
    {GROUPS.map(([group, groupLabel]) => <div key={group}>
      <div className="popoverSection">{groupLabel}</div>
      {PERMISSIONS.filter(([key]) => permGroup(key) === group && available.includes(key))
        .map(([key, Icon, label, hint]) => <div key={key} className="chatToolPermRow select">
          <Icon size={14} /><span className="chatToolPermLabel">{label}</span>
          <PermissionState kind={kind} permKey={key} label={label} hint={hint}
            perms={perms} setPerms={setPerms} disabled={disabled} />
        </div>)}
    </div>)}
  </fieldset>;
}
