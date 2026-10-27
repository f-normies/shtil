/**
 * Клавиатура и фокус инспектора (спека #106, раздел 3.8). Чистые функции, без React.
 */

/**
 * Esc закрывает панель на странице, если его не забрал верхний слой: поповер отменяет
 * keydown, а модальный лист закрывается сам. Над листом инспектор — модальное окно,
 * его закрывает браузер.
 */
export function closesOnEscape(
  event: { key: string; defaultPrevented: boolean },
  modalOpen: boolean,
): boolean {
  return event.key === "Escape" && !event.defaultPrevented && !modalOpen;
}

/** Фокус возвращается на кнопку, открывшую инспектор, если она ещё на странице. */
export function returnFocus(trigger: { isConnected: boolean; focus: () => void } | null): void {
  if (trigger?.isConnected) {
    trigger.focus();
  }
}

/**
 * Кнопка внутри открытого модального листа (направление к психиатру): всё вне листа
 * неактивно, поэтому инспектор открывается модальным окном поверх листа.
 */
export function insideModal(trigger: { closest: (selector: string) => unknown } | null): boolean {
  return Boolean(trigger?.closest("dialog:modal"));
}

/** Отметка элемента, который получает фокус при открытии панели (поле вопроса, «‹ Ответ»). */
export const AUTOFOCUS = "[data-autofocus]";

type Candidate = { matches: (selector: string) => boolean; closest: (selector: string) => unknown };

/**
 * Куда поставить фокус в открытой панели: первый отмеченный `data-autofocus` элемент,
 * который доступен и виден. Нет такого — `null`: фокус получает сама панель, и чтение
 * с экрана называет её заголовок.
 */
export function initialFocus<T extends Candidate>(panel: {
  querySelectorAll: (selector: string) => ArrayLike<T>;
}): T | null {
  const found = Array.from(panel.querySelectorAll(AUTOFOCUS)).find(
    (el) => !el.matches(":disabled") && !el.closest("[hidden]"),
  );
  return found ?? null;
}

/**
 * Повторное открытие того же содержимого на той же странице (повторный клик «Спросить
 * по КР»): панель не пересоздаётся, ответ не стирается, только фокус переходит в неё.
 * Содержимое без `id` всегда открывается заново.
 */
export function reopens(
  shown: { id?: string; scope: string } | null,
  content: { id?: string },
  scope: string,
): boolean {
  return (
    shown !== null && content.id !== undefined && shown.id === content.id && shown.scope === scope
  );
}

/**
 * An inspector normally belongs to the exact route that opened it. A chat may opt into
 * the patient-card scope, which keeps it open while that card changes sections.
 */
export function closesOnScopeChange(
  shown: { scope: string; withinScope?: string } | null,
  nextScope: string,
): boolean {
  if (!shown || shown.scope === nextScope) return false;
  const root = shown.withinScope;
  return !root || (nextScope !== root && !nextScope.startsWith(`${root}/`));
}

/** Return the patient-card root using the router's current pathname, with or without basepath. */
export function patientCardScope(pathname: string, patientId: string): string {
  const suffix = `/patients/${patientId}`;
  const start = pathname.lastIndexOf(suffix);
  if (start < 0) return suffix;
  const end = start + suffix.length;
  const boundary = pathname[end];
  return boundary === undefined || boundary === "/" ? pathname.slice(0, end) : suffix;
}
