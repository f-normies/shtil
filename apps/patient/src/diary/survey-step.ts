import type { SubmissionOutcome } from "../help/submission-outcome";

/**
 * Какую шкалу показывает шаг «Опрос» (P2-01, кадры 17 и 26). Id отправленного ответа
 * попадает в черновик до экрана помощи, поэтому форма удерживается на отправленной шкале
 * (`heldScaleId`), пока пациент не закроет экран помощи кнопкой «Вернуться к отметке».
 */
export function activeSurveyScaleId(input: {
  dueScaleIds: readonly string[];
  submittedIds: readonly string[];
  heldScaleId: string | null;
}): string | undefined {
  const { dueScaleIds, submittedIds, heldScaleId } = input;
  if (heldScaleId && dueScaleIds.includes(heldScaleId)) return heldScaleId;
  return dueScaleIds[submittedIds.length];
}

/** Текст после всех опросов: при живой сети ответы уже ушли, без сети — ждут связи. */
export function surveySubmittedNotice(
  outcome: SubmissionOutcome | null,
  online: boolean,
): { title: string; text: string } {
  const sent = outcome ? outcome === "confirmed" || outcome === "alert-raised" : online;
  if (sent) {
    return { title: "Ответы отправлены", text: "Врач увидит результат опроса сегодня." };
  }
  return {
    title: "Опросы сохранены",
    text: "Ответы сохранены на устройстве. Их можно отправить, когда появится связь.",
  };
}
