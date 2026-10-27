import { expect, test } from "@playwright/test";
import { PATIENT_A } from "./assessment-fixtures";
import { mockDiaryJourneyApi, waitForDiaryOwner } from "./diary-journey-api-mock";

test("daily text check-in keeps the chosen mood and saves only after local commit", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");

  await expect(page.getByRole("heading", { name: "Как вы сейчас?" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Сегодня" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Дневник" })).toBeVisible();
  const mood = page.getByRole("button", { name: /Начать ежедневную отметку.*Ровно/ });
  await mood.click();
  await expect(page).toHaveURL(/\/patient\/check-in(?:\?.*)?$/);
  await expect(page.getByRole("button", { name: "Ровно" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("1 из 4")).toBeVisible();

  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Как прошёл день?" })).toBeVisible();
  await page.getByRole("button", { name: "Текст" }).click();
  const story = "Сегодня я спокойно провёл день и немного гулял.";
  await page.getByRole("textbox", { name: "Расскажите о своём дне" }).fill(story);
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Лекарства" })).toBeVisible();
  await expect(page.getByText("Действующих назначений на сегодня нет.")).toBeVisible();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Отметка сохранена" })).toBeVisible();

  const saved = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    return { entries: await store.listEntries(ownerId), drafts: await store.listDrafts(ownerId) };
  }, PATIENT_A);
  expect(saved.entries).toHaveLength(1);
  expect(saved.entries[0]).toMatchObject({
    kind: "daily",
    mood_level: 3,
    text: story,
    other_medicine_text: null,
    survey_status: "not_due",
    sync_state: "pending",
  });
  expect(saved.drafts).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test("skipping the daily story finalizes empty text and medicine fields", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");

  await page.getByRole("button", { name: /Начать ежедневную отметку.*Ровно/ }).click();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Как прошёл день?" })).toBeVisible();
  await page.getByRole("button", { name: "Пропустить" }).click();
  await expect(page.getByRole("heading", { name: "Лекарства" })).toBeVisible();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Отметка сохранена" })).toBeVisible();

  const saved = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    return { entries: await store.listEntries(ownerId), drafts: await store.listDrafts(ownerId) };
  }, PATIENT_A);
  expect(saved.entries).toHaveLength(1);
  expect(saved.entries[0]).toMatchObject({
    kind: "daily",
    mood_level: 3,
    text: null,
    other_medicine_text: null,
    survey_status: "not_due",
    sync_state: "pending",
  });
  expect(saved.drafts).toEqual([]);
});

test("quick mood requires a level and tag or repeat taps update the same open-sheet entry", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/diary");
  await expect(page.getByRole("heading", { name: "Дневник" })).toBeVisible();
  await page
    .getByRole("button", { name: /Настроение/ })
    .first()
    .click();
  const sheet = page.getByRole("dialog", { name: "Как вы сейчас?" });
  await expect(sheet).toBeVisible();
  await sheet.getByRole("button", { name: "Готово" }).click();

  const initial = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    return (await DiaryStore.open()).listEntries(ownerId);
  }, PATIENT_A);
  expect(initial).toEqual([]);

  await page
    .getByRole("button", { name: /Настроение/ })
    .first()
    .click();
  const openSheet = page.getByRole("dialog", { name: "Как вы сейчас?" });
  await openSheet.getByRole("button", { name: "Ровно" }).click();
  await expect(openSheet.getByText("Сохранено в дневник")).toBeVisible();
  await openSheet.getByRole("button", { name: "усталость" }).click();
  await openSheet.getByRole("button", { name: "Хорошо" }).click();

  const firstSheetEntries = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    return (await DiaryStore.open()).listEntries(ownerId);
  }, PATIENT_A);
  expect(firstSheetEntries).toHaveLength(1);
  expect(firstSheetEntries[0]).toMatchObject({
    kind: "mood",
    mood_level: 5,
    mood_words: ["усталость"],
  });

  await openSheet.getByRole("button", { name: "Готово" }).click();
  await page
    .getByRole("button", { name: /Настроение/ })
    .first()
    .click();
  await page
    .getByRole("dialog", { name: "Как вы сейчас?" })
    .getByRole("button", { name: "Ровно" })
    .click();
  const reopenedEntries = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    return (await DiaryStore.open()).listEntries(ownerId);
  }, PATIENT_A);
  expect(reopenedEntries).toHaveLength(2);
});

test("switching a saved daily voice draft to text confirms before removing its audio", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  const today = await page.evaluate(() => {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Moscow",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date());
    const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
    return `${part("year")}-${part("month")}-${part("day")}`;
  });
  const ids = {
    draft: crypto.randomUUID(),
    entry: crypto.randomUUID(),
    operation: crypto.randomUUID(),
  };
  await page.evaluate(
    async ({ ownerId, day, identity }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const store = await DiaryStore.open();
      await store.saveDraft({
        ownerId,
        draftId: identity.draft,
        expectedRevision: 0,
        mode: "daily",
        entryKind: "daily",
        entryId: identity.entry,
        operationId: identity.operation,
        occurredAt: new Date().toISOString(),
        dailyOn: day,
        step: "story",
        moodLevel: 3,
        moodWords: [],
        text: "",
        scheduleSnapshot: null,
        scheduleNeedsRefresh: true,
        surveyStatus: "unknown",
      });
      await store.saveAudioBlob(
        ownerId,
        identity.entry,
        new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" }),
      );
    },
    { ownerId: PATIENT_A, day: today, identity: ids },
  );

  await page.goto("/patient/check-in");
  await expect(page.getByRole("heading", { name: "Как прошёл день?" })).toBeVisible();
  await page.getByRole("button", { name: "Текст" }).click();
  const confirmation = page.getByRole("alertdialog", { name: "Переключить способ рассказа?" });
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole("button", { name: "Удалить запись и перейти к тексту" }).click();

  const audio = await page.evaluate(
    async ({ ownerId, entryId }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      return (await DiaryStore.open()).getAudioBlob(ownerId, entryId);
    },
    { ownerId: PATIENT_A, entryId: ids.entry },
  );
  expect(audio).toBeNull();
  await expect(page.getByRole("textbox", { name: "Расскажите о своём дне" })).toBeVisible();
});
