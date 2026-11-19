// The server options of Settings that are sections of their own
// (serverOptions.js, gamma/server_options.py): Server › Online services
// (paper lookups, the AI model catalog), Server › Work folders (the largest
// file a work folder keeps, where this server keeps work folders) and AI ›
// Tool usage › Online search for every account (the admin's SearXNG
// instance and OpenAlex key, which every account without its own uses).
// docs/dev/settings.md.
import React from "react";
import { DialogButtons, Field, FormNotice, PasswordInput, Row, Section, SubDialog, Toggle, UnitInput } from "./SettingsKit";
import { useServerOptions } from "./serverOptions.js";
import { BookIcon, FolderIcon, GlobeIcon, KeyIcon, SparklesIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { t } from "../shared/i18n/i18n.js";

const adminTag = () => <span className="uiTag admin">{t("admin")}</span>;
const errorLine = (error) => (error ? <FormNotice>{error}</FormNotice> : null);

export function OnlineServicesSettings({ setStatus }) {
  const { options, error, save } = useServerOptions({ setStatus });
  return (
    <Section title={t("Online services")}>
      {options ? <>
        <Toggle icon={BookIcon} label={t("Paper lookups")} checked={!!options.metadata_lookup}
          hint={t("Find a paper's title, authors and DOI at arXiv, doi.org, Crossref, OpenAlex, Open Library and Google Books")}
          title={t("Off, this server asks none of them: a paper keeps what its PDF says, and a lookup finds nothing at once rather than waiting. For a server with no internet, or one that should not reach these sites.")}
          onChange={(on) => save({ metadata_lookup: on }, on ? t("Paper lookups on.") : t("Paper lookups off."))} />
        <Toggle icon={SparklesIcon} label={t("AI model catalog")} checked={!!options.model_catalog}
          hint={t("Read models' context windows and abilities from models.dev")}
          title={t("Off, model facts come from the AI providers' own listings alone, and this server never asks models.dev.")}
          onChange={(on) => save({ model_catalog: on }, on ? t("Model catalog on.") : t("Model catalog off."))} />
      </> : null}
      {errorLine(error)}
    </Section>
  );
}

export function WorkFolderSettings({ setStatus }) {
  const { options, error, save } = useServerOptions({ setStatus });
  if (!options?.work_folders) return null;
  const [lo, hi] = options.ranges.vault_keep_mb;
  async function commit(raw) {
    const n = Number.parseInt(String(raw).trim(), 10);
    if (!Number.isFinite(n) || n < lo || n > hi || n === options.vault_keep_mb) return; // the box shows the stored value again
    await save({ vault_keep_mb: n }, t("Work folders now keep files up to {n} MB.", { n }));
  }
  return (
    <Section title={t("Work folders")}>
      <Row icon={FolderIcon} label={t("Largest file a work folder keeps")}
        hint={t("A larger file is listed, but not copied or served; PDFs and notes are kept whatever their size")}
        title={t("Gamma copies a work folder's files so that other servers and devices can open them. A file past this size (a dataset, a video) is listed with its name and size only.")}>
        <UnitInput unit="MB" min={lo} label={t("Largest file a work folder keeps")} value={String(options.vault_keep_mb)} onCommit={commit} />
      </Row>
      {errorLine(error)}
    </Section>
  );
}

// One of the two services: its dialog's field, saved as the option `name`.
const SERVICES = {
  searxng_url: {
    label: () => t("SearXNG for every account"),
    field: () => t("Instance URL"),
    hint: () => t("A SearXNG instance with the JSON format turned on. It may be on this server's private network."),
    placeholder: "http://searxng:8080",
    secret: false,
  },
  openalex_key: {
    label: () => t("OpenAlex key for every account"),
    field: () => t("API key"),
    hint: () => t("A free key from openalex.org/settings/api: ten times the keyless daily budget for scholarly search."),
    placeholder: "",
    secret: true,
  },
};

function ServiceDialog({ name, current, saved, error, onSave, onClose }) {
  const service = SERVICES[name];
  const [value, setValue] = React.useState(service.secret ? "" : current);
  const [busy, setBusy] = React.useState(false);
  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    if (!(await onSave(value.trim()))) setBusy(false);
  }
  return (
    <SubDialog title={service.label()} onClose={onClose} draft={value}>
      <form className="settingsForm" noValidate onSubmit={submit}>
        <Field label={service.field()} hint={service.hint()}>
          {service.secret ? (
            <PasswordInput aria-label={service.field()} value={value} autoComplete="new-password" autoFocus
              placeholder={saved ? t("secret set — type to replace") : undefined} onChange={(event) => setValue(event.target.value)} />
          ) : (
            <input className="aiKeyInput" aria-label={service.field()} value={value} spellCheck={false} autoComplete="off" autoFocus
              placeholder={service.placeholder} onChange={(event) => setValue(event.target.value)} />
          )}
        </Field>
        {errorLine(error)}
        <DialogButtons>
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={busy || !value.trim()}>
            <MorphText>{busy ? t("Saving…") : t("Save")}</MorphText>
          </button>
        </DialogButtons>
      </form>
    </SubDialog>
  );
}

export function SearchServicesForEveryone({ setStatus }) {
  const { options, error, save } = useServerOptions({ setStatus });
  const [editing, setEditing] = React.useState("");
  if (!options) return errorLine(error);
  const set = { searxng_url: !!options.searxng_url, openalex_key: !!options.openalex_key_set };
  const hint = {
    searxng_url: options.searxng_url || t("Not set: an account searches with its own services"),
    openalex_key: set.openalex_key ? t("Set; an account's own key comes first") : t("Not set: OpenAlex is asked without a key"),
  };
  return <>
    <Section title={t("Online search for every account")} tag={adminTag()}>
      {Object.keys(SERVICES).map((name) => (
        <Row key={name} icon={name === "searxng_url" ? GlobeIcon : KeyIcon} label={SERVICES[name].label()} hint={hint[name]}>
          <span className="setRowControls">
            {set[name] ? (
              <button className="uiBtn sm" onClick={() => save({ [name]: "" }, t("{service} removed.", { service: SERVICES[name].label() }))}>
                {t("Remove")}
              </button>
            ) : null}
            <button className="uiBtn sm" onClick={() => setEditing(name)}>{set[name] ? t("Edit…") : t("Set up…")}</button>
          </span>
        </Row>
      ))}
      {errorLine(error)}
    </Section>
    {editing ? (
      <ServiceDialog name={editing} current={editing === "searxng_url" ? options.searxng_url : ""} saved={set[editing]}
        error={error} onClose={() => setEditing("")}
        onSave={async (value) => {
          const ok = await save({ [editing]: value }, t("{service} saved.", { service: SERVICES[editing].label() }));
          if (ok) setEditing("");
          return ok;
        }} />
    ) : null}
  </>;
}
