// Settings → Server (admins only): everything that is about the server
// rather than one account — the dashboard (build, uptime, warnings, the
// update check), the storage defaults every account inherits, guests and
// demo mode (SettingsGuests.jsx), the shared
// AI provider every account may use (SettingsAi.jsx), the shared
// workspaces (settingsWorkspacesAdmin.jsx), whole-data-directory snapshots
// (settingsBackups.jsx ServerBackups) and the scrubbed server log with a
// level filter. Per-account things — including each account's personal
// workspaces — stay in Users; per-workspace backups in Backups.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { PaneHead, Section, Row, Segmented, StatText, UnitInput, LogBox } from "./SettingsKit";
import { WorkspacesAdmin } from "./SettingsWorkspacesAdmin";
import { ServerBackups } from "./SettingsBackups";
import { PublicUrlSettings } from "./SettingsPublicUrl";
import { CloudSignInSettings } from "./SettingsCloudSignIn";
import { GuestSettings } from "./SettingsGuests";
import { SharedAiProviderSettings } from "./SettingsAi";
import { ActivityIcon, AlertCircleIcon, CloudDownloadIcon, ImportIcon, ServerIcon } from "../shared/ui/Icons";
import { t } from "../shared/i18n/i18n.js";

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
      <Section title={t("Storage defaults")}>
        <ServerLimitRows setStatus={value.setStatus} refreshQuota={value.refreshQuota} />
      </Section>
      <Section title={t("Guests")}>
        <GuestSettings setStatus={value.setStatus} />
      </Section>
      <SharedAiProviderSettings setStatus={value.setStatus} confirm={value.confirm} />
      <WorkspacesAdmin value={value} />
      <ServerBackups setStatus={value.setStatus} confirm={value.confirm} />
      <Section title={t("Log")}>
        <ServerLogBox setStatus={value.setStatus} />
      </Section>
    </>
  );
}

function fmtUptime(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

// What the update row says: newer / current / unknown, in that order of use.
// A -dev build (Docker, built from a branch) also counts its branch's newer
// commits as an update.
function updateHint(info) {
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
// to pull the image; the desktop app updates on its own.
function ServerDashboard() {
  const [info, setInfo] = React.useState(null);
  const [error, setError] = React.useState("");
  const [checking, setChecking] = React.useState(false);
  const load = React.useCallback(async (refresh) => {
    if (refresh) setChecking(true);
    try {
      setInfo(await apiJson(`${API}/admin/server-info${refresh ? "?refresh=1" : ""}`));
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setChecking(false);
    }
  }, []);
  React.useEffect(() => {
    load(false);
    const timer = setInterval(() => load(false), 30000);
    return () => clearInterval(timer);
  }, [load]);
  if (!info) {
    return error ? <p className="settingsPaneHint aiKeysError" role="alert">{t("Dashboard unavailable: {error}", { error: error })}</p>
      : <p className="setNotice">{t("Loading…")}</p>;
  }
  const counts = info.log_counts || {};
  const logTone = counts.error ? "error" : counts.warning ? "warn" : "";
  const updateTone = info.update_available ? "warn" : "";
  return <>
    <div className="setStats">
      <StatText icon={ServerIcon} label="version" value={info.version ? `v${info.version}` : "dev build"}
        hint={info.commit ? t("build {commit}", { commit: info.commit }) : info.frozen ? t("desktop app") : t("run from a checkout")}
        title={t("Gamma {label} · Python {python} · {platform} · data schema {schema_version}", { label: info.label, python: info.python, platform: info.platform, schema_version: info.schema_version })} />
      <StatText icon={ActivityIcon} label="uptime" value={fmtUptime(info.uptime_seconds)}
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
        <button className={`uiBtn sm ${updateTone ? "primary" : ""}`} disabled={checking} onClick={() => load(true)}>
          {checking ? t("Checking…") : t("Check now")}
        </button>
      </span>
    </Row>
    {info.update?.kind === "release" ? (
      <div className="settingsPaneHint">
        {t("A server in Docker does not update itself: pull {latest} (or {version}) and restart the container. The desktop app updates on its own.", {
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

// Server-wide default storage limits (users.db via /api/admin/settings).
// Per-account overrides live in the Users pane.
// Each limit saves when its box commits (Enter / blur), like every other
// setting; an invalid entry snaps back to the stored value.
function ServerLimitRows({ setStatus, refreshQuota }) {
  const [saved, setSaved] = React.useState(null);
  const [error, setError] = React.useState("");
  React.useEffect(() => {
    apiJson(`${API}/admin/settings`).then(setSaved).catch((err) => setError(err.message));
  }, []);
  async function commit(key, raw, min) {
    const n = Number.parseInt(String(raw).trim(), 10);
    if (!Number.isFinite(n) || n < min) return; // the box shows the stored value again once the commit settles
    if (n === saved[key]) return;
    setError("");
    try {
      // Only this field: two boxes committing back to back must not overwrite each other.
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
      <Row icon={ImportIcon} label={t("Default max upload")} hint={t("Largest single file; Users can override per account")}>
        <UnitInput unit="MB" min={1} value={String(saved.max_upload_mb)}
          onCommit={(raw) => commit("max_upload_mb", raw, 1)} />
      </Row>
      <Row icon={ServerIcon} label={t("Default quota")} hint={t("Personal uploads per account; 0 = unlimited")}>
        <UnitInput unit="MB" min={0} value={String(saved.quota_mb)}
          onCommit={(raw) => commit("quota_mb", raw, 0)} />
      </Row>
    </> : !error ? <p className="setNotice">{t("Loading…")}</p> : null}
    {error ? <p className="settingsPaneHint aiKeysError" role="alert">{error}</p> : null}
  </>;
}

const LEVEL_FILTERS = [["all", t("All")], ["warn", t("Warnings")], ["error", t("Errors")]];
const toneOf = (level) => (level === "ERROR" || level === "CRITICAL" ? "error" : level === "WARNING" ? "warn" : "");

// The backend's in-memory log (GET /api/admin/logs). Polls with a seq
// cursor while the pane is open; secrets are scrubbed server-side before
// entries ever reach the buffer. The filter narrows to warnings or errors.
function ServerLogBox({ setStatus }) {
  const [entries, setEntries] = React.useState(null); // null = first poll pending
  const [error, setError] = React.useState("");
  const [level, setLevel] = React.useState("all");
  const stateRef = React.useRef({ cursor: 0, entries: [] });
  React.useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const data = await apiJson(`${API}/admin/logs?after=${stateRef.current.cursor}`);
        if (!alive) return;
        const fresh = data.entries || [];
        if (fresh.length) {
          stateRef.current.cursor = fresh[fresh.length - 1].seq;
          stateRef.current.entries = [...stateRef.current.entries, ...fresh].slice(-500);
        }
        setEntries([...stateRef.current.entries]);
        setError("");
      } catch (err) {
        if (alive) { setError(err.message); setEntries((prev) => prev || []); }
      }
    }
    poll();
    const timer = setInterval(poll, 2000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
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
      copyStatus={t("Server log copied.")}
      setStatus={setStatus}
      extra={<Segmented value={level} onChange={setLevel} options={LEVEL_FILTERS} />}
    />
  );
}
