import { describe, expect, it } from "vitest";
import { activeSurveyScaleId, surveySubmittedNotice } from "./survey-step";

describe("activeSurveyScaleId (P2-01, кадр 17 → 26)", () => {
  const due = ["phq-9"];

  it("показывает первую положенную шкалу до отправки", () => {
    expect(activeSurveyScaleId({ dueScaleIds: due, submittedIds: [], heldScaleId: null })).toBe(
      "phq-9",
    );
  });

  it("после отправки с пунктом 9 форма остаётся на месте, пока экран помощи не закрыт", () => {
    // Сценарий дефекта: id ответа уже записан в черновик, экран помощи ещё открыт.
    expect(
      activeSurveyScaleId({ dueScaleIds: due, submittedIds: ["s-1"], heldScaleId: "phq-9" }),
    ).toBe("phq-9");
  });

  it("после «Вернуться к отметке» шаг идёт дальше", () => {
    expect(
      activeSurveyScaleId({ dueScaleIds: due, submittedIds: ["s-1"], heldScaleId: null }),
    ).toBeUndefined();
  });

  it("при двух шкалах удержание не пропускает вторую", () => {
    const two = ["gad-7", "phq-9"];
    expect(
      activeSurveyScaleId({ dueScaleIds: two, submittedIds: ["s-1"], heldScaleId: "gad-7" }),
    ).toBe("gad-7");
    expect(
      activeSurveyScaleId({ dueScaleIds: two, submittedIds: ["s-1"], heldScaleId: null }),
    ).toBe("phq-9");
  });
});

describe("surveySubmittedNotice", () => {
  it("при сети — «Ответы отправлены», без сети — про устройство и связь", () => {
    expect(surveySubmittedNotice("confirmed", true).title).toBe("Ответы отправлены");
    expect(surveySubmittedNotice("alert-raised", true).title).toBe("Ответы отправлены");
    expect(surveySubmittedNotice(null, true).title).toBe("Ответы отправлены");
    expect(surveySubmittedNotice("queued", true)).toEqual({
      title: "Опросы сохранены",
      text: "Ответы сохранены на устройстве. Их можно отправить, когда появится связь.",
    });
    expect(surveySubmittedNotice(null, false).title).toBe("Опросы сохранены");
  });
});
