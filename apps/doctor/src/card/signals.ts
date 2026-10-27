/**
 * Сигналы в карточке пациента (спека #13, разделы 4.1, 4.2 и 4.5): отбор, порядок
 * и то, что врач видит у сигнала. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import type { FileRouteTypes } from "../routeTree.gen";
import { hasNewFiring } from "./alerts";

type Alert = Schemas["AlertOut"];

export type Quote = { text: string; found: boolean };
export type RuleValue = { scale: string; name: string; value: unknown };
/** Ссылка на документ манифеста: id источника, название, раздел и адрес. */
export type SourceLink = { source: string; title: string; locator: string; url?: string };

export type SignalView = {
  levelLabel: string;
  kindLabel: string | null;
  originLabel: string;
  quotes: Quote[];
  recordLabel: string | null;
  firedAt: string | null;
  values: RuleValue[];
  /** Данные, по которым сработало правило безопасности терапии (спека #29). */
  detail: string | null;
  sources: SourceLink[];
};

const LEVEL_RANK: Record<Alert["level"], number> = { mandatory: 0, desirable: 1 };

const RECORD_LABELS: Record<string, string> = { diary: "дневник", message: "сообщение" };

const KIND_LABELS: Record<string, string> = {
  routing: "Маршрутизация",
  safety: "Безопасность терапии",
};

/**
 * Новые сигналы (спека #95) и сигналы в работе с новым срабатыванием «Обязательно»:
 * сначала «обязательно», внутри уровня — новые первыми.
 */
export function openSignals(alerts: readonly Alert[]): Alert[] {
  return alerts
    .filter((a) => a.state === "new" || hasNewFiring(a))
    .sort(
      (a, b) =>
        LEVEL_RANK[a.level] - LEVEL_RANK[b.level] ||
        b.last_triggered_at.localeCompare(a.last_triggered_at),
    );
}

/**
 * «Требует решения» (спека #13, раздел 10.3): новые сигналы и сигналы в работе — по ним
 * ещё нет решения. Отложенный ждёт даты возврата. Сначала «обязательно», новые первыми.
 */
export function needsDecision(alerts: readonly Alert[]): Alert[] {
  return alerts
    .filter((a) => a.state === "new" || a.state === "in_work")
    .sort(
      (a, b) =>
        LEVEL_RANK[a.level] - LEVEL_RANK[b.level] ||
        b.last_triggered_at.localeCompare(a.last_triggered_at),
    );
}

/** Обзор карточки пациента. Полоса открытых сигналов пациента есть только на этой странице. */
export const OVERVIEW_ROUTE_ID: FileRouteTypes["id"] = "/_doctor/patients/$patientId/";

/** Открытая страница: маршрут и его параметры. */
export type Page = { routeId: string; params: { patientId?: string; alertId?: string } };

/** Экран «Сигналы» (спека #95): новые сигналы там в списке, плашка их не повторяет. */
const SIGNALS_SCREEN = "/_doctor/alerts";

/**
 * Сигналы плашки: открытые «Обязательно» (спека #13, раздел 10.8), кроме сигналов пациента
 * на обзоре его карточки — там они в «Требует решения». На других страницах карточки
 * этой группы нет, поэтому плашка их показывает.
 */
export function bannerSignals(open: readonly Alert[], page: Page | undefined): Alert[] {
  if (page?.routeId.startsWith(SIGNALS_SCREEN)) {
    return [];
  }
  const shownOnPage = page?.routeId === OVERVIEW_ROUTE_ID ? page.params.patientId : undefined;
  return openSignals(open.filter((a) => a.level === "mandatory" && a.patient_id !== shownOnPage));
}

/** Что показать у сигнала. Данные — из последнего срабатывания. */
export function signalView(alert: Alert): SignalView {
  const latest = alert.triggers.at(-1);
  const evidence = latest?.evidence ?? {};
  const ai = alert.origin_type === "ai_run";
  return {
    levelLabel: alert.level === "mandatory" ? "Обязательно" : "Желательно",
    kindLabel: KIND_LABELS[alert.kind] ?? null,
    originLabel: ai ? "по тексту, AI" : "по правилу",
    quotes: ai ? quotesOf(evidence) : [],
    recordLabel: ai ? recordLabel(evidence) : null,
    firedAt: latest?.at ?? null,
    values: ai ? [] : ruleValues(evidence),
    detail: !ai && typeof evidence.detail === "string" ? evidence.detail : null,
    sources: ai
      ? signSources(evidence)
      : alert.sources.map((s) => ({
          source: s.source,
          title: s.title,
          locator: s.locator,
          url: s.url,
        })),
  };
}

/** Тип исходной записи AI-сигнала. Записи до #13 не знают о голосе и подписываются по типу. */
export function recordLabel(evidence: Record<string, unknown>): string | null {
  if (evidence.transcript === true) {
    return "голосовая запись";
  }
  const type = evidence.source_type;
  return typeof type === "string" ? (RECORD_LABELS[type] ?? type) : null;
}

/** Цитаты признаков. Одну цитату часто находят два вида признаков — показываем её один раз. */
function quotesOf(evidence: Record<string, unknown>): Quote[] {
  const signs = Array.isArray(evidence.signs) ? evidence.signs : [];
  const quotes = new Map<string, Quote>();
  for (const s of signs as { quote?: unknown; quote_found?: unknown }[]) {
    if (typeof s.quote === "string" && !quotes.has(s.quote)) {
      quotes.set(s.quote, { text: s.quote, found: s.quote_found !== false });
    }
  }
  return [...quotes.values()];
}

/** Ссылки видов признаков. Одна и та же ссылка у нескольких видов показывается один раз. */
function signSources(evidence: Record<string, unknown>): SourceLink[] {
  const byKind = evidence.sources;
  if (typeof byKind !== "object" || byKind === null) {
    return [];
  }
  const links = new Map<string, SourceLink>();
  for (const refs of Object.values(byKind)) {
    for (const r of Array.isArray(refs) ? refs : []) {
      const ref = r as { source: string; locator: string; title?: string; url?: string };
      // Снимки до #13 не знают названия документа: показываем его id.
      const link: SourceLink = {
        source: ref.source,
        title: ref.title ?? ref.source,
        locator: ref.locator,
      };
      if (ref.url) {
        link.url = ref.url;
      }
      links.set(`${ref.source}|${ref.locator}`, link);
    }
  }
  return [...links.values()];
}

function ruleValues(evidence: Record<string, unknown>): RuleValue[] {
  return Object.entries(evidence).flatMap(([scale, values]) =>
    typeof values === "object" && values !== null
      ? Object.entries(values).map(([name, value]) => ({ scale, name, value }))
      : [],
  );
}

/** Строк в плашке «Обязательно» (кадр 12): больше — «Ещё N» со ссылкой на «Сигналы». */
export const BANNER_LIMIT = 3;

/** Какие сигналы показать в плашке строками, сколько свернуть в «Ещё N». */
export function bannerRows<T>(signals: readonly T[]): { shown: T[]; rest: number } {
  return {
    shown: signals.slice(0, BANNER_LIMIT),
    rest: Math.max(0, signals.length - BANNER_LIMIT),
  };
}
