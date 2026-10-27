import { expect, test } from "@playwright/test";
import { DEFINITION_V1 } from "./assessment-fixtures";
import { storeCall } from "./assessment-test-helpers";
import {
  DOCTOR_ID,
  DOCTOR_NAME,
  mockMessagingPatientApi,
  PATIENT_ID,
} from "./messaging-patient-api-mock";

test.use({
  launchOptions: {
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
  },
});

test("patient opens chat from home and sends text to the verified conversation", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await mockMessagingPatientApi(page.context());

  await page.goto("/patient/");
  await page.getByRole("link", { name: "Чат", exact: true }).click();

  const log = page.getByRole("log");
  await expect(log).toBeVisible();
  await expect(page.getByText(DOCTOR_NAME, { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Сообщение врачу" }).fill("Сегодня мне спокойнее.");
  await page.getByRole("button", { name: "Отправить" }).click();

  await expect(log).toContainText("Сегодня мне спокойнее.");
  await expect(log).toContainText("Отправлено");
  await expect.poll(() => api.sendAttempts.length).toBe(1);
  expect(api.sendAttempts[0]).toMatchObject({
    expected_doctor_id: DOCTOR_ID,
    text: "Сегодня мне спокойнее.",
  });
  expect(api.typingCommands).toEqual(
    expect.arrayContaining([
      {
        type: "message.typing",
        data: { patient_id: PATIENT_ID, expected_doctor_id: DOCTOR_ID, active: true },
      },
      {
        type: "message.typing",
        data: { patient_id: PATIENT_ID, expected_doctor_id: DOCTOR_ID, active: false },
      },
    ]),
  );
  expect(api.patientMessageRequests).toContainEqual({
    method: "POST",
    path: `/api/v1/patients/${PATIENT_ID}/messages`,
  });
});

test("keeps a queued message in IndexedDB through a network failure and page reload", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await mockMessagingPatientApi(page.context());
  api.failNextTextSend();

  await page.goto("/patient/chat");
  await storeCall(page, "cacheDefinition", DEFINITION_V1);
  await page
    .getByRole("textbox", { name: "Сообщение врачу" })
    .fill("Сохранить до восстановления связи.");
  await page.getByRole("button", { name: "Отправить" }).click();

  const log = page.getByRole("log");
  await expect(log).toContainText("Сохранить до восстановления связи.");
  await expect(log).toContainText("Отправится при подключении");
  await expect.poll(() => api.sendAttempts.length).toBe(1);

  api.failNextAuthMe();
  await page.getByRole("link", { name: "На главную" }).click();
  await expect(page.getByRole("status")).toContainText("Офлайн-режим");
  await page.getByRole("button", { name: "Выйти" }).click();
  const logoutDialog = page.getByRole("dialog", { name: "Выход с локальными ответами" });
  await expect(logoutDialog).toContainText("1 неотправленных сообщений");
  await logoutDialog.getByRole("button", { name: "Остаться" }).click();

  api.holdNextSuccessfulTextSend();
  await page.goto("/patient/chat");
  await expect.poll(() => api.sendAttempts.length).toBe(2);
  await api.waitForHeldTextSend();
  expect(api.sendAttempts[1]?.client_message_id).toBe(api.sendAttempts[0]?.client_message_id);
  await expect(page.getByRole("log")).toContainText("Сохранить до восстановления связи.");

  api.releaseHeldTextSend();
  await expect(page.getByRole("log")).toContainText("Отправлено");
  expect(api.messages).toHaveLength(1);
});

test("sends an expired patient websocket session to login", async ({ page }) => {
  const api = await mockMessagingPatientApi(page.context());
  await page.goto("/patient/chat");
  await expect(page.getByRole("log")).toBeVisible();

  api.expireSocket();

  await expect(page).toHaveURL(/\/patient\/login$/);
  await expect(page.getByRole("log")).toHaveCount(0);
});

test("redirects an unauthenticated visitor before rendering local chat", async ({ page }) => {
  await mockMessagingPatientApi(page.context(), { unauthenticated: true });
  await page.goto("/patient/chat");

  await expect(page).toHaveURL(/\/patient\/login$/);
  await expect(page.getByRole("log")).toHaveCount(0);
});

test("records a patient voice message and shows its server transcript", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const api = await mockMessagingPatientApi(page.context());
  await page.goto("/patient/chat");
  await page.context().grantPermissions(["microphone"], { origin: new URL(page.url()).origin });
  const log = page.getByRole("log");

  await page.getByRole("button", { name: "Начать запись" }).click();
  await expect(page.getByText(/Идёт запись/)).toContainText("0:01");
  await page.getByRole("button", { name: "Остановить", exact: true }).click();
  await page.getByRole("button", { name: "Отправить голосовое" }).click();

  await expect(log).toContainText("Синтетическая расшифровка.");
  await expect(log).toContainText("Отправлено");
  await page.screenshot({ path: "/private/tmp/cc33-patient.png", fullPage: true });
  await expect.poll(() => api.voiceUploads.length).toBe(1);
  expect(api.voiceUploads[0]).toMatchObject({
    expectedDoctorId: DOCTOR_ID,
    clientMessageId: expect.any(String),
  });
  expect(api.voiceUploads[0]?.contentType).toContain("multipart/form-data");
  expect(api.voiceUploads[0]?.bodyBytes).toBeGreaterThan(0);
  expect(api.patientMessageRequests).toContainEqual({
    method: "POST",
    path: `/api/v1/patients/${PATIENT_ID}/messages/voice`,
  });
});
