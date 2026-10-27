import type { AssessmentSyncOutcome } from "../assessments/sync";
import type { OutboxState } from "../assessments/types";

/**
 * Исход отправки для экрана помощи (спека #57, раздел 5.1):
 * `alert-raised` — сервер принял ответ и создал сигнал; `confirmed` — принял без сигнала;
 * `queued` — ответ ждёт сети; `rejected` — сервер отклонил ответ, сам он не уйдёт.
 */
export type SubmissionOutcome = "alert-raised" | "confirmed" | "queued" | "rejected";

export type SubmissionOutcomeDeps = {
  syncNow: () => Promise<AssessmentSyncOutcome | null>;
  /** Состояние своей записи в очереди; записи нет — null. */
  outboxState: () => Promise<OutboxState | null>;
};

/**
 * Синхронизация идёт в один поток: если проход уже шёл, `syncNow` вернёт его результат
 * без своей отправки. Тогда второй проход возьмёт запись из очереди. Проход, который
 * не дошёл до конца (нет сети, нужен вход), не повторяется: запись ждёт в очереди.
 * `passes: 1` — без экрана помощи: второй проход нужен только фразе о враче.
 */
export async function submissionOutcome(
  submissionId: string,
  online: boolean,
  deps: SubmissionOutcomeDeps,
  { passes = 2 }: { passes?: 1 | 2 } = {},
): Promise<SubmissionOutcome> {
  for (let pass = 0; online && pass < passes; pass += 1) {
    const result = await deps.syncNow();
    if (!result) break;
    if (result.confirmedSubmissionIds.includes(submissionId)) {
      return result.alertRaisedSubmissionIds.includes(submissionId) ? "alert-raised" : "confirmed";
    }
    if (result.status !== "complete") break;
  }
  return (await deps.outboxState()) === "pending" ? "queued" : "rejected";
}
