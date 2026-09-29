import React from "react";
import { t } from "../shared/i18n/i18n.js";
import { MenuSelect } from "../shared/ui/Menus";
import { BookIcon, CloudDownloadIcon, EyeIcon, FileTextIcon, FolderIcon, GlobeIcon,
  ListIcon, OutlineIcon, PenIcon, PencilIcon, SearchIcon, ShieldIcon } from "../shared/ui/Icons";
import { permissionFor, permissionPreset, presetPermissions, toolsForKind } from "../chat/chatSettings.js";

// Shared labels for settings, the chat shortcut and inline approval requests.
const GROUPS = [
  ["library", t("Read your library")],
  ["web", t("Web research")],
  ["edit", t("Make changes")],
];
export const TOOL_PERMISSION_ROWS = [
  ["list", ListIcon, t("List pages"), t("Browse page titles, folders and metadata"), "library"],
  ["read", BookIcon, t("Read pages"), t("Read PDF text, highlights and notes"), "library"],
  ["block_read", OutlineIcon, t("Read note blocks"), t("Read individual notes and their outline"), "library"],
  ["view", EyeIcon, t("View PDF pages"), t("Look at figures, tables and scanned pages"), "library"],
  ["search", SearchIcon, t("Search library"), t("Find text in your notes and PDFs"), "library"],
  ["web_search", GlobeIcon, t("Search papers online"), t("Find papers through Crossref, arXiv and configured web search"), "web"],
  ["web_read", CloudDownloadIcon, t("Fetch documents"), t("Read a DOI or URL without saving it to your library"), "web"],
  ["publisher_cookies", ShieldIcon, t("Use journal sign-ins"), t("Use connected publisher cookies when fetching documents"), "web"],
  ["rename", PenIcon, t("Rename pages"), t("Change page titles on request"), "edit"],
  ["move", FolderIcon, t("Move pages"), t("Organize pages into folders"), "edit"],
  ["block_edit", PencilIcon, t("Edit note blocks"), t("Create, edit and move notes on request"), "edit"],
];

// [kind, icon, label, hint, agent scope, short column label]
export const CHAT_KIND_ROWS = [
  ["folder", FolderIcon, t("Folder chat"), t("Home and folder views"), "folder", t("Folder")],
  ["pdf", FileTextIcon, t("PDF chat"), t("Pages with a PDF"), "page", t("PDF")],
  ["notes", OutlineIcon, t("Notes chat"), t("Note pages"), "page", t("Notes")],
];

const PERMISSION_OPTIONS = [["allow", t("Always allow")], ["ask", t("Ask")], ["deny", t("Deny")]];

function changePermission(setPerms, kind, key, value) {
  setPerms((previous) => ({ ...previous, [kind]: { ...previous?.[kind], [key]: value } }));
}

function PermissionPreset({ kind, perms, setPerms, disabled }) {
  const label = CHAT_KIND_ROWS.find((row) => row[0] === kind)?.[2] || t("Chat");
  const value = permissionPreset(kind, perms?.[kind]);
  return <MenuSelect value={value} label={t("{kind} permissions", { kind: label })}
    display={value === "custom" ? t("Custom") : undefined}
    options={[["ask", t("Ask before changes")], ["read", t("Read only")],
      ["edit", t("Always allow")], ["library", t("Read library")]]}
    onChange={(preset) => {
      if (!disabled) setPerms((previous) => ({ ...previous,
        [kind]: { ...previous?.[kind], ...presetPermissions(kind, preset) } }));
    }} />;
}

const needsFetch = (key, permissions) => key === "publisher_cookies" && permissionFor("web_read", permissions) === "deny";

function PermissionChoice({ kind, tool, label, hint, perms, setPerms, disabled }) {
  const unavailable = disabled || needsFetch(tool, perms?.[kind]);
  return <fieldset className="agentToolChoice" disabled={unavailable}
    title={needsFetch(tool, perms?.[kind]) ? t("Enable Fetch documents first") : hint}>
    <MenuSelect value={permissionFor(tool, perms?.[kind])} label={label}
      options={PERMISSION_OPTIONS} onChange={(value) => {
        if (!unavailable) changePermission(setPerms, kind, tool, value);
      }} />
  </fieldset>;
}

export function AgentToolMatrix({ perms, setPerms, disabled }) {
  return <fieldset className="agentToolMatrix" disabled={disabled}>
    <legend className="srOnly">{t("Tool permissions by chat type")}</legend>
    <table className="agentToolTable">
      <caption className="srOnly">{t("Choose what each kind of chat can do")}</caption>
      <colgroup><col />{CHAT_KIND_ROWS.map(([kind]) => <col key={kind} className="agentToolColumn" />)}</colgroup>
      <thead><tr>
        <th scope="col" className="agentToolIntro">
          <span className="settingLabel">{t("Choose access for each chat")}</span>
          <span className="settingDesc">{t("Reads are allowed by default. Changes ask for your approval.")}</span>
        </th>
        {CHAT_KIND_ROWS.map(([kind, Icon, label, hint, , short]) => <th key={kind} scope="col" data-setting={label}>
          <span className="agentToolKind" title={`${label} — ${hint}`}><Icon size={15} />{short}</span>
          <PermissionPreset kind={kind} perms={perms} setPerms={setPerms} disabled={disabled} />
        </th>)}
      </tr></thead>
      {GROUPS.map(([group, groupLabel]) => <tbody key={group}>
        <tr className="agentToolGroup"><th colSpan={4} scope="colgroup">{groupLabel}</th></tr>
        {TOOL_PERMISSION_ROWS.filter((row) => row[4] === group).map(([key, Icon, label, hint]) => <tr key={key}>
          <th scope="row" className="agentToolDescription" data-setting={label}>
            <Icon size={16} />
            <span><span className="settingLabel">{label}</span><span className="settingDesc">{hint}</span></span>
          </th>
          {CHAT_KIND_ROWS.map(([kind, , kindLabel, , , short]) => <td key={kind}>
            <span className="agentToolMobileLabel" aria-hidden="true">{short}</span>
            {toolsForKind(kind).includes(key) ? <PermissionChoice kind={kind} tool={key}
              label={t("{tool} — {kind}", { tool: label, kind: kindLabel })} hint={hint}
              perms={perms} setPerms={setPerms} disabled={disabled} />
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
    <p className="settingDesc agentToolPickerHint">{t("Ask pauses the action until you approve it.")}</p>
    {GROUPS.map(([group, groupLabel]) => <div key={group}>
      <div className="popoverSection">{groupLabel}</div>
      {TOOL_PERMISSION_ROWS.filter(([key, , , , rowGroup]) => rowGroup === group && available.includes(key))
        .map(([key, Icon, label, hint]) => <div key={key} className="chatToolPermRow" title={hint}>
          <Icon size={14} /><span className="chatToolPermLabel">{label}</span>
          <PermissionChoice kind={kind} tool={key} label={label} hint={hint}
            perms={perms} setPerms={setPerms} disabled={disabled} />
        </div>)}
    </div>)}
  </fieldset>;
}
