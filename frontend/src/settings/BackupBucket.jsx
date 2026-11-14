// Settings → Backups › Bucket (admins only): an S3-compatible bucket (AWS
// S3, Cloudflare R2, MinIO, …) that backup tasks copy their snapshots to
// (BackupTasks.jsx, "Copy to the bucket"). The bucket has no schedule of its
// own: it holds what the tasks send it, and Saved snapshots lists what it
// holds (SettingsBackups.jsx). The section is the bucket's summary with Set
// up… / Edit…, which opens the editor dialog. GET/PUT /api/admin/offsite (the
// secret is write-only: `secret_set` says one is stored, and a PUT without
// `secret_key` keeps it), POST /api/admin/offsite/test with the unsaved
// fields. When the server's environment sets the bucket (GAMMA_S3_BUCKET,
// `from_env`), the summary is read-only, its Test tries the environment's
// bucket, and the environment's own rounds (whole databases and files,
// gamma/offsite.py) show their status with Copy now (POST
// /api/admin/offsite/run). docs/dev/settings.md.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { DialogButtons, Field, PasswordInput, Row, Section, SubDialog, TestResult } from "./SettingsKit";
import { fmtWhen } from "./SettingsBackups";
import { useSettingsRead } from "./settingsReads.js";
import { offsiteProgressText } from "./offsiteRound.js";
import { CloudIcon, RefreshIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { t, tn } from "../shared/i18n/i18n.js";

export const BUCKET_READ = "/admin/offsite";
export const BUCKET_COPIES_READ = "/admin/offsite/snapshots";
const ENDPOINT = `${API}${BUCKET_READ}`;
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });

// The dialog's copy of the saved settings. The secret starts empty: empty
// keeps the stored one.
const fromSaved = (s) => ({
  bucket: s.bucket || "", endpoint: s.endpoint || "", region: s.region || "",
  access_key: s.access_key || "", secret_key: "", prefix: s.prefix || "",
});

// What a save and a test send: the fields, the secret only when typed.
function payload(draft) {
  const { secret_key: secret, ...rest } = draft;
  const out = {};
  for (const key of ["bucket", "endpoint", "region", "access_key", "prefix"]) out[key] = rest[key].trim();
  if (secret) out.secret_key = secret;
  return out;
}

// Where the bucket is: "s3://gamma-backups/prefix", the endpoint's host in
// place of s3:// for R2, MinIO and the like. "" without a bucket.
export function bucketWhere(settings) {
  const { bucket, endpoint, prefix } = settings || {};
  if (!bucket) return "";
  let at = "s3://";
  if (endpoint) { try { at = `${new URL(endpoint).host}/`; } catch { at = `${endpoint}/`; } }
  return `${at}${bucket}${prefix ? `/${prefix}` : ""}`;
}

// One listing of the bucket: `body` the unsaved fields, null the settings in
// force (the environment's). Never throws: {ok, message}.
async function tryBucket(body) {
  try {
    const value = await apiJson(`${ENDPOINT}/test`, json("POST", body));
    return { ok: !!value.ok, message: value.message || (value.ok ? t("Connected") : t("Could not connect")) };
  } catch (err) { return { ok: false, message: err.message }; }
}

// The environment's rounds: how often, how many copies are kept, the round
// running and how far it is, how the last round went, and when the next
// one is due.
function roundHint(status) {
  const every = status.interval_s % 3600 === 0 ? t("every {n} h", { n: status.interval_s / 3600 })
    : t("every {n} min", { n: Math.round(status.interval_s / 60) });
  const parts = [every, t("{n} kept", { n: status.keep })];
  if (status.running) parts.push(...[t("copying now…"), offsiteProgressText(status.progress)].filter(Boolean));
  if (status.last_round_at) {
    parts.push(t("last round {time}", { time: fmtWhen(status.last_round_at) }),
      tn("{n} database", "{n} databases", status.copied || 0),
      tn("{n} upload", "{n} uploads", status.uploads_copied || 0),
      t("{n} failed", { n: status.failed || 0 }));
  } else if (!status.running) parts.push(t("no round yet"));
  if (status.next_round_at && !status.running) parts.push(t("next round {time}", { time: fmtWhen(status.next_round_at) }));
  return parts.join(" · ");
}

function BucketDialog({ settings, onClose, onSaved }) {
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
    setBusy(true); setError("");
    try {
      onSaved(await apiJson(ENDPOINT, json("PUT", payload(draft))));
    } catch (err) { setError(err.message); setBusy(false); }
  }
  async function runTest() {
    setTest({ busy: true });
    setTest(await tryBucket(payload(draft)));
  }

  return (
    <SubDialog title={first ? t("Set up a bucket") : t("Edit the bucket")} onClose={onClose} draft={draft}>
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
        <p className="settingsPaneHint">{t("The bucket has no schedule of its own: turn on “Copy to the bucket” in a backup task to send it that task’s snapshots.")}</p>
        <div className="reportModalBtns settingsAlignStart">
          <button type="button" className="uiBtn sm" disabled={!!test?.busy}
            title={t("Reach the bucket with the settings above, saved or not")} onClick={runTest}>{t("Test connection")}</button>
          <TestResult test={test} />
        </div>
        {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
        <DialogButtons>
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={busy}><MorphText>{busy ? t("Saving…") : t("Save")}</MorphText></button>
        </DialogButtons>
      </form>
    </SubDialog>
  );
}

// The settings and the rounds' status are the Backups pane's read
// (settingsReads.js). While an environment's round runs it is read again
// every few seconds, else twice a minute.
export function BackupBucket({ setStatus }) {
  const [running, setRunning] = React.useState(false);
  const offsite = useSettingsRead(BUCKET_READ, { poll: running ? 3000 : 30000 });
  const saved = offsite.data; // GET's {settings, from_env, status}
  const round = saved?.status || null;
  if (!!round?.running !== running) setRunning(!!round?.running);
  const [editing, setEditing] = React.useState(false);
  const [test, setTest] = React.useState(null);   // the environment's Test
  const [ownError, setError] = React.useState("");
  const error = ownError || (!saved ? offsite.error : "");
  const [starting, setStarting] = React.useState(false);
  const setRound = (next) => offsite.set((value) => ({ ...value, status: next(value?.status) }));

  const settings = saved?.settings;
  const managed = !!saved?.from_env;
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
  // The summary's hint: where the bucket is and what it is for; for the
  // environment's, the variable that sets it, or what its Test found.
  const summaryHint = !saved ? null : managed
    ? (test?.busy ? t("Testing…") : test?.ok ? `✓ ${test.message}` : test ? <span className="aiKeysError">{test.message}</span>
      : t("Set by {name} in the server's environment", { name: "GAMMA_S3_BUCKET" }))
    : settings.bucket ? t("{where} · backup tasks can copy their snapshots here", { where: bucketWhere(settings) })
      : t("AWS S3, Cloudflare R2, MinIO or another S3-compatible store, for backup tasks to copy their snapshots to");
  return <>
    <Section title={t("Bucket")}>
      {!saved ? errorLine || <p className="setNotice loadingHint">{t("Loading…")}</p> : <>
        {/* The environment's values are the hover title of its read-only summary. */}
        <Row icon={CloudIcon} label={settings.bucket || t("No bucket yet")} hint={summaryHint}
          title={managed ? bucketWhere(settings) : undefined}>
          {managed ? <button className="uiBtn sm" disabled={!!test?.busy} onClick={testEnvironment}>{t("Test")}</button>
            : <button className="uiBtn sm" onClick={() => { setError(""); setEditing(true); }}><MorphText>{settings.bucket ? t("Edit…") : t("Set up…")}</MorphText></button>}
        </Row>
        {managed && (round?.enabled || round?.last_round_at) ? <>
          <Row icon={RefreshIcon} label={t("Off-site rounds")} hint={roundHint(round)}
            title={t("Set by the server's environment: every database and uploaded file, copied each round.")}>
            <button className="uiBtn sm" disabled={running || starting || !round.enabled} onClick={copyNow}>
              <MorphText>{running ? t("Copying…") : t("Copy now")}</MorphText>
            </button>
          </Row>
          {round.error ? <p className="settingsPaneHint aiKeysError" role="alert">{round.error}</p> : null}
        </> : null}
        {errorLine}
      </>}
    </Section>
    {editing ? (
      <BucketDialog settings={settings} onClose={() => setEditing(false)}
        onSaved={(value) => { offsite.set(value); setEditing(false); setStatus?.(t("Bucket saved.")); }} />
    ) : null}
  </>;
}
