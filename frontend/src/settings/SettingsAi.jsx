// Settings → AI › Models: the model each task uses (any connection's: the
// account's roles, ai/useAiModels.js), then the connections that supply
// the models, then the connection check at login. The agents on the
// account's computers are the pane before it, Local agents
// (SettingsAgents.jsx), where it has one to show. The account's own list,
// its Test and Usage results live in App.jsx (the aiKeys* group), its
// connect dialog in useProviderEditor (providerEditor.js) and
// ProviderForm.jsx — these components only render them; the server's
// shared entries appear there as read-only cards. Token usage is
// SettingsAiUsage.jsx.
// SharedAiProviderSettings is Settings → Server's list of those shared
// entries (/api/admin/ai-providers), the same rows and the same form through
// the same hook over its own state.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { MenuSelect } from "../shared/ui/Menus";
import { failureCopy, fixLabel } from "../chat/chatErrors";
import { Section, SubDialog, Empty, PercentMeter, Row, UnitInput } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { useSettingsRead } from "./settingsReads.js";
import { accessSummary, modelList, useProviderEditor } from "./providerEditor.js";
import { ProviderForm } from "./ProviderForm.jsx";
import { signInWords } from "./aiServices.js";
import { ActivityIcon, BanIcon, CheckIcon, GlobeIcon, KeyIcon, LanguagesIcon, MessageSquareIcon, MicIcon, PaperIcon, PenIcon, PlusIcon, RefreshIcon, StarIcon, Trash2Icon, UserIcon } from "../shared/ui/Icons";
import { billingLabel } from "../chat/tokenUsage";
import { connectionsInUse, taskRow } from "../ai/aiModels.js";
import { ChatModels, TaskModelMenu } from "../ai/ModelMenu.jsx";
import { ProviderLogo } from "../ai/ProviderLogo.jsx";
import { T, fmtDate, getLocale, t } from "../shared/i18n/i18n.js";

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

// What is left of a subscription's plan, a bar per window (also Settings →
// AI → Usage's Subscriptions tile, SettingsAiUsage.jsx).
export function ProviderUsage({ usage }) {
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

// A connection's model tag, with what the check of whether it calls tools
// found (`check`, the entry's tool_checks entry): it does (✓), it can't (⊘,
// the reason on hover), or nothing yet. With `onCheck` the tag is a button
// that checks it again (Test with the model).
function ModelTag({ model, check, onCheck, busy }) {
  const date = check?.at ? fmtDate(check.at, { dateStyle: "medium" }) : "";
  const status = !check ? t("Tools not checked yet: the first chat with tools checks them")
    : check.ok ? t("Uses tools · checked {date}", { date })
    : t("No tools: {reason} · checked {date}", { reason: check.reason, date });
  const glyph = !check ? null : check.ok ? <CheckIcon size={14} /> : <BanIcon size={14} />;
  if (!onCheck) return <span className="categoryTag aiModelTag" title={status}>{glyph}{model}</span>;
  return (
    <button type="button" className="categoryTag aiModelTag" disabled={busy}
      title={check ? t("{status}. Click to check again.", { status }) : t("{status}. Click to check it now.", { status })}
      onClick={(event) => { event.preventDefault(); onCheck(model); }}>
      {glyph}{model}
    </button>
  );
}

// One connection's card: avatar, its status dot and name with how it is
// paid for, the credential line, its models (each with whether it calls
// tools), the last Test result or login check and the usage windows, then
// the caller's buttons. `test` is the last Test's result, `health` the
// login check's answer (App's aiHealth), the dot whichever is newer to
// say: green while the connection works, red with the fix while it does
// not. `onFix` opens the entry's editor from a failure; `onCheckModel(model)`
// checks a model for tools from its tag. A failure reads as the chat's
// error card does for its kind (chat/chatErrors.js), the provider's own
// words on hover; a tool check's says whether the model can use tools.
function ProviderRow({ provider, protocol, oauth, test, health = null, usage, onFix, onCheckModel = null, access = null, children }) {
  const models = modelList(provider.models);
  // The login check speaks only of a failure; a Test of either.
  const result = test || (health && !health.ok ? health : null);
  const failed = result && !result.busy && !result.ok && result.kind
    ? failureCopy(result.kind, { provider: provider.label || provider.protocol, auth: oauth ? "oauth" : "key" }) : null;
  const fix = failed && onFix && ["key", "signin", "connection"].includes(failed.fix) ? failed.fix : "";
  const checked = test && !test.busy ? test : health;
  const dot = !checked ? "" : checked.ok && checked.tools?.ok !== false ? "ok" : checked.ok ? "" : "failed";
  const billing = provider.billing || (provider.shared ? "shared" : "");
  return (
    <div className="aiProvRow">
      <span className="aiProvAvatar">
        <ProviderLogo service={provider.service} size={20} />
      </span>
      <span className="aiProvMeta">
        <span className="aiProvName">
          {dot ? <i className={`aiProvDot ${dot}`} role="img"
            aria-label={dot === "ok" ? t("Working") : failed?.headline || t("Not working")}
            title={dot === "ok" ? t("Working at the last check") : failed?.headline || result?.error || t("Not working")} /> : null}
          {provider.label || provider.protocol}
          {billingLabel(billing) ? (
            <span className="uiTag" title={billing === "shared"
              ? t("An administrator added this connection for everyone on this server. Its key is never shown; its tokens count on your account.")
              : undefined}>
              {billingLabel(billing)}
            </span>
          ) : null}
        </span>
        <span className="aiProvDesc">
          {oauth
            ? t("{connected} · {plan}", { plan: signInWords(provider.protocol).plan,
              connected: provider.oauth_connected ? t("signed in{account}", { account: provider.account ? ` as ${provider.account}` : "" }) : t("not connected") })
            : `${provider.key_hint ? `key ${provider.key_hint} · ` : provider.shared ? "" : `${t("no key")} · `}${protocol?.label || provider.protocol}`}
          {provider.base_url ? ` · ${provider.base_url}` : ""}
        </span>
        <span className="aiProvDesc aiProvModels">
          <span className="aiProvModelsLabel">{t("Models")}</span>
          {models.length
            ? models.map((model) => (
              <ModelTag key={model} model={model} check={provider.tool_checks?.[model]} onCheck={onCheckModel}
                busy={test?.busy} />
            ))
            : <span className="aiKeysError">{onFix ? t("none picked — edit to choose") : t("none picked")}</span>}
        </span>
        {access ? (
          <span className="aiProvDesc aiProvModels">
            <span className="aiProvModelsLabel">{t("Access")}</span>
            {accessSummary(access)}
          </span>
        ) : null}
        {result ? (
          <span
            className={`aiProvDesc ${result.busy ? "" : result.ok && result.tools?.ok !== false ? "aiTestOk" : "aiKeysError"}`}
            title={!result.busy && !result.ok ? result.error : result.tools?.ok === false ? result.tools.reason : undefined}
          >
            {result.busy
              ? t("Testing…") : result.ok && result.tools
                ? result.tools.ok ? t("✓ {model} uses tools · {seconds}s", { model: result.model, seconds: (result.latency_ms / 1000).toFixed(1) })
                  : t("✗ {model} can't use tools", { model: result.model })
              : result.ok
                ? `✓ working · ${result.model} · ${(result.latency_ms / 1000).toFixed(1)}s`
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
                ) : `✗ ${result.error}`}
          </span>
        ) : null}
        {usage ? <ProviderUsage usage={usage} /> : null}
      </span>
      {children ? <span className="aiProvActions">{children}</span> : null}
    </div>
  );
}

// The allowance is stored in tokens but set in millions: a real daily
// budget runs to eight or nine digits, which no longer reads as a number
// in a settings box. The backend's ceiling is 1e9 tokens = 1000 M.
const ALLOWANCE_M = 1_000_000;
const ALLOWANCE_MAX_M = 1000;
const inMillions = (tokens) => String(Math.max(0, Number(tokens) || 0) / ALLOWANCE_M);

// Settings → Server → Shared AI provider (admins): connections every
// account on the server may use next to its own (backend
// gamma/ai_settings.py). An API key, write-only like an account's, or a
// ChatGPT subscription signed in here (its e-mail shown to admins only),
// each saying who may use it (every account, guests too, or the accounts
// named). The allowance rows meter them per account per day.
// The list is the Server pane's read of /api/admin/ai-providers
// (settingsReads.js), kept while the dialog is open.
export function SharedAiProviderSettings({ setStatus, confirm }) {
  const base = `${API}/admin/ai-providers`;
  const { data: info, error: readError, set: setInfo } = useSettingsRead("/admin/ai-providers");
  const [ownError, setLoadError] = React.useState("");
  const loadError = ownError || (!info ? readError : "");
  const [tests, setTests] = React.useState({});
  const [usage, setUsage] = React.useState({});
  const editor = useProviderEditor({ info, setInfo, base, shared: true, onSaved: () => setStatus?.(t("Shared AI provider saved.")),
    onConnected: (entry) => test(entry) });
  // `toolsModel`: check that model for tools instead (its tag), then read
  // the list again for the verdict.
  async function test(p, toolsModel = "") {
    setTests((prev) => ({ ...prev, [p.id]: { busy: true } }));
    let result;
    try {
      result = await apiJson(`${API}/ai/providers/${encodeURIComponent(p.id)}/test`, toolsModel ? {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tools_model: toolsModel }),
      } : { method: "POST" });
    } catch (err) {
      result = { ok: false, error: err.message };
    }
    setTests((prev) => ({ ...prev, [p.id]: result }));
    if (result.tools) {
      try { setInfo(await apiJson(base)); } catch (err) { setLoadError(err.message); }
    }
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
  // The shared allowance: tokens per account (or guest) per rolling 24 h on
  // the shared entries, 0 = unlimited (docs/dev/guests.md). The box counts
  // in millions, the API in tokens. Only the changed field is sent; an
  // invalid or out-of-range entry snaps back to the stored value.
  async function commitAllowance(key, raw) {
    const m = Number(String(raw).trim() || NaN);
    if (!Number.isFinite(m) || m < 0 || m > ALLOWANCE_MAX_M) return;
    const n = Math.round(m * ALLOWANCE_M);
    if (n === (info?.allowance?.[key] ?? 0)) return;
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
      <button className="uiBtn sm" onClick={() => editor.startAdd("openai")}><PlusIcon size={14} />{t("Add provider")}</button>
    ) : null}>
      {!info && !loadError ? <p className="setNotice loadingHint">{t("Loading…")}</p> : null}
      {info && !providers.length ? <Empty icon={KeyIcon}>{t("No shared connection. Each account uses its own keys.")}</Empty> : null}
      {providers.map((provider) => {
        const el = tests[provider.id];
        const oauth = editor.isOauthProto(provider.protocol);
        return (
          <ProviderRow key={provider.id} provider={{ ...provider, shared: false }} oauth={oauth} access={provider.access}
            protocol={editor.aiProtocolOf(provider.protocol)} test={el} usage={usage[provider.id]}
            onFix={() => editor.startEdit(provider)} onCheckModel={(model) => test(provider, model)}>
            <button className="uiBtn sm" disabled={el?.busy}
              title={t("Send a tiny AI request through this connection to check it still works; the tokens count on your account")}
              onClick={() => test(provider)}>{t("Test")}</button>
            {provider.quota ? (
              <button className="uiBtn sm" disabled={usage[provider.id]?.busy}
                title={t("What is left of the subscription's usage windows")}
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
      {info && providers.length ? <>
        <Row icon={ActivityIcon} label={t("Allowance per account")} hint={t("Tokens a day on the shared keys; 0 = unlimited")}
          title={t("Input and output tokens each account may spend through the shared connections in any 24 hours, as the providers report them. An account's own keys are never counted.")}>
          <UnitInput unit={t("M tokens")} min={0} max={ALLOWANCE_MAX_M} step="any" label={t("Allowance per account")}
            value={inMillions(info.allowance?.accounts)} onCommit={(raw) => commitAllowance("accounts", raw)} />
        </Row>
        <Row icon={UserIcon} label={t("Allowance per guest")} hint={t("Tokens a day for each guest; 0 = unlimited")}
          title={t("The same allowance for guest accounts, on the shared connections whose access admits guests. Guest accounts are open to anyone who can reach this server.")}>
          <UnitInput unit={t("M tokens")} min={0} max={ALLOWANCE_MAX_M} step="any" label={t("Allowance per guest")}
            value={inMillions(info.allowance?.guests)} onCommit={(raw) => commitAllowance("guests", raw)} />
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

// Dictation goes through a connection that transcribes (an OpenAI key,
// gamma/ai_settings.transcriber_for), named when the server says which.
function dictationHint(info) {
  if (info?.transcriber) return t("Through {connection}", { connection: info.transcriber.provider_name });
  return info?.transcribe ? t("Through your OpenAI connection") : t("Needs an OpenAI key: no connection transcribes");
}

export function AiSettings({ value }) {
  const canEdit = value.aiKeysInfo?.can_edit;
  const providers = value.aiKeysInfo?.providers || [];
  const ai = value.models;
  const info = ai?.aiInfo;
  // The connections the tasks use: what the check at login asks about.
  const checked = connectionsInUse(info).map((id) => providers.find((p) => p.id === id)?.label
    || ai.models.find((m) => m.provider === id)?.provider_name).filter(Boolean);
  return (
    <>
      <Section title={t("Models for each task")}>
        {!info ? <p className="setNotice loadingHint">{t("Loading…")}</p> : <>
          {!ai.models.length ? <p className="setNotice">{t("Add a connection below to choose models.")}</p> : null}
          <Row icon={MessageSquareIcon} label={t("Chat")} {...taskRow(info, "chat")}
            title={t("The model a new chat starts with, and the one titles and structured notes use. The chat's model chip changes it too, on every device.")}>
            <TaskModelMenu role="chat" ai={ai} label={t("Chat")} />
          </Row>
          <Row icon={StarIcon} label={t("Chat models")} wide
            hint={ai.favorites.length ? t("The chat's model chip offers these to switch between.")
              : t("Add the models you switch between: the chat's model chip offers them.")}
            title={t("The models the chat's model chip lists beside the one in use, to switch with a click. A star in any model menu adds one too.")}>
            <ChatModels ai={ai} />
          </Row>
          <Row icon={PaperIcon} label={t("Metadata & citations")} {...taskRow(info, "metadata")}
            title={t("Metadata first tries arXiv and DOI records. This model checks which of the records the PDF's text names is this document, reads the metadata off the PDF when no registry has it, and writes the slide citation; a fast, cheap model is usually enough.")}>
            <TaskModelMenu role="metadata" ai={ai} label={t("Metadata & citations")} />
          </Row>
          <Row icon={LanguagesIcon} label={t("Translation")} {...taskRow(info, "translate")}
            title={t("What translates page text: a model, or a translation service (Settings → Language & translation sets them up).")}>
            <TaskModelMenu role="translate" ai={ai} label={t("Translation")} />
          </Row>
          <Row icon={PenIcon} label={t("Handwriting")} {...taskRow(info, "ink")}
            title={t("Redraw as shapes and Convert to text. Converting to text needs a model that sees pictures.")}>
            <TaskModelMenu role="ink" ai={ai} label={t("Handwriting")} />
          </Row>
        </>}
        <Row icon={MicIcon} label={t("Dictation")} scope="browser" hint={dictationHint(info)}
          className={info && !info.transcribe ? "aiTaskMissing" : ""}
          title={t("The chat's mic button. gpt-4o-transcribe is what ChatGPT dictation uses; it needs an OpenAI-protocol key.")}>
          <MenuSelect
            label={t("Dictation")} value={value.dictationModel} onChange={value.setDictationModel}
            options={[
              ["gpt-4o-transcribe", "gpt-4o-transcribe"],
              ["gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe"],
              ["whisper-1", "whisper-1"],
            ]}
          />
        </Row>
        <Row icon={GlobeIcon} label={t("Dictation language")} scope="browser"
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

      {/* Add provider sits on the section's rule once there is a list, as a
          section's action does everywhere in Settings; before, the empty
          state offers it. */}
      <Section title={t("Connections")} action={canEdit && providers.length ? (
        <button className="uiBtn sm" onClick={() => value.startAdd()}><PlusIcon size={14} />{t("Add provider")}</button>
      ) : null} />

      {!value.aiKeysInfo && !value.aiKeysError ? <Empty icon={KeyIcon}>{t("Loading…")}</Empty> : null}
      {value.aiKeysInfo ? (
        <>
          {providers.length === 0 && !value.aiKeysForm ? (
            <Empty icon={KeyIcon}>
              {canEdit ? <>
                <span>{t("No AI connection yet.")}</span>
                <button className="uiBtn primary" onClick={() => value.startAdd()}><PlusIcon size={14} />{t("Add provider")}</button>
              </> : t("Guest accounts cannot store API keys. Ask the admin for an account.")}
            </Empty>
          ) : null}
          {providers.map((provider) => {
            const test = value.aiKeyTests?.[provider.id];
            const usage = value.aiKeyUsage?.[provider.id];
            // A shared entry is read-only here: an admin edits it under
            // Settings → Server.
            const own = canEdit && !provider.shared;
            return (
              <ProviderRow key={provider.id} provider={provider}
                protocol={value.aiProtocolOf(provider.protocol)} oauth={value.isOauthProto(provider.protocol)}
                test={test} health={value.aiHealth?.[provider.id] || null} usage={usage}
                onFix={own ? () => value.startEdit(provider) : null}
                onCheckModel={own ? (model) => value.testAiProvider(provider, model) : null}>
                {own ? <>
                  <button className="uiBtn sm" disabled={value.aiKeysBusy || test?.busy}
                    title={t("Send a tiny AI request through this credential to check it still works")}
                    onClick={() => value.testAiProvider(provider)}>
                    {t("Test")}
                  </button>
                  {provider.quota ? (
                    <button className="uiBtn sm" disabled={value.aiKeysBusy || usage?.busy}
                      title={t("What is left of the subscription's usage windows")}
                      onClick={() => value.queryAiProviderUsage(provider)}>
                      {t("Usage")}
                    </button>
                  ) : null}
                  <button className="uiBtn sm" disabled={value.aiKeysBusy}
                    title={t("Edit connection and available models")} onClick={() => value.startEdit(provider)}>{t("Manage")}</button>
                  <button className="uiBtn sm iconSq danger" disabled={value.aiKeysBusy} title={t("Remove this connection")}
                    aria-label={t("Remove connection")} onClick={() => value.deleteAiProvider(provider)}>
                    <Trash2Icon size={16} />
                  </button>
                </> : null}
              </ProviderRow>
            );
          })}
          {canEdit && providers.length ? (
            <Section title={t("Connection check")} scope="account" prefs={SECTION_PREFS.connections["Connection check"]}>
              <Row icon={RefreshIcon} label={t("Check at login")}
                hint={checked.length ? t("Checks {connections} when Gamma opens", { connections: checked.join(", ") })
                  : t("Checks the connections your tasks use when Gamma opens")}
                title={t("Runs a connection check on each connection a task's model uses, at login; a failure (expired ChatGPT sign-in, rejected key, unreachable provider) shows a warning in the chat window and a red dot on the connection instead of surfacing as a broken chat later. The credential check is free — OAuth entries query subscription usage, API keys list models; the test request sends a tiny completion (through the connection's test model — by default the model a task uses there) and spends a few tokens.")}>
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
      {!value.aiKeysForm && value.aiKeysError ? <div className="settingsPaneHint aiKeysError">{value.aiKeysError}</div> : null}
    </>
  );
}
