// Settings → Network proxy (admins only): how this server reaches the
// internet. One proxy carries every outbound connection the server makes
// (AI providers, paper lookups and downloads, translation, Zotero, Gamma
// Cloud, other Gamma servers), routed by destination: the hosts under
// "Don't use the proxy for", this computer and the proxy itself are reached
// directly. Use system settings (the default) follows the server's
// environment or the OS; No proxy ignores both. GET/PUT /api/admin/proxy
// (the password is write-only: `password_set` says one is stored, and a PUT
// without `password` keeps it), POST /api/admin/proxy/test tries the unsaved
// fields on a few well-known hosts and an address of the admin's, saving
// nothing. A hosted server's network is Gamma Cloud's (`managed`), so the
// pane is read-only there. The desktop app's own windows are not the
// server's traffic and keep following the OS. docs/dev/network_proxy.md.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { IS_DESKTOP } from "../shared/lib/connector.js";
import { composing } from "../shared/lib/hotkeys.js";
import { PaneHead, PasswordInput, Row, Section, Segmented, TestResult, Toggle, useDraftOf, useSettingsDraft } from "./SettingsKit";
import { useSettingsRead } from "./settingsReads.js";
import { ActivityIcon, GlobeIcon, KeyIcon, ListIcon, SearchIcon, ServerIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { T, t } from "../shared/i18n/i18n.js";

const ENDPOINT = `${API}/admin/proxy`;
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const MODES = [["system", T("Use system settings")], ["none", T("No proxy")], ["manual", T("Manual")]];

// The draft of the saved settings. The password starts empty: empty keeps
// the stored one.
const fromSaved = (s) => ({ mode: s.mode || "system", server: s.server || "", username: s.username || "", password: "",
  bypass: s.bypass || "", proxy_dns: !!s.proxy_dns });
const NO_DRAFT = fromSaved({});

// What a save and a test send: the mode, the name lookups while their switch
// shows (`lookups`), and the address, the sign-in and the list only under
// Manual. So an address half typed under Manual and left there is neither
// refused nor saved by the other modes, and the stored one stays. The
// password goes only when typed.
function payload(draft, lookups) {
  const out = { mode: draft.mode };
  if (lookups) out.proxy_dns = draft.proxy_dns;
  if (draft.mode === "manual") {
    Object.assign(out, { server: draft.server.trim(), username: draft.username.trim(), bypass: draft.bypass });
    if (draft.password) out.password = draft.password;
  }
  return out;
}

// What the Proxy row says the chosen mode does; under Use system settings,
// the proxy the server found and where it found it.
function modeHint(mode, system) {
  if (mode === "none") return t("Connects directly, whatever the system sets");
  if (mode === "manual") return t("Uses the proxy below");
  if (system.proxy) {
    return system.from === "environment" ? t("Uses {proxy}, from the server's environment", { proxy: system.proxy })
      : t("Uses {proxy}, from this computer's network settings", { proxy: system.proxy });
  }
  // `proxy` is what https takes; a proxy set for http alone carries nothing else.
  if (system.http) return t("Uses {proxy} for http:// addresses only; https connects directly", { proxy: system.http });
  return system.unsupported ? t("{proxy} can't be used, so this server connects directly", { proxy: system.unsupported })
    : t("No system proxy found: connects directly");
}

// One Test. Never throws: a refusal (an address the server cannot read)
// is one failed line.
async function tryRoutes(body) {
  try { return (await apiJson(`${ENDPOINT}/test`, json("POST", body))).results || []; }
  catch (err) { return [{ ok: false, error: err.message }]; }
}

const hostOf = (url) => { try { return new URL(url).host; } catch { return url || ""; } };
// The proxy's host, read as the server reads it (a bare host:port is http).
const proxyHost = (server) => {
  const text = server.trim();
  try { return new URL(text.includes("://") ? text : `http://${text}`).hostname.toLowerCase(); } catch { return text; }
};

// One target's line: ✓ or ✗, its name (the host, for the admin's own
// address), whether it went through the proxy or directly (which proxy, or
// why direct, on hover), then the HTTP status and the time it took, or why
// it failed. Any answer from the site, an error page too, means the way works.
function RouteLine({ result }) {
  const proxied = result.route === "proxy";
  const tag = result.route ? <span className="uiTag" title={proxied ? result.via : result.reason}>{proxied ? t("Proxy") : t("Direct")}</span> : null;
  const outcome = result.ok ? t("{status} · {ms} ms", { status: result.status, ms: result.ms }) : result.error;
  return <TestResult live={false} test={{ ok: !!result.ok, message: <>{result.name || hostOf(result.url)} {tag} {outcome}</> }} />;
}

export function ProxySettings({ setStatus }) {
  const proxy = useSettingsRead("/admin/proxy");
  const saved = proxy.data;
  const [draft, setDraft] = useDraftOf(saved, fromSaved, NO_DRAFT);
  const [saveError, setError] = React.useState("");
  const error = saveError || (!saved ? proxy.error : "");
  const [busy, setBusy] = React.useState(false);
  const [address, setAddress] = React.useState("");
  const [testing, setTesting] = React.useState(false);
  const [results, setResults] = React.useState(null);
  // A Test's lines are about what it tried: an edit since drops them, and
  // the answer of a Test run before the edit is not shown.
  const tried = React.useRef(0);
  const forget = () => { tried.current += 1; setResults(null); };

  const managed = !!saved?.managed;
  const system = saved?.system || {};
  const manual = draft.mode === "manual";
  // Name lookups matter only where a proxy is in use (an http-only one
  // still carries the http:// links people open).
  const lookups = manual || (draft.mode === "system" && !!(system.proxy || system.http));
  const dirty = !!saved && !managed
    && JSON.stringify(payload(draft, lookups)) !== JSON.stringify(payload(fromSaved(saved), lookups));
  const discard = () => { setDraft(fromSaved(saved)); setError(""); forget(); };
  useSettingsDraft("network-proxy", dirty, discard);
  // An edit also drops a refusal, which was about the draft before it.
  const set = (key) => (value) => { setDraft((d) => ({ ...d, [key]: value })); setError(""); forget(); };
  // The server keeps the stored password only for the same proxy host and
  // user name; anything else it asks to be typed again.
  const user = draft.username.trim();
  const keepsPassword = !!saved?.password_set && !!user && user === saved.username
    && proxyHost(draft.server) === proxyHost(saved.server);

  async function save() {
    if (!dirty || busy) return;
    setBusy(true); setError("");
    try {
      const value = await apiJson(ENDPOINT, json("PUT", payload(draft, lookups)));
      proxy.set(value); setDraft(fromSaved(value));
      setStatus?.(t("Proxy settings saved."));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  // The draft, or under Gamma Cloud's management what is in force.
  async function test() {
    const mine = ++tried.current;
    setTesting(true); setResults(null);
    const url = address.trim();
    const found = await tryRoutes({ ...(managed ? {} : payload(draft, lookups)), ...(url ? { url } : {}) });
    if (tried.current === mine) setResults(found);
    setTesting(false);
  }
  const onEnter = (run) => (event) => {
    if (composing(event)) return;
    if (event.key === "Enter") { event.preventDefault(); run(); }
  };

  const disabled = !saved || managed || busy;
  const errorLine = error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null;
  const intro = [t("How this server reaches the internet: AI providers, paper lookups and downloads, translation, Zotero, Gamma Cloud and other Gamma servers. Not a library proxy (EZproxy)."),
    IS_DESKTOP ? t("This app's windows and its updates follow your system's proxy settings.") : null];
  return <>
    <PaneHead icon={GlobeIcon} title={t("Network proxy")}>{intro.filter(Boolean).join(" ")}</PaneHead>
    <Section title={t("Connection")} action={dirty ? (
      <button className="uiBtn sm primary" disabled={busy} onClick={save}><MorphText>{busy ? t("Saving…") : t("Save")}</MorphText></button>
    ) : null}>
      {!saved ? errorLine || <p className="setNotice loadingHint">{t("Loading…")}</p> : <>
        <Row icon={GlobeIcon} label={t("Proxy")} hint={managed ? t("Gamma Cloud manages this server's network.") : modeHint(draft.mode, system)}
          title={managed ? modeHint(draft.mode, system)
            : t("Use system settings follows the server's environment or this computer's network settings; No proxy connects directly; Manual uses a proxy you set")}>
          <Segmented value={draft.mode} onChange={set("mode")} options={MODES} disabled={disabled} />
        </Row>
        {manual ? <>
          <Row icon={ServerIcon} label={t("Proxy server")} hint={t("http:// or socks5://, with its port")}
            title={t("An http:// or socks5:// proxy; https:// proxies and PAC files are not supported. An address with a sign-in, http://user:password@host:port, fills in the Sign-in fields when saved.")}>
            <input className="aiKeyInput" type="url" aria-label={t("Proxy server")} value={draft.server} spellCheck={false}
              autoComplete="off" name="gamma-proxy-server" disabled={disabled} placeholder="http://127.0.0.1:7890"
              onChange={(event) => set("server")(event.target.value)} onKeyDown={onEnter(save)} />
          </Row>
          <Row icon={KeyIcon} label={t("Sign-in")} hint={t("Only if the proxy asks for one")} wide>
            <span className="setFieldPair">
              <input className="aiKeyInput" type="text" aria-label={t("Username")} placeholder={t("Username")} value={draft.username}
                spellCheck={false} autoComplete="off" autoCapitalize="none" autoCorrect="off" name="gamma-proxy-username" disabled={disabled}
                onChange={(event) => set("username")(event.target.value)} onKeyDown={onEnter(save)} />
              <PasswordInput aria-label={t("Password")} value={draft.password} disabled={disabled} autoComplete="new-password"
                name="gamma-proxy-password" placeholder={keepsPassword ? t("password set — type to replace") : t("Password")}
                onChange={(event) => set("password")(event.target.value)} onKeyDown={onEnter(save)} />
            </span>
          </Row>
          <Row icon={ListIcon} label={t("Don't use the proxy for")}
            hint={t("Hosts, domains (*.example.com), IP addresses and ranges (10.0.0.0/8), <local>. This computer always connects directly.")}
            title={t("Separated by commas or new lines. A domain covers its subdomains; <local> is any name without a dot; * is everything.")} wide>
            <span className="setRowControls">
              <textarea className="aiKeyInput" rows={3} aria-label={t("Don't use the proxy for")} value={draft.bypass} spellCheck={false}
                disabled={disabled} onChange={(event) => set("bypass")(event.target.value)} />
              {draft.bypass !== saved.default_bypass ? (
                <button className="uiBtn sm" disabled={disabled} onClick={() => set("bypass")(saved.default_bypass)}>{t("Reset to defaults")}</button>
              ) : null}
            </span>
          </Row>
        </> : null}
        {lookups ? (
          <Toggle icon={SearchIcon} label={t("Let the proxy look up names")} checked={draft.proxy_dns} disabled={disabled} onChange={set("proxy_dns")}
            hint={t("For links, PDFs and pages people or the assistant open: when only the proxy can resolve internet names, or with a VPN in fake-IP mode. The proxy then decides which addresses they can reach.")} />
        ) : null}
        {errorLine}
      </>}
    </Section>
    <Section title={t("Test")}>
      <Row icon={ActivityIcon} label={t("Test the connection")} hint={t("Tries the settings above before you save them")}
        title={t("Reaches OpenAI, ChatGPT, Anthropic, Zotero and arXiv, and Gamma Cloud once it is set up, with these settings, and the address typed here. Any answer from a site, an error page too, means the way there works, unless the site refuses this network (a region it doesn't serve, a Cloudflare block).")}>
        <span className="setRowControls">
          <input className="aiKeyInput" type="url" aria-label={t("Address to test")} value={address} spellCheck={false}
            autoComplete="off" name="gamma-proxy-test-url" disabled={!saved} placeholder="https://api.deepseek.com"
            onChange={(event) => { setAddress(event.target.value); forget(); }} onKeyDown={onEnter(() => { if (!testing) test(); })} />
          <button className="uiBtn sm" disabled={!saved || testing} onClick={test}><MorphText>{testing ? t("Testing…") : t("Test")}</MorphText></button>
        </span>
      </Row>
      {/* One region, mounted before a Test, so its outcome is read out
          when it replaces Testing…; the lines are not regions of their own. */}
      <div className="settingsPaneHint" role="status" aria-busy={testing}>
        {testing ? <TestResult live={false} test={{ busy: true }} />
          : (results || []).map((result) => <RouteLine key={`${result.name}|${result.url}|${result.error}`} result={result} />)}
      </div>
    </Section>
  </>;
}
