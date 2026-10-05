import React from "react";
import { PasswordInput } from "../settings/SettingsKit";
import { AlertCircleIcon, ArrowLeftIcon, CheckIcon, ChevronRightIcon } from "../shared/ui/Icons";
import { BrandMark } from "../shared/ui/BrandMark";
import { t, tn } from "../shared/i18n/i18n.js";

// The demo landing's picture: the README hero's paper and notes, cropped by
// tools/branding (design/brand/outputs.json) and served under /media/.
const DEMO_SCENE_URL = "/media/gamma-scene-light.svg";

// Every page shown before (or instead of) the app: login, loading, an
// unavailable workspace, a session conflict, a blocked share link. A page
// that reports a situation (`headline`) leads with it under the mark; the
// others with the name. `scene` (a demo server's landing) puts a picture
// beside the content instead, which then brings its own heading.
function AuthShell({ headline, scene, children }) {
  return (
    <div className="app">
      <div className="loginPage">
        {scene ? (
          <div className="loginCard loginLanding">
            <div className="loginScene"><img src={scene} alt="" /></div>
            <div className="loginLandingBody">{children}</div>
          </div>
        ) : (
          <div className="loginCard">
            <BrandMark className="loginMark" size={48} />
            {headline
              ? <h1 className="loginTitle loginHeadline">{headline}</h1>
              : <div className="loginTitle">{t("Gamma")}</div>}
            {children}
          </div>
        )}
      </div>
    </div>
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

// The cloud sign-in callback sends a refused sign-in back here as
// `?cloud_error=`: shown once, then dropped from the address bar.
function takeCloudError() {
  try {
    const url = new URL(window.location.href);
    const message = url.searchParams.get("cloud_error");
    if (!message) return "";
    url.searchParams.delete("cloud_error");
    window.history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
    return message;
  } catch { return ""; }
}

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
  next = window.location.pathname + window.location.search,
  onBack,
}) {
  const [cloudError] = React.useState(takeCloudError);
  const leadsWithDemo = demo && !!onGuestLogin;
  const [signInOpen, setSignInOpen] = React.useState(false);
  const errorText = typeof error === "string" ? error : error?.text || "";
  const badPassword = error?.field === "password";
  const passwordRef = React.useRef(null);
  React.useEffect(() => { if (badPassword) passwordRef.current?.focus(); }, [error, badPassword]);
  const hours = Number(guestTtlHours) || 0;
  const signIn = <>
    {readOnly ? (
      <LoginError>{t("This server is read-only: you can sign in to read and export your data, but nothing can be changed.")}</LoginError>
    ) : null}
    {cloudLogin?.enabled ? <>
      <a className="loginCloudBtn" href={`/api/auth/cloud/start?next=${encodeURIComponent(next)}`}
        title={t("Sign in through {issuer}", { issuer: cloudLogin.issuer })}>
        <BrandMark size={18} />
        {t("Sign in with Gamma Cloud")}
      </a>
      {cloudError ? <LoginError>{cloudError}</LoginError> : null}
      <div className="loginOr">{t("or use your account on this server")}</div>
    </> : cloudError ? <LoginError>{cloudError}</LoginError> : null}
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
  </>;
  const subtitleText = subtitle || t("Read papers, highlight, and keep what you learn — in one place.");
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
            whether a newcomer gets in is the admin's policy, so say nothing. */}
        {cloudLogin?.enabled ? null : (
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
      <button type="button" className="loginDisclosure" aria-expanded={signInOpen}
        onClick={() => setSignInOpen((open) => !open)}>
        <ChevronRightIcon size={14} className={`loginDisclosureChev ${signInOpen ? "open" : ""}`} />
        {t("Admin sign-in")}
      </button>
      {signInOpen ? <div className="loginDisclosureBody">{signIn}</div> : null}
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
