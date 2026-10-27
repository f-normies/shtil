/**
 * Основание в инспекторе источника (спека #106, разделы 3.6 и 3.7): заголовок, строки
 * применимости и все ссылки основания у сигнала, проверки терапии, схемы по инструкции
 * и утверждения справки по КР. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import { formatMomentInline } from "./format";
import { signalView } from "./signals";

type Alert = Schemas["AlertOut"];
type Check = Schemas["CheckOut"];
type Option = Schemas["OptionOut"];
type Instruction = Schemas["InstructionOut"];
type Claim = Schemas["Claim"];
type Profile = NonNullable<Schemas["GuidanceOut"]["profile"]>;
type Link = { source: string; title: string; locator: string; url?: string };

/**
 * Ссылка основания: документ манифеста и раздел (`locator`, как записан в данных) или
 * фрагмент утверждения с цитатой. `title` и `url` — снимок для строки источника.
 */
export type SourceRef = {
  source: string;
  title: string;
  locator: string | null;
  url?: string;
  fragmentId?: string;
  quote?: string;
};

/** Что открывает инспектор: заголовок основания, применимость и все ссылки. */
export type Basis = { heading: string; applicability: string[]; refs: SourceRef[] };

const SEX: Record<string, string> = { female: "женский", male: "мужской" };
/** Группы считает сервер (`therapy/guidance.py`), здесь только слова. */
const AGE: Record<NonNullable<Profile["age_group"]>, string> = {
  "<18": "младше 18 лет",
  "18–64": "18–64 года",
  "≥65": "65 лет и старше",
};

/**
 * Основание сигнала. По правилу — правило с версией, значения и время срабатывания;
 * AI-сигнал — вид признака, цитаты и запись. Без ссылок основания нет.
 */
export function alertBasis(alert: Alert, at: (iso: string) => string = formatMomentInline) {
  const view = signalView(alert);
  if (view.sources.length === 0) {
    return null;
  }
  const ai = alert.origin_type === "ai_run";
  const fired = view.firedAt ? at(view.firedAt) : null;
  const applicability = ai
    ? [
        ...view.quotes.map((q) =>
          q.found ? `«${q.text}»` : `«${q.text}» — цитата не найдена в тексте`,
        ),
        [view.recordLabel, fired].filter(Boolean).join(", "),
      ]
    : [
        ...view.values.map(
          (v) => `${v.scale.toUpperCase()} · ${valueName(v.name)}: ${String(v.value)}`,
        ),
        view.detail ?? "",
        fired ? `Срабатывание ${fired}` : "",
      ];
  const version = alert.rule_version === null ? "" : `, версия ${alert.rule_version}`;
  return {
    heading: ai ? `Вид признака «${alert.title}»` : `Правило «${alert.title}»${version}`,
    applicability: applicability.filter((line) => line !== ""),
    // Одинаковая ссылка правила показывается один раз, как в строке источника.
    refs: unique(view.sources.map(toRef)),
  } satisfies Basis;
}

/** Основание проверки терапии: её `detail` и документ. Проверка без источника — `null`. */
export function checkBasis(check: Check): Basis | null {
  if (!check.source) {
    return null;
  }
  return {
    heading: `Проверка «${check.title}»`,
    applicability: check.detail ? [check.detail] : [],
    refs: [toRef(check.source)],
  };
}

/** Схема приёма по инструкции: раздел ОХЛП со схемой. */
export function regimenBasis(option: Option): Basis {
  return {
    heading: `Схема по инструкции «${option.trade_name}»`,
    applicability: [],
    refs: [toRef(option.regimen.source)],
  };
}

/** Указание по инструкции (приём, особые условия): свой раздел ОХЛП. */
export function instructionBasis(option: Option, instruction: Instruction): Basis {
  return {
    heading: `Указание по инструкции «${option.trade_name}»`,
    applicability: [],
    refs: [toRef(instruction.source)],
  };
}

/** Утверждение справки по КР ведёт к своему фрагменту, цитата выделяется (решение 3.2). */
export function claimBasis(claim: Claim, applicability: string[]): Basis {
  const { fragment } = claim;
  return {
    heading: "Утверждение справки",
    applicability,
    refs: [
      {
        source: fragment.source_id,
        title: fragment.title,
        locator: null,
        fragmentId: fragment.id,
        quote: claim.quote,
      },
    ],
  };
}

/**
 * Применимость утверждения справки — профиль показанного ответа (`GuidanceOut.profile`):
 * коды диагнозов, пол и возрастная группа словами. Устаревший ответ посчитан по прежнему
 * профилю: это сказано отдельной строкой.
 */
export function guidanceApplicability(profile: Profile | null, stale: boolean): string[] {
  if (!profile) {
    return [];
  }
  return [
    profile.diagnoses.length > 0 ? `Диагнозы: ${profile.diagnoses.join(", ")}` : "",
    `Пол: ${SEX[profile.sex ?? ""] ?? "не указан"}`,
    `Возраст: ${profile.age_group ? AGE[profile.age_group] : "не указан"}`,
    stale ? "Справка посчитана по прежним данным пациента" : "",
  ].filter((line) => line !== "");
}

/** Документы и разделы для строки источника: «{документ}», раздел; одинаковые — один раз. */
export function refsText(refs: readonly SourceRef[]): string {
  const parts = refs.map((r) => (r.locator ? `«${r.title}», ${r.locator}` : `«${r.title}»`));
  return [...new Set(parts)].join("; ");
}

/** Параметры ручки `GET /knowledge/sources/{source_id}`: фрагмент или раздел. */
export function sourceQuery(ref: SourceRef): { fragment_id: string } | { locator: string } {
  return ref.fragmentId ? { fragment_id: ref.fragmentId } : { locator: ref.locator ?? "" };
}

/** Ключ ссылки в списке основания. */
export function refKey(ref: SourceRef): string {
  return ref.fragmentId ? `${ref.source}#${ref.fragmentId}` : `${ref.source}|${ref.locator}`;
}

/**
 * Ключи списка без повторов: повторное значение получает номер (`a#2`). Порядок сохраняется.
 */
export function keyed<T>(items: readonly T[], keyOf: (item: T) => string) {
  const seen = new Map<string, number>();
  return items.map((item) => {
    const base = keyOf(item);
    const count = (seen.get(base) ?? 0) + 1;
    seen.set(base, count);
    return { item, key: count > 1 ? `${base}#${count}` : base };
  });
}

function unique(refs: SourceRef[]): SourceRef[] {
  return [...new Map(refs.map((r) => [refKey(r), r])).values()];
}

function toRef(link: Link): SourceRef {
  const ref: SourceRef = { source: link.source, title: link.title, locator: link.locator };
  if (link.url) {
    ref.url = link.url;
  }
  return ref;
}

function valueName(name: string): string {
  return name === "total" ? "сумма" : name;
}
