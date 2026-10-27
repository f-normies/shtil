function dayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

/** Date separator for the chat log: «Сегодня», «Вчера», then «16 октября» (with the year when it differs). */
export function chatDayLabel(createdAt: string, now: Date = new Date()): string {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return "";
  const key = dayKey(date);
  if (key === dayKey(now)) return "Сегодня";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (key === dayKey(yesterday)) return "Вчера";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}),
  })
    .format(date)
    .replace(/\s*г\.$/, "");
}
