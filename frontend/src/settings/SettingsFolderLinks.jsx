// Settings → Workspaces → Folders on disk: the folders of the open workspace
// the server keeps as directories on its own disk (docs/dev/folder_sync.md
// "Links kept by the server"; GUI for /api/folder-links*). A row per link
// with the folder, the directory under the server's folders root and the
// last round's outcome; Sync runs a round now; the "more" menu writes every
// file again, replaces files changed on disk, switches the notes files on or
// off, and removes the link with or without what it wrote. "Keep a folder
// on disk" picks the folder, names the directory and chooses whether notes
// files are written beside the PDFs.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Section, SubDialog, Field, DialogButtons, Segmented, Empty } from "./SettingsKit";
import { ActionMenu, MenuSelect } from "../shared/ui/Menus";
import { AlertCircleIcon, FileTextIcon, HardDriveIcon, MoreIcon, PlusIcon, RefreshIcon, TrashIcon } from "../shared/ui/Icons";
import { clock } from "../collaboration/MirrorPopover";
import { T, t, tn } from "../shared/i18n/i18n.js";

const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const pathText = (names) => names.join(" / ");

// The open workspace's links (null while loading): `{links, root}`.
// `enabled` false (a guest, or signed out) reads nothing; `key` (the
// workspace id) reads again when the open workspace changes.
export function useFolderLinks(enabled = true, key = "") {
  const [data, setData] = React.useState(null);
  const refresh = React.useCallback(() => {
    if (!enabled) { setData({ links: [], root: "" }); return; }
    apiJson(`${API}/folder-links`).then(setData).catch(() => setData({ links: [], root: "" }));
  }, [enabled, key]);  // eslint-disable-line react-hooks/exhaustive-deps
  React.useEffect(() => { refresh(); }, [refresh]);
  // while a round runs its row moves: read again every 2 s
  const running = !!data?.links?.some((l) => l.status?.running);
  React.useEffect(() => {
    if (!running) return undefined;
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [running, refresh]);
  return [data, refresh];
}

// The folder tree, for naming a link's folder and for the dialog's choices.
function useFolderNames() {
  const [folders, setFolders] = React.useState([]);
  React.useEffect(() => {
    apiJson(`${API}/sync/folders`).then((d) => setFolders(d.folders || [])).catch(() => setFolders([]));
  }, []);
  return folders;
}

// The last round in a few words: "2 files added, 1 file removed".
export function roundSummary(status) {
  const c = status?.counts || {};
  const parts = [];
  if (c.added) parts.push(tn("{n} file added", "{n} files added", c.added));
  if (c.updated) parts.push(tn("{n} file updated", "{n} files updated", c.updated));
  if (c.renamed) parts.push(tn("{n} file renamed", "{n} files renamed", c.renamed));
  if (c.removed) parts.push(tn("{n} file removed", "{n} files removed", c.removed));
  return parts.join(", ");
}

export function linkStatusLine(l) {
  const s = l.status || {};
  if (s.running) return t("syncing…");
  if (s.last_error) return t("problem: {error}", { error: s.last_error });
  if (!s.last_sync) return t("not written yet");
  return t("up to date {last_sync} · {changed}", { last_sync: clock(s.last_sync), changed: roundSummary(s) || t("nothing had changed") });
}

export function FolderLinkDialog({ folders, root, busy, error, onSubmit, onClose }) {
  const [folder, setFolder] = React.useState(folders[0]?.id || "root");
  const [path, setPath] = React.useState("");
  const [notes, setNotes] = React.useState(true);
  const chosen = folders.find((f) => f.id === folder);
  const suggested = chosen ? chosen.path.join("/") : t("Library");
  return (
    <SubDialog title={t("Keep a folder on disk")} onClose={onClose} draft={path}>
      <div className="settingsForm">
        <Field label={t("Folder")} hint={t("Its pages and subfolders, written to the server's disk and kept up to date")}>
          <MenuSelect block label={t("Folder")} value={folder} onChange={setFolder}
            options={[["root", t("Whole library")], ...folders.map((f) => [f.id, pathText(f.path)])]} />
        </Field>
        <Field label={t("Directory")} hint={t("Under {root}. Empty: the folder's own name.", { root })}>
          <input className="aiKeyInput" value={path} placeholder={suggested} onChange={(e) => setPath(e.target.value)} />
        </Field>
        <Field label={t("Files")} hint={t("Each paper's PDF and, beside it, a Markdown note of its highlights and notes in Obsidian's dialect — or the PDFs alone")}>
          <Segmented value={notes ? "notes" : "pdfs"} onChange={(v) => setNotes(v === "notes")}
            options={[["notes", t("Papers and notes")], ["pdfs", t("Papers only")]]} />
        </Field>
        {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
        <DialogButtons>
          <button className="uiBtn" onClick={onClose} disabled={busy}>{t("Cancel")}</button>
          <button className="uiBtn primary" disabled={busy} onClick={() => onSubmit({ folder, path: path.trim(), notes })}>
            {busy ? t("Linking…") : t("Keep on disk")}
          </button>
        </DialogButtons>
      </div>
    </SubDialog>
  );
}

export function FolderLinksSection({ data, refresh, confirm, setStatus }) {
  const folders = useFolderNames();
  const [creating, setCreating] = React.useState(false);
  const [createError, setCreateError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const links = data?.links || [];
  const root = data?.root || "";
  const nameOf = (l) => (l.folder_id === "root" ? t("Whole library") : pathText(folders.find((f) => f.id === l.folder_id)?.path || [l.path]));

  async function submit(body) {
    setBusy(true);
    setCreateError("");
    try {
      await apiJson(`${API}/folder-links`, json("POST", body));
      setCreating(false);
      setStatus?.(t("Writing the folder in the background."));
      refresh();
      setTimeout(refresh, 3000);
    } catch (err) {
      setCreateError(err.message);
    } finally {
      setBusy(false);
    }
  }
  async function call(l, path, init, ok) {
    setBusy(true);
    try {
      await apiJson(`${API}/folder-links/${encodeURIComponent(l.id)}${path}`, init);
      if (ok) setStatus?.(ok);
    } catch (err) {
      setStatus?.(t("Could not do that: {message}", { message: err.message }));
    } finally {
      setBusy(false);
      refresh();
      setTimeout(refresh, 3000);
    }
  }
  // Sync runs a round inline and reports its outcome.
  async function syncNow(l) {
    setBusy(true);
    try {
      const d = await apiJson(`${API}/folder-links/${encodeURIComponent(l.id)}/sync?wait=1`, { method: "POST" });
      const s = d.status || {};
      setStatus?.(s.last_error ? t("Sync problem: {last_error}", { last_error: s.last_error })
        : t("Up to date — {side}.", { side: roundSummary(s) || t("nothing had changed") }));
    } catch (err) {
      setStatus?.(t("Sync failed: {message}", { message: err.message }));
    } finally {
      setBusy(false);
      refresh();
    }
  }
  function remove(l, withFiles) {
    confirm({
      title: withFiles ? T("Remove link and files") : T("Remove link"),
      message: withFiles
        ? t("Takes back what the sync wrote under {dest}; files you added or changed there stay.", { dest: l.dest })
        : t("{dest} stays as it is and is no longer kept up to date.", { dest: l.dest }),
      confirmLabel: withFiles ? t("Remove link and files") : t("Remove link"), danger: withFiles,
      onConfirm: () => call(l, withFiles ? "?remove_files=1" : "", { method: "DELETE" },
        withFiles ? t("Link and files removed.") : t("Link removed.")),
    });
  }

  function row(l) {
    const s = l.status || {};
    const tone = s.running ? "busy" : s.last_error ? "error" : s.last_sync ? "ok" : "";
    const Icon = s.last_error ? AlertCircleIcon : HardDriveIcon;
    const kept = s.kept || [];
    const more = [
      { icon: RefreshIcon, label: T("Write everything again"), title: T("Every file from the server, not only the changed ones"),
        disabled: s.running, onClick: () => call(l, "/sync?full=1", { method: "POST" }, t("Writing everything again in the background.")) },
      { icon: RefreshIcon, label: T("Replace files changed on disk"), title: T("Files changed in the directory are kept until you ask for this"),
        disabled: s.running || !kept.length, onClick: () => call(l, "/sync?force=1", { method: "POST" }, t("Replacing the changed files in the background.")) },
      l.notes
        ? { icon: FileTextIcon, label: T("Papers only"), title: T("Stop writing the notes files; they leave the directory at the next round"),
            onClick: () => call(l, "", json("PATCH", { notes: false })) }
        : { icon: FileTextIcon, label: T("Papers and notes"), title: T("Write a Markdown note beside each paper"),
            onClick: () => call(l, "", json("PATCH", { notes: true })) },
      { icon: TrashIcon, label: T("Remove link"), title: T("The directory stays as it is"), onClick: () => remove(l, false) },
      { icon: TrashIcon, label: T("Remove link and files"), danger: true, title: T("Takes back what the sync wrote; your own files there stay"),
        onClick: () => remove(l, true) },
    ];
    return (
      <div key={l.id} className="aiProvRow" data-folder-link={l.id}>
        <span className={`aiProvAvatar mirrorAvatar ${tone}`}><Icon size={16} /></span>
        <span className="aiProvMeta">
          <span className="aiProvName">
            {nameOf(l)}
            {!l.notes ? <span className="uiTag">{t("papers only")}</span> : null}
            {s.last_error ? <span className="uiTag warn">{t("problem")}</span> : null}
            {kept.length ? (
              <span className="uiTag warn" title={kept.map((k) => `${k.path}: ${k.why}`).join("\n")}>
                {tn("{n} file kept", "{n} files kept", kept.length)}
              </span>
            ) : null}
          </span>
          <span className="aiProvDesc" title={l.dest}>{l.dest}</span>
          <span className="aiProvDesc">{linkStatusLine(l)}</span>
        </span>
        <span className="aiProvActions">
          <button className="uiBtn sm" disabled={busy || s.running} onClick={() => syncNow(l)} title={t("Write the changes now")}>
            <RefreshIcon size={14} /> {t("Sync")}
          </button>
          <ActionMenu label={t("More")} icon={MoreIcon} iconOnly disabled={busy} items={more} />
        </span>
      </div>
    );
  }

  return (
    <>
      <Section
        title={t("Folders on disk")}
        action={(
          <button className="uiBtn sm" disabled={busy} onClick={() => { setCreateError(""); setCreating(true); }}>
            <PlusIcon size={14} /> {t("Keep a folder on disk")}
          </button>
        )}
      >
        {data === null ? <Empty icon={HardDriveIcon}>{t("Loading…")}</Empty>
          : links.length ? links.map(row)
          : <Empty icon={HardDriveIcon}>
              <span>{t("No folders on disk yet.")}</span>
              <span className="settingDesc">{t("The server writes a folder's papers and notes to a directory on its own disk and keeps it up to date; a NAS share then carries it to your computers.")}</span>
            </Empty>}
      </Section>
      {creating ? (
        <FolderLinkDialog folders={folders} root={root} busy={busy} error={createError} onSubmit={submit} onClose={() => setCreating(false)} />
      ) : null}
    </>
  );
}
