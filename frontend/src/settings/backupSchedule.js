// Backup task schedules (Settings → Backups): the editor's presets and the
// stored five-field cron, which is always UTC (the server runs it so).
// Daily, weekly and hourly schedules are entered in the browser's own time
// zone and converted with its current offset — hour and weekday move
// together; monthly ones stay in UTC (a day-of-month cannot move across a
// month's end). Pure, so node tests it (tests/backupSchedule.test.mjs).

// A five-field expression the presets can express → {preset, minute, hour,
// weekdays, monthday}, or null for any other expression (edited as "custom").
export function parseCron(cron) {
  const [minute, hour, day, month, weekday] = String(cron || "").trim().split(/\s+/);
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

// Minutes east of UTC at `date` (PDT: -420).
export const localOffset = (date = new Date()) => -date.getTimezoneOffset();

// Whether this time zone moves its clocks during the year.
export function observesDst(year = new Date().getFullYear()) {
  return new Date(year, 0, 1).getTimezoneOffset() !== new Date(year, 6, 1).getTimezoneOffset();
}

// A clock time moved by `delta` minutes, with the day it carries into.
export function shiftTime(hour, minute, delta) {
  const total = hour * 60 + minute + delta;
  const days = Math.floor(total / 1440);
  const rest = total - days * 1440;
  return { hour: Math.floor(rest / 60), minute: rest % 60, days };
}

const mod = (n, m) => ((n % m) + m) % m;
const shiftDays = (weekdays, days) => [...new Set(weekdays.map((d) => mod(d + days, 7)))].sort((a, b) => a - b);

// The editor's schedule (local time; monthly already UTC) → the UTC cron.
export function toUtcCron({ preset, hour, minute, weekdays = [1], monthday = 1 }, offset) {
  if (preset === "hourly") return `${mod(minute - offset, 60)} * * * *`;
  if (preset === "monthly") return `${minute} ${hour} ${monthday} * *`;
  const utc = shiftTime(hour, minute, -offset);
  if (preset === "daily") return `${utc.minute} ${utc.hour} * * *`;
  return `${utc.minute} ${utc.hour} * * ${shiftDays(weekdays, utc.days).join(",")}`;
}

// A stored UTC cron → the editor's schedule in local time (monthly stays
// UTC), or null for an expression the presets cannot show.
export function fromUtcCron(cron, offset) {
  const parsed = parseCron(cron);
  if (!parsed || parsed.preset === "monthly") return parsed;
  if (parsed.preset === "hourly") return { ...parsed, minute: mod(parsed.minute + offset, 60) };
  const local = shiftTime(parsed.hour, parsed.minute, offset);
  const weekdays = parsed.preset === "weekly" ? shiftDays(parsed.weekdays, local.days) : parsed.weekdays;
  return { ...parsed, hour: local.hour, minute: local.minute, weekdays };
}
