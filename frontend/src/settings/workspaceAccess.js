// What a connection may read, comment in and edit (docs/dev/mcp.md "The
// Comment level", "Editing notes"), as WorkspacePicker holds it: {all, ids}
// read, {commentAll, comments} at the Comment level and {editAll, edits} at
// the Edit level, which comments too.

// The level of a workspace `id` in `access`: view, comment or edit.
export const levelOf = (access, id) => ((access.edits || []).includes(id) ? "edit"
  : (access.comments || []).includes(id) ? "comment" : "view");

// What a connection may read, comment in and edit, as the API takes it.
export const accessBody = (access) => ({
  all_workspaces: access.all, workspaces: access.all ? [] : access.ids,
  comment_all: !!(access.all && access.commentAll),
  comment_workspaces: access.all ? [] : (access.comments || []).filter((id) => access.ids.includes(id)),
  edit_all: !!(access.all && access.editAll),
  edit_workspaces: access.all ? [] : (access.edits || []).filter((id) => access.ids.includes(id)),
});

// A listed connection's access, as the picker holds it. A workspace of
// `workspaces` the account only views is at Comment where an Edit level
// was stored before the account lost its role: the server gives it no
// more, and a save sends what the picker shows.
export const accessOf = (item, workspaces = []) => {
  const viewed = new Set(workspaces.filter((w) => w.role === "viewer").map((w) => w.id));
  const stored = item.edit_workspaces || [];
  const lost = stored.filter((id) => viewed.has(id));
  return { all: item.all_workspaces, ids: item.workspaces,
    commentAll: item.comment_all, comments: [...(item.comment_workspaces || []), ...lost],
    editAll: item.edit_all, edits: stored.filter((id) => !viewed.has(id)) };
};

// Whether an access lets the connection edit anywhere.
export const edits = (access) => (access.all ? !!access.editAll : (access.edits || []).some((id) => access.ids.includes(id)));

// Whether an access lets the connection comment anywhere: at the Comment
// level, or the Edit level, which comments too.
export const comments = (access) => edits(access)
  || (access.all ? !!access.commentAll : (access.comments || []).some((id) => access.ids.includes(id)));
