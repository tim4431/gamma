import React from "react";
import { t } from "../shared/i18n/i18n.js";

// The workspaces an assistant's connection may read (Settings → Integrations
// and the MCP sign-in): "All workspaces", which takes in those added later, or
// the ones ticked, kept in the account's order. `value` is {all, ids}.
export function WorkspacePicker({ workspaces, value, onChange, disabled, label }) {
  const pick = (id, on) => {
    const ids = new Set(value.ids);
    if (on) ids.add(id); else ids.delete(id);
    onChange({ all: false, ids: workspaces.map((w) => w.id).filter((w) => ids.has(w)) });
  };
  return <div className="reportPageList integrationSpaces" role="group" aria-label={label}>
    <label className="docPickerItem docPickerNotes">
      <input type="checkbox" checked={value.all} disabled={disabled}
        onChange={(event) => onChange({ all: event.target.checked, ids: value.ids })} />
      <span className="attachName">{t("All workspaces, and those added later")}</span>
    </label>
    {workspaces.map((w) => <label key={w.id} className="docPickerItem">
      <input type="checkbox" disabled={disabled || value.all} checked={value.all || value.ids.includes(w.id)}
        onChange={(event) => pick(w.id, event.target.checked)} />
      <span className="attachName">{w.name}</span>
    </label>)}
  </div>;
}

// What a connection may read, as the API takes it.
export const accessBody = (access) => ({ all_workspaces: access.all, workspaces: access.all ? [] : access.ids });
