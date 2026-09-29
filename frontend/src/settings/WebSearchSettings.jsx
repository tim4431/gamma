// Account-only search credentials stay on the server. The editor holds new
// keys only in React state; the API returns masked hints, never stored keys.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { friendlyApiError } from "../library/libraryUtils";
import { MenuSelect } from "../shared/ui/Menus";
import { GlobeIcon } from "../shared/ui/Icons";
import { t, tn } from "../shared/i18n/i18n.js";
import { Section, Row, SubDialog, Field, PasswordInput, DialogButtons } from "./SettingsKit";

const endpoint = `${API}/ai/web-search`;
const SEARCH_SERVICES = [["auto", t("Automatic")], ["openai", "OpenAI"], ["brave", "Brave Search"],
  ["searxng", "SearXNG"], ["off", t("Off")]];
const serviceName = (id) => SEARCH_SERVICES.find(([value]) => value === id)?.[1] || id;
const fields = ["provider", "openai_model", "openai_connection", "searxng_url"];

function SearchEditor({ info, onSaved, onClose }) {
  const [initial] = React.useState(() => ({
    provider: info.provider || "auto", openai_model: info.openai_model || "gpt-4.1-mini",
    openai_connection: info.openai_connection || "", searxng_url: info.searxng_url || "",
    openai_api_key: "", brave_api_key: "", clear_openai_api_key: false, clear_brave_api_key: false,
  }));
  const [form, setForm] = React.useState(initial);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);
  const change = (key, value) => { setForm((prev) => ({ ...prev, [key]: value })); setError(""); };
  const shows = (provider) => form.provider === "auto" || form.provider === provider;
  const connections = info.connections || [];
  const unavailable = form.openai_connection && !connections.some((c) => c.id === form.openai_connection);

  async function save() {
    setBusy(true);
    setError("");
    const payload = Object.fromEntries(fields.filter((key) => form[key] !== initial[key])
      .map((key) => [key, form[key].trim()]));
    for (const provider of ["openai", "brave"]) {
      const key = `${provider}_api_key`, clear = `clear_${key}`;
      if (form[clear]) payload[clear] = true;
      else if (form[key].trim()) payload[key] = form[key].trim();
    }
    try {
      onSaved(await apiJson(endpoint, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload) }));
    } catch (err) { setError(friendlyApiError(err)); }
    finally { setBusy(false); }
  }

  const keyField = (provider, label, removeLabel) => {
    const key = `${provider}_api_key`, clear = `clear_${key}`, hint = info[`${provider}_key_hint`];
    return <>
      <Field label={label} hint={hint ? t("Saved key: {hint}. Leave blank to keep it.", { hint }) : undefined}>
        <PasswordInput aria-label={label} value={form[key]} disabled={busy || form[clear]}
          autoComplete="new-password" spellCheck={false} maxLength={512}
          onChange={(event) => change(key, event.target.value)} />
      </Field>
      {hint ? <label className="uiCheckRow">
        <input type="checkbox" checked={form[clear]} disabled={busy}
          onChange={(event) => setForm((prev) => ({ ...prev, [clear]: event.target.checked, [key]: "" }))} />
        {removeLabel}
      </label> : null}
    </>;
  };

  return <SubDialog title={t("Web search settings")} draft={dirty} onClose={busy ? () => {} : onClose}>
    <div className="settingsForm">
      <div inert={busy ? "" : undefined}>
        <Field label={t("Preferred search service")} group>
          <MenuSelect label={t("Preferred search service")} value={form.provider} onChange={(v) => change("provider", v)}
            options={SEARCH_SERVICES} block />
        </Field>
        <p className="setNotice">{form.provider === "auto"
          ? t("Automatic uses the first configured service: OpenAI, Brave Search, then SearXNG.")
          : form.provider === "off" ? t("General web search is off. Crossref and arXiv searches remain available.")
            : t("Only the selected service is used. If it fails, Gamma reports the error.")}</p>
        {shows("openai") ? <>
          <Section title="OpenAI" />
          <Field label={t("OpenAI connection")} group
            hint={t("Choose an existing OpenAI API connection, or use a separate search key below.")}>
            <MenuSelect label={t("OpenAI connection")} value={form.openai_connection}
              onChange={(v) => change("openai_connection", v)} block options={[
                ["", t("Search key or automatic connection")],
                ...connections.map((c) => [c.id, c.label]),
                ...(unavailable ? [[form.openai_connection, t("Unavailable connection")]] : []),
              ]} />
          </Field>
          {!form.openai_connection ? <>
            {keyField("openai", t("OpenAI search key"), t("Remove saved OpenAI search key"))}
            <p className="setNotice">{t("Without a search key, Gamma can use your own saved OpenAI API connection. ChatGPT sign-in does not provide an API key.")}</p>
          </> : null}
          <Field label={t("Search model")}>
            <input className="aiKeyInput" aria-label={t("Search model")} value={form.openai_model}
              placeholder="gpt-4.1-mini" maxLength={100} autoComplete="off" spellCheck={false}
              onChange={(event) => change("openai_model", event.target.value)} />
          </Field>
        </> : null}
        {shows("brave") ? <>
          <Section title="Brave Search" />
          {keyField("brave", t("Brave Search API key"), t("Remove saved Brave Search key"))}
        </> : null}
        {shows("searxng") ? <>
          <Section title="SearXNG" />
          <Field label={t("SearXNG address")} hint={t("The address of a SearXNG service with JSON search enabled.")}>
            <input className="aiKeyInput" aria-label={t("SearXNG address")} value={form.searxng_url}
              placeholder="https://search.example.org" maxLength={2048} autoComplete="off" spellCheck={false}
              onChange={(event) => change("searxng_url", event.target.value)} />
          </Field>
        </> : null}
      </div>
      {error ? <p className="setNotice aiKeysError" role="alert">{error}</p> : null}
      <DialogButtons>
        <button type="button" className="uiBtn" disabled={busy} onClick={onClose}>{t("Cancel")}</button>
        <button type="button" className="uiBtn primary" disabled={busy || !dirty} onClick={save}>
          {busy ? t("Saving…") : t("Save")}
        </button>
      </DialogButtons>
    </div>
  </SubDialog>;
}

export default function WebSearchSettings({ connectionsVersion }) {
  const [info, setInfo] = React.useState(null);
  const [error, setError] = React.useState("");
  const [editing, setEditing] = React.useState(false);
  const [test, setTest] = React.useState(null);
  const [saved, setSaved] = React.useState(false);
  const [round, setRound] = React.useState(0);
  React.useEffect(() => {
    let live = true;
    apiJson(endpoint).then((next) => { if (live) { setInfo(next); setError(""); } })
      .catch((err) => { if (live) setError(friendlyApiError(err)); });
    return () => { live = false; };
  }, [connectionsVersion, round]);

  async function testSaved() {
    setTest({ busy: true });
    setSaved(false);
    try {
      setTest(await apiJson(`${endpoint}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }));
    } catch (err) { setTest({ ok: false, error: friendlyApiError(err) }); }
  }

  const hint = !info ? t("Loading…") : info.provider === "off" ? t("Off")
    : info.configured ? t("Using {service}", { service: serviceName(info.effective_provider) }) : t("No web search service configured");
  return <>
    <Section title={t("Web search")}>
      <Row icon={GlobeIcon} label={t("Search service")} hint={hint}
        title={t("Find papers, author pages and repository copies across the web.")}>
        {info?.can_edit ? <span className="setRowControls">
          <button type="button" className="uiBtn sm" disabled={test?.busy}
            onClick={() => { setEditing(true); setTest(null); setSaved(false); }}>{t("Manage")}</button>
          <button type="button" className="uiBtn sm" disabled={!info.configured || info.provider === "off" || test?.busy || editing}
            title={t("Runs one web search with saved settings. Your search service may charge for it.")} onClick={testSaved}>
            {test?.busy ? t("Testing…") : t("Test search")}
          </button>
        </span> : null}
      </Row>
      {info && !info.can_edit ? <p className="setNotice">{t("Guest accounts cannot store API keys. Ask the admin for an account.")}</p> : null}
      {info?.configuration_error ? <p className="setNotice aiKeysError" role="alert">{info.configuration_error}</p> : null}
      {error ? <p className="setNotice aiKeysError" role="alert">{error}{" "}
        <button type="button" className="uiBtn sm" onClick={() => setRound((n) => n + 1)}>{t("Retry")}</button>
      </p> : null}
      {saved ? <p className="setNotice" role="status">{t("Web search settings saved.")}</p> : null}
      {test && !test.busy ? <p className={`setNotice ${test.ok ? "aiTestOk" : "aiKeysError"}`} role={test.ok ? "status" : "alert"}>
        {test.ok ? tn("{provider}: {n} search result", "{provider}: {n} search results", test.count || 0,
          { provider: serviceName(test.provider) }) : test.error || t("Web search test failed.")}
      </p> : null}
    </Section>
    {editing && info?.can_edit ? <SearchEditor info={info} onClose={() => setEditing(false)} onSaved={(next) => {
      setInfo(next); setEditing(false); setTest(null); setSaved(true);
    }} /> : null}
  </>;
}
