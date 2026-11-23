// The stores backup tasks copy their snapshots to, off this server, as the
// Backups pane speaks of them (Backup destinations, BackupDestinations.jsx):
// Google Drive and OneDrive (BackupCloud.jsx, signed in to) and an
// S3-compatible bucket (BackupBucket.jsx). Their keys are the server's
// (gamma/snapshot_copies.py DESTINATIONS): a task's switch, its run's
// `last_result` count, a copy's `store`, the jobs' `store` and the
// listing's `?store=`. Each store is one entry: its name as a row's label
// (`title`), inside a sentence (`the`) and at a sentence's start (`The`),
// the read that says how it is set up, and where that puts its copies ("" while
// it is not set up). Every sentence the pane says of a store is below,
// written once with the store's name in it, so a new store is one entry.
// Pure (no React): tasks/taskKinds.js names a job's store from here too.
import { t } from "../shared/i18n/i18n.js";

// Where a cloud's copies go: "someone@gmail.com › Gamma backups", "" while
// no connection can be used (the read of /admin/clouds/<store>).
export const folderLabel = (folder) => (folder || "").split("/").filter(Boolean).join(" › ");
export function cloudWhere(status, title) {
  if (!status?.connected || status.problem) return "";
  return [status.email || title, folderLabel(status.folder)].filter(Boolean).join(" › ");
}

// Where the bucket is: "s3://gamma-backups/prefix", the endpoint's host in
// place of s3:// for R2, MinIO and the like. "" without a bucket.
export function bucketWhere(settings) {
  const { bucket, endpoint, prefix } = settings || {};
  if (!bucket) return "";
  let at = "s3://";
  if (endpoint) { try { at = `${new URL(endpoint).host}/`; } catch { at = `${endpoint}/`; } }
  return `${at}${bucket}${prefix ? `/${prefix}` : ""}`;
}

// The order the pane lists them in.
export const STORES = ["drive", "onedrive", "bucket"];

export const STORE = {
  drive: {
    title: () => t("Google Drive"), the: () => t("Google Drive"), The: () => t("Google Drive"),
    read: "/admin/clouds/drive", where: (status) => cloudWhere(status, t("Google Drive")),
  },
  onedrive: {
    title: () => t("OneDrive"), the: () => t("OneDrive"), The: () => t("OneDrive"),
    read: "/admin/clouds/onedrive", where: (status) => cloudWhere(status, t("OneDrive")),
  },
  bucket: {
    title: () => t("Bucket"), the: () => t("the bucket"), The: () => t("The bucket"),
    read: "/admin/offsite", where: (read) => bucketWhere(read?.settings),
  },
};

// A store's name inside a sentence; the key itself for one this build does
// not know (a job from a newer server).
const the = (store) => STORE[store]?.the() ?? store;
const The = (store) => STORE[store]?.The() ?? store;

const COPIES = "/admin/offsite/snapshots";
// GET: the tasks' copies in a store, by workspace (gamma/snapshot_copies.py `listing`).
export const copiesOf = (store) => (store === "bucket" ? COPIES : `${COPIES}?store=${store}`);
// DELETE: one copy.
export const copyOf = (store, ws, name) => `${COPIES}/${encodeURIComponent(ws)}/${encodeURIComponent(name)}`
  + (store === "bucket" ? "" : `?store=${store}`);

export const storeText = {
  held: (store) => t("in {store}", { store: the(store) }),
  heldTitle: (store) => t("A copy of this snapshot is in {store}", { store: the(store) }),
  only: (store) => t("only in {store}", { store: the(store) }),
  copiesTo: (store) => t("copies to {store}", { store: the(store) }),
  unreadable: (store, error) => t("{store} could not be read: {error}", { store: The(store), error }),
  notSetUp: (store) => t("{store} is not set up: set it up under Backup destinations, or turn it off here.", { store: The(store) }),
  mergeQuestion: (store, args) => t("Merge the copy in {store} from {b} into \"{name}\"? Pages and chats it has that the workspace lacks are added; everything already there is kept.", { ...args, store: the(store) }),
  replaceQuestion: (store, args) => t("Restore \"{name}\" to the copy in {store} from {b}? ALL of its current pages and chats are REPLACED by the copy's. The current state is saved first as a \"Before restore\" snapshot, so you can go back.", { ...args, store: the(store) }),
  deleteTitle: (store) => t("Delete from {store}", { store: the(store) }),
  deleteButton: (store) => t("Delete this copy from {store}", { store: the(store) }),
  deleteLabel: (store, date) => t("Delete the copy in {store} from {date}", { store: the(store), date }),
  deleteQuestion: (store, args) => t("Delete the copy of \"{name}\" in {store} from {b} ({size_bytes})? This can't be undone.", { ...args, store: the(store) }),
  deleted: (store) => t("Deleted from {store}.", { store: the(store) }),
  // a job's title (tasks/taskKinds.js)
  restoreNew: (store, name) => t("Restore “{name}” from {store} as a new workspace", { name, store: the(store) }),
  restoreMerge: (store, name) => t("Merge a copy from {store} into “{name}”", { name, store: the(store) }),
  restoreReplace: (store, name) => t("Restore “{name}” from {store}", { name, store: the(store) }),
  download: (store, name) => t("Download of “{name}” from {store}", { name, store: the(store) }),
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
