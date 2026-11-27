// Settings → Backups › Backup destinations: a cloud's row (admins only),
// Google Drive or OneDrive alike — the account backup tasks copy their
// snapshots to (BackupTasks.jsx, "Copy off this server"), into one folder
// of it. Connecting is signing in with Gamma's own app and allowing it
// (gamma/cloud_account.py). Where Gamma is opened on its own computer (the
// desktop app; the read's `browser`), Connect… opens the service's page,
// which comes back to the server (PageDialog). Anywhere else it is the
// service's device flow (CodeDialog): Connect… asks the server for a code
// (POST /api/admin/clouds/<store>/connect) as its dialog opens, whose one
// button copies the code and opens the service's page, and while it is
// open polls the server (POST …/poll, at the interval the service gave)
// until the service says yes, no, or the code expires; closing it stops
// waiting (DELETE …/connect). Edit… changes the folder (PUT), chosen in a
// folder browser over the cloud (explorer/FolderBrowser.jsx: GET …/browse,
// New folder POST …/folders), or disconnects (DELETE, which revokes the
// access where the service can; the copies stay). The folder is a path of
// names from the cloud's top ("Backups/Gamma"), shown "Backups › Gamma".
// GET /api/admin/clouds/<store> is the row's read (`status`). What differs
// between the clouds is CLOUDS below; every other sentence names the cloud.
// docs/dev/settings.md.
import React from "react";
import { API, apiJson, jsonInit, copyText } from "../shared/lib/utils";
import { CopyField, DialogButtons, Field, FormNotice, Row, SubDialog, TestResult } from "./SettingsKit";
import { useSettingsRead } from "./settingsReads.js";
import { STORE, folderLabel } from "./backupStores.js";
import { CloudCheckIcon, CloudIcon, CloudOffIcon, ExternalLinkIcon, HardDriveIcon, XIcon } from "../shared/ui/Icons";
import FolderBrowser from "../explorer/FolderBrowser.jsx";
import { MorphText } from "../shared/ui/MotionWidgets";
import { t } from "../shared/i18n/i18n.js";

// What each cloud says of itself: the company signed in to, its top folder,
// the dialog's lead (what is asked for, and what Gamma then sees), the
// folder's hint and the row's hint before it is connected.
const CLOUDS = {
  drive: {
    company: "Google",
    top: () => t("My Drive"),
    lead: () => t("Sign in to Google with the account to back up to, and allow Gamma to keep its backups in your Drive. Gamma sees only the files it makes there."),
    folderHint: () => t("New copies go here; the copies already in the old folder stay there. Gamma sees only the folders it made in your Drive."),
    idle: () => t("A folder of a Google account's Drive"),
  },
  onedrive: {
    company: "Microsoft",
    top: () => t("OneDrive"),
    lead: () => t("Sign in to Microsoft with the account to back up to, personal or work, and allow Gamma to keep its backups in your OneDrive. Gamma works only in the folder you choose."),
    folderHint: () => t("New copies go here; the copies already in the old folder stay there."),
    idle: () => t("A folder of a Microsoft account's OneDrive, personal or work"),
  },
};

// Signing in on the service's page, where Gamma is opened on its own
// computer (`status.browser`): the page's address asked for as the dialog
// opens (POST …/authorize), one button opening it, and the dialog waits,
// reading the row again, until the connection is there (the server's
// callback made it) or says why not.
function PageDialog({ store, endpoint, status, onStatus, onClose, onConnected }) {
  const cloud = CLOUDS[store];
  const [url, setUrl] = React.useState("");
  const [error, setError] = React.useState("");
  const [opened, setOpened] = React.useState(false);
  const before = React.useRef(status.connected_at);

  async function start() {
    setError(""); setUrl(""); setOpened(false);
    try { setUrl((await apiJson(`${endpoint}/authorize`, jsonInit("POST", { folder: status.folder || null }))).url); }
    catch (err) { setError(err.message); }
  }
  React.useEffect(() => { start(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  React.useEffect(() => {
    if (!opened) return undefined;
    let stopped = false;
    let timer = 0;
    const ask = async () => {
      try {
        const value = await apiJson(endpoint);
        if (stopped) return;
        onStatus(value);
        if (value.connected && value.connected_at !== before.current) return onConnected(value);
        if (value.error) return setError(value.error);
      } catch { /* asked again below */ }
      if (!stopped) timer = setTimeout(ask, 2000);
    };
    timer = setTimeout(ask, 2000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <SubDialog title={t("Connect {store}", { store: STORE[store].title() })} onClose={onClose}>
      <p className="settingsPaneHint">{cloud.lead()}</p>
      {url && !error ? (
        <a className="uiBtn primary cloudOpenPage" href={url} target="_blank" rel="noopener noreferrer" onClick={() => setOpened(true)}>
          {t("Continue with {company}", { company: cloud.company })}<ExternalLinkIcon size={14} />
        </a>
      ) : null}
      {error ? <div className="aiKeyCheck aiKeysError" role="status"><XIcon size={14} /> {error}</div>
        : opened ? <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Waiting for {company}… This closes by itself once you allow access.", { company: cloud.company })}</div>
          : !url ? <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Preparing {company}'s page…", { company: cloud.company })}</div> : null}
      <DialogButtons>
        {error ? <button type="button" className="uiBtn primary" onClick={start}>{t("Try again")}</button> : null}
        <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
      </DialogButtons>
    </SubDialog>
  );
}

// Signing in with a code, anywhere else: a code asked for as the dialog
// opens (unless one already waits), copied and the service's page opened
// by one button, then the dialog waits for the answer. A no or an expired
// code says why and offers a new code. `status` is the row's read.
function CodeDialog({ store, endpoint, status, onStatus, onClose, onConnected }) {
  const cloud = CLOUDS[store];
  const [error, setError] = React.useState("");
  const [asking, setAsking] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const pending = status.pending;

  async function start() {
    setAsking(true); setError(""); setCopied(false);
    try { onStatus(await apiJson(`${endpoint}/connect`, jsonInit("POST", { folder: status.folder || null }))); }
    catch (err) { setError(err.message); }
    finally { setAsking(false); }
  }
  React.useEffect(() => { if (!pending) start(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Ask the server at the service's interval, one request at a time, while
  // a code is shown. A yes closes the dialog; a no or an expired code shows why.
  const code = pending?.user_code;
  React.useEffect(() => {
    if (!code) return undefined;
    let stopped = false;
    let timer = 0;
    const ask = async () => {
      try {
        const value = await apiJson(`${endpoint}/poll`, jsonInit("POST"));
        if (stopped) return;
        onStatus(value);
        if (value.pending) {
          setError("");
          timer = setTimeout(ask, Math.max(2, value.pending.interval) * 1000);
        } else if (value.error) setError(value.error);
        else onConnected(value);
      } catch (err) {
        if (stopped) return;
        setError(err.message);
        timer = setTimeout(ask, Math.max(2, pending.interval) * 1000);
      }
    };
    timer = setTimeout(ask, Math.max(2, pending.interval) * 1000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [code]); // eslint-disable-line react-hooks/exhaustive-deps

  async function close() {
    if (pending) {
      try { onStatus(await apiJson(`${endpoint}/connect`, jsonInit("DELETE"))); } catch { /* it expires on its own */ }
    }
    onClose();
  }
  const where = (pending?.verification_url || "").replace(/^https?:\/\//, "");
  const company = cloud.company;
  return (
    <SubDialog title={t("Connect {store}", { store: STORE[store].title() })} onClose={close}>
      <p className="settingsPaneHint">{cloud.lead()}</p>
      {pending ? <>
        <Field group label={t("Your code")}>
          <CopyField label={t("Your code")} value={pending.user_code} action={t("Copy the code")} rows={1} />
        </Field>
        <a className="uiBtn primary cloudOpenPage" href={pending.verification_url} target="_blank" rel="noopener noreferrer"
          onClick={() => { copyText(pending.user_code).then((ok) => setCopied(!!ok)); }}>
          {t("Copy the code and open {company}", { company })}<ExternalLinkIcon size={14} />
        </a>
        <ol className="oauthInstructions">
          <li>{copied ? t("The code is copied: paste it on {company}'s page.", { company })
            : t("Enter the code on {company}'s page ({where}).", { company, where })}</li>
          <li>{t("Choose your account and allow access. This closes by itself once {company} says yes.", { company })}</li>
        </ol>
        {error ? <div className="aiKeyCheck aiKeysError" role="status"><XIcon size={14} /> {error}</div>
          : <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Waiting for {company}…", { company })}</div>}
      </> : asking ? (
        <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Asking {company} for a code…", { company })}</div>
      ) : error ? <FormNotice>{error}</FormNotice> : null}
      <DialogButtons>
        {!pending && !asking && error ? (
          <button type="button" className="uiBtn primary" onClick={start}>{t("Get a new code")}</button>
        ) : null}
        <button type="button" className="uiBtn" onClick={close}>{t("Cancel")}</button>
      </DialogButtons>
    </SubDialog>
  );
}

// The folder new copies go to, chosen in the cloud, and Disconnect.
function EditDialog({ store, endpoint, status, confirm, onStatus, onClose }) {
  const cloud = CLOUDS[store];
  const title = STORE[store].title();
  const [folder, setFolder] = React.useState(status.folder);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [making, setMaking] = React.useState(false); // a new folder on its way: Save waits for it
  const [source] = React.useState(() => ({
    name: cloud.top(), icon: HardDriveIcon,
    list: (path) => apiJson(`${endpoint}/browse?${new URLSearchParams({ path })}`),
    create: async (path) => (await apiJson(`${endpoint}/folders`, jsonInit("POST", { path }))).path,
  }));

  async function save(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try { onStatus(await apiJson(endpoint, jsonInit("PUT", { folder }))); onClose(); }
    catch (err) { setError(err.message); setBusy(false); }
  }
  function disconnect() {
    const args = { email: status.email || t("this account"), folder: folderLabel(status.folder), company: cloud.company };
    confirm({
      title: t("Disconnect {store}", { store: title }),
      message: status.revokes
        ? t("Disconnect {email}? Backup tasks stop copying there, and Gamma's access is revoked at {company}. The copies already in “{folder}” stay.", args)
        : t("Disconnect {email}? Backup tasks stop copying there and Gamma forgets the access; to revoke it too, remove Gamma from your {company} account's apps. The copies already in “{folder}” stay.", args),
      confirmLabel: t("Disconnect"), danger: true,
      onConfirm: async () => {
        try { onStatus(await apiJson(endpoint, jsonInit("DELETE"))); onClose(); }
        catch (err) { setError(err.message); }
      },
    });
  }
  return (
    <SubDialog title={t("Edit {store}", { store: title })} onClose={onClose} draft={folder} className="folderBrowserDialog">
      <form className="settingsForm" noValidate onSubmit={save}>
        <Field group label={t("Folder")} hint={cloud.folderHint()}>
          <FolderBrowser source={source} value={folder} onChange={setFolder} onBusy={setMaking}
            label={t("Folders in {top}", { top: cloud.top() })} />
        </Field>
        {error ? <FormNotice>{error}</FormNotice> : null}
        <DialogButtons footnote={folder ? null : t("Choose a folder in {top}: copies cannot go in {top} itself.", { top: cloud.top() })}>
          <button type="button" className="uiBtn danger" onClick={disconnect}>{t("Disconnect…")}</button>
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={busy || making || !folder}>
            <MorphText>{busy ? t("Saving…") : t("Save")}</MorphText>
          </button>
        </DialogButtons>
      </form>
    </SubDialog>
  );
}

// A cloud's row: its state, Connect… or Test and Edit…, and its dialogs.
export function CloudRow({ store, setStatus, confirm }) {
  const read = useSettingsRead(STORE[store].read);
  const status = read.data; // GET's {available, connected, email, folder, problem, pending, error, revokes, browser}
  const endpoint = `${API}${STORE[store].read}`;
  const title = STORE[store].title();
  const cloud = CLOUDS[store];
  const [dialog, setDialog] = React.useState(""); // "" | "connect" | "edit"
  const [test, setTest] = React.useState(null);

  async function runTest() {
    setTest({ busy: true });
    try {
      const value = await apiJson(`${endpoint}/test`, jsonInit("POST"));
      setTest({ ok: !!value.ok, message: value.message });
    } catch (err) { setTest({ ok: false, message: err.message }); }
  }

  if (!status) {
    return <Row icon={CloudIcon} label={title} hint={read.error || t("Loading…")}>
      {read.error ? <button type="button" className="uiBtn sm" onClick={() => read.reload().catch(() => {})}>{t("Try again")}</button> : null}
    </Row>;
  }
  const where = STORE[store].where(status);
  let row;
  if (!status.available && !status.connected) {
    row = <Row icon={CloudOffIcon} label={title} hint={t("Not available: this build of Gamma has no app to sign in to {store} with", { store: title })} />;
  } else if (!status.connected) {
    row = <Row icon={CloudIcon} label={title} hint={status.pending ? t("Waiting for the code") : cloud.idle()}>
      <button className="uiBtn sm" onClick={() => setDialog("connect")}>
        <MorphText>{status.pending ? t("Enter the code…") : t("Connect…")}</MorphText>
      </button>
    </Row>;
  } else {
    row = <Row icon={where ? CloudCheckIcon : CloudOffIcon} label={title}
      hint={where || [status.email, folderLabel(status.folder)].filter(Boolean).join(" › ")}>
      <span className="setRowControls">
        <TestResult test={test} />
        {where ? <button className="uiBtn sm" disabled={!!test?.busy} onClick={runTest}>{t("Test")}</button>
          : <button className="uiBtn sm" onClick={() => setDialog("connect")}>{t("Connect again…")}</button>}
        <button className="uiBtn sm" onClick={() => setDialog("edit")}>{t("Edit…")}</button>
      </span>
    </Row>;
  }
  const props = { store, endpoint, status, onStatus: (value) => read.set(value) };
  // the service's page here, a code elsewhere (or while a code already waits)
  const ConnectDialog = status.browser && !status.pending ? PageDialog : CodeDialog;
  return <>
    {row}
    {status.connected && status.problem ? <FormNotice>{status.problem}</FormNotice> : null}
    {dialog === "connect" ? (
      <ConnectDialog {...props} onClose={() => setDialog("")}
        onConnected={(value) => { read.set(value); setDialog(""); setStatus?.(t("{store} connected.", { store: title })); }} />
    ) : null}
    {dialog === "edit" ? (
      <EditDialog {...props} confirm={confirm} onStatus={(value) => { read.set(value); setTest(null); }} onClose={() => setDialog("")} />
    ) : null}
  </>;
}
