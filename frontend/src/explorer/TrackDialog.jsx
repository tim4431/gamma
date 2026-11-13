// Choose files…: what a work folder tracks of its directory
// (docs/dev/work_folders.md "The tracking dialog"), written as its
// .gammaignore when saved. The directory is read from the vault's server a
// directory at a time (GET /api/folder-links/{id}/tree, by the link's id:
// the page never names a path), as the import review's tree
// (transfers/ImportTree.jsx) with its folders read as they open. The
// choice is a base and the changes made on it (explorer/trackSelection.js),
// so a folder not read yet is ticked or unticked whole; the summary under
// the tree is the server's preview of the choice, asked 400 ms after the
// last change, which says what would leave, which files are too large to
// keep (listed without their bytes) and what the file would read. Every
// file ticked is a page, whatever its format (gamma/vault/kinds.py).
// `link`: the vault as GET /api/folder-links/{id} answers it; `fresh`: the
// vault is still choosing its first files (made from the shell, which
// opened this page with ?track=), so Cancel removes it (`onCancelled`).
// `onSaved()` follows a Save, whose round runs in the background.
import React from "react";
import { t, tn } from "../shared/i18n/i18n.js";
import { API, apiJson } from "../shared/lib/utils";
import { fmtBytes } from "../shared/lib/format.js";
import { SubDialog, Toggle } from "../settings/SettingsKit";
import ImportTree from "../transfers/ImportTree.jsx";
import { formatLabel } from "../library/libraryUtils";
import { body, boxOf, initial, isSelected, ofFormat, toggle, toggleAll, toggleFormat } from "./trackSelection.js";
import "../transfers/importReview.css";
import "./explorer.css";

// The format filters, which tick (or untick, when every file of theirs is
// ticked) the files of their format anywhere in the directory.
const FILTERS = [
  ["pdf", () => t("PDFs")],
  ["markdown", () => t("Notes")],
  ["latex", () => t("LaTeX sources")],
  ["image", () => t("Pictures")],
];
const PREVIEW_MS = 400;

const dirKey = (path) => String(path || "").replace(/\/+$/, "");
const leaf = (path) => dirKey(path).split("/").pop();
// Why a row is never tracked, whatever is chosen (gamma/vault/disk.py's fixed rules).
const NEVER = {
  dot: () => t("never tracked: a dot folder or file"),
  node_modules: () => t("never tracked: node_modules"),
  link: () => t("never tracked: a link"),
  name: () => t("never tracked: a name Windows refuses"),
  case: () => t("never tracked: its name differs from another's in case only"),
  encoding: () => t("never tracked: its name cannot be read"),
};

// What a row says after its size.
function hintOf(e) {
  return [
    e.never ? NEVER[e.never]?.() || t("never tracked") : "",
    e.git_ignored ? t("git ignores it") : "",
    e.locked === "followed" ? t("a file vault's PDF, taken in") : "",
  ].filter(Boolean).join(" · ");
}

// The tree ImportTree draws, from the entries read so far (by path): each
// directory a node carrying its own entry (`path`, `walked`, …) with its
// folders by name and its files, which are the entries themselves, a file
// the preview joins to its page's other file (`joined`: path → that file)
// saying so.
function buildTree(entries, reading, joined) {
  const root = { path: "", directory: true, walked: true, folders: new Map(), files: [] };
  const dirs = new Map([["", root]]);
  const dirOf = (path) => {
    const key = dirKey(path);
    if (dirs.has(key)) return dirs.get(key);
    const parent = dirOf(key.includes("/") ? key.slice(0, key.lastIndexOf("/")) : "");
    const node = { path: `${key}/`, directory: true, walked: true, folders: new Map(), files: [] };
    parent.folders.set(leaf(key), node);
    dirs.set(key, node);
    return node;
  };
  for (const e of [...entries.values()].sort((a, b) => a.path.localeCompare(b.path))) {
    if (e.directory) {
      const node = dirOf(e.path);
      Object.assign(node, e, { path: `${dirKey(e.path)}/`, hint: hintOf(e), reading: reading.has(dirKey(e.path)) });
    } else {
      const parent = dirOf(e.path.includes("/") ? e.path.slice(0, e.path.lastIndexOf("/")) : "");
      const hint = joined.has(e.path) ? [hintOf(e), t("kept with {file}: a page's two files go together", { file: leaf(joined.get(e.path)) })]
        .filter(Boolean).join(" · ") : hintOf(e);
      parent.files.push({ ...e, name: leaf(e.path), hint, chip: formatLabel(e) });
    }
  }
  return root;
}

export default function TrackDialog({ link, fresh, onSaved, onCancelled, onClose, setStatus }) {
  const base = fresh ? "preset" : "file";
  const [state, setState] = React.useState(() => initial(base));
  const [read, setRead] = React.useState(null); // {rules, capped, entries: Map} | {error}
  const [reading, setReading] = React.useState(() => new Set());
  const [preview, setPreview] = React.useState(null);
  const [library, setLibrary] = React.useState(fresh ? false : !!link.library);
  const [saving, setSaving] = React.useState(false);
  const [problem, setProblem] = React.useState(null); // {text, again?}
  // the footer's Cancel of a fresh vault, which removes it, asked about
  const [asking, setAsking] = React.useState(false);
  const url = `${API}/folder-links/${encodeURIComponent(link.id)}`;

  const readDir = React.useCallback(async (dir = "") => {
    const q = new URLSearchParams({ base });
    if (dir) q.set("dir", dir);
    return apiJson(`${url}/tree?${q}`);
  }, [url, base]);
  // Each read of the whole directory, so that a folder's rows read against
  // an earlier one are dropped (Read it again while one was being read).
  const generation = React.useRef(0);
  const readAll = React.useCallback(async () => {
    const n = ++generation.current;
    setRead(null);
    setPreview(null);
    setProblem(null);
    try {
      const d = await readDir();
      if (n === generation.current) {
        setRead({ rules: d.rules || {}, capped: !!d.capped, entries: new Map((d.entries || []).map((e) => [dirKey(e.path), e])) });
      }
    } catch (err) {
      if (n === generation.current) setRead({ error: err.message || String(err) });
    }
  }, [readDir]);
  React.useEffect(() => { readAll(); }, [readAll]);

  // A folder not read yet, opened: its rows are read and joined in.
  const openFolder = async (path) => {
    const key = dirKey(path);
    const entry = read?.entries?.get(key);
    if (!entry || entry.never || reading.has(key)) return;
    const n = generation.current;
    setReading((r) => new Set(r).add(key));
    try {
      const d = await readDir(`${key}/`);
      setRead((r) => {
        if (n !== generation.current || !r?.entries) return r;
        const entries = new Map(r.entries);
        for (const e of d.entries || []) entries.set(dirKey(e.path), e);
        entries.set(key, { ...entries.get(key), walked: true });
        return { ...r, entries };
      });
    } catch (err) {
      setStatus?.(t("Could not read {path}: {message}", { path: key, message: err.message || err }));
    } finally {
      setReading((r) => { const next = new Set(r); next.delete(key); return next; });
    }
  };

  const changed = state.changes.length > 0 || (!fresh && library !== !!link.library);
  const selection = body(state);
  const selectionKey = JSON.stringify(selection);
  // The preview pending or in flight: stopped before a fresh vault's Cancel
  // removes the link it would ask about.
  const previewRef = React.useRef(null); // {timer, ctl}
  const stopPreview = () => {
    const p = previewRef.current;
    previewRef.current = null;
    if (p) { clearTimeout(p.timer); p.ctl.abort(); }
  };
  React.useEffect(() => {
    if (!read?.entries) return undefined;
    const ctl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const d = await apiJson(`${url}/selection/preview`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selection }), signal: ctl.signal,
        });
        if (!ctl.signal.aborted) setPreview({ ...d, key: selectionKey });
      } catch (err) {
        if (!ctl.signal.aborted) setPreview({ error: err.message || String(err), key: selectionKey });
      }
    }, PREVIEW_MS);
    previewRef.current = { timer, ctl };
    return () => { clearTimeout(timer); ctl.abort(); if (previewRef.current?.ctl === ctl) previewRef.current = null; };
  }, [selectionKey, !!read?.entries]); // eslint-disable-line react-hooks/exhaustive-deps
  // Save confirms what leaves (the round's guard), so only once the summary
  // says what this choice, not an earlier one, comes to.
  const current = preview && !preview.error && preview.key === selectionKey ? preview : null;

  const entries = read?.entries || new Map();
  // The files the server keeps beside their page's other file (a paper's
  // PDF and its notes go together): ticked, and not to be unticked alone.
  const joined = React.useMemo(() => new Map((current?.joined || []).map((j) => [j.path, j.with])), [current]);
  const tree = React.useMemo(() => buildTree(entries, reading, joined), [entries, reading, joined]);
  const files = [...entries.values()].filter((e) => !e.directory && !e.never);
  const formatOn = (format) => {
    const of = files.filter((e) => ofFormat(e, format));
    return of.length > 0 && of.every((e) => isSelected(state, e));
  };
  const box = (node) => (!node.directory && joined.has(node.path)
    ? { checked: true, indeterminate: false, disabled: true } : boxOf(state, node, entries));

  async function save() {
    setSaving(true);
    setProblem(null);
    try {
      await apiJson(`${url}/selection`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selection, base_hash: read.rules.hash || "", library }),
      });
      onSaved();
    } catch (err) {
      if (err?.status === 409) setProblem({ text: t("The .gammaignore changed on disk since it was read."), again: true });
      else setProblem({ text: err.message || String(err) });
    } finally {
      setSaving(false);
    }
  }
  async function cancelFresh() {
    stopPreview();
    try {
      await apiJson(url, { method: "DELETE" });
    } catch (err) {
      setStatus?.(t("Could not cancel the work folder: {message}", { message: err.message || err }));
    }
    onCancelled();
  }
  // A fresh vault's Cancel removes it, so it asks first, from the footer as
  // from Escape or the ×; Choose files… asks once something changed.
  const close = fresh ? cancelFresh : onClose;
  const guard = fresh ? { question: t("Cancel making this work folder?"), keep: t("Keep choosing"), leave: t("Cancel it") }
    : changed ? { question: t("Leave without saving?"), keep: t("Keep choosing"), leave: t("Leave") } : null;
  const draft = fresh ? undefined : { changes: state.changes, library };
  const name = link.dest ? leaf(link.dest.replace(/\\/g, "/")) : link.path ? leaf(link.path.replace(/\\/g, "/")) : "";
  const leaving = current?.leaving || [];
  const kept = leaving.filter((p) => p.notes || p.open_comments);

  return (
    <SubDialog title={t("Choose files")} onClose={close} guard={guard} draft={draft} className="trackDialog">
      <div className="importReviewHeader">
        <strong>{name}</strong>
        <p>{fresh ? t("Pick what this work folder tracks: each file ticked becomes a page, and the rest stay on disk only.")
          : t("Pick what this work folder tracks. A file unticked leaves Gamma for Recently deleted; it stays on disk.")}</p>
      </div>
      {read?.error ? <p role="alert" className="importWarning">{read.error}</p> : null}
      {!read ? <p className="reportModalHint">{t("Reading…")}</p> : null}
      {read?.entries ? <>
        <div className="importSelectionToolbar">
          <div className="trackFilters" role="group" aria-label={t("Tick by format")}>
            {FILTERS.map(([format, label]) => {
              const on = formatOn(format);
              return (
                <button key={format} type="button" className={`uiBtn sm ${on ? "on" : ""}`} aria-pressed={on}
                  onClick={() => setState((s) => toggleFormat(s, format, !on))}>{label()}</button>
              );
            })}
          </div>
          <div className="importSelectionActions">
            <button type="button" className="uiBtn sm" onClick={() => setState((s) => toggleAll(s, entries.values(), true))}>{t("Select all")}</button>
            <button type="button" className="uiBtn sm" onClick={() => setState((s) => toggleAll(s, entries.values(), false))}>{t("Deselect all")}</button>
          </div>
        </div>
        <div className="importTreeScroll trackTree">
          <ImportTree node={tree} boxOf={box} onOpenFolder={openFolder}
            onSelect={(node, on) => setState((s) => toggle(s, node, on))} />
        </div>
        {read.capped ? <p className="reportModalHint">{t("The directory is large: folders not read yet are ticked or unticked whole.")}</p> : null}
        <div className="trackSummary" role="status">
          {current ? <>
            <p>{tn("{n} file, {size}", "{n} files, {size}", current.files, { size: fmtBytes(current.bytes || 0) })}</p>
            {joined.size ? (
              <details>
                <summary>{tn("{n} file kept with its page's other file", "{n} files kept with their page's other file", joined.size)}</summary>
                <ul>{[...joined].map(([path, other]) => (
                  <li key={path}><strong>{path}</strong> <span>{t("with {file}", { file: other })}</span></li>
                ))}</ul>
              </details>
            ) : null}
            {leaving.length ? (
              <details>
                <summary>{tn("{k} leaves: {j} with notes or open comments", "{k} leave: {j} with notes or open comments",
                  leaving.length, { k: leaving.length, j: kept.length })}</summary>
                <ul>{leaving.map((p) => (
                  <li key={p.page}><strong>{p.title || p.path}</strong>{" "}
                    <span>{[p.path, p.notes ? tn("{n} note", "{n} notes", p.notes) : "",
                      p.open_comments ? tn("{n} open comment", "{n} open comments", p.open_comments) : ""].filter(Boolean).join(" · ")}</span></li>
                ))}</ul>
              </details>
            ) : null}
            {current.large ? <p>{tn("{n} chosen file is over {size} and is listed without its bytes",
              "{n} chosen files are over {size} and are listed without their bytes",
              current.large, { size: fmtBytes(current.keep_bytes || 0) })}</p> : null}
            {current.over_cap ? <p className="importWarning">{t("A work folder tracks at most {cap} files: untick some to save.", { cap: current.cap })}</p> : null}
            {read.rules.exists && !current.rules_ours ? <p>{t("Your .gammaignore will be rewritten.")}</p> : null}
          </> : preview?.error && preview.key === selectionKey ? <p className="importWarning">{preview.error}</p> : <p>{t("Counting…")}</p>}
        </div>
        <div className="trackLibrary">
          <Toggle label={t("Add its PDFs and notes to the library")}
            hint={t("Each new file's page joins the library too; files already tracked keep their place")}
            checked={library} onChange={setLibrary} />
        </div>
        <p className="reportModalHint">{t("The AI chat sends a tracked file's text to the model provider when the file is in its context, as it sends a PDF's.")}</p>
      </> : null}
      {problem ? (
        <p role="alert" className="importWarning">{problem.text}{" "}
          {problem.again ? <button type="button" className="uiBtn sm" onClick={readAll}>{t("Read it again")}</button> : null}
        </p>
      ) : null}
      {asking ? (
        <div className="settingsUnsaved" role="alertdialog" aria-label={guard.question}>
          <span>{guard.question}</span>
          <span className="settingsUnsavedBtns">
            <button type="button" className="uiBtn" autoFocus onClick={() => setAsking(false)}>{guard.keep}</button>
            <button type="button" className="uiBtn danger" onClick={cancelFresh}>{guard.leave}</button>
          </span>
        </div>
      ) : (
        <div className="reportModalBtns importReviewFooter">
          <button type="button" className="uiBtn" onClick={fresh ? () => setAsking(true) : onClose}>{t("Cancel")}</button>
          <button type="button" className="uiBtn primary" disabled={!read?.entries || !current || saving || !!current.over_cap}
            title={!current && read?.entries ? t("Save once the summary says what this choice comes to") : undefined}
            onClick={save}>{t("Save")}</button>
        </div>
      )}
    </SubDialog>
  );
}
