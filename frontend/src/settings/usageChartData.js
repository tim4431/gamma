// Daily bars cover the latest 30 UTC days; monthly bars cover the latest
// 12 calendar months. Overall totals come separately from windows.all.
export function usageChartData(daily, interval = "daily", metric = "tokens") {
  let bars = daily.slice(-30);
  if (interval === "monthly") {
    const months = new Map();
    for (const day of daily) {
      const key = day.date.slice(0, 7);
      if (!months.has(key)) months.set(key, { date: `${key}-01`, calls: 0, input: 0, output: 0, cache_read: 0, cache_write: 0 });
      const month = months.get(key);
      for (const field of ["calls", "input", "output", "cache_read", "cache_write"]) month[field] += day[field] || 0;
    }
    bars = [...months.values()].slice(-12);
  }
  const values = bars.map((bar) => metric === "calls" ? bar.calls : bar.input + bar.output);
  const peak = Math.max(1, ...values);
  const magnitude = 10 ** Math.floor(Math.log10(peak));
  const ceiling = [1, 2, 5, 10].find((step) => step * magnitude >= peak) * magnitude;
  return { bars, ceiling };
}
