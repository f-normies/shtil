/**
 * Реакция кабинета на события WebSocket и закрытие соединения (спека #13, раздел 6).
 * Чистые функции: на входе событие, на выходе список действий. Действия выполняет хук.
 */

export type Action =
  | { kind: "connected" }
  | { kind: "disconnected" }
  | { kind: "catchUp" }
  | { kind: "markDelivered"; alertId: string }
  | { kind: "refresh"; what: "alerts" | "assessments" | "unread" }
  | { kind: "refreshMessaging"; eventType: string; data?: Record<string, unknown> }
  // Дневник пациента (спека #115, раздел 4); `null` — всех пациентов после переподключения.
  | { kind: "refreshDiary"; patientId: string | null }
  // Уведомление браузера без данных пациента (спека #56): шаг эскалации или «Обязательно».
  | { kind: "notify" }
  | { kind: "notifyIfMandatory"; alertId: string }
  | { kind: "toLogin" }
  | { kind: "checkSession"; delayMs: number }
  | { kind: "reconnect"; delayMs: number };

/** Код закрытия «сессия завершена» (`core/realtime.py`, SESSION_ENDED). */
export const SESSION_ENDED = 4401;

const MAX_DELAY_MS = 30_000;

export function onMessage(message: unknown): Action[] {
  if (typeof message !== "object" || message === null) {
    return [];
  }
  const { type, data } = message as { type?: unknown; data?: Record<string, unknown> };
  const alertId = typeof data?.alert_id === "string" ? data.alert_id : null;
  const patientId = typeof data?.patient_id === "string" ? data.patient_id : null;
  switch (type) {
    case "ready":
      // Пока связи не было, могли прийти сигналы, результаты шкал, сообщения и записи дневника.
      return [
        { kind: "connected" },
        { kind: "catchUp" },
        { kind: "refresh", what: "assessments" },
        { kind: "refresh", what: "unread" },
        { kind: "refreshMessaging", eventType: "ready", data: data ?? {} },
        { kind: "refreshDiary", patientId: null },
      ];
    case "alert.created":
    case "alert.raised":
      return alertId
        ? [
            { kind: "markDelivered", alertId },
            { kind: "refresh", what: "alerts" },
            { kind: "notifyIfMandatory", alertId },
          ]
        : [];
    case "alert.escalated":
      return alertId ? [{ kind: "refresh", what: "alerts" }, { kind: "notify" }] : [];
    case "alert.updated":
      return alertId ? [{ kind: "refresh", what: "alerts" }] : [];
    case "assessment.completed":
      return [{ kind: "refresh", what: "assessments" }];
    case "message.created":
    case "message.read":
      return [
        { kind: "refresh", what: "unread" },
        { kind: "refreshMessaging", eventType: type, data },
      ];
    case "message.typing":
      // Лента чата показывает «печатает…» из кэша статуса; счётчики не меняются.
      return [{ kind: "refreshMessaging", eventType: type, data }];
    case "diary.changed":
      // Новая запись, правка, скрытие или смена статуса расшифровки у пациента врача.
      return patientId ? [{ kind: "refreshDiary", patientId }] : [];
    default:
      return [];
  }
}

/**
 * Закрытие до `ready` бывает и отказом рукопожатия без сессии: сервер закрывает кодом 1008,
 * а браузер видит 1006, как при обрыве сети. Поэтому сначала проверяем сессию.
 */
export function onClose({
  code,
  wasReady,
  attempt,
}: {
  code: number;
  wasReady: boolean;
  attempt: number;
}): Action[] {
  if (code === SESSION_ENDED) {
    return [{ kind: "toLogin" }];
  }
  const delayMs = reconnectDelay(attempt);
  return [
    { kind: "disconnected" },
    wasReady ? { kind: "reconnect", delayMs } : { kind: "checkSession", delayMs },
  ];
}

/** 1, 2, 4, 8, 16 с, дальше каждые 30 с. */
export function reconnectDelay(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, MAX_DELAY_MS);
}
