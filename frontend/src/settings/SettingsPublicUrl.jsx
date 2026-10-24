import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Row, useSettingsDraft, useDraftOf } from "./SettingsKit";
import { useSettingsRead } from "./settingsReads.js";
import { LinkIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { t } from "../shared/i18n/i18n.js";

// Settings → Server → Assistant connections: the address assistants reach
// this server at, over /api/admin/settings (the pane's one read of it).
export function PublicUrlSettings({ setStatus }) {
  const { data: saved, error: readError, set: setSaved } = useSettingsRead("/admin/settings");
  const suggested = window.location.origin;
  const initial = (value) => value.public_url || suggested;
  const [draft, setDraft] = useDraftOf(saved, initial, "");
  const [saveError, setError] = React.useState("");
  const error = saveError || (!saved ? readError : "");
  const [busy, setBusy] = React.useState(false);
  const managed = saved?.public_url_source === "environment";
  const dirty = !!saved && !managed && draft !== initial(saved);
  const discard = () => { setDraft(initial(saved)); setError(""); };
  useSettingsDraft("public-server-url", dirty, discard);
  async function save() {
    if (!saved || managed || busy) return;
    setBusy(true); setError("");
    try {
      const value = await apiJson(`${API}/admin/settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ public_url: draft.trim() }),
      });
      setSaved(value); setDraft(initial(value));
      setStatus(value.public_url ? t("Public server URL saved. Assistant connections now use this address.") : t("Public server URL cleared."));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  const confirmed = !!saved?.public_url;
  const needsAction = !!saved && !managed && (dirty || !confirmed) && !!draft.trim();
  return <>
    <Row icon={LinkIcon} label={t("Public server URL")}
      hint={managed ? t("Set by the server's environment") : t("The address assistants use to reach Gamma; HTTPS unless localhost")}
      title={t("Assistant sign-in (MCP OAuth) needs to know the address this server is reached at. Confirm it once; no restart. Changing it later makes assistants connect again.")}>
      <span className="setRowControls">
        {saved && !managed ? (
          <span className={`uiTag ${confirmed && !dirty ? "ok" : ""}`}>{confirmed && !dirty ? "confirmed" : t("not confirmed")}</span>
        ) : null}
        <input className="aiKeyInput" type="url" aria-label={t("Public server URL")} value={draft} spellCheck={false}
          disabled={!saved || managed || busy} placeholder="https://gamma.example.com"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); save(); } }} />
        {needsAction ? (
          <button className="uiBtn sm primary" disabled={busy} onClick={save}><MorphText>{busy ? t("Saving…") : t("Confirm")}</MorphText></button>
        ) : null}
      </span>
    </Row>
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </>;
}
