// Settings → AI → Usage: the account's AI calls as the providers reported
// them (GET /api/ai/usage, gamma/ai_usage.py), for a period, read through
// the dialog's store (settingsReads.js usageOf). Tiles first:
// the estimated spend of the pay-per-token calls, the tokens that went
// through subscriptions with what is left of each plan, the shared
// connections' tokens against their allowance, and the totals. Then the
// chart (UsageChart.jsx) with Reset, and the period split by model and by
// task. Subscriptions and shared use are never shown in dollars: nobody
// pays per token for them.
import React from "react";
import { API, apiJson } from "../shared/lib/utils";
import { billingKind, billingLabel, cachedPercent, fmtCost, fmtTokens, usageDetail } from "../chat/tokenUsage";
import { UsageChart } from "./UsageChart.jsx";
import { ProviderUsage } from "./SettingsAi.jsx";
import { PaneHead, Section, Row, Segmented, StatText, ToggleGroup } from "./SettingsKit";
import { usageOf, useSettingsRead } from "./settingsReads.js";
import { ActivityIcon, ChevronDownIcon, ChevronUpIcon, KeyIcon, RefreshIcon, SparklesIcon, UsersIcon, ZapIcon } from "../shared/ui/Icons";
import { T, t, tn } from "../shared/i18n/i18n.js";

const USAGE_KIND_LABELS = { chat: t("Chat"), translate: t("Translation"), metadata: t("Metadata"), cite: t("Citations"), ink: t("Handwriting"),
  test: t("Connection tests") };

// The periods the pane offers (the API's `period`), and the chart's
// interval each one starts it on.
const PERIODS = [["week", t("7 days")], ["month", t("30 days")], ["year", t("12 months")], ["all", t("All")]];
const PERIOD_INTERVAL = { week: "daily", month: "daily", year: "monthly", all: "monthly" };

const tokensOf = (counts) => (counts?.input || 0) + (counts?.output || 0);

// The shared allowance (GET /api/ai/usage's `allowance`, present while a
// shared entry applies): what the account spent on the server's keys in the
// rolling 24 hours, against the admin's limit when there is one.
function AllowanceRow({ allowance }) {
  const { used = 0, limit = 0, exhausted = false } = allowance;
  return (
    <Row icon={KeyIcon} label={t("Shared allowance")}
      hint={!limit
        ? t("{used} tokens in the last 24 h · no limit", { used: fmtTokens(used) })
        : exhausted
          ? t("Used up: {used} of {limit} tokens in the last 24 h", { used: fmtTokens(used), limit: fmtTokens(limit) })
          : t("{used} of {limit} tokens in the last 24 h", { used: fmtTokens(used), limit: fmtTokens(limit) })}
      title={t("The server's shared AI keys allow each account this many tokens in any 24 hours. Your own keys are not counted; add one under Models to keep going.")}>
      {exhausted ? <span className="uiTag failed">{t("used up")}</span> : null}
    </Row>
  );
}

// A counts object's estimated cost for a table cell: the known sum, "—"
// when none of its pay-per-token calls had a price, the billing tag for a
// row nobody pays per token for.
function CostCell({ counts, billing }) {
  if (billing && billing !== "api") return <span role="cell"><span className="uiTag">{billingLabel(billing)}</span></span>;
  if (!counts.priced_calls) {
    return <span role="cell" title={counts.unpriced_calls ? t("No public price for this model") : undefined}>—</span>;
  }
  return (
    <span role="cell" title={counts.unpriced_calls ? tn("{n} call without a price is not counted", "{n} calls without a price are not counted", counts.unpriced_calls) : undefined}>
      {fmtCost(counts.cost)}{counts.unpriced_calls ? "+" : ""}
    </span>
  );
}

// The by-model table's columns: [key, label, how a row sorts by it].
const MODEL_COLUMNS = [
  ["model", t("Model"), (m) => `${m.model} ${m.provider_name || ""}`.toLowerCase()],
  ["calls", t("calls"), (m) => m.calls || 0],
  ["input", t("in"), (m) => m.input || 0],
  ["output", t("out"), (m) => m.output || 0],
  ["cached", t("cached"), (m) => cachedPercent(m)],
  ["cost", t("est. cost"), (m) => (billingKind(m.billing) === "api" && m.priced_calls ? m.cost || 0 : -1)],
];

// `totals` the Total row's counts (the task's, while the table is filtered
// to one), none for no Total row.
function ModelTable({ models, totals }) {
  // Server order (most tokens first) until a header is clicked.
  const [sort, setSort] = React.useState({ key: "", dir: -1 });
  const column = MODEL_COLUMNS.find(([key]) => key === sort.key);
  const rows = column ? [...models].sort((a, b) => {
    const x = column[2](a);
    const y = column[2](b);
    return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
  }) : models;
  const by = (key) => setSort((now) => ({ key, dir: now.key === key ? -now.dir : key === "model" ? 1 : -1 }));
  return (
    <div className="aiUsageTable" role="table" aria-label={t("Usage by model")}>
      <div className="aiUsageRow aiUsageHeader" role="row">
        {MODEL_COLUMNS.map(([key, label]) => (
          <span key={key} role="columnheader" aria-sort={sort.key === key ? (sort.dir > 0 ? "ascending" : "descending") : "none"}>
            <button type="button" className="aiUsageSort" title={t("Sort by {column}", { column: label })} onClick={() => by(key)}>
              {label}
              {sort.key === key ? (sort.dir > 0 ? <ChevronUpIcon size={14} /> : <ChevronDownIcon size={14} />) : null}
            </button>
          </span>
        ))}
      </div>
      {rows.map((m) => (
        <div className="aiUsageRow" role="row" key={`${m.provider_id}:${m.model}:${m.billing || ""}`} title={usageDetail(m)}>
          <span className="aiUsageModel" role="cell">{m.model}{m.provider_name ? <em> · {m.provider_name}</em> : null}</span>
          <span role="cell">{m.calls}</span>
          <span role="cell">{fmtTokens(m.input)}</span>
          <span role="cell">{fmtTokens(m.output)}</span>
          <span role="cell">{cachedPercent(m) ? `${cachedPercent(m)}%` : "—"}</span>
          <CostCell counts={m} billing={billingKind(m.billing)} />
        </div>
      ))}
      {totals ? (
        <div className="aiUsageRow aiUsageKind" role="row" title={usageDetail(totals)}>
          <span className="aiUsageModel" role="cell">{t("Total")}</span>
          <span role="cell">{totals.calls}</span>
          <span role="cell">{fmtTokens(totals.input)}</span>
          <span role="cell">{fmtTokens(totals.output)}</span>
          <span role="cell">{cachedPercent(totals) ? `${cachedPercent(totals)}%` : "—"}</span>
          <CostCell counts={totals} />
        </div>
      ) : null}
    </div>
  );
}

function KindTable({ kinds }) {
  return (
    <div className="aiUsageTable" role="table" aria-label={t("Usage by task")}>
      <div className="aiUsageRow aiUsageHeader" role="row">
        <span role="columnheader">{t("Task")}</span><span role="columnheader">{t("calls")}</span>
        <span role="columnheader">{t("in")}</span><span role="columnheader">{t("out")}</span>
        <span role="columnheader">{t("cached")}</span><span role="columnheader">{t("est. cost")}</span>
      </div>
      {kinds.map(([kind, u]) => (
        <div className="aiUsageRow" role="row" key={kind} title={usageDetail(u)}>
          <span className="aiUsageModel" role="cell">{USAGE_KIND_LABELS[kind] || kind}</span>
          <span role="cell">{u.calls}</span>
          <span role="cell">{fmtTokens(u.input)}</span>
          <span role="cell">{fmtTokens(u.output)}</span>
          <span role="cell">{cachedPercent(u) ? `${cachedPercent(u)}%` : "—"}</span>
          <CostCell counts={u} />
        </div>
      ))}
    </div>
  );
}

// The pane. `value` is App's AI group (the masked connection list and the
// quota each subscription reported, aiKeyUsage, which App reads as any AI
// pane opens). Guests see their usage too (the shared allowance), without
// Reset.
export function AiUsagePane({ value, confirm, setStatus }) {
  const canReset = !!value?.aiKeysInfo?.can_edit;
  const [period, setPeriod] = React.useState("month");
  const [kind, setKind] = React.useState("");
  const [chartInterval, setChartInterval] = React.useState("daily");
  const read = useSettingsRead(usageOf(period, kind));
  // Another period or task keeps the last answer on screen until its own is
  // in, so the controls stay where they are.
  const shown = React.useRef(null);
  if (read.data) shown.current = read.data;
  const data = read.data || shown.current;
  const error = read.error;

  // The subscriptions that can say what is left of their plan.
  const plans = (value?.aiKeysInfo?.providers || []).filter((p) => p.billing === "subscription" && p.quota);
  const quota = value?.aiKeyUsage || {};

  function reset() {
    const run = async () => {
      try {
        await apiJson(`${API}/ai/usage`, { method: "DELETE" });
        setStatus?.(t("Token usage reset"));
        read.reload().catch(() => {});
      } catch (err) {
        setStatus?.(t("Couldn't reset token usage: {message}", { message: err.message }));
      }
    };
    if (confirm) {
      confirm({ title: T("Reset token usage"), message: T("Forget every recorded token count of this account? The providers' own dashboards are not affected."), confirmLabel: t("Reset"), danger: true, onConfirm: run });
    } else run();
  }

  const choosePeriod = (next) => { setPeriod(next); setChartInterval(PERIOD_INTERVAL[next]); };
  const kinds = Object.entries(data?.kinds || {}).sort((a, b) => tokensOf(b[1]) - tokensOf(a[1]));
  // The period's totals over every task (the task filter narrows the tables alone).
  const all = data?.totals;
  const spent = all?.billing || {};
  const unknown = spent[""]?.calls || 0; // calls recorded before their billing was
  const subscription = spent.subscription;
  const shared = spent.shared;
  return (
    <>
      <PaneHead icon={ActivityIcon} title={t("Usage")} action={
        <button className="uiBtn sm iconSq" title={t("Fetch the latest counts")} aria-label={t("Refresh")}
          onClick={() => read.reload().catch(() => {})}>
          <RefreshIcon size={14} />
        </button>} />
      <div className="aiUsagePeriod" role="group" aria-label={t("Period")}>
        <Segmented value={period} onChange={choosePeriod} options={PERIODS} />
      </div>
      {error ? <p className="settingsPaneHint aiKeysError" role="alert">{t("Usage unavailable: {error}", { error })}</p> : null}
      {!data && !error ? <p className="setNotice">{t("Loading…")}</p> : null}
      {data ? <>
        <div className="setStats fit">
          <StatText icon={ZapIcon} label={t("Est. API spend")}
            value={all.priced_calls ? fmtCost(all.cost) : "—"}
            hint={[
              all.unpriced_calls ? tn("{n} call without a price", "{n} calls without a price", all.unpriced_calls)
                : all.priced_calls ? t("At public prices") : t("No priced pay-per-token calls"),
              unknown ? tn("{n} earlier call has no price", "{n} earlier calls have no price", unknown) : "",
            ]}
            title={t("What the pay-per-token calls would cost at each model's public price on the day of the call. Your provider's bill is what counts; subscriptions and shared connections are not billed per token.")} />
          {subscription?.calls || plans.length ? (
            <StatText icon={SparklesIcon} label={t("Subscriptions")}
              value={t("{tokens} tokens", { tokens: fmtTokens(tokensOf(subscription)) })}
              hint={tn("{n} call", "{n} calls", subscription?.calls || 0)}
              title={t("Calls through a subscription come out of its plan, not paid per token.")}>
              {plans.map((p) => quota[p.id] ? (
                <span className="aiUsageTilePlan" key={p.id}>
                  <span className="setStatHint">{p.label || p.protocol}</span>
                  <ProviderUsage usage={quota[p.id]} />
                </span>
              ) : null)}
            </StatText>
          ) : null}
          {shared?.calls || data.allowance ? (
            <StatText icon={UsersIcon} label={t("Shared")}
              value={t("{tokens} tokens", { tokens: fmtTokens(tokensOf(shared)) })}
              hint={tn("{n} call", "{n} calls", shared?.calls || 0)}
              title={t("Calls through the connections this server shares. They count against your allowance; you don't pay for them.")} />
          ) : null}
          <StatText icon={ActivityIcon} label={t("Tokens")}
            value={fmtTokens(tokensOf(all))}
            hint={[
              t("{input} in · {output} out", { input: fmtTokens(all.input), output: fmtTokens(all.output) }),
              `${tn("{n} call", "{n} calls", all.calls)} · ${t("{percent}% cached", { percent: cachedPercent(all) })}`,
            ]}
            title={usageDetail(all)} />
        </div>
        {data.allowance ? <AllowanceRow allowance={data.allowance} /> : null}
        <UsageChart daily={data.daily} interval={chartInterval} onInterval={setChartInterval} firstAt={data.first_at}
          keepDays={data.keep_days} onReset={canReset ? reset : null} canReset={!!data.windows?.all?.calls} />
        {kinds.length || kind ? <>
          <Section title={t("By model")} />
          {kinds.length > 1 || kind ? (
            <ToggleGroup selected={[kind || "all"]} onToggle={(next) => setKind(next === "all" ? "" : next)}
              options={[["all", t("All tasks")], ...[...kinds.map(([name]) => name), ...(kind && !data.kinds?.[kind] ? [kind] : [])]
                .map((name) => [name, USAGE_KIND_LABELS[name] || name])]} />
          ) : null}
          {(data.models || []).length
            ? <ModelTable models={data.models} totals={kind ? data.kinds?.[kind] : null} />
            : <p className="setNotice">{t("No calls of this task in this period.")}</p>}
          <Section title={t("By task")} />
          <KindTable kinds={kinds} />
        </> : null}
      </> : null}
    </>
  );
}
