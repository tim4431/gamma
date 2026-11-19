// The stores backup tasks copy their snapshots to, off this server, as the
// Backups pane speaks of them: the bucket (BackupBucket.jsx) and Google
// Drive (BackupDrive.jsx). Their keys are the server's
// (gamma/snapshot_copies.py DESTINATIONS): a task's switch, its run's
// `last_result` count, a copy's `store`, the jobs' `store` and the
// listing's `?store=`. Every sentence a component says of a store is here,
// so BackupTasks.jsx and SettingsBackups.jsx say it of either alike; each
// is a function, translated as it is shown.
import { T, t } from "../shared/i18n/i18n.js";

export const STORES = ["bucket", "drive"];

const COPIES = "/admin/offsite/snapshots";

export const STORE = {
  bucket: {
    title: () => t("Bucket"),
    copies: COPIES, // GET: the tasks' copies, by workspace (gamma/snapshot_copies.py `listing`)
    copy: (ws, name) => `${COPIES}/${encodeURIComponent(ws)}/${encodeURIComponent(name)}`, // DELETE: one copy
    toggle: () => t("Copy to the bucket"),
    notSetUp: () => t("No bucket is set up: set one up under Bucket below, or turn this off."),
    copiesTo: () => t("copies to the bucket"),
    held: () => t("in the bucket"),
    heldTitle: () => t("A copy of this snapshot is in the bucket"),
    only: () => t("only in the bucket"),
    unreadable: (error) => t("The bucket could not be read: {error}", { error }),
    mergeQuestion: (args) => t("Merge the bucket's copy from {b} into \"{name}\"? Pages and chats it has that the workspace lacks are added; everything already there is kept.", args),
    replaceQuestion: (args) => t("Restore \"{name}\" to the bucket's copy from {b}? ALL of its current pages and chats are REPLACED by the copy's. The current state is saved first as a \"Before restore\" snapshot, so you can go back.", args),
    deleteTitle: T("Delete from the bucket"),
    deleteButton: () => t("Delete this copy from the bucket"),
    deleteLabel: (date) => t("Delete the bucket's copy from {date}", { date }),
    deleteQuestion: (args) => t("Delete the bucket's copy of \"{name}\" from {b} ({size_bytes})? This can't be undone.", args),
    deleted: () => t("Deleted from the bucket."),
  },
  drive: {
    title: () => t("Google Drive"),
    copies: `${COPIES}?store=drive`,
    copy: (ws, name) => `${COPIES}/${encodeURIComponent(ws)}/${encodeURIComponent(name)}?store=drive`,
    toggle: () => t("Copy to Google Drive"),
    notSetUp: () => t("Google Drive is not connected: connect it under Google Drive below, or turn this off."),
    copiesTo: () => t("copies to Google Drive"),
    held: () => t("in Google Drive"),
    heldTitle: () => t("A copy of this snapshot is in Google Drive"),
    only: () => t("only in Google Drive"),
    unreadable: (error) => t("Google Drive could not be read: {error}", { error }),
    mergeQuestion: (args) => t("Merge the Google Drive copy from {b} into \"{name}\"? Pages and chats it has that the workspace lacks are added; everything already there is kept.", args),
    replaceQuestion: (args) => t("Restore \"{name}\" to the Google Drive copy from {b}? ALL of its current pages and chats are REPLACED by the copy's. The current state is saved first as a \"Before restore\" snapshot, so you can go back.", args),
    deleteTitle: T("Delete from Google Drive"),
    deleteButton: () => t("Delete this copy from Google Drive"),
    deleteLabel: (date) => t("Delete the Google Drive copy from {date}", { date }),
    deleteQuestion: (args) => t("Delete the Google Drive copy of \"{name}\" from {b} ({size_bytes})? This can't be undone.", args),
    deleted: () => t("Deleted from Google Drive."),
  },
};

// The copies every store holds as one list of groups by workspace, in the
// listings' shape ({workspace, name, here, snapshots}), each copy tagged
// with its `store`, newest first. `lists`: {store: its listing's groups,
// or null while there is none}.
export function mergeCopies(lists) {
  const groups = new Map();
  for (const store of STORES) {
    for (const g of lists[store] || []) {
      const into = groups.get(g.workspace) || { ...g, snapshots: [] };
      into.name = into.name || g.name;
      into.snapshots = [...into.snapshots, ...g.snapshots.map((c) => ({ ...c, store }))];
      groups.set(g.workspace, into);
    }
  }
  for (const g of groups.values()) g.snapshots.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  return [...groups.values()];
}
