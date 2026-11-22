// Settings → Backups › Google Drive (admins only): the Google account that
// backup tasks copy their snapshots to (BackupTasks.jsx, "Copy to Google
// Drive"), beside the bucket (BackupBucket.jsx), into one folder of its My
// Drive. Like the bucket it has no schedule of its own, and Saved snapshots
// lists what it holds (SettingsBackups.jsx). Connecting is signing in to
// Google with Gamma's own app and allowing it (gamma/gdrive_account.py).
// Where Gamma is opened on its own computer (the desktop app; the read's
// `browser`), Connect… opens Google's page, which comes back to the server
// (PageDialog). Anywhere else it is Google's device flow (CodeDialog):
// Connect… asks the server for a code (POST /api/admin/gdrive/connect) as
// its dialog opens, whose one button copies the code and opens Google's
// page, and while it is open polls the server
// (POST /api/admin/gdrive/poll, at the interval Google gave) until Google
// says yes, no, or the code expires; closing it stops waiting (DELETE
// /api/admin/gdrive/connect). Edit… changes the folder (PUT), chosen in a
// folder browser over My Drive (explorer/FolderBrowser.jsx: GET
// /api/admin/gdrive/browse, New folder POST /api/admin/gdrive/folders),
// which shows only what Gamma made there, or disconnects (DELETE
// /api/admin/gdrive, which revokes the access at Google; the copies stay in
// Drive). The folder is a path of names from My Drive's top
// ("Backups/Gamma"), shown "Backups › Gamma". GET /api/admin/gdrive is the
// section's read (gamma/gdrive_account.py `status`). docs/dev/settings.md.
import React from "react";
import { API, apiJson, copyText } from "../shared/lib/utils";
import { CopyField, DialogButtons, Field, FormNotice, Row, Section, SubDialog, TestResult } from "./SettingsKit";
import { useSettingsRead } from "./settingsReads.js";
import { CloudIcon, CloudOffIcon, ExternalLinkIcon, HardDriveIcon, XIcon } from "../shared/ui/Icons";
import FolderBrowser from "../explorer/FolderBrowser.jsx";
import { MorphText } from "../shared/ui/MotionWidgets";
import { T, t } from "../shared/i18n/i18n.js";

export const DRIVE_READ = "/admin/gdrive";
const ENDPOINT = `${API}${DRIVE_READ}`;
const DEFAULT_FOLDER = "Gamma backups";
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });

// The folder's path as the pane shows it: "Backups › Gamma".
const folderLabel = (folder) => folder.split("/").join(" › ");

// Where the copies go: "someone@gmail.com › Gamma backups", "" while no
// connection can be used.
export function driveWhere(status) {
  if (!status?.connected || status.problem) return "";
  return `${status.email || t("Google Drive")} › ${folderLabel(status.folder)}`;
}

// Signing in on Google's page, where Gamma is opened on its own computer
// (`status.browser`): the page's address asked for as the dialog opens
// (POST /api/admin/gdrive/authorize), one button opening it, and the
// dialog waits, reading the section again, until the connection is there
// (the server's callback made it) or says why not.
function PageDialog({ status, onStatus, onClose, onConnected }) {
  const [url, setUrl] = React.useState("");
  const [error, setError] = React.useState("");
  const [opened, setOpened] = React.useState(false);
  const before = React.useRef(status.connected_at);

  async function start() {
    setError(""); setUrl(""); setOpened(false);
    try { setUrl((await apiJson(`${ENDPOINT}/authorize`, json("POST", { folder: status.folder || DEFAULT_FOLDER }))).url); }
    catch (err) { setError(err.message); }
  }
  React.useEffect(() => { start(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  React.useEffect(() => {
    if (!opened) return undefined;
    let stopped = false;
    let timer = 0;
    const ask = async () => {
      try {
        const value = await apiJson(ENDPOINT);
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
    <SubDialog title={t("Connect Google Drive")} onClose={onClose}>
      <p className="settingsPaneHint">
        {t("Sign in to Google with the account to back up to, and allow Gamma to keep its backups in your Drive. Gamma sees only the files it makes there.")}
      </p>
      {url && !error ? (
        <a className="uiBtn primary driveOpenGoogle" href={url} target="_blank" rel="noopener noreferrer" onClick={() => setOpened(true)}>
          {t("Continue with Google")}<ExternalLinkIcon size={14} />
        </a>
      ) : null}
      {error ? <div className="aiKeyCheck aiKeysError" role="status"><XIcon size={14} /> {error}</div>
        : opened ? <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Waiting for Google… This closes by itself once you allow access.")}</div>
          : !url ? <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Preparing Google's page…")}</div> : null}
      <DialogButtons>
        {error ? <button type="button" className="uiBtn primary" onClick={start}>{t("Try again")}</button> : null}
        <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
      </DialogButtons>
    </SubDialog>
  );
}

// Signing in with a code, anywhere else: a code asked for as the dialog
// opens (unless one already waits), copied and Google's page opened by one
// button, then the dialog waits for Google's answer. A no or an expired
// code says why and offers a new code. `status` is the section's read.
function CodeDialog({ status, onStatus, onClose, onConnected }) {
  const [error, setError] = React.useState("");
  const [asking, setAsking] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const pending = status.pending;

  async function start() {
    setAsking(true); setError(""); setCopied(false);
    try { onStatus(await apiJson(`${ENDPOINT}/connect`, json("POST", { folder: status.folder || DEFAULT_FOLDER }))); }
    catch (err) { setError(err.message); }
    finally { setAsking(false); }
  }
  React.useEffect(() => { if (!pending) start(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Ask the server at Google's interval, one request at a time, while a
  // code is shown. A yes closes the dialog; a no or an expired code shows why.
  const code = pending?.user_code;
  React.useEffect(() => {
    if (!code) return undefined;
    let stopped = false;
    let timer = 0;
    const ask = async () => {
      try {
        const value = await apiJson(`${ENDPOINT}/poll`, json("POST"));
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
      try { onStatus(await apiJson(`${ENDPOINT}/connect`, json("DELETE"))); } catch { /* it expires on its own */ }
    }
    onClose();
  }
  const where = (pending?.verification_url || "").replace(/^https?:\/\//, "");
  return (
    <SubDialog title={t("Connect Google Drive")} onClose={close}>
      <p className="settingsPaneHint">
        {t("Sign in to Google with the account to back up to, and allow Gamma to keep its backups in your Drive. Gamma sees only the files it makes there.")}
      </p>
      {pending ? <>
        <Field group label={t("Your code")}>
          <CopyField label={t("Your code")} value={pending.user_code} action={t("Copy the code")} rows={1} />
        </Field>
        <a className="uiBtn primary driveOpenGoogle" href={pending.verification_url} target="_blank" rel="noopener noreferrer"
          onClick={() => { copyText(pending.user_code).then((ok) => setCopied(!!ok)); }}>
          {t("Copy the code and open Google")}<ExternalLinkIcon size={14} />
        </a>
        <ol className="oauthInstructions">
          <li>{copied ? t("The code is copied: paste it on Google's page.") : t("Enter the code on Google's page ({where}).", { where })}</li>
          <li>{t("Choose your Google account and allow access. This closes by itself once Google says yes.")}</li>
        </ol>
        {error ? <div className="aiKeyCheck aiKeysError" role="status"><XIcon size={14} /> {error}</div>
          : <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Waiting for Google…")}</div>}
      </> : asking ? (
        <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Asking Google for a code…")}</div>
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

// Google's page here, a code elsewhere (or while a code already waits).
function ConnectDialog(props) {
  return props.status.browser && !props.status.pending ? <PageDialog {...props} /> : <CodeDialog {...props} />;
}

// The folder new copies go to, chosen in My Drive, and Disconnect.
function EditDialog({ status, confirm, onStatus, onClose }) {
  const [folder, setFolder] = React.useState(status.folder);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [making, setMaking] = React.useState(false); // a new folder on its way: Save waits for it
  const [source] = React.useState(() => ({
    name: t("My Drive"), icon: HardDriveIcon,
    list: (path) => apiJson(`${ENDPOINT}/browse?${new URLSearchParams({ path })}`),
    create: async (path) => (await apiJson(`${ENDPOINT}/folders`, json("POST", { path }))).path,
  }));

  async function save(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try { onStatus(await apiJson(ENDPOINT, json("PUT", { folder }))); onClose(); }
    catch (err) { setError(err.message); setBusy(false); }
  }
  function disconnect() {
    confirm({
      title: T("Disconnect Google Drive"),
      message: t("Disconnect {email}? Backup tasks stop copying there, and Gamma's access is revoked at Google. The copies already in “{folder}” stay in Drive.",
        { email: status.email || t("this Google account"), folder: folderLabel(status.folder) }),
      confirmLabel: t("Disconnect"), danger: true,
      onConfirm: async () => {
        try { onStatus(await apiJson(ENDPOINT, json("DELETE"))); onClose(); }
        catch (err) { setError(err.message); }
      },
    });
  }
  return (
    <SubDialog title={t("Edit Google Drive")} onClose={onClose} draft={folder} className="folderBrowserDialog">
      <form className="settingsForm" noValidate onSubmit={save}>
        <Field group label={t("Folder")}
          hint={t("New copies go here; the copies already in the old folder stay there. Gamma sees only the folders it made in your Drive.")}>
          <FolderBrowser source={source} value={folder} onChange={setFolder} onBusy={setMaking} label={t("Folders in My Drive")} />
        </Field>
        {error ? <FormNotice>{error}</FormNotice> : null}
        <DialogButtons footnote={folder ? null : t("Choose a folder in My Drive: copies cannot go in My Drive itself.")}>
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

export function BackupDrive({ setStatus, confirm }) {
  const drive = useSettingsRead(DRIVE_READ);
  const status = drive.data; // GET's {available, connected, email, folder, problem, pending, error, …}
  const [dialog, setDialog] = React.useState(""); // "" | "connect" | "edit"
  const [test, setTest] = React.useState(null);

  async function runTest() {
    setTest({ busy: true });
    try {
      const value = await apiJson(`${ENDPOINT}/test`, json("POST"));
      setTest({ ok: !!value.ok, message: value.message });
    } catch (err) { setTest({ ok: false, message: err.message }); }
  }

  const tag = <span className="uiTag admin">{t("admin")}</span>;
  if (!status) {
    return <Section title={t("Google Drive")} tag={tag}>
      {drive.error ? <FormNotice>{drive.error}{" "}
        <button type="button" className="uiBtn sm" onClick={() => drive.reload().catch(() => {})}>{t("Try again")}</button>
      </FormNotice> : null}
    </Section>;
  }
  const where = driveWhere(status);
  let row;
  if (!status.available && !status.connected) {
    row = <Row icon={CloudOffIcon} label={t("Not available")} hint={t("This build of Gamma cannot sign in to Google Drive")} />;
  } else if (!status.connected) {
    row = <Row icon={CloudIcon} label={status.pending ? t("Waiting for the code") : t("Not connected")}
      hint={t("Sign in to a Google account: backup tasks copy their snapshots to a folder of its Drive")}>
      <button className="uiBtn sm primary" onClick={() => setDialog("connect")}>
        <MorphText>{status.pending ? t("Enter the code…") : t("Connect…")}</MorphText>
      </button>
    </Row>;
  } else {
    row = <Row icon={CloudIcon} label={status.email || t("Google Drive")}
      hint={where ? t("{folder} · backup tasks can copy their snapshots here", { folder: folderLabel(status.folder) }) : folderLabel(status.folder)}>
      <span className="setRowControls">
        <TestResult test={test} />
        {where ? <button className="uiBtn sm" disabled={!!test?.busy} onClick={runTest}>{t("Test")}</button>
          : <button className="uiBtn sm" onClick={() => setDialog("connect")}>{t("Connect again…")}</button>}
        <button className="uiBtn sm" onClick={() => setDialog("edit")}>{t("Edit…")}</button>
      </span>
    </Row>;
  }
  return <>
    <Section title={t("Google Drive")} tag={tag}>
      {row}
      {status.connected && status.problem ? <FormNotice>{status.problem}</FormNotice> : null}
    </Section>
    {dialog === "connect" ? (
      <ConnectDialog status={status} onStatus={(value) => drive.set(value)} onClose={() => setDialog("")}
        onConnected={(value) => { drive.set(value); setDialog(""); setStatus?.(t("Google Drive connected.")); }} />
    ) : null}
    {dialog === "edit" ? (
      <EditDialog status={status} confirm={confirm} onStatus={(value) => { drive.set(value); setTest(null); }}
        onClose={() => setDialog("")} />
    ) : null}
  </>;
}
