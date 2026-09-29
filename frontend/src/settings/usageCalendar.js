// The API supplies 365 consecutive UTC dates. Pad to Sunday-first weeks;
// padding is not a zero-usage day and cannot be selected.
export function usageCalendar(daily, metric = "tokens") {
  const value = (day) => metric === "calls" ? day.calls : day.input + day.output;
  const max = Math.max(1, ...daily.map(value));
  const offset = daily.length ? new Date(`${daily[0].date}T00:00:00Z`).getUTCDay() : 0;
  const cells = Array(offset).fill(null);
  daily.forEach((day) => cells.push({ ...day, level: value(day) ? Math.min(4, Math.ceil(value(day) / max * 4)) : 0 }));
  while (cells.length % 7) cells.push(null);
  const weeks = Array.from({ length: cells.length / 7 }, (_, i) => cells.slice(i * 7, i * 7 + 7));
  const months = [];
  let previous = "";
  weeks.forEach((week, column) => {
    const day = week.find(Boolean);
    const month = day.date.slice(0, 7);
    if (month !== previous) months.push({ date: day.date, column });
    previous = month;
  });
  // A partial first month can be only one column wide: omit its label.
  if (months.length > 1 && months[1].column < 3) months.shift();
  return { weeks, months, activeDays: daily.filter((day) => day.calls > 0).length,
    tokens: daily.reduce((sum, day) => sum + day.input + day.output, 0),
    calls: daily.reduce((sum, day) => sum + day.calls, 0) };
}
