import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { ActionMenu, MenuSelect } from "../shared/ui/Menus";
import { ClockIcon, DatabaseIcon, HardDriveIcon, MoreIcon, PlusIcon, Trash2Icon } from "../shared/ui/Icons";
import { DialogButtons, Empty, Field, Section, Segmented, SubDialog, Toggle, ToggleGroup } from "./SettingsKit";
import { fmtWhen } from "./SettingsBackups";
import { fromUtcCron, localOffset, observesDst, parseCron, shiftTime, toUtcCron } from "./backupSchedule.js";
import "./backupTasks.css";
import { T, t, tn } from "../shared/i18n/i18n.js";

const endpoint = `${API}/backup-tasks`;
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const date = (value) => fmtWhen(value, t("Not yet"));
const pad = (n) => String(n).padStart(2, "0");
const days = [[1, t("Mon")], [2, t("Tue")], [3, t("Wed")], [4, t("Thu")], [5, t("Fri")], [6, t("Sat")], [0, t("Sun")]];
const SCOPES = [["all_owned", t("All I own")], ["selected", t("Selected")]];
const FREQUENCIES = [["hourly", t("Hourly")], ["daily", t("Daily")], ["weekly", t("Weekly")], ["monthly", t("Monthly")], ["custom", t("Custom schedule (cron)")]];
const UNITS = [["days", t("Days")], ["weeks", t("Weeks")], ["months", t("Months (30 days)")], ["count", t("Snapshots per workspace")]];
// The server's bounds (backup_schedule.py): a task's retention, in days or
// snapshots (MAX_RETENTION), and the tasks one account keeps (MAX_TASKS).
const MAX_RETENTION = 90;
const MAX_TASKS = 5;

// This browser's offset from UTC and its zone's short name ("PDT"): what a
// daily, weekly or hourly schedule is entered and shown in (backupSchedule.js).
const OFFSET = localOffset();
const ZONE = (() => {
  try { return new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(new Date()).find((p) => p.type === "timeZoneName")?.value || "UTC"; } catch { return "UTC"; }
})();
const clock = (p) => `${pad(p.hour)}:${pad(p.minute)}`;
const dayNames = (list) => days.filter(([n]) => list.includes(n)).map(([, name]) => name).join(", ");

// A task's schedule for the table: [in your time, in UTC] — monthly and
// custom schedules are UTC already.
function frequency(cron) {
  const utc = parseCron(cron);
  if (!utc) return [cron, "UTC"];
  const local = fromUtcCron(cron, OFFSET);
  if (utc.preset === "hourly") return [t("Hourly at :{minute}", { minute: pad(local.minute) }), OFFSET % 60 ? t(":{minute} UTC", { minute: pad(utc.minute) }) : null];
  if (utc.preset === "daily") return [t("Daily · {time}", { time: clock(local) }), t("{time} UTC", { time: clock(utc) })];
  if (utc.preset === "weekly") {
    const sameDays = dayNames(utc.weekdays) === dayNames(local.weekdays);
    return [`${dayNames(local.weekdays)} · ${clock(local)}`,
      sameDays ? t("{time} UTC", { time: clock(utc) }) : `${dayNames(utc.weekdays)} · ${t("{time} UTC", { time: clock(utc) })}`];
  }
  return [t("Day {day} · {time}", { day: utc.monthday, time: clock(utc) }), "UTC"];
}

// The run state as a dot and a word.
const STATES = {
  pending: ["muted", T("Not run yet")], queued: ["busy", T("Queued")], running: ["busy", T("Running")],
  finished: ["ok", T("Finished")], failed: ["bad", T("Failed")],
};

function TaskEditor({ initial, workspaces, onClose, onSaved }) {
  const [draft, setDraft] = React.useState(() => initial || ({
    name: "", enabled: true, scope: "all_owned", workspaces: [], cron: "0 3 * * *", uploads: true,
    retention_mode: "days", retention_value: 30,
  }));
  // Daily, weekly and hourly times are the browser's; monthly stays UTC.
  const [parsed] = React.useState(() => (initial?.cron ? fromUtcCron(initial.cron, OFFSET)
    : { preset: "daily", hour: 3, minute: 0, weekdays: [1], monthday: 1 }));
  const [preset, setPreset] = React.useState(parsed?.preset || "custom");
  const [time, setTime] = React.useState(parsed ? clock(parsed) : "03:00");
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
  const cron = preset === "custom" ? draft.cron
    : toUtcCron({ preset, hour, minute, weekdays, monthday: Number(monthday) }, OFFSET);
  const local = preset === "daily" || preset === "weekly";
  const utcAt = shiftTime(hour, minute, -OFFSET);
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

  // Monthly is entered in UTC, daily and weekly in your time: moving
  // between them keeps the moment and shows it in the new field's zone.
  function choosePreset(value) {
    if (value === "custom") patch({ cron });
    const zoned = (p) => (p === "daily" || p === "weekly" ? "local" : p === "monthly" ? "utc" : null);
    if (zoned(preset) && zoned(value) && zoned(preset) !== zoned(value)) {
      setTime(clock(shiftTime(hour, minute, zoned(value) === "local" ? OFFSET : -OFFSET)));
    }
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
    draft={{ draft, preset, time, weekdays, monthday, unit, amount }} className="backupTaskDialog">
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
          {preset !== "custom" ? <Field label={preset === "hourly" ? t("Minute past the hour")
            : local ? t("Time (your time, {zone})", { zone: ZONE }) : t("Time (UTC)")}>
            {preset === "hourly" ? <input className="aiKeyInput" type="number" min="0" max="59" required value={minute}
              onChange={(e) => setTime(`00:${e.target.value.padStart(2, "0")}`)} /> :
              <input className="aiKeyInput" type="time" required value={time} onChange={(e) => setTime(e.target.value)} />}
            {local && OFFSET !== 0 && Number.isFinite(utcAt.hour) ? <span className="settingDesc backupTaskUtc">
              {utcAt.days > 0 ? t("= {time} UTC, the next day", { time: clock(utcAt) })
                : utcAt.days < 0 ? t("= {time} UTC, the day before", { time: clock(utcAt) }) : t("= {time} UTC", { time: clock(utcAt) })}
            </span> : null}
            {preset === "hourly" && OFFSET % 60 !== 0 && Number.isFinite(minute) ? <span className="settingDesc backupTaskUtc">
              {t("= :{minute} past the hour in UTC", { minute: pad(parseCron(cron)?.minute ?? 0) })}
            </span> : null}
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
        {preset === "monthly" || preset === "custom" ? <p className="settingsPaneHint">{t("Schedules use UTC. Preview times below use your local timezone.")}
          {preset === "custom" ? t(" Supports * (any), commas, ranges, and steps. Example: 0 9,17 * * 1-5 runs weekdays at 09:00 and 17:00 UTC.") : null}</p>
          : observesDst() ? <p className="settingsPaneHint">{t("Saved in UTC, so the run moves by an hour when daylight saving time starts or ends.")}</p> : null}
        <div className="backupTaskPreview" aria-live="polite">
          <span className="settingLabel"><ClockIcon size={14} /> {t("Next three runs")}</span>
          {previewError ? <span className="aiKeysError">{previewError}</span> : preview ?
            <ol>{preview.map((value) => <li key={value}>{date(value)}</li>)}</ol> : <span className="settingDesc">{t("Checking schedule…")}</span>}
        </div>
        <Section title={t("Retention")} />
        <div className="backupTaskGrid">
          <Field label={unit === "count" ? t("Keep latest") : t("Keep for")}><input className="aiKeyInput" type="number" required min="1"
            max={Math.floor(MAX_RETENTION / ({ weeks: 7, months: 30 }[unit] || 1))} value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
          <Field label={t("Retention unit")}><MenuSelect block label={t("Retention unit")} value={unit} onChange={setUnit} options={UNITS} /></Field>
        </div>
        <p className="settingsPaneHint">{t("Only this task’s snapshots expire, after a successful run. The newest snapshot is always kept. Deleting a task keeps its snapshots.")}</p>
        <Toggle icon={ClockIcon} label={t("Enable task")} checked={draft.enabled} onChange={(enabled) => patch({ enabled })} hint={t("Paused tasks can still be run manually.")} />
        {error ? <p className="aiKeysError" role="alert">{error}</p> : null}
        <DialogButtons footnote={!draft.name.trim() ? t("Name the task to continue.")
          : draft.scope === "selected" && !draft.workspaces.length ? t("Pick at least one workspace to continue.") : null}>
          <button type="button" className="uiBtn" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="uiBtn primary" disabled={!preview || !draft.name.trim() || (draft.scope === "selected" && !draft.workspaces.length)}>
            {busy ? t("Saving…") : initial?.id ? t("Save changes") : t("Create task")}
          </button>
        </DialogButtons>
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
  const full = (tasks?.length || 0) >= MAX_TASKS;
  const limitHint = t("You can keep up to {n} backup tasks. Delete one to add another.", { n: MAX_TASKS });
  return <>
    <Section title={t("Periodic backup tasks")} action={<button className="uiBtn" disabled={!owned.length || full} onClick={() => setEditor({})}
      title={full ? limitHint : t("Runs while the server is on. Missed runs catch up once; failed tasks retry after an hour.")}><PlusIcon size={14} /> {t("Add task")}</button>} />
    {error ? <div className="backupTaskError aiKeysError" role="alert">{error}<button className="uiBtn sm" onClick={() => { setError(""); load(); }}>{t("Retry")}</button></div> : null}
    {tasks === null && !error ? <Empty icon={ClockIcon}>{t("Loading tasks…")}</Empty> : null}
    {tasks?.length === 0 ? <Empty icon={ClockIcon}>{t("No tasks yet. Add one for a nightly backup, or a schedule of your own.")}</Empty> : null}
    {!!tasks?.length && <div className="backupTaskTableWrap" role="region" aria-label={t("Periodic backup tasks")} tabIndex={0}>
      <table className="backupTaskTable"><thead><tr><th>{t("Task / Workspaces")}</th><th>{t("Frequency")}</th><th>{t("Next run")}</th><th>{t("Last run")}</th><th>{t("Enabled")}</th><th>{t("State")}</th><th><span className="srOnly">{t("Actions")}</span></th></tr></thead>
        <tbody>{tasks.map((task) => {
          const names = task.scope === "all_owned" ? t("All workspaces I own") : task.workspaces.map((id) => workspaces.find((w) => w.id === id)?.name || t("Unavailable workspace")).join(", ");
          const running = task.state === "running" || task.state === "queued";
          const [when, whenUtc] = frequency(task.cron);
          const [tone, word] = STATES[task.state] || ["muted", task.state];
          const keeps = task.retention_mode === "count"
            ? tn("keeps {n} snapshot", "keeps {n} snapshots", task.retention_value)
            : tn("keeps {n} day", "keeps {n} days", task.retention_value);
          return <tr key={task.id}>
            <td><strong>{task.name}</strong><span className="settingDesc" title={names}>
              {[names, keeps, task.uploads ? t("includes files") : t("databases only")].join(" · ")}</span></td>
            <td><span>{when}</span>{whenUtc ? <span className="settingDesc">{whenUtc}</span> : null}</td>
            <td>{task.requested ? t("Queued") : task.enabled ? date(task.next_run) : t("Paused")}</td>
            <td>{date(task.last_run)}</td>
            <td><label className="switch"><input type="checkbox" aria-label={t("Enable {name}", { name: task.name })} checked={task.enabled}
              disabled={running || busy === task.id} onChange={() => action(task, "toggle")} /><span className="switchTrack" /></label></td>
            <td><span className={`metaCell ${tone}`} title={task.last_error || (task.last_success ? t("Last successful: {last_success}", { last_success: date(task.last_success) }) : t("No runs yet"))}>
              <i className="setDot" />{t(word)}</span></td>
            <td><ActionMenu label={t("Actions for {name}", { name: task.name })} icon={MoreIcon} iconOnly disabled={running || busy === task.id} items={[
              { label: T("Run now"), icon: ClockIcon, onClick: () => action(task, "run") },
              { label: T("Edit task"), icon: DatabaseIcon, onClick: () => setEditor(task) },
              { label: T("Duplicate task"), icon: PlusIcon, disabled: full, title: full ? limitHint : undefined, onClick: () => setEditor({ ...task, id: undefined, name: t("{name} copy", { name: task.name }) }) },
              { label: T("Delete task"), icon: Trash2Icon, onClick: () => confirm({ title: T("Delete backup task"), message: t("Delete “{name}”? Existing snapshots are kept.", { name: task.name }), confirmLabel: t("Delete task"), danger: true, onConfirm: () => action(task, "delete") }) },
            ]} /></td>
          </tr>;
        })}</tbody></table>
    </div>}
    {full ? <p className="settingsPaneHint">{limitHint}</p> : null}
    {tasks?.filter((t) => t.last_error).map((it) => <p key={it.id} className="settingsPaneHint aiKeysError" role="status"><strong>{it.name}:</strong> {it.last_error}{it.enabled ? t(" Next attempt: {next_run}.", { next_run: date(it.next_run) }) : ""}</p>)}
    {editor !== null ? <TaskEditor initial={editor.name !== undefined ? editor : null} workspaces={workspaces} onClose={() => setEditor(null)} onSaved={() => { setEditor(null); load(); }} /> : null}
  </>;
}
