import React from "react";
import { getLocale, t, tn } from "../shared/i18n/i18n.js";
import { cachedPercent, fmtTokens, usageDetail } from "../chat/tokenUsage";
import { MenuSelect } from "../shared/ui/Menus";
import { usageChartData } from "./usageChartData.js";

export function UsageChart({ daily = [], total = {}, firstAt, keepDays, onReset, busy }) {
  const [metric, setMetric] = React.useState("tokens");
  const [interval, setInterval] = React.useState("daily");
  const [selected, setSelected] = React.useState("");
  const plot = React.useRef(null);
  const { bars, ceiling } = usageChartData(daily, interval, metric);
  const current = bars.find((bar) => bar.date === selected) || bars.at(-1);
  const dateLabel = (date, options) => new Date(`${date.slice(0, 10)}T00:00:00Z`).toLocaleDateString(getLocale(), { timeZone: "UTC", ...options });
  const label = (date) => dateLabel(date, interval === "monthly" ? { month: "long", year: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
  const detail = (bar) => `${tn("{n} call", "{n} calls", bar.calls)} · ${usageDetail(bar)}`;

  function move(event, date) {
    const step = { ArrowLeft: -1, ArrowRight: 1, Home: -bars.length, End: bars.length }[event.key];
    if (!step) return;
    event.preventDefault();
    const index = bars.findIndex((bar) => bar.date === date);
    const next = bars[Math.max(0, Math.min(bars.length - 1, index + step))];
    setSelected(next.date);
    plot.current.querySelector(`[data-date="${next.date}"]`)?.focus();
  }

  return (
    <div className="usageChart">
      <div className="usageChartTotal" title={t("Prompt tokens in, reply tokens out, as each provider reported them. Rows older than {keep_days} days are dropped. {all}", { keep_days: keepDays, all: usageDetail(total) })}>
        <span className="usageChartEyebrow">{t("Total usage")}</span>
        <div className="usageChartHeadline"><strong>{fmtTokens((total.input || 0) + (total.output || 0))}</strong><span>{t("tokens")}</span></div>
        <div className="usageChartTotals">
          <span>{t("{input} input", { input: fmtTokens(total.input) })}</span>
          <span>{t("{output} output", { output: fmtTokens(total.output) })}</span>
          <span>{tn("{n} call", "{n} calls", total.calls || 0)}</span>
          <span>{t("{percent}% cached", { percent: cachedPercent(total) })}</span>
        </div>
        {firstAt ? <div className="usageChartSince">{t("Since {date}", { date: dateLabel(firstAt, { month: "short", day: "numeric", year: "numeric" }) })}</div> : null}
      </div>
      <div className="usageChartHead">
        <MenuSelect label={t("Chart interval")} value={interval} onChange={setInterval}
          options={[["daily", t("Daily · last 30 days")], ["monthly", t("Monthly · last 12 months")]]} />
        <div className="usageChartToggle" role="group" aria-label={t("Activity measured by")}>
          <button type="button" aria-pressed={metric === "tokens"} onClick={() => setMetric("tokens")}>{t("Tokens")}</button>
          <button type="button" aria-pressed={metric === "calls"} onClick={() => setMetric("calls")}>{t("Calls")}</button>
        </div>
      </div>
      {current ? <>
        <div className="usageChartPlot" ref={plot}>
          <div className="usageChartAxis" aria-hidden="true"><span>{fmtTokens(ceiling)}</span><span>{fmtTokens(ceiling / 2)}</span><span>0</span></div>
          <div className="usageChartBars" role="group" aria-label={t("AI usage chart; use arrow keys to explore")}>
            <div className="usageChartGrid" aria-hidden="true"><i /><i /><i /></div>
            {bars.map((bar) => {
              const value = metric === "calls" ? bar.calls : bar.input + bar.output;
              return <button type="button" key={bar.date} className="usageChartBar" data-date={bar.date} data-value={value}
                aria-label={`${label(bar.date)}: ${detail(bar)}`} aria-pressed={current.date === bar.date}
                tabIndex={current.date === bar.date ? 0 : -1} title={`${label(bar.date)}\n${detail(bar)}`}
                onKeyDown={(event) => move(event, bar.date)} onClick={() => setSelected(bar.date)}>
                <span className="usageChartFill" style={{ height: `${value / ceiling * 100}%` }} />
              </button>;
            })}
          </div>
          <div className="usageChartDates" aria-hidden="true">{[0, Math.floor((bars.length - 1) / 2), bars.length - 1].map((index, key) => <span key={key}>{dateLabel(bars[index].date, interval === "monthly" ? { month: "short", year: "2-digit" } : { month: "short", day: "numeric" })}</span>)}</div>
        </div>
        <div className="usageChartDetail" aria-live="polite" aria-atomic="true">
          <strong>{label(current.date)}</strong>
          <span>{current.calls ? detail(current) : t("No AI calls in this period")}</span>
        </div>
      </> : <p className="setNotice">{t("No AI calls recorded yet — counts appear once a provider reports them.")}</p>}
      <div className="usageChartFooter">
        <span>{t("Daily totals in UTC")}</span>
        {onReset ? <button className="uiBtn sm danger" disabled={busy || !total.calls} onClick={onReset}>{t("Reset")}</button> : null}
      </div>
    </div>
  );
}
