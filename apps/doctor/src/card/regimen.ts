/**
 * Схема приёма и курс в разделе «Терапия» (спека #28, разделы 8.2 и 8.3; кадр «Врач v4 ·
 * 10»): строки «Утро / Вечер», число таблеток, полоса курса, итог проверок. Доза,
 * кратность, расписание и длительность — из карточки препарата, код их не задаёт.
 */
import type { Schemas } from "@compass/api-client";
import { plural } from "./documents";
import { checkWord } from "./therapy";

type Check = Schemas["CheckOut"];

/** Слова расписания из карточки препарата и строка приёма для каждого. */
const MOMENTS: [RegExp, string][] = [
  // Начало слова: «внутрь» — не «утро».
  [/(^|[\s,])утр/, "Утро"],
  [/дн[её]м/, "День"],
  [/вечер/, "Вечер"],
  [/на ночь|перед сном/, "На ночь"],
];

/**
 * Строки схемы. Расписание называет приёмы словами («утром и вечером»); если слов
 * столько же, сколько приёмов, строки по ним, иначе — «Приём 1», «Приём 2».
 */
export function doseSlots(timesPerDay: number, schedule: string): string[] {
  const text = schedule.toLowerCase();
  const named = MOMENTS.filter(([re]) => re.test(text)).map(([, word]) => word);
  if (named.length === timesPerDay) {
    return named;
  }
  if (timesPerDay === 1) {
    return ["Приём"];
  }
  return Array.from({ length: timesPerDay }, (_, i) => `Приём ${i + 1}`);
}

/** «20 мг, 1 таблетка». Дробь, кроме половины, не пишем: таблетку так не делят. */
export function tabletsText(doseMg: number, strengthMg: number | null | undefined): string {
  const dose = `${doseMg} мг`;
  if (!strengthMg) {
    return dose;
  }
  const n = doseMg / strengthMg;
  if (n === 0.5) {
    return `${dose}, ½ таблетки`;
  }
  return Number.isInteger(n)
    ? `${dose}, ${n} ${plural(n, ["таблетка", "таблетки", "таблеток"])}`
    : dose;
}

export type CourseBar = {
  startLabel: string;
  endLabel: string;
  maxLabel: string | null;
  /** Доля сплошной части полосы. */
  share: number;
  /** Озвучка полосы для экранных читалок. */
  spoken: string;
};

const SHORT = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  timeZone: "UTC",
});
const LONG = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone: "UTC" });

/**
 * Полоса курса от сегодня (`today` — дата «ГГГГ-ММ-ДД»): сплошная до срока, пунктир до
 * максимума. Без срока (длительность определяет врач) — `null`, полосы нет.
 */
export function courseBar(
  days: number | null | undefined,
  maxDays: number | null | undefined,
  today: string,
): CourseBar | null {
  if (!days) {
    return null;
  }
  const end = addDays(today, days);
  const max = maxDays && maxDays > days ? addDays(today, maxDays) : null;
  return {
    startLabel: `Сегодня, ${SHORT.format(addDays(today, 0))}`,
    endLabel: `${SHORT.format(end)}, ${spanText(days, false)}`,
    maxLabel: max && maxDays ? `${SHORT.format(max)}, до ${spanText(maxDays, true)}` : null,
    share: max && maxDays ? days / maxDays : 1,
    spoken:
      `Курс ${spanText(days, false)}, до ${LONG.format(end)}` +
      (max ? `, при необходимости до ${LONG.format(max)}` : ""),
  };
}

function addDays(iso: string, days: number): Date {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date;
}

/** «4 недели», «10 дней»; после «до» — родительный падеж: «до 6 недель», «до 1 недели». */
function spanText(days: number, genitive: boolean): string {
  const weeks = days % 7 === 0 ? days / 7 : null;
  const [n, forms] =
    weeks !== null
      ? [
          weeks,
          genitive
            ? (["недели", "недель", "недель"] as const)
            : (["неделя", "недели", "недель"] as const),
        ]
      : [days, genitive ? (["дня", "дней", "дней"] as const) : (["день", "дня", "дней"] as const)];
  return `${n} ${plural(n, forms)}`;
}

/** Множественное число слова статуса; слова-наречия («обязательно») не меняются. */
const PLURAL: Record<string, string> = {
  пройдена: "пройдены",
  "не пройдена": "не пройдены",
};
const ORDER = [
  "пройдена",
  "не пройдена",
  "обязательно",
  "желательно",
  "уточните",
  "проверьте сами",
];

/**
 * Итог у заголовка группы проверок: «4 пройдены, 2 проверьте сами». Слова — те же, что
 * у строк (`checkWord`): у правил безопасности (#29) — «обязательно», «желательно».
 */
export function checksSummary(checks: readonly Check[]): string {
  const counts = new Map<string, number>();
  for (const c of checks) {
    const word = checkWord(c);
    counts.set(word, (counts.get(word) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([word, n]) => `${n} ${n === 1 ? word : (PLURAL[word] ?? word)}`)
    .join(", ");
}

function rank(word: string): number {
  const i = ORDER.indexOf(word);
  return i < 0 ? ORDER.length : i;
}

/** Причина «Назначить нельзя»: не пройденные проверки, регистр сокращений сохранён. */
export function failedText(checks: readonly Check[]): string {
  return checks
    .filter((c) => c.status === "fail")
    .map((c) => c.title.charAt(0).toLowerCase() + c.title.slice(1))
    .join("; ");
}

type Option = Schemas["OptionOut"];
type Proposal = Schemas["ProposalOut"];

/**
 * Предложение назначения из `payload` открытого предложения `therapy.prescription`:
 * снимок справочника на момент подбора (спека #28, раздел 6). Экран показывает его
 * без повторного подбора. Снимок неполный — `null`.
 */
export function offerOf(proposal: Proposal): Option | null {
  const p = proposal.payload;
  if (proposal.kind !== "therapy.prescription" || typeof p.regimen !== "object" || !p.regimen) {
    return null;
  }
  return {
    drug_id: String(p.drug_id),
    trade_name: String(p.trade_name),
    inn: String(p.inn),
    indication_id: String(p.indication_id),
    indication_title: String(p.indication_title),
    indication_icd10: Array.isArray(p.indication_icd10) ? p.indication_icd10.map(String) : [],
    regimen: p.regimen as Option["regimen"],
    instructions: Array.isArray(p.instructions) ? (p.instructions as Option["instructions"]) : [],
    checks: Array.isArray(p.checks) ? (p.checks as Option["checks"]) : [],
    proposal,
  };
}

/** Комментарий, с которым сервер отклоняет предложения при смене профиля (спека #28, 3). */
const PROFILE_CHANGED = "профиль изменился";

/** Открытые предложения назначения из списка предложений пациента, новые первыми. */
export function openOffers(proposals: readonly Proposal[]): Option[] {
  return [...proposals]
    .filter((p) => p.status === "proposed")
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .flatMap((p) => offerOf(p) ?? []);
}

/**
 * Пометка «профиль изменился»: последнее предложение назначения сервер отклонил после
 * правки диагнозов или даты рождения, а нового нет. Рядом — «Подобрать заново».
 */
export function staleNotice(proposals: readonly Proposal[]): string | null {
  const last = [...proposals]
    .filter((p) => p.kind === "therapy.prescription")
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  return last?.status === "rejected" && last.decision_comment === PROFILE_CHANGED
    ? `Предложение отклонено: ${PROFILE_CHANGED}`
    : null;
}
