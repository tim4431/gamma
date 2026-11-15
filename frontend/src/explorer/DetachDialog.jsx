// Detach, the one way a synced folder leaves Gamma (docs/dev/work_folders.md
// "Detaching a work folder"): its files and the bytes keeping them stores,
// the two choices, keep the files in Gamma as pages or remove them from
// Gamma to Recently deleted, and the line that the directory on the
// computer stays as it is. It reads GET /api/folders/{id}/detach again as it
// opens (the plan App's menu read shows meanwhile) and posts the choice
// there, on the page's own server whatever keeps the folder. A lazy chunk;
// App opens it from the folder's menu and the vault chip
// (useWorkFolderActions.jsx). Its words: detach.js.
import React, { useEffect, useState } from "react";
import { FormNotice, SubDialog } from "../settings/SettingsKit";
import { T, t } from "../shared/i18n/i18n.js";
import { API, apiJson } from "../shared/lib/utils";
import { detachText } from "./detach.js";
import "./explorer.css";

const CHOICES = [["keep", T("Keep the files in Gamma")], ["remove", T("Remove them from Gamma")]];

// `folderId` and `name`: the synced folder's root; `plan`: what its menu
// read; `onDone(pages, answer)` once it is detached, `onClose()` to leave.
export default function DetachDialog({ folderId, name, plan: first, onClose, onDone }) {
  const [plan, setPlan] = useState(first || null);
  const [pages, setPages] = useState("keep");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const url = `${API}/folders/${encodeURIComponent(folderId)}/detach`;
  useEffect(() => {
    let live = true;
    apiJson(url).then((read) => { if (live) setPlan(read); }, (err) => { if (live) setError(err.message || String(err)); });
    return () => { live = false; };
  }, [url]);
  const text = detachText(plan, name);

  async function detach() {
    setBusy(true);
    setError("");
    try {
      const answer = await apiJson(url, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pages }),
      });
      onDone(pages, answer);
    } catch (err) {
      setError(t("Could not detach the synced folder: {message}", { message: err.message || err }));
      setBusy(false);
    }
  }

  return (
    <SubDialog title={text.title} onClose={onClose} className="detachDialog">
      <div className="reportModalHint">{text.counts}</div>
      <div className="reportReasons detachChoices" role="radiogroup" aria-label={t("What becomes of its files")}>
        {CHOICES.map(([value, label]) => (
          <label key={value} className="reportReason">
            <input type="radio" name="detachPages" value={value} checked={pages === value} disabled={busy}
              onChange={() => setPages(value)} />
            <span className="detachChoice">
              <span>{t(label)}</span>
              <span className="confirmMessage">{value === "keep" ? text.keep : text.remove}</span>
            </span>
          </label>
        ))}
      </div>
      <div className="reportModalHint">{text.disk}</div>
      {text.offline ? <FormNotice tone="warn">{text.offline}</FormNotice> : null}
      {error ? <FormNotice>{error}</FormNotice> : null}
      <div className="reportModalBtns">
        <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
        <button type="button" className={`uiBtn primary${pages === "remove" ? " danger" : ""}`}
          disabled={busy || plan?.may === false} onClick={detach}>{busy ? t("Detaching…") : t("Detach")}</button>
      </div>
    </SubDialog>
  );
}
