/**
 * Даты в интерфейсе врача (кадры 02, 03, 05): по календарю браузера. Сегодня и вчера —
 * словом, раньше — «16.10», год только не текущий. Чистые функции, без React.
 */
const TIME = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });

/** «Сегодня, 09:14», «Вчера, 21:30», «16.10, 09:12», «30.12.2025, 18:20». */
export function formatDateTime(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  return `${dayWord(at, now) ?? dayText(at, now)}, ${TIME.format(at)}`;
}

/** То же внутри предложения: «сегодня, 09:14», «вчера, 21:30», «16.10, 09:12». */
export function formatMomentInline(iso: string, now: Date = new Date()): string {
  const text = formatDateTime(iso, now);
  return text.charAt(0).toLowerCase() + text.slice(1);
}

/** Время в строке списка: сегодня — «09:12», вчера — «Вчера», раньше — «16.10». */
export function formatListTime(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  const word = dayWord(at, now);
  if (word === "Сегодня") {
    return TIME.format(at);
  }
  return word ?? dayText(at, now);
}

/** День без времени: «16.10», «30.12.2025». Дата без времени из API («2026-09-22») — без сдвига пояса. */
export function formatDate(iso: string, now: Date = new Date()): string {
  const at = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T00:00:00`) : new Date(iso);
  return dayText(at, now);
}

function dayWord(at: Date, now: Date): "Сегодня" | "Вчера" | null {
  const day = localDay(at);
  if (day === localDay(now)) {
    return "Сегодня";
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  return day === localDay(yesterday) ? "Вчера" : null;
}

function dayText(at: Date, now: Date): string {
  const day = `${pad(at.getDate())}.${pad(at.getMonth() + 1)}`;
  return at.getFullYear() === now.getFullYear() ? day : `${day}.${at.getFullYear()}`;
}

function localDay(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}
