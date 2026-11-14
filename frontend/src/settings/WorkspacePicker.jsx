import React from "react";
import { Segmented } from "./SettingsKit";
import { EyeIcon, MessageSquareIcon, PenIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";
import { levelOf } from "./workspaceAccess.js";

// What a connection may do in a workspace it reads, worded and drawn as the
// share popover's roles (sharing/SharePopover.jsx): read, comment (the
// Comment level: comments, replies, resolves and highlights written under
// the connection's name, docs/dev/mcp.md "The Comment level"), or also edit
// notes, which comments too.
const LEVELS = [
  ["view", t("View"), EyeIcon, t("Can read its pages, notes, highlights and PDF text")],
  ["comment", t("Comment"), MessageSquareIcon, t("Can also comment, reply, resolve and highlight, under its own name — never change a note")],
  ["edit", t("Edit"), PenIcon, t("Can also create, edit and move notes — never delete anything, or change pages, folders or settings")],
];
// A workspace the account only views: it comments there, never edits (an
// Edit level stored from before the account lost its role is held as
// Comment, workspaceAccess.js accessOf).
const VIEWER_LEVELS = LEVELS.filter(([level]) => level !== "edit");

// The workspaces an assistant's connection may read (Settings → Integrations
// and the MCP sign-in): "All workspaces", which takes in those added later, or
// the ones ticked, kept in the account's order. `value` is {all, ids}; with
// `roles`, each ticked one (or "All workspaces") also says the connection's
// level there, View, Comment or Edit, as {commentAll, comments, editAll,
// edits}; a workspace the account only views offers View and Comment.
export function WorkspacePicker({ workspaces, value, onChange, disabled, label, roles = false }) {
  const edits = value.edits || [];
  const comments = value.comments || [];
  const pick = (id, on) => {
    const ids = new Set(value.ids);
    if (on) ids.add(id); else ids.delete(id);
    onChange({ ...value, all: false, ids: workspaces.map((w) => w.id).filter((w) => ids.has(w)),
      edits: edits.filter((w) => w !== id || on), comments: comments.filter((w) => w !== id || on) });
  };
  const setLevel = (id, level) => onChange({ ...value,
    edits: [...edits.filter((w) => w !== id), ...(level === "edit" ? [id] : [])],
    comments: [...comments.filter((w) => w !== id), ...(level === "comment" ? [id] : [])] });
  return <div className="reportPageList integrationSpaces" role="group" aria-label={label}>
    <div className="docPickerItem integrationSpace">
      <label>
        <input type="checkbox" checked={value.all} disabled={disabled}
          onChange={(event) => onChange({ ...value, all: event.target.checked })} />
        <span className="attachName">{t("All workspaces, and those added later")}</span>
      </label>
      {roles && value.all ? <Segmented value={value.editAll ? "edit" : value.commentAll ? "comment" : "view"}
        options={LEVELS} disabled={disabled}
        onChange={(next) => onChange({ ...value, editAll: next === "edit", commentAll: next === "comment" })} /> : null}
    </div>
    {workspaces.map((w) => {
      const on = !value.all && value.ids.includes(w.id);
      return <div key={w.id} className="docPickerItem integrationSpace">
        <label>
          <input type="checkbox" disabled={disabled || value.all} checked={value.all || on}
            onChange={(event) => pick(w.id, event.target.checked)} />
          <span className="attachName">{w.name}</span>
        </label>
        {on && roles ? <Segmented value={levelOf(value, w.id)}
          options={w.role === "viewer" ? VIEWER_LEVELS : LEVELS} disabled={disabled}
          onChange={(next) => setLevel(w.id, next)} /> : null}
      </div>;
    })}
  </div>;
}

