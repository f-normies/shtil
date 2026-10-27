import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import {
  confirmedText,
  isSlow,
  nextPollStart,
  POLL_LIMIT_MS,
  POLL_MS,
  periodText,
  pollInterval,
  type SummaryKind,
  statementView,
  summaryView,
} from "./summary";

type Summary = Schemas["VisitSummaryOut"];
type Fact = Schemas["SummaryFact"];

/** Синтетические факты периода (спека #38, раздел 3.2). */
const SCALE: Fact = {
  number: 1,
  kind: "scale",
  at: "2026-10-19T06:12:00Z",
  text: "GAD-7 19.10: 9 баллов, лёгкая; 12.10 было 12, умеренная",
  section: "assessments",
  level: null,
};
const SIGNAL: Fact = {
  number: 2,
  kind: "signal",
  at: "2026-10-19T06:14:00Z",
  text: "Сигнал «Обязательно»: мысли о смерти или самоповреждении; открыт",
  section: "signals",
  level: "mandatory",
};
const DIARY: Fact = {
  number: 3,
  kind: "finding",
  at: "2026-10-18T18:30:00Z",
  text: "Симптом: нарушение сна, «третью ночь не сплю», голосовая запись в дневнике",
  section: "diary",
  level: null,
};
const CHAT: Fact = {
  number: 4,
  kind: "finding",
  at: "2026-10-06T07:00:00Z",
  text: "Приём препарата: пропустила вечернюю дозу, сообщение в чате",
  section: "messages",
  level: null,
};
const DESIRABLE: Fact = {
  ...SIGNAL,
  number: 5,
  text: "Сигнал «Желательно»: вероятная депрессия; в работе",
  level: "desirable",
};

function summary(overrides: Partial<Summary>): Summary {
  return {
    id: "00000000-0000-4000-8000-0000000000a1",
    status: "done",
    period_start: "2026-10-05T07:00:00Z",
    period_end: "2026-10-19T07:00:00Z",
    stale: false,
    facts: [SCALE, SIGNAL, DIARY, CHAT, DESIRABLE],
    statements: [
      { text: "Тревога снизилась: GAD-7 умеренная → лёгкая", facts: [1] },
      { text: "Открыт сигнал «Обязательно»", facts: [2] },
    ],
    edited: false,
    confirmed_by: null,
    confirmed_by_name: null,
    confirmed_at: null,
    ...overrides,
  };
}

const ME = "00000000-0000-4000-8000-0000000000d1";
const OTHER = "00000000-0000-4000-8000-0000000000d2";

describe("periodText", () => {
  test("names the period start as day and month in the genitive", () => {
    expect(periodText("2026-10-05T07:00:00Z", "Europe/Moscow")).toBe("с 5 октября");
  });

  test("uses the browser time zone: late evening UTC is the next day in Moscow", () => {
    expect(periodText("2026-10-04T22:30:00Z", "Europe/Moscow")).toBe("с 5 октября");
  });
});

describe("summaryView: group states (спека #38, раздел 3.6)", () => {
  const cases: [string, Partial<Summary>, SummaryKind, string | null, boolean][] = [
    ["calculation", { status: "pending", statements: [] }, "pending", null, false],
    ["draft", {}, "draft", null, false],
    ["confirmed", { status: "confirmed" }, "confirmed", null, false],
    ["no data", { id: null, status: "empty", facts: [], statements: [] }, "empty", null, false],
    [
      "failure",
      { status: "failed", statements: [] },
      "failed",
      "Не удалось составить сводку",
      true,
    ],
    [
      "previous draft while the new one is calculated",
      { status: "pending", stale: true },
      "stale",
      "Данные пациента изменились, сводка обновляется",
      false,
    ],
    [
      "previous draft after the new one failed",
      { status: "failed", stale: true },
      "stale",
      "Не удалось обновить сводку по новым данным",
      true,
    ],
  ];

  test.each(cases)("%s", (_name, overrides, kind, notice, retry) => {
    const view = summaryView(summary(overrides), { timeZone: "Europe/Moscow" });
    expect([view.kind, view.notice, view.retry, view.refresh]).toEqual([
      kind,
      notice,
      retry,
      false,
    ]);
  });

  test("empty says since when there is nothing new", () => {
    const view = summaryView(summary({ id: null, status: "empty", facts: [], statements: [] }), {
      timeZone: "Europe/Moscow",
    });
    expect(view.empty).toBe("Нет новых данных с 5 октября");
  });

  test("calculation shows a skeleton and no statements", () => {
    const view = summaryView(summary({ status: "pending", statements: [] }));
    expect([view.skeleton, view.statements]).toEqual([true, []]);
  });

  test("stale keeps the previous draft on screen and cannot be confirmed", () => {
    const view = summaryView(summary({ status: "pending", stale: true }));
    expect(view.statements.map((s) => s.text)).toEqual([
      "Тревога снизилась: GAD-7 умеренная → лёгкая",
      "Открыт сигнал «Обязательно»",
    ]);
    expect(view.canConfirm).toBe(false);
  });

  // Опрос шёл 3 минуты, а черновик всё ещё считается (Мак с моделью выключен).
  test("slow calculation stops the skeleton and offers «Обновить»", () => {
    const view = summaryView(summary({ status: "pending", statements: [] }), { slow: true });
    expect([view.kind, view.notice, view.refresh, view.skeleton]).toEqual([
      "slow",
      "Сводка ещё составляется",
      true,
      false,
    ]);
  });

  test("slow update keeps the previous draft and offers «Обновить»", () => {
    const view = summaryView(summary({ status: "pending", stale: true }), { slow: true });
    expect([view.kind, view.notice, view.refresh, view.statements.length]).toEqual([
      "stale",
      "Данные пациента изменились, сводка ещё обновляется",
      true,
      2,
    ]);
  });

  test("slow does not matter once the draft is ready", () => {
    expect(summaryView(summary({}), { slow: true }).kind).toBe("draft");
  });

  test("only a fresh draft can be confirmed or edited", () => {
    expect(summaryView(summary({})).canConfirm).toBe(true);
    expect(summaryView(summary({ status: "confirmed" })).canConfirm).toBe(false);
  });
});

describe("pollInterval: every 3 s while the draft is calculated, at most 3 minutes", () => {
  const cases: [Partial<Summary> | null, number | false][] = [
    [{ status: "pending" }, POLL_MS],
    [{ status: "pending", stale: true }, POLL_MS],
    [{ status: "done" }, false],
    [{ status: "confirmed" }, false],
    [{ status: "empty" }, false],
    [{ status: "failed" }, false],
    // Новый черновик упал: прежний показан, но опрос ничего не изменит до «Повторить».
    [{ status: "failed", stale: true }, false],
    [null, false],
  ];

  test.each(cases)("%j → %s", (overrides, interval) => {
    expect(pollInterval(overrides ? summary(overrides) : undefined, 0, 1_000)).toBe(interval);
  });

  test("polls every 3 seconds for 3 minutes", () => {
    expect([POLL_MS, POLL_LIMIT_MS]).toEqual([3_000, 180_000]);
  });

  test("stops after 3 minutes of polling", () => {
    const pending = summary({ status: "pending" });
    expect(pollInterval(pending, 0, POLL_LIMIT_MS - 1)).toBe(POLL_MS);
    expect(pollInterval(pending, 0, POLL_LIMIT_MS)).toBe(false);
  });

  test("isSlow: still pending after 3 minutes of polling", () => {
    const pending = summary({ status: "pending" });
    expect(isSlow(pending, 0, POLL_LIMIT_MS)).toBe(true);
    expect(isSlow(pending, 0, POLL_LIMIT_MS - 1)).toBe(false);
    expect(isSlow(pending, null, POLL_LIMIT_MS)).toBe(false);
    expect(isSlow(summary({}), 0, POLL_LIMIT_MS)).toBe(false);
  });
});

describe("nextPollStart: polling starts with the first pending answer after mount", () => {
  const pending = summary({ status: "pending" });

  test.each([
    ["first pending answer", null, pending, 1_000, 1_000],
    ["keeps the start while pending", 500, pending, 1_000, 500],
    ["cached answer before the first request", null, pending, null, null],
    ["not pending resets", 500, summary({}), 1_000, null],
    ["no answer resets", 500, undefined, 1_000, null],
  ] as const)("%s", (_name, since, current, at, expected) => {
    expect(nextPollStart(since, current, at)).toBe(expected);
  });
});

describe("statementView", () => {
  const facts = [SCALE, SIGNAL, DIARY, CHAT, DESIRABLE];

  test("shows the facts behind the statement as details", () => {
    const view = statementView({ text: "Тревога снизилась", facts: [1] }, facts, "Europe/Moscow");
    expect(view.details).toBe("GAD-7 19.10: 9 баллов, лёгкая; 12.10 было 12, умеренная");
    expect(view.dates).toBe("19.10");
  });

  test("dates of several facts form a range", () => {
    const view = statementView({ text: "Сон и приём", facts: [3, 4] }, facts, "Europe/Moscow");
    expect(view.dates).toBe("6–18.10");
  });

  test("a range across months shows both months", () => {
    const september = { ...CHAT, number: 6, at: "2026-09-28T07:00:00Z" };
    const view = statementView(
      { text: "Приём", facts: [6, 3] },
      [...facts, september],
      "Europe/Moscow",
    );
    expect(view.dates).toBe("28.09–18.10");
  });

  test("links lead to card sections by fact section, once per section", () => {
    const view = statementView({ text: "Всё", facts: [1, 2, 5, 3, 4] }, facts);
    expect(view.links).toEqual([
      { label: "Шкалы", target: "scales" },
      { label: "Сигналы", target: "signals" },
      { label: "Дневник", target: null },
      { label: "Сообщения", target: null },
    ]);
  });

  // Уровень сигнала — словом и значком (кадр 05б, «Доступность»).
  test.each([
    [[2], "mandatory"],
    [[5], "desirable"],
    [[1], "scale"],
    [[3], "diary"],
    [[4], "messages"],
    [[1, 2], "mandatory"],
  ] as const)("facts %j → icon %s", (numbers, icon) => {
    expect(statementView({ text: "т", facts: [...numbers] }, facts).icon).toBe(icon);
  });

  // Уровень — из поля `level` факта, текст факта не разбираем.
  test("level comes from the fact field, not from its text", () => {
    const fact = { ...SIGNAL, number: 7, text: "Сигнал «Желательно»", level: "mandatory" as const };
    expect(statementView({ text: "т", facts: [7] }, [fact]).icon).toBe("mandatory");
  });

  test("a signal fact without a level keeps a neutral signal icon", () => {
    const plain = { ...SIGNAL, number: 7, text: "Сигнал «Обязательно»: решён", level: null };
    const view = statementView({ text: "т", facts: [7] }, [plain]);
    expect([view.icon, view.level]).toEqual(["signal", null]);
  });

  test.each([
    [[2], { tone: "mandatory", word: "Обязательно" }],
    [[5], { tone: "desirable", word: "Желательно" }],
    [[5, 2], { tone: "mandatory", word: "Обязательно" }],
    [[1, 3], null],
  ] as const)("facts %j → level word %j", (numbers, level) => {
    expect(statementView({ text: "т", facts: [...numbers] }, facts).level).toEqual(level);
  });

  test("unknown fact numbers are ignored", () => {
    const view = statementView({ text: "т", facts: [1, 42] }, facts, "Europe/Moscow");
    expect([view.details, view.links.length]).toEqual([SCALE.text, 1]);
  });
});

describe("confirmedText: who and when", () => {
  const confirmed = summary({
    status: "confirmed",
    confirmed_by: ME,
    confirmed_by_name: "Соколова Ирина Петровна",
    confirmed_at: "2026-10-19T06:46:00Z",
  });

  test("the current doctor reads «вы»", () => {
    expect(confirmedText(confirmed, ME, "Europe/Moscow")).toBe("вы, 19.10, 09:46");
  });

  test("another doctor is named", () => {
    expect(confirmedText(confirmed, OTHER, "Europe/Moscow")).toBe(
      "Соколова Ирина Петровна, 19.10, 09:46",
    );
  });
});

describe("«Изменено врачом»", () => {
  test("marks a confirmed summary the doctor edited", () => {
    expect(summaryView(summary({ status: "confirmed", edited: true })).editedNote).toBe(
      "Изменено врачом",
    );
  });

  test("no mark when the doctor confirmed the draft as is", () => {
    expect(summaryView(summary({ status: "confirmed", edited: false })).editedNote).toBeNull();
  });

  test("no mark on a draft", () => {
    expect(summaryView(summary({ edited: true })).editedNote).toBeNull();
  });
});
