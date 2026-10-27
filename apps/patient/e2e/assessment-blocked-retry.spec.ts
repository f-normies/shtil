import { expect, type Page, test } from "@playwright/test";
import { type AssessmentApiMock, mockAssessmentApi } from "./assessment-api-mock";
import { SCALE_ID } from "./assessment-fixtures";

async function submitBlockedAnswer(
  page: Page,
  api: AssessmentApiMock,
  status: number,
  code: string,
): Promise<void> {
  await page.goto(`/patient/assessments/${SCALE_ID}`);
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();
  api.setNextSubmissionResponse(status, code);
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(
    page.getByText("Сохранено на устройстве. Отправим, когда появится связь."),
  ).toBeVisible();
  await expect.poll(() => api.submissions.length).toBe(1);
  await page.getByRole("link", { name: "Назад" }).click();
  await expect(page.getByRole("heading", { name: "Нужно проверить" })).toBeVisible();
}

for (const { status, code, reason } of [
  { status: 404, code: "scale_unavailable", reason: "опросник сейчас недоступен" },
  { status: 409, code: "lead_doctor_missing", reason: "лечащий врач пока не назначен" },
]) {
  test(`blocked ${code} answer is resent manually with the same submission key`, async ({
    page,
  }) => {
    const api = await mockAssessmentApi(page.context());
    await submitBlockedAnswer(page, api, status, code);
    await expect(page.getByText(`Отправка остановлена: ${reason}`)).toBeVisible();
    expect(api.submissions).toHaveLength(1);

    await page.getByRole("button", { name: "Повторить отправку" }).click();

    await expect.poll(() => api.acknowledgedSubmissions.length).toBe(1);
    await expect(page.getByRole("heading", { name: "Нужно проверить" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toHaveCount(0);
    await expect(page.getByText("Synthetic calmness band")).toBeVisible();
    expect(api.submissions).toHaveLength(2);
    expect(api.submissions[1]).toEqual(api.submissions[0]);
  });
}

test("a blocked answer from an old scale version offers no resend of the same payload", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await submitBlockedAnswer(page, api, 409, "scale_version_changed");
  await expect(page.getByRole("link", { name: "Заполнить актуальную версию" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Повторить отправку" })).toHaveCount(0);
});
