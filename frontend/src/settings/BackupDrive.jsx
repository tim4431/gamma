// Settings → Backups › Google Drive (admins only): the Google account that
// backup tasks copy their snapshots to (BackupTasks.jsx, "Copy to Google
// Drive"), beside the bucket (BackupBucket.jsx), into one folder of its My
// Drive. Like the bucket it has no schedule of its own, and Saved snapshots
// lists what it holds (SettingsBackups.jsx). Connecting is Google's device
// flow: Connect… asks the server for a code (POST /api/admin/gdrive/connect),
// the dialog shows it with Google's address, and while it is open polls the
// server (POST /api/admin/gdrive/poll, at the interval Google gave) until
// Google says yes, no, or the code expires; closing it stops waiting
// (DELETE /api/admin/gdrive/connect). Edit… changes the folder (PUT) or
// disconnects (DELETE /api/admin/gdrive, which revokes the access at Google;
// the copies stay in Drive). GET /api/admin/gdrive is the section's read
// (gamma/gdrive_account.py `status`). docs/dev/settings.md.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { CopyField, DialogButtons, Field, FormNotice, Row, Section, SubDialog, TestResult } from "./SettingsKit";
import { useSettingsRead } from "./settingsReads.js";
import { CloudIcon, CloudOffIcon, ExternalLinkIcon, XIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { T, t } from "../shared/i18n/i18n.js";

export const DRIVE_READ = "/admin/gdrive";
const ENDPOINT = `${API}${DRIVE_READ}`;
const DEFAULT_FOLDER = "Gamma backups";
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });

// Where the copies go: "someone@gmail.com › Gamma backups", "" while no
// connection can be used.
export function driveWhere(status) {
  if (!status?.connected || status.problem) return "";
  return `${status.email || t("Google Drive")} › ${status.folder}`;
}

// The connection's two steps: the folder's name, then the code to enter
// at Google while the dialog polls. `status` is the section's read; one
// that already waits for a code opens on the second step.
function ConnectDialog({ status, onStatus, onClose, onConnected }) {
  const [folder, setFolder] = React.useState(status.folder || DEFAULT_FOLDER);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const pending = status.pending;

  async function start(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try { onStatus(await apiJson(`${ENDPOINT}/connect`, json("POST", { folder: folder.trim() }))); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
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
      {!pending ? (
        <form className="settingsForm" noValidate onSubmit={start}>
          <Field label={t("Folder")} hint={t("Made in your My Drive. Gamma sees only the files it puts there.")}>
            <input className="aiKeyInput" aria-label={t("Folder")} value={folder} maxLength={120} spellCheck={false}
              autoFocus placeholder={DEFAULT_FOLDER} onChange={(event) => setFolder(event.target.value)} />
          </Field>
          {error ? <FormNotice>{error}</FormNotice> : null}
          <DialogButtons>
            <button type="button" className="uiBtn" onClick={close}>{t("Cancel")}</button>
            <button type="submit" className="uiBtn primary" disabled={busy || !folder.trim()}>
              <MorphText>{busy ? t("Asking Google…") : t("Get a code")}</MorphText>
            </button>
          </DialogButtons>
        </form>
      ) : <>
        <ol className="oauthInstructions">
          <li>{t("On any device, open {where} and sign in to the Google account to back up to.", { where })}</li>
          <li>{t("Enter the code below and allow access to the files Gamma makes.")}</li>
        </ol>
        <Field group label={t("Code")}>
          <CopyField label={t("Code")} value={pending.user_code} action={t("Copy the code")} rows={1} />
          <a className="aiKeyLink" href={pending.verification_url} target="_blank" rel="noopener noreferrer">
            {t("Open {where}", { where })}<ExternalLinkIcon size={14} />
          </a>
        </Field>
        {error ? <div className="aiKeyCheck aiKeysError" role="status"><XIcon size={14} /> {error}</div>
          : <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Waiting for Google…")}</div>}
        <DialogButtons>
          <button type="button" className="uiBtn" onClick={close}>{t("Cancel")}</button>
        </DialogButtons>
      </>}
    </SubDialog>
  );
}

// The folder new copies go to, and Disconnect.
function EditDialog({ status, confirm, onStatus, onClose }) {
  const [folder, setFolder] = React.useState(status.folder);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function save(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try { onStatus(await apiJson(ENDPOINT, json("PUT", { folder: folder.trim() }))); onClose(); }
    catch (err) { setError(err.message); setBusy(false); }
  }
  function disconnect() {
    confirm({
      title: T("Disconnect Google Drive"),
      message: t("Disconnect {email}? Backup tasks stop copying there, and Gamma's access is revoked at Google. The copies already in “{folder}” stay in Drive.",
        { email: status.email || t("this Google account"), folder: status.folder }),
      confirmLabel: t("Disconnect"), danger: true,
      onConfirm: async () => {
        try { onStatus(await apiJson(ENDPOINT, json("DELETE"))); onClose(); }
        catch (err) { setError(err.message); }
      },
    });
  }
  return (
    <SubDialog title={t("Edit Google Drive")} onClose={onClose} draft={folder}>
      <form className="settingsForm" noValidate onSubmit={save}>
        <Field label={t("Folder")} hint={t("New copies go here; the copies already in the old folder stay there.")}>
          <input className="aiKeyInput" aria-label={t("Folder")} value={folder} maxLength={120} spellCheck={false}
            onChange={(event) => setFolder(event.target.value)} />
        </Field>
        {error ? <FormNotice>{error}</FormNotice> : null}
        <DialogButtons>
          <button type="button" className="uiBtn danger" onClick={disconnect}>{t("Disconnect…")}</button>
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={busy || !folder.trim()}>
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
    row = <Row icon={CloudOffIcon} label={t("Not available")}
      hint={t("Google Drive needs a Google OAuth client: set {id} and {secret} in the server's environment.",
        { id: "GAMMA_GOOGLE_DRIVE_CLIENT_ID", secret: "GAMMA_GOOGLE_DRIVE_CLIENT_SECRET" })} />;
  } else if (!status.connected) {
    row = <Row icon={CloudIcon} label={status.pending ? t("Waiting for the code") : t("Not connected")}
      hint={t("A folder in your Google Drive, for backup tasks to copy their snapshots to")}>
      <button className="uiBtn sm" onClick={() => setDialog("connect")}>
        <MorphText>{status.pending ? t("Enter the code…") : t("Connect…")}</MorphText>
      </button>
    </Row>;
  } else {
    row = <Row icon={CloudIcon} label={status.email || t("Google Drive")}
      hint={where ? t("{folder} · backup tasks can copy their snapshots here", { folder: status.folder }) : status.folder}>
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
