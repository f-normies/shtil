import { expect, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";

test("catalog explains loading, an empty result, and a server error", async ({ page }) => {
  await mockAssessmentApi(page.context(), { definitions: [] });
  let releaseCatalog!: () => void;
  const catalogGate = new Promise<void>((resolve) => {
    releaseCatalog = resolve;
  });
  let catalogFails = false;
  await page.route("**/api/v1/assessments/scales", async (route) => {
    await catalogGate;
    if (catalogFails) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ status: 503, title: "Synthetic service unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [] }),
    });
  });

  await page.goto("/patient/");
  await expect(page.getByText("Загружаем опросники…")).toBeVisible();
  releaseCatalog();
  await expect(page.getByText("Опросники пока не доступны.")).toBeVisible();

  catalogFails = true;
  await page.reload();
  await expect(
    page.getByRole("alert").filter({ hasText: "Не удалось загрузить опросники" }),
  ).toContainText("Проверьте связь и повторите попытку.");
});
