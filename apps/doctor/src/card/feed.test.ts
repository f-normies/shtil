import { describe, expect, test } from "vitest";
import { buildFeed, feedByDay } from "./feed";
import { alert, assessment, DOCTOR, proposal } from "./fixtures";

describe("buildFeed", () => {
  test("merges scales, signals and proposals newest first", () => {
    const feed = buildFeed({
      assessments: [assessment({ completed_at: "2026-10-17T09:00:00Z" })],
      alerts: [
        alert({
          title: "Мысли о смерти",
          created_at: "2026-10-18T08:12:00Z",
          acknowledged_at: "2026-10-18T08:20:00Z",
          acknowledged_by: DOCTOR,
        }),
      ],
      proposals: [
        proposal({
          created_at: "2026-10-18T08:13:00Z",
          payload: { category: "side_effect" },
          status: "rejected",
          decided_at: "2026-10-18T08:30:00Z",
          decided_by: "someone-else",
          decision_comment: "уже обсуждали",
        }),
      ],
      currentDoctorId: DOCTOR,
    });

    expect(feed.map((e) => [e.at, e.text])).toEqual([
      ["2026-10-18T08:30:00Z", "Отклонено другим врачом: уже обсуждали"],
      ["2026-10-18T08:20:00Z", "Сигнал взят в работу вами"],
      ["2026-10-18T08:13:00Z", "Предложение: побочный эффект"],
      ["2026-10-18T08:12:00Z", "Сигнал: Мысли о смерти"],
      ["2026-10-17T09:00:00Z", "Тестовая шкала настроения: 10, Умеренная"],
    ]);
    // Вид события выбирает значок строки ленты (кадр «Врач v4 · 05»).
    expect(feed.map((e) => e.kind)).toEqual([
      "proposal",
      "alert",
      "proposal",
      "alert",
      "assessment",
    ]);
  });

  // Спека #95, раздел 5: отсрочка и решение по сигналу видны в ленте карточки.
  test("shows snooze and decision of a signal", () => {
    const action = {
      actor_id: DOCTOR,
      decision_id: null,
      decision_text: null,
      reason: null,
      snoozed_until: null,
    };
    const feed = buildFeed({
      assessments: [],
      alerts: [
        alert({
          title: "Вероятная депрессия",
          created_at: "2026-10-18T08:12:00Z",
          actions: [
            {
              ...action,
              action: "snooze",
              at: "2026-10-18T09:00:00Z",
              snoozed_until: "2026-10-25",
            },
            {
              ...action,
              action: "resolve",
              at: "2026-10-25T09:00:00Z",
              decision_text: "Риск не высокий",
              reason: "Связались",
            },
          ],
        }),
      ],
      proposals: [],
      currentDoctorId: DOCTOR,
    });

    expect(feed.map((e) => e.text)).toEqual([
      "Решение по сигналу вами: Риск не высокий. Связались",
      "Сигнал отложен вами до 25.10.2026",
      "Сигнал: Вероятная депрессия",
    ]);
  });

  test("names a confirmed proposal of an unknown kind by its kind", () => {
    const feed = buildFeed({
      assessments: [],
      alerts: [],
      proposals: [
        proposal({
          kind: "therapy.prescribe",
          payload: {},
          status: "confirmed",
          decided_at: "2026-10-18T09:00:00Z",
          decided_by: DOCTOR,
        }),
      ],
      currentDoctorId: DOCTOR,
    });

    expect(feed.map((e) => e.text)).toEqual([
      "Подтверждено вами",
      "Предложение: therapy.prescribe",
    ]);
  });

  test("keeps the 20 latest events", () => {
    const assessments = Array.from({ length: 25 }, (_, i) =>
      assessment({
        id: `r${i}`,
        completed_at: `2026-10-${String(i + 1).padStart(2, "0")}T09:00:00Z`,
      }),
    );

    const feed = buildFeed({ assessments, alerts: [], proposals: [], currentDoctorId: DOCTOR });

    expect(feed).toHaveLength(20);
    expect(feed[0]?.at).toBe("2026-10-25T09:00:00Z");
  });
});

describe("feedByDay: лента по дням, новые выше (кадр «Врач v4 · 05»)", () => {
  const NOW = new Date("2026-10-19T12:00:00");
  const ev = (key: string, at: string) => ({ key, kind: "alert" as const, at, text: key });

  test("сегодня, вчера и раньше — с днём недели", () => {
    const days = feedByDay(
      [
        ev("a", "2026-10-19T09:14:00"),
        ev("b", "2026-10-19T09:12:00"),
        ev("c", "2026-10-18T21:30:00"),
        ev("d", "2026-10-14T20:00:00"),
      ],
      NOW,
    );
    expect(days.map((d) => d.title)).toEqual([
      "Сегодня, 19 октября",
      "Вчера, 18 октября",
      "14 октября, среда",
    ]);
    expect(days[0]?.events.map((e) => e.key)).toEqual(["a", "b"]);
  });

  test("пустая лента — нет дней", () => {
    expect(feedByDay([], NOW)).toEqual([]);
  });
});
