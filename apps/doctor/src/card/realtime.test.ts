import { describe, expect, test } from "vitest";
import { onClose, onMessage, reconnectDelay } from "./realtime";

describe("onMessage", () => {
  test("ready catches up on signals, scales, unread messages and diaries missed while offline", () => {
    expect(onMessage({ type: "ready", data: {} })).toEqual([
      { kind: "connected" },
      { kind: "catchUp" },
      { kind: "refresh", what: "assessments" },
      { kind: "refresh", what: "unread" },
      { kind: "refreshMessaging", eventType: "ready", data: {} },
      { kind: "refreshDiary", patientId: null },
    ]);
  });

  // Спека #115, раздел 4: новая запись, правка, скрытие и смена статуса расшифровки.
  test.each([
    { patient_id: "p1", entry_id: "e1" },
    { patient_id: "p1", entry_id: "e1", audio_status: "done" },
  ])("diary.changed refreshes the diary of that patient: %j", (data) => {
    expect(onMessage({ type: "diary.changed", data })).toEqual([
      { kind: "refreshDiary", patientId: "p1" },
    ]);
  });

  test("diary.changed without a patient is skipped", () => {
    expect(onMessage({ type: "diary.changed", data: { entry_id: "e1" } })).toEqual([]);
  });

  test.each(["alert.created", "alert.raised"])("%s is marked delivered over ws", (type) => {
    // «Обязательно» — ещё и уведомление браузера (спека #56): уровень узнаём после обновления.
    expect(onMessage({ type, data: { alert_id: "a1" } })).toEqual([
      { kind: "markDelivered", alertId: "a1" },
      { kind: "refresh", what: "alerts" },
      { kind: "notifyIfMandatory", alertId: "a1" },
    ]);
  });

  test("alert.escalated refreshes signals and notifies (спека #56)", () => {
    expect(onMessage({ type: "alert.escalated", data: { alert_id: "a1" } })).toEqual([
      { kind: "refresh", what: "alerts" },
      { kind: "notify" },
    ]);
  });

  test("alert.updated refreshes signals without a delivery mark", () => {
    expect(onMessage({ type: "alert.updated", data: { alert_id: "a1" } })).toEqual([
      { kind: "refresh", what: "alerts" },
    ]);
  });

  test("assessment.completed refreshes scale history", () => {
    expect(onMessage({ type: "assessment.completed", data: { assessment_id: "r1" } })).toEqual([
      { kind: "refresh", what: "assessments" },
    ]);
  });

  test.each([
    {
      type: "message.created",
      data: { patient_id: "p1", doctor_id: "d1", message_id: "m1", seq: 1 },
    },
    {
      type: "message.read",
      data: { patient_id: "p1", doctor_id: "d1", reader_id: "d1", through_seq: 1 },
    },
  ])("$type refreshes unread message counts", (event) => {
    expect(onMessage(event)).toEqual([
      { kind: "refresh", what: "unread" },
      { kind: "refreshMessaging", eventType: event.type, data: event.data },
    ]);
  });

  test("message.typing updates the chat status without touching unread counts", () => {
    const data = { patient_id: "p1", doctor_id: "d1", user_id: "p1", typing_until: null };
    expect(onMessage({ type: "message.typing", data })).toEqual([
      { kind: "refreshMessaging", eventType: "message.typing", data },
    ]);
  });

  test("unknown and malformed events are skipped", () => {
    expect(onMessage({ type: "unknown", data: {} })).toEqual([]);
    expect(onMessage({ type: "alert.created", data: {} })).toEqual([]);
    expect(onMessage("not an event")).toEqual([]);
  });
});

describe("onClose", () => {
  test("ended session goes to login", () => {
    expect(onClose({ code: 4401, wasReady: true, attempt: 0 })).toEqual([{ kind: "toLogin" }]);
  });

  test("close before ready checks the session first", () => {
    // Отказ рукопожатия (1008 на сервере) браузер видит как 1006.
    expect(onClose({ code: 1006, wasReady: false, attempt: 2 })).toEqual([
      { kind: "disconnected" },
      { kind: "checkSession", delayMs: 4000 },
    ]);
  });

  test("other closes reconnect with a delay", () => {
    expect(onClose({ code: 1006, wasReady: true, attempt: 0 })).toEqual([
      { kind: "disconnected" },
      { kind: "reconnect", delayMs: 1000 },
    ]);
  });
});

test("reconnect delay doubles up to 30 seconds", () => {
  expect([0, 1, 2, 3, 4, 5, 6, 20].map(reconnectDelay)).toEqual([
    1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000,
  ]);
});
