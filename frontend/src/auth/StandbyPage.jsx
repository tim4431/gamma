import React from "react";
import { FormNotice, PasswordInput, Row, Section } from "../settings/SettingsKit";
import { ArrowUpIcon, DatabaseIcon, DownloadIcon, ExportIcon, ShieldIcon, UserIcon, UsersIcon } from "../shared/ui/Icons";
import { ActionMenu } from "../shared/ui/Menus";
import { Chevron, Collapse } from "../shared/ui/MotionWidgets";
import { fmtBytes } from "../shared/lib/format.js";
import { saveUrl } from "../shared/lib/saveFile.js";
import { updateHowTo, updateLink } from "../shared/lib/updates.js";
import { TaskBlock } from "../tasks/TasksTray";
import { taskTitle } from "../tasks/taskKinds.js";
import { T, t } from "../shared/i18n/i18n.js";
import { AuthShell, LoginError, LoginNotice } from "./LoginPage";

// The page a server shows while it cannot open its data directory (backend
// gamma/standby.py; docs/dev/migrations.md "When the server cannot open the
// library"): its config answers a 503 carrying this error and the guidance,
// which App's PageHostGate hands here instead of mounting the app.
export const STANDBY_ERROR = "data_directory_not_upgradable";

// Guidance text with its `code` spans marked up (the server's wording).
function Text({ children }) {
  return String(children).split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part));
}

// "Technical details", folded: the guidance's lines, the build, the data
// folder. Server text: it names versions and paths, so it is not translated.
function Details({ guide }) {
  const [open, setOpen] = React.useState(false);
  const lines = [...(guide.details || []), `Gamma ${guide.build}`, `${t("Data folder")}: \`${guide.data_dir}\``];
  return <>
    <button type="button" className="loginDisclosure standbyDisclosure" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
      <Chevron open={open} dir="right" size={14} />
      {t("Technical details")}
    </button>
    <Collapse open={open}>
      <ul className="standbyDetails">{lines.map((line, i) => <li key={i}><Text>{line}</Text></li>)}</ul>
    </Collapse>
  </>;
}

// The update goes where this install's updates come from (its channel:
// shared/lib/updates.js, as Settings → Server says it): TestFlight, the
// releases page, a store; a server's has no button, its admin updates it.
export function StandbyPage({ guide }) {
  if (guide.kind !== "newer") return <OperatorPage guide={guide} />;
  const link = updateLink(guide.install);
  return (
    <AuthShell headline={t("Gamma needs an update to open your library")} className="standbyCard">
      <LoginNotice icon={ShieldIcon}>{t("Your library is safe. Nothing has been changed.")}</LoginNotice>
      <p className="loginConflictText">
        {t("A newer version of Gamma last opened this library and saved it in a way this version can't read yet. Update Gamma and everything will be where you left it.")}
      </p>
      {link ? (
        <a className="uiBtn primary loginBtn" href={link.href} target="_blank" rel="noopener noreferrer">
          <ArrowUpIcon size={16} aria-hidden="true" />{link.label}
        </a>
      ) : null}
      <p className="standbyHint">{updateHowTo(guide.install)}</p>
      <SaveCopy primary={!link} />
      <Details guide={guide} />
    </AuthShell>
  );
}

// What Export makes, as Settings → Workspaces offers it: everything, or
// the databases alone.
const KINDS = [
  [ExportIcon, T("Everything (.zip)"), true],
  [DatabaseIcon, T("Databases only (.zip)"), false],
];
const FILE = "/api/standby/export/file";

// "Can't update right now?": an admin saves a copy as Settings → Workspaces
// does — a workspace's export, or Export all for every personal one — made
// in the background (GET /api/standby's `export`, a workspace-export job's
// shape, shown as Settings shows one) and downloaded once it is ready
// (saveUrl: the browser's download, or the app's own save on a device);
// anyone else signs in, or is told whom to ask. `primary`: the sign-in is
// the page's one main action (a server's page, which has no update button).
function SaveCopy({ primary }) {
  const [library, setLibrary] = React.useState(null); // GET /api/standby: {user, admin, workspaces, export}
  const [error, setError] = React.useState("");
  const asked = React.useRef(false); // this page started the copy: it downloads it when it is done
  const read = React.useCallback(() => fetch("/api/standby", { credentials: "same-origin", cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null)).then((answer) => { setLibrary(answer); return answer; }, () => null), []);
  React.useEffect(() => { read(); }, [read]);
  const task = library?.export || null;
  const running = task?.state === "running";
  React.useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(async () => {
      const answer = await read();
      if (answer?.export?.state === "done" && asked.current) {
        asked.current = false;
        saveUrl(FILE, answer.export.artifact.name).catch(() => {});
      }
    }, 700);
    return () => clearInterval(timer);
  }, [running, read]);
  async function start(body) {
    setError("");
    const r = await fetch("/api/standby/export", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
    if (!r?.ok) { setError((await r?.json().catch(() => null))?.detail || t("Can't reach the server.")); return; }
    asked.current = true;
    setLibrary((l) => ({ ...l, export: null }));
    read();
  }
  if (!library) return null;
  let body;
  if (!library.user) body = <SignIn primary={primary} onSignedIn={read} />;
  else if (!library.admin) {
    body = <p className="standbyText">
      {t("You're signed in as {name}. Saving a copy is for this server's admins: ask yours to update Gamma, or to save a copy of your workspace for you.", { name: <b>{library.user}</b> })}
    </p>;
  } else {
    const personal = library.workspaces.filter((w) => w.personal);
    const shared = library.workspaces.filter((w) => !w.personal);
    const items = (what) => KINDS.map(([icon, label, uploads]) => ({ icon, label, onClick: () => start({ ...what, uploads }) }));
    const row = (w) => (
      <Row key={w.id} icon={w.personal ? UserIcon : UsersIcon} label={w.name} hint={fmtBytes(w.size)}>
        <ActionMenu label={T("Export")} icon={ExportIcon} disabled={running} items={items({ ws: w.id })} />
      </Row>
    );
    body = <>
      <p className="standbyText">{t("Save a copy of your workspaces, to keep them safe or to open them in an up-to-date Gamma on another device.")}</p>
      {running ? <TaskBlock task={task} tasks={null} /> : null}
      {task?.state === "done" ? (
        <Row icon={DownloadIcon} label={taskTitle(task)} hint={`${task.artifact.name} · ${fmtBytes(task.artifact.size)}`}>
          <button type="button" className="uiBtn sm" onClick={() => saveUrl(FILE, task.artifact.name).catch(() => {})}>
            <DownloadIcon size={16} aria-hidden="true" />{t("Download")}
          </button>
        </Row>
      ) : null}
      {task?.state === "failed" ? <FormNotice>{t("The copy could not be made: {error}", { error: task.error })}</FormNotice> : null}
      {error ? <FormNotice>{error}</FormNotice> : null}
      <Section title={t("Personal")} action={personal.length ? (
        <ActionMenu label={T("Export all")} icon={ExportIcon} disabled={running} items={items({ all: true })} />
      ) : null}>
        {personal.length ? personal.map(row) : <p className="standbyText">{t("There is no workspace to save yet.")}</p>}
      </Section>
      {shared.length ? <Section title={t("Shared")}>{shared.map(row)}</Section> : null}
      <p className="standbyHint">{t("To open one there: Settings → Workspaces → Data → Restore from a backup.")}</p>
    </>;
  }
  return <>
    <div className="loginOr">{t("Can't update right now?")}</div>
    {body}
  </>;
}

// The app's sign-in, as LoginPage words its failures; the desktop, iPad
// and Android apps sign their own account in before this shows.
function SignIn({ primary, onSignedIn }) {
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState(null); // a message, or {text, field}
  async function submit(e) {
    e.preventDefault();
    setError(null);
    let r;
    try {
      r = await fetch("/api/login", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) });
    } catch { setError(t("Can't reach the server.")); return; }
    if (r.ok) { onSignedIn(); return; }
    setError(r.status === 401
      ? { text: t("That username and password don't match. Check both, or ask your server's admin to reset the password."), field: "password" }
      : r.status === 429 ? t("Too many attempts. Wait a few minutes, then try again.") : t("Login failed"));
  }
  const text = typeof error === "string" ? error : error?.text || "";
  const bad = error?.field === "password";
  return (
    <form onSubmit={submit}>
      <p className="standbyText">{t("Sign in to save a copy of each workspace as a backup file.")}</p>
      <input type="text" className="loginInput" value={username} onChange={(e) => setUsername(e.target.value)}
        placeholder={t("Username")} autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="username" />
      <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t("Password")}
        className={`loginInput${bad ? " invalid" : ""}`} aria-invalid={bad || undefined} autoComplete="current-password" />
      {text ? <LoginError>{text}</LoginError> : null}
      <button type="submit" className={`uiBtn loginBtn${primary ? " primary" : ""}`} disabled={!username.trim() || !password}>
        {t("Log in")}
      </button>
    </form>
  );
}

// A library too old for this build, or an upgrade that stopped: pages for
// whoever runs the server, in the server's words (versions, commands).
function OperatorPage({ guide }) {
  const [open, setOpen] = React.useState(false);
  const fallback = guide.fallback || { lead: "", steps: [] };
  return (
    <AuthShell headline={guide.title} className="standbyCard">
      <LoginNotice icon={ShieldIcon}>{guide.status}</LoginNotice>
      <p className="standbyText">{guide.summary}</p>
      <ol className="standbySteps">{guide.steps.map((step, i) => <li key={i}><Text>{step}</Text></li>)}</ol>
      {fallback.steps.length ? <>
        <button type="button" className="loginDisclosure standbyDisclosure" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <Chevron open={open} dir="right" size={14} />{fallback.lead}
        </button>
        <Collapse open={open}>
          <ul className="standbyDetails">{fallback.steps.map((step, i) => <li key={i}><Text>{step}</Text></li>)}</ul>
        </Collapse>
      </> : null}
      <Details guide={guide} />
    </AuthShell>
  );
}
