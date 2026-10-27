import { expect, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { PATIENT_A, SCALE_ID } from "./assessment-fixtures";
import { storeCall } from "./assessment-test-helpers";

test("a denial persisted before subscription prevents a cached offline session from showing data", async ({
  page,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();
  await page.getByRole("link", { name: "Назад" }).click();

  await page.addInitScript(() => {
    let denialWritten = false;
    const addEventListener = BroadcastChannel.prototype.addEventListener;
    Object.defineProperty(BroadcastChannel.prototype, "addEventListener", {
      configurable: true,
      value: function (
        this: BroadcastChannel,
        type: string,
        listener: EventListenerOrEventListenerObject | null,
        options?: boolean | AddEventListenerOptions,
      ) {
        if (!denialWritten && type === "message") {
          denialWritten = true;
          window.localStorage.setItem("assessment_offline_access_denied", "true");
        }
        return Reflect.apply(addEventListener, this, [type, listener, options]);
      },
    });
  });
  api.setNetworkAvailable(false);
  await page.reload();

  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("assessment_offline_access_denied")))
    .toBe("true");
  await expect(
    page.getByText("Для первого входа и загрузки опросников нужно подключение к интернету."),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: /Синтетический пациент А/ })).toHaveCount(0);
  expect(
    await storeCall<Record<string, unknown> | null>(page, "getDraft", {
      ownerId: PATIENT_A,
      scaleId: SCALE_ID,
    }),
  ).toMatchObject({ answers: { calm: 2 }, definition_snapshot: { scale: { version: 1 } } });
});
