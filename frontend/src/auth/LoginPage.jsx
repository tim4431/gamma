import React from "react";
import { PasswordInput } from "../settings/SettingsKit";
import { AlertCircleIcon, ArrowLeftIcon, CheckIcon, MailIcon, ServerIcon } from "../shared/ui/Icons";
import { BrandMark } from "../shared/ui/BrandMark";
import { Chevron, Collapse } from "../shared/ui/MotionWidgets";
import { nativeHost, postToHost } from "../shared/lib/host.js";
import { T, t, tn } from "../shared/i18n/i18n.js";

// The demo landing's picture: the README hero's paper and notes, cropped by
// tools/branding (design/brand/outputs.json) and served under /media/.
const DEMO_SCENE_URL = "/media/gamma-scene-light.svg";

// Every page shown before (or instead of) the app: login, loading, an
// unavailable workspace, a session conflict, a blocked share link. A page
// that reports a situation (`headline`) leads with it under the mark; the
// others with the name. `scene` (a demo server's landing) puts a picture
// beside the content instead, which then brings its own heading. Under the
// iPad app every one of them also offers the app's server picker (the
// account menu's "Server…" row needs a signed-in library), so a reachable
// but wrong server is never a dead end.
function AuthShell({ headline, scene, children }) {
  return (
    <div className="app">
      <div className="loginPage">
        {scene ? (
          <div className="loginCard loginLanding">
            <div className="loginScene"><img src={scene} alt="" /></div>
            <div className="loginLandingBody">{children}<HostServerLink /></div>
          </div>
        ) : (
          <div className="loginCard">
            <BrandMark className="loginMark" size={48} />
            {headline
              ? <h1 className="loginTitle loginHeadline">{headline}</h1>
              : <div className="loginTitle">{t("Gamma")}</div>}
            {children}
            <HostServerLink />
          </div>
        )}
      </div>
    </div>
  );
}

function HostServerLink() {
  if (!nativeHost()?.servers) return null;
  return (
    <button type="button" className="loginDisclosure" title={t("Connect this app to another Gamma server")}
      onClick={() => postToHost({ type: "servers" }).catch(() => {})}>
      <ServerIcon size={14} />{t("Server…")}
    </button>
  );
}

function LoginError({ children }) {
  return (
    <div className="loginError" role="alert">
      <AlertCircleIcon size={16} aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

// A sign-in's outcome that is no failure: a request to join sent, or waiting.
function LoginNotice({ children }) {
  return (
    <div className="loginNotice" role="status">
      <MailIcon size={16} aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

export function AuthLoading() {
  return <AuthShell><p className="loginLoading">{t("Loading...")}</p></AuthShell>;
}

export function WorkspaceUnavailablePage() {
  return (
    <AuthShell headline={t("This workspace is unavailable")}>
      <p className="loginConflictText">
        {t("It may have been deleted, or your account may no longer have access. Ask a workspace owner to invite you if you need access.")}
      </p>
      <button type="button" className="loginBtn" onClick={() => window.location.assign("/")}>
        {t("Open my workspaces")}
      </button>
    </AuthShell>
  );
}

// Shown when another tab of the same browser signed into a different account:
// the session cookie is browser-wide, so this tab's identity changed under it.
// The tab is frozen (its API calls are refused with 409 by the backend) until
// the user reloads into the account that now owns the session.
export function SessionConflictPage({ tabUser, activeUser, onReload }) {
  return (
    <AuthShell headline={t("Signed in elsewhere")}>
      <p className="loginConflictText">
        {t("This tab was open as {tabUser}, but this browser is now signed in as {activeUser} (from another tab). This tab has been paused so the two accounts' data can't mix.", {
          tabUser: <b>{tabUser}</b>, activeUser: <b>{activeUser}</b> })}
      </p>
      <button type="button" className="loginBtn" onClick={onReload}>
        {t("Continue as {name}", { name: activeUser })}
      </button>
      <p className="loginConflictHint">
        {t("To use both accounts at the same time, open one of them in a private window or a separate browser profile.")}
      </p>
    </AuthShell>
  );
}

// The cloud sign-in callback sends a refused sign-in back to the page it
// started from as `?cloud_error=`, or, when the refusal asked to join
// instead (the invited policy, gamma/join_requests.py), as `?cloud_join=`
// `sent` | `waiting` | `declined`: shown once, then dropped from the
// address bar.
function takeParam(name) {
  try {
    const url = new URL(window.location.href);
    const value = url.searchParams.get(name);
    if (!value) return "";
    url.searchParams.delete(name);
    window.history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
    return value;
  } catch { return ""; }
}
const takeCloudError = () => takeParam("cloud_error");
const takeCloudJoin = () => takeParam("cloud_join");

// Whether the address carries a cloud sign-in's outcome for this page to
// show: a share view that opens anyway then shows its sign-in page.
export function cloudOutcomeInUrl() {
  const q = new URLSearchParams(window.location.search);
  return q.has("cloud_error") || q.has("cloud_join");
}

// This page's address to come back to after a sign-in, without an earlier
// sign-in's outcome (read before the page drops it from the address bar).
function hereWithoutOutcome() {
  const q = new URLSearchParams(window.location.search);
  q.delete("cloud_error");
  q.delete("cloud_join");
  const search = q.toString();
  return window.location.pathname + (search ? `?${search}` : "");
}

const JOIN_OUTCOMES = {
  sent: T("Asked to join. Sign in again once your request is approved."),
  waiting: T("Your request to join is still waiting. Sign in again once it is approved."),
  declined: T("Your request to join was declined."),
};

// `demo` (a demo server, GET /api/server-config): a small landing page — the
// product pictured beside what it does — that leads with Try the demo (the
// guest login) and a line saying how long the workspace lasts; the password
// form (and the cloud sign-in) fold behind an Admin sign-in link, collapsed
// until asked for. `guestSeeded`: guests start with a sample library
// (GAMMA_GUEST_SEED), which the landing then promises. docs/dev/guests.md
// "Demo mode".
// `error`: a message, or {text, field: "password"} when the password was
// refused — that field then takes the focus and a red border. `onBack`
// returns to what the visitor was reading (a share view's own Sign in).
// `readOnly`: a hosted server whose plan lapsed (server-config's
// `read_only`): signing in still works, to read and export.
// `cloudLogin.lead` (a Gamma Cloud server: a hosted container or the shared
// server, whose people all sign in through Gamma Cloud): the page is that
// one button, and the password form folds behind Admin sign-in.
// `cloudLogin.join_requests` (the invited policy): a newcomer's Gamma Cloud
// sign-in asks to join, which the foot says, and to whom — the person who
// shared the page on a share link's gate (`forShare`), else the admin.
export function LoginPage({
  username,
  password,
  error,
  onUsernameChange,
  onPasswordChange,
  onSubmit,
  onGuestLogin,
  cloudLogin,
  subtitle,
  demo = false,
  guestSeeded = false,
  guestTtlHours,
  readOnly = false,
  next = hereWithoutOutcome(),
  forShare = false,
  onBack,
}) {
  const [cloudError] = React.useState(takeCloudError);
  const [cloudJoin] = React.useState(takeCloudJoin);
  const joinOutcome = JOIN_OUTCOMES[cloudJoin] ? (cloudJoin === "declined"
    ? <LoginError>{t(JOIN_OUTCOMES[cloudJoin])}</LoginError>
    : <LoginNotice>{t(JOIN_OUTCOMES[cloudJoin])}</LoginNotice>) : null;
  const asksToJoin = !!cloudLogin?.enabled && !!cloudLogin.join_requests;
  const joinFoot = asksToJoin ? (
    <p className="loginFoot">
      {forShare
        ? t("New here? Signing in with Gamma Cloud asks the person who shared this page to let you in. They see your Gamma Cloud username and e-mail address.")
        : t("New here? Signing in with Gamma Cloud asks this server's admin to let you in. They see your Gamma Cloud username and e-mail address.")}
    </p>
  ) : null;
  const leadsWithDemo = demo && !!onGuestLogin;
  const leadsWithCloud = !leadsWithDemo && !!cloudLogin?.enabled && !!cloudLogin.lead;
  const [signInOpen, setSignInOpen] = React.useState(false);
  const errorText = typeof error === "string" ? error : error?.text || "";
  const badPassword = error?.field === "password";
  const passwordRef = React.useRef(null);
  React.useEffect(() => {
    if (!badPassword) return;
    const input = passwordRef.current;
    input?.focus();
    // the field shakes once, a small no, on each refusal (motion.css .shakeNo)
    const field = input?.closest(".pwField");
    if (!field) return;
    field.classList.remove("shakeNo");
    void field.offsetWidth;
    field.classList.add("shakeNo");
  }, [error, badPassword]);
  // on a Gamma Cloud server a refused password keeps the folded form it was typed in open
  React.useEffect(() => { if (errorText && leadsWithCloud) setSignInOpen(true); }, [errorText, leadsWithCloud]);
  const hours = Number(guestTtlHours) || 0;
  const readOnlyNote = readOnly ? (
    <LoginError>{t("This server is read-only: you can sign in to read and export your data, but nothing can be changed.")}</LoginError>
  ) : null;
  const cloudButton = cloudLogin?.enabled ? (
    <a className={`loginCloudBtn${leadsWithCloud ? " lead" : ""}`} href={`/api/auth/cloud/start?next=${encodeURIComponent(next)}`}
      title={t("Sign in through {issuer}", { issuer: cloudLogin.issuer })}>
      <BrandMark size={18} />
      {t("Sign in with Gamma Cloud")}
    </a>
  ) : null;
  const passwordForm = (
    <form onSubmit={onSubmit}>
      <input
        type="text"
        value={username}
        onChange={(event) => onUsernameChange(event.target.value)}
        placeholder={t("Username")}
        className="loginInput"
        autoFocus
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        autoComplete="username"
      />
      <PasswordInput
        value={password}
        onChange={(event) => onPasswordChange(event.target.value)}
        placeholder={t("Password")}
        className={`loginInput${badPassword ? " invalid" : ""}`}
        aria-invalid={badPassword || undefined}
        inputRef={passwordRef}
        autoComplete="current-password"
      />
      {errorText ? <LoginError>{errorText}</LoginError> : null}
      <button type="submit" className="loginBtn" disabled={!username.trim() || !password.trim()}>
        {t("Log in")}
      </button>
      {onGuestLogin && !leadsWithDemo ? (
        <button type="button" className="loginGuestBtn" onClick={onGuestLogin}>
          {t("Continue as guest")}
          {hours ? (
            <span className="loginGuestNote">
              {tn("A private workspace for {n} hour, then deleted", "A private workspace for {n} hours, then deleted", hours)}
            </span>
          ) : null}
        </button>
      ) : null}
    </form>
  );
  const signIn = <>
    {readOnlyNote}
    {cloudButton ? <>
      {cloudButton}
      {cloudError ? <LoginError>{cloudError}</LoginError> : null}
      {joinOutcome}
      <div className="loginOr">{t("or use your account on this server")}</div>
    </> : <>{cloudError ? <LoginError>{cloudError}</LoginError> : null}{joinOutcome}</>}
    {passwordForm}
  </>;
  const adminSignIn = (
    <button type="button" className="loginDisclosure" aria-expanded={signInOpen}
      onClick={() => setSignInOpen((open) => !open)}>
      <Chevron open={signInOpen} dir="right" size={14} />
      {t("Admin sign-in")}
    </button>
  );
  const subtitleText = subtitle || t("Read papers, highlight, and keep what you learn — in one place.");
  if (leadsWithCloud) {
    return (
      <AuthShell>
        <p className="loginSubtitle">{subtitleText}</p>
        {readOnlyNote}
        {cloudButton}
        {cloudError ? <LoginError>{cloudError}</LoginError> : null}
        {joinOutcome}
        {adminSignIn}
        <Collapse open={signInOpen}><div className="loginDisclosureBody">{passwordForm}</div></Collapse>
        {onBack ? (
          <button type="button" className="loginDisclosure" onClick={onBack}>
            <ArrowLeftIcon size={14} />{t("Back to the shared page")}
          </button>
        ) : null}
        {joinFoot}
      </AuthShell>
    );
  }
  if (!leadsWithDemo) {
    return (
      <AuthShell>
        <p className="loginSubtitle">{subtitleText}</p>
        {signIn}
        {onBack ? (
          <button type="button" className="loginDisclosure" onClick={onBack}>
            <ArrowLeftIcon size={14} />{t("Back to the shared page")}
          </button>
        ) : null}
        {/* No self-service sign-up on a Gamma server (server-config's
            `registration` is always off); with Gamma Cloud sign-in on,
            whether a newcomer gets in is the admin's policy, so say
            nothing unless their sign-in asks to join. */}
        {cloudLogin?.enabled ? joinFoot : (
          <p className="loginFoot">{t("New here? Accounts are made by the person who runs this server.")}</p>
        )}
      </AuthShell>
    );
  }
  return (
    <AuthShell scene={DEMO_SCENE_URL}>
      <div className="loginBrand"><BrandMark size={36} />{t("Gamma")}</div>
      <h1 className="loginLandingTitle">{t("Read papers. Keep what you learn.")}</h1>
      <p className="loginLandingLead">
        {t("Highlight PDFs, take outliner notes with live math, and ask an AI about your papers — right in your browser.")}
      </p>
      <ul className="loginPoints">
        {guestSeeded ? <li><CheckIcon size={16} aria-hidden="true" />{t("A sample library is ready for you")}</li> : null}
        <li><CheckIcon size={16} aria-hidden="true" />{t("No sign-up, no install")}</li>
        <li><CheckIcon size={16} aria-hidden="true" />{t("Self-host it for your lab when you like it")}</li>
      </ul>
      <button type="button" className="loginBtn" onClick={onGuestLogin}>
        {t("Try the demo")}
      </button>
      <p className="loginDemoNote">
        {hours
          ? tn("Your own workspace for {n} hour, then it is deleted.", "Your own workspace for {n} hours, then it is deleted.", hours)
          : t("Your own workspace for a while, then it is deleted.")}
      </p>
      {errorText && !signInOpen ? <LoginError>{errorText}</LoginError> : null}
      {cloudError && !signInOpen ? <LoginError>{cloudError}</LoginError> : null}
      {!signInOpen ? joinOutcome : null}
      {adminSignIn}
      <Collapse open={signInOpen}><div className="loginDisclosureBody">{signIn}</div></Collapse>
    </AuthShell>
  );
}

// A share link this visitor can't open: unknown/turned off, or shared with
// specific people that don't include the signed-in account. A dead link
// points on: to this server's front door (`offerHome`) and, for a visitor
// who isn't signed in, to signing in (`onSignIn`); a page host offers neither.
export function ShareBlockedPage({ reason, viewer, onSwitchAccount, onSignIn, offerHome = false }) {
  const missing = reason !== "forbidden";
  return (
    <AuthShell headline={missing ? t("This link doesn't work") : t("Not shared with you")}>
      <p className="loginConflictText">
        {missing
          ? t("The page it pointed to was unshared, or the link was copied incompletely. Ask the person who sent it for a new link.")
          : t("This page is shared with specific people only{them}. Ask the owner to add your username.", { them: viewer ? t(", and {viewer} isn't one of them", { viewer }) : "" })}
      </p>
      {missing && offerHome ? (
        <button type="button" className="loginBtn" onClick={() => window.location.assign("/")}>{t("Go to Gamma")}</button>
      ) : null}
      {missing && onSignIn && !viewer ? (
        <button type="button" className="loginGuestBtn" onClick={onSignIn}>{t("Sign in to your library")}</button>
      ) : null}
      {!missing && onSwitchAccount ? (
        <button type="button" className="loginBtn" onClick={onSwitchAccount}>{t("Sign in as someone else")}</button>
      ) : null}
    </AuthShell>
  );
}
