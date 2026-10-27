import { describe, expect, test } from "vitest";
import { alert, OTHER_PATIENT, PATIENT, proposal } from "./fixtures";
import { proposalTitle, proposalView } from "./proposals";
import { arrivedMandatory, canSnooze, declineHint, isReferral, routingSignals } from "./referral";

describe("routingSignals", () => {
  // Спека #35: лист показывает все нерешённые сигналы маршрутизации пациента.
  test("keeps unresolved routing signals of the patient, mandatory first", () => {
    const signals = [
      alert({ id: "desirable", kind: "routing", level: "desirable" }),
      alert({ id: "risk", kind: "alert" }),
      alert({ id: "mandatory", kind: "routing" }),
      alert({ id: "snoozed", kind: "routing", level: "desirable", state: "snoozed" }),
      alert({ id: "resolved", kind: "routing", state: "resolved" }),
      alert({ id: "other", kind: "routing", patient_id: OTHER_PATIENT }),
    ];
    expect(routingSignals(signals, PATIENT).map((a) => a.id)).toEqual([
      "mandatory",
      "desirable",
      "snoozed",
    ]);
  });
});

test("a mandatory routing signal that arrived after the sheet opened", () => {
  const seen = [alert({ id: "a", kind: "routing", level: "desirable" })];
  const now = [...seen, alert({ id: "b", kind: "routing" })];
  expect(arrivedMandatory(["a"], now).map((a) => a.id)).toEqual(["b"]);
  expect(arrivedMandatory(["a", "b"], now)).toEqual([]);
});

// Кадр 11в: подсказка под неактивной кнопкой; комментарий обязателен всегда (#93).
test.each([
  [null, "", "Выберите причину"],
  ["other", "  ", "Напишите комментарий"],
  ["patient-declined", "", "Напишите комментарий"],
  ["patient-declined", "Не готов", null],
])("decline hint %s / %j", (reason, comment, expected) => {
  expect(declineHint(reason, comment)).toBe(expected);
});

test("referral proposal is named and summarised by its signal", () => {
  const referral = proposal({
    kind: "alerts.referral",
    origin_type: "rule",
    payload: { alert_id: "a1", rule_id: "r", level: "desirable", title: "Вероятная депрессия" },
  });
  expect(isReferral(referral)).toBe(true);
  expect(proposalTitle(referral)).toBe("Направление к психиатру");
  const view = proposalView(referral);
  expect(view.fields).toEqual([]);
  expect(view.summary).toBe("По сигналу: Вероятная депрессия");
});

// Спека #35: «Отложить до…» только если среди сигналов нет «Обязательно».
test("snooze is offered only when every routing signal is desirable", () => {
  const desirable = alert({ kind: "routing", level: "desirable" });
  expect(canSnooze([desirable])).toBe(true);
  expect(canSnooze([desirable, alert({ kind: "routing" })])).toBe(false);
  expect(canSnooze([])).toBe(false);
});
