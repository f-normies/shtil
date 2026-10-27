/**
 * Экран «Сигналы» (спека #95, раздел 5; кадры «Врач v4 · 03» и «03б»): группы списка,
 * подписи состояния, отсрочка, варианты решения, причина и ход обработки.
 * Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import { accessClosedStep, escalationTitle } from "./escalation";
import { formatListTime } from "./format";

type Alert = Schemas["AlertOut"];
type Decisions = Schemas["DecisionsOut"];
type DecisionOption = Schemas["DecisionOptionOut"];
type Level = Alert["level"];
type Threshold = Schemas["ThresholdOut"];
type Assessment = Schemas["AssessmentOut"];

export type Mark = { value: number; level: Level };
export type ScorePoint = { value: number; band: string | null; at: string };

const LEVEL_RANK: Record<Level, number> = { mandatory: 0, desirable: 1 };
const LEVEL_WORD: Record<Level, string> = { mandatory: "Обязательно", desirable: "Желательно" };
const MONTH_DAY = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  timeZone: "UTC",
});
const DAY_MS = 86_400_000;

export type Step = { title: string; detail: string; at: string | null; done: boolean };
export type Reason = { scale: string; key: string; value: number; isScore: boolean };

/** «Обязательно» выше, внутри уровня — по последнему срабатыванию, новые первыми. */
export function byPriority(a: Alert, b: Alert): number {
  return (
    LEVEL_RANK[a.level] - LEVEL_RANK[b.level] ||
    b.last_triggered_at.localeCompare(a.last_triggered_at)
  );
}

/**
 * «Обязательно» в работе сработал снова после последнего действия врача. Сигнал остаётся
 * «в работе» (спека #95), но врач должен его увидеть, как новый. Действия системы
 * (`actor_id` пуст) врачом не считаются.
 */
export function hasNewFiring(alert: Alert): boolean {
  if (alert.state !== "in_work" || alert.level !== "mandatory") {
    return false;
  }
  const doctorActions = (alert.actions ?? []).filter((a) => a.actor_id !== null);
  const seen = doctorActions.at(-1)?.at ?? alert.acknowledged_at ?? alert.created_at;
  return Date.parse(alert.last_triggered_at) > Date.parse(seen);
}

/**
 * «Новые» и «В работе у вас». Отложенные стоят в работе до даты возврата. Сигнал в работе
 * с новым срабатыванием «Обязательно» — среди новых.
 */
export function openGroups(alerts: readonly Alert[]): { fresh: Alert[]; inWork: Alert[] } {
  const open = alerts.filter((a) => a.state !== "resolved").sort(byPriority);
  const fresh = (a: Alert) => a.state === "new" || hasNewFiring(a);
  return {
    fresh: open.filter(fresh),
    inWork: open.filter((a) => !fresh(a) && (a.state === "in_work" || a.state === "snoozed")),
  };
}

/** Время в строке списка: сегодня — часы, вчера — «Вчера», раньше — «16.10» (кадр 02). */
export function whenText(iso: string, now: Date): string {
  return formatListTime(iso, now);
}

/** Подпись состояния в строке списка. */
export function stateLine(alert: Alert, now: Date): string {
  switch (alert.state) {
    case "new":
      return alert.level === "mandatory" ? "Новый, ждёт решения" : "Новый";
    case "in_work": {
      if (hasNewFiring(alert)) {
        return `Новое срабатывание ${dayWord(alert.last_triggered_at, now)}`;
      }
      // После отсрочки сигнал берут повторно: показываем последнее взятие.
      const taken = (alert.actions ?? []).filter((a) => a.action === "acknowledge").at(-1);
      return `Взят в работу ${dayWord(taken?.at ?? alert.acknowledged_at, now)}`;
    }
    case "snoozed":
      return `Отложен до ${dayOf(alert.snoozed_until ?? "")}`;
    default:
      return `Решён ${dayWord(alert.resolved_at, now)}`;
  }
}

/** Варианты отсрочки: дата и подпись «Через неделю, 29 октября». */
export function snoozeChoices(
  today: string,
  presets: readonly number[],
): { days: number; date: string; label: string }[] {
  return presets.map((days) => {
    const date = addDays(today, days);
    return { days, date, label: `${inDays(days)}, ${dayOf(date)}` };
  });
}

/** Сегодня по Москве (`YYYY-MM-DD`): даты отсрочки сервер считает по московскому календарю. */
export function moscowToday(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(now);
}

/** Последний день, до которого можно отложить: сегодня + `max_days`. */
export function lastSnoozeDay(today: string, maxDays: number): string {
  return addDays(today, maxDays);
}

/**
 * Варианты решения, как их выбирает сервер (`alerts/lifecycle.py`): сигнал о риске —
 * `safety`, безопасность терапии — `therapy` по `evidence.safety_kind`. Маршрутизацию
 * решает направление (#35): вариантов нет.
 */
export function decisionChoices(alert: Alert, decisions: Decisions | undefined): DecisionOption[] {
  if (!decisions || alert.kind === "routing") {
    return [];
  }
  if (alert.kind === "safety") {
    const kind = alert.triggers[0]?.evidence.safety_kind;
    return decisions.therapy.filter(
      (d) => typeof kind !== "string" || (d.applies_to ?? []).includes(kind),
    );
  }
  return decisions.safety;
}

/** Можно ли сохранить решение: вариант выбран, у «Обязательно» есть причина. */
export function canSaveDecision(level: Level, decisionId: string | null, reason: string): boolean {
  return decisionId !== null && (level !== "mandatory" || reason.trim().length > 0);
}

/** Что сработало: значение суммы или пункта шкалы из последнего срабатывания правила. */
export function reasonOf(alert: Alert): Reason | null {
  if (alert.origin_type !== "rule") {
    return null;
  }
  const evidence = alert.triggers.at(-1)?.evidence ?? {};
  for (const [scale, values] of Object.entries(evidence)) {
    if (typeof values !== "object" || values === null || Array.isArray(values)) {
      continue;
    }
    const [entry] = Object.entries(values as Record<string, unknown>);
    if (entry && typeof entry[1] === "number") {
      return { scale, key: entry[0], value: entry[1], isScore: entry[0] === "total" };
    }
  }
  return null;
}

/** Пороги на линейке суммы: нижние границы правил по этой сумме («от 10»), без повторов. */
export function scoreMarks(thresholds: readonly Threshold[], score: string): Mark[] {
  const marks = new Map<string, Mark>();
  for (const t of thresholds) {
    if (t.score === score && (t.op === ">=" || t.op === ">")) {
      const value = t.op === ">" ? t.value + 1 : t.value;
      const level = t.level === "mandatory" ? "mandatory" : "desirable";
      marks.set(`${value}|${level}`, { value, level });
    }
  }
  return [...marks.values()].sort((a, b) => a.value - b.value);
}

/**
 * Две последние оценки суммы по шкале, старая первой: «было → стало». `until` — время
 * срабатывания: оценки после него к сигналу не относятся.
 */
export function lastTwo(
  history: readonly Assessment[],
  scale: string,
  score: string,
  until?: string,
): ScorePoint[] {
  return history
    .filter((a) => a.scale_id === scale && a.scores[score])
    .filter((a) => until === undefined || Date.parse(a.completed_at) <= Date.parse(until))
    .sort((a, b) => a.completed_at.localeCompare(b.completed_at))
    .slice(-2)
    .map((a) => {
      const s = a.scores[score];
      return { value: s?.value ?? 0, band: s?.band.label ?? null, at: a.completed_at };
    });
}

/** «+4 за 2 недели», «−4 за 3 дня», «без изменений за 1 день». Даты — `YYYY-MM-DD`. */
export function changeText(from: number, to: number, fromDay: string, toDay: string): string {
  const days = Math.max(1, Math.round((Date.parse(toDay) - Date.parse(fromDay)) / DAY_MS));
  const delta = to - from;
  const sign = delta > 0 ? `+${delta}` : delta < 0 ? `−${Math.abs(delta)}` : "без изменений";
  return `${sign} за ${periodText(days)}`;
}

/**
 * Ход обработки: создан, доставлен, взят в работу, затем действия из истории и повторные
 * срабатывания по времени. Шаги эскалации (#56) — от системы, закрытие доступа заместителя —
 * после действий. Пока нет решения — шаг «Решение» с подсказкой. `names` — ФИО врачей по id
 * (`actors`).
 */
export function timeline(
  alert: Alert,
  names: Record<string, string>,
  now: Date = new Date(),
): Step[] {
  const ai = alert.origin_type === "ai_run";
  const steps: Step[] = [
    {
      title: ai ? "Создан по тексту, AI" : "Создан по правилу",
      detail: alert.occurrences > 1 ? `Срабатываний: ${alert.occurrences}` : "",
      at: alert.created_at,
      done: true,
    },
    {
      title: "Доставлен врачу",
      detail: alert.delivered_at
        ? [names[alert.doctor_id], "кабинет врача"].filter(Boolean).join(", ")
        : "Ещё нет",
      at: alert.delivered_at,
      done: alert.delivered_at !== null,
    },
  ];
  const actions = alert.actions ?? [];
  if (!actions.some((a) => a.action === "acknowledge")) {
    steps.push({
      title: "Взят в работу",
      detail: alert.acknowledged_at ? (names[alert.acknowledged_by ?? ""] ?? "") : "Ещё нет",
      at: alert.acknowledged_at,
      done: alert.acknowledged_at !== null,
    });
  }
  // Действия идут в порядке истории, повторное срабатывание встаёт перед первым действием
  // позже него.
  const firings = alert.triggers.slice(1).map((t) => ({
    title: "Повторное срабатывание",
    detail: [LEVEL_WORD[t.level as Level], ...firingValues(t.evidence)].join(" · "),
    at: t.at,
    done: true,
  }));
  for (const a of actions) {
    while (firings[0] && Date.parse(firings[0].at) < Date.parse(a.at)) {
      steps.push(firings.shift() as Step);
    }
    const who = a.actor_id ? (names[a.actor_id] ?? "") : "система";
    steps.push({
      title: a.action === "escalate" ? escalationTitle(a, alert, names) : actionTitle(a),
      detail: [who, a.reason].filter(Boolean).join(" · "),
      at: a.at,
      done: true,
    });
  }
  const closed = accessClosedStep(alert, now);
  if (closed) {
    steps.push(closed);
  }
  steps.push(...firings);
  if (alert.state !== "resolved") {
    steps.push({
      title: "Решение",
      detail:
        alert.kind === "routing"
          ? "По желанию: направить, отложить или отклонить с причиной"
          : alert.level === "mandatory"
            ? "Ещё нет, зафиксировать обязательно"
            : "Ещё нет",
      at: null,
      done: false,
    });
  }
  return steps;
}

function actionTitle(a: Schemas["ActionOut"]): string {
  switch (a.action) {
    case "acknowledge":
      return "Взят в работу";
    case "snooze":
      return `Отложен до ${dayOf(a.snoozed_until ?? "")}`;
    case "unsnooze":
      return "Отсрочка снята: уровень поднялся";
    case "no_contact":
      return "Не удалось связаться";
    default:
      // Сигналы, закрытые подтверждением до #95, перенесены миграцией без текста решения.
      return `Решение: ${a.decision_text ?? "Закрыт подтверждением"}`;
  }
}

/** Значения правила или подробность срабатывания: «phq-9 · phq9-9: 2». */
function firingValues(evidence: Record<string, unknown>): string[] {
  if (typeof evidence.detail === "string") {
    return [evidence.detail];
  }
  return Object.entries(evidence).flatMap(([scale, values]) =>
    typeof values === "object" && values !== null && !Array.isArray(values)
      ? Object.entries(values as Record<string, unknown>)
          .filter(([, value]) => typeof value === "number")
          .map(([name, value]) => `${scale} · ${name}: ${String(value)}`)
      : [],
  );
}

/** «сегодня», «вчера» или «12 октября» по календарю браузера. */
function dayWord(iso: string | null, now: Date): string {
  if (!iso) {
    return "";
  }
  const day = localDay(new Date(iso));
  const today = localDay(now);
  if (day === today) {
    return "сегодня";
  }
  if (day === addDays(today, -1)) {
    return "вчера";
  }
  return dayOf(day);
}

function localDay(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** `YYYY-MM-DD` → «29 октября». */
function dayOf(day: string): string {
  return MONTH_DAY.format(new Date(`${day}T00:00:00Z`));
}

function addDays(day: string, days: number): string {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);
}

function inDays(days: number): string {
  if (days === 7) {
    return "Через неделю";
  }
  if (days % 7 === 0) {
    return `Через ${days / 7} недели`;
  }
  return `Через ${periodText(days)}`;
}

/** «1 день», «3 дня», «5 дней», «2 недели». */
function periodText(days: number): string {
  if (days % 7 === 0 && days >= 14) {
    const weeks = days / 7;
    return `${weeks} ${weeks < 5 ? "недели" : "недель"}`;
  }
  if (days === 7) {
    return "неделю";
  }
  const tens = days % 100;
  const ones = days % 10;
  const word =
    tens >= 11 && tens <= 14
      ? "дней"
      : ones === 1
        ? "день"
        : ones >= 2 && ones <= 4
          ? "дня"
          : "дней";
  return `${days} ${word}`;
}
