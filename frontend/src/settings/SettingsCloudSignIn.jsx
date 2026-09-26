// Sign in with Gamma Cloud (backend gamma/cloud_auth.py, docs/dev/cloud_accounts.md):
// - CloudSignInSettings — Settings → Server → Sign-in (admins): the account
//   server's address, the client this server is, and what happens to a cloud
//   identity this server has not seen (refuse / claim / provision), and
//   under provision whether it accepts published pages (the share host). A
//   server with a public URL still on the desktop client (`needs_connect`)
//   gets a Connect button: a round trip through the account server that
//   brings back its own client id and secret (`?cloud_connect=ok` /
//   `?cloud_connect_error=` on return, shown once).
// - CloudIdentityRow — Settings → Account & sync: the signed-in account's own link
//   to its cloud account (link = a round trip through the account server,
//   unlink = one call; refused for an account that has no password; no Link
//   button until an admin connected the server), and under it, once linked,
//   CloudSyncRow: the settings sync by hand.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { Row, Segmented, PasswordInput, SettingsSyncContext, Toggle, useSettingsDraft } from "./SettingsKit";
import { cloudSyncHint } from "./syncState.js";
import { REOPEN_SETTINGS_KEY } from "./settingsNavigation.js";
import { CloudIcon, ExternalLinkIcon, GlobeIcon, KeyIcon, RefreshIcon, UserIcon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

const POLICIES = [
  ["refuse", t("Refuse"), null, t("Only accounts already linked to a cloud account can sign in")],
  ["claim", t("Claim"), null, t("A cloud account whose username equals an unlinked username here signs in as it")],
  ["provision", t("Provision"), null, t("Any verified cloud account gets an account here, named after its username")],
];

// The connect round trip's outcome, read once from the address bar and dropped.
function takeConnectResult() {
  try {
    const url = new URL(window.location.href);
    const ok = url.searchParams.get("cloud_connect") === "ok";
    const error = url.searchParams.get("cloud_connect_error") || "";
    if (!ok && !error) return null;
    url.searchParams.delete("cloud_connect");
    url.searchParams.delete("cloud_connect_error");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    return { ok, error };
  } catch { return null; }
}

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
    const result = takeConnectResult();
    if (result?.ok) setStatus(t("This server is connected to Gamma Cloud. People can sign in with it now."));
    else if (result) setError(result.error);
    return () => { active = false; };
  }, []);
  const managed = saved?.source === "environment";
  const connect = () => {
    try { sessionStorage.setItem(REOPEN_SETTINGS_KEY, "server"); } catch {}
    const here = window.location.pathname + window.location.search;
    window.location.assign(`${API}/auth/cloud/connect/start?next=${encodeURIComponent(here)}`);
  };
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
      setStatus(value.cloud.enabled ? t("Cloud sign-in saved. The login page now offers it.") : t("Cloud sign-in turned off."));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  const set = (key) => (value) => setDraft((d) => ({ ...d, [key]: value }));
  const disabled = !saved || managed || busy;
  React.useEffect(() => {
    action?.(dirty ? <button className="uiBtn sm primary" disabled={busy} onClick={save}>{busy ? t("Saving…") : t("Save sign-in")}</button> : null);
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
      hint={saved?.needs_connect && !dirty
        ? t("This server has a public address, so it needs a client of its own: connect it once through Gamma Cloud")
        : t("How this server identifies itself to the account server. Leave empty on your own machine; a hosted server enters the client id and secret it was given")}
      title={t("Not a person: the OpenID Connect client this Gamma is. Empty = the account server's built-in public desktop client (loopback callback, PKCE only). Connect asks Gamma Cloud for a client of this server's own; you approve it there with your account.")}>
      <span className="setRowControls">
        {saved?.needs_connect && !dirty ? (
          <button className="uiBtn sm primary" onClick={connect}
            title={t("Go to Gamma Cloud, approve this server, and come back with its client id and secret")}>
            <CloudIcon size={14} /> {t("Connect")}
          </button>
        ) : null}
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
  const load = React.useCallback(() => {
    apiJson(`${API}/auth/cloud/status`).then(setState).catch((err) => setError(err.message));
  }, []);
  React.useEffect(() => { load(); }, [load]);
  if (!state || !state.enabled) return null;
  const id = state.identity;
  const waiting = !id && state.connected === false;
  const here = window.location.pathname + window.location.search;
  const link = () => { window.location.assign(`${API}/auth/cloud/start?link=1&next=${encodeURIComponent(here)}`); };
  async function doUnlink() {
    try {
      await apiJson(`${API}/auth/cloud/unlink`, { method: "POST" });
      setStatus?.(t("Gamma Cloud account unlinked."));
      load();
    } catch (err) { setError(err.message); }
  }
  function unlink() {
    if (!confirm) { doUnlink(); return; }
    confirm({ title: T("Unlink Gamma Cloud"), message: t("This account will no longer sign in as \"{username}\". You can link it again any time.", { username: id.username }),
      confirmLabel: t("Unlink"), onConfirm: doUnlink });
  }
  return <>
    <Row icon={CloudIcon} label={t("Gamma Cloud")}
      hint={id ? `${id.username}${id.email ? ` · ${id.email}` : ""}${id.plan ? ` · ${id.plan} plan` : ""}`
        : waiting ? t("An admin connects this server to Gamma Cloud first, in Settings → Server → Sign-in")
        : t("Sign in here with your Gamma Cloud account")}
      title={id ? t("Linked {linked_at}. Signing in with this cloud account opens this account.", { linked_at: id.linked_at ? id.linked_at.slice(0, 10) : "" })
        : t("Link your Gamma Cloud account: you are sent to the account server and back, then either login opens this account.")}>
      <span className="setRowControls">
        {id ? <span className="uiTag ok">{t("linked")}</span> : null}
        {id && state.issuer ? (
          <a className="uiBtn sm" href={`${state.issuer}/`} target="_blank" rel="noopener"
            title={t("Your Gamma Cloud account: plan, devices, sign-in methods")}>
            <ExternalLinkIcon size={14} /> {t("Open account")}
          </a>) : null}
        {id ? <button className="uiBtn sm" onClick={unlink}>{t("Unlink")}</button>
            : waiting ? null : <button className="uiBtn sm primary" onClick={link}>{t("Link Gamma Cloud account")}</button>}
      </span>
    </Row>
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
    {id ? <CloudSyncRow setStatus={setStatus} confirm={confirm} /> : null}
  </>;
}

const SYNC_OUTCOMES = {
  pulled: T("Settings updated from Gamma Cloud."),
  pushed: T("Settings sent to Gamma Cloud."),
  merged: T("Settings merged with Gamma Cloud."),
  same: T("Settings already in sync with Gamma Cloud."),
};

// The account's settings against Gamma Cloud by hand (backend
// gamma/cloud_sync.py, POST /api/auth/cloud/sync): one Sync now button, the
// merge both ways. Only a conflict — a first sync that found two different
// copies, state "choose" — asks more: a dialog offering Fetch from cloud
// (the cloud's copy replaces this server's) or Push to cloud (the other way
// round), opened once when the row finds that state, and again by Sync now
// while it lasts.
function CloudSyncRow({ setStatus, confirm }) {
  const sync = React.useContext(SettingsSyncContext);
  const [busy, setBusy] = React.useState("");
  const [error, setError] = React.useState("");
  const profile = sync?.cloud?.profile;
  const choosing = profile ? profile.state === "choose" : !!sync?.local?.cloudChoice;
  async function run(action) {
    setBusy(action); setError("");
    try {
      await sync?.local?.flush?.();
      const d = await apiJson(`${API}/auth/cloud/sync`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }),
      });
      await sync?.local?.reload?.();
      if (d.outcome === "choose") ask();
      else if (SYNC_OUTCOMES[d.outcome]) setStatus?.(t(SYNC_OUTCOMES[d.outcome]));
    } catch (err) { setError(err.message); }
    finally { setBusy(""); sync?.refresh?.(); }
  }
  const ask = () => confirm?.({
    title: T("Settings differ from Gamma Cloud"),
    message: T("This server and Gamma Cloud hold different settings. Fetch the cloud's copy to replace this server's, or push this server's to replace the cloud's? Your other servers take a push at their next sync."),
    confirmLabel: T("Fetch from cloud"), onConfirm: () => run("fetch"),
    altLabel: t("Push to cloud"), onAlt: () => run("push"),
  });
  const asked = React.useRef(false);
  React.useEffect(() => {
    if (!choosing) { asked.current = false; return; }
    if (asked.current) return;
    asked.current = true;
    ask();
  }, [choosing]);
  const off = profile?.state === "off";
  return <>
    <Row icon={RefreshIcon} label={t("Settings sync")}
      hint={choosing ? t("This server and Gamma Cloud hold different settings. Choose which to keep.") : cloudSyncHint(sync?.cloud)}
      title={t("Your account's settings (appearance, reading, editing, chat, shortcuts) follow you to every server you sign in to with Gamma Cloud. AI keys stay on each server.")}>
      {off ? null : (
        <span className="setRowControls">
          <button className={`uiBtn sm ${choosing ? "primary" : ""}`} disabled={!!busy} onClick={() => (choosing ? ask() : run("sync"))}
            title={choosing ? t("Choose whether the cloud's settings or this server's are kept")
              : t("Merge with Gamma Cloud now: each side keeps what the other did not change")}>
            {busy ? { sync: t("Syncing…"), fetch: t("Fetching…"), push: t("Pushing…") }[busy] : t("Sync now")}
          </button>
        </span>
      )}
    </Row>
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </>;
}
