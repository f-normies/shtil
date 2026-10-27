/** Имя врача в меню кабинета (компонент «Врач v4/Меню»). */

function words(fullName: string): string[] {
  return fullName.trim().split(/\s+/).filter(Boolean);
}

/** «Соколова Ирина Петровна» → «Соколова И. П.». */
export function shortName(fullName: string): string {
  const [last = "", ...rest] = words(fullName);
  return [last, ...rest.map((w) => `${w.charAt(0)}.`)].join(" ");
}

/** Инициалы для аватара: имя, затем фамилия — «ИС». */
export function initials(fullName: string): string {
  const [last, first] = words(fullName);
  return `${first?.charAt(0) ?? ""}${last?.charAt(0) ?? ""}`.toUpperCase();
}
