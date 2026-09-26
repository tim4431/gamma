// Settings → Server → Guests (admins): how long a guest's throwaway account
// and workspace last, and demo mode (the login page leads with Try the demo,
// the guide offers the first-run tour on arrival). Both live in the server
// `settings` table through /api/admin/settings; an environment variable
// (GAMMA_GUEST_TTL_HOURS, GAMMA_DEMO) overrides the saved value, and the row
// is then read-only. docs/dev/guests.md.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Row, Toggle, UnitInput } from "./SettingsKit";
import { ClockIcon, GlobeIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

const envHint = (name) => t("Set by {name} in the server's environment", { name });

export function GuestSettings({ setStatus }) {
  const [saved, setSaved] = React.useState(null);
  const [error, setError] = React.useState("");
  React.useEffect(() => {
    let active = true;
    apiJson(`${API}/admin/settings`).then((value) => { if (active) setSaved(value); })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);
  // Only the changed field: another box committing meanwhile keeps its value.
  // A switch shows its new state at once and flips back if the save fails.
  async function save(patch, message) {
    setError("");
    const before = saved;
    setSaved((prev) => ({ ...prev, ...patch }));
    try {
      setSaved(await apiJson(`${API}/admin/settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
      }));
      setStatus?.(message);
    } catch (err) {
      setSaved(before);
      setError(t("Could not save: {message}", { message: err.message }));
    }
  }
  async function commitTtl(raw) {
    const n = Number.parseInt(String(raw).trim(), 10);
    if (!Number.isFinite(n) || n < 1 || n === saved.guest_ttl_hours) return; // the box shows the stored value again
    await save({ guest_ttl_hours: n }, t("Guest workspaces now last {n} h.", { n }));
  }
  if (!saved) return error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p>
    : <p className="setNotice">{t("Loading…")}</p>;
  const ttlManaged = saved.guest_ttl_source === "environment";
  const demoManaged = saved.demo_mode_source === "environment";
  return <>
    <Row icon={ClockIcon} label={t("Guest workspaces last")}
      hint={ttlManaged ? envHint("GAMMA_GUEST_TTL_HOURS") : t("Then the guest's account and workspace are deleted")}
      title={t("Every guest login makes a fresh throwaway account with its own workspace. It is deleted this many hours after it was made, whether or not the visitor comes back.")}>
      <UnitInput unit="h" min={1} label={t("Guest workspaces last")} value={String(saved.guest_ttl_hours ?? "")}
        disabled={ttlManaged} onCommit={commitTtl} />
    </Row>
    <Toggle icon={GlobeIcon} label={t("Demo mode")} checked={!!saved.demo_mode} disabled={demoManaged}
      hint={demoManaged ? envHint("GAMMA_DEMO") : t("The login page leads with Try the demo")}
      title={t("For a public try-it server: the login page offers Try the demo (a guest login) first and folds the password form behind Admin sign-in, and a guest is offered the first-paper tour on arrival. Guests, their expiry and the AI allowance work the same either way.")}
      onChange={(on) => save({ demo_mode: on }, on ? t("Demo mode on.") : t("Demo mode off."))} />
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </>;
}
