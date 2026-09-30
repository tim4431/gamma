// Background tasks in the topbar (docs/dev/tasks.md): the button with its
// state drawn on it, and the panel listing every task — what runs and
// waits, then what finished — each with its progress and what can be done
// with it (stop, download, start again, remove). A click on a task that has
// a window of its own (an export, an import) opens it again; other kinds
// open where they are managed (Settings → Backups). TaskProgress is the
// same progress block the export and import dialogs show.
import React from "react";
import { Empty } from "../settings/SettingsKit";
import {
  ActivityIcon, AlertCircleIcon, CheckIcon, ClockIcon, CloudDownloadIcon, DatabaseIcon, DownloadIcon,
  ExportIcon, FileIcon, HardDriveIcon, ImportIcon, RefreshIcon, SearchIcon, SparklesIcon, StopIcon,
  UploadIcon, XIcon,
} from "../shared/ui/Icons";
import { t, tn } from "../shared/i18n/i18n.js";
import { isActive, progressFraction } from "./taskModel.js";
import { canOpen, kindOf, progressText, taskActions, taskInfo, taskTime, taskTitle } from "./taskKinds.js";
import "./tasks.css";

const ICONS = {
  activity: ActivityIcon, cloudDownload: CloudDownloadIcon, database: DatabaseIcon, download: DownloadIcon,
  export: ExportIcon, file: FileIcon, hardDrive: HardDriveIcon, import: ImportIcon, search: SearchIcon,
  sparkles: SparklesIcon, upload: UploadIcon,
};

// A clock that ticks while `live` (the elapsed times of running tasks).
function useNow(live) {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!live) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);
  return now;
}

export function TasksButton({ tasks, open, onToggle }) {
  const { badge, running } = tasks;
  return (
    <button
      type="button"
      className={`iconBtn transferBtn ${open ? "activeIcon" : ""}`}
      onClick={onToggle}
      data-guide="header.tasks"
      aria-expanded={open}
      title={running ? tn("Background tasks — {n} running", "Background tasks — {n} running", running)
        : t("Background tasks — exports, backups, imports, downloads and uploads")}
      aria-label={t("Background tasks")}
    >
      <ActivityIcon size={16} />
      <TaskBadge badge={badge} />
    </button>
  );
}

// The button's mark: a spinner while anything runs, a red dot for a failure
// worth a look, an accent dot for a file waiting to be downloaded.
export function TaskBadge({ badge, inline = false }) {
  if (badge === "running") return <span className={`transferSpin${inline ? " inline" : ""}`} aria-hidden="true" />;
  if (badge === "failed" || badge === "ready") {
    return <span className={`${inline ? "noticeDot inline" : "transferDot"}${badge === "ready" ? " info" : ""}`} aria-hidden="true" />;
  }
  return null;
}

function StatusGlyph({ state }) {
  const glyph = state === "running" ? <span className="transferSpin inline" />
    : state === "queued" ? <ClockIcon size={14} />
      : state === "done" ? <CheckIcon size={14} />
        : state === "failed" ? <AlertCircleIcon size={14} /> : <XIcon size={14} />;
  return <span className={`taskStatus ${state}`} aria-hidden="true">{glyph}</span>;
}

// A thin bar: how far the task got, or a sweep while it cannot tell.
function TaskBar({ task, label }) {
  const fraction = progressFraction(task);
  const pct = fraction === undefined ? undefined : Math.round(fraction * 100);
  return (
    <span className={`taskBar${pct === undefined ? " indeterminate" : ""}`} role="progressbar" aria-label={label}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <span style={pct === undefined ? undefined : { width: `${pct}%` }} />
    </span>
  );
}

const ACTION = {
  stop: { icon: StopIcon, label: (title) => t("Stop {name}", { name: title }), run: (tasks, task) => tasks.cancel(task) },
  download: { icon: DownloadIcon, label: (title) => t("Download {name}", { name: title }), run: (tasks, task) => tasks.download(task) },
  retry: { icon: RefreshIcon, label: (title) => t("Start {name} again", { name: title }), run: (tasks, task, onRetry) => onRetry(task) },
};

function TaskRow({ task, tasks, now, onOpen, onRetry }) {
  const title = taskTitle(task);
  const info = taskInfo(task);
  const when = taskTime(task, now);
  const Icon = ICONS[kindOf(task).icon] || ActivityIcon;
  const openable = onOpen && canOpen(task);
  const body = (
    <>
      <span className="taskTitleLine">
        <span className="taskName">{title}</span>
        {when ? <span className="taskTime">{when}</span> : null}
      </span>
      {isActive(task) ? <TaskBar task={task} label={t("{name} progress", { name: title })} /> : null}
      {info ? <span className={`taskInfo${task.state === "failed" ? " error" : ""}`}>{info}</span> : null}
    </>
  );
  return (
    <div className={`taskRow ${task.state}`} title={info ? `${title} — ${info}` : title}>
      <StatusGlyph state={task.state} />
      <span className="taskKind" aria-hidden="true"><Icon size={14} /></span>
      {openable ? (
        <button type="button" className="taskMain" onClick={() => onOpen(task)} aria-label={t("Open {name}", { name: title })}>{body}</button>
      ) : <div className="taskMain">{body}</div>}
      <span className="ctlBtnRow taskActions">
        {taskActions(task).map((id) => {
          if (id === "dismiss") {
            return (
              <button key={id} type="button" className="uiClose uiCloseSm" title={t("Remove from the list")}
                aria-label={t("Remove {name}", { name: title })} onClick={() => tasks.dismiss(task).catch(() => {})}>
                <XIcon size={14} />
              </button>
            );
          }
          const action = ACTION[id];
          const ActionIcon = action.icon;
          return (
            <button key={id} type="button" className="ctlBtn" title={action.label(title)} aria-label={action.label(title)}
              onClick={() => Promise.resolve(action.run(tasks, task, onRetry)).catch(() => {})}>
              <ActionIcon size={16} />
            </button>
          );
        })}
      </span>
    </div>
  );
}

// The panel under the button. `onOpen(task)`: bring back the window of a
// task that has one; `onRetry(task)`: start the same work again.
export function TasksPanel({ tasks, onOpen, onRetry }) {
  const { watch } = tasks;
  React.useEffect(() => watch(), [watch]); // fresh progress while it is open
  const rows = tasks.tasks;
  const active = rows.filter(isActive);
  const finished = rows.filter((task) => !isActive(task));
  const now = useNow(active.length > 0);
  const row = (task) => <TaskRow key={task.id} task={task} tasks={tasks} now={now} onOpen={onOpen} onRetry={onRetry} />;
  return (
    <div className="popover tasksPopover" role="region" aria-label={t("Background tasks")}>
      <div className="popoverTitle tasksHead">
        <span>{t("Background tasks")}</span>
        {finished.length ? (
          <button type="button" className="uiBtn ghost sm" onClick={() => tasks.clear().catch(() => {})}>{t("Clear finished")}</button>
        ) : null}
      </div>
      {!rows.length ? <Empty icon={ActivityIcon}>{t("Nothing running")}</Empty> : null}
      {active.length ? <div className="popoverSection">{t("In progress")}</div> : null}
      {active.map(row)}
      {finished.length ? <div className="popoverSection">{t("Finished")}</div> : null}
      {finished.map(row)}
    </div>
  );
}

// A job's progress as a dialog shows it: the bar, what it is doing, how
// long it has run.
export function TaskProgress({ task, label }) {
  const now = useNow(task?.state === "running");
  if (!task) return null;
  const when = taskTime(task, now);
  return (
    <div className="taskProgress" role="status">
      <div className="taskProgressLine">
        <strong>{task.state === "queued" ? t("Waiting for its turn…") : progressText(task)}</strong>
        {when ? <span>{when}</span> : null}
      </div>
      <TaskBar task={task} label={label} />
    </div>
  );
}
