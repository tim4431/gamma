// App's commands over work folders' files and folders (docs/dev/
// work_folders.md "Add to library, Save a copy, Remove", "The op rule"):
// the refusals a menu, a drop or a key meets, with the rule's words on the
// status; the open page as a file; Add to library, Remove from library and
// Save a copy to library, one request a page; the library rows of a menu;
// the vault chip's file rows and the add dialog; Choose files… of a work
// folder this desktop app keeps; Stop syncing (Detach) of a synced folder's
// root, in every window (DetachDialog.jsx); what a synced folder's root says
// of the computer that keeps it. App hands in its page rows, the open page,
// the vault (vault/useVault.js) and the few commands the answers reach.
// Pure parts: workFolderActions.js, detach.js, vault/vaultState.js
// folderKeeper.
import { useEffect, useRef, useState } from "react";
import { pageDocument, isPdf } from "../documents/pageDocument.js";
import { batchText, runBatch } from "../library/pageBatch.js";
import { T, t } from "../shared/i18n/i18n.js";
import { IS_DESKTOP } from "../shared/lib/connector.js";
import { API, apiJson } from "../shared/lib/utils";
import { useStableActions } from "../shared/lib/stableActions.js";
import { CopyIcon, FolderMinusIcon, FolderPlusIcon } from "../shared/ui/Icons";
import { MenuItem } from "../shared/ui/Menus";
import { folderKeeper } from "../vault/vaultState.js";
import { detachLabel, detachRefusal, detachSaid } from "./detach.js";
import {
  fileRow, foldersMoveRefused, libraryRequest, libraryRows, librarySaid, libraryTask, openFile, refusesFiles,
} from "./workFolderActions.js";
import { markOf, refusalText, treeOf } from "./workFolders.js";

// `tree`: the folder tree as the rules read it (treeOf); `pages`: App's page
// rows by id; `focusedId`, `focusedBlock`: the open page; `vault`: its
// owner's; `setStatus`; `confirm(box)`, App's confirmation box; `openPage`;
// `tasks`, Background tasks; `reload(ids)`, the listing and the open page
// read again once `ids` changed; `menuFolder`, the folder whose menu is
// open ("" for none), a synced folder's root of which is asked whether it
// may be detached here; `listing` App's listing tree, with which the
// computers that keep the synced folders are read again while `signedIn`
// (an account's own session).
export function useWorkFolderActions({ tree, pages, focusedId, focusedBlock, vault, setStatus, confirm, openPage, tasks, reload,
  menuFolder = "", listing, signedIn = false }) {
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
        <MenuItem key="addfile" icon={FolderPlusIcon} title={t("Add the file's page to the library, filed in a library folder too: it stays in its synced folder")}
          onClick={() => { close(); vault.setAddingFile({ ids }); }}>{t("Add to library…")}</MenuItem>
      ),
      remove && (
        <MenuItem key="removefile" icon={FolderMinusIcon} title={t("Take the file's page out of the library: it stays in its synced folder, with its notes")}
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

  // The account's computers that keep the workspace's synced folders, by
  // folder (GET /api/folders/computers), and what a synced folder's root
  // says of the one that keeps it (a StateTag's state with its icon's
  // `badge` and the `computer`, or null: folderKeeper): this desktop app's
  // own vault of it first.
  const [kept, setKept] = useState({});
  useEffect(() => {
    if (!signedIn) { setKept({}); return undefined; }
    let on = true;
    apiJson(`${API}/folders/computers`).then((d) => { if (on) setKept(d?.folders || {}); }, () => { if (on) setKept({}); });
    return () => { on = false; };
  }, [listing, signedIn]);
  const keeperOf = (folderId) => (markOf(tree, folderId) ? folderKeeper(linkOf(folderId), kept[folderId]) : null);

  // Stop syncing (Detach) of a synced folder's root: what GET
  // /api/folders/{id}/detach answered of each one asked (`lookDetach`: each
  // root once as the listing first names it, so its menu opens whole, and
  // again as its menu or the vault chip opens; false for a read that
  // failed), and the folder whose dialog is open.
  const [plans, setPlans] = useState(() => new Map());
  const [detaching, setDetaching] = useState("");
  const askedAhead = useRef(new Set());
  async function lookDetach(folderId) {
    if (!folderId || !markOf(tree, folderId)) return;
    try {
      const plan = await apiJson(`${API}/folders/${encodeURIComponent(folderId)}/detach`);
      setPlans((prev) => new Map(prev).set(folderId, plan));
    } catch {
      // a server from before Detach, or the folder gone meanwhile: no row
      setPlans((prev) => (prev.has(folderId) ? prev : new Map(prev).set(folderId, false)));
    }
  }
  // A menu's row for the synced folder's root `folderId` (null for any
  // other folder, outside an account's session, and once its read failed):
  // {label, start, disabled, why}, Stop syncing with {computer}… opening the
  // dialog where its plan says the account may; disabled, "Stop syncing…",
  // while the plan is read and where the account may not, `why` the rule
  // that says no (the row's title).
  const detachOf = (folderId) => {
    if (!signedIn || !folderId || !markOf(tree, folderId)) return null;
    const plan = plans.get(folderId);
    if (plan === false) return null;
    if (!plan) return { label: detachLabel(null), start: () => {}, disabled: true, why: "" };
    if (!plan.may) return { label: detachLabel(null), start: () => {}, disabled: true, why: detachRefusal(plan) };
    return { label: detachLabel(plan), start: () => setDetaching(folderId), disabled: false, why: "" };
  };
  // The dialog's props while it is open: once detached the status says so,
  // the folder's plan goes, and the listing, the open page and the desktop
  // app's links are read again.
  const name = (folderId) => treeOf(tree).lib.folders.get(folderId)?.name || t("Untitled");
  const detachDialog = detaching ? {
    folderId: detaching, name: name(detaching), plan: plans.get(detaching),
    onClose: () => setDetaching(""),
    onDone: async (how, answer) => {
      setStatus(detachSaid(name(detaching), how, answer));
      setDetaching("");
      setPlans((prev) => { const next = new Map(prev); next.delete(detaching); return next; });
      vault.refreshLinks();
      await reload(focusedId ? [focusedId] : []);
    },
  } : null;

  // The vault chip's rows for the open page: Add to library… (a file's, or
  // an opened PDF's), and for a file Remove from library, Save a copy to
  // library…, Choose files… and Stop syncing with {computer}… of its work
  // folder.
  const chip = {
    onAdd: () => (open.work ? vault.setAddingFile({ ids: [focusedId] }) : vault.setAdding([focusedId])),
    file: open.work ? { library: open.inLibrary, pdf: isPdf(pageDocument(focusedBlock)) } : null,
    onRemove: () => libraryRoute([focusedId], "remove"),
    onCopy: () => vault.setAddingFile({ ids: [focusedId], copy: true }),
    onChoose: chooser(open.work) || undefined,
    detach: detachOf(open.work),
  };
  // The folder picker of Add to library… or Save a copy… for files, while
  // it is open (vault.addingFile): its props but the folders.
  const adding = vault.addingFile;
  const addDialog = adding ? {
    count: adding.ids.length, copy: !!adding.copy, pdf: adding.ids.some((id) => isPdf(pages.get(id)?._doc)),
    onPick: (folder) => libraryRoute(adding.ids, adding.copy ? "copy" : "add", folder),
    onClose: () => vault.setAddingFile(null),
  } : null;

  const commands = useStableActions({ refused, foldersRefused, libraryRoute, libraryItems, chooser, linkOf, lookDetach,
    fileOf: (id) => fileRow(pages, id) });
  // Each synced folder's root is asked ahead, once, as the listing names it
  // in an account's session; a folder's menu, in the library or the Files
  // window, asks again as it opens: its Stop syncing row is disabled until
  // an answer is in, so the menu never grows under the pointer.
  useEffect(() => {
    if (!signedIn) return;
    for (const root of treeOf(tree).marks.keys()) {
      if (askedAhead.current.has(root)) continue;
      askedAhead.current.add(root);
      commands.lookDetach(root);
    }
  }, [tree, signedIn, commands]);
  useEffect(() => { commands.lookDetach(menuFolder); }, [menuFolder, commands]);
  return { ...commands, open, chip, addDialog, detachOf, detachDialog, keeperOf };
}
