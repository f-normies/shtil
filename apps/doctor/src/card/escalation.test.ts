import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import { timeline } from "./alerts";
import {
  accessClosedStep,
  bannerNote,
  deputyAccessUntil,
  deputyNote,
  escalationTitle,
} from "./escalation";
import { alert, DOCTOR } from "./fixtures";

type Action = Schemas["ActionOut"];

const DEPUTY = "44444444-4444-4444-4444-444444444444";
const SINCE = "2026-10-22T09:00:00+03:00";
const NAMES = { [DOCTOR]: "Соколова Ирина Петровна", [DEPUTY]: "Громов Андрей Николаевич" };

function escalate(step: 1 | 2 | 3, minutes: number, recipient: string | null): Action {
  const at = new Date(Date.parse(SINCE) + minutes * 60_000).toISOString();
  return {
    at,
    actor_id: null,
    action: "escalate",
    decision_id: null,
    decision_text: null,
    reason: null,
    snoozed_until: null,
    step,
    recipient_id: recipient,
  };
}

function escalated(actions: Action[], extra: Partial<Schemas["AlertOut"]> = {}) {
  return alert({
    urgent_since: SINCE,
    actions,
    actors: NAMES,
    escalation_step: actions.reduce<1 | 2 | 3 | null>((m, a) => a.step ?? m, null),
    ...extra,
  });
}

describe("шаги эскалации в ходе обработки (спека #56, раздел 6)", () => {
  const a = escalated([]);

  test.each([
    [escalate(1, 15, DOCTOR), "Не взят 15 мин — повтор"],
    [escalate(2, 30, DEPUTY), "Передан заместителю: Громов А. Н."],
    [escalate(2, 30, null), "Заместитель не назначен"],
    [escalate(3, 60, null), "Сообщено администратору"],
  ])("%#", (action, title) => {
    expect(escalationTitle(action, a, NAMES)).toBe(title);
  });

  test("минуты — от момента, когда сигнал стал «Обязательно»", () => {
    expect(escalationTitle(escalate(1, 1, DOCTOR), a, NAMES)).toBe("Не взят 1 мин — повтор");
  });

  test("в ходе обработки шаги идут от системы, действия заместителя — с его именем", () => {
    const steps = timeline(
      escalated([
        escalate(1, 15, DOCTOR),
        escalate(2, 30, DEPUTY),
        {
          ...escalate(2, 35, null),
          action: "acknowledge",
          actor_id: DEPUTY,
          step: null,
        },
      ]),
      NAMES,
    );
    const titles = steps.map((s) => [s.title, s.detail]);
    expect(titles).toContainEqual(["Не взят 15 мин — повтор", "система"]);
    expect(titles).toContainEqual(["Передан заместителю: Громов А. Н.", "система"]);
    expect(titles).toContainEqual(["Взят в работу", "Громов Андрей Николаевич"]);
  });
});

describe("доступ заместителя", () => {
  const access = {
    doctor_id: DEPUTY,
    granted_at: "2026-10-22T09:30:00+03:00",
    expires_at: "2026-10-22T21:30:00+03:00",
    ended_at: null,
    end_reason: null,
  } as const;
  const now = new Date("2026-10-22T10:00:00+03:00");

  test("действующий доступ не закрыт", () => {
    expect(accessClosedStep(escalated([], { deputy_access: access }), now)).toBeNull();
  });

  test.each([
    ["resolved", "по решению сигнала"],
    ["lead_took", "ведущий врач занялся сигналом"],
    ["revoked", "назначение отозвано"],
  ] as const)("закрыт: %s", (reason, detail) => {
    const closed = { ...access, ended_at: "2026-10-22T09:50:00+03:00", end_reason: reason };
    expect(accessClosedStep(escalated([], { deputy_access: closed }), now)).toEqual({
      title: "Доступ заместителя закрыт",
      detail,
      at: "2026-10-22T09:50:00+03:00",
      done: true,
    });
  });

  test("истёк по сроку", () => {
    const later = new Date("2026-10-22T22:00:00+03:00");
    expect(accessClosedStep(escalated([], { deputy_access: access }), later)?.detail).toBe(
      "срок 12 часов истёк",
    );
  });

  test("«Доступ по замещению до 21:30» в шапке карточки", () => {
    expect(
      deputyAccessUntil([escalated([], { deputy_access: access, as_deputy: true })], now),
    ).toBe("Доступ по замещению до 21:30");
    expect(deputyAccessUntil([], now)).toBeNull();
  });
});

describe("плашка «Обязательно»", () => {
  test("после шага 1 — сколько не взят, после шага 2 — кому передан", () => {
    expect(bannerNote(escalated([escalate(1, 15, DOCTOR)]))).toBe("не взят 15 мин");
    expect(bannerNote(escalated([escalate(1, 15, DOCTOR), escalate(2, 30, DEPUTY)]))).toBe(
      "передан заместителю: Громов А. Н.",
    );
    expect(bannerNote(escalated([]))).toBeNull();
  });

  test("заместитель видит, чей это пациент", () => {
    expect(deputyNote(escalated([], { as_deputy: true }))).toBe(
      "Замещение, ведущий врач — Соколова И. П.",
    );
    expect(deputyNote(escalated([]))).toBeNull();
  });
});
