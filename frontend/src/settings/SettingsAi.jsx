// Settings → AI › Connections: the user's AI credential list (OpenAI-platform style)
// and the add/edit-key wizard. The account's own list, its Test and Usage
// results live in App.jsx (the aiKeys* group), its connect dialog in
// useProviderEditor (providerEditor.js) — these components only render them;
// the server's shared entries appear there as read-only rows.
// SharedAiProviderSettings is Settings → Server's list of those shared
// entries (/api/admin/ai-providers), the same rows and the same form through
// the same hook over its own state.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { MenuSelect } from "../shared/ui/Menus";
import { cachedPercent, fmtTokens, usageDetail } from "../chat/tokenUsage";
import { failureCopy, fixLabel } from "../chat/chatErrors";
import { ModelPicker } from "./ModelPicker";
import { UsageChart } from "./UsageChart.jsx";
import { Section, SubDialog, Step, Field, CopyField, Empty, IconChoices, PercentMeter, Row, PasswordInput, Toggle, UnitInput } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { aiServiceTiles, modelList, onThisMachine, signInAddress, useProviderEditor } from "./providerEditor.js";
import { ActivityIcon, CheckIcon, ExternalLinkIcon, GlobeIcon, KeyIcon, MicIcon, PaperIcon, RefreshIcon, SparklesIcon, Trash2Icon, UserIcon, XIcon } from "../shared/ui/Icons";
import { T, getLocale, t, tn } from "../shared/i18n/i18n.js";

// Spoken languages for dictation, by native name. The pick "" follows the
// display language, "auto" leaves the language to the model.
const DICTATION_LANGS = [
  ["en", t("English")], ["zh", "中文"], ["ja", "日本語"], ["ko", "한국어"],
  ["de", t("Deutsch")], ["fr", t("Français")], ["es", t("Español")], ["pt", t("Português")],
  ["it", t("Italiano")], ["ru", "Русский"], ["hi", "हिन्दी"], ["ar", "العربية"],
];

function formatPercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function ProviderUsage({ usage }) {
  if (usage.busy) return <span className="aiProvDesc">{t("Checking usage…")}</span>;
  if (!usage.available) {
    return <span className="aiProvDesc aiKeysError">{usage.reason || t("Usage percentage unavailable")}</span>;
  }
  return (
    <span className="aiUsage">
      {(usage.windows || []).map((window, index) => {
        const used = Number(window.used_percent) || 0;
        const left = Number(window.remaining_percent) || 0;
        const reset = window.reset_at
          ? `resets ${new Date(window.reset_at * 1000).toLocaleString([], {
              month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
            })}`
          : "";
        const label = [usage.plan_type ? `${usage.plan_type}` : "", window.name]
          .filter(Boolean).join(" · ");
        const detail = t("{used}% used · {left}% left{reset}", { used: formatPercent(used), left: formatPercent(left), reset: reset ? ` · ${reset}` : "" });
        return (
          <span className="aiUsageWindow" key={`${window.name}-${index}`}>
            <span className="aiUsageHead">
              <span>{label}</span>
              <span title={detail}>{detail}</span>
            </span>
            <PercentMeter percent={used} barOnly />
          </span>
        );
      })}
    </span>
  );
}

// One connection of a provider list: avatar, name (plus "in use" and the
// shared tag), the credential line, its models, the last Test result and
// the usage windows, then the caller's buttons. `radio` is the active-key
// picker of the account's own list; `onFix` opens the entry's editor from
// a failed test. A failed test reads as the chat's error card does for its
// kind (chat/chatErrors.js), the provider's own words on hover. Only a row
// with a radio is a <label>: without one, the label's control would be its
// first button, and a click anywhere on the row would press it (Test).
function ProviderRow({ provider, protocol, oauth, active = false, radio = null, test, usage, onFix, children }) {
  const Frame = radio ? "label" : "div";
  const models = modelList(provider.models);
  const failed = test && !test.busy && !test.ok && test.kind
    ? failureCopy(test.kind, { provider: provider.label || provider.protocol, auth: oauth ? "oauth" : "key" }) : null;
  const fix = failed && onFix && ["key", "signin", "connection"].includes(failed.fix) ? failed.fix : "";
  return (
    <Frame className={`aiProvRow ${radio ? "aiProvSelectable" : ""} ${active ? "active" : ""}`}>
      {radio}
      <span className={`aiProvAvatar ${active ? "active" : ""}`}>
        {oauth ? <SparklesIcon size={16} /> : <KeyIcon size={16} />}
      </span>
      <span className="aiProvMeta">
        <span className="aiProvName">
          {provider.label || provider.protocol}
          {active ? <span className="aiProvActiveBadge">{t("in use")}</span> : null}
          {provider.shared ? (
            <span className="uiTag" title={t("An administrator added this connection for everyone on this server. Its key is never shown; its tokens count on your account.")}>
              {t("Shared by this server")}
            </span>
          ) : null}
        </span>
        <span className="aiProvDesc">
          {oauth
            ? t("{connected} · ChatGPT subscription", { connected: provider.oauth_connected ? t("signed in{account}", { account: provider.account ? ` as ${provider.account}` : "" }) : t("not connected") })
            : `${provider.key_hint ? `key ${provider.key_hint} · ` : provider.shared ? "" : "key set · "}${protocol?.label || provider.protocol}`}
          {provider.base_url ? ` · ${provider.base_url}` : ""}
        </span>
        <span className="aiProvDesc aiProvModels">
          <span className="aiProvModelsLabel">{t("Models")}</span>
          {models.length
            ? models.map((model) => <span className="categoryTag" key={model}>{model}</span>)
            : <span className="aiKeysError">{onFix ? t("none picked — edit to choose") : t("none picked")}</span>}
        </span>
        {test ? (
          <span
            className={`aiProvDesc ${test.busy ? "" : test.ok ? "aiTestOk" : "aiKeysError"}`}
            title={!test.busy && !test.ok ? test.error : undefined}
          >
            {test.busy
              ? t("Testing…") : test.ok
                ? `✓ working · ${test.model} · ${(test.latency_ms / 1000).toFixed(1)}s`
                : failed ? (
                  <>
                    ✗ {failed.headline}
                    {fix ? <>
                      {" — "}
                      <button className="chatEmptyLink" onClick={(event) => { event.preventDefault(); onFix(); }}>
                        {fixLabel(fix)}
                      </button>
                    </> : null}
                  </>
                ) : `✗ ${test.error}`}
          </span>
        ) : null}
        {usage ? <ProviderUsage usage={usage} /> : null}
      </span>
      {children ? <span className="aiProvActions">{children}</span> : null}
    </Frame>
  );
}

// Settings → Server → Shared AI provider (admins): connections every
// account on the server may use next to its own (backend
// gamma/ai_settings.py). An API key, write-only like an account's, or a
// ChatGPT subscription signed in here (its e-mail shown to admins only);
// guests get them only while the switch is on. The allowance rows meter
// them per account per day.
export function SharedAiProviderSettings({ setStatus, confirm }) {
  const base = `${API}/admin/ai-providers`;
  const [info, setInfo] = React.useState(null);
  const [loadError, setLoadError] = React.useState("");
  const [tests, setTests] = React.useState({});
  const [usage, setUsage] = React.useState({});
  React.useEffect(() => {
    let active = true;
    apiJson(base).then((v) => { if (active) setInfo(v); }).catch((err) => { if (active) setLoadError(err.message); });
    return () => { active = false; };
  }, [base]);
  const editor = useProviderEditor({ info, setInfo, base, onSaved: () => setStatus?.(t("Shared AI provider saved.")),
    onConnected: (entry) => test(entry) });
  async function test(p) {
    setTests((prev) => ({ ...prev, [p.id]: { busy: true } }));
    let result;
    try {
      result = await apiJson(`${API}/ai/providers/${encodeURIComponent(p.id)}/test`, { method: "POST" });
    } catch (err) {
      result = { ok: false, error: err.message };
    }
    setTests((prev) => ({ ...prev, [p.id]: result }));
  }
  // A shared sign-in's subscription windows (the account list's Usage).
  async function queryUsage(p) {
    setUsage((prev) => ({ ...prev, [p.id]: { busy: true } }));
    let result;
    try {
      result = await apiJson(`${API}/ai/providers/${encodeURIComponent(p.id)}/usage`, { method: "POST" });
    } catch (err) {
      result = { available: false, reason: err.message };
    }
    setUsage((prev) => ({ ...prev, [p.id]: result }));
  }
  const run = async (call) => {
    try { setInfo(await call()); } catch (err) { setLoadError(err.message); }
  };
  function remove(p) {
    confirm({
      title: T("Remove shared AI key"),
      message: t("Remove the \"{protocol}\" connection? Every account using it loses its models. This cannot be undone.", { protocol: p.label || p.protocol }),
      confirmLabel: t("Remove"),
      danger: true,
      onConfirm: () => run(() => apiJson(`${base}/${encodeURIComponent(p.id)}`, { method: "DELETE" })),
    });
  }
  const setGuests = (guests) => run(() => apiJson(base, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ guests }),
  }));
  // The shared allowance: tokens per account (or guest) per rolling 24 h on
  // the shared entries, 0 = unlimited (docs/dev/guests.md). Only the changed
  // field is sent; an invalid entry snaps back to the stored value.
  async function commitAllowance(key, raw) {
    const n = Number.parseInt(String(raw).trim(), 10);
    if (!Number.isFinite(n) || n < 0 || n === (info?.allowance?.[key] ?? 0)) return;
    try {
      setInfo(await apiJson(base, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ allowance: { [key]: n } }),
      }));
      setStatus?.(t("Shared AI allowance saved."));
    } catch (err) { setLoadError(err.message); }
  }
  const providers = info?.providers || [];
  return <>
    <Section title={t("Shared AI provider")} action={info ? (
      <button className="uiBtn sm" onClick={() => editor.startAdd("openai")}>{t("+ Add provider")}</button>
    ) : null}>
      {!info && !loadError ? <p className="setNotice">{t("Loading…")}</p> : null}
      {info && !providers.length ? <Empty icon={KeyIcon}>{t("No shared connection. Each account uses its own keys.")}</Empty> : null}
      {providers.map((provider) => {
        const el = tests[provider.id];
        const oauth = editor.isOauthProto(provider.protocol);
        return (
          <ProviderRow key={provider.id} provider={{ ...provider, shared: false }} oauth={oauth}
            protocol={editor.aiProtocolOf(provider.protocol)} test={el} usage={usage[provider.id]}
            onFix={() => editor.startEdit(provider)}>
            <button className="uiBtn sm" disabled={el?.busy}
              title={t("Send a tiny AI request through this connection to check it still works; the tokens count on your account")}
              onClick={() => test(provider)}>{t("Test")}</button>
            {oauth ? (
              <button className="uiBtn sm" disabled={usage[provider.id]?.busy}
                title={t("What is left of the ChatGPT subscription's usage windows")}
                onClick={() => queryUsage(provider)}>{t("Usage")}</button>
            ) : null}
            <button className="uiBtn sm" title={t("Edit connection and available models")}
              onClick={() => editor.startEdit(provider)}>{t("Manage")}</button>
            <button className="uiBtn sm iconSq danger" title={t("Remove this shared key")} aria-label={t("Remove shared key")}
              onClick={() => remove(provider)}>
              <Trash2Icon size={16} />
            </button>
          </ProviderRow>
        );
      })}
      {info ? (
        <Toggle icon={UserIcon} label={t("Guests may use it")} checked={!!info.guests} onChange={setGuests}
          hint={t("Off keeps guests without AI")}
          title={t("Guest accounts are open to anyone who can reach this server; with this on, they spend the shared connections too, a shared ChatGPT subscription included.")} />
      ) : null}
      {info && providers.length ? <>
        <Row icon={ActivityIcon} label={t("Allowance per account")} hint={t("Tokens a day on the shared keys; 0 = unlimited")}
          title={t("Input and output tokens each account may spend through the shared connections in any 24 hours, as the providers report them. An account's own keys are never counted.")}>
          <UnitInput unit={t("tokens")} min={0} label={t("Allowance per account")} value={String(info.allowance?.accounts ?? 0)}
            onCommit={(raw) => commitAllowance("accounts", raw)} />
        </Row>
        <Row icon={UserIcon} label={t("Allowance per guest")} hint={t("Tokens a day for each guest; 0 = unlimited")}
          title={t("The same allowance for guest accounts, which only use the shared keys while Guests may use it is on.")}>
          <UnitInput unit={t("tokens")} min={0} label={t("Allowance per guest")} value={String(info.allowance?.guests ?? 0)}
            onCommit={(raw) => commitAllowance("guests", raw)} />
        </Row>
      </> : null}
      {loadError ? <p className="settingsPaneHint aiKeysError" role="alert">{loadError}</p> : null}
    </Section>
    {editor.aiKeysForm ? (
      <SubDialog draft={editor.aiKeysForm} title={editor.aiKeysForm.id ? t("Edit shared key") : t("Connect a shared AI service")}
        onClose={editor.close}>
        <ProviderForm value={editor} onCancel={editor.close} />
      </SubDialog>
    ) : null}
  </>;
}

// Step 2 for a ChatGPT sign-in. OpenAI ends the sign-in on localhost:1455
// (Codex CLI's registered redirect). When this browser shares the server's
// machine, the server catches it and the form only waits. Anywhere else that
// page fails to load: its address is pasted here, which connects at once, or
// the device code is entered at OpenAI instead while the form waits.
function ChatGPTSignIn({ form, setForm, busy, start, connect }) {
  const started = !!form.oauthState;
  // Until the start answers, where the page runs predicts what it will say.
  const local = started ? !!form.oauthLocal : onThisMachine();
  const device = form.oauthDevice;
  const waiting = started && (local || device) && !form.oauthWaitError ? (
    <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Waiting for the sign-in…")}</div>
  ) : null;
  const where = (device?.verification_url || "").replace(/^https?:\/\//, "");
  const paste = (
    <Field label={t("Callback URL")} hint={t("The full address the sign-in ended on")}>
      <input
        className="aiKeyInput" type="text" spellCheck={false}
        placeholder="http://localhost:1455/auth/callback?code=…"
        value={form.oauthCallback || ""}
        onChange={(event) => setForm((f) => ({ ...f, oauthCallback: event.target.value }))}
        onPaste={(event) => {
          const text = event.clipboardData.getData("text").trim();
          if (!signInAddress(text)) return;
          event.preventDefault();
          setForm((f) => ({ ...f, oauthCallback: text }));
          connect(text);
        }}
      />
    </Field>
  );
  return <>
    <ol className="oauthInstructions">
      <li>{t("Open ChatGPT sign-in below and log in.")}</li>
      {local ? <li>{t("Gamma connects as soon as you are done. There is nothing to copy.")}</li> : <>
        <li>{t("It ends on a page that cannot load (“localhost refused to connect”). That is expected.")}</li>
        <li>{t("Copy that page's address and paste it below. Gamma connects right away.")}</li>
      </>}
    </ol>
    <div className="reportModalBtns settingsAlignStart">
      <button className="uiBtn" disabled={busy} onClick={start}>
        {started ? t("Re-open ChatGPT sign-in") : t("Open ChatGPT sign-in")}
      </button>
    </div>
    {local ? <>
      {waiting}
      {started ? (
        <details className="aiMoreOptions">
          <summary>{t("Did not connect? Paste the address instead")}</summary>
          {paste}
        </details>
      ) : null}
    </> : paste}
    {device ? (
      <Field group label={t("Or sign in with a code instead")}
        hint={t("Needs device code sign-in, turned on in ChatGPT's security settings (for a school or work account, by its administrator).")}>
        <CopyField label={t("One-time code")} value={device.user_code} action={t("Copy one-time code")} rows={1} />
        <a className="aiKeyLink" href={device.verification_url} target="_blank" rel="noopener noreferrer">
          {t("Enter it at {where}", { where })}<ExternalLinkIcon size={14} />
        </a>
        {waiting}
      </Field>
    ) : null}
    {form.oauthWaitError ? (
      <div className="aiKeyCheck aiKeysError" role="status"><XIcon size={14} /> {form.oauthWaitError}</div>
    ) : null}
  </>;
}

// The connect dialog, for the account's list and Settings →
// Server's shared one alike: 1 a service tile (Other opens the named
// services, a custom endpoint and its API format); 2 the API key — the
// provider's own placeholder and "Get a key at …" link (key_placeholder /
// key_url from the adapter or the service preset), checked live by the
// debounced model fetch — or the ChatGPT sign-in; 3 the models, the live
// list's first one picked when nothing is, with the name and test model
// under More options.
function ProviderForm({ value, onCancel }) {
  const {
    aiKeysForm,
    setAiKeysForm,
    aiKeysInfo,
    aiKeysBusy,
    aiKeysError,
    aiModelCatalog,
    formOauthPending,
    formModels,
    availModels,
    customModel,
    setCustomModel,
    aiProtocolOf,
    isOauthProto,
    startChatGPTAuth,
    connectSignIn,
    loadModelCatalog,
    addCatalogModel,
    removeModel,
    submitAiProvider,
  } = value;
  // Named services are a protocol plus a fixed endpoint (DeepSeek = the
  // OpenAI protocol at api.deepseek.com); an entry made from one is
  // recognized by that pair.
  const services = aiKeysInfo.services || [];
  const [service, setService] = React.useState(() => {
    if (aiKeysForm.custom) return "custom";
    const base = (aiKeysForm.base_url || "").replace(/\/$/, "");
    const preset = services.find((item) => item.protocol === aiKeysForm.protocol && item.base_url === base);
    if (preset) return preset.id;
    const protocol = aiProtocolOf(aiKeysForm.protocol);
    return base && base !== protocol?.default_base_url?.replace(/\/$/, "") ? "custom" : aiKeysForm.protocol;
  });
  const oauth = isOauthProto(aiKeysForm.protocol);
  const protocol = aiProtocolOf(aiKeysForm.protocol);
  const preset = services.find((item) => item.id === service);
  // A saved connection can't turn from sign-in into API key or back (the
  // server refuses it): editing offers only services of the same kind.
  const offered = (protocolId) => !aiKeysForm.id || isOauthProto(protocolId) === oauth;
  const tiles = aiServiceTiles(aiKeysInfo, { only: (item) => offered(item.id) });
  const tile = aiKeysInfo.protocols.some((item) => item.id === service) ? service : "other";
  const choose = (next) => {
    setService(next);
    const named = services.find((item) => item.id === next);
    if (named) setAiKeysForm((form) => ({ ...form, protocol: named.protocol, base_url: named.base_url, models: "", test_model: "" }));
    else if (next !== "custom") setAiKeysForm((form) => ({ ...form, protocol: next, base_url: "", models: "", test_model: "" }));
    else if (oauth) setAiKeysForm((form) => ({ ...form, protocol: "openai", base_url: "", models: "", test_model: "" }));
  };
  const pickTile = (next) => {
    if (next !== "other") choose(next);
    else if (tile !== "other") choose(services.find((item) => offered(item.protocol))?.id || "custom");
  };
  // What the key field shows: the preset's own hint, else the protocol's
  // when it points at the provider's own endpoint; a custom endpoint has none.
  const keyHelp = preset || (service === "custom" ? null : protocol);
  const keyUrl = keyHelp?.key_url || "";
  const keyWhere = keyUrl.replace(/^https?:\/\//, "").replace(/\/$/, "");
  const stored = aiKeysForm.id ? aiKeysInfo.providers?.find((item) => item.id === aiKeysForm.id) : null;
  // The live model list doubles as the key check: listing needs a key the
  // provider accepts, so a list is "the key works" and a refusal says why.
  const keyChecked = !oauth && !!(aiKeysForm.api_key.trim() || stored?.key_hint);
  // The first model of the live list is picked when none is, once per list:
  // a connection is never saved offering nothing, and no model name is
  // written into the code.
  const [autoPicked, setAutoPicked] = React.useState(false);
  React.useEffect(() => {
    const first = aiModelCatalog?.models?.[0];
    if (!first || modelList(aiKeysForm.models).length) return;
    addCatalogModel(first);
    setAutoPicked(true);
  }, [aiModelCatalog]); // eslint-disable-line react-hooks/exhaustive-deps
  const [moreOpen, setMoreOpen] = React.useState(() => !!(aiKeysForm.name || aiKeysForm.test_model));

  return (
    <div className="settingsForm">
      <Step n={1} title={t("Choose a service")}>
        <IconChoices label={t("AI service")} value={tile} onChange={pickTile} options={tiles} />
        {tile === "other" ? <Field label={t("Service")}>
          <MenuSelect block label={t("Service")} value={service} onChange={choose}
            options={[
              ...services.filter((item) => offered(item.protocol)).map((item) => [item.id, item.label]),
              ...(offered("openai") ? [["custom", t("Custom endpoint")]] : []),
            ]} />
        </Field> : null}
        {service === "custom" ? <Field label={t("API format")} hint={t("Use the format supported by your service")}>
          <MenuSelect block label={t("API protocol")} value={aiKeysForm.protocol}
            onChange={(next) => setAiKeysForm((form) => ({ ...form, protocol: next }))}
            options={aiKeysInfo.protocols.filter((item) => !isOauthProto(item.id)).map((item) => [item.id, item.label])} />
        </Field> : null}
      </Step>

      <Step
        n={2}
        title={oauth ? t("Sign in with ChatGPT") : t("Paste your API key")}
        hint={oauth
          ? t("No API key — usage is billed to your ChatGPT subscription.") : t("Stored on the server, never shown to the browser again.")}
      >
        {oauth ? (
          <ChatGPTSignIn form={aiKeysForm} setForm={setAiKeysForm} busy={aiKeysBusy}
            start={startChatGPTAuth} connect={connectSignIn} />
        ) : (
          <>
            <Field label={t("API key")} hint={aiKeysForm.id ? t("Leave empty to keep the current one") : null}>
              <PasswordInput
                autoComplete="new-password" spellCheck={false}
                placeholder={keyHelp?.key_placeholder || t("API key")}
                value={aiKeysForm.api_key}
                onChange={(event) => setAiKeysForm((form) => ({ ...form, api_key: event.target.value }))}
              />
            </Field>
            {keyUrl ? (
              <a className="aiKeyLink" href={keyUrl} target="_blank" rel="noopener noreferrer">
                {t("Get a key at {where}", { where: keyWhere })}<ExternalLinkIcon size={14} />
              </a>
            ) : null}
            {service === "custom" ? <Field label={t("Base URL")} hint={t("Optional — default {default_base_url}", { default_base_url: protocol?.default_base_url || "" })}>
              <input
                className="aiKeyInput" type="text" spellCheck={false}
                placeholder={protocol?.default_base_url || ""}
                value={aiKeysForm.base_url}
                onChange={(event) => setAiKeysForm((form) => ({ ...form, base_url: event.target.value }))}
              />
            </Field> : null}
            {keyChecked && aiModelCatalog ? (
              <div className={`aiKeyCheck ${aiModelCatalog.error ? "aiKeysError" : aiModelCatalog.models ? "aiTestOk" : ""}`} role="status">
                {aiModelCatalog.loading ? <><span className="transferSpin inline" /> {t("Checking the key…")}</>
                  : aiModelCatalog.models ? <><CheckIcon size={14} /> {tn("Key works · {n} model available", "Key works · {n} models available", aiModelCatalog.models.length)}</>
                  : <><XIcon size={14} /> {aiModelCatalog.error}</>}
              </div>
            ) : null}
          </>
        )}
      </Step>

      <Step
        n={3}
        title={t("Models")}
        hint={autoPicked && formModels.length
          ? t("The first model of your list is picked for you; add more for the chat menu.")
          : formModels.length
            ? t("Offered in the chat model menu.") : t("None picked yet — pick at least one to use this connection.")}
      >
        {formModels.length ? (
          <div className="aiModelChips">
            {formModels.map((model) => (
              <span className="categoryTag" key={model}>
                {model}
                <button className="uiClose uiCloseSm" title={t("Remove model")} aria-label={t("Remove {model}", { model: model })} onClick={() => removeModel(model)}><XIcon size={14} /></button>
              </span>
            ))}
          </div>
        ) : null}
        <div className="aiProvPwForm">
          <ModelPicker models={availModels} value={customModel}
            onChange={setCustomModel} onAdd={addCatalogModel} loading={aiModelCatalog?.loading} />
          <button
            className="uiBtn sm"
            disabled={!!aiModelCatalog?.loading || formOauthPending}
            title={formOauthPending ? t("Connect with ChatGPT first") : t("Fetch the models available to this credential")}
            onClick={loadModelCatalog}
          >
            {aiModelCatalog?.loading
              ? <><span className="transferSpin inline" /> {t("fetching…")}</>
              : aiModelCatalog?.models
                ? <><RefreshIcon size={14} /> {aiModelCatalog.models.length} {t("usable")}</>
                : <><RefreshIcon size={14} /> {t("Fetch")}</>}
          </button>
        </div>
        {aiModelCatalog?.error ? (
          <div className="reportModalHint settingsNoMargin">
            {keyChecked ? t("No model list without a working key.") : aiModelCatalog.error}{" "}
            <button className="searchToggle" title={t("Retry loading the model list")} onClick={loadModelCatalog}><RefreshIcon size={14} /></button>
          </div>
        ) : null}
        <details className="aiMoreOptions" open={moreOpen} onToggle={(event) => setMoreOpen(event.currentTarget.open)}>
          <summary>{t("More options — name, test model")}</summary>
          <Field label={t("Name")} hint={t('Optional — e.g. "work key"')}>
            <input
              className="aiKeyInput" type="text" spellCheck={false}
              value={aiKeysForm.name}
              onChange={(event) => setAiKeysForm((form) => ({ ...form, name: event.target.value }))}
            />
          </Field>
          <Field label={t("Test model")} hint={t("Used by the Test button and the login connection check")}>
            <MenuSelect
              label={t("Test model")}
              value={formModels.includes(aiKeysForm.test_model) ? aiKeysForm.test_model : ""}
              onChange={(model) => setAiKeysForm((form) => ({ ...form, test_model: model }))}
              options={[
                ["", t("Auto — metadata model, else first")],
                ...formModels.map((model) => [model, model]),
              ]}
            />
          </Field>
        </details>
      </Step>

      {aiKeysError ? <div className="settingsPaneHint aiKeysError">{aiKeysError}</div> : null}
      <div className="reportModalBtns">
        <button className="uiBtn" onClick={onCancel}>{t("Cancel")}</button>
        <button className="uiBtn primary" disabled={aiKeysBusy} onClick={submitAiProvider}>
          {aiKeysBusy
            ? t("Saving…")
            : !aiKeysForm.id || (oauth && (aiKeysForm.oauthCallback || "").trim()) ? t("Connect") : t("Save changes")}
        </button>
      </div>
    </div>
  );
}

const USAGE_KIND_LABELS = { chat: t("Chat"), translate: t("Translation"), metadata: t("Metadata"), cite: t("Citations"), test: t("Connection tests") };

// The shared allowance (GET /api/ai/usage's `allowance`, present while a
// shared entry applies): what the account spent on the server's keys in the
// rolling 24 hours, against the admin's limit when there is one.
function AllowanceRow({ allowance }) {
  const { used = 0, limit = 0, exhausted = false } = allowance;
  return (
    <Row icon={KeyIcon} label={t("Shared allowance")}
      hint={!limit
        ? t("{used} tokens in the last 24 h · no limit", { used: fmtTokens(used) })
        : exhausted
          ? t("Used up: {used} of {limit} tokens in the last 24 h", { used: fmtTokens(used), limit: fmtTokens(limit) })
          : t("{used} of {limit} tokens in the last 24 h", { used: fmtTokens(used), limit: fmtTokens(limit) })}
      title={t("The server's shared AI keys allow each account this many tokens in any 24 hours. Your own keys are not counted; add one under Connections to keep going.")}>
      {exhausted ? <span className="uiTag failed">{t("used up")}</span> : null}
    </Row>
  );
}

// Settings → AI → Token usage: what the account's AI calls cost in tokens,
// as the providers reported it (GET /api/ai/usage — one row per call in
// users.db, see gamma/ai_usage.py). Overall totals and a bar chart in one
// card with Reset, then the last 30 days by model
// and by kind. No prices: they differ per provider and change.
function AiUsageSection({ confirm, setStatus, canReset = true }) {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const load = React.useCallback(async () => {
    setBusy(true);
    try {
      setData(await apiJson(`${API}/ai/usage`));
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }, []);
  React.useEffect(() => { load(); }, [load]);

  function reset() {
    const run = async () => {
      try {
        await apiJson(`${API}/ai/usage`, { method: "DELETE" });
        setStatus?.(t("Token usage reset"));
        load();
      } catch (err) {
        setStatus?.(t("Couldn't reset token usage: {message}", { message: err.message }));
      }
    };
    if (confirm) {
      confirm({ title: T("Reset token usage"), message: T("Forget every recorded token count of this account? The providers' own dashboards are not affected."), confirmLabel: t("Reset"), danger: true, onConfirm: run });
    } else run();
  }

  const models = data?.models || [];
  const kinds = Object.entries(data?.kinds || {}).sort((a, b) => (b[1].input + b[1].output) - (a[1].input + a[1].output));
  return (
    <>
      <Section title={t("Token usage")} action={
        <button className="uiBtn sm" disabled={busy} title={t("Fetch the latest counts")} onClick={load}>
          <RefreshIcon size={14} /> {t("Refresh")}
        </button>} />
      {error ? <p className="settingsPaneHint aiKeysError" role="alert">{t("Usage unavailable: {error}", { error: error })}</p> : null}
      {!data && !error ? <p className="setNotice">{t("Loading…")}</p> : null}
      {data ? <>
        <UsageChart daily={data.daily} total={data.windows?.all} firstAt={data.first_at}
          keepDays={data.keep_days} onReset={canReset ? reset : null} busy={busy} />
        {data.allowance ? <AllowanceRow allowance={data.allowance} /> : null}
        {models.length ? (
          <div className="aiUsageTable" role="table" aria-label={t("Token usage by model, last 30 days")}>
            <div className="aiUsageRow aiUsageHeader" role="row">
              <span>{t("Model · last 30 days")}</span><span>{t("calls")}</span><span>{t("in")}</span><span>{t("out")}</span><span>{t("cached")}</span>
            </div>
            {models.map((m) => (
              <div className="aiUsageRow" role="row" key={`${m.provider_id}:${m.model}`} title={usageDetail(m)}>
                <span className="aiUsageModel">{m.model}{m.provider_name ? <em> · {m.provider_name}</em> : null}</span>
                <span>{m.calls}</span>
                <span>{fmtTokens(m.input)}</span>
                <span>{fmtTokens(m.output)}</span>
                <span>{cachedPercent(m) ? `${cachedPercent(m)}%` : "—"}</span>
              </div>
            ))}
            {kinds.length > 1 ? kinds.map(([kind, u]) => (
              <div className="aiUsageRow aiUsageKind" role="row" key={kind} title={usageDetail(u)}>
                <span className="aiUsageModel">{USAGE_KIND_LABELS[kind] || kind}</span>
                <span>{u.calls}</span>
                <span>{fmtTokens(u.input)}</span>
                <span>{fmtTokens(u.output)}</span>
                <span>{cachedPercent(u) ? `${cachedPercent(u)}%` : "—"}</span>
              </div>
            )) : null}
          </div>
        ) : null}
      </> : null}
    </>
  );
}

export function AiSettings({ value, confirm, setStatus }) {
  const activeKeyId = value.aiKeysInfo?.providers.some((item) => item.id === value.aiProvider)
    ? value.aiProvider
    : value.aiKeysInfo?.providers[0]?.id;
  const canEdit = value.aiKeysInfo?.can_edit;
  const providers = value.aiKeysInfo?.providers || [];
  return (
    <>
      <Section title={t("Connections")} />

      {!value.aiKeysInfo && !value.aiKeysError ? <Empty icon={KeyIcon}>{t("Loading…")}</Empty> : null}
      {value.aiKeysInfo ? (
        <>
          {providers.length === 0 && !value.aiKeysForm ? (
            <Empty icon={KeyIcon}>
              {canEdit ? <>
                <span>{t("No AI connection yet.")}</span>
                <button className="uiBtn primary" onClick={() => value.startAdd()}>{t("+ Add provider")}</button>
              </> : t("Guest accounts cannot store API keys. Ask the admin for an account.")}
            </Empty>
          ) : null}
          {providers.map((provider) => {
            const test = value.aiKeyTests?.[provider.id];
            const usage = value.aiKeyUsage?.[provider.id];
            const active = activeKeyId === provider.id;
            // A shared entry is read-only here: an admin edits it under
            // Settings → Server.
            const own = canEdit && !provider.shared;
            return (
              <ProviderRow key={provider.id} provider={provider} active={active}
                protocol={value.aiProtocolOf(provider.protocol)} oauth={value.isOauthProto(provider.protocol)}
                test={test} usage={usage} onFix={own ? () => value.startEdit(provider) : null}
                radio={providers.length > 1 ? (
                  <input
                    type="radio"
                    className="aiProvRadio"
                    name="activeAiKey"
                    checked={active}
                    onChange={() => value.setAiProvider(provider.id)}
                    title={t("Use this key for AI requests")}
                  />
                ) : null}>
                {own ? <>
                  <button className="uiBtn sm" disabled={value.aiKeysBusy || test?.busy}
                    title={t("Send a tiny AI request through this credential to check it still works")}
                    onClick={() => value.testAiProvider(provider)}>
                    {t("Test")}
                  </button>
                  <button className="uiBtn sm" disabled={value.aiKeysBusy || usage?.busy}
                    title={t("Query remaining allowance; subscription percentages are available for ChatGPT sign-in providers")}
                    onClick={() => value.queryAiProviderUsage(provider)}>
                    {t("Usage")}
                  </button>
                  <button className="uiBtn sm" disabled={value.aiKeysBusy}
                    title={t("Edit connection and available models")} onClick={() => value.startEdit(provider)}>{t("Manage")}</button>
                  <button className="uiBtn sm iconSq danger" disabled={value.aiKeysBusy} title={t("Remove this key")}
                    aria-label={t("Remove key")} onClick={() => value.deleteAiProvider(provider)}>
                    <Trash2Icon size={16} />
                  </button>
                </> : null}
              </ProviderRow>
            );
          })}
          {canEdit && providers.length ? (
            <div className="reportModalBtns settingsAlignStart">
              <button className="uiBtn primary" onClick={() => value.startAdd()}>{t("+ Add provider")}</button>
            </div>
          ) : null}
          {canEdit && providers.length ? (
            <Section title={t("Connection check")} scope="account" prefs={SECTION_PREFS.connections["Connection check"]}>
              <Row icon={RefreshIcon} label={t("Check at login")}
                hint={t("Verify the active provider when Gamma opens")}
                title={t("Runs a connection check on the active provider at login; a failure (expired ChatGPT sign-in, rejected key, unreachable provider) shows a warning in the chat window instead of surfacing as a broken chat later. The credential check is free — OAuth entries query subscription usage, API keys list models; the test request sends a tiny completion (through the provider's test model — by default your metadata model) and spends a few tokens.")}>
                <MenuSelect
                  label={t("Check at login")}
                  value={value.aiLoginCheck}
                  onChange={value.setAiLoginCheck}
                  options={[
                    ["ping", t("Credential check (free)")],
                    ["test", t("Test request (uses tokens)")],
                    ["off", t("Off")],
                  ]}
                />
              </Row>
            </Section>
          ) : null}
          {value.aiKeysForm ? (
            <SubDialog draft={value.aiKeysForm}
              title={value.aiKeysForm.id ? t("Edit key") : t("Connect an AI service")}
              onClose={value.close}
            >
              <ProviderForm value={value} onCancel={value.close} />
            </SubDialog>
          ) : null}
        </>
      ) : null}
      {providers.length ? <>
      <Section title={t("Models")} scope="browser">
        {(value.aiModels || []).length ? <Row icon={SparklesIcon} label={t("Default chat model")}
          hint={t("Also used by citations and generated titles")}>
          <MenuSelect label={t("Default chat model")} value={value.chatModel} onChange={value.setChatModel}
            options={(value.aiModels || []).map((model) => [model.id, model.model])} />
        </Row> : <p className="setNotice">{t("Pick models on the connection (Manage) to choose one here.")}</p>}
        <Row icon={PaperIcon} label={t("Metadata model")}
          hint={t("Used only when identifiers cannot resolve the paper")}
          title={t("Metadata first tries arXiv and DOI records. This model is used only when metadata has to be AI-extracted from PDF text; a fast, cheap model is usually enough.")}>
          <MenuSelect
            label={t("Metadata model")}
            value={value.metaModel && (value.aiModels || []).some((model) => model.id === value.metaModel)
              ? value.metaModel : ""}
            onChange={value.setMetaModel}
            options={[
              ["", t("Same as chat: {default}", { default: (value.aiModels || []).find((m) => m.id === value.chatModel)?.model || t("provider default") })],
              ...(value.aiModels || []).map((model) => [model.id, model.model]),
            ]}
          />
        </Row>
        <Row icon={MicIcon} label={t("Dictation model")}
          hint={t("For the chat mic button; needs an OpenAI key")}
          title={t("gpt-4o-transcribe is what ChatGPT dictation uses; it needs an OpenAI-protocol provider key.")}>
          <MenuSelect
            label={t("Dictation model")} value={value.dictationModel} onChange={value.setDictationModel}
            options={[
              ["gpt-4o-transcribe", "gpt-4o-transcribe"],
              ["gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe"],
              ["whisper-1", "whisper-1"],
            ]}
          />
        </Row>
        <Row icon={GlobeIcon} label={t("Dictation language")}
          hint={t("Naming the language improves accuracy")}
          title={t("Telling the model the spoken language improves accuracy; auto-detect handles mixed or unlisted languages.")}>
          <MenuSelect
            label={t("Dictation language")} value={value.dictationLang} onChange={value.setDictationLang}
            options={[
              ["", `${t("Display language")} (${DICTATION_LANGS.find(([code]) => code === getLocale())?.[1] || getLocale()})`],
              ["auto", t("Auto-detect")],
              ...DICTATION_LANGS,
            ]}
          />
        </Row>
      </Section>
      {/* Guests see their usage too (the shared allowance), without Reset. */}
      <AiUsageSection confirm={confirm} setStatus={setStatus} canReset={!!value.aiKeysInfo?.can_edit} />
      </> : null}
      {!value.aiKeysForm && value.aiKeysError ? <div className="settingsPaneHint aiKeysError">{value.aiKeysError}</div> : null}
    </>
  );
}
