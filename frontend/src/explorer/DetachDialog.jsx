// Stop syncing, the one way a synced folder leaves Gamma (docs/dev/
// work_folders.md "Detaching a work folder"): what it does, its files and
// what keeping them stores, the two choices, keep the files in Gamma as
// pages or remove them from Gamma to Recently deleted, and the line that
// the directory on the computer stays as it is. It reads GET
// /api/folders/{id}/detach again as it opens (the plan App's menu read
// shows meanwhile; a failed read says so with Try again, and Stop syncing
// waits for a read that succeeds) and posts the choice there, on the page's
// own server whatever keeps the folder. A lazy chunk; App opens it from the
// folder's menu and the vault chip (useWorkFolderActions.jsx). Its words:
// detach.js.
import React, { useEffect, useState } from "react";
import { FormNotice, SubDialog } from "../settings/SettingsKit";
import { T, t } from "../shared/i18n/i18n.js";
import { API, apiJson } from "../shared/lib/utils";
import { MorphText } from "../shared/ui/MotionWidgets";
import { detachRefusal, detachText } from "./detach.js";
import "./explorer.css";

const CHOICES = [["keep", T("Keep the files in Gamma")], ["remove", T("Remove them from Gamma")]];

// `folderId` and `name`: the synced folder's root; `plan`: what its menu
// read; `onDone(pages, answer)` once it is detached, `onClose()` to leave.
export default function DetachDialog({ folderId, name, plan: first, onClose, onDone }) {
  const [plan, setPlan] = useState(first || null);
  const [readFailed, setReadFailed] = useState(false);
  const [reads, setReads] = useState(0);
  const [pages, setPages] = useState("keep");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const url = `${API}/folders/${encodeURIComponent(folderId)}/detach`;
  useEffect(() => {
    let live = true;
    apiJson(url).then((read) => { if (live) { setPlan(read); setReadFailed(false); } }, () => { if (live) setReadFailed(true); });
    return () => { live = false; };
  }, [url, reads]);
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
      setError(t("Could not stop syncing the folder: {message}", { message: err.message || err }));
      setBusy(false);
    }
  }
  const refused = plan?.may === false;

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
      {readFailed ? (
        <FormNotice>{t("Could not read the folder.")}{" "}
          <button type="button" className="uiBtn sm" onClick={() => setReads((n) => n + 1)}>{t("Try again")}</button>
        </FormNotice>
      ) : refused ? <FormNotice tone="warn">{detachRefusal(plan)}</FormNotice> : null}
      {error ? <FormNotice>{error}</FormNotice> : null}
      <div className="reportModalBtns">
        <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
        <button type="button" className={`uiBtn primary${pages === "remove" ? " danger" : ""}`}
          disabled={busy || readFailed || !plan || refused} onClick={detach}>
          <MorphText>{busy ? t("Stopping…") : t("Stop syncing")}</MorphText>
        </button>
      </div>
    </SubDialog>
  );
}
