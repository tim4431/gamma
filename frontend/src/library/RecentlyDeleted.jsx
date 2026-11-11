import { useEffect, useState } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { API, apiJson } from "../shared/lib/utils";
import { DialogButtons, Empty, SubDialog } from "../settings/SettingsKit";
import { FileTextIcon, Trash2Icon, UndoIcon } from "../shared/ui/Icons";
import { Reveal } from "../shared/ui/MotionWidgets";
import { firstFolderPath, formatFullDate, formatRelativeTime, pagePlaceLabel } from "./libraryUtils.js";
import { aheadTrash, dropTrash } from "./trashList.js";

// Recently deleted (docs/dev/home_library.md): the pages deleted in the last
// 30 days (GET /api/trash, gamma/trash.py), newest first. A page deleted
// with its sub-pages is one entry, which says how many went with it
// (`sub_pages`). Restore puts one back where it was filed, its sub-pages
// with it; the trash icon deletes it for good after a confirmation, and so
// does Empty for all of them. `confirm` is App's
// confirm box, `onRestored()` refreshes the library; `tree` (the listing's,
// libraryUtils.libraryTree) names the folder a row was filed in, and
// `pages` (App's page blocks by id) the page a sub-page deleted on its own
// comes back inside (`parent`, its path with it). The list
// asked for ahead, as the pointer or the focus reached the trash button
// (trashList.js), shows at once; without it the list comes into focus once
// it is in (Reveal). A work folder's file has no Restore unless the server
// says a person's restore passes (`restorable`, gamma/trash.py): a note of a
// work folder that pulls, sent there from Gamma. One its vault sent (its
// file deleted or untracked) comes back with its file
// (docs/dev/work_folders.md).
export default function RecentlyDeleted({ onClose, confirm, onRestored, setStatus, tree, pages: pagesById }) {
  const [ahead] = useState(aheadTrash);
  const [list, setList] = useState(ahead?.value || null); // {pages, keep_days} | {error}
  const [busy, setBusy] = useState(false); // a restore or delete is running

  async function load() {
    try {
      setList(await apiJson(`${API}/trash`));
    } catch (err) {
      setList({ error: err.message || String(err) });
    }
  }
  useEffect(() => {
    // a fresh answer asked ahead is the list; an older one is asked again
    if (!ahead?.fresh) load();
    else if (!ahead.value) ahead.list.then((got) => (got ? setList(got) : load()));
    return dropTrash;
  }, []);

  async function run(request, done) {
    setBusy(true);
    try {
      done(await request());
    } catch (err) {
      setStatus(t("Recently deleted: {message}", { message: err.message || err }));
    } finally {
      setBusy(false);
      load();
    }
  }
  // The page a restore puts `page` back inside, by its path ("Top › Page");
  // "" for one that comes back in the library, null for a page the
  // listing lacks.
  const insideOf = (page) => (!page.parent ? "" : pagesById?.has(page.parent) ? pagePlaceLabel(pagesById, page.parent) : null);
  const restore = (page) => run(() => apiJson(`${API}/trash/${encodeURIComponent(page.id)}/restore`, { method: "POST" }),
    () => {
      const inside = insideOf(page);
      setStatus(inside ? t("Restored “{title}” inside “{page}”.", { title: page.title, page: inside })
        : t("Restored “{title}”.", { title: page.title }));
      onRestored();
    });
  const deleteForGood = (page) => confirm({
    title: t("Delete permanently"),
    message: page.sub_pages
      ? tn("Delete “{title}” and the {n} page inside it for good? Their notes, highlights and chats go, and this can't be undone.",
        "Delete “{title}” and the {n} pages inside it for good? Their notes, highlights and chats go, and this can't be undone.",
        page.sub_pages, { title: page.title })
      : t("Delete “{title}” for good? Its notes, highlights and chats go, and this can't be undone.", { title: page.title }),
    confirmLabel: t("Delete permanently"),
    danger: true,
    onConfirm: () => run(() => apiJson(`${API}/trash/${encodeURIComponent(page.id)}`, { method: "DELETE" }),
      () => setStatus(t("Deleted “{title}” for good.", { title: page.title }))),
  });
  const pages = list?.pages || [];
  // Every page Empty deletes: each entry and the sub-pages that went with it.
  const pageCount = pages.reduce((n, page) => n + 1 + (page.sub_pages || 0), 0);
  const emptyAll = () => confirm({
    title: t("Empty Recently deleted"),
    message: tn("Delete {n} page for good? This can't be undone.", "Delete {n} pages for good? This can't be undone.", pageCount),
    confirmLabel: t("Empty"),
    danger: true,
    // the status repeats the count confirmed, sub-pages included
    onConfirm: () => run(() => apiJson(`${API}/trash`, { method: "DELETE" }),
      () => setStatus(tn("Deleted {n} page for good.", "Deleted {n} pages for good.", pageCount))),
  });
  const daysLeft = (purgeAt) => Math.max(0, Math.ceil((new Date(purgeAt) - Date.now()) / 86400000));

  return (
    <SubDialog title={t("Recently deleted")} onClose={onClose}>
      <div className="settingsPaneHint">
        {t("Deleted pages stay here for {n} days with their notes and chats, then they are deleted for good.", { n: list?.keep_days || 30 })}
      </div>
      <Reveal ready={!!list} k="trash" fallback={<Empty icon={Trash2Icon}>{t("Loading…")}</Empty>}>
        {list?.error ? <div className="settingsPaneHint aiKeysError">{list.error}</div> : null}
        {list && !list.error && !pages.length ? <Empty icon={Trash2Icon}>{t("Nothing was deleted recently.")}</Empty> : null}
        {pages.map((page) => (
          <div key={page.id} className="aiProvRow" data-page={page.id}>
            <span className="aiProvAvatar"><FileTextIcon size={16} /></span>
            <span className="aiProvMeta">
              <span className="aiProvName">{page.title}</span>
              <span className="aiProvDesc" title={formatFullDate(page.deleted_at)}>
                {[page.deleted_by ? t("Deleted {when} by {name}", { when: formatRelativeTime(page.deleted_at), name: page.deleted_by })
                  : t("Deleted {when}", { when: formatRelativeTime(page.deleted_at) }),
                page.sub_pages ? tn("with {n} page inside", "with {n} pages inside", page.sub_pages) : "",
                // a sub-page deleted on its own: Restore puts it back inside its parent
                insideOf(page) ? t("comes back inside “{page}”", { page: insideOf(page) })
                  : insideOf(page) === null ? t("comes back inside the page it was in") : "",
                page.purge_at ? tn("{n} day left", "{n} days left", daysLeft(page.purge_at)) : "",
                firstFolderPath(tree, page.folders)].filter(Boolean).join(" · ")}
              </span>
            </span>
            <span className="aiProvActions">
              {page.restorable !== false ? (
                <button className="uiBtn sm" disabled={busy} onClick={() => restore(page)}>
                  <UndoIcon size={16} />{t("Restore")}
                </button>
              ) : <span className="aiProvDesc">{t("Comes back when its file is tracked again.")}</span>}
              <button className="uiBtn sm iconSq danger" disabled={busy} onClick={() => deleteForGood(page)}
                title={t("Delete permanently")} aria-label={t("Delete “{title}” permanently", { title: page.title })}>
                <Trash2Icon size={16} />
              </button>
            </span>
          </div>
        ))}
      </Reveal>
      <DialogButtons>
        {pages.length ? <button className="uiBtn danger" disabled={busy} onClick={emptyAll}>{t("Empty")}</button> : null}
        <button className="uiBtn" onClick={onClose}>{t("Close")}</button>
      </DialogButtons>
    </SubDialog>
  );
}
