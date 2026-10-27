import { describe, expect, test } from "vitest";
import { alert, OTHER_PATIENT, PATIENT, trigger } from "./fixtures";
import { bannerRows, bannerSignals, needsDecision, openSignals, signalView } from "./signals";

describe("openSignals", () => {
  // Спека #95, раздел 3: плашка и полоса — только новые; взятые, отложенные и решённые — нет.
  test("keeps only new signals", () => {
    const signals = [
      alert({ id: "new" }),
      alert({ id: "taken", state: "in_work", acknowledged_at: "2026-10-18T09:00:00Z" }),
      alert({ id: "snoozed", state: "snoozed", snoozed_until: "2026-10-29" }),
      alert({ id: "resolved", state: "resolved" }),
    ];

    expect(openSignals(signals).map((a) => a.id)).toEqual(["new"]);
  });

  test("keeps an in-work mandatory signal that fired again after the doctor's last action", () => {
    // Ревью #95: повторное срабатывание у сигнала «в работе» не должно пропадать из полосы.
    const taken = { state: "in_work" as const, acknowledged_at: "2026-10-18T09:00:00Z" };
    const signals = [
      alert({ id: "refired", ...taken, last_triggered_at: "2026-10-19T09:00:00Z" }),
      alert({ id: "seen", ...taken, last_triggered_at: "2026-10-18T08:00:00Z" }),
      alert({
        id: "desirable",
        ...taken,
        level: "desirable",
        last_triggered_at: "2026-10-19T09:00:00Z",
      }),
    ];

    expect(openSignals(signals).map((a) => a.id)).toEqual(["refired"]);
  });

  test("puts mandatory first, newest first within a level", () => {
    const signals = [
      alert({ id: "desirable-new", level: "desirable", last_triggered_at: "2026-10-18T10:00:00Z" }),
      alert({ id: "mandatory-old", level: "mandatory", last_triggered_at: "2026-10-17T10:00:00Z" }),
      alert({ id: "mandatory-new", level: "mandatory", last_triggered_at: "2026-10-18T09:00:00Z" }),
    ];

    expect(openSignals(signals).map((a) => a.id)).toEqual([
      "mandatory-new",
      "mandatory-old",
      "desirable-new",
    ]);
  });
});

describe("signalView", () => {
  test("a therapy safety signal is named as such", () => {
    expect(signalView(alert({ kind: "safety" })).kindLabel).toBe("Безопасность терапии");
  });

  test("a therapy safety signal shows the data it fired on", () => {
    // Ревью #76: врач не видел, по каким данным сработало правило безопасности.
    const evidence = {
      prescription_id: "p1",
      detail: "Аурорикс (моклобемид): отменён 08.10.2026; можно назначать с 23.10.2026",
    };
    const view = signalView(alert({ kind: "safety", triggers: [trigger({ evidence })] }));

    expect(view.detail).toBe(evidence.detail);
    expect(view.values).toEqual([]);
  });

  test("signals without a detail show none", () => {
    expect(signalView(alert({ triggers: [trigger({ evidence: { x: { y: 1 } } })] })).detail).toBe(
      null,
    );
  });

  test("names level and origin in words", () => {
    const view = signalView(alert({ level: "desirable", kind: "routing", origin_type: "rule" }));

    expect([view.levelLabel, view.kindLabel, view.originLabel]).toEqual([
      "Желательно",
      "Маршрутизация",
      "по правилу",
    ]);
  });

  test("shows quotes of the latest AI firing and marks quotes missing from the text", () => {
    const view = signalView(
      alert({
        origin_type: "ai_run",
        rule_id: null,
        triggers: [
          trigger({ evidence: { signs: [{ kind: "k", quote: "старая", quote_found: true }] } }),
          trigger({
            at: "2026-10-18T09:00:00Z",
            evidence: {
              source_type: "diary",
              transcript: true,
              signs: [
                { kind: "k", quote: "не хочу жить", quote_found: true },
                { kind: "k", quote: "выдумано", quote_found: false },
              ],
              sources: {
                k: [
                  {
                    source: "kr",
                    locator: "раздел 2.5",
                    title: "КР «Депрессия»",
                    url: "https://cr",
                  },
                ],
                other: [
                  {
                    source: "kr",
                    locator: "раздел 2.5",
                    title: "КР «Депрессия»",
                    url: "https://cr",
                  },
                  { source: "old", locator: "с. 1" },
                ],
              },
            },
          }),
        ],
      }),
    );

    expect(view.originLabel).toBe("по тексту, AI");
    expect(view.quotes).toEqual([
      { text: "не хочу жить", found: true },
      { text: "выдумано", found: false },
    ]);
    expect(view.recordLabel).toBe("голосовая запись");
    expect(view.firedAt).toBe("2026-10-18T09:00:00Z");
    // Одинаковая ссылка у двух видов признаков показывается один раз. Снимок до #13 —
    // без названия: показываем id документа.
    expect(view.sources).toEqual([
      { source: "kr", title: "КР «Депрессия»", locator: "раздел 2.5", url: "https://cr" },
      { source: "old", title: "old", locator: "с. 1" },
    ]);
  });

  test("shows a quote found by two signs once", () => {
    const view = signalView(
      alert({
        origin_type: "ai_run",
        triggers: [
          trigger({
            evidence: {
              signs: [
                { kind: "thoughts", quote: "не хочу жить", quote_found: true },
                { kind: "intent", quote: "не хочу жить", quote_found: true },
              ],
            },
          }),
        ],
      }),
    );

    expect(view.quotes).toEqual([{ text: "не хочу жить", found: true }]);
  });

  test.each([
    [{ source_type: "diary" }, "дневник"],
    [{ source_type: "message" }, "сообщение"],
    [{ source_type: "message", transcript: true }, "голосовая запись"],
    [{ source_type: "diary", transcript: false }, "дневник"],
  ])("labels the record %o as %s", (evidence, label) => {
    const view = signalView(alert({ origin_type: "ai_run", triggers: [trigger({ evidence })] }));

    expect(view.recordLabel).toBe(label);
  });

  test("shows values and documents of a rule signal", () => {
    const view = signalView(
      alert({
        sources: [
          { source: "kr", locator: "раздел 3", title: "КР «Депрессия»", url: "https://cr.example" },
        ],
        triggers: [trigger({ evidence: { "test-mood": { q9: 2, total: 18 } } })],
      }),
    );

    expect(view.quotes).toEqual([]);
    expect(view.values).toEqual([
      { scale: "test-mood", name: "q9", value: 2 },
      { scale: "test-mood", name: "total", value: 18 },
    ]);
    expect(view.sources).toEqual([
      { source: "kr", title: "КР «Депрессия»", locator: "раздел 3", url: "https://cr.example" },
    ]);
  });
});

describe("bannerSignals", () => {
  const signals = [
    alert({ id: "here", patient_id: PATIENT }),
    alert({ id: "there", patient_id: OTHER_PATIENT }),
  ];

  test("leaves out signals of the patient on the overview of his card", () => {
    const page = { routeId: "/_doctor/patients/$patientId/", params: { patientId: PATIENT } };

    expect(bannerSignals(signals, page).map((a) => a.id)).toEqual(["there"]);
  });

  // Полоса открытых сигналов есть только на обзоре. На других страницах карточки
  // сигнал пациента без плашки врач бы не увидел.
  test.each([
    "/_doctor/patients/$patientId/proposals",
    "/_doctor/patients/$patientId/therapy",
    "/_doctor/patients/$patientId/scales/$scaleId",
  ])("keeps signals of the patient on %s", (routeId) => {
    const page = { routeId, params: { patientId: PATIENT } };

    expect(bannerSignals(signals, page).map((a) => a.id)).toEqual(["here", "there"]);
  });

  // На экране «Сигналы» новые сигналы и так в списке слева: плашка их не повторяет.
  test.each(["/_doctor/alerts/", "/_doctor/alerts/$alertId"])("is empty on %s", (routeId) => {
    expect(bannerSignals(signals, { routeId, params: {} })).toEqual([]);
  });

  // Спека #13, раздел 10.8: плашка — пока открыт хотя бы один сигнал «Обязательно».
  // «Желательно» виден по счётчику меню и в «Требуют внимания».
  test("keeps only mandatory signals", () => {
    const mixed = [alert({ id: "m" }), alert({ id: "d", level: "desirable" })];

    expect(bannerSignals(mixed, undefined).map((a) => a.id)).toEqual(["m"]);
  });

  test("keeps all signals outside patient cards", () => {
    expect(bannerSignals(signals, { routeId: "/_doctor/", params: {} }).map((a) => a.id)).toEqual([
      "here",
      "there",
    ]);
    expect(bannerSignals(signals, undefined).map((a) => a.id)).toEqual(["here", "there"]);
  });
});

describe("needsDecision", () => {
  // Спека #13, раздел 10.3, и #99: сигнал «в работе» требует решения, пока его нет.
  // Отложенный ждёт даты возврата, решённый закрыт.
  test("keeps new and in-work signals, mandatory first, newest first", () => {
    const signals = [
      alert({ id: "old-d", level: "desirable", last_triggered_at: "2026-10-17T08:00:00Z" }),
      alert({ id: "work-m", state: "in_work", last_triggered_at: "2026-10-16T08:00:00Z" }),
      alert({ id: "new-m", last_triggered_at: "2026-10-18T08:00:00Z" }),
      alert({ id: "snoozed", state: "snoozed", snoozed_until: "2026-10-29" }),
      alert({ id: "resolved", state: "resolved" }),
    ];

    expect(needsDecision(signals).map((a) => a.id)).toEqual(["new-m", "work-m", "old-d"]);
  });
});

describe("bannerRows: не больше трёх строк, остальное — «Ещё N» (кадр 12)", () => {
  const make = (id: string) => ({ id }) as Parameters<typeof bannerRows>[0][number];
  test.each([
    [0, 0, 0],
    [3, 3, 0],
    [4, 3, 1],
    [7, 3, 4],
  ])("%d сигналов → %d строк, ещё %d", (count, shown, rest) => {
    const rows = bannerRows(Array.from({ length: count }, (_, i) => make(`s${i}`)));
    expect(rows.shown).toHaveLength(shown);
    expect(rows.rest).toBe(rest);
  });
});
