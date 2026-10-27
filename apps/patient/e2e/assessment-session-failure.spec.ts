import { type BrowserContext, expect, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";

type SessionReply = { status: number; contentType: string; body: string } | null;

/** Override only /auth/me; null passes the request to the synthetic API mock. */
async function overrideSessionCheck(context: BrowserContext): Promise<{
  reply: (next: SessionReply) => void;
}> {
  let current: SessionReply = null;
  await context.route("**/api/v1/auth/me", async (route) => {
    if (!current) {
      await route.fallback();
      return;
    }
    await route.fulfill(current);
  });
  return {
    reply: (next) => {
      current = next;
    },
  };
}

const PROXY_502 = { status: 502, contentType: "text/html", body: "<html>Bad Gateway</html>" };
const HTML_200 = { status: 200, contentType: "text/html", body: "<!doctype html><title>x</title>" };

test("a 502 session check opens the saved questionnaires offline and names the server", async ({
  page,
}) => {
  await mockAssessmentApi(page.context());
  const session = await overrideSessionCheck(page.context());
  await page.goto("/patient/");
  await expect(page.getByText("Synthetic wellbeing check")).toBeVisible();

  session.reply(PROXY_502);
  await page.reload();

  await expect(page.getByRole("status").filter({ hasText: "Сервер недоступен" })).toContainText(
    "Офлайн-режим",
  );
  await expect(page.getByText("Synthetic wellbeing check")).toBeVisible();
  await expect(page.getByText("Something went wrong!")).toHaveCount(0);
});

test("a non-JSON session reply without saved data says the server is unavailable", async ({
  page,
}) => {
  await mockAssessmentApi(page.context());
  const session = await overrideSessionCheck(page.context());
  session.reply(HTML_200);

  await page.goto("/patient/");

  await expect(page.getByRole("alert")).toContainText("Сервер недоступен");
  await expect(page.getByText("Something went wrong!")).toHaveCount(0);
});

test("an unexpected route error shows a Russian error page with a retry", async ({ page }) => {
  await mockAssessmentApi(page.context());
  const session = await overrideSessionCheck(page.context());
  session.reply({ status: 404, contentType: "application/json", body: '{"status":404}' });

  await page.goto("/patient/");

  await expect(page.getByRole("heading", { name: "Не удалось открыть приложение" })).toBeVisible();
  await expect(page.getByText("Something went wrong!")).toHaveCount(0);

  session.reply(null);
  await page.getByRole("button", { name: "Повторить" }).click();
  await expect(page.getByRole("heading", { name: /Здравствуйте/ })).toBeVisible();
});
