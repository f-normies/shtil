import type { Schemas } from "@compass/api-client";
import { expect, test } from "@playwright/test";
import { PATIENT_A } from "./assessment-fixtures";
import { mockDiaryJourneyApi, waitForDiaryOwner } from "./diary-journey-api-mock";

const TODAY = "2026-10-20";
const NOW = `${TODAY}T12:00:00.000Z`;

test("diary calendar and filters show only matching synthetic entries", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context, { serverNow: NOW });
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  await page.evaluate(
    async ({ ownerId, today }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const store = await DiaryStore.open();
      const entries = [
        {
          entry_id: "50000000-0000-4000-8000-000000000001",
          operation_id: "50000000-0000-4000-8000-000000000002",
          kind: "text" as const,
          occurred_at: `${today}T10:00:00.000Z`,
          daily_on: null,
          mood_level: null,
          mood_words: [],
          text: "Синтетическая запись за сегодня.",
          other_medicine_text: null,
        },
        {
          entry_id: "50000000-0000-4000-8000-000000000003",
          operation_id: "50000000-0000-4000-8000-000000000004",
          kind: "mood" as const,
          occurred_at: "2026-10-19T10:00:00.000Z",
          daily_on: null,
          mood_level: 4,
          mood_words: [],
          text: null,
          other_medicine_text: null,
        },
      ];
      for (const payload of entries) await store.enqueueEntry({ ownerId, payload });
    },
    { ownerId: PATIENT_A, today: TODAY },
  );

  await page.reload();
  await page.goto("/patient/diary");
  const textEntry = page.getByRole("link", { name: /Синтетическая запись за сегодня/ });
  const moodEntry = page.getByRole("link", { name: /Неплохо/ });
  const filters = page.locator(".patient-diary-filter");
  await expect(textEntry).toBeVisible();
  await expect(moodEntry).toBeVisible();

  await filters.getByRole("button", { name: "Настроение", exact: true }).click();
  await expect(moodEntry).toBeVisible();
  await expect(textEntry).toHaveCount(0);

  await filters.getByRole("button", { name: "Записи", exact: true }).click();
  await expect(textEntry).toBeVisible();
  await expect(moodEntry).toHaveCount(0);

  await filters.getByRole("button", { name: "Все", exact: true }).click();
  await page.getByLabel("Выбрать дату").fill("2026-10-19");
  await expect(moodEntry).toBeVisible();
  await expect(textEntry).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Вчера", exact: true })).toBeVisible();
});

test("edit conflict keeps the patient's edit beside the server version", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context, { serverNow: NOW });
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  const entryId = "51000000-0000-4000-8000-000000000001";
  const operationId = "51000000-0000-4000-8000-000000000002";
  const original = {
    audio: null,
    daily_on: null,
    entry_id: entryId,
    kind: "text",
    mood_level: null,
    mood_words: [],
    occurred_at: NOW,
    other_medicine_text: null,
    received_at: NOW,
    revision: 1,
    source_statuses: [],
    text: "Исходная серверная запись.",
  } satisfies Schemas["DiaryEntryOut"];
  const remote = { ...original, revision: 2, text: "Обновлённая серверная версия." };
  let updateRejected = false;
  let patchBody: Record<string, unknown> | undefined;
  await context.route(`**/api/v1/diary/entries/${entryId}`, async (route) => {
    const request = route.request();
    if (request.method() === "PATCH") {
      updateRejected = true;
      patchBody = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 409,
        contentType: "application/problem+json",
        body: JSON.stringify({ status: 409, code: "revision_conflict", title: "Conflict" }),
      });
      return;
    }
    if (request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(updateRejected ? remote : original),
      });
      return;
    }
    await route.fallback();
  });

  await page.evaluate(
    async ({ ownerId, entry, opId }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const store = await DiaryStore.open();
      await store.enqueueEntry({
        ownerId,
        payload: {
          operation_id: opId,
          entry_id: entry.entry_id,
          kind: "text",
          occurred_at: entry.occurred_at,
          daily_on: null,
          mood_level: null,
          mood_words: [],
          text: entry.text,
          other_medicine_text: null,
        },
      });
      await store.confirmCommand(ownerId, {
        entry_id: entry.entry_id,
        operation_id: opId,
        revision: 1,
        hidden: false,
        received_at: entry.received_at,
      });
    },
    { ownerId: PATIENT_A, entry: original, opId: operationId },
  );

  await page.reload();
  await page.goto(`/patient/diary/${entryId}`);
  await expect(page.getByText("Исходная серверная запись.")).toBeVisible();
  await page.getByRole("button", { name: "Изменить" }).click();
  await page.getByLabel("Текст записи").fill("Моя сохранённая правка.");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.getByText("Моя сохранённая правка.")).toBeVisible();

  const patchSent = page.waitForRequest(
    (request) =>
      request.method() === "PATCH" && request.url().endsWith(`/diary/entries/${entryId}`),
  );
  await page.getByRole("button", { name: "Дневник", exact: true }).click();
  await patchSent;
  await expect
    .poll(async () =>
      page.evaluate(
        async ({ ownerId, id }) => {
          const { DiaryStore } = await import("/patient/src/diary/store.ts");
          const store = await DiaryStore.open();
          const entry = (await store.listEntries(ownerId)).find((item) => item.entry_id === id);
          return entry
            ? {
                sync_state: entry.sync_state,
                text: entry.text,
                remote_text: entry.remote_snapshot?.text,
                remote_revision: entry.remote_snapshot?.revision,
              }
            : null;
        },
        { ownerId: PATIENT_A, id: entryId },
      ),
    )
    .toEqual({
      sync_state: "conflict",
      text: "Моя сохранённая правка.",
      remote_text: "Обновлённая серверная версия.",
      remote_revision: 2,
    });

  await page.goto(`/patient/diary/${entryId}`);
  const conflict = page.getByRole("region", { name: "Сравнение версий записи" });
  await expect(conflict).toBeVisible();
  await expect(conflict.getByText("Моя сохранённая правка.")).toBeVisible();
  await expect(
    conflict.getByText("Обе версии сохранены. Изменения можно перенести вручную."),
  ).toBeVisible();
  await conflict.getByRole("button", { name: "На сервере" }).click();
  await expect(conflict.getByText("Обновлённая серверная версия.")).toBeVisible();
  await conflict.getByRole("button", { name: "Моя версия" }).click();
  await expect(conflict.getByText("Моя сохранённая правка.")).toBeVisible();
  expect(patchBody).toMatchObject({ expected_revision: 1, text: "Моя сохранённая правка." });
});

test("a daily check-in crossing Moscow midnight keeps its starting day", async ({
  page,
  context,
}) => {
  const beforeMidnight = "2026-10-24T20:59:50.000Z";
  await page.clock.install({ time: new Date(beforeMidnight) });
  await mockDiaryJourneyApi(context, { serverNow: beforeMidnight });
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  await page.getByRole("button", { name: /Начать ежедневную отметку.*Ровно/ }).click();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Как прошёл день?" })).toBeVisible();

  await page.clock.setFixedTime(new Date("2026-10-24T21:00:10.000Z"));
  await page.getByRole("button", { name: "Текст" }).click();
  await page
    .getByRole("textbox", { name: "Расскажите о своём дне" })
    .fill("Синтетический день до полуночи.");
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Лекарства" })).toBeVisible();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Отметка сохранена" })).toBeVisible();

  const saved = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    return (await DiaryStore.open()).listEntries(ownerId);
  }, PATIENT_A);
  expect(saved).toHaveLength(1);
  expect(saved[0]).toMatchObject({
    kind: "daily",
    daily_on: "2026-10-24",
    text: "Синтетический день до полуночи.",
  });
});
