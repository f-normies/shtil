/**
 * Терапия в карточке (спека #28, разделы 5 и 8): профиль пациента, текст схемы и признак
 * «вне инструкции». Чистые функции, без React. Правило вне инструкции повторяет серверное
 * (`therapy/checks.py`, `is_off_label`): врач видит пометку до отправки, решает сервер.
 */
import type { Schemas } from "@compass/api-client";

type LabelRegimen = Schemas["LabelRegimen"];
type Check = Schemas["CheckOut"];
type DrugLookup = Schemas["DrugLookupOut"];
type DrugMatch = Schemas["DrugMatchOut"];
type ReactionReport = Schemas["ReactionReportOut"];
type ReportedReaction = Schemas["ReportedReaction"];
export type ChosenRegimen = Schemas["RegimenIn"];

const SEX: Record<string, string> = { female: "женщина", male: "мужчина" };

/** Полных лет на дату (`YYYY-MM-DD`). Родившийся 29 февраля становится старше 1 марта. */
export function years(birth: string, today: string): number {
  const [by, bm, bd] = birth.split("-").map(Number) as [number, number, number];
  const [ty, tm, td] = today.split("-").map(Number) as [number, number, number];
  return ty - by - (tm < bm || (tm === bm && td < bd) ? 1 : 0);
}

/** «1 год», «2 года», «5 лет», «21 год», «112 лет». */
export function ageText(n: number): string {
  const tens = n % 100;
  const ones = n % 10;
  if (tens >= 11 && tens <= 14) {
    return `${n} лет`;
  }
  if (ones === 1) {
    return `${n} год`;
  }
  return ones >= 2 && ones <= 4 ? `${n} года` : `${n} лет`;
}

/** «34 года, женщина» для шапки; неизвестное пропускается, ничего не известно — `null`. */
export function profileText(
  patient: { birth_date?: string | null; sex?: string | null },
  today: string,
): string | null {
  const parts = [
    patient.birth_date ? ageText(years(patient.birth_date, today)) : null,
    patient.sex ? (SEX[patient.sex] ?? patient.sex) : null,
  ].filter((p): p is string => p !== null);
  return parts.length > 0 ? parts.join(", ") : null;
}

/** «1 раз», «2 раза», «5 раз». */
export function timesText(n: number): string {
  const ones = n % 10;
  const tens = n % 100;
  return ones >= 2 && ones <= 4 && !(tens >= 12 && tens <= 14) ? `${n} раза` : `${n} раз`;
}

export function regimenText(regimen: LabelRegimen): string {
  return (
    `по ${regimen.dose_mg} мг ${timesText(regimen.times_per_day)} в сутки, ${regimen.schedule}; ` +
    `длительность: ${regimen.duration_text}`
  );
}

export function isOffLabel(label: LabelRegimen, chosen: ChosenRegimen): boolean {
  if (chosen.dose_mg !== label.dose_mg || chosen.times_per_day !== label.times_per_day) {
    return true;
  }
  if (label.duration_days === null || label.duration_days === undefined) {
    return false;
  }
  const longest = label.max_days ?? label.duration_days;
  const days = chosen.duration_days;
  return days === null || days === undefined || days < label.duration_days || days > longest;
}

/**
 * Ошибка под полем дозы в «Изменить схему» (D3-10): доза — число больше нуля. Нативная
 * проверка `min` поле не подсвечивает и кнопку не блокирует, поэтому проверка явная.
 */
export function doseError(dose: number): string | null {
  if (!Number.isFinite(dose)) {
    return "Укажите дозу числом";
  }
  return dose > 0 ? null : "Доза должна быть больше нуля";
}

/** Форма «Причина отмены» видна только у действующего назначения (D3-2). */
export function stopFormOpen(status: Schemas["PrescriptionOut"]["status"], stopping: boolean) {
  return stopping && status === "active";
}

/** Элемент, который можно прокрутить в поле зрения и сфокусировать. */
export type Revealable = {
  scrollIntoView: (options: ScrollIntoViewOptions) => void;
  focus: (options: FocusOptions) => void;
};

/**
 * Показать готовое предложение назначения (M-5): плавная прокрутка к началу блока и фокус на
 * заголовке без второй прокрутки, чтобы и с клавиатуры было понятно, что появилось.
 */
export function revealOffer(el: Revealable): void {
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.focus({ preventScroll: true });
}

/**
 * Препарат отменён раньше, чем начат: сервер ответит 422 (спека #29). Даты — `YYYY-MM-DD`,
 * пустая дата не сравнивается.
 */
export function stopBeforeStart(
  startedOn: string | null | undefined,
  stoppedOn: string | null | undefined,
): boolean {
  return Boolean(startedOn && stoppedOn && stoppedOn < startedOn);
}

/** Сегодня по календарю браузера в виде `YYYY-MM-DD`. */
export function todayIso(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

const SAFETY = /^(contra|caution):/;

/**
 * Слово рядом с проверкой. У правил безопасности (спека #29) — уровень: противопоказание
 * «обязательно», мера предосторожности «желательно», нехватка данных «уточните».
 */
export function checkWord(check: Check): string {
  if (SAFETY.test(check.id)) {
    if (check.status === "fail") {
      return "обязательно";
    }
    if (check.status === "warn") {
      return "желательно";
    }
    if (check.status === "manual") {
      return "уточните";
    }
  }
  return {
    pass: "пройдена",
    fail: "не пройдена",
    warn: "желательно",
    manual: "проверьте сами",
  }[check.status];
}

/**
 * Уровень проверки, если слово рядом с ней — уровень сигнала: тогда и значок уровня
 * (спека #36, правила 1–3). Остальные статусы — свои значки и слова.
 */
export function checkLevel(check: Check): "mandatory" | "desirable" | null {
  const word = checkWord(check);
  return word === "обязательно" ? "mandatory" : word === "желательно" ? "desirable" : null;
}

/**
 * Сводка над проверками: найденные противопоказания или «не найдено».
 * Без оговорки — только когда уточнять нечего: неизвестная СКФ или препарат без МНН
 * противопоказание не исключают. Без правил — `null`.
 */
export function safetySummary(checks: readonly Check[]): string | null {
  const contra = checks.filter((c) => c.id.startsWith("contra:"));
  if (contra.length === 0) {
    return null;
  }
  const found = contra.filter((c) => c.status === "fail").map((c) => c.title);
  if (found.length > 0) {
    return `Противопоказания: ${found.join("; ")}`;
  }
  const open = checks.some(
    (c) => c.status === "manual" && (c.id.startsWith("contra:") || c.id.startsWith("unmatched:")),
  );
  return open
    ? "Противопоказаний по данным карты не найдено, уточните отмеченные пункты"
    : "Противопоказаний не найдено";
}

export type InnChoice = { inn: string; label: string };

/**
 * Варианты МНН для выбора врачом: все разные МНН точного названия или МНН похожих названий
 * (`inn_options`, сервер убрал повторы и не обрезал список). У похожих названий в подписи —
 * их торговые наименования. МНН известен или вариантов нет — выбирать нечего.
 */
export function innChoices(lookup: DrugLookup): InnChoice[] {
  if (lookup.check === "not_found" || lookup.inn) {
    return [];
  }
  return (lookup.inn_options ?? []).map((inn) => {
    const names = lookup.check === "similar" ? tradeNames(lookup.matches, inn) : ([] as string[]);
    return { inn, label: names.length > 0 ? `${names.join(", ")} — ${inn}` : inn };
  });
}

function tradeNames(matches: readonly DrugMatch[], inn: string): string[] {
  const key = inn.trim().toLowerCase();
  const names = matches
    .filter((m) => m.inn.trim().toLowerCase() === key && m.trade_name)
    .map((m) => m.trade_name as string);
  return [...new Set(names)];
}

/** Отметки нежелательных реакций одного назначения в порядке сервера (старые первыми). */
export function reportsOf(
  reports: readonly ReactionReport[] | undefined,
  prescriptionId: string,
): ReactionReport[] {
  return (reports ?? []).filter((r) => r.prescription_id === prescriptionId);
}

/** Уровень отметки — наибольший среди её реакций, как у сигнала (спека #29, раздел 6). */
export function reportLevel(report: ReactionReport): "mandatory" | "desirable" {
  return report.reactions.some((r) => r.level === "mandatory") ? "mandatory" : "desirable";
}

/** Термин инструкции и слова пациента, если они другие: «Тризм — «сводит челюсть»». */
export function reactionText(reaction: ReportedReaction): string {
  const same = reaction.patient_title.toLowerCase() === reaction.title.toLowerCase();
  return same ? reaction.title : `${reaction.title} — «${reaction.patient_title}»`;
}
