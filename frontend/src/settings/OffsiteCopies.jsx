// Settings → Backups › Off-site copies (admins only): the server copies its
// databases and uploaded files to an S3-compatible bucket (AWS S3,
// Cloudflare R2, MinIO, …) every so often. The bucket is a backup target
// only; Gamma keeps running on its local files. GET/PUT /api/admin/offsite
// (the secret is write-only: `secret_set` says one is stored, and a PUT
// without `secret_key` keeps it), POST /api/admin/offsite/test with the
// unsaved fields, POST /api/admin/offsite/run for a round now. When the
// server's environment sets the bucket (GAMMA_S3_BUCKET, `from_env`), the
// rows are read-only. docs/dev/settings.md.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { MenuSelect } from "../shared/ui/Menus";
import { PasswordInput, Row, Section, Toggle, UnitInput, useSettingsDraft } from "./SettingsKit";
import { fmtWhen } from "./SettingsBackups";
import { ActivityIcon, ClockIcon, CloudIcon, CloudUploadIcon, FolderIcon, GlobeIcon, HistoryIcon, KeyIcon, LinkIcon, RefreshIcon } from "../shared/ui/Icons";
import { t, tn } from "../shared/i18n/i18n.js";

const ENDPOINT = `${API}/admin/offsite`;
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
const INTERVALS = [[900, t("Every 15 minutes")], [3600, t("Every hour")], [21600, t("Every 6 hours")], [86400, t("Every 24 hours")]];

// The choices, plus a stored interval that is none of them (set elsewhere).
const intervalOptions = (value) => (INTERVALS.some(([s]) => s === value) ? INTERVALS
  : [...INTERVALS, [value, tn("Every {n} minute", "Every {n} minutes", Math.round(value / 60))]].sort((a, b) => a[0] - b[0]));

// The form's copy of the saved settings. The secret starts empty: empty
// keeps the stored one.
const fromSaved = (s) => ({
  enabled: !!s.enabled, bucket: s.bucket || "", endpoint: s.endpoint || "", region: s.region || "",
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

export function OffsiteCopies({ setStatus }) {
  const [saved, setSaved] = React.useState(null); // GET's {settings, from_env}
  const [round, setRound] = React.useState(null); // GET's status
  const [draft, setDraft] = React.useState(null);
  const [test, setTest] = React.useState(null);   // {busy} | {ok, message}
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [starting, setStarting] = React.useState(false);
  const take = (value) => { setSaved(value); setRound(value.status); setDraft(fromSaved(value.settings)); setTest(null); };
  React.useEffect(() => {
    let active = true;
    apiJson(ENDPOINT).then((value) => { if (active) take(value); })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);
  // The status moves on by itself: read it every few seconds while a round
  // runs, else twice a minute. The form is left as it is.
  const running = !!round?.running;
  React.useEffect(() => {
    if (!saved) return undefined;
    const timer = setInterval(() => {
      apiJson(ENDPOINT).then((value) => setRound(value.status)).catch(() => {});
    }, running ? 3000 : 30000);
    return () => clearInterval(timer);
  }, [!!saved, running]); // eslint-disable-line react-hooks/exhaustive-deps

  const managed = !!saved?.from_env;
  const dirty = !!saved && !managed && JSON.stringify(draft) !== JSON.stringify(fromSaved(saved.settings));
  const discard = () => { setDraft(fromSaved(saved.settings)); setTest(null); setError(""); };
  useSettingsDraft("offsite-copies", dirty, discard);
  // A test result is about the values it tested.
  const set = (key) => (value) => { setDraft((d) => ({ ...d, [key]: value })); setTest(null); };

  async function save() {
    if (!dirty || busy) return;
    // A number the box cannot read would go as null, which the server takes as "unchanged".
    const keep = Number.parseInt(draft.keep, 10);
    if (!Number.isFinite(keep) || keep < 1) { setError(t("Keep at least one copy.")); return; }
    setBusy(true); setError("");
    try {
      take(await apiJson(ENDPOINT, json("PUT", payload(draft, keep))));
      setStatus?.(draft.enabled ? t("Off-site copies saved.") : t("Off-site copies turned off."));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  async function runTest() {
    setTest({ busy: true });
    try {
      // The environment's settings are tested as they are; otherwise the form's, saved or not.
      const keep = Number.parseInt(draft.keep, 10) || saved.settings.keep;
      const value = await apiJson(`${ENDPOINT}/test`, json("POST", managed ? null : payload(draft, keep)));
      setTest({ ok: !!value.ok, message: value.message || (value.ok ? t("Connected") : t("Could not connect")) });
    } catch (err) { setTest({ ok: false, message: err.message }); }
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

  const disabled = !saved || managed || busy;
  const errorLine = error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null;
  const onEnter = (event) => { if (event.key === "Enter") { event.preventDefault(); save(); } };
  const field = (key, label, placeholder, type = "text") => (
    <input className="aiKeyInput" type={type} aria-label={label} value={draft[key]} spellCheck={false}
      autoComplete="off" name={`gamma-offsite-${key}`} disabled={disabled} placeholder={placeholder}
      onChange={(event) => set(key)(event.target.value)} onKeyDown={onEnter} />
  );
  return (
    <Section title={t("Off-site copies")}
      action={dirty ? <button className="uiBtn sm primary" disabled={busy} onClick={save}>{busy ? t("Saving…") : t("Save")}</button> : null}>
      {!saved ? errorLine || <p className="setNotice">{t("Loading…")}</p> : <>
        <Toggle icon={CloudUploadIcon} label={t("Copy to a bucket")} checked={draft.enabled} disabled={disabled} onChange={set("enabled")}
          hint={managed ? t("Set by {name} in the server's environment", { name: "GAMMA_S3_BUCKET" })
            : t("Databases and uploaded files, every so often; Gamma keeps running on its own disk")}
          title={t("A backup target only: Gamma keeps reading and writing its local files and copies them to an S3-compatible bucket each round.")} />
        {draft.enabled || saved.settings.bucket || managed ? <>
          <Row icon={CloudIcon} label={t("Bucket")} hint={t("AWS S3, Cloudflare R2, MinIO or another S3-compatible store")}>
            <span className="setRowControls">{field("bucket", t("Bucket"), "gamma-backups")}</span>
          </Row>
          <Row icon={LinkIcon} label={t("Endpoint")} hint={t("Empty for AWS S3")}>
            <span className="setRowControls">{field("endpoint", t("Endpoint"), "https://<account>.r2.cloudflarestorage.com", "url")}</span>
          </Row>
          <Row icon={GlobeIcon} label={t("Region")} hint={t("auto for Cloudflare R2; the bucket's region on AWS")}>
            <span className="setRowControls">{field("region", t("Region"), "auto")}</span>
          </Row>
          <Row icon={KeyIcon} label={t("Access key")} hint={t("A key pair that may read and write the bucket")}>
            <span className="setRowControls">
              {field("access_key", t("Access key ID"), t("access key ID"))}
              <PasswordInput aria-label={t("Secret key")} value={draft.secret_key} disabled={disabled} autoComplete="new-password"
                name="gamma-offsite-secret" placeholder={saved.settings.secret_set ? t("secret set — type to replace") : t("secret key")}
                onChange={(event) => set("secret_key")(event.target.value)} onKeyDown={onEnter} />
            </span>
          </Row>
          <Row icon={FolderIcon} label={t("Prefix")} hint={t("Put before every name, so the bucket can hold other things")}>
            <span className="setRowControls">{field("prefix", t("Prefix"), "gamma")}</span>
          </Row>
          <Row icon={ClockIcon} label={t("How often")} hint={t("The time between two rounds")}>
            <MenuSelect label={t("How often")} value={draft.interval_s} options={intervalOptions(draft.interval_s)}
              onChange={set("interval_s")} disabled={disabled} />
          </Row>
          <Row icon={HistoryIcon} label={t("Copies kept")} hint={t("Per database; older copies are deleted from the bucket")}>
            <UnitInput unit="" min={1} label={t("Copies kept")} value={draft.keep} onChange={set("keep")} onEnter={save} disabled={disabled} />
          </Row>
          <Row icon={ActivityIcon} label={t("Test connection")}
            hint={test?.busy ? t("Testing…") : test ? (test.ok ? `✓ ${test.message}` : <span className="aiKeysError">{test.message}</span>)
              : t("Reach the bucket with the settings above, saved or not")}>
            <button className="uiBtn sm" disabled={!!test?.busy} onClick={runTest}>{t("Test")}</button>
          </Row>
        </> : null}
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
  );
}
