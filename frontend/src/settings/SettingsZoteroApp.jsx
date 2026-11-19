// Settings → Server › Zotero sign-in: this server's own Zotero app, which an
// admin registers at zotero.org so that people connect their Zotero account
// by signing in there (routers/zotero.py `server` mode) rather than through
// Gamma Cloud's broker or a pasted key. GET/PUT/DELETE /api/admin/zotero-app
// answer {client_key, callback_url}, the URL to register the app with ("" while
// no public URL is confirmed); the secret is write-only (gamma/zotero_oauth.py
// `app`). docs/dev/zotero_sync.md.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { CopyField, DialogButtons, Field, FormNotice, PasswordInput, Row, Section, SubDialog } from "./SettingsKit";
import { useSettingsRead } from "./settingsReads.js";
import { ExternalLinkIcon, KeyIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { T, t } from "../shared/i18n/i18n.js";

export const ZOTERO_APP_READ = "/admin/zotero-app";
const ENDPOINT = `${API}${ZOTERO_APP_READ}`;
const APPS = "https://www.zotero.org/oauth/apps";
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });

// Where to register the app, the callback URL to give it, its key and its
// secret (empty keeps the saved one, for the same key), and Remove….
function AppDialog({ app, confirm, onSaved, onRemoved, onClose }) {
  const saved = !!app.client_key;
  const [draft, setDraft] = React.useState({ key: app.client_key, secret: "" });
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function save(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try { onSaved(await apiJson(ENDPOINT, json("PUT", { client_key: draft.key.trim(), client_secret: draft.secret.trim() }))); }
    catch (err) { setError(err.message); setBusy(false); }
  }
  function remove() {
    confirm({
      title: T("Remove the Zotero app"),
      message: t("Remove this server's Zotero app? Accounts already connected stay connected; new ones sign in through Gamma Cloud, if this server uses it, or paste a key."),
      confirmLabel: t("Remove"), danger: true,
      onConfirm: async () => {
        try { onRemoved(await apiJson(ENDPOINT, json("DELETE"))); }
        catch (err) { setError(err.message); }
      },
    });
  }
  const field = (key, label) => ({
    "aria-label": label, value: draft[key], spellCheck: false, autoComplete: key === "secret" ? "new-password" : "off",
    onChange: (event) => setDraft((d) => ({ ...d, [key]: event.target.value })),
  });
  return (
    <SubDialog title={saved ? t("Edit the Zotero app") : t("Set up a Zotero app")} onClose={onClose} draft={draft}>
      <form className="settingsForm" noValidate onSubmit={save}>
        <ol className="oauthInstructions">
          <li>
            {t("Signed in to zotero.org, register a new application: any name, the type Browser, and the callback URL below.")}{" "}
            <a className="aiKeyLink" href={APPS} target="_blank" rel="noopener noreferrer">{t("Open")}<ExternalLinkIcon size={14} /></a>
          </li>
          <li>{t("Paste the client key and secret Zotero shows.")}</li>
        </ol>
        {app.callback_url ? (
          <Field group label={t("Callback URL")}>
            <CopyField label={t("Callback URL")} value={app.callback_url} action={t("Copy the callback URL")} rows={1} />
          </Field>
        ) : <FormNotice tone="info">{t("Signing in at zotero.org needs this server's public URL: confirm it under Assistant connections first.")}</FormNotice>}
        <Field label={t("Client key")}>
          <input className="aiKeyInput" autoFocus={!saved} {...field("key", t("Client key"))} />
        </Field>
        <Field label={t("Client secret")} hint={t("Stored on the server, never shown to the browser again.")}>
          <PasswordInput placeholder={saved ? t("secret set — type to replace") : undefined} {...field("secret", t("Client secret"))} />
        </Field>
        {error ? <FormNotice>{error}</FormNotice> : null}
        <DialogButtons>
          {saved ? <button type="button" className="uiBtn danger" onClick={remove}>{t("Remove…")}</button> : null}
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={busy || !draft.key.trim() || (!saved && !draft.secret.trim())}>
            <MorphText>{busy ? t("Saving…") : t("Save")}</MorphText>
          </button>
        </DialogButtons>
      </form>
    </SubDialog>
  );
}

export function ZoteroAppSettings({ setStatus, confirm }) {
  const read = useSettingsRead(ZOTERO_APP_READ);
  const app = read.data; // GET's {client_key, callback_url}
  const [editing, setEditing] = React.useState(false);
  let row = null;
  if (app) {
    row = (
      <Row icon={KeyIcon} label={app.client_key ? t("This server's Zotero app") : t("Not set up")}
        hint={app.client_key ? t("{key} · people connect Zotero by signing in at zotero.org", { key: app.client_key })
          : t("An app registered at zotero.org lets people connect Zotero by signing in there. Without one they sign in through Gamma Cloud, if this server uses it, or paste a key.")}>
        <button className="uiBtn sm" onClick={() => setEditing(true)}>{app.client_key ? t("Edit…") : t("Set up…")}</button>
      </Row>
    );
  } else if (read.error) {
    row = (
      <FormNotice>{read.error}{" "}
        <button type="button" className="uiBtn sm" onClick={() => read.reload().catch(() => {})}>{t("Try again")}</button>
      </FormNotice>
    );
  }
  return <>
    <Section title={t("Zotero sign-in")}>{row}</Section>
    {editing ? (
      <AppDialog app={app} confirm={confirm} onClose={() => setEditing(false)}
        onSaved={(value) => { read.set(value); setEditing(false); setStatus?.(t("Zotero app saved.")); }}
        onRemoved={(value) => { read.set(value); setEditing(false); }} />
    ) : null}
  </>;
}
