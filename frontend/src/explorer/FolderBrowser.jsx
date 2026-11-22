// The folder browser (docs/dev/ui-design.md "Trees"): a store's folders as
// a tree, to choose one. Settings → Backups chooses with it the folder of
// Google Drive copies go to (BackupDrive.jsx, in its Edit dialog) and the
// bucket's prefix (BackupBucket.jsx, Browse… beside the field, in
// FolderBrowserDialog); any place that lists folders a level at a time can
// hand it a `source`. Above the tree, the folder chosen as crumbs, New
// folder and Refresh; the tree is TreeRow's rows with useTree's keys, the
// Files window's: a press on a folder chooses and opens it, on the folder
// chosen opens or closes it; Space chooses, Enter and the arrows open and
// close; its files show with their size and date, and a folder is read the
// first time it opens (folderBrowser.js browserRows). New folder names a
// folder in the one chosen, in place: made at once where the source can
// make one (`create`), else drawn until something is saved in it (a
// bucket's folders are only its keys' names).
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { composing } from "../shared/lib/hotkeys.js";
import { fmtBytes } from "../shared/lib/format.js";
import FileIcon from "../shared/ui/FileIcon.jsx";
import { FolderPlusIcon, RefreshIcon } from "../shared/ui/Icons";
import { formatFullDate, formatShortDate } from "../library/libraryUtils";
import { DialogButtons, FormNotice, SubDialog } from "../settings/SettingsKit";
import { browserRows, cleanName, joinPath, nameProblem, pathsTo, unread } from "./folderBrowser.js";
import TreeRow from "./TreeRow.jsx";
import { useTree } from "./useTree.js";
import "./folderBrowser.css";

// `source`: what is browsed, the same object for the browser's life —
// {name (the top's, "My Drive", the bucket's), icon (the top's glyph),
// list(path) → a promise of {folders: [{name, modified}], files: [{name,
// size, modified}], more}, its error's message said under the folder, and
// create(path) → a promise of the path as made, when the source makes
// folders (a folder of its path, those above it made too)}. `value` the
// path chosen ("" the top, which a new folder may go in even where the
// folder cannot be the top: the form says so), onChange(path); onBusy(on)
// hears while a new folder is being made, which a form's Save waits for;
// `label` the tree's name.
export default function FolderBrowser({ source, value, onChange, onBusy, label }) {
  const [reads, setReads] = useState(() => new Map());
  // the folder chosen open, and every folder above it, so it shows
  const [expanded, setExpanded] = useState(() => new Set(["", ...pathsTo(value)]));
  // folders not made yet: those of the path chosen (it may be gone, or its
  // first copy not sent), and the new ones of a source that makes none
  const [added, setAdded] = useState(() => pathsTo(value));
  const [naming, setNaming] = useState(null); // {parent, text, busy} while a new folder is named
  const [error, setError] = useState("");
  const [active, setActive] = useState("");
  const asked = useRef(new Map()); // a folder's path → its last read's number: an older answer is dropped
  const readSeq = useRef(0);        // the reads' numbers, across Refresh
  // whether the name field still takes a commit: Enter, Escape and a commit
  // end it before the field goes, whose blur then commits nothing
  const field = useRef(false);
  // the row the keys go to once it is drawn: the folder chosen as the
  // browser opens (scrolled to, not focused), a folder named by the keys
  const reveal = useRef({ key: `folder:${value}`, focus: false });
  const chosen = useRef(value); // the folder chosen now, for a make that ends after another was chosen
  chosen.current = value;

  const rows = browserRows({ root: source.name, reads, expanded, added, naming });

  const setRead = (path, entry) => setReads((held) => new Map(held).set(path, entry(held.get(path))));
  async function read(path) {
    const n = ++readSeq.current;
    asked.current.set(path, n);
    setRead(path, (held) => ({ ...held, loading: true, error: "" }));
    try {
      const got = await source.list(path);
      if (asked.current.get(path) === n) {
        setRead(path, () => ({ loaded: true, loading: false, error: "", folders: got.folders || [], files: got.files || [], more: !!got.more }));
      }
    } catch (err) {
      if (asked.current.get(path) === n) setRead(path, (held) => ({ ...held, loading: false, error: err.message || String(err) }));
    }
  }
  const wanted = unread(rows, reads);
  useEffect(() => { for (const path of wanted) read(path); }, [wanted.join("\n")]); // eslint-disable-line react-hooks/exhaustive-deps

  const open = (path, on) => setExpanded((was) => {
    if (was.has(path) === on) return was;
    const next = new Set(was);
    if (on) next.add(path); else next.delete(path);
    return next;
  });
  // A press on a folder: chosen and opened; on the folder chosen, opened or
  // closed. On a failed read's note, its Try again.
  function press(row) {
    if (row.kind === "folder") {
      if (row.id !== value) { onChange(row.id); open(row.id, true); }
      else open(row.id, !row.expanded);
    } else if (row.failed) read(row.id);
  }
  function refresh() {
    setReads(new Map());
    setError("");
  }

  function startNaming() {
    open(value, true);
    field.current = true;
    setNaming({ parent: value, text: "", busy: false });
    setError("");
  }
  // `keys`: ended by Enter or Escape, so the keys go on in the tree, on
  // `path`'s row; a blur leaves the focus where it went.
  function stopNaming(keys = false, path = naming?.parent ?? "") {
    field.current = false;
    setNaming(null);
    setError("");
    if (keys) reveal.current = { key: `folder:${path}`, focus: true };
  }
  async function commitName(keys) {
    if (!field.current) return;
    const { parent, text } = naming;
    const name = cleanName(text);
    if (!name) { stopNaming(keys); return; }
    const problem = nameProblem(name);
    if (problem) { setError(problem); return; }
    const path = joinPath(parent, name);
    const draw = (at) => setAdded((was) => (was.includes(at) ? was : [...was, at]));
    if (rows.some((r) => r.kind === "folder" && r.id === path) || !source.create) {
      draw(path);
      stopNaming(keys, path);
      onChange(path);
      return;
    }
    field.current = false;
    setNaming({ ...naming, busy: true });
    onBusy?.(true);
    try {
      const made = (await source.create(path)) || path;
      // drawn at once, and made once the folders above it that were read
      // are read again (the folder it is in may have been made with it)
      draw(made);
      for (const above of ["", ...pathsTo(parent)]) if (reads.get(above)?.loaded) read(above);
      stopNaming(keys, made);
      if (chosen.current === parent) onChange(made);
    } catch (err) {
      field.current = true;
      setError(err.message || String(err));
      setNaming((was) => (was ? { ...was, busy: false } : was));
    } finally {
      onBusy?.(false);
    }
  }

  const keys = useTree({
    rows, active, setActive, selectedId: value, menuOf: () => null,
    act: (kind, row) => {
      if (kind === "toggle") open(row.id, !row.expanded);
      else if (kind === "select" && row.kind === "folder") onChange(row.id);
      else if (kind === "open") press(row);
    },
  });

  useLayoutEffect(() => {
    const { key, focus } = reveal.current;
    const el = key && keys.rowRefs.current.get(key);
    if (!el) return;
    reveal.current = { key: "", focus: false };
    if (focus) keys.focusRow(key);
    el.scrollIntoView({ block: "nearest" });
  });

  const Icon = source.icon;
  const crumbs = ["", ...pathsTo(value)];
  return (
    <div className="folderBrowser">
      <div className="folderBrowserBar">
        <nav className="folderBrowserPath" aria-label={t("Folder chosen")} title={[source.name, ...(value ? value.split("/") : [])].join(" / ")}>
          {crumbs.map((path, i) => (
            <span key={path || "/"} className="folderBrowserCrumb">
              {i ? <span className="crumbSep" aria-hidden="true">/</span> : null}
              <button type="button" className="crumbBtn" disabled={path === value}
                onClick={() => onChange(path)}>{i ? path.slice(path.lastIndexOf("/") + 1) : source.name}</button>
            </span>
          ))}
        </nav>
        <button type="button" className="uiBtn sm" disabled={!!naming} onClick={startNaming}>
          <FolderPlusIcon size={16} /> {t("New folder")}
        </button>
        <button type="button" className="ctlBtn" title={t("Refresh")} aria-label={t("Refresh")} onClick={refresh}>
          <RefreshIcon size={16} />
        </button>
      </div>
      <div className="folderBrowserTree" aria-label={label || t("Folders")} {...keys.treeProps}>
        {rows.map((row) => {
          const own = keys.rowProps(row);
          if (row.kind === "note") return <TreeRow key={row.key} row={row} {...own} onPress={press} />;
          if (row.kind === "field") {
            return (
              <TreeRow key={row.key} row={row} {...own} icon={<FileIcon dir size={16} />} name={
                <input className="fileTreeName fileRowRename" autoFocus aria-label={t("New folder's name")} placeholder={t("New folder")}
                  value={naming.text} disabled={naming.busy} spellCheck={false}
                  onChange={(e) => setNaming({ ...naming, text: e.target.value })}
                  onBlur={() => commitName(false)}
                  onKeyDown={(e) => {
                    if (composing(e)) return;
                    if (e.key === "Enter") { e.preventDefault(); commitName(true); }
                    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); stopNaming(true); }
                  }} />
              } />
            );
          }
          if (row.kind === "file") {
            return (
              <TreeRow key={row.key} row={row} {...own} className="folderBrowserFile" title={`${row.name}${row.modified ? ` · ${formatFullDate(row.modified)}` : ""}`}
                after={<span className="folderBrowserMeta">{[row.size != null ? fmtBytes(row.size) : "", formatShortDate(row.modified)].filter(Boolean).join(" · ")}</span>} />
            );
          }
          return (
            <TreeRow key={row.key} row={row} {...own} selected={row.id === value} title={row.name} onClick={() => press(row)}
              icon={row.id === "" && Icon ? <Icon size={16} /> : null}
              after={row.made ? null : <span className="uiTag" title={t("Not made yet: it is made when something is saved in it.")}>{t("new")}</span>} />
          );
        })}
      </div>
      {error ? <FormNotice>{error}</FormNotice> : null}
    </div>
  );
}

// The browser in a dialog of its own, for a form whose folder is one field
// among others (the bucket's prefix): Choose hands the folder chosen to
// onChoose(path), once no new folder is being made. `title`, `hint` the
// line under it; `source` and `value` FolderBrowser's.
export function FolderBrowserDialog({ title, hint, source, value = "", onChoose, onClose }) {
  const [path, setPath] = useState(value);
  const [busy, setBusy] = useState(false);
  return (
    <SubDialog title={title} onClose={onClose} className="folderBrowserDialog">
      {hint ? <p className="settingsPaneHint">{hint}</p> : null}
      <FolderBrowser source={source} value={path} onChange={setPath} onBusy={setBusy} label={title} />
      <DialogButtons>
        <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
        <button type="button" className="uiBtn primary" disabled={busy} onClick={() => onChoose(path)}>{t("Choose")}</button>
      </DialogButtons>
    </SubDialog>
  );
}
