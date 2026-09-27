import React from "react";
import { API, apiJson, copyText } from "../shared/lib/utils";
import { PaneHead, Section, Row, Segmented, Step } from "./SettingsKit";
import { LinkIcon, KeyIcon, CopyIcon, CheckIcon, RefreshIcon, UnlinkIcon, ShieldIcon } from "../shared/ui/Icons";
import { codexSetupCommand, claudeConnectCommand, claudePluginInstallCommands, dshInstallCommand, dshStartCommand } from "./assistantSetup";
import { t } from "../shared/i18n/i18n.js";

const COPIED = t("Copied. You can paste it now.");
const COPY_MANUALLY = t("Select the text above and copy it manually.");

// A read-only code box with the copy button in its corner. A successful copy
// swaps the icon for a check; the status text is visible only when the
// clipboard was refused (the check already says the rest, but the sentence
// stays in the DOM for assistive tech).
function CopyField({ label, value, action, rows = 2 }) {
  const [status, setStatus] = React.useState("");
  React.useEffect(() => setStatus(""), [value]);
  React.useEffect(() => {
    if (status !== COPIED) return undefined;
    const timer = setTimeout(() => setStatus(""), 2000);
    return () => clearTimeout(timer);
  }, [status]);
  const copy = async () => {
    try { setStatus(await copyText(value) ? COPIED : COPY_MANUALLY); }
    catch { setStatus(COPY_MANUALLY); }
  };
  const copied = status === COPIED;
  return <div className="integrationDetails">
    <div className="integrationCode">
      <textarea className="aiKeyInput" aria-label={label} readOnly rows={rows} value={value}
        onFocus={(event) => event.target.select()} />
      <button type="button" className={`uiBtn sm iconSq integrationCopy${copied ? " on" : ""}`} aria-label={action} title={action} onClick={copy}>
        {copied ? <CheckIcon size={13} /> : <CopyIcon size={13} />}
      </button>
    </div>
    <span className={`settingDesc integrationCopyStatus${copied ? " srOnly" : ""}`} role="status">{status}</span>
  </div>;
}

const dateOf = (seconds) => new Date(seconds * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

// One connected assistant: how it signed in, what it may do, when it was
// connected and when its access lapses. OAuth connections are minted with an
// " (OAuth)" suffix on the client's name (mcp_oauth_provider.py).
function ConnectionRow({ item, busy, onRevoke }) {
  const oauth = /\s\(OAuth\)$/.test(item.name);
  const name = oauth ? item.name.replace(/\s\(OAuth\)$/, "") : item.name;
  const daysLeft = Math.ceil((item.expires_at * 1000 - Date.now()) / 86400000);
  const expiry = daysLeft <= 0 ? `Expired ${dateOf(item.expires_at)}`
    : t("Expires {expires_at} ({days} left)", { expires_at: dateOf(item.expires_at), days: daysLeft === 1 ? "1 day" : `${daysLeft} days` });
  const connected = item.created_at ? `Connected ${new Date(item.created_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}` : null;
  const parts = [oauth ? t("Browser sign-in") : t("Token"), item.scope === "write" ? t("Read and write") : t("Read-only"), connected, expiry].filter(Boolean);
  return <Row icon={oauth ? LinkIcon : KeyIcon} label={name}
    hint={<>{parts.join(" · ")}{daysLeft <= 0 ? <span className="uiTag warn">{t("Expired")}</span> : null}</>}>
    <button type="button" className="uiBtn sm iconSq danger" disabled={busy} aria-label={t("Disconnect")}
      title={t("Disconnect {name}: the assistant loses access to this workspace", { name: name })} onClick={() => onRevoke(item)}>
      <UnlinkIcon size={13} />
    </button>
  </Row>;
}

export function IntegrationSettings({ workspaceId }) {
  const [data, setData] = React.useState(null);
  const [name, setName] = React.useState(t("Codex"));
  const [scope, setScope] = React.useState("read");
  const [secret, setSecret] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [message, setMessage] = React.useState("");
  const [method, setMethod] = React.useState("settings");
  const [platform, setPlatform] = React.useState(() => /Windows/i.test(navigator.userAgent) ? "windows" : "unix");
  const [loadError, setLoadError] = React.useState("");
  const endpoint = `${API}/integrations/tokens`;
  const loadVersion = React.useRef(0);
  const refresh = React.useCallback(async (notice = "") => {
    const version = ++loadVersion.current;
    setMessage(notice);
    try {
      const value = await apiJson(endpoint);
      // A focus refresh started before a revocation must not restore its row.
      if (version !== loadVersion.current) return;
      setData(value); setLoadError("");
    } catch (err) {
      if (version === loadVersion.current) setLoadError(err.message);
    }
  }, [endpoint]);
  React.useEffect(() => {
    setData(null); setSecret(null); setMessage(""); setLoadError("");
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
  // `origin` is where the one-time token is shown: the manual section, or the
  // DeepSeek Harness tab, which connects only with a token.
  const create = async (tokenName, tokenScope, origin) => {
    setBusy(true); setMessage("");
    try {
      const value = await apiJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: tokenName, scope: tokenScope }) });
      setSecret({ ...value, origin });
      await refresh();
    } catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  };
  const revoke = async ({ id, name: connectionName }) => {
    setBusy(true); setMessage("");
    try {
      await apiJson(`${API}/integrations/tokens/${id}`, { method: "DELETE" });
      if (secret?.id === id) setSecret(null);
      // DELETE succeeded even if reloading the remaining connections fails.
      setData((value) => value ? { ...value, tokens: value.tokens.filter((item) => item.id !== id) } : value);
      await refresh(t("Access revoked for the selected “{connectionName}” connection.", { connectionName }));
    } catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  };
  const config = data ? `[mcp_servers.gamma]\nurl = ${JSON.stringify(data.mcp_url)}\nbearer_token_env_var = "GAMMA_TOKEN"` : "";
  const setup = data ? codexSetupCommand(data.mcp_url, platform) : "";
  const isClaude = method === "claude";
  const isDsh = method === "dsh";
  const tokenField = <div className="integrationDetails">
    <p>{t("Copy this token now. Gamma will not show it again. Keep it private.")}</p>
    <CopyField label={t("New integration token")} value={secret?.token ?? ""} action="Copy token" />
    <div className="integrationActions">
      <button className="uiBtn" onClick={() => setSecret(null)}>{t("Done")}</button>
    </div>
  </div>;
  return <>
    <PaneHead icon={LinkIcon} title={t("Integrations")}>{t("Read-only access for Codex, Claude Code, DeepSeek Harness, or any MCP assistant.")}</PaneHead>
    {loadError ? <div className="integrationDetails" role="alert">
      <p>{t("Could not load your connections. {loadError}", { loadError: loadError })}</p>
      <button className="uiBtn" onClick={() => refresh()}>{t("Try again")}</button>
    </div> : null}
    <Section title={t("Connect an assistant")}>
      {data ? <div className="integrationDetails">
        {data.oauth_available ? <>
          <div role="group" aria-label={t("Connection method")}>
            <Segmented value={method} onChange={setMethod} options={[["settings", t("Any assistant")], ["terminal", t("Codex CLI")], ["claude", t("Claude Code")], ["dsh", t("DeepSeek Harness")]]} />
          </div>
          {isDsh ? <>
            <Step n={1} title={t("Create a token")}
              hint={t("DeepSeek Harness has no browser sign-in. It connects with a read-only token for this workspace, which expires after 90 days.")}>
              {secret?.origin === "dsh" ? tokenField
                : <button className="uiBtn" disabled={busy || !!secret} onClick={() => create(t("DeepSeek Harness"), "read", "dsh")}>{t("Create token")}</button>}
            </Step>
            <Step n={2} title={t("Install the Gamma plugin")}
              hint={t("Run this in a terminal on the computer where you use DeepSeek Harness. It downloads the plugin from Gamma's latest release into your dsh home and adds it to the web profile; run it again to update.")}>
              <div role="group" aria-label={t("Terminal platform")}>
                <Segmented value={platform} onChange={setPlatform} options={[["windows", t("Windows PowerShell")], ["unix", t("macOS / Linux")]]} />
              </div>
              <CopyField key="dsh-install" label={t("DeepSeek Harness plugin install command")} value={dshInstallCommand(platform)} action="Copy install command" rows={platform === "windows" ? 5 : 3} />
              <p className="settingDesc">{t("Requires {dsh} and {pnpm}. For another dsh profile, change {flag}.", {
                dsh: <a href="https://github.com/deepseek-ai/deepseek-harness" target="_blank" rel="noreferrer">{t("DeepSeek Harness")}</a>,
                pnpm: <a href="https://pnpm.io/installation" target="_blank" rel="noreferrer">pnpm</a>, flag: <code>--profile web</code> })}</p>
            </Step>
            <Step n={3} title={t("Start dsh with Gamma")}
              hint={t("Paste the token when asked. Then paste a Gamma page or share link with your question; Gamma's tools appear as mcp__gamma__….")}>
              <CopyField key="dsh-start" label={t("DeepSeek Harness start command")} value={dshStartCommand(data.mcp_url, platform)} action="Copy start command" rows={3} />
              <p className="settingDesc">{t("Without {env}, dsh starts without Gamma. When the token expires after 90 days, create a new one here.", { env: <code>GAMMA_URL</code> })}</p>
            </Step>
          </> : <>
            <Step n={1} title={method === "settings" ? t("Add Gamma to your assistant") : isClaude ? t("Connect Claude Code to Gamma") : t("Install and connect Gamma PDF")}
              hint={method === "settings" ? t("In your assistant's settings, add an MCP server with this URL.") : isClaude
                ? t("Run this command in a terminal on the computer where you use Claude Code. If Gamma is already connected at this address, continue to sign-in.")
                : t("Run this command on the computer where you use Codex. It installs the plugin and opens Gamma sign-in.")}>
              {method === "settings"
                ? <CopyField key="url" label={t("Gamma MCP server URL")} value={data.mcp_url} action="Copy server URL" />
                : <>
                  <div role="group" aria-label={t("Terminal platform")}>
                    <Segmented value={platform} onChange={setPlatform} options={[["windows", t("Windows PowerShell")], ["unix", t("macOS / Linux")]]} />
                  </div>
                  {isClaude ? <>
                    <CopyField key="claude-connect" label={t("Claude Code connection command")} value={claudeConnectCommand(data.mcp_url, platform)} action="Copy connection command" />
                    <p className="settingDesc">{t("Requires {claude} and a running Gamma server.", { claude: <a href="https://code.claude.com/docs/en/setup" target="_blank" rel="noreferrer">{t("Claude Code")}</a> })}</p>
                  </> : <>
                    <CopyField key="commands" label={t("Codex setup command")} value={setup} action="Copy setup command" rows={4} />
                    <p className="settingDesc">{t("Requires the {codex}. Downloads the setup script and plugin from {release}.", {
                      codex: <a href="https://learn.chatgpt.com/docs/cli" target="_blank" rel="noreferrer">{t("Codex CLI")}</a>,
                      release: <a href="https://github.com/tim4431/Gamma/releases/latest" target="_blank" rel="noreferrer">{t("Gamma's latest release")}</a> })}</p>
                </>}
              </>}
            </Step>
            <Step n={2} title={t("Sign in and choose a workspace")}
              hint={isClaude ? t("Start Claude Code, run /mcp, select gamma, and authenticate. Sign in to Gamma in your browser and approve a workspace.")
                : t("Follow your assistant's sign-in prompt. Approve read-only access in Gamma. No token to create or paste.")} />
            {isClaude ? <>
              <Step n={3} title={t("Ask about your papers")}
                hint={t("Start a new session and paste a Gamma page or share link with your question, or mention a paper by name. With the Gamma plugin installed, /gamma:gamma starts the workflow.")}>
                <p className="settingDesc">{t("The MCP connection above is all Claude Code needs to read your library. The plugin (optional) adds the /gamma:gamma workflow and the Gamma identity.")}</p>
                <details>
                  <summary>{t("Install the plugin (once)")}</summary>
                  <p>{t("Download the Claude Code plugin ZIP from {release} and extract it into a permanent folder. Open a terminal in the folder containing {folder} and run:", {
                    release: <a href="https://github.com/tim4431/Gamma/releases/latest" target="_blank" rel="noreferrer">{t("Gamma's latest release")}</a>, folder: <code>gamma-marketplace</code> })}</p>
                  <CopyField label={t("Claude Code plugin install commands")} value={claudePluginInstallCommands} action="Copy plugin install commands" rows={3} />
                  <p className="settingDesc">{t("Choose the asset named {name} followed by the version and {ext}. Requires a release that includes the Claude Code plugin. Keep the extracted folder after installing.", {
                    name: <code>gamma-claude-code-plugin</code>, ext: <code>.zip</code> })}</p>
                </details>
              </Step>
              <details className="integrationAdvanced">
                <summary>{t("Changed the server address?")}</summary>
                <p>{t("Open Gamma at its new address, then copy these commands to replace the connection. Your plugin stays installed. Restart Claude Code and sign in again through {mcp}.", { mcp: <code>/mcp</code> })}</p>
                <CopyField label={t("Claude Code change server commands")} value={claudeConnectCommand(data.mcp_url, platform, { replace: true })} action="Copy change server commands" rows={3} />
                <p className="settingDesc">{t("If Gamma runs on the same computer, localhost keeps working when your LAN IP changes. For a remote server, use a stable HTTPS hostname and confirm it in Gamma's Settings → Server.")}</p>
              </details>
            </> : <Step n={3} title={t("Start a new chat")}
              hint={method === "terminal" ? t("Copy the Gamma page URL from your browser and paste it with your question. A share link works too.") : t("Try asking: “Use Gamma to find my notes about…”")} />}
          </>}
        </> : <>
          <p>{t("Browser sign-in is not available for this Gamma address yet.")}</p>
          <p>{t("An administrator can enable it by confirming the public server URL in Settings → Server.")}</p>
          <details><summary>{t("Server setup details")}</summary><p>{data.oauth_error}</p></details>
        </>}
      </div> : !loadError ? <p role="status">{t("Loading connection settings…")}</p> : null}
    </Section>
    <Section title={t("Workspace access")} action={
      <button type="button" className="uiBtn sm iconSq" aria-label={t("Refresh connections")} title={t("Refresh connections")} onClick={() => refresh()}>
        <RefreshIcon size={13} />
      </button>}>
      {data ? data.tokens.length ? data.tokens.map((item) => <ConnectionRow key={item.id} item={item} busy={busy} onRevoke={revoke} />)
        : <div className="integrationDetails"><p>{t("No assistants have access to this workspace yet.")}</p></div> : null}
    </Section>
    {message ? <p role="status">{message}</p> : null}
    <details className="integrationAdvanced">
      <summary>{t("Manual setup (advanced)")}</summary>
      <div className="integrationDetails"><p>{t("Use a token if your assistant does not support browser sign-in.")}</p></div>
      <Section title={t("Create a token")}>
      <Row icon={KeyIcon} label={t("Connection name")} hint={t("Access to the current workspace. Expires after 90 days.")}>
        <div className="integrationCreateControls">
          <input className="aiKeyInput" aria-label={t("Connection name")} value={name} maxLength={80}
            onChange={(event) => setName(event.target.value)} />
          <button className="uiBtn" disabled={busy || !data || !name.trim() || !!secret} onClick={() => create(name.trim(), scope, "manual")}>{t("Create token")}</button>
        </div>
      </Row>
      <Row icon={ShieldIcon} label={t("Scope")} hint={scope === "write"
        ? t("Read and write: what an offline copy on another Gamma (Settings → Account & sync → Clones there) signs in with. Assistants only need read.") : t("Read-only: assistants. Choose “Read and write” for an offline copy of this workspace on another Gamma.")}>
        <Segmented value={scope} onChange={setScope} options={[["read", t("Read-only")], ["write", t("Read and write")]]} />
      </Row>
      {secret?.origin === "manual" ? tokenField : null}
    </Section>
    <Section title={t("Add the token to Codex")}>
      {data ? <div className="integrationDetails">
        <p>{t("Set the {env} environment variable to your token before starting Codex. Add this connection to {file}, then restart Codex.", { env: <code>GAMMA_TOKEN</code>, file: <code>~/.codex/config.toml</code> })}</p>
        <CopyField label={t("Codex MCP configuration")} value={config} action="Copy configuration" rows={4} />
        <p>{t("Gamma must be running. For a remote server, its administrator must allow the server's hostname for MCP.")}</p>
      </div> : null}
    </Section>
    </details>
  </>;
}
