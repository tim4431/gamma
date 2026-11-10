// Edit the file: a synced vault's notes file edited whole, as a person's
// own editor would (GET and PUT /api/folder-links/page/{id}/file,
// folder_links.read_note / write_note). The text is the file's bytes, not
// Gamma's blocks: a save writes the file, and the page takes it at its
// vault's next round, as it takes any edit made on disk. A save names the
// hash the editor read; a file changed since answers 409 with what it holds
// now, and the dialog offers its new text or a save over it. Loaded on
// first use (App's lazySurface): the editor is a plain CodeMirror view.
import React from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { API, apiJson } from "../shared/lib/utils";
import { t } from "../shared/i18n/i18n.js";
import { CheckIcon, FileTextIcon } from "../shared/ui/Icons";
import { DialogButtons, Empty, SubDialog } from "../settings/SettingsKit";
import "./vault.css";

const fileUrl = (pageId) => `${API}/folder-links/page/${encodeURIComponent(pageId)}/file`; // i18n-ignore: a URL

// `pageId`: the page whose notes file it is; `onSaved()` after a write.
export default function FileSourceDialog({ pageId, onClose, onSaved }) {
  const [file, setFile] = React.useState(null);     // {text, hash, path} as read
  const [error, setError] = React.useState("");
  const [dirty, setDirty] = React.useState(false);
  const [moved, setMoved] = React.useState(null);   // {hash}: the file changed on disk since it was read
  const [saving, setSaving] = React.useState(false);
  const host = React.useRef(null);
  const view = React.useRef(null);
  const base = React.useRef("");

  const load = React.useCallback(async () => {
    setError("");
    try {
      const found = await apiJson(fileUrl(pageId));
      base.current = found.text;
      setFile(found);
      setMoved(null);
      setDirty(false);
      const v = view.current;
      if (v) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: found.text } });
    } catch (err) {
      setError(err.message);
    }
  }, [pageId]);
  React.useEffect(() => { load(); }, [load]);

  // The editor, once the text is in: undo, the standard keys, lines wrapped.
  const ready = !!file;
  React.useEffect(() => {
    if (!ready || !host.current) return undefined;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: base.current,
        extensions: [
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": t("The file's text") }),
          EditorView.updateListener.of((u) => { if (u.docChanged) setDirty(u.state.doc.toString() !== base.current); }),
        ],
      }),
    });
    view.current = v;
    v.focus();
    return () => { v.destroy(); view.current = null; };
  }, [ready]);

  async function save(hash) {
    const v = view.current;
    if (!v || saving) return;
    setSaving(true);
    setError("");
    const text = v.state.doc.toString();
    try {
      await apiJson(fileUrl(pageId), {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, hash }),
      });
      setDirty(false);
      onSaved?.();
      onClose();
    } catch (err) {
      if (err?.status === 409 && err.data?.hash) setMoved({ hash: err.data.hash });
      else setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <SubDialog title={t("Edit the file")} onClose={onClose} draft={{ dirty }} className="fileSourceModal">
      {file ? <div className="popoverHint fileSourcePath" title={file.path}>{file.path}</div> : null}
      {!file && !error ? <Empty icon={FileTextIcon} loading>{t("Loading…")}</Empty> : null}
      {file ? <div className="fileSourceEditor" ref={host} /> : null}
      {moved ? (
        <div className="popoverHint fileSourceMoved" role="alert">
          {t("The file changed on this computer since it was opened here. Load its new text, losing these edits, or save over it.")}
        </div>
      ) : null}
      <DialogButtons footnote={error || null}>
        <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
        {moved ? (
          <>
            <button type="button" className="uiBtn" disabled={saving} onClick={load}>{t("Load its new text")}</button>
            <button type="button" className="uiBtn primary danger" disabled={saving} onClick={() => save(moved.hash)}>{t("Save over it")}</button>
          </>
        ) : (
          <button type="button" className="uiBtn primary" disabled={!file || !dirty || saving} onClick={() => save(file.hash)}>
            <CheckIcon size={14} /> {t("Save")}
          </button>
        )}
      </DialogButtons>
    </SubDialog>
  );
}
