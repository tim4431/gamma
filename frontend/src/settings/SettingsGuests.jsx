// Settings → Server → Guests (admins): whether this server takes guest
// logins at all, how long a guest's throwaway account and workspace last,
// and demo mode (the login page leads with Try the demo, the guide offers
// the first-run tour on arrival). All three live in the server `settings`
// table through /api/admin/settings; an environment variable
// (GAMMA_GUEST_TTL_HOURS, GAMMA_DEMO) overrides the saved value, and the row
// is then read-only. A server that can take no guest logins (a hosted
// container, a share host, GAMMA_GUEST_MAX=0) has no Guests section — there
// is nothing to switch on. docs/dev/guests.md.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Row, Section, Toggle, UnitInput } from "./SettingsKit";
import { ClockIcon, GlobeIcon, UserIcon } from "../shared/ui/Icons";
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
  if (saved?.guest_logins_available === false) return null;
  if (!saved) return <Section title={t("Guests")}>
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : <p className="setNotice">{t("Loading…")}</p>}
  </Section>;
  const ttlManaged = saved.guest_ttl_source === "environment";
  const demoManaged = saved.demo_mode_source === "environment";
  // The other two rows only describe guests, so they are shown but idle
  // while nobody can sign in as one.
  const guests = !!saved.guest_logins;
  return <Section title={t("Guests")}>
    <Toggle icon={UserIcon} label={t("Guest sign-in")} checked={guests}
      hint={guests ? t("The login page offers a throwaway account") : t("The login page shows no guest button")}
      title={t("A guest login makes a throwaway account with a workspace of its own, which is deleted when it expires. Off, the server takes no new guest; the accounts you made sign in as before, and a guest already here keeps its workspace until it expires.")}
      onChange={(on) => save({ guest_logins: on }, on ? t("Guest sign-in on.") : t("Guest sign-in off."))} />
    <Row icon={ClockIcon} label={t("Guest workspaces last")}
      hint={ttlManaged ? envHint("GAMMA_GUEST_TTL_HOURS") : t("Then the guest's account and workspace are deleted")}
      title={t("Every guest login makes a fresh throwaway account with its own workspace. It is deleted this many hours after it was made, whether or not the visitor comes back.")}>
      <UnitInput unit="h" min={1} label={t("Guest workspaces last")} value={String(saved.guest_ttl_hours ?? "")}
        disabled={ttlManaged || !guests} onCommit={commitTtl} />
    </Row>
    <Toggle icon={GlobeIcon} label={t("Demo mode")} checked={!!saved.demo_mode} disabled={demoManaged || !guests}
      hint={demoManaged ? envHint("GAMMA_DEMO") : t("The login page leads with Try the demo")}
      title={t("For a public try-it server: the login page offers Try the demo (a guest login) first and folds the password form behind Admin sign-in, and a guest is offered the first-paper tour on arrival. Guests, their expiry and the AI allowance work the same either way.")}
      onChange={(on) => save({ demo_mode: on }, on ? t("Demo mode on.") : t("Demo mode off."))} />
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </Section>;
}
