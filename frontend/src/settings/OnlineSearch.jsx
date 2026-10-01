// Settings → AI → Chat › Online search: what the assistant's general web
// search goes through, and the services' keys. The keys are the account's,
// kept on the server and write-only like the AI keys: GET
// /api/ai/search-services masks them (gamma/search_services.py). Scholarly
// search (Crossref, arXiv, OpenAlex) needs none of this; the OpenAlex key
// only lifts its daily budget.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { friendlyApiError } from "../library/libraryUtils";
import { MenuSelect } from "../shared/ui/Menus";
import { Row, SubDialog, Field, PasswordInput } from "./SettingsKit";
import { BookIcon, GlobeIcon, KeyIcon, ServerIcon, Trash2Icon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

// Per service: the form's fields, where to get them, and the row's icon.
const SERVICE_FORMS = {
  brave: {
    icon: KeyIcon,
    hint: T("Brave Search API dashboard (api-dashboard.search.brave.com) › API keys. Its plans include monthly free credits."),
    fields: [{ id: "api_key", label: T("API key"), secret: true, placeholder: "BSA…" }],
  },
  searxng: {
    icon: ServerIcon,
    hint: T("The address of a SearXNG instance with the JSON format turned on (search › formats in its settings.yml)."),
    fields: [{ id: "url", label: T("Address"), secret: false, placeholder: "https://search.example.org" }],
  },
  openalex: {
    icon: BookIcon,
    hint: T("A free key from openalex.org/settings/api: ten times the keyless daily budget, and searches that are not paused when OpenAlex is busy."),
    fields: [{ id: "api_key", label: T("API key"), secret: true }],
  },
};

const ENGINE_OPTIONS = () => [
  ["auto", t("Automatic")],
  ["ai", t("Your AI connection")],
  ["brave", "Brave Search"],
  ["searxng", "SearXNG"],
  ["off", t("Off")],
];

function serviceHint(service, result) {
  if (result?.busy) return t("Testing…");
  if (result) return result.ok ? `✓ ${result.text}` : result.error;
  if (service.configured) {
    return service.id === "searxng" ? service.fields.url : t("Key {hint}", { hint: service.fields.api_key || "" });
  }
  if (service.server) return t("Provided by this server");
  return service.id === "openalex" ? t("Optional — works without a key") : t("Not set up");
}

export function OnlineSearchSettings() {
  const [info, setInfo] = React.useState(null); // {engine, services, can_edit}
  const [error, setError] = React.useState("");
  const [form, setForm] = React.useState(null); // {service, label, configured, fields}
  const [busy, setBusy] = React.useState(false);
  const [tests, setTests] = React.useState({}); // service → {busy} | {ok, text} | {ok: false, error}

  React.useEffect(() => {
    apiJson(`${API}/ai/search-services`).then(setInfo).catch((err) => setError(friendlyApiError(err)));
  }, []);

  async function change(call) {
    setBusy(true);
    setError("");
    try {
      setInfo(await call());
      setForm(null);
      setTests({}); // a result is about the settings it tested
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setBusy(false);
    }
  }
  const put = (path, body) => apiJson(`${API}/ai/search-services/${path}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const setEngine = (engine) => change(() => put("engine", { engine }));
  const save = () => change(() => put(form.service, { fields: form.fields }));
  const remove = (service) => change(() => apiJson(`${API}/ai/search-services/${service.id}`, { method: "DELETE" }));
  async function test(service) {
    setTests((prev) => ({ ...prev, [service.id]: { busy: true } }));
    let result;
    try {
      result = await apiJson(`${API}/ai/search-services/${service.id}/test`, { method: "POST" });
    } catch (err) {
      result = { ok: false, error: friendlyApiError(err) };
    }
    setTests((prev) => ({ ...prev, [service.id]: result }));
  }
  function edit(service) {
    setError("");
    // Secret fields start empty (empty = keep the stored one); plain ones
    // come back from the server as they are.
    const fields = Object.fromEntries(SERVICE_FORMS[service.id].fields.map((f) =>
      [f.id, f.secret ? "" : service.fields?.[f.id] || ""]));
    setForm({ service: service.id, label: service.label, configured: service.configured, fields });
  }

  const canEdit = info?.can_edit;
  return (<>
    <Row icon={GlobeIcon} label={t("Search the web with")}
      hint={t("Lab pages, repositories and copies the registries miss")}
      title={t("What the assistant's general web search goes through. Automatic takes Brave Search or SearXNG when one is set up below, else your AI connection's own web search (OpenAI, the ChatGPT sign-in and Anthropic have one; each search is one short extra call on it). Searching Crossref, arXiv and OpenAlex needs none of this.")}>
      <MenuSelect label={t("Search the web with")} value={info?.engine || "auto"}
        options={ENGINE_OPTIONS()} onChange={(engine) => { if (canEdit && !busy) setEngine(engine); }} />
    </Row>
    {/* The service rows are the search target for "Online search services". */}
    <div data-setting={t("Online search services")}>
      {info?.services.map((service) => {
        const result = tests[service.id];
        const Icon = SERVICE_FORMS[service.id]?.icon || KeyIcon;
        return (
          <Row key={service.id} icon={Icon} label={service.label} hint={serviceHint(service, result)}>
            {canEdit ? (service.configured ? <span className="setRowControls">
              <button className="uiBtn sm" disabled={busy || result?.busy}
                title={t("Run one small search with these settings")} onClick={() => test(service)}>
                {t("Test")}
              </button>
              <button className="uiBtn sm" disabled={busy} onClick={() => edit(service)}>{t("Edit")}</button>
              <button className="uiBtn sm iconSq danger" disabled={busy} title={t("Remove these settings")}
                aria-label={t("Remove")} onClick={() => remove(service)}>
                <Trash2Icon size={16} />
              </button>
            </span> : <button className="uiBtn sm" disabled={busy} onClick={() => edit(service)}>{t("Set up")}</button>) : null}
          </Row>
        );
      })}
      {info && !canEdit ? <div className="settingsPaneHint">{t("Guest accounts cannot store API keys. Ask the admin for an account.")}</div> : null}
      {error && !form ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
    </div>
    {form ? (
      <SubDialog draft={form.fields} title={form.label} onClose={() => { setForm(null); setError(""); }}>
        <div className="settingsForm">
          <div className="settingsPaneHint settingsNoMargin">{t(SERVICE_FORMS[form.service].hint)}</div>
          {SERVICE_FORMS[form.service].fields.map((f) => {
            const props = {
              spellCheck: false, placeholder: f.placeholder || "", value: form.fields[f.id],
              onChange: (event) => setForm((prev) => ({ ...prev, fields: { ...prev.fields, [f.id]: event.target.value } })),
            };
            return (
              <Field key={f.id} label={t(f.label)} hint={f.secret && form.configured ? t("Leave empty to keep the current one") : null}>
                {f.secret ? <PasswordInput autoComplete="new-password" {...props} />
                  : <input className="aiKeyInput" type="text" autoComplete="off" {...props} />}
              </Field>
            );
          })}
          {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
          <div className="reportModalBtns">
            <button className="uiBtn" onClick={() => { setForm(null); setError(""); }}>{t("Cancel")}</button>
            <button className="uiBtn primary" disabled={busy} onClick={save}>{busy ? t("Saving…") : t("Save")}</button>
          </div>
        </div>
      </SubDialog>
    ) : null}
  </>);
}
