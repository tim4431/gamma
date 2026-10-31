// Settings → Account & sync → Zotero (backend gamma/routers/zotero.py,
// docs/dev/zotero_sync.md): the account's connection to a zotero.org
// account, which the Zotero links read (Workspaces → Clones and Zotero
// links → Link a Zotero library…). Connected, the row names the Zotero user and what the key may
// do, asks Zotero whether the key still works (the libraries it reaches)
// and offers to connect again, which replaces the key, and Disconnect,
// which pauses the links. Not connected, it offers the OAuth round trip
// where the server can broker one (its own Zotero app or Gamma Cloud's):
// zotero.org opens in a new tab and the row asks the server every few
// seconds until the key has arrived there, which works in a browser and in
// the desktop and iPad shells alike, whose navigation guards send other
// origins to the system browser. A key made by hand on zotero.org and
// pasted always works. Guests keep no keys, so the row is not drawn for
// them. The link dialog connects with the same pieces (useZoteroAccount,
// useZoteroSignIn, ZoteroKeyDialog), so connecting there never leaves it.
import React from "react";
import { guideEvents } from "../guide/events.js";
import { Field, PasswordInput, Row, Step, SubDialog } from "./SettingsKit";
import { useSettingsRead } from "./settingsReads.js";
import { ZOTERO_ACCOUNT, connectZoteroKey, disconnectZotero, startZoteroOAuth, zoteroKeyRefusal,
  zoteroOAuthStatus } from "../transfers/zoteroApi";
import { composing } from "../chat/enterKey.js";
import { BookIcon, ExternalLinkIcon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

const NEW_KEY_URL = "https://www.zotero.org/settings/keys/new";
const POLL_MS = 2500;
// The server forgets a sign-in nobody finished after ten minutes.
const WAIT_LIMIT_MS = 10 * 60 * 1000;

// What the key may do, in the words of Zotero's key page.
function accessHint(username, access = {}) {
  const groups = { write: t("Groups"), read: t("Groups: read only"), some: t("Some groups") }[access.groups];
  return [username, access.library && t("Read"), access.notes && t("Notes"), access.write && t("Write"), access.files && t("Files"), groups]
    .filter(Boolean).join(" · ");
}

// GET /api/zotero/account through the dialog's read store, shared by the
// Account row and the link dialog: {account (null while loading), error,
// set, reload}. `poll` reads it again every so many ms (the dialog, while
// it waits for a connection made elsewhere).
export function useZoteroAccount({ poll = 0 } = {}) {
  const { data, error, set, reload } = useSettingsRead(ZOTERO_ACCOUNT, { poll });
  return { account: data, error, set, reload };
}

// The OAuth round trip: `connect()` opens zotero.org in a new tab and asks
// the server until the sign-in is done, refused or forgotten; a 4xx answer
// will not change on asking again, anything else (a dropped connection, a
// restarting server) is asked again on the next tick. `onConnected(account)`
// hears the account the key connected, `onError(message)` why it did not.
// {waiting: {state, url, since} | null, starting, connect, cancel}.
export function useZoteroSignIn({ onConnected, onError }) {
  const [waiting, setWaiting] = React.useState(null);
  const [starting, setStarting] = React.useState(false); // one sign-in tab per press
  const hear = React.useRef({ onConnected, onError });
  hear.current = { onConnected, onError };
  React.useEffect(() => {
    if (!waiting) return undefined;
    let stopped = false;
    let timer = 0;
    const stop = (message) => { setWaiting(null); if (message) hear.current.onError?.(message); };
    const ask = async () => {
      try {
        const answer = await zoteroOAuthStatus(waiting.state);
        if (stopped) return;
        if (answer.state === "connected") {
          stop("");
          hear.current.onConnected?.(answer.account || null);
          return;
        }
        if (answer.state === "error") { stop(answer.error || t("Zotero did not connect.")); return; }
      } catch (err) {
        if (stopped) return;
        if (err.status >= 400 && err.status < 500) { stop(err.message); return; }
      }
      if (Date.now() - waiting.since > WAIT_LIMIT_MS) { stop(t("The Zotero sign-in timed out. Connect again to retry.")); return; }
      timer = setTimeout(ask, POLL_MS);
    };
    timer = setTimeout(ask, POLL_MS);
    return () => { stopped = true; clearTimeout(timer); };
  }, [waiting]);
  async function connect() {
    hear.current.onError?.("");
    setStarting(true);
    try {
      const { state, url } = await startZoteroOAuth();
      window.open(url, "_blank", "noopener");
      setWaiting({ state, url, since: Date.now() });
    } catch (err) { hear.current.onError?.(err.message); } finally { setStarting(false); }
  }
  return { waiting, starting, connect, cancel: () => setWaiting(null) };
}

// While a sign-in waits: the tag, zotero.org again (its tab may not have
// opened), and Cancel.
export function SignInWaiting({ waiting, onCancel }) {
  return <>
    <span className="uiTag pending">{t("waiting")}</span>
    <a className="uiBtn sm" href={waiting.url} target="_blank" rel="noopener"
      title={t("Open the zotero.org sign-in again, if its tab did not open")}>
      <ExternalLinkIcon size={14} /> {t("Open zotero.org")}
    </a>
    <button className="uiBtn sm" onClick={onCancel}>{t("Cancel")}</button>
  </>;
}

export function ZoteroAccountRow({ setStatus, confirm }) {
  const { account, error: readError, set, reload } = useZoteroAccount();
  const [error, setError] = React.useState("");
  const [keyOpen, setKeyOpen] = React.useState(false);
  const [dead, setDead] = React.useState(""); // why Zotero no longer takes the stored key, "" while it does
  function connected(next) {
    setKeyOpen(false);
    if (next) set((previous) => ({ ...previous, ...next, connected: true }));
    else reload().catch(() => {});
    setStatus?.(t("Zotero connected."));
    guideEvents.emit("zotero.connected");
  }
  const signIn = useZoteroSignIn({ onConnected: connected, onError: setError });

  // The stored pref says "connected" whatever Zotero thinks of the key: a
  // key revoked on zotero.org, or one this server can no longer unseal,
  // shows only when Zotero is asked, and the row then says so.
  const connection = account?.connected && account.can_edit ? `${account.connected_at || ""}|${account.hint || ""}` : "";
  React.useEffect(() => {
    setDead("");
    if (!connection) return undefined;
    let live = true;
    zoteroKeyRefusal().then((reason) => { if (live) setDead(reason); });
    return () => { live = false; };
  }, [connection]);

  if (!account) return readError ? <p className="settingsPaneHint aiKeysError" role="alert">{readError}</p> : null;
  if (!account.can_edit) return null;

  async function doDisconnect() {
    try {
      await disconnectZotero();
      setStatus?.(t("Zotero disconnected."));
      set((previous) => ({ ...previous, connected: false }));
    } catch (err) { setError(err.message); }
  }
  function disconnect() {
    setError("");
    if (!confirm) { doDisconnect(); return; }
    confirm({ title: T("Disconnect Zotero"),
      message: t("Gamma forgets its key to the Zotero account \"{username}\". Your Zotero links pause until you connect again; nothing is deleted.", { username: account.username }),
      confirmLabel: t("Disconnect"), onConfirm: doDisconnect });
  }

  const oauth = Boolean(account.oauth?.available);
  const since = account.connected_at ? account.connected_at.slice(0, 10) : "";
  const useKey = (primary) => <button className={`uiBtn sm${primary ? " primary" : ""}`}
    onClick={() => { setError(""); setKeyOpen(true); }}>{t("Use an API key…")}</button>;
  return <>
    <Row icon={BookIcon} label={t("Zotero")}
      hint={signIn.waiting ? t("Finish signing in on zotero.org; this row updates by itself.")
        : account.connected ? (dead ? t(dead) : accessHint(account.username, account.access))
        : t("Link a Zotero library to a workspace and keep the two in step")}
      title={account.connected
        ? `${account.via === "oauth" ? t("Connected {date} by signing in at zotero.org.", { date: since })
          : t("Connected {date} with the API key {hint}.", { date: since, hint: account.hint || "" })} ${t("Link a library to a workspace in Workspaces › Clones and Zotero links.")}`
        : t("Connect your zotero.org account by signing in there or with an API key you make there. The key stays on this server.")}>
      <span className="setRowControls">
        {signIn.waiting ? <SignInWaiting waiting={signIn.waiting} onCancel={signIn.cancel} />
          : account.connected ? <>
          {/* Connecting again replaces the key (the server revokes a key it
              made), which mends a dead key or one without the access a
              library needs. */}
          <span className={`uiTag ${dead ? "warn" : "ok"}`}>{dead ? t("key no longer works") : t("connected")}</span>
          {oauth ? <button className={`uiBtn sm${dead ? " primary" : ""}`} disabled={signIn.starting} onClick={signIn.connect}>{t("Connect again")}</button> : null}
          {useKey(dead && !oauth)}
          <button className="uiBtn sm" onClick={disconnect}>{t("Disconnect")}</button>
        </> : <>
          {oauth ? <button className="uiBtn sm primary" disabled={signIn.starting} onClick={signIn.connect}>{t("Connect Zotero")}</button> : null}
          {useKey(!oauth)}
        </>}
      </span>
    </Row>
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
    {keyOpen ? <ZoteroKeyDialog onClose={() => setKeyOpen(false)} onConnected={connected} /> : null}
  </>;
}

// A key made by hand at zotero.org: where, which boxes to tick, then paste.
// The server checks it with Zotero before keeping it, so a key that cannot
// read the personal library is refused here, with Zotero's reason.
export function ZoteroKeyDialog({ onClose, onConnected }) {
  const [key, setKey] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  async function save() {
    const apiKey = key.trim();
    if (busy) return;
    if (!apiKey) { setError(t("Paste the key first.")); return; }
    setBusy(true);
    setError("");
    try {
      onConnected(await connectZoteroKey(apiKey));
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }
  return <SubDialog draft={key} title={t("Connect Zotero with an API key")} onClose={onClose}>
    <div className="settingsForm">
      <Step n={1} title={t("Create a key at zotero.org")} hint={t("Sign in there if it asks.")}>
        <a className="aiKeyLink" href={NEW_KEY_URL} target="_blank" rel="noopener noreferrer">
          {t("Open zotero.org/settings/keys/new")}<ExternalLinkIcon size={14} />
        </a>
      </Step>
      <Step n={2} title={t("Give it access")}
        hint={t("Under Personal Library tick “Allow library access”, “Allow notes access” and “Allow write access”; under Default Group Permissions choose “Read/Write”. Then Save Key.")} />
      <Step n={3} title={t("Paste the key")} hint={t("Kept on this server, never shown to the browser again.")}>
        <Field label={t("API key")}>
          <PasswordInput autoComplete="new-password" spellCheck={false} value={key}
            onChange={(event) => setKey(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && !composing(event)) save(); }} />
        </Field>
      </Step>
      {error ? <div className="settingsPaneHint aiKeysError" role="alert">{error}</div> : null}
      <div className="reportModalBtns">
        <button className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
        <button className="uiBtn primary" disabled={busy} onClick={save}>{busy ? t("Connecting…") : t("Save")}</button>
      </div>
    </div>
  </SubDialog>;
}
