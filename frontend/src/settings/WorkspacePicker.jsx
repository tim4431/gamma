import React from "react";
import { Segmented } from "./SettingsKit";
import { EyeIcon, PenIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

// What a connection may do in a workspace it reads, worded and drawn as the
// share popover's roles (sharing/SharePopover.jsx).
const ROLES = [
  ["view", t("View"), EyeIcon, t("Can read its pages, notes, highlights and PDF text")],
  ["edit", t("Edit"), PenIcon, t("Can also create, edit and move notes — never delete anything, or change pages, folders or settings")],
];

// The workspaces an assistant's connection may read (Settings → Integrations
// and the MCP sign-in): "All workspaces", which takes in those added later, or
// the ones ticked, kept in the account's order. `value` is {all, ids}; with
// `roles`, each ticked one (or "All workspaces") also says whether the
// connection may edit there, as {editAll, edits}, where the account itself
// edits: a workspace it only views stays View.
export function WorkspacePicker({ workspaces, value, onChange, disabled, label, roles = false }) {
  const edits = value.edits || [];
  const pick = (id, on) => {
    const ids = new Set(value.ids);
    if (on) ids.add(id); else ids.delete(id);
    onChange({ ...value, all: false, ids: workspaces.map((w) => w.id).filter((w) => ids.has(w)),
      edits: edits.filter((w) => w !== id || on) });
  };
  const setRole = (id, role) => onChange({ ...value,
    edits: role === "edit" ? [...edits.filter((w) => w !== id), id] : edits.filter((w) => w !== id) });
  return <div className="reportPageList integrationSpaces" role="group" aria-label={label}>
    <div className="docPickerItem integrationSpace">
      <label>
        <input type="checkbox" checked={value.all} disabled={disabled}
          onChange={(event) => onChange({ ...value, all: event.target.checked })} />
        <span className="attachName">{t("All workspaces, and those added later")}</span>
      </label>
      {roles && value.all ? <Segmented value={value.editAll ? "edit" : "view"} options={ROLES} disabled={disabled}
        onChange={(next) => onChange({ ...value, editAll: next === "edit" })} /> : null}
    </div>
    {workspaces.map((w) => {
      const on = !value.all && value.ids.includes(w.id);
      return <div key={w.id} className="docPickerItem integrationSpace">
        <label>
          <input type="checkbox" disabled={disabled || value.all} checked={value.all || on}
            onChange={(event) => pick(w.id, event.target.checked)} />
          <span className="attachName">{w.name}</span>
        </label>
        {on && roles ? (w.role === "viewer"
          ? <span className="uiTag" title={t("You can only view this workspace")}>{t("view only")}</span>
          : <Segmented value={edits.includes(w.id) ? "edit" : "view"} options={ROLES} disabled={disabled}
            onChange={(next) => setRole(w.id, next)} />) : null}
      </div>;
    })}
  </div>;
}

// What a connection may read, and edit, as the API takes it.
export const accessBody = (access) => ({
  all_workspaces: access.all, workspaces: access.all ? [] : access.ids,
  edit_all: !!(access.all && access.editAll),
  edit_workspaces: access.all ? [] : (access.edits || []).filter((id) => access.ids.includes(id)),
});

// A listed connection's access, as the picker holds it.
export const accessOf = (item) => ({ all: item.all_workspaces, ids: item.workspaces,
  editAll: item.edit_all, edits: item.edit_workspaces || [] });

// Whether an access lets the connection edit anywhere.
export const edits = (access) => (access.all ? !!access.editAll : (access.edits || []).some((id) => access.ids.includes(id)));
