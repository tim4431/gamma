// Settings → Reading › Translation: the viewer's translate button, target
// language, what translates (a chat model or a machine-translation engine),
// and the engines' credentials (Google Cloud Translation, Youdao). The keys
// are write-only like the AI keys: /api/translate/engines masks them.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { friendlyApiError } from "../library/libraryUtils";
import { MenuSelect } from "../shared/ui/Menus";
import { Section, Row, Toggle, SubDialog, Field, PasswordInput } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { TRANSLATE_LANGS } from "../app/prefs";
import { GlobeIcon, HighlightIcon, KeyIcon, LanguagesIcon, SlidersIcon, SparklesIcon, TextCursorIcon, Trash2Icon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

// Per engine: the form's fields in order, with where to get them.
const ENGINE_FORMS = {
  google: {
    hint: T("Google Cloud console › APIs & Services › Credentials, with the Cloud Translation API enabled"),
    fields: [{ id: "api_key", label: T("API key"), secret: true, placeholder: "AIza…" }],
  },
  youdao: {
    hint: T("Youdao AI platform (ai.youdao.com) › an application with text translation bound"),
    fields: [
      { id: "app_key", label: T("App ID"), secret: false },
      { id: "app_secret", label: T("App secret"), secret: true },
    ],
  },
};

export function TranslationSettings({ value, onSpeed }) {
  return (
    <>
      <Section title={t("Translation")} scope="account" prefs={SECTION_PREFS.reading["Translation"]} action={
        <button className="uiBtn sm" onClick={onSpeed} title={t("Translation effort and parallel requests (AI › Advanced)")}>
          <SlidersIcon size={13} /> {t("Speed")}
        </button>
      }>
        <Toggle
          icon={LanguagesIcon}
          label={t("Translation button")}
          hint={t("In the viewer; nothing translates until you ask")}
          title={t("Show the translate button in the PDF viewer. Click translates the current page (or shows/hides an existing translation); right-click or long-press opens the options, including translating the whole document. Nothing translates until you ask.")}
          checked={value.translateEnabled}
          onChange={value.setTranslateEnabled}
        />
        <Row
          icon={GlobeIcon}
          label={t("Translate into")}
          hint={t("The translated view's language")}
          title={t("The translated view (the languages button in the PDF viewer's zoom column) redraws each paragraph in this language in place — figures and layout stay put, and holding Alt peeks at the original. Paragraph translations are cached per language and model, so re-reading a page is free.")}
        >
          <MenuSelect
            label={t("Translation language")}
            value={value.translateLang}
            onChange={value.setTranslateLang}
            options={TRANSLATE_LANGS}
          />
        </Row>
        <Toggle
          icon={HighlightIcon}
          label={t("Translate a selection")}
          hint={t("A button next to the highlight colors")}
          title={t("Selecting text in a PDF shows the highlight colors; this adds a translate button there. The translation opens under the colors, in the language above, with the same model or service as the page translation.")}
          checked={value.selTranslate}
          onChange={value.setSelTranslate}
        />
        <Toggle
          icon={TextCursorIcon}
          label={t("Translate on select")}
          hint={t("Without clicking the button first")}
          title={t("Translate as soon as text is selected, instead of when you click the translate button. Every selection is then a translation request.")}
          checked={value.selTranslateAuto}
          onChange={value.setSelTranslateAuto}
          disabled={!value.selTranslate}
        />
      </Section>
      <Section title={t("Translation engine")} scope="browser">
        <Row
          icon={SparklesIcon}
          label={t("Translate with")}
          hint={t("A chat model, or a translation service below")}
          title={t("What translates page text. A chat model keeps formulas and citations intact and follows the paper's register; a translation service (Google, Youdao) is faster and cheaper per page and needs no AI connection. Translation is a bulk job — a fast, cheap model usually reads fine.")}
        >
          <TranslateModelSelect value={value} />
        </Row>
      </Section>
      <TranslationServices value={value} />
    </>
  );
}

// "" = follow the chat model; a set-up engine ("engine:<id>") or a model id.
// A stale pick (engine removed, model gone) shows as the default, which is
// also what App sends.
function TranslateModelSelect({ value }) {
  const models = value.aiModels || [];
  const engines = value.translateEngines || [];
  const multiProvider = new Set(models.map((m) => m.provider)).size > 1;
  const known = [...engines, ...models].some((m) => m.id === value.translateModel);
  return (
    <MenuSelect
      label={t("Translate with")} value={known ? value.translateModel : ""} onChange={value.setTranslateModel}
      options={[
        ["", t("Same as chat: {default}", { default: value.chatModelName || t("provider default") })],
        ...engines.map((e) => [e.id, e.label]),
        ...models.map((m) => [m.id, multiProvider ? `${m.model} · ${m.provider_name || m.provider}` : m.model]),
      ]}
    />
  );
}

function TranslationServices({ value }) {
  const [info, setInfo] = React.useState(null); // {engines, can_edit}
  const [error, setError] = React.useState("");
  const [form, setForm] = React.useState(null); // {engine, label, fields}
  const [busy, setBusy] = React.useState(false);
  const [tests, setTests] = React.useState({}); // engine → {busy} | {ok, text} | {ok: false, error}

  React.useEffect(() => {
    apiJson(`${API}/translate/engines`).then(setInfo).catch((err) => setError(friendlyApiError(err)));
  }, []);

  async function change(call) {
    setBusy(true);
    setError("");
    try {
      setInfo(await call());
      setForm(null);
      setTests({}); // a result is about the key it tested
      value.refreshAiModels?.(); // the picker's engine list
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setBusy(false);
    }
  }
  const save = () => change(() => apiJson(`${API}/translate/engines/${form.engine}`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fields: form.fields }),
  }));
  const remove = (engine) => change(() => apiJson(`${API}/translate/engines/${engine.id}`, { method: "DELETE" }));
  async function test(engine) {
    setTests((prev) => ({ ...prev, [engine.id]: { busy: true } }));
    let result;
    try {
      result = await apiJson(`${API}/translate/engines/${engine.id}/test`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lang: value.translateLang }),
      });
    } catch (err) {
      result = { ok: false, error: friendlyApiError(err) };
    }
    setTests((prev) => ({ ...prev, [engine.id]: result }));
  }
  function edit(engine) {
    setError("");
    // Secret fields start empty (empty = keep the stored one); plain ones
    // come back from the server as they are.
    const fields = Object.fromEntries(ENGINE_FORMS[engine.id].fields.map((f) =>
      [f.id, f.secret ? "" : engine.fields?.[f.id] || ""]));
    setForm({ engine: engine.id, label: engine.label, configured: engine.configured, fields });
  }

  const canEdit = info?.can_edit;
  return (
    <Section title={t("Translation services")}>
      {info?.engines.map((engine) => {
        const result = tests[engine.id];
        const secret = ENGINE_FORMS[engine.id]?.fields.find((f) => f.secret);
        const hint = result?.busy ? t("Testing…")
          : result ? (result.ok ? `✓ ${result.text}` : result.error)
          : engine.configured ? t("Key {hint}", { hint: engine.fields?.[secret?.id] || "" })
          : t("Not set up");
        return (
          <Row key={engine.id} icon={KeyIcon} label={engine.label} hint={hint}>
            {canEdit ? (engine.configured ? <span className="setRowControls">
              <button className="uiBtn sm" disabled={busy || result?.busy}
                title={t("Translate one sentence with this service to check the key")} onClick={() => test(engine)}>
                {t("Test")}
              </button>
              <button className="uiBtn sm" disabled={busy} onClick={() => edit(engine)}>{t("Edit")}</button>
              <button className="uiBtn sm iconSq danger" disabled={busy} title={t("Remove this key")}
                aria-label={t("Remove key")} onClick={() => remove(engine)}>
                <Trash2Icon size={13} />
              </button>
            </span> : <button className="uiBtn sm" disabled={busy} onClick={() => edit(engine)}>{t("Set up")}</button>) : null}
          </Row>
        );
      })}
      {info && !canEdit ? <div className="settingsPaneHint">{t("Guest accounts cannot store API keys. Ask the admin for an account.")}</div> : null}
      {error && !form ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
      {form ? (
        <SubDialog draft={form.fields} title={form.label} onClose={() => { setForm(null); setError(""); }}>
          <div className="settingsForm">
            <div className="settingsPaneHint settingsNoMargin">{t(ENGINE_FORMS[form.engine].hint)}</div>
            {ENGINE_FORMS[form.engine].fields.map((f) => {
              const props = {
                spellCheck: false, placeholder: f.placeholder || "", value: form.fields[f.id],
                onChange: (event) => setForm((prev) => ({ ...prev, fields: { ...prev.fields, [f.id]: event.target.value } })),
              };
              return (
                <Field key={f.id} label={t(f.label)} hint={f.secret && form.configured ? t("leave empty to keep the current one") : null}>
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
    </Section>
  );
}
