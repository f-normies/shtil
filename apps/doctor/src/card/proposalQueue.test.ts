import { describe, expect, test } from "vitest";
import { queueItem as item, OTHER_PATIENT, PATIENT } from "./fixtures";
import {
  decidedElsewhere,
  decisionError,
  decisionNotice,
  groupByPatient,
  nextAfterDecision,
  positionOf,
  progress,
  type QueueItem,
  relatedProposals,
  selectionAfterDecision,
  startOfToday,
  statusOf,
} from "./proposalQueue";

/** Очередь врача (спека #96, 3.4 и 3.5): группы, прогресс, выбор после решения, ошибки. */

const ERSHOV = { patient_id: OTHER_PATIENT, patient_name: "Ершов Павел Ильич" };

describe("groupByPatient", () => {
  test("groups proposals by patient in list order and counts them", () => {
    const groups = groupByPatient([
      item({ id: "e1", ...ERSHOV }),
      item({ id: "l1" }),
      item({ id: "e2", ...ERSHOV }),
    ]);

    expect(groups.map((g) => [g.patientId, g.name, g.items.map((i) => i.id)])).toEqual([
      [OTHER_PATIENT, "Ершов Павел Ильич", ["e1", "e2"]],
      [PATIENT, "Лебедева Марина Викторовна", ["l1"]],
    ]);
  });

  test("names a patient without a name in the response", () => {
    expect(groupByPatient([item({ patient_name: undefined })])[0]?.name).toBe("Пациент");
  });
});

describe("positionOf", () => {
  test("numbers a proposal within its patient", () => {
    const groups = groupByPatient([item({ id: "a" }), item({ id: "b" })]);

    expect(positionOf(groups, "b")).toEqual({ index: 2, total: 2 });
    expect(positionOf(groups, "x")).toBeNull();
  });
});

describe("progress", () => {
  test("decided today out of decided and pending", () => {
    expect(progress({ pending: 5, decided_today: 3 })).toEqual({
      decided: 3,
      total: 8,
      text: "3 из 8",
      ratio: 3 / 8,
    });
  });

  test("nothing to decide is an empty bar", () => {
    expect(progress({ pending: 0, decided_today: 0 })).toEqual({
      decided: 0,
      total: 0,
      text: "0 из 0",
      ratio: 0,
    });
  });
});

test("startOfToday is local midnight of the browser", () => {
  const now = new Date(2026, 9, 23, 15, 40);

  expect(startOfToday(now)).toBe(new Date(2026, 9, 23).toISOString());
});

describe("nextAfterDecision", () => {
  const groups = groupByPatient([
    item({ id: "e1", ...ERSHOV }),
    item({ id: "e2", ...ERSHOV }),
    item({ id: "e3", ...ERSHOV }),
    item({ id: "l1" }),
    item({ id: "l2" }),
  ]);

  test.each([
    ["e1", "e2"],
    // Последнее у пациента: сначала его же предложения выше, затем следующий пациент.
    ["e3", "e1"],
    ["l1", "l2"],
    ["l2", "l1"],
  ])("after %s selects %s of the same patient", (decided, next) => {
    expect(nextAfterDecision(groups, decided)).toBe(next);
  });

  test("moves to the next patient when the patient has nothing left", () => {
    const one = groupByPatient([item({ id: "e1", ...ERSHOV }), item({ id: "l1" })]);

    expect(nextAfterDecision(one, "e1")).toBe("l1");
    expect(nextAfterDecision(one, "l1")).toBe("e1");
  });

  test("nothing is selected after the last proposal", () => {
    expect(nextAfterDecision(groupByPatient([item({ id: "only" })]), "only")).toBeNull();
    expect(nextAfterDecision(groups, "missing")).toBeNull();
  });
});

describe("relatedProposals", () => {
  test("findings of the same analysis", () => {
    const list = [
      item({ id: "a", payload: { analysis_id: "an1" } }),
      item({ id: "b", payload: { analysis_id: "an1" } }),
      item({ id: "c", payload: { analysis_id: "an2" } }),
    ];

    expect(relatedProposals(list[0] as QueueItem, list).map((p) => p.id)).toEqual(["b"]);
  });

  test("case history facts of the same document", () => {
    const list = [
      item({ id: "a", kind: "ingestion.diagnosis", payload: { document_id: "d1" } }),
      item({ id: "b", kind: "ingestion.fact", payload: { document_id: "d1" } }),
      item({ id: "c", kind: "ingestion.fact", payload: { document_id: "d2" } }),
      item({ id: "d", kind: "freetext.finding", payload: { document_id: "d1" } }),
    ];

    expect(relatedProposals(list[0] as QueueItem, list).map((p) => p.id)).toEqual(["b"]);
  });

  test("other kinds have no related proposals", () => {
    const list = [
      item({ id: "a", kind: "therapy.prescription", payload: {} }),
      item({ id: "b", kind: "therapy.prescription", payload: {} }),
    ];

    expect(relatedProposals(list[0] as QueueItem, list)).toEqual([]);
  });
});

describe("decisionError", () => {
  test("already decided elsewhere: the row leaves with a notice", () => {
    expect(decisionError({ detail: "Proposal is already decided" })).toEqual({
      text: "Предложение уже рассмотрено",
      gone: true,
    });
  });

  test("a kind the system cannot apply keeps the proposal with a message", () => {
    expect(decisionError({ detail: "Proposal kind cannot be applied" })).toEqual({
      text: "Система не умеет применять предложения этого вида, решение не сохранено",
      gone: false,
    });
  });

  test("a refusal of the applier (422) is shown as is", () => {
    expect(decisionError({ status: 422, detail: "Препарат уже есть в карточке" })).toEqual({
      text: "Препарат уже есть в карточке",
      gone: false,
    });
  });

  test("a validation error (422 with a list) and a network error get a generic text", () => {
    const generic = {
      text: "Решение не сохранено, проверьте связь и попробуйте ещё раз",
      gone: false,
    };
    expect(decisionError({ status: 422, detail: [{ msg: "x" }] })).toEqual(generic);
    expect(decisionError(new TypeError("Failed to fetch"))).toEqual(generic);
  });
});

describe("decidedElsewhere", () => {
  test.each([
    ["therapy.prescription", "therapy"],
    ["therapy.stop", "therapy"],
    ["alerts.referral", "signal"],
    ["ingestion.diagnosis", "documents"],
    ["freetext.finding", null],
  ])("%s is decided on %s", (kind, place) => {
    expect(decidedElsewhere(item({ kind, payload: { document_id: "d1" } }))).toBe(place);
  });
});

test.each([
  ["confirmed", "confirmed"],
  ["rejected", "rejected"],
  [undefined, "proposed"],
  ["all", "proposed"],
])("status %s from the address is %s", (value, status) => {
  expect(statusOf(value)).toBe(status);
});

// Пациента передали другому врачу, пока предложение было открыто: строка уходит, как при 409.
test.each([
  [{ status: 404, title: "Not Found", detail: "Proposal not found" }],
  [{ detail: "Proposal not found" }],
])("a proposal that is no longer available leaves the list (%o)", (error) => {
  expect(decisionError(error)).toEqual({ text: "Предложение больше недоступно", gone: true });
});

describe("selectionAfterDecision", () => {
  const groups = groupByPatient([
    item({ id: "e1", ...ERSHOV }),
    item({ id: "e2", ...ERSHOV }),
    item({ id: "l1" }),
  ]);

  test("the decided proposal is still selected: select the next one", () => {
    expect(selectionAfterDecision(groups, "e1", "e1")).toEqual({ selectedId: "e2", moved: true });
  });

  // Врач выбрал другую строку, пока решение было в обработке: выбор не перескакивает.
  test("the doctor has selected another proposal meanwhile: keep it", () => {
    expect(selectionAfterDecision(groups, "l1", "e1")).toEqual({ selectedId: "l1", moved: false });
  });

  test("nothing is left after the last proposal", () => {
    const one = groupByPatient([item({ id: "only" })]);

    expect(selectionAfterDecision(one, "only", "only")).toEqual({ selectedId: null, moved: true });
  });
});

describe("decisionNotice", () => {
  test.each([
    ["confirmed", "Подтверждено: Побочный эффект"],
    ["rejected", "Отклонено: Побочный эффект"],
  ] as const)("a %s proposal is announced, the screen shows the next one", (kind, text) => {
    expect(decisionNotice({ kind }, "Побочный эффект", true)).toEqual({ text, visible: false });
  });

  test("a proposal that left the list is shown in words", () => {
    const gone = { kind: "gone", text: "Предложение уже рассмотрено" } as const;

    expect(decisionNotice(gone, "Симптом", true)).toEqual({
      text: "Предложение уже рассмотрено",
      visible: true,
    });
    // Врач уже смотрит другое предложение: сообщение только для чтения с экрана.
    expect(decisionNotice(gone, "Симптом", false)).toEqual({
      text: "Предложение уже рассмотрено",
      visible: false,
    });
  });
});
