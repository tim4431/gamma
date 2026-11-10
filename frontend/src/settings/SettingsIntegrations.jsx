import React from "react";
import { guideEvents } from "../guide/events.js";
import { API, apiJson } from "../shared/lib/utils";
import { CopyField, PaneHead, Section, Row, Segmented, Step } from "./SettingsKit";
import { LinkIcon, KeyIcon, RefreshIcon, UnlinkIcon, ShieldIcon, FoldersIcon } from "../shared/ui/Icons";
import { codexSetupCommand, claudeConnectCommand, claudePluginInstallCommands, dshSetupCommand,
  claudeRemoveCommands, codexRemoveCommands, dshRemoveCommand } from "./assistantSetup";
import { WorkspacePicker, accessBody } from "./WorkspacePicker";
import { t } from "../shared/i18n/i18n.js";

const dateOf = (value) => new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
const timeOf = (value) => new Date(value).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
const ASSISTANTS = [["claude", t("Claude Code")], ["codex", t("Codex")], ["dsh", t("DeepSeek Harness")], ["other", t("Other")]];
const PLATFORMS = [["windows", t("Windows PowerShell")], ["unix", t("macOS / Linux")]];

// One connected assistant: how it signed in, what it may read, when it was
// connected and when its access lapses. OAuth connections are minted with an
// " (OAuth)" suffix on the client's name (mcp_oauth_provider.py).
function ConnectionRow({ item, names, busy, onEdit, onRevoke }) {
  const oauth = /\s\(OAuth\)$/.test(item.name);
  const name = oauth ? item.name.replace(/\s\(OAuth\)$/, "") : item.name;
  const daysLeft = Math.ceil((item.expires_at * 1000 - Date.now()) / 86400000);
  const expiry = daysLeft <= 0 ? t("Expired {date}", { date: dateOf(item.expires_at * 1000) })
    : t("Expires {expires_at} ({days} left)", { expires_at: dateOf(item.expires_at * 1000), days: daysLeft === 1 ? "1 day" : `${daysLeft} days` });
  const spaces = item.workspaces.map((id) => names.get(id)).filter(Boolean);
  const reads = item.all_workspaces ? t("Reads all workspaces")
    : spaces.length ? t("Reads {workspaces}", { workspaces: spaces.join(", ") }) : t("Reads no workspace");
  const parts = [oauth ? t("Browser sign-in") : t("Token"), item.scope === "write" ? t("Read and write") : reads,
    item.created_at ? t("Connected {date}", { date: dateOf(item.created_at) }) : null, expiry].filter(Boolean);
  return <Row icon={oauth ? LinkIcon : KeyIcon} label={name}
    hint={<>{parts.join(" · ")}{daysLeft <= 0 ? <span className="uiTag warn">{t("Expired")}</span> : null}</>}>
    <div className="integrationActions">
      {item.scope === "read" ? <button type="button" className="uiBtn sm iconSq" disabled={busy} aria-label={t("Choose workspaces")}
        title={t("Choose the workspaces {name} may read", { name })} onClick={() => onEdit(item)}>
        <FoldersIcon size={16} />
      </button> : null}
      <button type="button" className="uiBtn sm iconSq danger" disabled={busy} aria-label={t("Disconnect")}
        title={t("Disconnect {name}: the assistant loses access", { name })} onClick={() => onRevoke(item)}>
        <UnlinkIcon size={16} />
      </button>
    </div>
  </Row>;
}

export function IntegrationSettings({ workspaceId }) {
  const [data, setData] = React.useState(null);
  const [assistant, setAssistant] = React.useState("claude");
  const [platform, setPlatform] = React.useState(() => /Windows/i.test(navigator.userAgent) ? "windows" : "unix");
  // What DeepSeek Harness and a new token may read; the open workspace to start.
  const [access, setAccess] = React.useState({ all: false, ids: [workspaceId] });
  const [setup, setSetup] = React.useState(null);  // {code, until}: a dsh setup command's one-time code
  const [editing, setEditing] = React.useState(null);  // {id, access}: a connection's workspaces, being changed
  const [name, setName] = React.useState(t("Script"));
  const [scope, setScope] = React.useState("read");
  const [secret, setSecret] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [message, setMessage] = React.useState("");
  const [loadError, setLoadError] = React.useState("");
  const endpoint = `${API}/integrations/tokens`;
  const loadVersion = React.useRef(0);
  // The connections the last read listed: one more since, after the user
  // ran an assistant's setup and allowed it, is the setup guide's step.
  const known = React.useRef(null);
  const refresh = React.useCallback(async (notice = "") => {
    const version = ++loadVersion.current;
    setMessage(notice);
    try {
      const value = await apiJson(endpoint);
      // A focus refresh started before a revocation must not restore its row.
      if (version !== loadVersion.current) return;
      const ids = new Set((value.tokens || []).map((item) => item.id));
      if (known.current && [...ids].some((id) => !known.current.has(id))) {
        guideEvents.emit("mcp.connected");
        setSetup(null);  // its code is spent: the connection it made is listed
      }
      known.current = ids;
      setData(value); setLoadError("");
    } catch (err) {
      if (version === loadVersion.current) setLoadError(err.message);
    }
  }, [endpoint]);
  React.useEffect(() => {
    setData(null); setSecret(null); setSetup(null); setEditing(null); setMessage(""); setLoadError("");
    const load = () => refresh();
    load();
    window.addEventListener("focus", load);
    const visible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      ++loadVersion.current;
      window.removeEventListener("focus", load);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);
  const run = async (work) => {
    setBusy(true); setMessage("");
    try { await work(); } catch (err) { setMessage(err.message); } finally { setBusy(false); }
  };
  const post = (url, body) => apiJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const create = () => run(async () => {
    setSecret(await post(endpoint, { name: name.trim(), scope, ...(scope === "read" ? accessBody(access) : {}) }));
    await refresh();
  });
  const makeSetup = () => run(async () => {
    const value = await post(`${API}/integrations/setup-codes`, { name: t("DeepSeek Harness"), ...accessBody(access) });
    setSetup({ code: value.code, until: Date.now() + value.expires_in * 1000 });
  });
  const saveAccess = () => run(async () => {
    await apiJson(`${endpoint}/${editing.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(accessBody(editing.access)) });
    setEditing(null);
    await refresh();
  });
  const revoke = ({ id, name: connectionName }) => run(async () => {
    await apiJson(`${endpoint}/${id}`, { method: "DELETE" });
    if (secret?.id === id) setSecret(null);
    if (editing?.id === id) setEditing(null);
    // DELETE succeeded even if reloading the remaining connections fails.
    setData((value) => value ? { ...value, tokens: value.tokens.filter((item) => item.id !== id) } : value);
    await refresh(t("Access revoked for the selected “{connectionName}” connection.", { connectionName }));
  });
  const chooseAccess = (value) => { setAccess(value); setSetup(null); };
  const spaces = data?.workspaces || [];
  const names = new Map(spaces.map((w) => [w.id, w.name]));
  const picking = spaces.length > 1;
  const chosen = access.all || access.ids.length > 0;
  const live = setup && Date.now() < setup.until;
  const platformPick = <div role="group" aria-label={t("Terminal platform")}>
    <Segmented value={platform} onChange={setPlatform} options={PLATFORMS} />
  </div>;
  const picker = (label) => <WorkspacePicker workspaces={spaces} value={access} onChange={chooseAccess} disabled={busy} label={label} />;
  // Claude Code, Codex and other assistants sign in through the browser,
  // which needs a confirmed address; DeepSeek Harness connects with a token.
  const noSignIn = data && !data.oauth_available ? <div className="integrationDetails">
    <p>{t("Browser sign-in is not available for this Gamma address yet.")}</p>
    <p>{t("An administrator can enable it by confirming the public server URL in Settings → Server. DeepSeek Harness and tokens work without it.")}</p>
    <details><summary>{t("Server setup details")}</summary><p>{data.oauth_error}</p></details>
  </div> : null;
  const signInStep = (n, who) => <Step n={n} title={t("Choose what it may read")}
    hint={t("{who} opens Gamma in your browser. Sign in, tick the workspaces it may read, and allow it. You can change them here later.", { who })} />;
  // How to take Gamma out of an assistant again, folded under its last step.
  const removal = (who, hint, label, value) => <details>
    <summary>{t("Remove Gamma from {assistant}", { assistant: who })}</summary>
    <p>{hint}</p>
    <CopyField key={label} label={label} value={value} action="Copy remove commands" rows={3} />
    <p className="settingDesc">{t("Then disconnect it under Connected assistants: that ends its access.")}</p>
  </details>;
  const steps = !data ? null : assistant === "dsh" ? <>
    {picking ? <Step n={1} title={t("Choose what it may read")}
      hint={t("DeepSeek Harness reads these workspaces. You can change them here later.")}>
      {picker(t("Workspaces DeepSeek Harness may read"))}
    </Step> : null}
    <Step n={picking ? 2 : 1} title={t("Run the setup command")}
      hint={t("Quit DeepSeek Harness first, then run this in a terminal on the same computer. It adds the Gamma plugin to DeepSeek Harness and connects it to this server. The command works once.")}>
      {platformPick}
      {live ? <>
        <CopyField key={`dsh-${platform}`} label={t("DeepSeek Harness setup command")} value={dshSetupCommand(data.mcp_url, setup.code, platform)}
          action="Copy setup command" rows={platform === "windows" ? 3 : 4} />
        <p className="settingDesc">{t("Valid until {time}. Requires {dsh}.", { time: timeOf(setup.until),
          dsh: <a href="https://github.com/deepseek-ai/deepseek-harness" target="_blank" rel="noreferrer">{t("DeepSeek Harness")}</a> })}</p>
      </> : <button className="uiBtn" disabled={busy || !chosen} onClick={makeSetup}>
        {setup ? t("Show a new setup command") : t("Show the setup command")}
      </button>}
    </Step>
    <Step n={picking ? 3 : 2} title={t("Open DeepSeek Harness")}
      hint={t("Ask about your papers, or paste a Gamma page or share link with your question. Gamma's tools appear as mcp__gamma__….")}>
      {removal(t("DeepSeek Harness"), t("Quit DeepSeek Harness, then run this in a terminal. It takes the Gamma plugin out of every DeepSeek Harness profile that has it and deletes the saved connection."),
        t("DeepSeek Harness remove command"), dshRemoveCommand(platform))}
    </Step>
  </> : noSignIn || <>
    {assistant === "claude" ? <>
      <Step n={1} title={t("Run this in a terminal")}
        hint={t("On the computer where you use Claude Code. If Gamma is already connected at this address, go on to the next step.")}>
        {platformPick}
        <CopyField key="claude-connect" label={t("Claude Code connection command")} value={claudeConnectCommand(data.mcp_url, platform)} action="Copy connection command" />
      </Step>
      <Step n={2} title={t("Sign in from Claude Code")}
        hint={t("Start Claude Code, run /mcp, select gamma and choose Authenticate. Gamma opens in your browser: tick the workspaces it may read, and allow it.")} />
      <Step n={3} title={t("Ask about your papers")}
        hint={t("Start a new session and paste a Gamma page or share link with your question, or mention a paper by name.")}>
        <details>
          <summary>{t("Optional: the /gamma:gamma workflow")}</summary>
          <p>{t("Download the Claude Code plugin ZIP from {release} and extract it into a permanent folder. Open a terminal in the folder containing {folder} and run:", {
            release: <a href="https://github.com/tim4431/Gamma/releases/latest" target="_blank" rel="noreferrer">{t("Gamma's latest release")}</a>, folder: <code>gamma-marketplace</code> })}</p>
          <CopyField label={t("Claude Code plugin install commands")} value={claudePluginInstallCommands} action="Copy plugin install commands" rows={3} />
          <p className="settingDesc">{t("Choose the asset named {name} followed by the version and {ext}. Keep the extracted folder after installing.", {
            name: <code>gamma-claude-code-plugin</code>, ext: <code>.zip</code> })}</p>
        </details>
        <details>
          <summary>{t("Changed the server address?")}</summary>
          <p>{t("Open Gamma at its new address, then copy these commands to replace the connection. Restart Claude Code and sign in again through {mcp}.", { mcp: <code>/mcp</code> })}</p>
          <CopyField label={t("Claude Code change server commands")} value={claudeConnectCommand(data.mcp_url, platform, { replace: true })} action="Copy change server commands" rows={3} />
        </details>
        {removal(t("Claude Code"), t("Run these in a terminal. The last two are for the /gamma:gamma plugin, if you installed it."),
          t("Claude Code remove commands"), claudeRemoveCommands)}
      </Step>
    </> : assistant === "codex" ? <>
      <Step n={1} title={t("Run this in a terminal")}
        hint={t("On the computer where you use Codex. It installs the Gamma PDF plugin, adds this server and opens Gamma's sign-in.")}>
        {platformPick}
        <CopyField key="codex" label={t("Codex setup command")} value={codexSetupCommand(data.mcp_url, platform)} action="Copy setup command" rows={4} />
        <p className="settingDesc">{t("Requires the {codex}.", {
          codex: <a href="https://learn.chatgpt.com/docs/cli" target="_blank" rel="noreferrer">{t("Codex CLI")}</a> })}</p>
      </Step>
      {signInStep(2, t("Codex"))}
      <Step n={3} title={t("Start a new chat")}
        hint={t("Paste a Gamma page or share link with your question, or mention @Gamma PDF. If sign-in was interrupted, run codex mcp login gamma.")}>
        {removal(t("Codex"), t("Run these in a terminal: they remove the connection, the Gamma PDF plugin and its marketplace."),
          t("Codex remove commands"), codexRemoveCommands)}
      </Step>
    </> : <>
      <Step n={1} title={t("Add Gamma to your assistant")}
        hint={t("In your assistant's settings, add an MCP server (Streamable HTTP) with this URL.")}>
        <CopyField key="url" label={t("Gamma MCP server URL")} value={data.mcp_url} action="Copy server URL" />
      </Step>
      {signInStep(2, t("Your assistant"))}
      <p className="settingDesc">{t("If your assistant cannot sign in through the browser, give it a token from Tokens (advanced) below.")}</p>
    </>}
  </>;
  return <>
    <PaneHead icon={LinkIcon} title={t("Integrations")}>{t("Let Claude Code, Codex, DeepSeek Harness or another MCP assistant read your library. One connection can read several workspaces; it never changes them.")}</PaneHead>
    {loadError ? <div className="integrationDetails" role="alert">
      <p>{t("Could not load your connections. {loadError}", { loadError: loadError })}</p>
      <button className="uiBtn" onClick={() => refresh()}>{t("Try again")}</button>
    </div> : null}
    <Section title={t("Connect an assistant")} guide="settings.assistantSetup">
      {data ? <div className="integrationDetails">
        <div role="group" aria-label={t("Assistant")}>
          <Segmented value={assistant} onChange={setAssistant} options={ASSISTANTS} />
        </div>
        {steps}
      </div> : !loadError ? <p role="status">{t("Loading connection settings…")}</p> : null}
    </Section>
    <Section title={t("Connected assistants")} guide="settings.assistantAccess" action={
      <button type="button" className="uiBtn sm iconSq" aria-label={t("Refresh connections")} title={t("Refresh connections")} onClick={() => refresh()}>
        <RefreshIcon size={16} />
      </button>}>
      {data ? data.tokens.length ? data.tokens.map((item) => <React.Fragment key={item.id}>
        <ConnectionRow item={item} names={names} busy={busy} onRevoke={revoke}
          onEdit={(row) => setEditing(editing?.id === row.id ? null : { id: row.id, access: { all: row.all_workspaces, ids: row.workspaces } })} />
        {editing?.id === item.id ? <div className="integrationDetails integrationEdit">
          <WorkspacePicker workspaces={spaces} value={editing.access} disabled={busy}
            label={t("Workspaces {name} may read", { name: item.name })} onChange={(value) => setEditing({ ...editing, access: value })} />
          <div className="integrationActions">
            <button className="uiBtn primary" disabled={busy || !(editing.access.all || editing.access.ids.length)} onClick={saveAccess}>{t("Save")}</button>
            <button className="uiBtn" disabled={busy} onClick={() => setEditing(null)}>{t("Cancel")}</button>
          </div>
        </div> : null}
      </React.Fragment>)
        : <div className="integrationDetails"><p>{t("No assistant is connected yet.")}</p></div> : null}
    </Section>
    {message ? <p role="status">{message}</p> : null}
    <details className="integrationAdvanced">
      <summary>{t("Tokens (advanced)")}</summary>
      <div className="integrationDetails"><p>{t("For an assistant without browser sign-in, or a script: it sends the token as an Authorization: Bearer header. Gamma shows a token once.")}</p></div>
      <Section title={t("Create a token")}>
        <Row icon={KeyIcon} label={t("Connection name")} hint={t("Expires after 90 days.")}>
          <div className="integrationCreateControls">
            <input className="aiKeyInput" aria-label={t("Connection name")} value={name} maxLength={80}
              onChange={(event) => setName(event.target.value)} />
            <button className="uiBtn" disabled={busy || !data || !name.trim() || !!secret || (scope === "read" && !chosen)} onClick={create}>{t("Create token")}</button>
          </div>
        </Row>
        <Row icon={ShieldIcon} label={t("Scope")} hint={scope === "write"
          ? t("Read and write: what an offline copy of this workspace on another Gamma (Settings → Workspaces → Clones and Zotero links there) signs in with. Assistants only need read.") : t("Read-only: assistants and scripts. Choose “Read and write” for an offline copy of this workspace on another Gamma.")}>
          <Segmented value={scope} onChange={setScope} options={[["read", t("Read-only")], ["write", t("Read and write")]]} />
        </Row>
        {scope === "read" && picking ? <Row icon={FoldersIcon} label={t("Workspaces")} hint={t("What an assistant with this token may read. The HTTP API reads the first one.")} /> : null}
        {scope === "read" && picking ? picker(t("Workspaces the token may read")) : null}
        {secret ? <div className="integrationDetails">
          <p>{t("Copy this token now. Gamma will not show it again. Keep it private.")}</p>
          <CopyField label={t("New integration token")} value={secret.token} action="Copy token" />
          <div className="integrationActions">
            <button className="uiBtn" onClick={() => setSecret(null)}>{t("Done")}</button>
          </div>
        </div> : null}
      </Section>
    </details>
  </>;
}
