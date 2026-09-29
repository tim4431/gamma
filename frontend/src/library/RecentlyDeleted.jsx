import { useEffect, useState } from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { API, apiJson } from "../shared/lib/utils";
import { DialogButtons, Empty, SubDialog } from "../settings/SettingsKit";
import { FileTextIcon, Trash2Icon, UndoIcon } from "../shared/ui/Icons";
import { formatFullDate, formatRelativeTime } from "./libraryUtils.js";

// Recently deleted (docs/dev/home_library.md): the pages deleted in the last
// 30 days (GET /api/trash, gamma/trash.py), newest first. Restore puts one
// back where it was filed; the trash icon deletes it for good after a
// confirmation, and so does Empty for all of them. `confirm` is App's
// confirm box, `onRestored()` refreshes the library.
export default function RecentlyDeleted({ onClose, confirm, onRestored, setStatus }) {
  const [list, setList] = useState(null); // {pages, keep_days} | {error}
  const [busy, setBusy] = useState(false); // a restore or delete is running

  async function load() {
    try {
      setList(await apiJson(`${API}/trash`));
    } catch (err) {
      setList({ error: err.message || String(err) });
    }
  }
  useEffect(() => { load(); }, []);

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
  const restore = (page) => run(() => apiJson(`${API}/trash/${encodeURIComponent(page.id)}/restore`, { method: "POST" }),
    () => {
      setStatus(t("Restored “{title}”.", { title: page.title }));
      onRestored();
    });
  const deleteForGood = (page) => confirm({
    title: t("Delete permanently"),
    message: t("Delete “{title}” for good? Its notes, highlights and chats go, and this can't be undone.", { title: page.title }),
    confirmLabel: t("Delete permanently"),
    danger: true,
    onConfirm: () => run(() => apiJson(`${API}/trash/${encodeURIComponent(page.id)}`, { method: "DELETE" }),
      () => setStatus(t("Deleted “{title}” for good.", { title: page.title }))),
  });
  const pages = list?.pages || [];
  const emptyAll = () => confirm({
    title: t("Empty Recently deleted"),
    message: tn("Delete {n} page for good? This can't be undone.", "Delete {n} pages for good? This can't be undone.", pages.length),
    confirmLabel: t("Empty"),
    danger: true,
    onConfirm: () => run(() => apiJson(`${API}/trash`, { method: "DELETE" }),
      (out) => setStatus(tn("Deleted {n} page for good.", "Deleted {n} pages for good.", out.deleted.length))),
  });
  const daysLeft = (purgeAt) => Math.max(0, Math.ceil((new Date(purgeAt) - Date.now()) / 86400000));

  return (
    <SubDialog title={t("Recently deleted")} onClose={onClose}>
      <div className="settingsPaneHint">
        {t("Deleted pages stay here for {n} days with their notes and chats, then they are deleted for good.", { n: list?.keep_days || 30 })}
      </div>
      {!list ? <Empty icon={Trash2Icon}>{t("Loading…")}</Empty> : null}
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
              page.purge_at ? tn("{n} day left", "{n} days left", daysLeft(page.purge_at)) : "",
              page.folder].filter(Boolean).join(" · ")}
            </span>
          </span>
          <span className="aiProvActions">
            <button className="uiBtn sm" disabled={busy} onClick={() => restore(page)}>
              <UndoIcon size={16} />{t("Restore")}
            </button>
            <button className="uiBtn sm iconSq danger" disabled={busy} onClick={() => deleteForGood(page)}
              title={t("Delete permanently")} aria-label={t("Delete “{title}” permanently", { title: page.title })}>
              <Trash2Icon size={16} />
            </button>
          </span>
        </div>
      ))}
      <DialogButtons>
        {pages.length ? <button className="uiBtn danger" disabled={busy} onClick={emptyAll}>{t("Empty")}</button> : null}
        <button className="uiBtn" onClick={onClose}>{t("Close")}</button>
      </DialogButtons>
    </SubDialog>
  );
}
