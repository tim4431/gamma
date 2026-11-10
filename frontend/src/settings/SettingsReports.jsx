// Settings → Server → Reports (admins; backend gamma/reports.py,
// /api/admin/reports): the comments, shared pages and AI replies people
// reported here (support/ReportContentDialog.jsx), the open ones first. A row
// says why and what, quoting the text, who wrote it, the page, who reported
// it and when, and whether it went on to Gamma Cloud too; Resolve (or
// Reopen) and Delete. A notice tells an admin a new one came
// (gamma/notices.py "reports").
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Row, Segmented } from "./SettingsKit";
import { useSettingsRead } from "./settingsReads.js";
import { formatRelativeTime } from "../library/libraryUtils";
import { CheckIcon, FlagIcon, RefreshIcon, Trash2Icon } from "../shared/ui/Icons";
import { REPORT_REASONS } from "../support/reportContent.js";
import { T, t, tn } from "../shared/i18n/i18n.js";

const KIND = { comment: T("Comment"), page: T("Page"), ai: T("AI reply") };
const REASON = Object.fromEntries(REPORT_REASONS);
const SHOWN = [["open", T("Open")], ["resolved", T("Resolved")]];

// What a report is about and who made it, the line under its reason.
function reportLine(r, forwards, now) {
  const about = [
    r.author ? (r.kind === "ai" ? t("written by {model}", { model: r.author }) : t("by {name}", { name: r.author })) : "",
    r.title ? t("on “{title}”", { title: r.title }) : "",
  ].filter(Boolean).join(" ");
  const who = r.reporter ? t("reported by {name}", { name: r.reporter }) : t("reported by a visitor without an account");
  const sent = !forwards ? "" : r.sent_at ? t("sent to Gamma Cloud") : t("not yet sent to Gamma Cloud");
  return [about, who, formatRelativeTime(r.created_at, now), sent].filter(Boolean).join(" · ");
}

export function ContentReports({ setStatus, confirm }) {
  const reports = useSettingsRead("/admin/reports");
  const [shown, setShown] = React.useState("open");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const data = reports.data;
  const all = data?.reports || [];
  const list = all.filter((r) => r.state === shown);
  async function act(r, state) {
    setBusy(true); setError("");
    try {
      if (state) {
        await apiJson(`${API}/admin/reports/${encodeURIComponent(r.id)}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state }) });
      } else {
        await apiJson(`${API}/admin/reports/${encodeURIComponent(r.id)}`, { method: "DELETE" });
      }
      await reports.reload();
      setStatus?.(state === "resolved" ? t("Report resolved.") : state === "open" ? t("Report reopened.") : t("Report deleted."));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  const remove = (r) => confirm({
    title: T("Delete report"), message: t("Delete this report? It cannot be brought back."),
    confirmLabel: t("Delete"), danger: true, onConfirm: () => act(r, ""),
  });
  const errorLine = (error || (!data && reports.error))
    ? <p className="settingsPaneHint aiKeysError" role="alert">{error || reports.error}</p> : null;
  if (!data) return errorLine;
  const now = Date.now();
  return <>
    <Row icon={FlagIcon} label={t("Reports")}
      hint={data.open ? tn("{n} report is open", "{n} reports are open", data.open) : t("No open reports")}
      title={data.forwards
        ? t("Comments, shared pages and AI replies people reported from their menus. This server also sends each report to Gamma Cloud, which tells Gamma's developer.")
        : t("Comments, shared pages and AI replies people reported from their menus.")}>
      {all.length ? <Segmented value={shown} onChange={setShown} options={SHOWN} /> : null}
    </Row>
    {list.map((r) => (
      <div key={r.id} className="aiProvRow" data-report={r.id}>
        <span className="aiProvAvatar"><FlagIcon size={16} /></span>
        <span className="aiProvMeta">
          <span className="aiProvName">
            {t(REASON[r.reason] || T("Something else"))}
            <span className="uiTag">{t(KIND[r.kind] || KIND.page)}</span>
          </span>
          {r.content ? <span className="aiProvDesc">“{r.content.slice(0, 400)}”</span> : null}
          {r.details ? <span className="aiProvDesc">{t("Their words: {details}", { details: r.details })}</span> : null}
          <span className="aiProvDesc">{reportLine(r, data.forwards, now)}</span>
        </span>
        <span className="aiProvActions">
          {r.state === "open" ? (
            <button className="uiBtn sm iconSq" disabled={busy} onClick={() => act(r, "resolved")}
              title={t("Resolve: dealt with, or nothing to do")} aria-label={t("Resolve")}>
              <CheckIcon size={16} />
            </button>
          ) : (
            <button className="uiBtn sm iconSq" disabled={busy} onClick={() => act(r, "open")}
              title={t("Reopen")} aria-label={t("Reopen")}>
              <RefreshIcon size={16} />
            </button>
          )}
          <button className="uiBtn sm iconSq" disabled={busy} onClick={() => remove(r)}
            title={t("Delete the report")} aria-label={t("Delete the report")}>
            <Trash2Icon size={16} />
          </button>
        </span>
      </div>
    ))}
    {errorLine}
  </>;
}
