// Settings → Chat's skills sections (docs/dev/skills.md): the account's own
// skills and the built-in ones, which a chat message runs by starting with
// /name and the library agent may load itself. Your skills lists the
// account's, each opening its editor, with Add and Import SKILL.md… on the
// rule; Built-in skills lists the server's, each with View (its
// instructions, and a copy to edit under the same name, which then replaces
// it) and how it is offered (BUILTIN_STATES: the editor's two choices for a
// skill the account cannot edit). The listing is the chat's own
// (chat/useSkills.js, GET /api/ai/skills without bodies, with the server's
// limits); a skill's instructions are read as its editor or viewer opens.
// Every write reads the listing again once (refreshSkills), which the "/"
// menu shares, so it has the change at once. A guest's sections are
// read-only: the server answers a guest's writes with 403, and a guest has
// no skills of its own.
import React from "react";
import { API, apiJson, fmtBytes } from "../shared/lib/utils";
import { pickFiles } from "../shared/lib/pickFiles.js";
import { DialogButtons, Empty, Field, FormNotice, Row, Section, SubDialog, Toggle, useDraftOf } from "./SettingsKit";
import { refreshSkills, useSkills } from "../chat/useSkills.js";
import { skillDescription, skillHint } from "../chat/skills.js";
import { MorphText } from "../shared/ui/MotionWidgets";
import { MenuSelect } from "../shared/ui/Menus";
import { BanIcon, CheckIcon, CopyIcon, EyeIcon, ImportIcon, PencilIcon, PlusIcon, SlashIcon, TrashIcon } from "../shared/ui/Icons";
import { T, t } from "../shared/i18n/i18n.js";

const ENDPOINT = `${API}/ai/skills`;
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const skillUrl = (id) => `${ENDPOINT}/${encodeURIComponent(id)}`;
// One skill with its instructions (GET /api/ai/skills/{id}).
const readSkill = (id) => apiJson(skillUrl(id));

// A name's shape (gamma/skills.py NAME_RE), checked as it is typed so a
// refusal is seldom the first word. The listing sends the server's numbers
// (useSkills `limits`), but a pattern is no number to send: this one copy
// only words the hint early, and the server's check stays the rule.
const NAME_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

const byName = (a, b) => a.name.localeCompare(b.name);

// How a built-in is offered: the two choices an account skill has as well,
// as one control, since a skill that is not offered at all has no use for
// the other. "Only from /" is an account skill's "The assistant may use it
// on its own" turned off, for a built-in whose description otherwise draws
// the assistant to it (docs/dev/skills.md "Loading one is the exception").
const BUILTIN_STATES = [
  ["auto", t("On its own"), CheckIcon],
  ["manual", t("Only from /"), SlashIcon],
  ["off", t("Off"), BanIcon],
];
const builtinState = (row) => (row.off ? "off" : row.model === false ? "manual" : "auto");
// What each state sends: the one it settles, the other left as it stands.
const BUILTIN_WRITES = {
  auto: { off: false, model: true },
  manual: { off: false, model: false },
  off: { off: true },
};
const builtinSaid = (state, name) => (
  state === "off" ? t("/{name} is no longer offered.", { name })
    : state === "manual" ? t("/{name} runs only when you type it.", { name })
      : t("The assistant may use /{name} on its own.", { name })
);

const fromSkill = (skill) => ({
  name: skill.name || "", description: skill.description || "", argument_hint: skill.argument_hint || "",
  body: skill.body || "", model: skill.model !== false,
});
const NO_SKILL = fromSkill({});

// What is wrong with a name as typed, or "" (nothing typed is the
// footnote's to say).
function nameProblem(name, taken, limits) {
  if (!name) return "";
  if (name.length > limits.name) return t("A name is up to {n} characters.", { n: limits.name });
  if (!NAME_RE.test(name)) return t("Use lowercase letters, digits and single hyphens, as in paper-summary.");
  if (taken.includes(name)) return t("You already have a skill /{name}.", { name });
  return "";
}

// The editor of one account skill, or of a new one (`skill` without an id:
// blank, or a built-in's copy). A save sends the `updated_at` it opened
// with; one changed elsewhere since is refused (409 kind "changed") and
// the draft stays, with Load the newer version to start again from what
// is stored (`onNewer`, which opens the editor afresh on it). `limits` the
// server's (useSkills).
function SkillEditor({ skill, taken, limits, onClose, onSaved, onNewer }) {
  const [draft, setDraft] = useDraftOf(skill, fromSkill, NO_SKILL);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState(null); // {message, changed?}
  const patch = (key) => (value) => { setDraft((d) => ({ ...d, [key]: value })); setError((e) => (e?.changed ? e : null)); };
  const name = draft.name.trim();
  const problem = nameProblem(name, taken, limits);
  const footnote = !name ? t("Name the skill to continue.")
    : !draft.description.trim() ? t("Describe the skill to continue.")
      : !draft.body.trim() ? t("Write its instructions to continue.") : "";
  const ready = !footnote && !problem;

  async function save(event) {
    event.preventDefault();
    if (!ready || busy) return;
    setBusy(true); setError(null);
    const fields = { name, description: draft.description.trim(), argument_hint: draft.argument_hint.trim(),
      body: draft.body, model: draft.model };
    try {
      const row = skill.id
        ? await apiJson(skillUrl(skill.id), json("PUT", { ...fields, updated_at: skill.updated_at }))
        : await apiJson(ENDPOINT, json("POST", fields));
      onSaved(row);
    } catch (err) {
      setError(err.status === 409 && err.data?.kind === "changed"
        ? { changed: true, message: t("This skill was changed elsewhere since you opened it.") }
        : { message: err.message });
      setBusy(false);
    }
  }
  async function newer() {
    setBusy(true);
    try { onNewer(await readSkill(skill.id)); }
    catch (err) { setError({ message: err.message }); setBusy(false); }
  }

  return (
    <SubDialog title={skill.id ? t("Edit /{name}", { name: skill.name }) : t("New skill")} onClose={onClose} draft={draft}>
      <form onSubmit={save}>
        <Field label={t("Name")} hint={t("Lowercase letters, digits and hyphens. Type / and the name in the chat to run it.")}>
          <span className="skillNameField">
            <span className="skillSlash" aria-hidden="true">/</span>
            <input className="aiKeyInput" autoFocus={!skill.id} required maxLength={limits.name} spellCheck={false} autoCapitalize="off"
              autoCorrect="off" value={draft.name} placeholder="paper-summary" aria-invalid={!!problem}
              onChange={(event) => patch("name")(event.target.value)} />
          </span>
        </Field>
        {problem ? <FormNotice>{problem}</FormNotice> : null}
        <Field label={t("Description")} hint={t("The assistant reads this to pick the skill: say what it is for, and what it is not for.")}>
          <textarea className="aiKeyInput promptTextarea" rows={2} required maxLength={limits.description} value={draft.description}
            onChange={(event) => patch("description")(event.target.value)} />
        </Field>
        <Field label={t("Argument hint")} hint={t("Optional. Shown in the / menu after the name, as in [topic].")}>
          <input className="aiKeyInput" maxLength={limits.hint} value={draft.argument_hint} placeholder={t("[section or topic]")}
            onChange={(event) => patch("argument_hint")(event.target.value)} />
        </Field>
        <Row wide label={t("Instructions")} hint={t("$ARGUMENTS stands for what follows the skill's name in the message.")}
          title={t("Without $ARGUMENTS, that text comes after the instructions. Up to {n} characters.", { n: limits.body.toLocaleString() })}>
          <textarea className="aiKeyInput promptTextarea" rows={12} maxLength={limits.body} aria-label={t("Instructions")}
            value={draft.body} onChange={(event) => patch("body")(event.target.value)} />
        </Row>
        <Toggle label={t("The assistant may use it on its own")} checked={draft.model} onChange={patch("model")}
          hint={t("Off: it runs only when you type its name after /.")} />
        {error ? (
          <FormNotice tone={error.changed ? "warn" : "error"}>
            {error.message}
            {error.changed ? <>{" "}<button type="button" className="uiBtn sm" disabled={busy} onClick={newer}>{t("Load the newer version")}</button></> : null}
          </FormNotice>
        ) : null}
        <DialogButtons footnote={footnote}>
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={!ready || busy}>
            <MorphText>{busy ? t("Saving…") : t("Save")}</MorphText>
          </button>
        </DialogButtons>
      </form>
    </SubDialog>
  );
}

// A built-in skill, read-only: its description, argument hint and
// instructions, and Copy to your skills (where the account may write),
// which opens the editor on a copy under the same name.
function BuiltinView({ skill, onClose, onCopy }) {
  return (
    <SubDialog title={`/${skill.name}`} onClose={onClose}>
      <Field label={t("Description")}><span className="settingDesc">{skillDescription(skill)}</span></Field>
      {skill.argument_hint ? <Field label={t("Argument hint")}><span className="settingDesc">{skillHint(skill)}</span></Field> : null}
      <Field label={t("Instructions")}>
        <textarea className="aiKeyInput promptTextarea" rows={14} readOnly value={skill.body || ""} />
      </Field>
      <DialogButtons>
        {onCopy ? <button type="button" className="uiBtn" onClick={onCopy}><CopyIcon size={14} />{t("Copy to your skills")}</button> : null}
        <button type="button" className="uiBtn primary" onClick={onClose}>{t("Close")}</button>
      </DialogButtons>
    </SubDialog>
  );
}

// `canEdit`: a signed-in account that is not a guest. `confirm` and
// `setStatus` are the app's (a delete asks first; a write says so in the pill).
export function SkillsSettings({ canEdit, confirm, setStatus }) {
  const { rows, ready, error: readError, limits } = useSkills();
  const mine = rows.filter((row) => row.source === "account").sort(byName);
  const builtins = rows.filter((row) => row.source === "builtin").sort(byName);
  const [busy, setBusy] = React.useState(null); // the id a request is out for, or "import"
  const [error, setError] = React.useState("");
  const [clash, setClash] = React.useState(null); // {text, name, detail}: an import whose name is taken
  const [editor, setEditor] = React.useState(null); // {skill, key}
  const [view, setView] = React.useState(null); // a built-in with its body
  const full = ready && mine.length >= limits.skills;
  const fullHint = ready ? t("Up to {n} skills. Delete one to add another.", { n: limits.skills }) : "";

  // After a write: the listing again, for this pane and the chat alike, and the pill.
  const written = (message) => {
    refreshSkills();
    if (message) setStatus?.(message);
  };
  // Runs `request` for one row (or the import), its failure the section's line.
  async function run(id, request) {
    setBusy(id); setError("");
    try { return await request(); }
    catch (err) { setError(err.message); return null; }
    finally { setBusy(null); }
  }
  const edit = (skill) => setEditor({ skill, key: `${skill.id || "new"}:${skill.updated_at || ""}` });
  const open = (row) => run(row.id, async () => edit(await readSkill(row.id)));
  const openView = (row) => run(row.id, async () => setView(await readSkill(row.id)));

  function remove(row) {
    const go = () => run(row.id, async () => {
      try {
        await apiJson(`${skillUrl(row.id)}?updated_at=${encodeURIComponent(row.updated_at || "")}`, { method: "DELETE" });
        written(t("Deleted /{name}.", { name: row.name }));
      } catch (err) {
        if (err.status === 409) refreshSkills(); // the newer copy, for a second try
        throw err;
      }
    });
    if (!confirm) { go(); return; }
    confirm({
      title: T("Delete skill"), message: t("Delete /{name}? Its instructions cannot be brought back.", { name: row.name }),
      confirmLabel: t("Delete"), danger: true, onConfirm: go,
    });
  }

  // POSTs a SKILL.md's text; a name already taken asks Replace or Cancel.
  async function upload(text, replace = false) {
    setClash(null);
    await run("import", async () => {
      try {
        const row = await apiJson(`${ENDPOINT}/import`, json("POST", replace ? { text, replace: true } : { text }));
        written(t("Imported /{name}.", { name: row.name }));
      } catch (err) {
        if (err.status !== 409 || err.data?.kind !== "name") throw err;
        const name = mine.find((row) => row.id === err.data.id)?.name;
        setClash({ text, name, detail: err.message });
      }
    });
  }
  const importFile = () => pickFiles({ accept: ".md,text/markdown,text/plain" }, async ([file]) => {
    setClash(null);
    if (file.size > limits.import) {
      setError(t("{file} is over {size}; a SKILL.md is at most that.", { file: file.name, size: fmtBytes(limits.import) }));
      return;
    }
    let text;
    try { text = await file.text(); }
    catch (err) { setError(t("Could not read {file}: {message}", { file: file.name, message: err.message })); return; }
    upload(text);
  });

  // Sets how a built-in is offered. Only what the state settles is sent
  // (gamma/skills.py set_builtin_state), so turning one off and on again
  // keeps the "on its own" choice made for it.
  function setBuiltinState(row, next) {
    run(row.id, async () => {
      await apiJson(`${skillUrl(row.id)}/state`, json("PUT", BUILTIN_WRITES[next]));
      written(builtinSaid(next, row.name));
    });
  }

  return (
    <>
      <Section title={t("Your skills")} action={canEdit ? (
        <span className="setControlGroup">
          <button type="button" className="uiBtn sm" disabled={!ready || full} title={full ? fullHint : undefined}
            onClick={() => edit({ model: true })}><PlusIcon size={14} />{t("Add skill")}</button>
          <button type="button" className="uiBtn sm" disabled={!ready || full || busy === "import"}
            title={full ? fullHint : ready ? t("A SKILL.md file, up to {size}", { size: fmtBytes(limits.import) }) : undefined}
            onClick={importFile}><ImportIcon size={14} />{t("Import SKILL.md…")}</button>
        </span>
      ) : null}>
        {readError && !ready ? (
          <FormNotice>
            {t("Could not read the skills: {message}", { message: readError })}{" "}
            <button type="button" className="uiBtn sm" onClick={() => refreshSkills()}>{t("Try again")}</button>
          </FormNotice>
        ) : null}
        {clash ? (
          <FormNotice tone="warn">
            {clash.name ? t("You already have a skill /{name}. Replace it with the imported one?", { name: clash.name }) : clash.detail}{" "}
            <button type="button" className="uiBtn sm danger" onClick={() => upload(clash.text, true)}>{t("Replace")}</button>{" "}
            <button type="button" className="uiBtn sm" onClick={() => setClash(null)}>{t("Cancel")}</button>
          </FormNotice>
        ) : null}
        {error ? <FormNotice>{error}</FormNotice> : null}
        {!ready && !readError ? <Empty icon={SlashIcon} loading>{t("Loading…")}</Empty> : null}
        {ready && !canEdit ? <Empty icon={SlashIcon}>{t("Skills of your own need an account. A guest can run the built-in ones below.")}</Empty> : null}
        {ready && canEdit && !mine.length ? (
          <Empty icon={SlashIcon}>
            {t("A skill is a set of instructions for one kind of request. Start a chat message with /name to run it, or let the assistant pick one that fits.")}
          </Empty>
        ) : null}
        {mine.map((row) => (
          <Row key={row.id} className="skillRow" icon={SlashIcon} label={`/${row.name}`} hint={row.description} title={row.description}>
            <span className="setRowControls">
              {row.model === false ? <span className="uiTag">{t("Only when you run it")}</span> : null}
              {canEdit ? <>
                <button type="button" className="uiBtn sm" disabled={busy === row.id} aria-label={t("Edit /{name}", { name: row.name })}
                  onClick={() => open(row)}><PencilIcon size={14} />{t("Edit")}</button>
                <button type="button" className="uiBtn sm iconSq danger" disabled={busy === row.id}
                  aria-label={t("Delete /{name}", { name: row.name })} title={t("Delete /{name}", { name: row.name })}
                  onClick={() => remove(row)}><TrashIcon size={14} /></button>
              </> : null}
            </span>
          </Row>
        ))}
      </Section>
      <Section title={t("Built-in skills")}>
        {ready && !builtins.length ? <Empty icon={SlashIcon}>{t("This server offers no built-in skills.")}</Empty> : null}
        {builtins.map((row) => (
          <Row key={row.id} className="skillRow" icon={SlashIcon} label={`/${row.name}`} hint={skillDescription(row)} title={skillDescription(row)}>
            <span className="setRowControls">
              {row.replaced ? <span className="uiTag">{t("Replaced by your skill")}</span> : null}
              <button type="button" className="uiBtn sm" disabled={busy === row.id} aria-label={t("View /{name}", { name: row.name })}
                onClick={() => openView(row)}><EyeIcon size={14} />{t("View")}</button>
              {row.replaced ? null : (
                <MenuSelect value={builtinState(row)} options={BUILTIN_STATES} disabled={!canEdit || busy === row.id}
                  label={t("How /{name} is offered", { name: row.name })}
                  onChange={(next) => setBuiltinState(row, next)} />
              )}
            </span>
          </Row>
        ))}
      </Section>
      {editor ? (
        <SkillEditor key={editor.key} skill={editor.skill} limits={limits}
          taken={mine.filter((row) => row.id !== editor.skill.id).map((row) => row.name)}
          onClose={() => setEditor(null)} onNewer={edit}
          onSaved={(row) => { setEditor(null); written(t("Saved /{name}.", { name: row.name })); }} />
      ) : null}
      {view ? (
        <BuiltinView skill={view} onClose={() => setView(null)}
          onCopy={canEdit && !full ? () => {
            setView(null);
            edit({ name: view.name, description: view.description, argument_hint: view.argument_hint, body: view.body, model: view.model });
          } : null} />
      ) : null}
    </>
  );
}
