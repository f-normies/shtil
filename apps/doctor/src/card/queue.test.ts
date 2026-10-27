import { describe, expect, it } from "vitest";
import { alert, assessment, OTHER_PATIENT, PATIENT, patient, proposal, unread } from "./fixtures";
import { matchesName, patientQueue } from "./queue";

const THIRD = "44444444-4444-4444-4444-444444444444";
const FOURTH = "55555555-5555-5555-5555-555555555555";
const NOW = new Date("2026-10-19T12:00:00Z");
const patients = [
  patient({ id: PATIENT, full_name: "Первая" }),
  patient({ id: OTHER_PATIENT, full_name: "Вторая" }),
  patient({ id: THIRD, full_name: "Третья" }),
  patient({ id: FOURTH, full_name: "Четвёртая" }),
];
const empty = { alerts: [], proposals: [], unread: [], latest: new Map(), now: NOW };
const names = (rows: { patient: { full_name: string } }[]) => rows.map((r) => r.patient.full_name);

describe("patientQueue: группа «Требуют внимания» (спека #13, 10.2)", () => {
  it("порядок: «Обязательно», «Желательно», предложения, непрочитанные", () => {
    const q = patientQueue({
      ...empty,
      patients,
      alerts: [
        alert({ id: "d", patient_id: OTHER_PATIENT, level: "desirable" }),
        alert({ id: "m", patient_id: THIRD, level: "mandatory" }),
      ],
      proposals: [proposal({ patient_id: FOURTH })],
      unread: [unread({ patient_id: PATIENT })],
    });
    expect(names(q.attention)).toEqual(["Третья", "Вторая", "Четвёртая", "Первая"]);
    expect(q.attention.map((r) => r.kind)).toEqual([
      "mandatory",
      "desirable",
      "proposal",
      "unread",
    ]);
    expect(q.rest).toEqual([]);
  });

  it("внутри одной категории новые выше", () => {
    const q = patientQueue({
      ...empty,
      patients,
      alerts: [
        alert({ id: "old", patient_id: PATIENT, last_triggered_at: "2026-10-18T08:00:00Z" }),
        alert({ id: "new", patient_id: OTHER_PATIENT, last_triggered_at: "2026-10-19T08:00:00Z" }),
      ],
    });
    expect(names(q.attention)).toEqual(["Вторая", "Первая"]);
  });

  it("сигнал в работе держит пациента в группе, отложенный — нет", () => {
    const q = patientQueue({
      ...empty,
      patients,
      alerts: [
        alert({ id: "w", patient_id: PATIENT, state: "in_work" }),
        alert({
          id: "s",
          patient_id: OTHER_PATIENT,
          state: "snoozed",
          snoozed_until: "2026-10-25",
        }),
      ],
    });
    expect(names(q.attention)).toEqual(["Первая"]);
    expect(names(q.rest)).toContain("Вторая");
  });

  it("решённое предложение не переводит пациента в группу", () => {
    const q = patientQueue({
      ...empty,
      patients,
      proposals: [proposal({ patient_id: PATIENT, status: "confirmed" })],
    });
    expect(q.attention).toEqual([]);
  });

  it("одна причина: заголовок самого важного сигнала", () => {
    const q = patientQueue({
      ...empty,
      patients,
      alerts: [alert({ patient_id: PATIENT, title: "Мысли о смерти или самоповреждении" })],
    });
    expect(q.attention[0]?.reason).toBe("Мысли о смерти или самоповреждении");
  });

  it("несколько сигналов: число и самый важный", () => {
    const q = patientQueue({
      ...empty,
      patients,
      alerts: [
        alert({ id: "1", patient_id: PATIENT, level: "desirable", title: "Рост PHQ-9" }),
        alert({
          id: "2",
          patient_id: PATIENT,
          level: "mandatory",
          title: "Консультация психиатра",
        }),
      ],
    });
    expect(q.attention[0]?.reason).toBe("2 сигнала, консультация психиатра");
    expect(q.attention[0]?.kind).toBe("mandatory");
  });

  it.each([
    [1, "1 предложение ждёт решения"],
    [2, "2 предложения ждут решения"],
    [5, "5 предложений ждут решения"],
  ])("только предложения: %i → «%s»", (n, text) => {
    const q = patientQueue({
      ...empty,
      patients,
      proposals: Array.from({ length: n }, (_, i) => proposal({ id: `p${i}` })),
    });
    expect(q.attention[0]?.reason).toBe(text);
  });

  it("только непрочитанные: число сообщений", () => {
    const q = patientQueue({ ...empty, patients, unread: [unread({ unread_count: 3 })] });
    expect(q.attention[0]?.reason).toBe("3 новых сообщения");
  });

  it("время строки — по событию причины", () => {
    const q = patientQueue({
      ...empty,
      patients,
      alerts: [alert({ last_triggered_at: "2026-10-19T09:12:00Z" })],
    });
    expect(q.attention[0]?.at).toBe("2026-10-19T09:12:00Z");
  });
});

describe("patientQueue: группа «Остальные»", () => {
  it("по дате последних данных, новые выше; без данных — по дате добавления", () => {
    const q = patientQueue({
      ...empty,
      patients,
      latest: new Map([
        [PATIENT, [assessment({ completed_at: "2026-10-10T09:00:00Z" })]],
        [OTHER_PATIENT, [assessment({ completed_at: "2026-10-18T09:00:00Z" })]],
      ]),
    });
    expect(names(q.rest)).toEqual(["Вторая", "Первая", "Третья", "Четвёртая"]);
  });

  it("вторая строка — последние баллы шкал", () => {
    const q = patientQueue({
      ...empty,
      patients: patients.slice(0, 1),
      latest: new Map([
        [
          PATIENT,
          [
            assessment({
              id: "a",
              scale_id: "phq9",
              scale_title: "PHQ-9",
              completed_at: "2026-10-18T09:00:00Z",
            }),
            assessment({
              id: "b",
              scale_id: "gad7",
              scale_title: "GAD-7",
              completed_at: "2026-10-18T09:00:00Z",
            }),
            assessment({
              id: "c",
              scale_id: "phq9",
              scale_title: "PHQ-9",
              completed_at: "2026-10-01T09:00:00Z",
            }),
          ],
        ],
      ]),
    });
    expect(q.rest[0]?.reason).toBe("PHQ-9 10, GAD-7 10");
    expect(q.rest[0]?.kind).toBe("none");
  });

  it("имя шкалы — короткое, из скобок названия", () => {
    const q = patientQueue({
      ...empty,
      patients: patients.slice(0, 1),
      latest: new Map([
        [PATIENT, [assessment({ scale_title: "Опросник здоровья пациента (PHQ-9)" })]],
      ]),
    });
    expect(q.rest[0]?.reason).toBe("PHQ-9 10");
  });

  it("без данных — «Данных пока нет»", () => {
    const q = patientQueue({ ...empty, patients: patients.slice(0, 1) });
    expect(q.rest[0]?.reason).toBe("Данных пока нет");
  });
});

describe("patientQueue: отозванное согласие (спека #112, 3.7; кадр «Врач v4 · 02в»)", () => {
  const revoked = patient({
    id: PATIENT,
    full_name: "Первая",
    consent_revoked_at: "2026-10-18T12:00:00Z",
  });

  it("без сигналов и предложений: в «Остальных», вторая строка — отзыв и дата", () => {
    const q = patientQueue({
      ...empty,
      patients: [revoked],
      latest: new Map([[PATIENT, [assessment({ completed_at: "2026-10-10T09:00:00Z" })]]]),
    });
    expect(q.rest[0]?.reason).toBe("Согласие отозвано 18.10");
    expect(q.rest[0]?.at).toBe("2026-10-18T12:00:00Z");
  });

  it("открытый сигнал отзыв не снимает: пациент в «Требуют внимания» с сигналом", () => {
    const q = patientQueue({
      ...empty,
      patients: [revoked],
      alerts: [alert({ patient_id: PATIENT, title: "Рост PHQ-9" })],
    });
    expect(q.attention[0]?.reason).toBe("Рост PHQ-9");
  });
});

describe("matchesName: поиск по части ФИО (кадр «Врач v4 · 02»)", () => {
  it.each([
    ["Лебедева Марина Викторовна", "мари", true],
    ["Лебедева Марина Викторовна", "ЛЕБЕД", true],
    ["Фёдорова Анна", "федор", true],
    ["Федорова Анна", "фёдор", true],
    ["Лебедева Марина", "котов", false],
    ["Лебедева Марина", "  ", true],
  ])("%s по «%s» → %s", (name, query, found) => {
    expect(matchesName(name, query)).toBe(found);
  });
});
