import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import {
  canSaveDecision,
  changeText,
  decisionChoices,
  hasNewFiring,
  lastTwo,
  moscowToday,
  openGroups,
  reasonOf,
  scoreMarks,
  snoozeChoices,
  stateLine,
  timeline,
  whenText,
} from "./alerts";
import { alert, assessment, DOCTOR, trigger } from "./fixtures";

type Decisions = Schemas["DecisionsOut"];
type Action = Schemas["ActionOut"];

const NOW = new Date("2026-10-22T12:00:00+03:00");
const DECISIONS: Decisions = {
  safety: [{ id: "risk-not-high", text: "Риск не высокий", reason_hint: "план", applies_to: [] }],
  no_contact: "Не удалось связаться",
  therapy: [
    {
      id: "drug-stopped",
      text: "Препарат отменён",
      reason_hint: "что сделано",
      applies_to: ["contraindication", "caution", "reaction"],
    },
    {
      id: "data-corrected",
      text: "Данные исправлены",
      reason_hint: "что исправлено",
      applies_to: ["contraindication"],
    },
  ],
  referral_decline: [{ id: "other", text: "Другое" }],
  snooze: { max_days: 14, presets_days: [3, 7, 14] },
};

function action(overrides: Partial<Action>): Action {
  return {
    at: "2026-10-22T09:30:00+03:00",
    actor_id: DOCTOR,
    action: "acknowledge",
    decision_id: null,
    decision_text: null,
    reason: null,
    snoozed_until: null,
    ...overrides,
  };
}

describe("openGroups", () => {
  // Кадр 03: «Новые» и «В работе у вас»; отложенные — в работе (аннотация кадра 11б).
  test("splits by state and orders mandatory first, then newest", () => {
    const groups = openGroups([
      alert({ id: "old-desirable", level: "desirable", last_triggered_at: "2026-10-20T10:00:00Z" }),
      alert({ id: "work", state: "in_work" }),
      alert({ id: "mandatory", last_triggered_at: "2026-10-19T10:00:00Z" }),
      alert({ id: "new-desirable", level: "desirable", last_triggered_at: "2026-10-21T10:00:00Z" }),
      alert({ id: "snoozed", state: "snoozed", snoozed_until: "2026-10-29" }),
      alert({ id: "resolved", state: "resolved" }),
    ]);
    expect(groups.fresh.map((a) => a.id)).toEqual(["mandatory", "new-desirable", "old-desirable"]);
    expect(groups.inWork.map((a) => a.id)).toEqual(["work", "snoozed"]);
  });

  test("an in-work mandatory signal that fired again is listed with the new ones", () => {
    const groups = openGroups([
      alert({
        id: "refired",
        state: "in_work",
        acknowledged_at: "2026-10-20T10:00:00Z",
        last_triggered_at: "2026-10-21T10:00:00Z",
      }),
      alert({ id: "work", state: "in_work", acknowledged_at: "2026-10-20T10:00:00Z" }),
    ]);
    expect(groups.fresh.map((a) => a.id)).toEqual(["refired"]);
    expect(groups.inWork.map((a) => a.id)).toEqual(["work"]);
  });
});

describe("hasNewFiring", () => {
  // Ревью #95: новое срабатывание «Обязательно» после последнего действия врача.
  const taken = { state: "in_work" as const, acknowledged_at: "2026-10-20T10:00:00Z" };

  test.each([
    ["fired after taking", alert({ ...taken, last_triggered_at: "2026-10-21T10:00:00Z" }), true],
    ["nothing new", alert({ ...taken, last_triggered_at: "2026-10-20T09:00:00Z" }), false],
    [
      "doctor acted after the firing",
      alert({
        ...taken,
        last_triggered_at: "2026-10-21T10:00:00Z",
        actions: [
          action({ at: "2026-10-20T10:00:00Z" }),
          action({ action: "no_contact", at: "2026-10-21T11:00:00Z" }),
        ],
      }),
      false,
    ],
    [
      "system action does not count as seen",
      alert({
        ...taken,
        last_triggered_at: "2026-10-21T10:00:00Z",
        actions: [
          action({ at: "2026-10-20T10:00:00Z" }),
          action({ action: "unsnooze", actor_id: null, at: "2026-10-21T10:00:00Z" }),
        ],
      }),
      true,
    ],
    [
      "desirable stays in work",
      alert({ ...taken, level: "desirable", last_triggered_at: "2026-10-21T10:00:00Z" }),
      false,
    ],
    ["new signal is new anyway", alert({ last_triggered_at: "2026-10-21T10:00:00Z" }), false],
  ])("%s", (_name, a, expected) => {
    expect(hasNewFiring(a)).toBe(expected);
  });
});

describe("stateLine", () => {
  test.each([
    [alert({}), "Новый, ждёт решения"],
    [alert({ level: "desirable" }), "Новый"],
    [
      alert({ state: "in_work", acknowledged_at: "2026-10-21T15:00:00+03:00" }),
      "Взят в работу вчера",
    ],
    [
      alert({ state: "in_work", acknowledged_at: "2026-10-22T09:00:00+03:00" }),
      "Взят в работу сегодня",
    ],
    [
      alert({
        state: "in_work",
        acknowledged_at: "2026-10-21T15:00:00+03:00",
        last_triggered_at: "2026-10-22T09:00:00+03:00",
      }),
      "Новое срабатывание сегодня",
    ],
    [alert({ state: "snoozed", snoozed_until: "2026-10-29" }), "Отложен до 29 октября"],
    [alert({ state: "resolved", resolved_at: "2026-10-12T09:00:00+03:00" }), "Решён 12 октября"],
  ])("%#", (a, expected) => {
    expect(stateLine(a, NOW)).toBe(expected);
  });
});

test("snooze choices are dated and capped by max_days", () => {
  expect(snoozeChoices("2026-10-22", DECISIONS.snooze?.presets_days ?? [])).toEqual([
    { days: 3, date: "2026-10-25", label: "Через 3 дня, 25 октября" },
    { days: 7, date: "2026-10-29", label: "Через неделю, 29 октября" },
    { days: 14, date: "2026-11-05", label: "Через 2 недели, 5 ноября" },
  ]);
});

describe("decisionChoices", () => {
  test("risk signal takes safety decisions, routing has none", () => {
    expect(decisionChoices(alert({}), DECISIONS).map((d) => d.id)).toEqual(["risk-not-high"]);
    expect(decisionChoices(alert({ kind: "routing" }), DECISIONS)).toEqual([]);
    expect(decisionChoices(alert({}), undefined)).toEqual([]);
  });

  test("therapy signal is filtered by its safety kind", () => {
    const caution = alert({
      kind: "safety",
      triggers: [trigger({ evidence: { safety_kind: "caution" } })],
    });
    const old = alert({ kind: "safety" });
    expect(decisionChoices(caution, DECISIONS).map((d) => d.id)).toEqual(["drug-stopped"]);
    expect(decisionChoices(old, DECISIONS).map((d) => d.id)).toEqual([
      "drug-stopped",
      "data-corrected",
    ]);
  });
});

// Кадр 03: «Сохранить решение» неактивна без варианта и, у «Обязательно», без причины.
test.each([
  ["mandatory", "risk-not-high", "Связались", true],
  ["mandatory", "risk-not-high", "   ", false],
  ["mandatory", null, "Связались", false],
  ["desirable", "drug-stopped", "", true],
] as const)("can save %s / %s / %j → %s", (level, decision, reason, expected) => {
  expect(canSaveDecision(level, decision, reason)).toBe(expected);
});

describe("reasonOf", () => {
  test("item answer and total score come from the latest trigger", () => {
    const item = alert({ triggers: [trigger({ evidence: { "phq-9": { "phq9-9": 1 } } })] });
    const total = alert({ triggers: [trigger({ evidence: { "phq-9": { total: 16 } } })] });
    const ai = alert({ origin_type: "ai_run", triggers: [trigger({ evidence: { signs: [] } })] });
    expect(reasonOf(item)).toEqual({ scale: "phq-9", key: "phq9-9", value: 1, isScore: false });
    expect(reasonOf(total)).toEqual({ scale: "phq-9", key: "total", value: 16, isScore: true });
    expect(reasonOf(ai)).toBeNull();
  });
});

test.each([
  [12, 16, "2026-10-04", "2026-10-18", "+4 за 2 недели"],
  [16, 12, "2026-10-15", "2026-10-18", "−4 за 3 дня"],
  [12, 12, "2026-10-17", "2026-10-18", "без изменений за 1 день"],
])("change %i → %i", (from, to, fromDay, toDay, expected) => {
  expect(changeText(from, to, fromDay, toDay)).toBe(expected);
});

describe("timeline", () => {
  const names = { [DOCTOR]: "Соколова И. П." };

  test("new mandatory signal waits for work and a decision", () => {
    const steps = timeline(
      alert({ delivered_at: "2026-10-22T09:14:00+03:00", delivered_via: "ws" }),
      names,
    );
    expect(steps.map((s) => [s.title, s.done])).toEqual([
      ["Создан по правилу", true],
      ["Доставлен врачу", true],
      ["Взят в работу", false],
      ["Решение", false],
    ]);
    expect(steps[3]?.detail).toBe("Ещё нет, зафиксировать обязательно");
  });

  test("actions are listed with author, decision text and reason", () => {
    const steps = timeline(
      alert({
        origin_type: "ai_run",
        level: "mandatory",
        state: "resolved",
        actors: names,
        actions: [
          action({}),
          action({ action: "no_contact", at: "2026-10-22T10:00:00+03:00" }),
          action({
            action: "resolve",
            decision_text: "Риск не высокий",
            reason: "Связались вечером",
          }),
        ],
      }),
      names,
    );
    expect(steps.map((s) => s.title)).toEqual([
      "Создан по тексту, AI",
      "Доставлен врачу",
      "Взят в работу",
      "Не удалось связаться",
      "Решение: Риск не высокий",
    ]);
    expect(steps[4]?.detail).toBe("Соколова И. П. · Связались вечером");
  });

  test("repeated firings are steps in time order with level and values", () => {
    // Ревью #95: врач видит, когда и с какими значениями сигнал сработал снова.
    const steps = timeline(
      alert({
        state: "in_work",
        occurrences: 2,
        acknowledged_at: "2026-10-20T10:00:00Z",
        last_triggered_at: "2026-10-21T10:00:00Z",
        actions: [action({ at: "2026-10-20T10:00:00Z" })],
        triggers: [
          trigger({ at: "2026-10-18T08:12:00Z" }),
          trigger({ at: "2026-10-21T10:00:00Z", evidence: { "phq-9": { "phq9-9": 2 } } }),
        ],
      }),
      names,
    );
    expect(steps.map((s) => s.title)).toEqual([
      "Создан по правилу",
      "Доставлен врачу",
      "Взят в работу",
      "Повторное срабатывание",
      "Решение",
    ]);
    expect(steps[3]).toMatchObject({
      at: "2026-10-21T10:00:00Z",
      detail: "Обязательно · phq-9 · phq9-9: 2",
      done: true,
    });
  });

  test("a signal closed before decisions existed reads as closed by confirmation", () => {
    const steps = timeline(
      alert({ state: "resolved", actions: [action({ action: "resolve", decision_text: null })] }),
      names,
    );
    expect(steps.at(-1)?.title).toBe("Решение: Закрыт подтверждением");
  });
});

// Кадр 03: время в строке списка — «09:14» сегодня, «Вчера», затем дата.
test("when a signal fired, in the browser time zone", () => {
  const today = "2026-10-22T09:14:00+03:00";
  const hours = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });
  expect(whenText(today, NOW)).toBe(hours.format(new Date(today)));
  expect(whenText("2026-10-21T18:20:00+03:00", NOW)).toBe("Вчера");
  expect(whenText("2026-10-12T18:20:00+03:00", NOW)).toBe("12.10");
  expect(whenText("2025-12-30T18:20:00+03:00", NOW)).toBe("30.12.2025");
});

type Threshold = Schemas["ThresholdOut"];

function threshold(overrides: Partial<Threshold>): Threshold {
  return {
    rule_id: "r",
    kind: "routing",
    level: "desirable",
    item: null,
    score: "total",
    op: ">=",
    value: 10,
    ...overrides,
  };
}

// Кадр 03б: «Желательно от 10», «Обязательно от 20» — нижние границы правил, без повторов.
test("score marks are lower bounds of rules on this score", () => {
  const marks = scoreMarks(
    [
      threshold({}),
      threshold({ op: "<=", value: 19 }),
      threshold({ rule_id: "hamd", score: "total", value: 10 }),
      threshold({ level: "mandatory", value: 20 }),
      threshold({ item: "phq9-9", score: null, level: "mandatory", value: 1 }),
    ],
    "total",
  );
  expect(marks).toEqual([
    { value: 10, level: "desirable" },
    { value: 20, level: "mandatory" },
  ]);
});

test("last two results of the score, older first", () => {
  const band = {
    code: "b",
    label: "Умеренная",
    source: { id: "s", locator: "1", title: "t", url: "u", supplementary: false },
  };
  const score = (value: number) => ({ total: { value, band } });
  const history = [
    assessment({ id: "a", completed_at: "2026-10-04T09:00:00Z", scores: score(12) }),
    assessment({ id: "b", completed_at: "2026-10-18T09:00:00Z", scores: score(16) }),
    assessment({
      id: "c",
      completed_at: "2026-10-10T09:00:00Z",
      scale_id: "other",
      scores: score(1),
    }),
  ];
  expect(lastTwo(history, "test-mood", "total").map((p) => p.value)).toEqual([12, 16]);
  expect(lastTwo(history.slice(0, 1), "test-mood", "total").map((p) => p.value)).toEqual([12]);
  // На момент срабатывания: оценки после него не берутся.
  expect(
    lastTwo(history, "test-mood", "total", "2026-10-11T00:00:00Z").map((p) => p.value),
  ).toEqual([12]);
});

// Отсрочку проверяет сервер по московской дате: у врача во Владивостоке уже завтра.
test.each([
  ["2026-10-22T20:59:00Z", "2026-10-22"],
  ["2026-10-22T21:00:00Z", "2026-10-23"],
])("Moscow date of %s", (iso, expected) => {
  expect(moscowToday(new Date(iso))).toBe(expected);
});

test("in-work line shows the latest take into work", () => {
  const retaken = alert({
    state: "in_work",
    acknowledged_at: "2026-10-12T09:00:00+03:00",
    actions: [
      action({ at: "2026-10-12T09:00:00+03:00" }),
      action({ action: "snooze", at: "2026-10-12T09:05:00+03:00", snoozed_until: "2026-10-15" }),
      action({ at: "2026-10-22T09:00:00+03:00" }),
    ],
  });
  expect(stateLine(retaken, NOW)).toBe("Взят в работу сегодня");
});
