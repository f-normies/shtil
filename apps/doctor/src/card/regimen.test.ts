import type { Schemas } from "@compass/api-client";
import { describe, expect, it } from "vitest";
import {
  checksSummary,
  courseBar,
  doseSlots,
  failedText,
  offerOf,
  openOffers,
  staleNotice,
  tabletsText,
} from "./regimen";

describe("doseSlots: строки схемы приёма (спека #28, 8.2)", () => {
  it.each([
    [2, "утром и вечером", ["Утро", "Вечер"]],
    [1, "вечером", ["Вечер"]],
    [1, "на ночь", ["На ночь"]],
    [3, "утром, днём и вечером", ["Утро", "День", "Вечер"]],
    [2, "время приёма определяет врач", ["Приём 1", "Приём 2"]],
    [1, "время приёма определяет врач", ["Приём"]],
    [2, "вечером", ["Приём 1", "Приём 2"]],
    [2, "внутрь вечером и на ночь", ["Вечер", "На ночь"]],
  ])("%i раз, «%s» → %j", (times, schedule, slots) => {
    expect(doseSlots(times, schedule)).toEqual(slots);
  });
});

describe("tabletsText: доза и число таблеток", () => {
  it.each([
    [20, 20, "20 мг, 1 таблетка"],
    [40, 20, "40 мг, 2 таблетки"],
    [100, 20, "100 мг, 5 таблеток"],
    [10, 20, "10 мг, ½ таблетки"],
    [15, 20, "15 мг"],
    [20, null, "20 мг"],
  ])("%i мг при форме %s мг → «%s»", (dose, strength, text) => {
    expect(tabletsText(dose, strength)).toBe(text);
  });
});

describe("courseBar: полоса курса от сегодня", () => {
  it("срок и запас до максимума — даты и подписи", () => {
    expect(courseBar(28, 42, "2026-10-19")).toEqual({
      startLabel: "Сегодня, 19.10",
      endLabel: "16.11, 4 недели",
      maxLabel: "30.11, до 6 недель",
      share: 28 / 42,
      spoken: "Курс 4 недели, до 16 ноября, при необходимости до 30 ноября",
    });
  });

  it("срок без запаса — сплошная полоса до конца", () => {
    expect(courseBar(14, null, "2026-10-19")).toEqual({
      startLabel: "Сегодня, 19.10",
      endLabel: "02.11, 2 недели",
      maxLabel: null,
      share: 1,
      spoken: "Курс 2 недели, до 2 ноября",
    });
  });

  it("срок в днях, не кратный неделе", () => {
    expect(courseBar(10, null, "2026-10-19")?.endLabel).toBe("29.10, 10 дней");
  });

  it("без срока (ГТР) — полосы нет", () => {
    expect(courseBar(null, null, "2026-10-19")).toBeNull();
  });
});

const check = (status: Schemas["CheckOut"]["status"], id: string = status, title = "") =>
  ({ id, status, title, detail: "", source: null, actions: [] }) as Schemas["CheckOut"];

describe("checksSummary: итог у заголовка проверок", () => {
  it.each([
    [["pass", "pass", "pass", "pass", "manual", "manual"], "4 пройдены, 2 проверьте сами"],
    [["pass", "fail"], "1 пройдена, 1 не пройдена"],
    [["pass", "warn"], "1 пройдена, 1 желательно"],
    [["manual"], "1 проверьте сами"],
  ] as const)("%j → «%s»", (statuses, text) => {
    expect(checksSummary(statuses.map((s) => check(s)))).toBe(text);
  });

  it("у правил безопасности (#29) — те же слова, что в строках", () => {
    const checks = [
      check("pass"),
      check("fail", "contra:mao"),
      check("warn", "caution:renal"),
      check("manual", "contra:lactose"),
    ];
    expect(checksSummary(checks)).toBe("1 пройдена, 1 обязательно, 1 желательно, 1 уточните");
  });
});

describe("failedText: причина «Назначить нельзя»", () => {
  it("названия не пройденных проверок через точку с запятой, регистр сокращений сохранён", () => {
    expect(
      failedText([
        check("fail", "contra:mao", "Приём ингибиторов МАО"),
        check("pass", "age", "Возраст от 18 лет"),
        check("fail", "contra:lactose", "Непереносимость лактозы, дефицит лактазы"),
      ]),
    ).toBe("приём ингибиторов МАО; непереносимость лактозы, дефицит лактазы");
  });
});

describe("offerOf: предложение назначения из payload открытого предложения", () => {
  const regimen = {
    form: "таблетки, 20 мг",
    strength_mg: 20,
    dose_mg: 20,
    times_per_day: 2,
    schedule: "утром и вечером",
    duration_days: 28,
    max_days: 42,
    duration_text: "4 недели, при необходимости до 6",
    source: { source: "ohlp", locator: "4.2", title: "ОХЛП", url: "https://example.invalid" },
  };
  const proposal = (payload: Record<string, unknown>) =>
    ({
      id: "p1",
      kind: "therapy.prescription",
      status: "proposed",
      payload,
    }) as unknown as Schemas["ProposalOut"];

  it("собирает предложение со схемой, указаниями и проверками", () => {
    const offer = offerOf(
      proposal({
        drug_id: "aviandr",
        trade_name: "Авиандр",
        inn: "маритупирдин",
        indication_id: "post-covid-anxiety",
        indication_title: "тревожное состояние после COVID-19",
        indication_icd10: ["F43.2", "F43.8"],
        regimen,
        instructions: [{ text: "Внутрь за 30 минут до еды", source: regimen.source }],
        checks: [],
      }),
    );
    expect(offer?.trade_name).toBe("Авиандр");
    expect(offer?.indication_icd10).toEqual(["F43.2", "F43.8"]);
    expect(offer?.regimen.strength_mg).toBe(20);
    expect(offer?.proposal?.id).toBe("p1");
  });

  it("снимок до #28 v4 без кодов показания — пустой список", () => {
    const offer = offerOf(
      proposal({
        drug_id: "aviandr",
        trade_name: "Авиандр",
        inn: "маритупирдин",
        indication_id: "gad",
        indication_title: "ГТР",
        regimen,
        instructions: [],
        checks: [],
      }),
    );
    expect(offer?.indication_icd10).toEqual([]);
  });

  it("не предложение назначения — null", () => {
    expect(offerOf({ ...proposal({}), kind: "therapy.stop" })).toBeNull();
    expect(offerOf(proposal({ drug_id: "x" }))).toBeNull();
  });
});

describe("openOffers и staleNotice: что показать в «Терапии» без нового подбора", () => {
  const regimen = {
    form: "таблетки",
    dose_mg: 20,
    times_per_day: 2,
    schedule: "утром и вечером",
    duration_days: null,
    max_days: null,
    duration_text: "определяет врач",
    source: { source: "s", locator: "1", title: "s", url: "https://example.invalid" },
  };
  const prescription = (
    id: string,
    status: string,
    created_at: string,
    decision_comment: string | null = null,
  ) =>
    ({
      id,
      kind: "therapy.prescription",
      status,
      created_at,
      decided_at: status === "proposed" ? null : created_at,
      decision_comment,
      payload: {
        drug_id: "d",
        trade_name: "Т",
        inn: "м",
        indication_id: "i",
        indication_title: "п",
        regimen,
      },
    }) as unknown as Schemas["ProposalOut"];

  it("открытые предложения назначения — новые первыми", () => {
    const offers = openOffers([
      prescription("old", "proposed", "2026-10-18T09:00:00Z"),
      prescription("done", "confirmed", "2026-10-19T09:00:00Z"),
      prescription("new", "proposed", "2026-10-19T08:00:00Z"),
    ]);
    expect(offers.map((o) => o.proposal?.id)).toEqual(["new", "old"]);
  });

  it("последнее предложение отклонено системой из-за профиля — пометка", () => {
    expect(
      staleNotice([prescription("p", "rejected", "2026-10-19T09:00:00Z", "профиль изменился")]),
    ).toBe("Предложение отклонено: профиль изменился");
  });

  it("есть открытое предложение или отклонил врач — пометки нет", () => {
    expect(
      staleNotice([
        prescription("p", "rejected", "2026-10-19T09:00:00Z", "профиль изменился"),
        prescription("q", "proposed", "2026-10-19T10:00:00Z"),
      ]),
    ).toBeNull();
    expect(
      staleNotice([prescription("p", "rejected", "2026-10-19T09:00:00Z", "не нужно")]),
    ).toBeNull();
  });
});
