import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import { accessText, bannerText, noDeputyText, periodText, stepsText } from "./escalations";

type Escalation = Schemas["EscalationOut"];

const LEAD = "11111111-1111-1111-1111-111111111111";
const DEPUTY = "22222222-2222-2222-2222-222222222222";
const SINCE = "2026-10-22T09:00:00+03:00";

function escalation(overrides: Partial<Escalation> = {}): Escalation {
  return {
    alert_id: "0192aaaa-bbbb-7000-8000-000000000001",
    number: "0192aaaa",
    urgent_since: SINCE,
    doctor_id: LEAD,
    deputy_id: null,
    step: 3,
    steps: [
      { step: 1, at: "2026-10-22T09:15:00+03:00", recipient_id: LEAD },
      { step: 2, at: "2026-10-22T09:30:00+03:00", recipient_id: null },
      { step: 3, at: "2026-10-22T09:30:00+03:00", recipient_id: null },
    ],
    acknowledged_at: null,
    acknowledged_by: null,
    resolved_at: null,
    access: null,
    reviewed_at: null,
    reviewed_by: null,
    names: { [LEAD]: "Соколова Ирина Петровна", [DEPUTY]: "Громов Андрей Николаевич" },
    ...overrides,
  };
}

describe("заместители (спека #56, раздел 6)", () => {
  test.each([
    [0, null],
    [1, "У 1 врача нет заместителя на сегодня"],
    [2, "У 2 врачей нет заместителя на сегодня"],
    [5, "У 5 врачей нет заместителя на сегодня"],
    [21, "У 21 врача нет заместителя на сегодня"],
  ])("%i без заместителя", (n, text) => {
    expect(noDeputyText(n)).toBe(text);
  });

  test("период с обеими датами", () => {
    expect(periodText("2026-10-20", "2026-10-26")).toBe("20.10–26.10");
  });
});

describe("эскалации", () => {
  test("шаги по времени", () => {
    expect(stepsText(escalation())).toEqual([
      "09:15 — повтор врачу",
      "09:30 — заместитель не назначен",
      "09:30 — сообщено администратору",
    ]);
  });

  test("шаг 2 с заместителем называет его", () => {
    const e = escalation({
      deputy_id: DEPUTY,
      step: 2,
      steps: [{ step: 2, at: "2026-10-22T09:30:00+03:00", recipient_id: DEPUTY }],
    });
    expect(stepsText(e)).toEqual(["09:30 — заместителю: Громов А. Н."]);
  });

  test.each([
    [null, "—"],
    [
      {
        doctor_id: DEPUTY,
        granted_at: "2026-10-22T09:30:00+03:00",
        expires_at: "2026-10-22T21:30:00+03:00",
        ended_at: null,
        end_reason: null,
      },
      "с 09:30 до 21:30",
    ],
    [
      {
        doctor_id: DEPUTY,
        granted_at: "2026-10-22T09:30:00+03:00",
        expires_at: "2026-10-22T21:30:00+03:00",
        ended_at: "2026-10-22T09:50:00+03:00",
        end_reason: "resolved",
      },
      "с 09:30, закрыт в 09:50: по решению сигнала",
    ],
  ] as const)("доступ %#", (access, text) => {
    const now = new Date("2026-10-22T10:00:00+03:00");
    expect(accessText(escalation({ access }), now)).toBe(text);
  });

  test("плашка без данных пациента", () => {
    expect(bannerText(escalation())).toBe(
      "Срочный сигнал не взят 30 мин, ведущий врач — Соколова И. П.",
    );
  });
});
