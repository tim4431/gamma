// Settings → Server (admins only): everything that is about the server
// rather than one account — the dashboard (build, uptime, warnings, the
// update check), on a hosted container the plan its limits come from
// (HostedPlan), the storage defaults every account inherits, guests and
// demo mode (SettingsGuests.jsx), the shared AI provider every account may
// use (SettingsAi.jsx), the shared workspaces (SettingsWorkspacesAdmin.jsx),
// the database check, whole-data-directory snapshots (SettingsBackups.jsx
// ServerBackups) and the scrubbed server log with a level filter. Per-account things — including each account's personal
// workspaces — stay in Users; per-workspace backups in Backups.
// The sections read through the dialog's store (settingsReads.js), so the
// pane's reads go out together and /api/admin/settings is read once for
// all the sections over it.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { LOG_START, useSettingsRead } from "./settingsReads.js";
import { PaneHead, Section, Row, Segmented, StatText, UnitInput, LogBox } from "./SettingsKit";
import { WorkspacesAdmin } from "./SettingsWorkspacesAdmin";
import { ServerBackups } from "./SettingsBackups";
import { PublicUrlSettings } from "./SettingsPublicUrl";
import { CloudSignInSettings } from "./SettingsCloudSignIn";
import { GuestSettings } from "./SettingsGuests";
import { SharedAiProviderSettings } from "./SettingsAi";
import { ActivityIcon, AlertCircleIcon, CloudDownloadIcon, CloudIcon, DatabaseIcon, ExternalLinkIcon, ServerIcon,
  UsersIcon } from "../shared/ui/Icons";
import { MorphText } from "../shared/ui/MotionWidgets";
import { fmtDate, t, tn } from "../shared/i18n/i18n.js";

export function ServerSettings({ value }) {
  const [signInAction, setSignInAction] = React.useState(null);
  return (
    <>
      <PaneHead icon={ServerIcon} title={t("Server")} />
      <Section title={t("Dashboard")}>
        <ServerDashboard />
      </Section>
      <Section title={t("Assistant connections")}>
        <PublicUrlSettings setStatus={value.setStatus} />
      </Section>
      <Section title={t("Sign-in")} action={signInAction}>
        <CloudSignInSettings setStatus={value.setStatus} action={setSignInAction} />
      </Section>
      <HostedPlan setStatus={value.setStatus} onSynced={() => value.refreshQuota?.()} />
      <Section title={t("Storage defaults")}>
        <ServerLimitRows setStatus={value.setStatus} refreshQuota={value.refreshQuota} />
      </Section>
      <GuestSettings setStatus={value.setStatus} />
      <SharedAiProviderSettings setStatus={value.setStatus} confirm={value.confirm} />
      <WorkspacesAdmin value={value} />
      <Section title={t("Databases")}>
        <DatabaseCheck setStatus={value.setStatus} />
      </Section>
      <ServerBackups setStatus={value.setStatus} confirm={value.confirm} tasks={value.tasks} />
      <Section title={t("Log")}>
        <ServerLogBox setStatus={value.setStatus} />
      </Section>
    </>
  );
}

function fmtUptime(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return t("{d}d {h}h", { d, h });
  if (h) return t("{h}h {m}m", { h, m });
  return t("{m}m", { m });
}

// What the update row says: newer / current / unknown, in that order of use.
// A -dev build (Docker, built from a branch) also counts its branch's newer
// commits as an update.
function updateHint(info) {
  if (info.updates === "mas") return t("installed from the Mac App Store, which delivers its updates");
  const latest = info.latest?.version;
  if (info.update_available) return t("v{latest} is out — this server runs v{version}", { latest: info.update.version, version: info.version });
  if (info.update_available === false && info.latest_build) {
    return latest ? t("up to date with {branch} · latest release v{latest}", { branch: info.latest_build.branch, latest })
      : t("up to date with {branch}", { branch: info.latest_build.branch });
  }
  if (info.update_available === false) return t("up to date · latest release v{latest}", { latest });
  if (latest) return t("latest release v{latest} · this build carries no version to compare", { latest });
  if (info.latest_error) return t("could not check: {latest_error}", { latest_error: info.latest_error });
  return t("checking…");
}

// GET /api/admin/server-info: the build, uptime, log counts by level and the
// GitHub release check (cached server-side; "Check now" refreshes). A
// Docker server cannot update itself, so an available update is a hint
// to pull the image. The desktop app's server says who installs it
// (`updates`): the app itself, or in a Microsoft Store install the Store,
// which has a release only once Microsoft certifies it — no warning
// tone there, and Open Store leads to the Store's updates page. A Mac App
// Store install may point at no other way to update, so its server asks
// GitHub nothing (GAMMA_UPDATE_CHECK off) and the row only says so.
const STORE_UPDATES_URL = "ms-windows-store://downloadsandupdates";

function ServerDashboard() {
  const { data: info, error: readError, set: setInfo } = useSettingsRead("/admin/server-info", { poll: 30000 });
  const [checkError, setCheckError] = React.useState("");
  const [checking, setChecking] = React.useState(false);
  const error = checkError || readError;
  async function checkNow() {
    setChecking(true);
    try {
      setInfo(await apiJson(`${API}/admin/server-info?refresh=1`));
      setCheckError("");
    } catch (err) {
      setCheckError(err.message);
    } finally {
      setChecking(false);
    }
  }
  if (!info) {
    return error ? <p className="settingsPaneHint aiKeysError" role="alert">{t("Dashboard unavailable: {error}", { error: error })}</p>
      : <p className="setNotice loadingHint">{t("Loading…")}</p>;
  }
  const counts = info.log_counts || {};
  const logTone = counts.error ? "error" : counts.warning ? "warn" : "";
  const fromStore = info.updates === "store";
  const updateTone = info.update_available && !fromStore ? "warn" : "";
  return <>
    <div className="setStats">
      <StatText icon={ServerIcon} label={t("version")} value={info.version ? `v${info.version}` : t("dev build")}
        hint={info.commit ? t("build {commit}", { commit: info.commit }) : info.frozen ? t("desktop app") : t("run from a checkout")}
        title={t("Gamma {label} · Python {python} · {platform} · data schema {schema_version}", { label: info.label, python: info.python, platform: info.platform, schema_version: info.schema_version })} />
      <StatText icon={ActivityIcon} label={t("uptime")} value={fmtUptime(info.uptime_seconds)}
        hint={t("since {started}", { started: new Date(info.started_at).toLocaleString() })} />
      <StatText icon={AlertCircleIcon} label={t("warnings · errors")} value={`${counts.warning || 0} · ${counts.error || 0}`}
        hint={t("{info} info lines since start", { info: counts.info || 0 })} tone={logTone}
        title={t("Lines logged since the server started, by level. The log below shows the most recent ones.")} />
    </div>
    <Row icon={CloudDownloadIcon} label={t("Updates")} hint={updateHint(info)}
      title={info.branch && info.version.includes("-dev.")
        ? t("Compared against the newest GitHub release and the newest commit on {branch}. Checked at most every six hours; Check now asks again.", { branch: info.branch })
        : t("Compared against the newest GitHub release. Checked at most every six hours; Check now asks again.")}>
      <span className="setRowControls">
        {info.update?.kind === "build" && info.update.url ? (
          <button className="uiBtn sm" onClick={() => window.open(info.update.url, "_blank", "noopener")}>{t("What's new")}</button>
        ) : info.latest?.url ? (
          <button className="uiBtn sm" onClick={() => window.open(info.latest.url, "_blank", "noopener")}>{t("Release notes")}</button>
        ) : null}
        {fromStore && info.update?.kind === "release" ? (
          <button className="uiBtn sm" onClick={() => window.open(STORE_UPDATES_URL, "_blank", "noopener")}>{t("Open Store")}</button>
        ) : null}
        {info.updates === "mas" ? null : (
          <button className={`uiBtn sm ${updateTone ? "primary" : ""}`} disabled={checking} onClick={checkNow}>
            <MorphText>{checking ? t("Checking…") : t("Check now")}</MorphText>
          </button>
        )}
      </span>
    </Row>
    {info.update?.kind === "release" ? (
      <div className="settingsPaneHint">
        {fromStore
          ? t("Installed from the Microsoft Store: the Store has a release once Microsoft certifies it, one to three days after it is out, and Windows then installs it. To get it sooner, Open Store → Get updates.")
          : info.updates === "desktop"
            ? t("The desktop app updates itself: its top bar offers the update when it is ready, and Help → Check for Updates… asks now.")
            : t("A server in Docker does not update itself: pull {latest} (or {version}) and restart the container.", {
              latest: <code>{info.image}:latest</code>, version: <code>:{info.update.version}</code> })}
      </div>
    ) : info.update?.kind === "build" ? (
      <div className="settingsPaneHint">
        {info.branch === "main"
          ? t("A server in Docker does not update itself: pull {latest} and restart the container.", { latest: <code>{info.image}:latest</code> })
          : t("Newer commits are on {branch}: build that branch's image and restart the container.", { branch: <code>{info.branch}</code> })}
      </div>
    ) : null}
  </>;
}

const PLAN_STATUS = {
  active: [t("active"), "ok"],
  grace: [t("grace period"), "warn"],
  read_only: [t("read-only"), "failed"],
  stopped: [t("stopped"), "failed"],
};
const POLICY_NAMES = { refuse: t("linked accounts only"), claim: t("cloud accounts claim their usernames"),
  provision: t("any cloud account"), invited: t("invited people only") };

// Settings → Server → Plan, on a hosted container only (gamma/hosted.py,
// the `hosted` block of /api/admin/settings): the account server's last
// answer — plan and status, the accounts, storage and sign-in it allows —
// and when it came. Read-only: the plan changes on Gamma Cloud's /plan page
// (Manage plan); Sync now (POST /api/admin/hosted/sync) asks for the
// answer again instead of waiting for the hourly sync.
function HostedPlan({ setStatus, onSynced }) {
  const { data: settings, set: setSettings, reload } = useSettingsRead("/admin/settings");
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const pane = settings?.hosted || null;
  if (!pane) return null;
  async function sync() {
    setBusy(true); setError("");
    try {
      const d = await apiJson(`${API}/admin/hosted/sync`, { method: "POST" });
      setSettings((s) => ({ ...s, hosted: d.hosted })); // a failed sync shows as the pane's last_failure
      // the plan caps the storage rows below: the settings are read again
      if (d.ok) { setStatus(t("The plan was synced with Gamma Cloud.")); reload().catch(() => {}); onSynced?.(); }
    } catch (err) { setError(t("Could not sync: {message}", { message: err.message })); }
    finally { setBusy(false); }
  }
  const plan = pane.limits;
  const [statusLabel, tone] = PLAN_STATUS[plan?.status] || [plan?.status || "", ""];
  const synced = plan?.synced_at ? t("last synced {time}", { time: fmtDate(plan.synced_at, { dateStyle: "medium", timeStyle: "short" }) })
    : t("not synced yet");
  const failed = pane.last_failure?.error;
  return (
    <Section title={t("Plan")}>
      <Row icon={CloudIcon} label={t("Plan")}
        hint={plan ? `${plan.plan} · ${synced}` : synced}
        title={t("This server is hosted by Gamma Cloud: its plan sets the storage, the accounts and who may sign in. The limits are fetched every hour.")}>
        <span className="setRowControls">
          {plan ? <span className={`uiTag ${tone}`}>{statusLabel}</span> : null}
          {pane.issuer ? (
            <a className="uiBtn sm" href={`${pane.issuer}/plan`} target="_blank" rel="noopener">
              <ExternalLinkIcon size={14} /> {t("Manage plan")}
            </a>) : null}
          <button className="uiBtn sm" disabled={busy} onClick={sync}><MorphText>{busy ? t("Syncing…") : t("Sync now")}</MorphText></button>
        </span>
      </Row>
      {plan ? (
        <Row icon={UsersIcon} label={t("Plan limits")}
          hint={[
            plan.max_accounts ? t("{n} of {max} accounts", { n: pane.accounts, max: plan.max_accounts })
              : tn("{n} account", "{n} accounts", pane.accounts),
            plan.quota_mb ? t("{n} MB per account", { n: plan.quota_mb }) : null,
            plan.policy ? t("sign-in: {policy}", { policy: POLICY_NAMES[plan.policy] || plan.policy }) : null,
          ].filter(Boolean).join(" · ")}
          title={t("The default quota below may be set lower than the plan's cap, never higher.")} />
      ) : null}
      {plan?.status === "grace" ? (
        <p className="settingsPaneHint" role="status">
          {plan.grace_until
            ? t("Payment for this server failed: it becomes read-only on {date}", { date: fmtDate(plan.grace_until, { dateStyle: "long" }) })
            : t("Payment for this server failed: it becomes read-only soon")}
          {plan.message ? <><br />{plan.message}</> : null}
        </p>
      ) : plan?.read_only ? (
        <p className="settingsPaneHint aiKeysError" role="alert">
          {t("This server is read-only: people can sign in, read and export, but nothing can be changed.")}
          {plan.message ? <><br />{plan.message}</> : null}
        </p>
      ) : null}
      {failed ? <p className="settingsPaneHint aiKeysError" role="alert">{t("The last sync failed: {error}", { error: failed })}</p> : null}
      {error && !failed ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
    </Section>
  );
}

// What a storage row says under its box: the plan's cap when it decides,
// else the row's own hint and the cap it may not pass.
function limitHint(saved, key, own) {
  const cap = saved.plan_caps?.[key];
  if (saved[`${key}_source`] === "plan") return t("The plan's cap of {n} MB; you can set a lower one", { n: saved[key] });
  return cap ? t("{own} · the plan allows up to {n} MB", { own, n: cap }) : own;
}

// The server-wide default storage quota (users.db via /api/admin/settings);
// one file has no size cap of its own. Per-account overrides live in the
// Users pane. On a hosted container the plan caps it (`plan_caps`): a lower
// value saves, a higher one is refused. It saves when its box commits
// (Enter / blur), like every other setting; an invalid entry snaps back to
// the stored value.
function ServerLimitRows({ setStatus, refreshQuota }) {
  const { data: saved, error: readError, set: setSaved } = useSettingsRead("/admin/settings");
  const [saveError, setError] = React.useState("");
  const error = saveError || (!saved ? readError : "");
  // The box commits only after an edit, so every commit is sent: a check
  // against `saved` could read a value an earlier save has already replaced
  // on the server and drop this one.
  async function commit(key, raw, min) {
    const n = Number.parseInt(String(raw).trim(), 10);
    if (!Number.isFinite(n) || n < min) return; // the box shows the stored value again once the commit settles
    setError("");
    try {
      // Only this field: a save of another setting just before must not be overwritten.
      const value = await apiJson(`${API}/admin/settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: n }),
      });
      setSaved(value); refreshQuota?.();
      setStatus(t("Storage defaults saved."));
    } catch (err) { setError(t("Could not save: {message}", { message: err.message })); }
  }
  return <>
    {saved ? <>
      <Row icon={ServerIcon} label={t("Default quota")}
        hint={limitHint(saved, "quota_mb", t("Personal uploads per account; 0 = unlimited"))}>
        <UnitInput unit="MB" min={0} value={String(saved.quota_mb)}
          onCommit={(raw) => commit("quota_mb", raw, 0)} />
      </Row>
    </> : !error ? <p className="setNotice loadingHint">{t("Loading…")}</p> : null}
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </>;
}

// POST /api/admin/check-databases: SQLite's quick check of users.db and of
// every workspace's pages.db and data.db, on demand. Snapshots check their
// copies the same way; damage found either way is also a notice for admins
// (gamma/integrity.py). The row says how the last check came out and each
// damaged file what SQLite found.
function DatabaseCheck({ setStatus }) {
  const [result, setResult] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  async function run() {
    setBusy(true);
    setError("");
    try {
      const d = await apiJson(`${API}/admin/check-databases`, { method: "POST" });
      setResult(d);
      setStatus(d.ok ? t("Every database passed its check.") : t("The database check found damage."));
    } catch (err) {
      setError(t("Could not check: {message}", { message: err.message }));
    } finally {
      setBusy(false);
    }
  }
  const files = result?.files || [];
  const bad = files.filter((f) => !f.ok);
  const hint = !result ? t("a quick check of every account and workspace database")
    : bad.length ? tn("{n} damaged database file (of {total})", "{n} damaged database files (of {total})", bad.length, { total: files.length })
      : tn("{n} database file passed · {time}", "All {n} database files passed · {time}", files.length,
        { time: new Date(result.checked_at).toLocaleTimeString() });
  return <>
    <Row icon={DatabaseIcon} label={t("Check databases")} hint={hint}
      title={t("Runs SQLite's quick_check on users.db and on every workspace's pages.db and data.db. Nothing is changed; a damaged file is best replaced from a backup.")}>
      <button className="uiBtn sm" disabled={busy} onClick={run}><MorphText>{busy ? t("Checking…") : t("Check now")}</MorphText></button>
    </Row>
    {bad.map((f) => (
      <p key={f.file} className="settingsPaneHint aiKeysError" role="alert">
        {t("{name} {file}: {result}", { name: f.name ? `${f.name} ·` : "", file: <code>{f.file}</code>, result: f.result })}
      </p>
    ))}
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </>;
}

const LEVEL_FILTERS = [["all", t("All")], ["warn", t("Warnings")], ["error", t("Errors")]];
const toneOf = (level) => (level === "ERROR" || level === "CRITICAL" ? "error" : level === "WARNING" ? "warn" : "");

// The backend's in-memory log (GET /api/admin/logs). The pane's read takes
// it from the start (LOG_START, kept in the dialog's store, so a second
// visit shows what the first had while it reads again); then it polls
// with a seq cursor while the pane is open, keeping the last 500 lines.
// Secrets are scrubbed server-side before entries ever reach the buffer.
// The filter narrows to warnings or errors.
const LOG_KEEP = 500;
function ServerLogBox({ setStatus }) {
  const { data, error: readError, set } = useSettingsRead(LOG_START);
  const [pollError, setPollError] = React.useState("");
  const [level, setLevel] = React.useState("all");
  const entries = data ? (data.entries || []).slice(-LOG_KEEP) : readError ? [] : null; // null = first read pending
  const error = pollError || readError;
  const latest = React.useRef(data);
  latest.current = data;
  const loaded = !!data;
  React.useEffect(() => {
    if (!loaded) return undefined;
    let alive = true;
    const timer = setInterval(async () => {
      const after = latest.current?.entries?.at(-1)?.seq || 0;
      try {
        const fresh = (await apiJson(`${API}/admin/logs?after=${after}`)).entries || [];
        if (!alive) return;
        setPollError("");
        if (!fresh.length) return;
        // only what is newer than the last line held: the pane's read again may have brought some
        set((d) => {
          const last = d?.entries?.at(-1)?.seq || 0;
          return { ...d, entries: [...(d?.entries || []), ...fresh.filter((entry) => entry.seq > last)].slice(-LOG_KEEP) };
        });
      } catch (err) {
        if (alive) setPollError(err.message);
      }
    }, 2000);
    return () => { alive = false; clearInterval(timer); };
  }, [loaded, set]);
  const shown = (entries || [])
    .map((entry) => ({ key: entry.seq, timeMs: entry.t * 1000, text: entry.msg, tone: toneOf(entry.level) }))
    .filter((entry) => level === "all" || (level === "warn" ? !!entry.tone : entry.tone === "error"));
  return (
    <LogBox
      icon={ServerIcon}
      label={t("Server log")}
      description={t("Backend events since startup · secrets masked")}
      entries={shown}
      emptyText={error ? t("Server log unavailable: {error}", { error })
        : !entries ? t("Loading…") : level === "all" ? t("Nothing logged since the server started.")
            : t("No {errors} since the server started.", { errors: level === "warn" ? t("warnings") : t("errors") })}
      loading={!entries && !error}
      copyStatus={t("Server log copied.")}
      setStatus={setStatus}
      extra={<Segmented value={level} onChange={setLevel} options={LEVEL_FILTERS} />}
    />
  );
}
