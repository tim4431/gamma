// Settings → AI → Usage's chart: a bar per UTC day (the latest 30) or per
// month (the latest 12), each stacked by what its calls were billed as, in
// tokens, calls or estimated cost (pay-per-token calls alone). Arrow keys
// walk the bars; the detail under the plot spells the selected one out.
import React from "react";
import { getLocale, t, tn } from "../shared/i18n/i18n.js";
import { billingLabel, fmtCost, fmtTokens } from "../chat/tokenUsage";
import { MenuSelect } from "../shared/ui/Menus";
import { Segmented } from "./SettingsKit";
import { usageChartData } from "./usageChartData.js";

// The axis' figures: tokens and calls compact, dollars to two figures.
const axisLabel = (metric, value) => (metric === "cost" ? `$${Number(value.toPrecision(2))}` : fmtTokens(value));
const metricValue = (metric, value) => (metric === "cost" ? fmtCost(value) : metric === "calls" ? String(value) : fmtTokens(value));

export function UsageChart({ daily = [], interval = "daily", onInterval, firstAt, keepDays, onReset, canReset = false }) {
  const [metric, setMetric] = React.useState("tokens");
  const [selected, setSelected] = React.useState("");
  const plot = React.useRef(null);
  const { bars, ceiling } = usageChartData(daily, interval, metric);
  const current = bars.find((bar) => bar.date === selected) || bars.at(-1);
  // The kinds the bars shown hold, for the legend (one kind needs none).
  const kinds = [...new Set(bars.flatMap((bar) => bar.segments.map((segment) => segment.kind)))];
  const dateLabel = (date, options) => new Date(`${date.slice(0, 10)}T00:00:00Z`).toLocaleDateString(getLocale(), { timeZone: "UTC", ...options });
  const label = (date) => dateLabel(date, interval === "monthly" ? { month: "long", year: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
  const detail = (bar) => [
    tn("{n} call", "{n} calls", bar.calls),
    t("{tokens} tokens", { tokens: fmtTokens((bar.input || 0) + (bar.output || 0)) }),
    ...(bar.cost ? [t("est. {cost}", { cost: fmtCost(bar.cost) })] : []),
  ].join(" · ");

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
      <div className="usageChartHead">
        <MenuSelect label={t("Chart interval")} value={interval} onChange={onInterval}
          options={[["daily", t("Daily · last 30 days")], ["monthly", t("Monthly · last 12 months")]]} />
        <Segmented value={metric} onChange={setMetric} options={[
          ["cost", t("Cost"), null, t("Estimated cost of the pay-per-token calls, at public prices")],
          ["tokens", t("Tokens")], ["calls", t("Calls")]]} />
      </div>
      {kinds.length > 1 ? (
        <div className="usageChartLegend">
          {kinds.map((kind) => <span key={kind}><i className="usageChartSwatch" data-kind={kind} aria-hidden="true" />{billingLabel(kind)}</span>)}
        </div>
      ) : null}
      {current ? <>
        <div className="usageChartPlot" ref={plot}>
          <div className="usageChartAxis" aria-hidden="true">
            <span>{axisLabel(metric, ceiling)}</span><span>{axisLabel(metric, ceiling / 2)}</span><span>0</span>
          </div>
          <div className="usageChartBars" role="group" aria-label={t("AI usage chart; use arrow keys to explore")}>
            <div className="usageChartGrid" aria-hidden="true"><i /><i /><i /></div>
            {bars.map((bar) => (
              <button type="button" key={bar.date} className="usageChartBar" data-date={bar.date} data-value={bar.value}
                aria-label={`${label(bar.date)}: ${detail(bar)}`} aria-pressed={current.date === bar.date}
                tabIndex={current.date === bar.date ? 0 : -1} title={`${label(bar.date)}\n${detail(bar)}`}
                onKeyDown={(event) => move(event, bar.date)} onClick={() => setSelected(bar.date)}>
                <span className="usageChartFill" style={{ height: `${bar.value / ceiling * 100}%` }}>
                  {bar.segments.map((segment) => <span key={segment.kind} className="usageChartSeg" data-kind={segment.kind} style={{ flexGrow: segment.value }} />)}
                </span>
              </button>
            ))}
          </div>
          <div className="usageChartDates" aria-hidden="true">{[0, Math.floor((bars.length - 1) / 2), bars.length - 1].map((index, key) => <span key={key}>{dateLabel(bars[index].date, interval === "monthly" ? { month: "short", year: "2-digit" } : { month: "short", day: "numeric" })}</span>)}</div>
        </div>
        <div className="usageChartDetail" aria-live="polite" aria-atomic="true">
          <strong>{label(current.date)}</strong>
          <span>{current.calls ? detail(current) : t("No AI calls in this period")}</span>
          {current.segments.length > 1 ? (
            <span className="usageChartLegend">
              {current.segments.map((segment) => (
                <span key={segment.kind}><i className="usageChartSwatch" data-kind={segment.kind} aria-hidden="true" />
                  {billingLabel(segment.kind)} {metricValue(metric, segment.value)}</span>
              ))}
            </span>
          ) : null}
        </div>
      </> : <p className="setNotice">{t("No AI calls recorded yet — counts appear once a provider reports them.")}</p>}
      <div className="usageChartFooter" title={t("Prompt tokens in, reply tokens out, as each provider reported them. Rows older than {keep_days} days are dropped.", { keep_days: keepDays })}>
        <span>{firstAt
          ? t("Daily totals in UTC · since {date}", { date: dateLabel(firstAt, { month: "short", day: "numeric", year: "numeric" }) })
          : t("Daily totals in UTC")}</span>
        {onReset ? <button className="uiBtn sm danger" disabled={!canReset} onClick={onReset}>{t("Reset")}</button> : null}
      </div>
    </div>
  );
}
