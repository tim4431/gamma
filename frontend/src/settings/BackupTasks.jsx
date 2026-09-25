import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { ActionMenu, MenuSelect } from "../shared/ui/Menus";
import { ClockIcon, DatabaseIcon, HardDriveIcon, MoreIcon, PlusIcon, Trash2Icon } from "../shared/ui/Icons";
import { Empty, Field, Section, Segmented, SubDialog, Toggle, ToggleGroup } from "./SettingsKit";
import { fmtWhen } from "./SettingsBackups";
import "./backupTasks.css";
import { T, t } from "../shared/i18n/i18n.js";

const endpoint = `${API}/backup-tasks`;
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const date = (value) => fmtWhen(value, t("Not yet"));
const pad = (n) => String(n).padStart(2, "0");
const days = [[1, t("Mon")], [2, t("Tue")], [3, t("Wed")], [4, t("Thu")], [5, t("Fri")], [6, t("Sat")], [0, t("Sun")]];
const SCOPES = [["all_owned", t("All I own")], ["selected", t("Selected")]];
const FREQUENCIES = [["hourly", t("Hourly")], ["daily", t("Daily")], ["weekly", t("Weekly")], ["monthly", t("Monthly")], ["custom", t("Custom schedule (cron)")]];
const UNITS = [["days", t("Days")], ["weeks", t("Weeks")], ["months", t("Months (30 days)")], ["count", t("Snapshots per workspace")]];

// The one cron reader: a five-field expression the editor's presets can
// express → {preset, minute, hour, weekdays, monthday}, or null for any
// other expression (shown raw, edited as "custom").
function parseCron(cron) {
  const [minute, hour, day, month, weekday] = cron.split(/\s+/);
  if (!/^\d+$/.test(minute) || month !== "*") return null;
  const base = { minute: Number(minute), hour: 0, weekdays: [1], monthday: 1 };
  if (hour === "*" && day === "*" && weekday === "*") return { ...base, preset: "hourly" };
  if (!/^\d+$/.test(hour)) return null;
  base.hour = Number(hour);
  if (day === "*" && weekday === "*") return { ...base, preset: "daily" };
  if (day === "*" && /^[0-6](,[0-6])*$/.test(weekday)) return { ...base, preset: "weekly", weekdays: weekday.split(",").map(Number) };
  if (/^\d+$/.test(day) && weekday === "*") return { ...base, preset: "monthly", monthday: Number(day) };
  return null;
}

function frequency(cron) {
  const parsed = parseCron(cron);
  if (!parsed) return cron;
  const time = `${pad(parsed.hour)}:${pad(parsed.minute)}`;
  if (parsed.preset === "hourly") return t("Hourly at :{minute}", { minute: pad(parsed.minute) });
  if (parsed.preset === "daily") return `Daily · ${time}`;
  if (parsed.preset === "weekly") return `${parsed.weekdays.map((d) => days.find(([n]) => n === d)[1]).join(", ")} · ${time}`;
  return `Day ${parsed.monthday} · ${time}`;
}

function TaskEditor({ initial, workspaces, onClose, onSaved }) {
  const [draft, setDraft] = React.useState(() => initial || ({
    name: "", enabled: true, scope: "all_owned", workspaces: [], cron: "0 3 * * *", uploads: true,
    retention_mode: "days", retention_value: 30,
  }));
  const [parsed] = React.useState(() => parseCron(initial?.cron || "0 3 * * *"));
  const [preset, setPreset] = React.useState(parsed?.preset || "custom");
  const [time, setTime] = React.useState(parsed ? `${pad(parsed.hour)}:${pad(parsed.minute)}` : "03:00");
  const [weekdays, setWeekdays] = React.useState(parsed?.weekdays || [1]);
  const [monthday, setMonthday] = React.useState(parsed?.monthday || 1);
  const [unit, setUnit] = React.useState(draft.retention_mode === "count" ? "count" : "days");
  const [amount, setAmount] = React.useState(draft.retention_value);
  const [preview, setPreview] = React.useState(null);
  const [previewError, setPreviewError] = React.useState("");
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const owned = workspaces.filter((w) => w.role === "owner");
  const patch = (values) => setDraft((prev) => ({ ...prev, ...values }));
  const [hour, minute] = time.split(":").map(Number);
  const cron = preset === "custom" ? draft.cron : preset === "hourly" ? `${minute} * * * *`
    : preset === "daily" ? `${minute} ${hour} * * *` : preset === "weekly" ? `${minute} ${hour} * * ${weekdays.join(",")}`
      : `${minute} ${hour} ${monthday} * *`;
  const retention = Number(amount) * ({ days: 1, weeks: 7, months: 30, count: 1 }[unit]);
  React.useEffect(() => {
    let active = true;
    setPreview(null);
    setPreviewError("");
    const timer = setTimeout(() => {
      apiJson(`${endpoint}/preview`, json("POST", { cron })).then((data) => {
        if (active) setPreview(data.runs);
      }).catch((e) => { if (active) setPreviewError(e.message); });
    }, 250);
    return () => { active = false; clearTimeout(timer); };
  }, [cron]);

  function choosePreset(value) {
    if (value === "custom") patch({ cron });
    setPreset(value);
  }

  async function submit(event) {
    event.preventDefault();
    if (!preview || busy) return;
    setBusy(true);
    setError("");
    try {
      await apiJson(initial?.id ? `${endpoint}/${initial.id}` : endpoint, json(initial?.id ? "PUT" : "POST", {
        ...draft, cron, retention_mode: unit === "count" ? "count" : "days", retention_value: retention,
      }));
      onSaved();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  return <SubDialog title={initial?.id ? t("Edit backup task") : t("Add backup task")} onClose={onClose}
    draft={{ draft, preset, time, weekdays, monthday, unit, amount }} className="backupTaskDialog" closeButton>
    <form onSubmit={submit}>
      <fieldset className="backupTaskFields" disabled={busy}>
        <Field label={t("Task name")}><input autoFocus className="aiKeyInput" required maxLength={80} value={draft.name}
          placeholder={t("e.g. Nightly research backup")} onChange={(e) => patch({ name: e.target.value })} /></Field>
        <Section title={t("What to back up")} />
        <Field label={t("Workspaces")}><Segmented value={draft.scope} onChange={(scope) => patch({ scope })} options={SCOPES} /></Field>
        {draft.scope === "all_owned" ? <p className="settingsPaneHint">{t("Includes new workspaces you own automatically. Each workspace gets its own restorable snapshot.")}</p> :
          <Field label={t("Selected workspaces")}>
            <ToggleGroup selected={draft.workspaces}
              onToggle={(id, on) => patch({ workspaces: on ? [...draft.workspaces, id] : draft.workspaces.filter((w) => w !== id) })}
              options={[
                ...owned.map((w) => [w.id, w.name, null, w.personal ? t("Personal workspace") : t("Shared workspace")]),
                ...draft.workspaces.filter((id) => !owned.some((w) => w.id === id)).map((id) => [id, t("Unavailable workspace"), null, t("Remove to save")]),
              ]} />
          </Field>}
        <Toggle icon={HardDriveIcon} label={t("Include uploaded files")} checked={draft.uploads} onChange={(uploads) => patch({ uploads })}
          hint={t("Include PDFs and images alongside notes and chats.")} />
        <Section title={t("When to run")} />
        <div className="backupTaskGrid">
          <Field label={t("Frequency")}><MenuSelect block label={t("Frequency")} value={preset} onChange={choosePreset} options={FREQUENCIES} /></Field>
          {preset !== "custom" ? <Field label={preset === "hourly" ? t("Minute past the hour") : t("Time (UTC)")}>
            {preset === "hourly" ? <input className="aiKeyInput" type="number" min="0" max="59" required value={minute}
              onChange={(e) => setTime(`00:${e.target.value.padStart(2, "0")}`)} /> :
              <input className="aiKeyInput" type="time" required value={time} onChange={(e) => setTime(e.target.value)} />}
          </Field> : null}
        </div>
        {preset === "weekly" ? <Field label={t("Days of the week")}>
          <ToggleGroup selected={weekdays} options={days}
            onToggle={(day, on) => setWeekdays((prev) => on ? [...prev, day] : prev.filter((d) => d !== day))} />
        </Field> : null}
        {preset === "monthly" ? <Field label={t("Day of the month")} hint={t("Dates absent from a month are skipped.")}>
          <input className="aiKeyInput" type="number" min="1" max="31" required value={monthday} onChange={(e) => setMonthday(e.target.value)} />
        </Field> : null}
        {preset === "custom" ? <Field label={t("Cron expression")} hint={t("Minute · hour · day of month · month · weekday")}>
          <input className="aiKeyInput backupCron" required value={draft.cron} onChange={(e) => patch({ cron: e.target.value })} placeholder="0 3 * * *" />
        </Field> : null}
        <p className="settingsPaneHint">{t("Schedules use UTC. Preview times below use your local timezone.")}
          {preset === "custom" ? t(" Supports * (any), commas, ranges, and steps. Example: 0 9,17 * * 1-5 runs weekdays at 09:00 and 17:00 UTC.") : null}</p>
        <div className="backupTaskPreview" aria-live="polite">
          <span className="settingLabel"><ClockIcon size={14} /> {t("Next three runs")}</span>
          {previewError ? <span className="aiKeysError">{previewError}</span> : preview ?
            <ol>{preview.map((value) => <li key={value}>{date(value)}</li>)}</ol> : <span className="settingDesc">{t("Checking schedule…")}</span>}
        </div>
        <Section title={t("Retention")} />
        <div className="backupTaskGrid">
          <Field label={unit === "count" ? t("Keep latest") : t("Keep for")}><input className="aiKeyInput" type="number" required min="1"
            max={Math.floor(3650 / ({ weeks: 7, months: 30 }[unit] || 1))} value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
          <Field label={t("Retention unit")}><MenuSelect block label={t("Retention unit")} value={unit} onChange={setUnit} options={UNITS} /></Field>
        </div>
        <p className="settingsPaneHint">{t("Only this task’s snapshots expire, after a successful run. The newest snapshot is always kept. Deleting a task keeps its snapshots.")}</p>
        <Toggle icon={ClockIcon} label={t("Enable task")} checked={draft.enabled} onChange={(enabled) => patch({ enabled })} hint={t("Paused tasks can still be run manually.")} />
        {error ? <p className="aiKeysError" role="alert">{error}</p> : null}
        <div className="reportModalBtns">
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={!preview || !draft.name.trim() || (draft.scope === "selected" && !draft.workspaces.length)}>
            {busy ? t("Saving…") : initial?.id ? t("Save changes") : t("Create task")}
          </button>
        </div>
      </fieldset>
    </form>
  </SubDialog>;
}

export function BackupTasks({ workspaces, confirm, onRefresh }) {
  const [tasks, setTasks] = React.useState(null);
  const [editor, setEditor] = React.useState(null);
  const [busy, setBusy] = React.useState(null);
  const [error, setError] = React.useState("");
  const previous = React.useRef("");
  const load = React.useCallback(async () => {
    try {
      const data = await apiJson(endpoint);
      setTasks(data.tasks);
      const signature = JSON.stringify(data.tasks.map((task) => [task.id, task.last_success]));
      if (previous.current && previous.current !== signature) workspaces.forEach((w) => onRefresh(w.id));
      previous.current = signature;
    } catch (e) { setError(e.message); }
  }, [workspaces, onRefresh]);
  React.useEffect(() => { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer); }, [load]);

  async function action(task, kind) {
    setBusy(task.id);
    setError("");
    try {
      if (kind === "toggle") await apiJson(`${endpoint}/${task.id}`, json("PUT", { ...task, enabled: !task.enabled }));
      else await apiJson(`${endpoint}/${task.id}${kind === "run" ? "/run" : ""}`, { method: kind === "run" ? "POST" : "DELETE" });
      await load();
    } catch (e) { setError(e.message); }
    finally { setBusy(null); }
  }

  const owned = workspaces.filter((w) => w.role === "owner");
  return <>
    <Section title={t("Periodic backup tasks")} action={<button className="uiBtn" disabled={!owned.length} onClick={() => setEditor({})}
      title={t("Runs while the server is on. Missed runs catch up once; failed tasks retry after an hour.")}><PlusIcon size={14} /> {t("Add task")}</button>} />
    {error ? <div className="backupTaskError aiKeysError" role="alert">{error}<button className="uiBtn sm" onClick={() => { setError(""); load(); }}>{t("Retry")}</button></div> : null}
    {tasks === null && !error ? <Empty icon={ClockIcon}>{t("Loading tasks…")}</Empty> : null}
    {tasks?.length === 0 ? <Empty icon={ClockIcon}>{t("No tasks yet. Add one for a nightly backup, or a schedule of your own.")}</Empty> : null}
    {!!tasks?.length && <div className="backupTaskTableWrap" role="region" aria-label={t("Periodic backup tasks")} tabIndex={0}>
      <table className="backupTaskTable"><thead><tr><th>{t("Task / Workspaces")}</th><th>{t("Keep for")}</th><th>{t("Frequency")}</th><th>{t("Next run")}</th><th>{t("Last run")}</th><th>{t("Enabled")}</th><th>{t("State")}</th><th><span className="srOnly">{t("Actions")}</span></th></tr></thead>
        <tbody>{tasks.map((task) => {
          const names = task.scope === "all_owned" ? t("All workspaces I own") : task.workspaces.map((id) => workspaces.find((w) => w.id === id)?.name || t("Unavailable workspace")).join(", ");
          const running = task.state === "running" || task.state === "queued";
          return <tr key={task.id}>
            <td><strong>{task.name}</strong><span className="settingDesc" title={names}>{names}</span><span className="settingDesc">{task.uploads ? t("Includes uploaded files") : t("Databases only")}</span></td>
            <td>{task.retention_value}<span className="settingDesc">{task.retention_mode === "count" ? "snapshots" : "days"}</span></td>
            <td><span>{frequency(task.cron)}</span><span className="settingDesc">UTC</span></td>
            <td>{task.requested ? t("Queued") : task.enabled ? date(task.next_run) : t("Paused")}</td>
            <td>{date(task.last_run)}</td>
            <td><label className="switch"><input type="checkbox" aria-label={t("Enable {name}", { name: task.name })} checked={task.enabled}
              disabled={running || busy === task.id} onChange={() => action(task, "toggle")} /><span className="switchTrack" /></label></td>
            <td><span className={`uiTag ${task.state}`} title={task.last_error || (task.last_success ? t("Last successful: {last_success}", { last_success: date(task.last_success) }) : t("No runs yet"))}>
              {task.state === "pending" ? t("Not run") : task.state}</span></td>
            <td><ActionMenu label={t("Actions for {name}", { name: task.name })} icon={MoreIcon} iconOnly disabled={running || busy === task.id} items={[
              { label: T("Run now"), icon: ClockIcon, onClick: () => action(task, "run") },
              { label: T("Edit task"), icon: DatabaseIcon, onClick: () => setEditor(task) },
              { label: T("Duplicate task"), icon: PlusIcon, onClick: () => setEditor({ ...task, id: undefined, name: `${task.name} copy` }) },
              { label: T("Delete task"), icon: Trash2Icon, onClick: () => confirm({ title: T("Delete backup task"), message: t("Delete “{name}”? Existing snapshots are kept.", { name: task.name }), confirmLabel: t("Delete task"), danger: true, onConfirm: () => action(task, "delete") }) },
            ]} /></td>
          </tr>;
        })}</tbody></table>
    </div>}
    {tasks?.filter((t) => t.last_error).map((it) => <p key={it.id} className="settingsPaneHint aiKeysError" role="status"><strong>{it.name}:</strong> {it.last_error}{it.enabled ? t(" Next attempt: {next_run}.", { next_run: date(it.next_run) }) : ""}</p>)}
    {editor !== null ? <TaskEditor initial={editor.name !== undefined ? editor : null} workspaces={workspaces} onClose={() => setEditor(null)} onSaved={() => { setEditor(null); load(); }} /> : null}
  </>;
}
