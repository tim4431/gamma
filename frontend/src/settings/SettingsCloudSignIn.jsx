// Sign in with Gamma Cloud (backend gamma/cloud_auth.py, docs/dev/cloud_accounts.md):
// - CloudSignInSettings — Settings → Server → Sign-in (admins): the account
//   server's address, the client this server is, and what happens to a cloud
//   identity this server has not seen (refuse / claim / provision), and
//   under provision whether it accepts published pages (the share host).
// - CloudIdentityRow — Settings → Account: the signed-in account's own link
//   to its cloud account (link = a round trip through the account server,
//   unlink = one call; refused for an account that has no password).
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Row, Segmented, PasswordInput, SettingsSyncContext, Toggle, useSettingsDraft } from "./SettingsKit";
import { cloudSyncHint } from "./syncState.js";
import { CloudIcon, ExternalLinkIcon, GlobeIcon, KeyIcon, UserIcon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

const POLICIES = [
  ["refuse", "Refuse", null, "Only accounts already linked to a cloud account can sign in"],
  ["claim", "Claim", null, "A cloud account whose username equals an unlinked username here signs in as it"],
  ["provision", "Provision", null, "Any verified cloud account gets an account here, named after its username"],
];

// `action` receives the Save button (present while the draft is dirty) so
// the Server pane can put it on its Sign-in section rule.
export function CloudSignInSettings({ setStatus, action }) {
  const [saved, setSaved] = React.useState(null); // the `cloud` object of /api/admin/settings
  const [draft, setDraft] = React.useState({ issuer: "", client_id: "", secret: "", policy: "refuse", share_host: false });
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const fromSaved = (c) => ({ issuer: c.issuer || "", client_id: c.client_id === "gamma-desktop" ? "" : (c.client_id || ""),
    secret: "", policy: c.policy || "refuse", share_host: !!c.share_host });
  React.useEffect(() => {
    let active = true;
    apiJson(`${API}/admin/settings`).then((v) => {
      if (active) { setSaved(v.cloud); setDraft(fromSaved(v.cloud)); }
    }).catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);
  const managed = saved?.source === "environment";
  const dirty = !!saved && !managed && JSON.stringify(draft) !== JSON.stringify(fromSaved(saved));
  const discard = () => { setDraft(fromSaved(saved)); setError(""); };
  useSettingsDraft("cloud-sign-in", dirty, discard);
  async function save() {
    if (!saved || managed || busy) return;
    setBusy(true); setError("");
    try {
      const body = { cloud_issuer: draft.issuer.trim(), cloud_client_id: draft.client_id.trim(), cloud_policy: draft.policy,
        cloud_share_host: draft.policy === "provision" && draft.share_host };
      if (draft.secret) body.cloud_client_secret = draft.secret;
      const value = await apiJson(`${API}/admin/settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      setSaved(value.cloud); setDraft(fromSaved(value.cloud));
      setStatus(value.cloud.enabled ? "Cloud sign-in saved. The login page now offers it." : "Cloud sign-in turned off.");
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  const set = (key) => (value) => setDraft((d) => ({ ...d, [key]: value }));
  const disabled = !saved || managed || busy;
  React.useEffect(() => {
    action?.(dirty ? <button className="uiBtn sm primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save sign-in"}</button> : null);
    return () => action?.(null);
  }, [dirty, busy, draft]); // eslint-disable-line react-hooks/exhaustive-deps
  return <>
    <Row icon={CloudIcon} label={t("Account server")}
      hint={managed ? t("Set by the server's environment") : t("The Gamma Cloud address people sign in through; empty turns it off")}
      title={t("Sign in with Gamma Cloud: this server becomes an OpenID Connect client of the account server. Its login page gets a second button.")}>
      <span className="setRowControls">
        {saved ? <span className={`uiTag ${saved.enabled ? "ok" : ""}`}>{saved.enabled ? "on" : "off"}</span> : null}
        <input className="aiKeyInput" type="url" aria-label={t("Account server")} value={draft.issuer} spellCheck={false}
          autoComplete="off" name="gamma-cloud-issuer" disabled={disabled} placeholder="https://account.gammapdf.com" onChange={(e) => set("issuer")(e.target.value)} />
      </span>
    </Row>
    <Row icon={KeyIcon} label={t("Server client")}
      hint={t("How this server identifies itself to the account server. Leave empty on your own machine; a hosted server enters the client id and secret it was given")}
      title={t("Not a person: the OpenID Connect client this Gamma is. Empty = the account server's built-in public desktop client (loopback callback, PKCE only). A server the account server provisioned was handed a confidential client id and secret at creation.")}>
      <span className="setRowControls">
        <input className="aiKeyInput" type="text" aria-label={t("Client id")} value={draft.client_id} spellCheck={false}
          autoComplete="off" name="gamma-cloud-client-id" disabled={disabled} placeholder={t("empty = desktop client")}
          onChange={(e) => set("client_id")(e.target.value)} />
        {draft.client_id.trim() || saved?.has_secret ? (
          <PasswordInput aria-label={t("Client secret")} value={draft.secret} disabled={disabled} autoComplete="new-password"
            name="gamma-cloud-client-secret" placeholder={saved?.has_secret ? t("secret set — type to replace") : t("client secret")}
            onChange={(e) => set("secret")(e.target.value)} />
        ) : null}
      </span>
    </Row>
    <Row icon={UserIcon} label={t("Unknown cloud accounts")}
      hint={t("What a cloud account that is not linked to an account here may do")}
      title={t("Refuse: only linked accounts. Claim: a cloud username equal to an unlinked username here takes it over — for a server whose accounts were created under cloud usernames. Provision: every verified cloud account gets an account — the free share host.")}>
      <Segmented value={draft.policy} onChange={set("policy")} options={POLICIES} disabled={disabled} />
    </Row>
    {draft.policy === "provision" ? (
      <Toggle icon={GlobeIcon} label={t("Accept published pages")} checked={draft.share_host} onChange={set("share_host")}
        disabled={disabled} hint={t("This server is the share host people publish pages to")}
        title={t("The free share host: a Gamma Cloud account may publish pages here from its own Gamma. Also turns off the guest account and limits the account list to exact names.")} />
    ) : null}
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </>;
}

export function CloudIdentityRow({ setStatus, confirm }) {
  const [state, setState] = React.useState(null); // {identity, enabled}
  const [error, setError] = React.useState("");
  const sync = React.useContext(SettingsSyncContext);
  const syncHint = cloudSyncHint(sync?.cloud);
  const load = React.useCallback(() => {
    apiJson(`${API}/auth/cloud/status`).then(setState).catch((err) => setError(err.message));
  }, []);
  React.useEffect(() => { load(); }, [load]);
  if (!state || !state.enabled) return null;
  const id = state.identity;
  const here = window.location.pathname + window.location.search;
  const link = () => { window.location.assign(`${API}/auth/cloud/start?link=1&next=${encodeURIComponent(here)}`); };
  async function doUnlink() {
    try {
      await apiJson(`${API}/auth/cloud/unlink`, { method: "POST" });
      setStatus?.("Gamma Cloud account unlinked.");
      load();
    } catch (err) { setError(err.message); }
  }
  function unlink() {
    if (!confirm) { doUnlink(); return; }
    confirm({ title: T("Unlink Gamma Cloud"), message: t("This account will no longer sign in as \"{username}\". You can link it again any time.", { username: id.username }),
      confirmLabel: "Unlink", onConfirm: doUnlink });
  }
  return <>
    <Row icon={CloudIcon} label={t("Gamma Cloud")}
      hint={id ? `${id.username}${id.email ? ` · ${id.email}` : ""}${id.plan ? ` · ${id.plan} plan` : ""}${syncHint ? ` · ${syncHint}` : ""}`
        : "Sign in here with your Gamma Cloud account"}
      title={id ? `Linked ${id.linked_at ? id.linked_at.slice(0, 10) : ""}. Signing in with this cloud account opens this account.`
        : "Link your Gamma Cloud account: you are sent to the account server and back, then either login opens this account."}>
      <span className="setRowControls">
        {id ? <span className="uiTag ok">linked</span> : null}
        {id && state.issuer ? (
          <a className="uiBtn sm" href={`${state.issuer}/`} target="_blank" rel="noopener"
            title={t("Your Gamma Cloud account: plan, devices, sign-in methods")}>
            <ExternalLinkIcon size={14} /> Open account
          </a>) : null}
        {id ? <button className="uiBtn sm" onClick={unlink}>{t("Unlink")}</button>
            : <button className="uiBtn sm primary" onClick={link}>{t("Link Gamma Cloud account")}</button>}
      </span>
    </Row>
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </>;
}
