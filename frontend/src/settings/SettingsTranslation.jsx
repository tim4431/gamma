// Settings → Language and Translation: interface language, Viewer &
// selection (the viewer's translate button, target language, the selection
// translator), Service (what translates — a chat model or a
// machine-translation service — and the services' rows: Microsoft needs no
// key; Google and Youdao keys are write-only like the AI keys:
// /api/translate/engines masks them; then whether translations are kept,
// and what this workspace keeps), Glossary (the workspace's terms in the
// target language) and Speed (effort, tier, parallel requests).
import React from "react";
import { API, apiJson, fmtBytes } from "../shared/lib/utils";
import { friendlyApiError } from "../library/libraryUtils";
import { MenuSelect } from "../shared/ui/Menus";
import { Section, Row, Toggle, SubDialog, Field, PasswordInput, UnitInput, Empty, DialogButtons } from "./SettingsKit";
import { SECTION_PREFS } from "./sectionPrefs.js";
import { termsOf, useSettingsRead } from "./settingsReads.js";
import { SPEED_LABELS, SPEED_ORDER } from "../chat/modelPrefs.js";
import { FREE_TRANSLATE_ENGINE, TRANSLATE_LANGS, TRANSLATE_PARALLEL_MAX, translateModelFor } from "../app/prefs";
import { addTerm, clearTranslations, editTerm, removeTerm } from "../app/translateClient.js";
import { VIEWER_TOOLS } from "../app/viewerToolItems.js";
import { commandChord } from "../app/commands.js";
import { chordLabel } from "../shared/lib/hotkeys.js";
import { ActivityIcon, ArchiveIcon, BookIcon, DatabaseIcon, GlobeIcon, HighlightIcon, KeyIcon, LanguagesIcon, PlusIcon, RefreshIcon, SparklesIcon, TextCursorIcon, Trash2Icon, ZapIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { LANGUAGES, T, t, tn } from "../shared/i18n/i18n.js";

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

// In the order a reader meets it: what shows in the viewer, then what
// translates (a model or a service, the services' keys, and whether the
// translations are kept), then how fast. "Translate with" names this
// server's provider entries, so it stays with the browser (its row's own
// tag); the service keys are the account's, kept on the server.
export function TranslationSettings({ value, keybindings }) {
  const peekKey = commandChord("pdf.peekOriginal", keybindings);
  return (<>
    <Section title={t("Language")} scope="account" prefs={SECTION_PREFS.translation["Language"]}>
      <Row icon={GlobeIcon} label={t("Interface language")} hint={t("Interface text only.")}
        title={t("Menus, settings and messages. Your notes, PDFs and the AI's replies are not affected; System follows the browser's language.")}>
        <MenuSelect label={t("Interface language")} value={value.language} onChange={value.setLanguage}
          options={LANGUAGES.map(([code, name]) => [code, code === "system" ? t("System") : name])} />
      </Row>
    </Section>

    <Section title={t("Viewer & selection")} scope="account" prefs={SECTION_PREFS.translation["Viewer & selection"]}>
      <Toggle
        icon={LanguagesIcon}
        label={t("Translation button")}
        hint={t("Off, it moves to the ⋯ menu")}
        title={t("Shows the translate button on the PDF viewer's strip; off, it is a row of the strip's ⋯ menu that opens its options. A click translates the page being read, or shows the original or the translation once it is translated; right-click or long-press opens the options: the whole document, the language, Forget. Nothing translates until you ask. Appearance › Viewer tools sets the same, and the strip's order.")}
        checked={VIEWER_TOOLS.read(value.viewerTools).modes.translate !== "hidden"}
        onChange={(on) => value.setViewerTools(VIEWER_TOOLS.setMode(value.viewerTools, "translate", on ? "always" : "hidden"))}
      />
      <Row
        icon={GlobeIcon}
        label={t("Translate into")}
        hint={t("Also in the translate button's menu")}
        title={peekKey
          ? t("The translated view (the languages button in the PDF viewer's zoom column) redraws each paragraph in this language in place — figures and layout stay put, and holding {key} peeks at the original. Each language and model keeps its own translations.", { key: chordLabel(peekKey) })
          : t("The translated view (the languages button in the PDF viewer's zoom column) redraws each paragraph in this language in place — figures and layout stay put. Each language and model keeps its own translations.")}
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
    <Section title={t("Service")} scope="account" prefs={SECTION_PREFS.translation["Service"]}>
      <Row
        icon={SparklesIcon}
        label={t("Translate with")}
        scope="browser"
        hint={t("A chat model reads the paper first and keeps its terms; a service is quick and needs no AI")}
        title={t("What translates page text. A chat model reads a brief of the whole paper first, so its terms stay the same from page to page, and follows the paper's register; a translation service (Microsoft for free, or Google and Youdao with a key) translates sentence by sentence, is faster and cheaper per page, and needs no AI connection. Formulas, citations and links stay as printed either way. Translation is a bulk job — a fast, cheap model usually reads fine.")}
      >
        <TranslateModelSelect value={value} />
      </Row>
      <TranslationServices value={value} />
      <KeptTranslations value={value} />
    </Section>
    <Glossary value={value} />
    <TranslationPerformance value={value} />
  </>);
}

// The workspace's glossary in the target language in use (GET
// /api/translate/terms): a row per term, the term and its translation. A
// writer of the workspace adds one (the section's Add term), edits one in
// the same dialog and deletes one; a reader sees the rows alone. A chat
// model renders each term as it says; a service takes none. A changed term
// leaves the paragraphs on screen as they are (docs/dev/translation.md "The
// glossary").
function Glossary({ value }) {
  const lang = value.translateLang;
  const read = useSettingsRead(termsOf(lang));
  const info = read.data; // {terms, max, max_chars, can_edit}
  const [editor, setEditor] = React.useState(null); // {was?, source, target}
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const canEdit = !!info?.can_edit && !value.readOnly;
  const full = !!info && info.terms.length >= info.max;
  const langName = TRANSLATE_LANGS.find(([code]) => code === lang)?.[1] || lang;
  async function change(call, after) {
    setBusy(true);
    setError("");
    try {
      read.set(await call());
      after?.();
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setBusy(false);
    }
  }
  const save = (draft) => change(() => (editor.was
    ? editTerm(lang, editor.was, draft.source, draft.target)
    : addTerm(lang, draft.source, draft.target)), () => setEditor(null));
  const remove = (term) => change(() => removeTerm(lang, term.source));
  const terms = info?.terms || [];
  return <>
    <Section title={t("Glossary")} action={canEdit ? (
      <button type="button" className="uiBtn" disabled={busy || full} onClick={() => { setError(""); setEditor({ source: "", target: "" }); }}
        title={full ? t("The glossary holds {n} terms in a language at most. Delete one to add another.", { n: info.max })
          : t("A term and how to translate it into {lang}", { lang: langName })}>
        <PlusIcon size={14} /> {t("Add term")}
      </button>
    ) : null}>
      <div className="settingsPaneHint">
        {t("How every translation into {lang} in this workspace renders these terms, when a chat model translates. A paragraph already translated keeps its words until you translate it again.", { lang: langName })}
      </div>
      {!info && !read.error ? <Empty icon={BookIcon} loading>{t("Loading the glossary…")}</Empty> : null}
      {info && !terms.length ? <Empty icon={BookIcon}>{canEdit
        ? t("No terms in {lang} yet. Add one here, or from a selection's translation.", { lang: langName })
        : t("No terms in {lang} yet.", { lang: langName })}</Empty> : null}
      {terms.map((term) => (
        <div key={term.source} className="aiProvRow" data-term={term.source}>
          <span className="aiProvAvatar"><BookIcon size={16} /></span>
          <span className="aiProvMeta">
            <span className="aiProvName">{term.source}</span>
            <span className="aiProvDesc">
              <span lang={lang}>{term.target}</span>
              {term.by ? t(" · added by {name}", { name: term.by }) : ""}
            </span>
          </span>
          {canEdit ? <span className="aiProvActions">
            <button type="button" className="uiBtn sm" disabled={busy}
              onClick={() => { setError(""); setEditor({ was: term.source, source: term.source, target: term.target }); }}>
              {t("Edit")}
            </button>
            <button type="button" className="uiBtn sm iconSq" disabled={busy}
              title={t("Delete “{term}”", { term: term.source })} aria-label={t("Delete “{term}”", { term: term.source })}
              onClick={() => remove(term)}>
              <Trash2Icon size={16} />
            </button>
          </span> : null}
        </div>
      ))}
      {error && !editor ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
      {read.error && !info ? <div className="settingsPaneHint aiKeysError">{friendlyApiError({ message: read.error })}</div> : null}
    </Section>
    {editor ? <TermDialog initial={editor} langName={langName} maxChars={info?.max_chars || 100} busy={busy} error={error}
      onSave={save} onClose={() => { setEditor(null); setError(""); }} /> : null}
  </>;
}

// Add a term, or edit one (`initial.was`: the term as it is): the term as the
// papers write it and its translation.
function TermDialog({ initial, langName, maxChars, busy, error, onSave, onClose }) {
  const [draft, setDraft] = React.useState({ source: initial.source, target: initial.target });
  const ready = draft.source.trim() && draft.target.trim();
  const submit = (e) => { e.preventDefault(); if (ready && !busy) onSave(draft); };
  const field = (key) => ({
    className: "aiKeyInput", type: "text", spellCheck: false, autoComplete: "off", maxLength: maxChars, value: draft[key],
    onChange: (event) => setDraft((prev) => ({ ...prev, [key]: event.target.value })),
  });
  return (
    <SubDialog title={initial.was ? t("Edit term") : t("Add term")} draft={draft} onClose={onClose}>
      <form className="settingsForm" onSubmit={submit}>
        <Field label={t("Term")} hint={t("As papers write it. Case does not matter, but an acronym such as LED matches only in capitals.")}>
          <input {...field("source")} autoFocus />
        </Field>
        <Field label={t("Translation")} hint={t("Into {lang}", { lang: langName })}>
          <input {...field("target")} />
        </Field>
        {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
        <DialogButtons footnote={ready ? null : t("Type the term and its translation to continue.")}>
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={busy || !ready}>
            <MorphText>{busy ? t("Saving…") : t("Save")}</MorphText>
          </button>
        </DialogButtons>
      </form>
    </SubDialog>
  );
}

// Keep translations (the account's switch, sent as `keep` with every
// translation request) and what this workspace keeps (GET
// /api/translations/stats), with Clear (DELETE /api/translations: the
// workspace's kept translations and briefs, asked first; the viewer drops
// what it shows of them too, app/translateClient.js clearTranslations).
function KeptTranslations({ value }) {
  const stats = useSettingsRead("/translations/stats");
  const kept = stats.data; // {documents, paragraphs, bytes}
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const clear = () => value.confirm?.({
    title: T("Clear kept translations"),
    message: T("Forget every translation kept in this workspace, and the papers' briefs? A paper is translated again the next time you ask for it."),
    confirmLabel: t("Clear"),
    danger: true,
    onConfirm: async () => {
      setBusy(true);
      setError("");
      try {
        await clearTranslations();
        stats.set({ documents: 0, paragraphs: 0, bytes: 0 });
      } catch (err) {
        setError(friendlyApiError(err));
      } finally {
        setBusy(false);
      }
    },
  });
  const summary = !kept ? (stats.error ? friendlyApiError({ message: stats.error }) : null)
    : !kept.bytes ? t("Nothing kept yet")
      : t("{paragraphs} in {documents} ({size})", {
        paragraphs: tn("{n} paragraph", "{n} paragraphs", kept.paragraphs),
        documents: tn("{n} document", "{n} documents", kept.documents),
        size: fmtBytes(kept.bytes),
      });
  return <>
    <Toggle
      icon={ArchiveIcon}
      label={t("Keep translations")}
      hint={t("A paper translated again needs no AI call")}
      title={t("Translations of this workspace's PDFs, and each paper's brief, are kept on the server in the workspace, so translating a paper again, yours or another member's, needs no AI call. Off, they last only until the server restarts; what is kept already stays until you clear it. A PDF without a copy on the server is never kept.")}
      checked={value.translateKeep}
      onChange={value.setTranslateKeep}
    />
    <Row
      icon={DatabaseIcon}
      label={t("Kept in this workspace")}
      hint={summary}
      title={t("Translated paragraphs in every language and model, and the papers' briefs. A PDF that leaves the workspace takes its translations along; Forget this paper's translations, in the translate button's menu, forgets one paper's.")}
    >
      {/* Clearing needs a writer of the workspace, as its other clears do. */}
      {!value.readOnly ? (
        <button type="button" className="uiBtn sm" disabled={busy || !kept?.bytes} onClick={clear}>{t("Clear")}</button>
      ) : null}
    </Row>
    {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
  </>;
}

// Effort and service tiers mean nothing to a translation service, so their
// rows hide while one is picked (or the free default with no AI), and
// until the list of services has loaded (it always holds the free one).
function TranslationPerformance({ value }) {
  const engines = value.translateEngines || [];
  const engine = !engines.length
    || translateModelFor(value.translateModel, engines, value.aiModels || []).startsWith("engine:");
  return <Section title={t("Speed")} scope="account" prefs={SECTION_PREFS.translation["Speed"]}>
    {!engine ? <Row
      icon={ActivityIcon}
      label={t("Translation effort")}
      hint={t("Default asks for the model's lowest level, the fastest")}
      title={t("Reasoning effort sent with translation calls. Reasoning models spend their thinking budget before writing any output, which a translation rarely needs, so Default asks for the model's lowest level and sends none to a model that takes none. Pick a higher level for a paper that needs it.")}
    >
      <MenuSelect
        label={t("Translation effort")}
        value={value.translateEffort}
        onChange={value.setTranslateEffort}
        options={[["", t("Default")], ["minimal", t("Minimal")], ["low", t("Low")], ["medium", t("Medium")], ["high", t("High")]]}
      />
    </Row> : null}
    {!engine ? <Row
      icon={ZapIcon}
      label={t("Translation speed")}
      hint={t("Only models whose provider offers that tier")}
      title={t("Which service tier translation asks the provider for: fast uses premium low-latency routing at a higher price per token; flex trades latency for a lower price. Default uses the provider's usual routing. Models that do not offer the chosen tier use Default.")}
    >
      <MenuSelect label={t("Translation speed")} value={value.translateSpeed} onChange={value.setTranslateSpeed}
        options={[["", t("Default")], ...SPEED_ORDER.map((tier) => [tier, t(SPEED_LABELS[tier])])]} />
    </Row> : null}
    <Row
      icon={RefreshIcon}
      label={t("Parallel requests")}
      hint={t("How many chunks translate at once (1–{max})", { max: TRANSLATE_PARALLEL_MAX })}
      title={t("A page is translated in small chunks, this many at a time; a whole-document job streams chunks across pages and never exceeds it. Higher is faster until your provider's rate limit pushes back.")}
    >
      <UnitInput value={value.translateParallel} unit={t("calls")} min={1} max={TRANSLATE_PARALLEL_MAX}
        onCommit={(raw) => {
          const n = Number.parseInt(raw, 10);
          if (Number.isFinite(n)) value.setTranslateParallel(Math.max(1, Math.min(TRANSLATE_PARALLEL_MAX, n)));
        }} />
    </Row>
  </Section>;
}

// "" = the default; a set-up service ("engine:<id>") or a model id. A stale
// pick (service removed, model gone) shows as the default, which is what
// App sends then: the chat model, or the free service with no AI at all.
function TranslateModelSelect({ value }) {
  const models = value.aiModels || [];
  const engines = value.translateEngines || [];
  const known = translateModelFor(value.translateModel, engines, models) === value.translateModel;
  const free = translateModelFor("", engines, models) === FREE_TRANSLATE_ENGINE
    && engines.find((e) => e.id === FREE_TRANSLATE_ENGINE);
  return (
    <MenuSelect
      label={t("Translate with")} value={known ? value.translateModel : ""} onChange={value.setTranslateModel}
      options={[
        ["", free ? t("Default: {name}", { name: free.label })
          : t("Same as chat: {default}", { default: value.chatModelName || t("provider default") })],
        ...engines.map((e) => [e.id, e.label]),
        ...models.map((m) => [m.id, m.model]),
      ]}
    />
  );
}

// The engines are the pane's read (settingsReads.js), kept while the
// dialog is open.
function TranslationServices({ value }) {
  const engines = useSettingsRead("/translate/engines");
  const info = engines.data; // {engines, can_edit}
  const setInfo = engines.set;
  const [ownError, setError] = React.useState("");
  const error = ownError || (!info && engines.error ? friendlyApiError({ message: engines.error }) : "");
  const [form, setForm] = React.useState(null); // {engine, label, fields}
  const [busy, setBusy] = React.useState(false);
  const [tests, setTests] = React.useState({}); // engine → {busy} | {ok, text} | {ok: false, error}

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
    // The key rows sit right under "Translate with" in the Service section;
    // the wrapper is the search target for "Translation services".
    <div data-setting={t("Translation services")}>
      {info?.engines.map((engine) => {
        const result = tests[engine.id];
        const secret = ENGINE_FORMS[engine.id]?.fields.find((f) => f.secret);
        const hint = result?.busy ? t("Testing…")
          : result ? (result.ok ? `✓ ${result.text}` : result.error)
          : engine.failing ? t("Not responding: {error}", { error: engine.failing.error })
          : !engine.needs_key ? t("No key needed")
          : engine.configured ? t("Key {hint}", { hint: engine.fields?.[secret?.id] || "" })
          : t("Not set up");
        // A service without a key has nothing to edit or remove — just Test.
        if (!engine.needs_key) return (
          <Row key={engine.id} icon={GlobeIcon} label={engine.label} hint={hint}
            title={t("The endpoint Microsoft Edge's built-in page translation uses: free and without an account, but unofficial — Microsoft may change or throttle it without notice. Set up Google or Youdao as a fallback.")}>
            {canEdit ? <button className="uiBtn sm" disabled={result?.busy}
              title={t("Translate one sentence with this service to check it responds")} onClick={() => test(engine)}>
              {t("Test")}
            </button> : null}
          </Row>
        );
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
                <Trash2Icon size={16} />
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
                <Field key={f.id} label={t(f.label)} hint={f.secret && form.configured ? t("Leave empty to keep the current one") : null}>
                  {f.secret ? <PasswordInput autoComplete="new-password" {...props} />
                    : <input className="aiKeyInput" type="text" autoComplete="off" {...props} />}
                </Field>
              );
            })}
            {error ? <div className="settingsPaneHint aiKeysError">{error}</div> : null}
            <div className="reportModalBtns">
              <button className="uiBtn" onClick={() => { setForm(null); setError(""); }}>{t("Cancel")}</button>
              <button className="uiBtn primary" disabled={busy} onClick={save}><MorphText>{busy ? t("Saving…") : t("Save")}</MorphText></button>
            </div>
          </div>
        </SubDialog>
      ) : null}
    </div>
  );
}
