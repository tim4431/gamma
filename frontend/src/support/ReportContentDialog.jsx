// The report dialog (reportContent.js): what is reported, quoted; why, one
// of the server's reasons; anything the reporter wants to add; Send. The
// report goes to this server's admins, and on a server that signs in with
// Gamma Cloud to Gamma's developer too, without leaving the app.
import React, { useState } from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Field, SubDialog } from "../settings/SettingsKit";
import { t } from "../shared/i18n/i18n.js";
import { MAX_DETAILS, REPORT_REASONS, reportAuthor, reportBody, reportPreview, reportTitle } from "./reportContent.js";

export default function ReportContentDialog({ target, onClose, setStatus }) {
  const [reason, setReason] = useState("");
  const [details, setDetails] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const preview = reportPreview(target);
  const author = reportAuthor(target);

  async function send() {
    if (!reason || busy) return;
    setBusy(true);
    setError("");
    try {
      await apiJson(`${API}/reports`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reportBody(target, reason, details)),
      });
      setStatus?.(t("Report sent. Thank you for telling us."));
      onClose();
    } catch (err) {
      setError(err.status === 429 ? t("Too many reports for now. Try again in a few minutes.") : err.message);
      setBusy(false);
    }
  }

  return (
    <SubDialog title={reportTitle(target)} onClose={onClose} className="reportContent" draft={details}>
      {preview ? (
        <blockquote className="reportQuote">
          {preview}
          {author ? <span className="reportQuoteBy">{author}</span> : null}
        </blockquote>
      ) : null}
      <Field label={t("What is wrong with it?")} group>
        <div className="reportReasons" role="radiogroup">
          {REPORT_REASONS.map(([value, label]) => (
            <label key={value} className="reportReason">
              <input type="radio" name="reportReason" value={value} checked={reason === value}
                onChange={() => setReason(value)} />
              <span>{t(label)}</span>
            </label>
          ))}
        </div>
      </Field>
      <Field label={t("Anything to add")} hint={t("Optional")}>
        <textarea className="reportProblemText" rows={3} maxLength={MAX_DETAILS} value={details}
          onChange={(e) => setDetails(e.target.value)} />
      </Field>
      <div className="reportModalHint">
        {t("The report, with what it is about, goes to the people who run this server, and to Gamma's developer when this server uses Gamma Cloud.")}
      </div>
      {error ? <div className="reportModalHint aiKeysError" role="alert">{error}</div> : null}
      <div className="reportModalBtns">
        <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
        <button type="button" className="uiBtn primary" disabled={!reason || busy} onClick={send}>
          {busy ? t("Sending…") : t("Send report")}
        </button>
      </div>
    </SubDialog>
  );
}
