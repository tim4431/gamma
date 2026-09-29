import React from "react";
import { getLocale, t, tn } from "../shared/i18n/i18n.js";
import { fmtTokens, usageDetail } from "../chat/tokenUsage";
import { usageCalendar } from "./usageCalendar.js";

export function UsageCalendar({ daily }) {
  const [metric, setMetric] = React.useState("tokens");
  const [selected, setSelected] = React.useState("");
  const scroll = React.useRef(null);
  const calendar = usageCalendar(daily, metric);
  const current = daily.find((day) => day.date === selected) || daily.at(-1);
  const detail = (day) => `${tn("{n} call", "{n} calls", day.calls)} · ${usageDetail(day)}`;
  const dateLabel = (date, options) => new Date(`${date}T00:00:00Z`).toLocaleDateString(getLocale(), { timeZone: "UTC", ...options });
  React.useEffect(() => { if (scroll.current) scroll.current.scrollLeft = scroll.current.scrollWidth; }, []);
  if (!current) return null;

  function move(event, date) {
    const step = { ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const index = daily.findIndex((day) => day.date === date);
    const next = daily[Math.max(0, Math.min(daily.length - 1, index + step))];
    setSelected(next.date);
    scroll.current.querySelector(`[data-date="${next.date}"]`)?.focus();
  }

  return (
    <div className="usageCalendar">
      <div className="usageCalendarHead">
        <div>
          <strong>{t("Activity over the last year")}</strong>
          <div className="usageCalendarSummary">{t("{tokens} tokens · {calls} calls", { tokens: fmtTokens(calendar.tokens), calls: calendar.calls.toLocaleString(getLocale()) })}</div>
        </div>
        <div className="usageCalendarToggle" role="group" aria-label={t("Activity measured by")}>
          <button type="button" aria-pressed={metric === "tokens"} onClick={() => setMetric("tokens")}>{t("Tokens")}</button>
          <button type="button" aria-pressed={metric === "calls"} onClick={() => setMetric("calls")}>{t("Calls")}</button>
        </div>
      </div>
      <div className="usageCalendarScroll" ref={scroll}>
        <div className="usageCalendarPlot" style={{ "--usage-weeks": calendar.weeks.length }}>
          <div className="usageCalendarMonths" aria-hidden="true">
            {calendar.months.map(({ date, column }) => <span key={date} style={{ gridColumn: column + 1 }}>{dateLabel(date, { month: "short" })}</span>)}
          </div>
          <div className="usageCalendarWeekdays" aria-hidden="true">
            {[1, 3, 5].map((day) => <span key={day} style={{ gridRow: day + 1 }}>{dateLabel(`2024-01-0${day}`, { weekday: "short" })}</span>)}
          </div>
          <div className="usageCalendarDays" role="group" aria-label={t("Daily AI usage; use arrow keys to explore")}>
            {calendar.weeks.flat().map((day, index) => day ? (
              <button type="button" key={day.date} className="usageCalendarCell" data-level={day.level} data-date={day.date}
                aria-label={`${dateLabel(day.date, { dateStyle: "full" })}: ${detail(day)}`}
                aria-pressed={current.date === day.date} tabIndex={current.date === day.date ? 0 : -1}
                title={`${dateLabel(day.date, { dateStyle: "full" })}\n${detail(day)}`}
                onKeyDown={(event) => move(event, day.date)} onClick={() => setSelected(day.date)} />
            ) : <span key={`pad-${index}`} />)}
          </div>
        </div>
      </div>
      <div className="usageCalendarLegend">
        <span>{tn("{n} active day", "{n} active days", calendar.activeDays)} · {t("Daily totals in UTC")}</span>
        <span className="usageCalendarScale">{t("Less")}{[0, 1, 2, 3, 4].map((level) => <span key={level} className="usageCalendarCell" data-level={level} aria-hidden="true" />)}{t("More")}</span>
      </div>
      <div className="usageCalendarDetail" aria-live="polite" aria-atomic="true">
        <strong>{dateLabel(current.date, { month: "short", day: "numeric", year: "numeric" })}</strong>
        <span>{current.calls ? detail(current) : t("No AI calls on this day")}</span>
      </div>
    </div>
  );
}
