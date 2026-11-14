// App's commands over work folders' files and folders (docs/dev/
// work_folders.md "Add to library, Save a copy, Remove", "The op rule"):
// the refusals a menu, a drop or a key meets, with the rule's words on the
// status; the open page as a file; Add to library, Remove from library and
// Save a copy to library, one request a page; the library rows of a menu;
// the vault chip's file rows and the add dialog; Choose files… of a work
// folder this desktop app keeps; Let go of one no vault of this server
// keeps. App hands in its page rows, the open page,
// the vault (vault/useVault.js) and the few commands the answers reach.
// Pure parts: workFolderActions.js.
import { pageDocument, isPdf } from "../documents/pageDocument.js";
import { batchText, runBatch } from "../library/pageBatch.js";
import { T, t } from "../shared/i18n/i18n.js";
import { IS_DESKTOP } from "../shared/lib/connector.js";
import { API, apiJson } from "../shared/lib/utils";
import { useStableActions } from "../shared/lib/stableActions.js";
import { CopyIcon, FolderMinusIcon, FolderPlusIcon } from "../shared/ui/Icons";
import { MenuItem } from "../shared/ui/Menus";
import {
  fileRow, foldersMoveRefused, letGoSaid, libraryRequest, libraryRows, librarySaid, libraryTask, mayLetGo, openFile, refusesFiles,
} from "./workFolderActions.js";
import { refusalText } from "./workFolders.js";

// `tree`: the folder tree as the rules read it (treeOf); `pages`: App's page
// rows by id; `focusedId`, `focusedBlock`: the open page; `vault`: its
// owner's; `setStatus`; `confirm(box)`, App's confirmation box; `openPage`;
// `tasks`, Background tasks; `reload(ids)`, the listing and the open page
// read again once `ids` changed; `owner`, whether the account owns the
// workspace.
export function useWorkFolderActions({ tree, pages, focusedId, focusedBlock, vault, setStatus, confirm, openPage, tasks, reload, owner = false }) {
  const open = openFile(pages.get(focusedId), focusedBlock, tree);

  // An action on pages that names a work folder's file the rule refuses
  // (`may` of its fileRules) is not made, and the status says why in the
  // rule's words of `kind`. Whether one was refused.
  function refused(ids, may, kind = "filed") {
    const no = refusesFiles(pages, ids, may, tree);
    if (no) setStatus(refusalText(kind));
    return no;
  }
  function foldersRefused(ids, parent) {
    const no = foldersMoveRefused(tree, ids, parent);
    if (no) setStatus(refusalText("directories"));
    return no;
  }

  // A PDF whose bytes a library page owns already is answered with that
  // page (an add's 409 with `page_id`, a copy's `existed`), which the
  // status names and offers: no copy was made.
  async function libraryRoute(ids, how, folder = "") {
    vault.setAddingFile(null);
    let owned = null, existed = 0;
    const result = await runBatch(ids, async (id) => {
      const { path, init } = libraryRequest(id, how, folder);
      try {
        const answer = await apiJson(`${API}${path}`, init);
        if (how === "copy" && answer?.existed) { existed += 1; owned = answer.page?.id || owned; }
      } catch (err) {
        if (err?.status === 409 && err.data?.page_id) owned = err.data.page_id;
        throw err;
      }
    }, { name: libraryTask(how, ids.length), tasks });
    await reload(ids);
    if (owned && ids.length === 1) {
      const title = pages.get(owned)?.content || t("Untitled");
      confirm({
        title: T("Already in the library"),
        message: t("Already in the library as “{title}”.", { title }),
        confirmLabel: t("Open"),
        onConfirm: () => openPage(owned),
      });
      return;
    }
    const text = batchText(librarySaid(how, result.done.length, existed), result, ids.length);
    if (text) setStatus(text);
  }

  // The library rows of a menu for work folders' files (`files`, rows):
  // Add to library…, Remove from library, Save a copy to library…
  // (libraryRows). `close` closes the menu.
  function libraryItems(files, close) {
    const { ids, add, remove, copy } = libraryRows(files, tree);
    return [
      add && (
        <MenuItem key="addfile" icon={FolderPlusIcon} title={t("Add the file's page to the library, filed in a library folder too: it stays in its work folder")}
          onClick={() => { close(); vault.setAddingFile({ ids }); }}>{t("Add to library…")}</MenuItem>
      ),
      remove && (
        <MenuItem key="removefile" icon={FolderMinusIcon} title={t("Take the file's page out of the library: it stays in its work folder, with its notes")}
          onClick={() => { close(); libraryRoute(ids, "remove"); }}>{t("Remove from library")}</MenuItem>
      ),
      copy && (
        <MenuItem key="copyfile" icon={CopyIcon} title={t("Store the PDF as it is now in the library, as a page of its own")}
          onClick={() => { close(); vault.setAddingFile({ ids, copy: true }); }}>{t("Save a copy to library…")}</MenuItem>
      ),
    ];
  }

  // Choose files… of the work folder `folderId`: its tracking dialog, in the
  // desktop app that keeps its vault (null elsewhere), and the vault's link.
  const linkOf = (folderId) => (IS_DESKTOP && folderId ? vault.links.get(folderId) || null : null);
  const chooser = (folderId) => {
    const link = linkOf(folderId);
    return link ? () => vault.track(link.id) : null;
  };

  // Let go of the work folder `folderId` (POST /api/folders/{id}/let-go),
  // asked first: its files outside the library go to Recently deleted, the
  // rest stay as library pages, and the mark comes off, so the computer
  // that kept it stops syncing it. For the workspace's owner when no vault
  // of this server keeps it (mayLetGo); null where it is not offered.
  const letGo = (folderId) => (mayLetGo(tree, folderId, { owner, kept: vault.kept }) ? () => confirm({
    title: T("Let go of this work folder?"),
    message: t("The files outside your library go to Recently deleted; your notes and papers stay in the folder. The computer that kept it stops syncing it."),
    confirmLabel: t("Let go"),
    danger: true,
    onConfirm: async () => {
      try {
        const answer = await apiJson(`${API}/folders/${encodeURIComponent(folderId)}/let-go`, { method: "POST" });
        setStatus(letGoSaid(answer?.trashed || 0));
      } catch (err) {
        setStatus(t("Could not let go of the work folder: {message}", { message: err.message || err }));
      }
      await reload(focusedId ? [focusedId] : []);
    },
  }) : null);

  // The vault chip's rows for the open page: Add to library… (a file's, or
  // an opened PDF's), and for a file Remove from library, Save a copy to
  // library… and Choose files….
  const chip = {
    onAdd: () => (open.work ? vault.setAddingFile({ ids: [focusedId] }) : vault.setAdding([focusedId])),
    file: open.work ? { library: open.inLibrary, pdf: isPdf(pageDocument(focusedBlock)) } : null,
    onRemove: () => libraryRoute([focusedId], "remove"),
    onCopy: () => vault.setAddingFile({ ids: [focusedId], copy: true }),
    onChoose: chooser(open.work) || undefined,
  };
  // The folder picker of Add to library… or Save a copy… for files, while
  // it is open (vault.addingFile): its props but the folders.
  const adding = vault.addingFile;
  const addDialog = adding ? {
    count: adding.ids.length, copy: !!adding.copy, pdf: adding.ids.some((id) => isPdf(pages.get(id)?._doc)),
    onPick: (folder) => libraryRoute(adding.ids, adding.copy ? "copy" : "add", folder),
    onClose: () => vault.setAddingFile(null),
  } : null;

  const commands = useStableActions({ refused, foldersRefused, libraryRoute, libraryItems, chooser, linkOf, letGo,
    fileOf: (id) => fileRow(pages, id) });
  return { ...commands, open, chip, addDialog };
}
