// The building blocks every settings pane is composed from — and nothing
// else: PaneHead › Section › Row/Toggle for the panes themselves, SubDialog ›
// Step/Field for the editor dialogs they open, plus the small shared controls
// (Segmented, PictureChoices, Stepper, UnitInput, CharSlider, AccountPicker, LogBox, Stat, Empty, QuotaMeter/PercentMeter, KeyBinding). New settings
// UI should reuse these; bespoke classes are for layout only.
import React from "react";
import { API, apiJson, copyText, fmtBytes } from "../shared/lib/utils";
import { AlertCircleIcon, CheckIcon, CloudCheckIcon, EyeIcon, EyeOffIcon, MonitorIcon, RefreshIcon, ShieldIcon, UndoIcon, UserIcon } from "../shared/ui/Icons";
import { bindable, chordFromEvent, chordParts } from "../shared/lib/hotkeys.js";
import { BROWSER_TAG, profileSyncState } from "./syncState.js";
import { t } from "../shared/i18n/i18n.js";
import { fmtTokens } from "../chat/tokenUsage";

export const SettingsDraftContext = React.createContext(null);

export function useSettingsDraft(key, dirty, discard) {
  const drafts = React.useContext(SettingsDraftContext);
  React.useEffect(() => {
    if (dirty) drafts?.current.set(key, discard);
    else drafts?.current.delete(key);
    return () => drafts?.current.delete(key);
  }, [drafts, key, dirty, discard]);
}

export function PaneHead({ icon: Icon, title, children }) {
  return (
    <div className="setHead" data-setting={title}>
      <span className="setHeadIcon"><Icon size={17} /></span>
      <span className="settingText">
        <span className="settingsPaneTitle">{title}</span>
        {children ? <span className="settingsPaneHint">{children}</span> : null}
      </span>
    </div>
  );
}

// `scope` tags where the section's settings live: "account" (they follow
// the signed-in account, app/prefDefs.js) or "browser" (this device only).
// An account section also names the preferences it holds (`prefs`, from
// settings/sectionPrefs.js), and its tag reads their live sync state from
// SettingsSyncContext ({local, cloud, refresh}, provided by the dialog;
// settings/syncState.js). The tag is an icon and one muted word; the
// sentence is its hover title and accessible name.
export const SettingsSyncContext = React.createContext(null);
const SYNC_ICONS = { monitor: MonitorIcon, check: CheckIcon, cloudCheck: CloudCheckIcon, refresh: RefreshIcon, alert: AlertCircleIcon };

// A save takes about a second; the spinner it starts keeps turning at least
// this long, so the feedback is seen rather than flickered.
const MIN_SPIN_MS = 700;

function useMinimumSpin(spin) {
  const [held, setHeld] = React.useState(false);
  const since = React.useRef(0);
  React.useEffect(() => {
    if (spin) { since.current = Date.now(); setHeld(true); return undefined; }
    const left = MIN_SPIN_MS - (Date.now() - since.current);
    if (left <= 0) { setHeld(false); return undefined; }
    const timer = setTimeout(() => setHeld(false), left);
    return () => clearTimeout(timer);
  }, [spin]);
  return spin || held;
}

function ScopeTag({ scope, prefs }) {
  const sync = React.useContext(SettingsSyncContext);
  const read = scope === "account" ? profileSyncState(sync?.local, sync?.cloud, prefs) : BROWSER_TAG;
  const spinning = useMinimumSpin(read.spin);
  const tag = spinning && !read.spin ? { ...read, state: "syncing", icon: "refresh", spin: true } : read;
  const Icon = SYNC_ICONS[tag.icon];
  const where = tag.state === "browser" ? t("Browser setting") : t("Account setting");
  return (
    <span className={`setScope ${tag.tone} ${tag.spin ? "mirrorSpin" : ""}`} data-scope={scope} data-sync={tag.state}
      role="img" aria-label={t("{where}. {title}", { where: where, title: tag.title })} title={tag.title}>
      {Icon ? <Icon size={14} /> : null}{tag.label}
    </span>
  );
}

// `guide`: a data-guide anchor id (guide/anchors.js) for the whole section,
// header and rows, which then sit in one box a tour can point at.
export function Section({ title, scope, prefs, action, guide, children }) {
  const body = (
    <>
      <div className="setSection" data-setting={title}>
        <span className="setSectionLabel">{title}</span>
        <span className="setSectionRule" />
        {scope ? <ScopeTag scope={scope} prefs={prefs} /> : null}
        {action}
      </div>
      {children}
    </>
  );
  return guide ? <div className="setSectionGroup" data-guide={guide}>{body}</div> : body;
}

// Keep the row compact: icon, label, short hint, and a shared control.
// Longer explanations use the native hover tooltip. `scope="browser"` tags
// the one row of an account section whose value stays with this browser.
export function Row({ icon: Icon, label, hint, title, scope, className = "", children }) {
  return (
    <div className={`settingRow setRow ${className}`} data-setting={label} title={title}>
      <span className="setIcon">{Icon ? <Icon size={15} /> : null}</span>
      <div className="settingText">
        <span className="settingLabel">{label}{scope ? <ScopeTag scope={scope} /> : null}</span>
        {hint ? <span className="settingDesc">{hint}</span> : null}
      </div>
      {children}
    </div>
  );
}

export function Toggle({ checked, onChange, disabled, label, ...row }) {
  return (
    <Row label={label} {...row}>
      <span className="switch">
        <input
          type="checkbox" checked={checked} disabled={disabled} aria-label={label}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="switchTrack" />
      </span>
    </Row>
  );
}

// Joined pill buttons for a single mutually-exclusive choice.
// `options` are [value, label, Icon, tooltip].
export function Segmented({ value, onChange, options, disabled }) {
  return (
    <span className="segGroup">
      {options.map(([val, label, Icon, tip]) => (
        <button
          key={val} type="button" title={t(tip || label)} disabled={disabled}
          aria-pressed={value === val}
          className={`uiBtn sm ${value === val ? "on" : ""}`}
          onClick={() => onChange(val)}
        >
          {Icon ? <Icon size={13} /> : null}{t(label)}
        </button>
      ))}
    </span>
  );
}

// Compact visual alternatives for a single preference. Previews are decorative;
// labels, descriptions and the pressed state identify each choice accessibly.
export function PictureChoices({ label, value, onChange, onConfirm, options, columns = 3 }) {
  return <div className="setPictureChoices" role="group" aria-label={label} style={{ "--picture-columns": columns }}>
    {options.map(({ value: id, label: name, hint, preview }) => (
      <button key={String(id)} type="button" className={`uiBtn setPictureChoice${value === id ? " on" : ""}`}
        aria-label={t(name)} aria-description={t(hint)} title={t(hint)} aria-pressed={value === id} onClick={() => onChange(id)}
        onDoubleClick={onConfirm ? () => onConfirm(id) : undefined}>
        {preview}
        <span className="setPictureCaption">
          <span className="setPictureName">{t(name)}</span>
          {hint ? <span className="setPictureHint">{t(hint)}</span> : null}
          <span className="setPictureCheck" aria-hidden="true">{value === id ? <CheckIcon size={12} /> : null}</span>
        </span>
      </button>
    ))}
  </div>;
}

// Picture choices whose picture is an icon tile: stacked glyph / name / hint,
// three or four to a row — an audience, an input mode, any small exclusive
// set that reads faster as tiles than as a dropdown. `options` are
// [{value, label, hint, Icon}].
export function IconChoices({ label, value, onChange, options, columns }) {
  return <div className="setIconTiles">
    <PictureChoices label={label} value={value} onChange={onChange} columns={columns || options.length}
      options={options.map(({ value: id, label: name, hint, Icon }) => ({
        value: id, label: name, hint,
        preview: <span className="setTileIcon" aria-hidden="true"><Icon size={18} /></span>,
      }))} />
  </div>;
}

// A row of small icon + short-name chips, each an independent on/off switch
// (the multi-select counterpart of Segmented): the agent's per-tool
// permissions, any "which of these" choice. `options` are
// [value, label, Icon, tooltip]; `selected` lists the values that are on.
// Chips wrap when the row is narrow, so it fits a settings row's control
// slot and a chat popover alike.
export function ToggleGroup({ selected, onToggle, options, disabled }) {
  const on = new Set(selected || []);
  return (
    <span className="toggleGroup" role="group">
      {options.map(([val, label, Icon, tip]) => (
        <button
          key={val} type="button" title={t(tip || label)} disabled={disabled}
          className={`uiBtn sm ${on.has(val) ? "on" : ""}`}
          aria-pressed={on.has(val)}
          onClick={() => onToggle(val, !on.has(val))}
        >
          {Icon ? <Icon size={13} /> : null}{t(label)}
        </button>
      ))}
    </span>
  );
}

// Draft-aware editor dialog opened from inside the settings surface — same shape as
// the PDF export dialog (reportModal), stacked above the settings overlay.
// Every editor dialog is composed the same way: SubDialog › .settingsForm ›
// Step (numbered stages, for flows) or Field (label + hint + one control),
// closed by a .reportModalBtns footer.
export function SubDialog({ title, onClose, children, draft, className = "", closeButton = false }) {
  const key = React.useId();
  const [initial] = React.useState(() => JSON.stringify(draft));
  const dirty = draft !== undefined && JSON.stringify(draft) !== initial;
  const [confirmClose, setConfirmClose] = React.useState(false);
  const ref = React.useRef(null);
  useSettingsDraft(key, dirty, onClose);
  const close = () => dirty ? setConfirmClose(true) : onClose();
  React.useEffect(() => {
    const previous = document.activeElement;
    if (!ref.current?.contains(document.activeElement)) ref.current?.focus();
    return () => previous?.focus?.();
  }, []);
  return (
    <div className="reportOverlay subDialog" onClick={(event) => { event.stopPropagation(); close(); }}>
      <div className={`reportModal ${className}`} role="dialog" aria-modal="true" aria-label={title}
        ref={ref} tabIndex={-1} onClick={(event) => event.stopPropagation()}
        onClickCapture={(event) => {
          if (dirty && event.target.closest("button")?.textContent.trim() === t("Cancel")) {
            event.preventDefault(); event.stopPropagation(); close();
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault(); event.stopPropagation();
            if (confirmClose) setConfirmClose(false); else close();
          }
          if (event.key === "Tab") {
            event.stopPropagation();
            const targets = [...ref.current.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), summary')]
              .filter((el) => el.getClientRects().length && !el.closest("[inert]"));
            const first = targets[0], last = targets.at(-1);
            if (event.shiftKey && (document.activeElement === first || !targets.includes(document.activeElement))) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && (document.activeElement === last || document.activeElement === ref.current)) { event.preventDefault(); first?.focus(); }
          }
        }}>
        {closeButton ? <div className="settingsDialogHeader" inert={confirmClose ? "" : undefined}>
          <div className="reportModalTitle">{title}</div>
          <button type="button" className="uiClose uiCloseLg" onClick={close} aria-label={t("Close {title}", { title: title })} title={t("Close")}>×</button>
        </div> : <div className="reportModalTitle">{title}</div>}
        <div className="settingsDialogContent" inert={confirmClose ? "" : undefined}>{children}</div>
        {confirmClose ? <div className="settingsUnsaved" role="alertdialog" aria-label={t("Unsaved changes")}>
          <span>{t("Discard your unsaved edits?")}</span>
          <button className="uiBtn" autoFocus onClick={() => setConfirmClose(false)}>{t("Keep editing")}</button>
          <button className="uiBtn danger" onClick={onClose}>{t("Discard changes")}</button>
        </div> : null}
      </div>
    </div>
  );
}

// Numbered stage of a dialog flow (the add/edit-key wizard).
export function Step({ n, title, hint, children }) {
  return (
    <div className="setStep">
      <div className="setStepHead">
        <span className="setStepNum">{n}</span>
        <span className="settingText">
          <span className="setStepTitle">{title}</span>
          {hint ? <span className="settingDesc">{hint}</span> : null}
        </span>
      </div>
      <div className="setStepBody">{children}</div>
    </div>
  );
}

// One labeled control: bold-ish caption, muted hint after it, control below.
export function Field({ label, hint, children }) {
  return (
    <label className="setField">
      <span className="setFieldLabel">
        {label}
        {hint ? <span className="settingDesc"> — {hint}</span> : null}
      </span>
      {children}
    </label>
  );
}

// A password box with a show/hide eye at its right edge. `className` is the
// input's own class (aiKeyInput in settings forms, loginInput on the login
// page); everything else is passed through to the <input>. The eye is kept
// out of the Tab order so Enter/Tab flow stays input → next control.
export function PasswordInput({ className = "aiKeyInput", ...props }) {
  const [shown, setShown] = React.useState(false);
  return (
    <span className="pwField">
      <input {...props} className={className} type={shown ? "text" : "password"} />
      <button
        type="button" className="ctlBtn pwToggle" tabIndex={-1}
        title={shown ? t("Hide password") : t("Show password")}
        aria-label={shown ? t("Hide password") : t("Show password")}
        aria-pressed={shown}
        onMouseDown={(event) => event.preventDefault()} // keep the input's focus + caret
        onClick={() => setShown((v) => !v)}
      >
        {shown ? <EyeOffIcon size={14} /> : <EyeIcon size={14} />}
      </button>
    </span>
  );
}

// Number input with a fixed unit suffix, so "MB" never has to live in the
// label text. Empty string means "inherit" wherever the caller says so.
// Two modes: live (onChange fires per keystroke — for draft state the caller
// buffers itself) or deferred (onCommit fires the raw text on blur/Enter —
// for handlers that clamp into range, so the clamp doesn't fight half-typed
// values: typing "25" into a 1–32 field must not snap at "2").
export function UnitInput({ value, onChange, onCommit, unit, placeholder, min, onEnter, disabled, label }) {
  const [draft, setDraft] = React.useState(null); // non-null only while editing deferred
  // The draft stays on screen until the commit settles (an async save), then
  // the stored value shows — the parent never needs a `key` remount to reset
  // the box, which would drop a value typed while the save was landing.
  const commit = async () => {
    if (draft == null) return;
    try { await onCommit(draft); } finally { setDraft(null); }
  };
  return (
    <span className="unitInput">
      <input
        className="aiKeyInput" type="number" min={min} disabled={disabled} aria-label={label}
        placeholder={placeholder} value={onCommit ? (draft ?? String(value ?? "")) : value}
        onChange={(event) => (onCommit ? setDraft(event.target.value) : onChange(event.target.value))}
        onBlur={onCommit ? commit : undefined}
        onKeyDown={(event) => {
          if (event.key !== "Enter") return;
          if (onCommit) event.currentTarget.blur(); // commit via onBlur
          onEnter?.();
        }}
      />
      <span className="unitSuffix">{unit}</span>
    </span>
  );
}

// A −/+ stepper for a small numeric range (the control size): two square
// `uiBtn sm iconSq` buttons around a tabular readout. `format` renders the
// value (e.g. as a percentage); steps clamp to [min, max] and round away
// float drift. Click the readout to jump back to `reset` when given.
export function Stepper({ value, onChange, min, max, step, format, reset }) {
  const clamp = (n) => Math.round(Math.min(max, Math.max(min, n)) * 1000) / 1000;
  return (
    <span className="stepper">
      <button type="button" className="uiBtn sm iconSq" aria-label={t("Smaller")}
        disabled={value <= min} onClick={() => onChange(clamp(value - step))}>−</button>
      <button type="button" className="stepperValue" disabled={reset == null || value === reset}
        title={reset != null ? t("Reset to default") : undefined}
        onClick={() => reset != null && onChange(reset)}>{format ? format(value) : value}</button>
      <button type="button" className="uiBtn sm iconSq" aria-label={t("Larger")}
        disabled={value >= max} onClick={() => onChange(clamp(value + step))}>+</button>
      {reset != null ? <button type="button" className="uiBtn sm" disabled={value === reset}
        onClick={() => onChange(reset)}>{t("Reset")}</button> : null}
    </span>
  );
}

// Character budgets span 100 … 1 000 000, so the slider is log-scaled and snaps
// to round numbers; the box next to it still accepts any exact value. The max
// matches the backend's request-model ceiling (READ_CHARS_MAX in
// gamma/ai_tools.py) — keep the two in sync.
const SLIDER_MIN = 100, SLIDER_MAX = 1000000, SLIDER_SPAN = Math.log(SLIDER_MAX / SLIDER_MIN);
const toSlider = (v) => Math.round((1000 * Math.log(Math.max(SLIDER_MIN, v) / SLIDER_MIN)) / SLIDER_SPAN);
const fromSlider = (s) => {
  const raw = SLIDER_MIN * Math.exp((s / 1000) * SLIDER_SPAN);
  const step = raw < 10000 ? 100 : raw < 100000 ? 1000 : 10000;
  return Math.min(SLIDER_MAX, Math.max(SLIDER_MIN, Math.round(raw / step) * step));
};

// The number box commits on blur/Enter, not per keystroke — the range clamp
// must not fight half-typed values (typing "20000" would snap to 100 at "2").
// The slider stays live; grabbing it blurs the box, committing any draft first.
export function CharSlider({ value, onChange }) {
  const [draft, setDraft] = React.useState(null); // non-null only while the box is being edited
  const commit = () => {
    if (draft == null) return;
    const next = Number.parseInt(draft, 10);
    if (Number.isFinite(next)) onChange(Math.min(SLIDER_MAX, Math.max(SLIDER_MIN, next)));
    setDraft(null);
  };
  return (
    <span className="setSlider">
      <input
        type="range" min="0" max="1000" step="1" className="setRange"
        value={toSlider(value)}
        onChange={(event) => onChange(fromSlider(Number(event.target.value)))}
      />
      <input
        className="aiKeyInput setNum" type="number" min={SLIDER_MIN} max={SLIDER_MAX} step="1000"
        value={draft ?? value}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
      />
    </span>
  );
}

// ~1800 characters is about one dense page of a paper — enough to make an
// abstract character budget mean something.
export const approxPages = (chars) => `≈ ${Math.max(1, Math.round(chars / 1800))} page${chars >= 2700 ? "s" : ""}`;

// Coverage tile: big count, what it counts, and how far along it is.
export function Stat({ icon: Icon, label, value, total, title }) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  const tone = pct >= 100 ? "ok" : pct < 60 ? "warn" : "";
  return (
    <div className="setStat" title={title}>
      <span className="setStatTop">
        <span className="setStatNum">{value}</span>
        <span className="setStatOf">/ {total}</span>
      </span>
      <span className="setStatLabel"><Icon size={12} />{label}</span>
      <span className="setStatBar"><i className={tone} style={{ width: `${Math.max(pct, 2)}%` }} /></span>
    </div>
  );
}

// A text-valued tile beside Stat's numeric ones: a version, an uptime, a
// pair of counts. `tone` "warn" / "error" colours the frame.
export function StatText({ icon: Icon, label, value, hint, tone = "", title }) {
  return (
    <div className={`setStat setStatText ${tone}`} title={title}>
      <span className="setStatTop"><span className="setStatNum">{value}</span></span>
      <span className="setStatLabel"><Icon size={12} />{label}</span>
      {hint ? <span className="setStatHint">{hint}</span> : null}
    </div>
  );
}

// Newest-first log list with a Copy button — one rendering for the session
// log (Advanced) and the admin server log (Server). Entries are normalized
// to {key, timeMs, text, tone?} — tone "warn" / "error" adds a badge.
// `extra` sits before the Copy button (a level filter).
export function LogBox({ icon, label, description, entries, emptyText, copyStatus, setStatus, extra }) {
  const prefix = (entry) => (entry.tone ? `[${entry.tone === "error" ? "ERROR" : "WARNING"}] ` : "");
  function copy() {
    const text = entries
      .map((entry) => `${new Date(entry.timeMs).toLocaleTimeString([], { hour12: false })} ${prefix(entry)}${entry.text}`)
      .join("\n");
    copyText(text).then((ok) => setStatus(ok ? copyStatus : t("Copy failed—copy manually.")));
  }
  return (
    <>
      <Row icon={icon} label={label} hint={description}>
        <span className="setRowControls">
          {extra}
          <button className="uiBtn sm" disabled={!entries.length} onClick={copy}>{t("Copy")}</button>
        </span>
      </Row>
      <div className="sysLogBox">
        {entries.length ? [...entries].reverse().map((entry) => (
          <div key={entry.key} className="sysLogRow">
            <span className="sysLogTime">{new Date(entry.timeMs).toLocaleTimeString([], { hour12: false })}</span>
            {entry.tone ? <span className={`sysLogLevel ${entry.tone}`}>{entry.tone === "error" ? "ERR" : "WARN"}</span> : null}
            <span className="sysLogMsg">{entry.text}</span>
          </div>
        )) : <div className="sysLogEmpty">{emptyText}</div>}
      </div>
    </>
  );
}

export function Empty({ icon: Icon, children }) {
  return <div className="setEmpty"><Icon size={26} />{children}</div>;
}

// Notion-style people picker: a search box over the account directory with
// the matches listed beneath as selectable rows (avatar · name · admin tag).
// `accounts` is the directory (null while loading), `exclude` the usernames
// already in (members, the owner); `value` is the picked username and
// `onChange` receives it (or "" again when the text no longer names one).
// Typing filters; Enter picks the first match; ↑/↓ move the highlight.
// `compact` keeps the list closed until the box is focused or has text —
// for a popover, where an always-open list would crowd the rest.
// An empty directory is a hidden one (a share host lists no accounts, only
// the one named exactly): the typed name is then looked up as
// `GET /api/accounts?q=`.
export function AccountPicker({ accounts, exclude = [], value, onChange, placeholder, autoFocus, compact }) {
  const [query, setQuery] = React.useState(value || "");
  const [cursor, setCursor] = React.useState(0);
  const [focused, setFocused] = React.useState(false);
  const [found, setFound] = React.useState([]);
  const hidden = Array.isArray(accounts) && accounts.length === 0;
  const typed = query.trim();
  React.useEffect(() => {
    if (!hidden || !typed) { setFound([]); return undefined; }
    let live = true;
    const timer = setTimeout(() => {
      apiJson(`${API}/accounts?q=${encodeURIComponent(typed)}`)
        .then((d) => { if (live) setFound(d.accounts || []); })
        .catch(() => { if (live) setFound([]); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [hidden, typed]);
  const directory = hidden ? found : accounts;
  const skip = new Set(exclude);
  const q = query.trim().toLowerCase();
  const matches = (directory || []).filter((a) => !skip.has(a.username) && (!q || a.username.toLowerCase().includes(q)));
  const shown = matches.slice(0, 8);
  const open = !compact || focused || !!q;
  // a looked-up name arrives after the typing: spelled exactly, it is picked
  React.useEffect(() => {
    const exact = hidden && found.find((a) => a.username === typed && !skip.has(a.username));
    if (exact && exact.username !== value) onChange(exact.username);
  }, [found]); // eslint-disable-line react-hooks/exhaustive-deps

  function pick(name) {
    setQuery(name);
    onChange(name);
    setCursor(0);
  }
  function type(text) {
    setQuery(text);
    setCursor(0);
    // the text may spell an account exactly — that counts as picking it
    const exact = matches.find((a) => a.username === text.trim());
    onChange(exact ? exact.username : "");
  }
  function onKeyDown(event) {
    if (event.key === "ArrowDown") { event.preventDefault(); setCursor((c) => Math.min(c + 1, shown.length - 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
    else if (event.key === "Enter" && shown[cursor] && shown[cursor].username !== value) { event.preventDefault(); pick(shown[cursor].username); }
  }

  return (
    <span className="setPick">
      <input
        className="aiKeyInput" type="text" spellCheck={false} autoComplete="off" autoFocus={autoFocus}
        placeholder={placeholder || t("Search accounts…")} value={query}
        onChange={(event) => type(event.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 120)} // let a click on a row land first
        aria-label={placeholder || t("Search accounts")}
      />
      {open ? (
        <span className="setPickList" role="listbox">
          {accounts == null ? <span className="setPickEmpty">{t("Loading accounts…")}</span> : null}
          {accounts != null && !shown.length ? (
            <span className="setPickEmpty">{q ? t("No account matches \"{query}\"", { query: query.trim() }) : hidden ? t("Type an exact username") : t("No other accounts")}</span>
          ) : null}
          {shown.map((a, i) => (
            <button
              key={a.username} type="button" role="option" aria-selected={a.username === value}
              className={`setPickItem ${a.username === value ? "picked" : ""} ${i === cursor ? "cursor" : ""}`}
              onMouseDown={(event) => event.preventDefault()} // keep the box focused
              onClick={() => pick(a.username)}
              onMouseEnter={() => setCursor(i)}
            >
              <span className="setPickAvatar">{a.is_admin ? <ShieldIcon size={13} /> : <UserIcon size={13} />}</span>
              <span className="setPickName">{a.username}</span>
              {a.is_admin ? <span className="uiTag admin">{t("admin")}</span> : null}
              {a.username === value ? <CheckIcon size={13} className="setPickCheck" /> : null}
            </button>
          ))}
          {matches.length > shown.length ? (
            <span className="setPickEmpty">{t("{n} more — keep typing", { n: matches.length - shown.length })}</span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}

// Cloud-drive-style storage meter: thin bar + "used of total" caption.
// quotaMb 0/undefined = unlimited → caption only, no bar (no denominator).
// barOnly renders just the bar (the account popover puts the numbers next to
// the user card instead). Shared by the popover, Users pane, Library.
export function QuotaMeter({ usedBytes, quotaMb, barOnly }) {
  if (usedBytes == null) return null;
  const quotaBytes = (quotaMb || 0) * 1024 * 1024;
  const pct = quotaBytes ? Math.min(100, (usedBytes / quotaBytes) * 100) : 0;
  const state = pct >= 95 ? " full" : pct >= 80 ? " warn" : "";
  return (
    <span className="quotaMeter">
      {quotaBytes ? (
        <span className="quotaBar">
          <span className={`quotaBarFill${state}`} style={{ width: `${usedBytes ? Math.max(pct, 2) : 0}%` }} />
        </span>
      ) : null}
      {barOnly ? null : (
        <span className="settingDesc">
          {quotaBytes
            ? t("{used} of {quota} used ({pct}%)", { used: fmtBytes(usedBytes), quota: fmtBytes(quotaBytes), pct: Math.round(pct) })
            : t("{used} used — no quota", { used: fmtBytes(usedBytes) })}
        </span>
      )}
    </span>
  );
}

// Percentage-only variant of QuotaMeter. It deliberately shares the exact
// quotaMeter/quotaBar markup and warning thresholds so provider allowance and
// storage quota read as the same kind of capacity indicator.
export function PercentMeter({ percent, barOnly, caption = "" }) {
  const value = Number(percent);
  if (!Number.isFinite(value)) return null;
  const pct = Math.max(0, Math.min(100, value));
  const state = pct >= 95 ? " full" : pct >= 80 ? " warn" : "";
  return (
    <span className="quotaMeter">
      <span className="quotaBar">
        <span className={`quotaBarFill${state}`} style={{ width: `${pct ? Math.max(pct, 2) : 0}%` }} />
      </span>
      {barOnly ? null : <span className="settingDesc">{caption || `${Math.round(pct)}% used`}</span>}
    </span>
  );
}

// What the account has spent through the server's shared AI connections in
// the last 24 hours (the `allowance` of GET /api/ai/models, docs/dev/guests.md):
// the storage meter's bar under the admin's limit, the plain count without
// one. Null when no shared connection applies.
export function AllowanceMeter({ allowance }) {
  if (!allowance) return null;
  const used = Number(allowance.used) || 0;
  const limit = Number(allowance.limit) || 0;
  if (!limit) {
    return (
      <span className="quotaMeter">
        <span className="settingDesc">{t("Server AI: {used} tokens in the last 24 h", { used: fmtTokens(used) })}</span>
      </span>
    );
  }
  return <PercentMeter percent={(used / limit) * 100}
    caption={allowance.exhausted
      ? t("Server AI used up: {used} of {limit} tokens in the last 24 h", { used: fmtTokens(used), limit: fmtTokens(limit) })
      : t("Server AI: {used} of {limit} tokens in the last 24 h", { used: fmtTokens(used), limit: fmtTokens(limit) })} />;
}

// The keys of a chord as <kbd> caps: "Ctrl" "Shift" "K", or ⇧⌘K on a Mac.
export function KeyCaps({ chord }) {
  return <span className="keyCaps">{chordParts(chord).map((part, i) => <kbd key={i} className="keyCap">{part}</kbd>)}</span>;
}

// One shortcut, VSCode-style: the chord as key caps; click, then press the
// new chord (recorded with the same reader the dispatcher matches with).
// Backspace or Delete alone unbinds, Escape cancels, a bare letter is
// refused (it would replace typing — add a modifier). `modified` shows the
// reset button; `fixed` shows the chord read-only. `conflict` colours the
// caps: another command answers to the same keys.
export function KeyBinding({ chord, label, fixed, modified, conflict, onChange, onReset }) {
  const [recording, setRecording] = React.useState(false);
  const [refused, setRefused] = React.useState(false);
  const stop = () => { setRecording(false); setRefused(false); };
  const onKeyDown = (event) => {
    if (!recording) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Escape") { stop(); return; }
    if ((event.key === "Backspace" || event.key === "Delete") && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey) {
      onChange(null); stop(); return;
    }
    const next = chordFromEvent(event.nativeEvent || event);
    if (!next) return; // a modifier alone: keep waiting
    if (!bindable(next)) { setRefused(true); return; }
    onChange(next);
    stop();
  };
  const state = recording ? (refused ? t("Add a modifier…") : t("Press keys…")) : null;
  const cls = ["uiBtn", "sm", "keyChip", recording ? "recording" : "", !chord && !recording ? "unbound" : "", conflict ? "conflict" : ""]
    .filter(Boolean).join(" ");
  return (
    <span className="keyBinding">
      <button
        type="button"
        className={cls}
        aria-label={fixed ? label : t("Change the shortcut for {name}", { name: label })}
        title={fixed ? t("Built in — cannot be changed") : conflict ? t("Another command uses these keys") : t("Click, then press the new keys · Backspace unbinds · Esc cancels")}
        disabled={fixed}
        onClick={() => { if (!recording) setRecording(true); }}
        onKeyDown={onKeyDown}
        onBlur={stop}
      >
        {state ? <span className="keyState">{state}</span> : chord ? <KeyCaps chord={chord} /> : <span className="keyState">{t("Not bound")}</span>}
      </button>
      {modified && !fixed ? (
        <button type="button" className="uiBtn sm iconSq" title={t("Reset to default")} aria-label={t("Reset {name} to its default shortcut", { name: label })} onClick={onReset}>
          <UndoIcon size={13} />
        </button>
      ) : null}
    </span>
  );
}
