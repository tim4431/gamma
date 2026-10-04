// Settings → Backups › Off-site copies (admins only): the server copies its
// databases and uploaded files to an S3-compatible bucket (AWS S3,
// Cloudflare R2, MinIO, …) every so often. The bucket is a backup target
// only; Gamma keeps running on its local files. The section is three rows:
// the switch (saved at once), the bucket's summary with Set up… / Edit…,
// which opens the editor dialog, and the rounds' status with Copy now.
// GET/PUT /api/admin/offsite (the secret is write-only: `secret_set` says
// one is stored, and a PUT without `secret_key` keeps it), POST
// /api/admin/offsite/test with the unsaved fields, POST /api/admin/offsite/run
// for a round now. When the server's environment sets the bucket
// (GAMMA_S3_BUCKET, `from_env`), the summary is read-only and its Test
// tries the environment's bucket. docs/dev/settings.md.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { MenuSelect } from "../shared/ui/Menus";
import { DialogButtons, Field, PasswordInput, Row, Section, SubDialog, Toggle, UnitInput } from "./SettingsKit";
import { fmtWhen } from "./SettingsBackups";
import { CheckIcon, CloudIcon, CloudUploadIcon, RefreshIcon, XIcon } from "../shared/ui/Icons";
import { t, tn } from "../shared/i18n/i18n.js";

const ENDPOINT = `${API}/admin/offsite`;
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
const INTERVALS = [[900, t("Every 15 minutes")], [3600, t("Every hour")], [21600, t("Every 6 hours")], [86400, t("Every 24 hours")]];

// The choices, plus a stored interval that is none of them (set elsewhere).
const intervalOptions = (value) => (INTERVALS.some(([s]) => s === value) ? INTERVALS
  : [...INTERVALS, [value, tn("Every {n} minute", "Every {n} minutes", Math.round(value / 60))]].sort((a, b) => a[0] - b[0]));

// The dialog's copy of the saved settings. The secret starts empty: empty
// keeps the stored one.
const fromSaved = (s) => ({
  bucket: s.bucket || "", endpoint: s.endpoint || "", region: s.region || "",
  access_key: s.access_key || "", secret_key: "", prefix: s.prefix || "",
  interval_s: s.interval_s || 3600, keep: String(s.keep ?? ""),
});

// What a save and a test send: the fields, the secret only when typed.
function payload(draft, keep) {
  const { secret_key: secret, ...rest } = draft;
  const out = { ...rest, keep };
  for (const key of ["bucket", "endpoint", "region", "access_key", "prefix"]) out[key] = rest[key].trim();
  if (secret) out.secret_key = secret;
  return out;
}

// The summary row's hint: where the copies go, how often, how many are kept
// ("s3://gamma-backups/prefix · every 1 h · 7 kept"), the endpoint's host
// in place of s3:// for R2, MinIO and the like.
function summary({ bucket, endpoint, prefix, interval_s: every, keep }) {
  let at = "s3://";
  if (endpoint) { try { at = `${new URL(endpoint).host}/`; } catch { at = `${endpoint}/`; } }
  return [`${at}${bucket}${prefix ? `/${prefix}` : ""}`,
    every % 3600 === 0 ? t("every {n} h", { n: every / 3600 }) : t("every {n} min", { n: Math.round(every / 60) }),
    t("{n} kept", { n: keep })].join(" · ");
}

// One listing of the bucket: `body` the unsaved fields, null the settings in
// force (the environment's). Never throws: {ok, message}.
async function tryBucket(body) {
  try {
    const value = await apiJson(`${ENDPOINT}/test`, json("POST", body));
    return { ok: !!value.ok, message: value.message || (value.ok ? t("Connected") : t("Could not connect")) };
  } catch (err) { return { ok: false, message: err.message }; }
}

// A test's answer, the AI editor's key check: testing, ✓ what the bucket
// holds, or ✗ why it could not be reached.
function TestResult({ test }) {
  if (!test) return null;
  return (
    <span className={`aiKeyCheck ${test.busy ? "" : test.ok ? "aiTestOk" : "aiKeysError"}`} role="status">
      {test.busy ? <><span className="transferSpin inline" /> {t("Testing…")}</>
        : <>{test.ok ? <CheckIcon size={14} /> : <XIcon size={14} />} {test.message}</>}
    </span>
  );
}

// The status line: how the last round went and when the next one is due.
function roundHint(status) {
  const parts = status.running ? [t("copying now…")] : [];
  if (status.last_round_at) {
    parts.push(t("last round {time}", { time: fmtWhen(status.last_round_at) }),
      tn("{n} database", "{n} databases", status.copied || 0),
      tn("{n} upload", "{n} uploads", status.uploads_copied || 0),
      t("{n} failed", { n: status.failed || 0 }));
  } else if (!status.running) parts.push(t("no round yet"));
  if (status.next_round_at && !status.running) parts.push(t("next round {time}", { time: fmtWhen(status.next_round_at) }));
  return parts.join(" · ");
}

// The editor dialog. Saving the first bucket turns the copies on, as
// connecting an AI service does; an edit leaves the switch as it is.
function OffsiteDialog({ settings, onClose, onSaved }) {
  const first = !settings.bucket;
  const [draft, setDraft] = React.useState(() => fromSaved(settings));
  const [test, setTest] = React.useState(null); // {busy} | {ok, message}
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  // A test result is about the values it tested.
  const set = (key) => (value) => { setDraft((d) => ({ ...d, [key]: value })); setTest(null); };
  const text = (key, label) => ({
    "aria-label": label, value: draft[key], spellCheck: false, autoComplete: "off", name: `gamma-offsite-${key}`,
    onChange: (event) => set(key)(event.target.value),
  });

  async function save(event) {
    event.preventDefault();
    if (busy) return;
    // A number the box cannot read would go as null, which the server takes as "unchanged".
    const keep = Number.parseInt(draft.keep, 10);
    if (!Number.isFinite(keep) || keep < 1) { setError(t("Keep at least one copy.")); return; }
    setBusy(true); setError("");
    try {
      onSaved(await apiJson(ENDPOINT, json("PUT", { ...payload(draft, keep), ...(first ? { enabled: true } : {}) })));
    } catch (err) { setError(err.message); setBusy(false); }
  }
  async function runTest() {
    setTest({ busy: true });
    setTest(await tryBucket(payload(draft, Number.parseInt(draft.keep, 10) || settings.keep)));
  }

  return (
    <SubDialog title={first ? t("Set up off-site copies") : t("Edit off-site copies")} onClose={onClose} draft={draft}>
      {/* noValidate: the server says what is wrong with an endpoint, in its words. */}
      <form className="settingsForm" noValidate onSubmit={save}>
        <Field label={t("Bucket")} hint={t("AWS S3, Cloudflare R2, MinIO or another S3-compatible store")}>
          <input className="aiKeyInput" placeholder="gamma-backups" {...text("bucket", t("Bucket"))} />
        </Field>
        <Field label={t("Endpoint")} hint={t("Empty for AWS S3")}>
          <input className="aiKeyInput" type="url" placeholder="https://<account>.r2.cloudflarestorage.com" {...text("endpoint", t("Endpoint"))} />
        </Field>
        <Field label={t("Region")} hint={t("auto for Cloudflare R2; the bucket's region on AWS")}>
          <input className="aiKeyInput" placeholder="auto" {...text("region", t("Region"))} />
        </Field>
        <Field label={t("Access key ID")} hint={t("A key pair that may read and write the bucket")}>
          <input className="aiKeyInput" {...text("access_key", t("Access key ID"))} />
        </Field>
        <Field label={t("Secret key")} hint={t("Stored on the server, never shown to the browser again.")}>
          <PasswordInput {...text("secret_key", t("Secret key"))} autoComplete="new-password"
            placeholder={settings.secret_set ? t("secret set — type to replace") : undefined} />
        </Field>
        <Field label={t("Prefix")} hint={t("Put before every name, so the bucket can hold other things")}>
          <input className="aiKeyInput" placeholder="gamma" {...text("prefix", t("Prefix"))} />
        </Field>
        <Field label={t("How often")} hint={t("The time between two rounds")}>
          <MenuSelect block label={t("How often")} value={draft.interval_s} options={intervalOptions(draft.interval_s)}
            onChange={set("interval_s")} />
        </Field>
        <Field label={t("Copies kept")} hint={t("Per database; older copies are deleted from the bucket")}>
          <UnitInput unit="" min={1} label={t("Copies kept")} value={draft.keep} onChange={set("keep")} />
        </Field>
        <div className="reportModalBtns settingsAlignStart">
          <button type="button" className="uiBtn sm" disabled={!!test?.busy}
            title={t("Reach the bucket with the settings above, saved or not")} onClick={runTest}>{t("Test connection")}</button>
          <TestResult test={test} />
        </div>
        {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
        <DialogButtons>
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={busy}>{busy ? t("Saving…") : t("Save")}</button>
        </DialogButtons>
      </form>
    </SubDialog>
  );
}

export function OffsiteCopies({ setStatus }) {
  const [saved, setSaved] = React.useState(null); // GET's {settings, from_env}
  const [round, setRound] = React.useState(null); // GET's status
  const [editing, setEditing] = React.useState(false);
  const [test, setTest] = React.useState(null);   // the environment's Test
  const [error, setError] = React.useState("");
  const [switching, setSwitching] = React.useState(false);
  const [starting, setStarting] = React.useState(false);
  const take = (value) => { setSaved(value); setRound(value.status); };
  React.useEffect(() => {
    let active = true;
    apiJson(ENDPOINT).then((value) => { if (active) take(value); })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);
  // The status moves on by itself: read it every few seconds while a round
  // runs, else twice a minute.
  const running = !!round?.running;
  React.useEffect(() => {
    if (!saved) return undefined;
    const timer = setInterval(() => {
      apiJson(ENDPOINT).then((value) => setRound(value.status)).catch(() => {});
    }, running ? 3000 : 30000);
    return () => clearInterval(timer);
  }, [!!saved, running]); // eslint-disable-line react-hooks/exhaustive-deps

  const settings = saved?.settings;
  const managed = !!saved?.from_env;
  async function turn(on) {
    // With no bucket there is nothing to turn on yet: the switch opens the set-up.
    if (on && !settings.bucket) { setError(""); setEditing(true); return; }
    setSwitching(true); setError("");
    try {
      take(await apiJson(ENDPOINT, json("PUT", { enabled: on })));
      setStatus?.(on ? t("Off-site copies turned on.") : t("Off-site copies turned off."));
    } catch (err) { setError(err.message); }
    finally { setSwitching(false); }
  }
  async function copyNow() {
    setStarting(true); setError("");
    try {
      const value = await apiJson(`${ENDPOINT}/run`, json("POST"));
      if (value.started) { setRound((r) => ({ ...r, running: true })); setStatus?.(t("Copying to the bucket now.")); }
      else setError(value.message || t("No round started."));
    } catch (err) { setError(err.message); }
    finally { setStarting(false); }
  }
  async function testEnvironment() {
    setTest({ busy: true });
    setTest(await tryBucket(null));
  }

  const errorLine = error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null;
  // The summary's hint: where the copies go and how; for the environment's,
  // the variable that sets them, or what its Test found.
  const summaryHint = !saved ? null : managed
    ? (test?.busy ? t("Testing…") : test?.ok ? `✓ ${test.message}` : test ? <span className="aiKeysError">{test.message}</span>
      : t("Set by {name} in the server's environment", { name: "GAMMA_S3_BUCKET" }))
    : settings.bucket ? summary(settings) : t("AWS S3, Cloudflare R2, MinIO or another S3-compatible store");
  return <>
    <Section title={t("Off-site copies")}>
      {!saved ? errorLine || <p className="setNotice">{t("Loading…")}</p> : <>
        <Toggle icon={CloudUploadIcon} label={t("Copy to a bucket")} checked={settings.enabled} disabled={managed || switching} onChange={turn}
          hint={t("Databases and uploaded files, every so often; Gamma keeps running on its own disk")}
          title={t("A backup target only: Gamma keeps reading and writing its local files and copies them to an S3-compatible bucket each round.")} />
        {/* The environment's values are the hover title of its read-only summary. */}
        <Row icon={CloudIcon} label={settings.bucket || t("No bucket yet")} hint={summaryHint}
          title={managed ? summary(settings) : undefined}>
          {managed ? <button className="uiBtn sm" disabled={!!test?.busy} onClick={testEnvironment}>{t("Test")}</button>
            : <button className="uiBtn sm" onClick={() => { setError(""); setEditing(true); }}>{settings.bucket ? t("Edit…") : t("Set up…")}</button>}
        </Row>
        {round?.enabled || round?.last_round_at ? <>
          <Row icon={RefreshIcon} label={t("Status")} hint={roundHint(round)}>
            <button className="uiBtn sm" disabled={running || starting || !round.enabled} onClick={copyNow}>
              {running ? t("Copying…") : t("Copy now")}
            </button>
          </Row>
          {round.error ? <p className="settingsPaneHint aiKeysError" role="alert">{round.error}</p> : null}
        </> : null}
        {errorLine}
      </>}
    </Section>
    {editing ? (
      <OffsiteDialog settings={settings} onClose={() => setEditing(false)}
        onSaved={(value) => { take(value); setEditing(false); setStatus?.(t("Off-site copies saved.")); }} />
    ) : null}
  </>;
}
