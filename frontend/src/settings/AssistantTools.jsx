import React from "react";
import { T, t } from "../shared/i18n/i18n.js";
import { MenuSelect } from "../shared/ui/Menus";
import { Chevron } from "../shared/ui/MotionWidgets";
import { IconChoices } from "./SettingsKit";
import { BanIcon, BookIcon, CheckIcon, CloudDownloadIcon, EyeIcon, FilePlusIcon, FileTextIcon, FolderIcon,
  FolderMinusIcon, FoldersIcon, GlobeIcon, HelpCircleIcon, HistoryIcon, ListIcon, LocateIcon, MessageSquareIcon, OutlineIcon,
  PagesIcon, PenIcon, PencilIcon, SearchIcon, ShapesIcon, ShieldIcon, SlashIcon, TrashIcon } from "../shared/ui/Icons";
import { permGroup, permState, permStates, permissionPreset, presetPermissions, toolsForKind } from "../chat/chatSettings.js";

// One catalog for the comparison table, the current chat's quick settings
// and the approval card (chat/ApprovalCard.jsx names a permission by it).
const GROUPS = [
  ["library", t("Read your library")],
  ["web", t("Web research")],
  ["edit", t("Make changes")],
];
const PERMISSIONS = [
  ["list", ListIcon, t("List pages"), t("Browse page titles, folders, metadata and Recently deleted")],
  ["read", BookIcon, t("Read pages"), t("Read PDF text, highlights, notes and citation records, and export them on request")],
  ["block_read", OutlineIcon, t("Read note blocks"), t("Read individual notes and their outline")],
  ["view", EyeIcon, t("View pages and handwriting"), t("Look at figures, tables, scanned pages and your handwriting")],
  ["search", SearchIcon, t("Search library"), t("Find text in your notes and PDFs")],
  ["show", LocateIcon, t("Show pages"), t("Open a page for you at a PDF page, a passage or a note")],
  ["skills", SlashIcon, t("Use skills on its own"), t("Load one of your skills when a request fits it")],
  ["web_search", GlobeIcon, t("Search online"), t("Search the web and Crossref, arXiv and OpenAlex, and follow papers' citations")],
  ["web_read", CloudDownloadIcon, t("Fetch documents"), t("Read a web page, DOI or URL without saving it to your library")],
  ["publisher_cookies", ShieldIcon, t("Use journal sign-ins"), t("Use connected publisher cookies when fetching documents")],
  ["save", FilePlusIcon, t("Save papers"), t("Add papers found online to your library on request")],
  ["create", PagesIcon, t("Create pages"), t("Make new pages of notes on request. You can undo it from the reply.")],
  ["rename", PenIcon, t("Rename pages"), t("Change page titles on request")],
  ["move", FolderIcon, t("Move pages"), t("Organize pages into folders")],
  ["restore", HistoryIcon, t("Restore deleted pages"), t("Bring pages back from Recently deleted on request")],
  ["delete", TrashIcon, t("Delete pages"), t("Move pages to Recently deleted on request. You can undo it from the reply.")],
  ["organize", FoldersIcon, t("Organize folders and labels"), t("Rename folders, and make, rename and apply labels, on request")],
  ["organize_delete", FolderMinusIcon, t("Delete folders and labels"), t("Delete a folder or a label on request, never its pages. You can undo it from the reply.")],
  ["block_edit", PencilIcon, t("Edit note blocks"), t("Create, edit and move notes, and transcribe handwriting, on request")],
  ["comment", MessageSquareIcon, t("Comments and highlights"), t("Comment on passages, reply in and resolve comment threads, and highlight passages and areas, under its own name")],
  ["draw", ShapesIcon, t("Draw on pages"), t("Draw shapes, add text boxes, pictures and pages of paper on a page, on request. You can undo it from the reply.")],
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

function PermissionState({ kind, permKey, label, hint, perms, setPerms, disabled, ghost = false }) {
  const state = permState(perms?.[kind], permKey);
  const blocked = needsFetch(permKey, perms?.[kind]);
  return <span className={`agentPermState ${state}`} title={blocked ? t("Enable Fetch documents first") : hint}>
    <MenuSelect value={state} label={label} disabled={disabled || blocked} ghost={ghost}
      options={STATE_OPTIONS.filter(([value]) => permStates(permKey).includes(value))}
      onChange={(next) => { if (!disabled) changePermission(setPerms, kind, permKey, next); }} />
  </span>;
}

// The presets (chatSettings.js presetPermissions), least first: what each
// lets the assistant do, as the chat popover's tiles say it.
const PRESETS = [
  { value: "library", label: T("Read library"), hint: T("No web, no changes"), Icon: BookIcon },
  { value: "read", label: T("Read & search"), hint: T("Adds the web, no changes"), Icon: GlobeIcon },
  { value: "ask", label: T("Ask before changes"), hint: T("You approve each change"), Icon: HelpCircleIcon },
  { value: "all", label: T("Allow all"), hint: T("Changes without asking"), Icon: CheckIcon },
];

const applyPreset = (setPerms, kind, preset) => setPerms((previous) => ({ ...previous,
  [kind]: { ...previous?.[kind], ...presetPermissions(kind, preset) } }));

function PermissionPreset({ kind, perms, setPerms, disabled }) {
  const value = permissionPreset(kind, perms?.[kind]);
  return <MenuSelect value={value} label={t("{kind} permissions", { kind: chatKindName(kind) })}
    display={value === "custom" ? t("Custom") : undefined} disabled={disabled}
    options={PRESETS.map((p) => [p.value, p.label])}
    onChange={(preset) => { if (!disabled) applyPreset(setPerms, kind, preset); }} />;
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
          <span className="agentToolKind" title={`${label} — ${hint}`}><Icon size={14} />{short}</span>
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
    <p className="settingDesc agentToolNote">{t("Manage journal sign-ins in the Gamma Connector. Turning access off here keeps your connections saved. Fetched documents are temporary; to keep one, save it (Save papers, or Save to library under the reply).")}</p>
  </fieldset>;
}

// The narrow chat popover shows the same permissions: the presets as
// tiles, two to a row (none pressed while the tools are set one by one),
// then Each tool, a disclosure that says how many tools stand in each state
// and opens onto grouped, named rows; it starts open on a custom set.
export function AgentToolPicker({ kind, perms, setPerms, disabled }) {
  const available = toolsForKind(kind);
  const preset = permissionPreset(kind, perms?.[kind]);
  const [each, setEach] = React.useState(preset === "custom");
  const counts = STATE_OPTIONS.map(([state, label]) => [available.filter((key) => permState(perms?.[kind], key) === state).length, label])
    .filter(([n]) => n).map(([n, label]) => `${n} ${label}`).join(" · ");
  return <fieldset className="agentToolPicker" disabled={disabled}>
    <legend className="srOnly">{t("Tool permissions")}</legend>
    <IconChoices label={t("{kind} permissions", { kind: chatKindName(kind) })} value={preset} columns={2}
      onChange={(next) => applyPreset(setPerms, kind, next)} options={PRESETS} />
    <button type="button" className="uiBtn sm ghost agentToolEach" aria-expanded={each} onClick={() => setEach((v) => !v)}>
      <Chevron open={each} dir="right" />
      <span className="agentToolEachName">{preset === "custom" ? t("Custom: each tool") : t("Each tool")}</span>
      <span className="agentToolEachCount">{counts}</span>
    </button>
    {each ? GROUPS.map(([group, groupLabel]) => <div key={group} className="agentToolGroupRows">
      <div className="popoverSection">{groupLabel}</div>
      {PERMISSIONS.filter(([key]) => permGroup(key) === group && available.includes(key))
        .map(([key, Icon, label, hint]) => <div key={key} className="chatToolPermRow select">
          <Icon size={14} /><span className="chatToolPermLabel">{label}</span>
          <PermissionState kind={kind} permKey={key} label={label} hint={hint}
            perms={perms} setPerms={setPerms} disabled={disabled} ghost />
        </div>)}
    </div>) : null}
  </fieldset>;
}
