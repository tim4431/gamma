import React, { useEffect, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import * as store from "./inkStore.js";

export default function InkConflicts({ pageId, onKeepBoth }) {
  const [, refresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => store.subscribe(() => refresh((v) => v + 1)), []);
  const pending = store.conflicts().filter((d) => d.pageId === pageId);
  const noRecovery = store.recoveryUnavailable() && store.dirtyDrafts().some((d) => d.pageId === pageId);
  if (!pending.length && !noRecovery) return null;
  return <div className="inkConflicts" role="alert">
    {noRecovery && <span>{t("Browser recovery storage is unavailable. Keep this tab open until your handwriting saves.")}</span>}
    {pending.length > 0 && <>
    <span>{t("This handwriting changed on another device. Your drawing is still here.")}</span>
    <button className="uiBtn" disabled={busy} onClick={async () => {
      setBusy(true); setError("");
      try { for (const d of pending) await onKeepBoth(d); }
      catch (e) { setError(e.message); }
      finally { setBusy(false); }
    }}>{t("Keep both versions")}</button>
    <button className="uiBtn" disabled={busy} onClick={() => pending.forEach((d) => store.discardDraft(d.key))}>
      {t("Use saved version")}
    </button>
    </>}
    {error ? <span>{error}</span> : null}
  </div>;
}
