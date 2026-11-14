import React from "react";
import { MorphText } from "../shared/ui/MotionWidgets";
import { API, apiJson, setExpectedUser } from "../shared/lib/utils";
import { AuthShell, LoginError, LoginNotice, LoginPage, SessionConflictPage } from "./LoginPage";
import { Section, Toggle } from "../settings/SettingsKit";
import { WorkspacePicker, accessBody, edits } from "../settings/WorkspacePicker";
import { Splash } from "../shared/ui/BrandMark";
import { CheckIcon, EyeIcon, LockIcon, MessageSquareIcon, PenIcon, ShieldIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

// A standalone sign-in gate: no workspace/session restoration runs while the
// user is reviewing a request. Keep the request URL through account changes.
// `requestId` is an MCP client's browser sign-in (?gamma_oauth=), `userCode`
// a setup command's (?gamma_device=, routers/integrations.py).
export function McpAuthorization({ requestId, userCode }) {
  const [user, setUser] = React.useState(null);
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState("");
  const [conflict, setConflict] = React.useState("");
  const acceptSession = (data) => {
    const next = data.user || false;
    setExpectedUser(next);
    setUser(next);
    if (next) {
      try { localStorage.setItem("gamma-active-user", next); } catch {}
    }
  };
  React.useEffect(() => {
    let active = true;
    apiJson(`${API}/session`).then((data) => { if (active) acceptSession(data); })
      .catch(() => { if (active) setUser(false); });
    return () => { active = false; setExpectedUser(null); };
  }, []);
  React.useEffect(() => {
    if (!user) return;
    let active = true;
    const check = (who) => {
      if (!active) return;
      if (!who) { setExpectedUser(null); setUser(false); setPassword(""); }
      else if (who !== user) setConflict(who);
    };
    const mismatch = (event) => check(event.detail?.user || "");
    const storage = (event) => { if (event.key === "gamma-active-user" && event.newValue !== null) check(event.newValue); };
    const focus = () => apiJson(`${API}/session`).then((data) => check(data.user)).catch(() => {});
    window.addEventListener("gamma-user-mismatch", mismatch);
    window.addEventListener("gamma-auth-expired", mismatch);
    window.addEventListener("storage", storage);
    window.addEventListener("focus", focus);
    return () => {
      active = false;
      window.removeEventListener("gamma-user-mismatch", mismatch);
      window.removeEventListener("gamma-auth-expired", mismatch);
      window.removeEventListener("storage", storage);
      window.removeEventListener("focus", focus);
    };
  }, [user]);
  const login = async (event) => {
    event.preventDefault(); setError("");
    try {
      await apiJson(`${API}/login`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }) });
      acceptSession(await apiJson(`${API}/session`));
      setPassword("");
    } catch (err) { setError(err.message || t("Login failed")); }
  };
  if (user === null) return <Splash />;
  if (!user) return <LoginPage username={username} password={password} error={error}
    onUsernameChange={setUsername} onPasswordChange={setPassword} onSubmit={login}
    subtitle={t("Sign in to connect your assistant")} />;
  if (conflict) return <SessionConflictPage tabUser={user} activeUser={conflict} onReload={() => window.location.reload()} />;
  return <McpConsent key={`${requestId || userCode}:${user}`} requestId={requestId} userCode={userCode} />;
}

// The consent page: the workspaces the connection may read, which of them it
// may also edit the notes of (View or Edit, as a share's people are given),
// and whether it may also answer the user's comments on work-folder files (reply and
// resolve: the `feedback` scope, docs/dev/feedback.md "Tokens and MCP"),
// ticked when the client asked for gamma:feedback. A setup command's
// sign-in (`userCode`) shows its code to check against the terminal's and
// ends on this page, where an MCP client's returns to the client.
export default function McpConsent({ requestId, userCode }) {
  const [details, setDetails] = React.useState(null);
  const [access, setAccess] = React.useState({ all: false, ids: [] });
  const [feedback, setFeedback] = React.useState(false);
  const [asks, setAsks] = React.useState(true);  // where it edits, whether each change asks first
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [done, setDone] = React.useState(null);  // a setup command's answer: "allowed" or "cancelled"
  const device = !requestId;
  React.useEffect(() => {
    let active = true;
    apiJson(device ? `${API}/integrations/device/request?user_code=${encodeURIComponent(userCode)}`
      : `${API}/integrations/oauth/request?request_id=${encodeURIComponent(requestId)}`)
      .then((data) => {
        if (!active) return;
        setDetails(data);
        setAccess({ all: false, ids: [data.default_workspace || data.workspaces[0]?.id].filter(Boolean) });
        setFeedback(!!data.feedback_asked);
      }).catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [device, requestId, userCode]);
  const decide = async (approve) => {
    setBusy(true); setError("");
    const body = { csrf: details.csrf, ...accessBody(access), feedback, asks, approve };
    try {
      const result = await apiJson(`${API}/integrations/${device ? "device" : "oauth"}/consent`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(device ? { ...body, user_code: userCode } : { ...body, request_id: requestId }),
      });
      if (device) { setDone(approve ? "allowed" : "cancelled"); setBusy(false); }
      else window.location.assign(result.redirect_url);
    } catch (err) { setError(err.message); setBusy(false); }
  };
  const switchAccount = async () => {
    setBusy(true); setError("");
    try {
      await apiJson(`${API}/logout`, { method: "POST" });
      window.location.reload();
    } catch (err) { setError(err.message); setBusy(false); }
  };
  const editing = edits(access);
  // The splash stays until the request is read: the card shows whole.
  if (!details && !error) return <Splash />;
  const name = details?.client_name || t("An assistant");
  // What the connection may do, as the choices above stand: one line each.
  const can = [
    [EyeIcon, t("Read pages, notes, highlights, and PDF text.")],
    editing ? [PenIcon, t("Create, edit and move notes in the workspaces marked Edit. Never deletes anything, or changes pages, folders or settings.")] : null,
    editing ? [ShieldIcon, asks ? t("Asks you before each change, in the assistant's own window; one that cannot ask changes nothing.")
      : t("Changes notes without asking you first.")] : null,
    feedback ? [MessageSquareIcon, t("Answer comments on your work folders' files: reply and resolve.")] : null,
    editing || feedback ? null : [LockIcon, t("Cannot edit or delete your library.")],
  ].filter(Boolean);
  return <AuthShell headline={details ? t("Connect {name}", { name }) : t("Connect your library")} className="mcpConsent">
    {details ? <p className="mcpWho">
      <span>{t("Signed in as {username}", { username: <strong>{details.username}</strong> })}</span>
      {done ? null : <button type="button" className="uiBtn ghost sm" disabled={busy} onClick={switchAccount}>{t("Use another account")}</button>}
    </p> : null}
    {done ? <LoginNotice icon={CheckIcon}>{done === "allowed"
      ? t("{name} is connected. Go back to your terminal: the setup finishes there.", { name })
      : t("{name} was not connected. You can close this page.", { name })}</LoginNotice>
    : details ? <>
      {device ? <div className="mcpCode">
        <span className="settingDesc">{t("Check that your terminal shows this code:")}</span>
        <strong>{details.user_code}</strong>
      </div> : null}
      <Section title={t("Workspaces it may read")}>
        <WorkspacePicker workspaces={details.workspaces} value={access} onChange={setAccess} disabled={busy}
          label={t("Workspaces it may read")} roles />
      </Section>
      <Section title={t("Permissions")}>
        {editing ? <Toggle icon={ShieldIcon} label={t("Ask me before each change it makes")} checked={asks} onChange={setAsks}
          disabled={busy} hint={t("Gamma's question shows in the assistant's own window.")} /> : null}
        <Toggle icon={MessageSquareIcon} label={t("Answer my comments on work-folder files")} checked={feedback}
          onChange={setFeedback} disabled={busy} hint={t("Reply to and resolve them")} />
      </Section>
      <Section title={editing ? t("Read and edit notes") : feedback ? t("Read and answer feedback") : t("Read-only access")}>
        <ul className="mcpCan">{can.map(([Icon, text]) => <li key={text}><Icon size={14} aria-hidden="true" />{text}</li>)}</ul>
      </Section>
      <p className="settingDesc mcpFoot">{t("Content the assistant reads is shared with its provider.")}{" "}
        {t("Access lasts 90 days. Change the workspaces or disconnect any time in Settings → AI → Integrations.")}</p>
      {device ? <p className="settingDesc mcpFoot">{t("Only allow it if you ran its setup command yourself and the codes match.")}</p>
        : <details className="mcpConnectionDetails"><summary>{t("Connection details")}</summary>
          <p className="settingDesc">{t("The assistant provided its name. Only approve if you started this connection.")}</p>
          <p className="settingDesc mcpCallback">{t("Returns to: {redirect_uri}", { redirect_uri: details.redirect_uri })}</p>
        </details>}
      {/* An answer's failure (an expired request, a session ended) shows
          above Allow, where the eye is, as the login form's above Log in. */}
      {error ? <LoginError>{error}</LoginError> : null}
      <div className="mcpConsentActions">
        <button className="uiBtn primary loginBtn" disabled={busy || !(access.all || access.ids.length)} onClick={() => decide(true)}>
          <MorphText>{busy ? t("Connecting…") : editing ? t("Allow reading and editing")
            : feedback ? t("Allow reading and answering") : t("Allow read-only access")}</MorphText>
        </button>
        <button className="uiBtn loginGuestBtn" disabled={busy} onClick={() => decide(false)}>{t("Cancel")}</button>
      </div>
    </> : error ? <LoginError>{error}</LoginError> : null}
  </AuthShell>;
}
