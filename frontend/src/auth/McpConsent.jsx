import React from "react";
import { MorphText } from "../shared/ui/MotionWidgets";
import { API, apiJson, setExpectedUser } from "../shared/lib/utils";
import { LoginPage, SessionConflictPage } from "./LoginPage";
import { WorkspacePicker, accessBody, edits } from "../settings/WorkspacePicker";
import { Splash } from "../shared/ui/BrandMark";
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
    const body = { csrf: details.csrf, ...accessBody(access), feedback, approve };
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
  return <div className="app mcpConsentPage"><div className="loginPage"><div className="loginCard mcpConsent">
    <div className="loginTitle">{t("Gamma")}</div>
    <h1 className="loginSubtitle">{t("Connect your library")}</h1>
    {done ? <p role="status">{done === "allowed"
      ? t("{name} is connected. Go back to your terminal: the setup finishes there.", { name: details.client_name })
      : t("{name} was not connected. You can close this page.", { name: details.client_name })}</p>
    : details ? <>
      <p><strong>{details.client_name}</strong> {t("wants to connect to Gamma.")}</p>
      {device ? <p className="mcpDeviceCode">{t("Check that your terminal shows this code:")} <strong>{details.user_code}</strong></p> : null}
      <p className="loginConflictHint">{t("Signed in as")} <strong>{details.username}</strong>.</p>
      <label>{t("Workspaces it may read")}</label>
      <WorkspacePicker workspaces={details.workspaces} value={access} onChange={setAccess} disabled={busy}
        label={t("Workspaces it may read")} roles />
      <label className="mcpFeedback">
        <input type="checkbox" checked={feedback} disabled={busy} onChange={(e) => setFeedback(e.target.checked)} />
        {" "}{t("Also let it answer my comments on work-folder files (reply and resolve)")}
      </label>
      <div className="mcpPermissions">
        <strong>{editing ? t("Read and edit notes") : feedback ? t("Read and answer feedback") : t("Read-only access")}</strong>
        <ul><li>{t("Read pages, notes, highlights, and PDF text.")}</li>
          {editing ? <li>{t("Create, edit and move notes in the workspaces marked Edit. Never deletes anything, or changes pages, folders or settings.")}</li> : null}
          {feedback ? <li>{t("Answer comments on your work folders' files: reply and resolve.")}</li> : null}
          {editing || feedback ? null : <li>{t("Cannot edit or delete your library.")}</li>}</ul>
        <p className="loginConflictHint">{t("Content the assistant reads is shared with its provider.")}</p>
      </div>
      <p className="loginConflictHint">{t("Access lasts 90 days. Change the workspaces or disconnect any time in Settings → AI → Integrations.")}</p>
      {device ? <p className="loginConflictHint">{t("Only allow it if you ran its setup command yourself and the codes match.")}</p>
        : <details className="mcpConnectionDetails"><summary>{t("Connection details")}</summary>
          <p className="loginConflictHint">{t("The assistant provided its name. Only approve if you started this connection.")}</p>
          <p className="loginConflictHint mcpCallback">{t("Returns to: {redirect_uri}", { redirect_uri: details.redirect_uri })}</p>
        </details>}
      <div className="mcpConsentActions">
      <button className="loginBtn" disabled={busy || !(access.all || access.ids.length)} onClick={() => decide(true)}>
        <MorphText>{busy ? t("Connecting…") : editing ? t("Allow reading and editing")
          : feedback ? t("Allow reading and answering") : t("Allow read-only access")}</MorphText>
      </button>
      <button className="loginGuestBtn" disabled={busy} onClick={() => decide(false)}>{t("Cancel")}</button>
      </div>
    </> : null}
    {error ? <p className="loginError" role="alert">{error}</p> : null}
    {done ? null : <button className="loginGuestBtn" disabled={busy} onClick={switchAccount}>{t("Use another account")}</button>}
  </div></div></div>;
}
