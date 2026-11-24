// The connect dialog, for the account's list (SettingsAi.jsx AiSettings)
// and Settings → Server's shared one (SharedAiProviderSettings) alike: the
// service, the credential (an API key or a sign-in), the models
// and, for a shared entry, who may use it. Its state lives in
// useProviderEditor (providerEditor.js); this module only renders it.
import React from "react";
import { MenuSelect } from "../shared/ui/Menus";
import { composing } from "../shared/lib/hotkeys.js";
import { billingLabel } from "../chat/tokenUsage";
import { AccountPicker, Step, Field, CopyField, GlyphChoices, IconChoices, PasswordInput, Segmented } from "./SettingsKit";
import { useAccounts } from "./SettingsWorkspace";
import { modelList, onThisMachine } from "./providerEditor.js";
import { autoBilling, formService, keyOptional, serviceSections, signInAddress, signInWords } from "./aiServices.js";
import { ModelFacts } from "../ai/ModelMenu.jsx";
import { ProviderLogo } from "../ai/ProviderLogo.jsx";
import { matchesWords } from "../ai/modelMenu.js";
import { CheckIcon, ExternalLinkIcon, GlobeIcon, KeyIcon, MonitorIcon, RefreshIcon, SparklesIcon, UserIcon, UsersIcon, XIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { T, t, tn } from "../shared/i18n/i18n.js";

// Step 2 for a sign-in (ChatGPT, Gemini), in the protocol's own words
// (aiServices.js SIGN_INS). When this browser shares the server's machine,
// the server catches the redirect and the form only waits. Anywhere else
// ChatGPT ends on localhost:1455 (Codex CLI's registered redirect), a page
// that fails to load, and Google on its own page showing a code: either is
// pasted here, which connects at once, or ChatGPT's device code is entered
// at OpenAI instead while the form waits.
function SignInStep({ form, setForm, busy, start, connect }) {
  const words = signInWords(form.protocol);
  const started = !!form.oauthState;
  // Until the start answers, where the page runs predicts what it will say.
  const local = started ? !!form.oauthLocal : onThisMachine();
  const device = form.oauthDevice;
  const code = words.paste === "code";
  const waiting = started && (local || device) && !form.oauthWaitError ? (
    <div className="aiKeyCheck" role="status"><span className="transferSpin inline" /> {t("Waiting for the sign-in…")}</div>
  ) : null;
  const where = (device?.verification_url || "").replace(/^https?:\/\//, "");
  const paste = (
    <Field label={code && !local ? t("Authorization code") : t("Callback URL")}
      hint={code && !local ? t("The code Google shows once you allow it") : t("The full address the sign-in ended on")}>
      <input
        className="aiKeyInput" type="text" spellCheck={false}
        placeholder={code && !local ? "4/0A…" : code ? "http://127.0.0.1:…/oauth2callback?code=…" : "http://localhost:1455/auth/callback?code=…"}
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
      <li>{t("Open the sign-in below and log in.")}</li>
      {local ? <li>{t("Gamma connects as soon as you are done. There is nothing to copy.")}</li> : code ? (
        <li>{t("Google then shows a code. Copy it and paste it below. Gamma connects right away.")}</li>
      ) : <>
        <li>{t("It ends on a page that cannot load (“localhost refused to connect”). That is expected.")}</li>
        <li>{t("Copy that page's address and paste it below. Gamma connects right away.")}</li>
      </>}
    </ol>
    <div className="reportModalBtns settingsAlignStart">
      <button className="uiBtn" disabled={busy} onClick={start}>
        {started ? words.reopen : words.open}
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

// The connect dialog's groups of services, each caption with its icon: a
// subscription's sparkles (as Settings → AI → Usage draws subscriptions),
// a key, a computer.
const SECTION_ICONS = { signin: SparklesIcon, key: KeyIcon, local: MonitorIcon };

// The connect dialog, for the account's list and Settings → Server's shared
// one alike: 1 the service, a tile per way to connect with its logo in three
// groups (serviceSections in aiServices.js), searchable, with a vendor's plans
// side by side under its tile and, for a custom endpoint, its API format;
// 2 the API key — the provider's own placeholder and "Get a key at …" link
// (key_placeholder / key_url from the adapter or the service preset),
// checked live by the debounced model fetch; optional for a local preset
// and a custom endpoint — or a sign-in (ChatGPT, Gemini); 3 the models, a
// searchable checklist of the live list with what the catalogs know of each,
// the list's first one picked when nothing is, and any name added by hand
// (a saved one the list lacks stays listed), with the name, test model and
// billing under More options.
export function ProviderForm({ value, onCancel }) {
  const {
    aiKeysForm,
    setAiKeysForm,
    aiKeysInfo,
    aiKeysBusy,
    aiKeysError,
    aiModelCatalog,
    formOauthPending,
    formModels,
    customModel,
    setCustomModel,
    aiProtocolOf,
    isOauthProto,
    startSignIn,
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
  const service = formService(aiKeysForm, aiKeysInfo);
  const oauth = isOauthProto(aiKeysForm.protocol);
  const protocol = aiProtocolOf(aiKeysForm.protocol);
  const preset = services.find((item) => item.id === service);
  // A saved connection can't turn from sign-in into API key or back (the
  // server refuses it): editing offers only services of the same kind.
  const offered = (protocolId) => !aiKeysForm.id || isOauthProto(protocolId) === oauth;
  const sections = serviceSections(aiKeysInfo, { only: offered });
  const card = sections.flatMap((section) => section.cards).find((item) => item.plans.some((plan) => plan.id === service));
  const [serviceQuery, setServiceQuery] = React.useState("");
  const matches = (item) => matchesWords(serviceQuery, ...[item.label, item.vendor, item.hint, billingLabel(item.billing), ...item.plans.map((plan) => plan.label)]
    .filter(Boolean).map((text) => t(text)));
  const shown = sections.map((section) => ({ ...section, cards: section.cards.filter(matches) })).filter((section) => section.cards.length);
  // A preset's note, else the protocol's own (the Gemini sign-in's terms).
  const note = preset?.note ? t(preset.note) : !preset && service !== "custom" && protocol?.note ? t(protocol.note) : null;
  const choose = (next) => {
    const named = services.find((item) => item.id === next);
    const reset = { service: next, models: "", test_model: "", billing: "" };
    if (named) setAiKeysForm((form) => ({ ...form, ...reset, protocol: named.protocol, base_url: named.base_url }));
    else if (next !== "custom") setAiKeysForm((form) => ({ ...form, ...reset, protocol: next, base_url: "" }));
    else setAiKeysForm((form) => ({ ...form, ...reset, ...(oauth ? { protocol: "openai", base_url: "" } : {}) }));
  };
  // A card keeps its plan when it already holds the one picked, else
  // starts on its first.
  const pickCard = (id) => {
    const next = sections.flatMap((section) => section.cards).find((item) => item.id === id);
    if (next && next !== card) choose(next.plans[0].id);
  };
  const keyless = !oauth && keyOptional(aiKeysForm, aiKeysInfo);
  // What the key field shows: the preset's own hint, else the protocol's
  // when it points at the provider's own endpoint; a custom endpoint has none.
  const keyHelp = preset || (service === "custom" ? null : protocol);
  const keyUrl = keyHelp?.key_url || "";
  const keyWhere = keyUrl.replace(/^https?:\/\//, "").replace(/\/$/, "");
  const stored = aiKeysForm.id ? aiKeysInfo.providers?.find((item) => item.id === aiKeysForm.id) : null;
  const typedKey = !!(aiKeysForm.api_key.trim() || stored?.key_hint);
  // The live model list doubles as the key check: listing needs a key the
  // provider accepts, so a list is "the key works" and a refusal says why.
  // An endpoint that needs no key is checked the same way, without one.
  const keyChecked = !oauth && (typedKey || keyless);
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
  // The Models step's list: the names picked that the live list lacks
  // (typed by hand, or gone from the provider) first, then the live list,
  // both narrowed by the search box.
  const listing = aiModelCatalog?.models || [];
  const facts = aiModelCatalog?.facts || {};
  const [modelQuery, setModelQuery] = React.useState("");
  const unlisted = formModels.filter((model) => !listing.includes(model));
  const rows = [...unlisted, ...listing].filter((model) => matchesWords(modelQuery, model));
  const addByName = () => {
    const name = customModel.trim();
    if (!name) return;
    addCatalogModel(name);
    setCustomModel("");
  };
  const billingAuto = autoBilling(aiKeysForm, aiKeysInfo);
  // A model's facts are its catalog's, priced only where its tokens are paid for.
  const billing = oauth ? "subscription" : aiKeysForm.billing || billingAuto;
  const [moreOpen, setMoreOpen] = React.useState(() => !!(aiKeysForm.name || aiKeysForm.test_model || aiKeysForm.billing));

  return (
    <div className="settingsForm">
      <Step n={1} title={t("Choose a service")}>
        <input className="aiKeyInput" type="search" spellCheck={false} placeholder={t("Search services")} aria-label={t("Search services")}
          value={serviceQuery} onChange={(event) => setServiceQuery(event.target.value)} />
        {shown.map((section) => {
          const Icon = SECTION_ICONS[section.id];
          return (
            <div className="aiServiceGroup" key={section.id}>
              <span className="setFieldLabel aiServiceCaption"><Icon size={14} />{t(section.title)}</span>
              <GlyphChoices label={t(section.title)} value={card?.id} onChange={pickCard}
                options={section.cards.map((item) => ({
                  value: item.id, label: item.label, hint: item.hint || billingLabel(item.billing),
                  glyph: <ProviderLogo service={item.plans[0].id} size={20} />,
                }))} />
            </div>
          );
        })}
        {!shown.length ? <p className="setNotice">{t("No service matches “{query}”.", { query: serviceQuery.trim() })}</p> : null}
        {card?.plans.length > 1 ? (
          <Field group label={t("Plan")} hint={note}>
            <Segmented value={service} onChange={choose} options={card.plans.map((plan) => [plan.id, plan.label])} />
          </Field>
        ) : note ? <p className="settingsPaneHint settingsNoMargin">{note}</p> : null}
        {service === "custom" ? <Field label={t("API format")} hint={t("Use the format supported by your service")}>
          <MenuSelect block label={t("API protocol")} value={aiKeysForm.protocol}
            onChange={(next) => setAiKeysForm((form) => ({ ...form, protocol: next }))}
            options={aiKeysInfo.protocols.filter((item) => !isOauthProto(item.id)).map((item) => [item.id, item.label])} />
        </Field> : null}
      </Step>

      <Step
        n={2}
        title={oauth ? signInWords(aiKeysForm.protocol).title : keyless ? t("API key, if it needs one") : t("Paste your API key")}
        hint={oauth ? signInWords(aiKeysForm.protocol).billed : t("Stored on the server, never shown to the browser again.")}
      >
        {oauth ? (
          <SignInStep form={aiKeysForm} setForm={setAiKeysForm} busy={aiKeysBusy}
            start={startSignIn} connect={connectSignIn} />
        ) : (
          <>
            <Field label={t("API key")} hint={aiKeysForm.id ? t("Leave empty to keep the current one")
              : keyless ? t("Optional — leave it empty for a server that asks for none") : null}>
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
                {aiModelCatalog.loading ? <><span className="transferSpin inline" /> {typedKey ? t("Checking the key…") : t("Checking the address…")}</>
                  : aiModelCatalog.models ? <><CheckIcon size={14} /> {typedKey
                    ? tn("Key works · {n} model available", "Key works · {n} models available", aiModelCatalog.models.length)
                    : tn("Reached · {n} model available", "Reached · {n} models available", aiModelCatalog.models.length)}</>
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
          <input className="aiKeyInput" type="search" spellCheck={false} aria-label={t("Search models")}
            placeholder={aiModelCatalog?.loading ? t("Loading models…") : listing.length ? tn("Search {n} model", "Search {n} models", listing.length) : t("Search models")}
            value={modelQuery} onChange={(event) => setModelQuery(event.target.value)} />
          <button
            className="uiBtn sm"
            disabled={!!aiModelCatalog?.loading || formOauthPending}
            title={formOauthPending ? t("Sign in first") : t("Fetch the models available to this credential")}
            onClick={loadModelCatalog}
          >
            {aiModelCatalog?.loading
              ? <><span className="transferSpin inline" /> {t("fetching…")}</>
              : aiModelCatalog?.models
                ? <><RefreshIcon size={14} /> {aiModelCatalog.models.length} {t("usable")}</>
                : <><RefreshIcon size={14} /> {t("Fetch")}</>}
          </button>
        </div>
        {rows.length || modelQuery.trim() ? (
          <div className="reportPageList aiModelList" role="group" aria-label={t("Available models")}>
            {rows.map((model) => {
              const picked = formModels.includes(model);
              return (
                <label className="docPickerItem" key={model}>
                  <input type="checkbox" checked={picked} onChange={() => (picked ? removeModel(model) : addCatalogModel(model))} />
                  <span className="attachName">{model}</span>
                  {unlisted.includes(model) && listing.length ? (
                    <span className="uiTag" title={t("Picked by name; the provider's list doesn't show it")}>{t("not listed")}</span>
                  ) : null}
                  <ModelFacts model={facts[model] ? { ...facts[model], billing } : null} />
                </label>
              );
            })}
            {!rows.length ? <div className="setPickEmpty">{t("No model matches “{query}”.", { query: modelQuery.trim() })}</div> : null}
          </div>
        ) : null}
        {aiModelCatalog?.error ? (
          <div className="reportModalHint settingsNoMargin">
            {keyChecked ? t("No model list without a working key.") : aiModelCatalog.error}{" "}
            <button className="searchToggle" title={t("Retry loading the model list")} onClick={loadModelCatalog}><RefreshIcon size={14} /></button>
          </div>
        ) : null}
        <Field group label={t("Add by name")} hint={t("For a model the list doesn't show, or an endpoint that lists none")}>
          <div className="aiProvPwForm">
            <input className="aiKeyInput" type="text" spellCheck={false} autoComplete="off" aria-label={t("Model name")}
              placeholder={t("Model name")} value={customModel}
              onChange={(event) => setCustomModel(event.target.value)}
              onKeyDown={(event) => {
                if (composing(event) || event.key !== "Enter") return;
                event.preventDefault();
                addByName();
              }} />
            <button className="uiBtn sm" disabled={!customModel.trim()} onClick={addByName}>{t("Add")}</button>
          </div>
        </Field>
        <details className="aiMoreOptions" open={moreOpen} onToggle={(event) => setMoreOpen(event.currentTarget.open)}>
          <summary>{oauth ? t("More options — name, test model") : t("More options — name, test model, billing")}</summary>
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
          {!oauth ? (
            <Field label={t("Billing")} hint={t("How Usage counts this connection's calls: only pay-per-token calls get an estimated cost")}>
              <MenuSelect
                label={t("Billing")}
                value={aiKeysForm.billing || ""}
                onChange={(billing) => setAiKeysForm((form) => ({ ...form, billing }))}
                options={[
                  ["", t("Auto: {billing}", { billing: billingLabel(billingAuto) })],
                  ["api", t("Pay per token")],
                  ["subscription", t("Subscription")],
                  ["local", t("Free (local)")],
                ]}
              />
            </Field>
          ) : null}
        </details>
      </Step>

      {aiKeysForm.access ? (
        <AccessStep n={4} access={aiKeysForm.access} onChange={(access) => setAiKeysForm((form) => ({ ...form, access }))} />
      ) : null}

      {aiKeysError ? <div className="settingsPaneHint aiKeysError">{aiKeysError}</div> : null}
      <div className="reportModalBtns">
        <button className="uiBtn" onClick={onCancel}>{t("Cancel")}</button>
        <button className="uiBtn primary" disabled={aiKeysBusy} onClick={submitAiProvider}>
          <MorphText>{aiKeysBusy
            ? t("Saving…")
            : !aiKeysForm.id || (oauth && (aiKeysForm.oauthCallback || "").trim()) ? t("Connect") : t("Save changes")}</MorphText>
        </button>
      </div>
    </div>
  );
}

// A shared entry's last step: who may use it (gamma/ai_settings.py
// "access"). Three tiles like the share popover's audience — every
// account, every account and the guests too, or the accounts named — and
// under the last a chip per named account with an AccountPicker to add
// one. Administrators always have access; the step says so.
const ACCESS_TILES = [
  { value: "accounts", label: T("Every account"), hint: T("everyone signed in here"), Icon: UsersIcon },
  { value: "guests", label: T("Accounts and guests"), hint: T("guest visitors too"), Icon: GlobeIcon },
  { value: "selected", label: T("Selected accounts"), hint: T("only the accounts you name"), Icon: UserIcon },
];

function AccessStep({ n, access, onChange }) {
  const accounts = useAccounts();
  const [round, setRound] = React.useState(0); // remounts the picker, emptied, after a pick
  const users = access.users || [];
  const tile = access.who === "selected" ? "selected" : access.guests ? "guests" : "accounts";
  const pick = (next) => onChange(next === "selected"
    ? { who: "selected", users, guests: false }
    : { who: "everyone", users: [], guests: next === "guests" });
  const add = (name) => {
    if (!name || users.includes(name)) return;
    onChange({ ...access, users: [...users, name] });
    setRound((r) => r + 1);
  };
  const remove = (name) => onChange({ ...access, users: users.filter((u) => u !== name) });
  return (
    <Step n={n} title={t("Who may use it")} hint={t("Administrators always have access.")}>
      <IconChoices label={t("Access")} value={tile} onChange={pick} options={ACCESS_TILES} />
      {access.who === "selected" ? (
        <Field label={t("Accounts")} hint={users.length ? null : t("No account named yet — only administrators can use this connection.")}>
          {users.length ? (
            <div className="aiModelChips">
              {users.map((name) => (
                <span className="categoryTag" key={name}>
                  {name}
                  <button className="uiClose uiCloseSm" title={t("Remove account")} aria-label={t("Remove {name}", { name })} onClick={() => remove(name)}>
                    <XIcon size={14} />
                  </button>
                </span>
              ))}
            </div>
          ) : null}
          <AccountPicker key={round} accounts={accounts} exclude={users} value="" onChange={add} placeholder={t("Add an account…")} compact />
        </Field>
      ) : null}
    </Step>
  );
}
