// Pages copied into another workspace of this server, and a workspace
// duplicated into a new one (docs/dev/workspaces.md "Copies between
// workspaces", gamma/workspace_copy.py): which workspaces may take them,
// what a copy says before it starts and once it ended. The work is the
// `workspace-copy` job (tasks/taskKinds.js), which useAppJobs' copyPages
// starts; the server's preview is exportApi.js `copyPreview`. Pure:
// tests/workspaceCopy.test.mjs.
import { t, tn } from "../shared/i18n/i18n.js";

// The workspaces of the session's list (`workspaces`) that pages of `fromId`
// may be copied into: those the account may edit, never one a delete holds,
// nor `fromId` itself. In the list's order: personal ones first.
export const copyTargets = (workspaces, fromId) => (workspaces || []).filter((w) => w.id !== fromId
  && (w.role === "owner" || w.role === "editor") && !w.deleting);

// The job's body: the pages of workspace `ws` (null: every page; each brings
// the pages inside it), into the workspace `to` or, with `name`, into a new
// personal workspace of that name.
export const copyBody = ({ ws, pages = null, to = "", name = "" }) => ({
  ws, ...(pages ? { pages } : {}), ...(to ? { to } : { name }),
});

// What a copy says of the `n` pages that wait for a PDF their Zotero item
// holds and this workspace has not brought yet (the preview's zotero_pdfs):
// they come without it, and how to bring the PDFs first.
export function zoteroPdfsNote(n) {
  return [
    tn("{n} paper's PDF is still in Zotero, not here yet: its copy comes without it.",
      "{n} papers' PDFs are still in Zotero, not here yet: their copies come without them.", n),
    t("To bring them first, open the papers, or set PDFs from Zotero to All in the link's settings."),
  ].join(" ");
}

// The name a duplicate of the workspace `name` is offered.
export const duplicateName = (name) => t("{name} (copy)", { name: name || t("Workspace") });

// What the tab that started a copy says once it is done, from the job's
// `result` ({pages_added, pages_skipped, zotero_pdfs}) and `params` (the
// names: `name` the source's, `to_name` the target's, `new` a duplicate).
export function copySummary(result, params) {
  const added = result?.pages_added || 0;
  const skipped = result?.pages_skipped || 0;
  const waiting = result?.zotero_pdfs || 0;
  return [
    params?.new ? t("Duplicated “{name}” as “{copy}”.", { name: params.name || "", copy: params.to_name || "" })
      : tn("Copied {n} page to “{name}”.", "Copied {n} pages to “{name}”.", added, { name: params?.to_name || "" }),
    skipped ? tn("{n} was there already.", "{n} were there already.", skipped) : "",
    waiting ? tn("{n} came without its PDF, which is still in Zotero.", "{n} came without their PDFs, which are still in Zotero.", waiting) : "",
  ].filter(Boolean).join(" ");
}
