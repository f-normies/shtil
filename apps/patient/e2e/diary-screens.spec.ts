import { expect, test } from "@playwright/test";
import { PATIENT_A } from "./assessment-fixtures";
import { mockDiaryJourneyApi, waitForDiaryOwner } from "./diary-journey-api-mock";

async function installSyntheticMicrophone(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(() => {
    class FakeMediaRecorder extends EventTarget {
      static isTypeSupported(): boolean {
        return true;
      }
      state = "inactive";
      mimeType = "audio/webm;codecs=opus";
      start(): void {
        this.state = "recording";
      }
      stop(): void {
        this.state = "inactive";
        this.dispatchEvent(
          new BlobEvent("dataavailable", {
            data: new Blob(["synthetic audio"], { type: this.mimeType }),
          }),
        );
        this.dispatchEvent(new Event("stop"));
      }
      pause(): void {
        this.state = "paused";
      }
      resume(): void {
        this.state = "recording";
      }
    }
    Object.defineProperty(window, "MediaRecorder", {
      configurable: true,
      value: FakeMediaRecorder,
    });
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) },
    });
    Object.defineProperty(window, "AudioContext", {
      configurable: true,
      value: class {
        createAnalyser() {
          return { fftSize: 0, frequencyBinCount: 32, getByteFrequencyData() {} };
        }
        createMediaStreamSource() {
          return { connect() {} };
        }
        async resume() {}
        async close() {}
      },
    });
  });
}

test("quick text saves its draft and entry locally before opening the detail", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/diary/new/text");
  await expect(page.getByRole("heading", { name: "Новая заметка" })).toBeVisible();
  const textbox = page.getByRole("textbox", { name: "Текст записи" });
  await textbox.fill("Сегодня я спокойно провёл день.");
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page).toHaveURL(/\/patient\/diary\/[0-9a-f-]+$/);
  await expect(page.getByText("Сегодня я спокойно провёл день.")).toBeVisible();
  const detailContent = page.locator(".patient-entry-detail__content");
  await expect(detailContent).toHaveCSS("padding-left", "20px");
  await expect(detailContent).toHaveCSS("padding-right", "20px");
  expect(await detailContent.boundingBox()).toMatchObject({ x: 0, width: 360 });
  expect(await detailContent.locator(":scope > h1").boundingBox()).toMatchObject({
    x: 20,
    width: 320,
  });
  await expect(page.locator(".patient-entry-detail__actions")).toHaveCSS("position", "fixed");

  const stored = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    return { entries: await store.listEntries(ownerId), drafts: await store.listDrafts(ownerId) };
  }, PATIENT_A);
  expect(stored.entries).toHaveLength(1);
  expect(stored.entries[0]).toMatchObject({
    kind: "text",
    text: "Сегодня я спокойно провёл день.",
    sync_state: "pending",
  });
  expect(stored.drafts).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test("diary shows the owner's record and hides it only after explicit confirmation", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  const entryId = crypto.randomUUID();
  await page.evaluate(
    async ({ ownerId, id }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      await (await DiaryStore.open()).enqueueEntry({
        ownerId,
        payload: {
          entry_id: id,
          operation_id: crypto.randomUUID(),
          kind: "text",
          occurred_at: new Date().toISOString(),
          daily_on: null,
          mood_level: null,
          mood_words: [],
          text: "Локальная запись для проверки списка.",
          other_medicine_text: null,
        },
      });
    },
    { ownerId: PATIENT_A, id: entryId },
  );
  await page.goto("/patient/diary");
  await expect(page.getByRole("heading", { name: "Дневник" })).toBeVisible();
  await page.getByRole("link", { name: /Локальная запись для проверки списка/ }).click();
  await expect(page).toHaveURL(new RegExp(`/patient/diary/${entryId}$`));
  await page.getByRole("button", { name: "Удалить" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Удалить запись?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Оставить" }).click();
  await expect(page.getByText("Локальная запись для проверки списка.")).toBeVisible();
  await page.getByRole("button", { name: "Удалить" }).click();
  await page
    .getByRole("alertdialog", { name: "Удалить запись?" })
    .getByRole("button", { name: "Удалить" })
    .click();
  await expect(page).toHaveURL(/\/patient\/diary$/);
  const stored = await page.evaluate(
    async ({ ownerId, id }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      return (await DiaryStore.open())
        .listEntries(ownerId)
        .then((entries) => entries.find((entry) => entry.entry_id === id));
    },
    { ownerId: PATIENT_A, id: entryId },
  );
  expect(stored).toBeUndefined();
});

test("quick voice starts microphone capture as soon as its screen opens", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await installSyntheticMicrophone(page);
  await page.goto("/patient/diary/new/voice");
  await expect(page.getByRole("status").filter({ hasText: "Идёт запись" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Остановить запись" })).toBeVisible();
});

test("saved voice remains durable while its pending transcript view is closed", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await page.setViewportSize({ width: 360, height: 780 });
  await installSyntheticMicrophone(page);
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  await page.goto("/patient/diary/new/voice");
  await expect(page.getByRole("status").filter({ hasText: "Идёт запись" })).toBeVisible();
  await page.getByRole("button", { name: "Остановить запись" }).click();

  await expect(page).toHaveURL(/\/patient\/diary\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: "Голосовая запись" })).toBeVisible();
  await expect(page.getByText("Расшифровка ожидает отправки")).toBeVisible();
  await expect(page.getByRole("slider", { name: "Перемотать аудиозапись" })).toBeVisible();

  const saved = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    const entries = await store.listEntries(ownerId);
    const entry = entries.find((item) => item.kind === "voice");
    const commands = await store.getOutboxForOwner(ownerId);
    const command = commands.find((item) => item.action === "voice");
    const blob = entry ? await store.getAudioBlob(ownerId, entry.entry_id) : null;
    return {
      entryId: entry?.entry_id ?? null,
      entryKind: entry?.kind ?? null,
      commandEntryId: command?.entry_id ?? null,
      commandAction: command?.action ?? null,
      blobSize: blob?.size ?? 0,
      drafts: await store.listDrafts(ownerId),
    };
  }, PATIENT_A);
  expect(saved).toMatchObject({
    entryKind: "voice",
    commandAction: "voice",
    commandEntryId: saved.entryId,
  });
  expect(saved.entryId).toBeTruthy();
  expect(saved.blobSize).toBeGreaterThan(0);
  expect(saved.drafts).toEqual([]);

  await page.getByRole("button", { name: "Закрыть" }).click();
  await expect(page).toHaveURL(/\/patient\/diary$/);
  const afterClose = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    const entries = await store.listEntries(ownerId);
    const entry = entries.find((item) => item.kind === "voice");
    const commands = await store.getOutboxForOwner(ownerId);
    const command = commands.find((item) => item.action === "voice");
    const blob = entry ? await store.getAudioBlob(ownerId, entry.entry_id) : null;
    return {
      entryId: entry?.entry_id ?? null,
      commandEntryId: command?.entry_id ?? null,
      blobSize: blob?.size ?? 0,
    };
  }, PATIENT_A);
  expect(afterClose).toMatchObject({
    entryId: saved.entryId,
    commandEntryId: saved.entryId,
  });
  expect(afterClose.blobSize).toBe(saved.blobSize);
});

test("a Today mood tap after daily completion saves one quick mood entry immediately", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const day = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Moscow",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
    await (await DiaryStore.open()).enqueueEntry({
      ownerId,
      payload: {
        entry_id: crypto.randomUUID(),
        operation_id: crypto.randomUUID(),
        kind: "daily",
        daily_on: day,
        occurred_at: new Date().toISOString(),
        mood_level: 3,
        mood_words: [],
        text: null,
        other_medicine_text: null,
      },
    });
  }, PATIENT_A);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Отметка сделана" })).toBeVisible();
  await page.getByRole("button", { name: "Быстрая отметка настроения: Хорошо" }).click();
  await expect
    .poll(async () =>
      page.evaluate(async (ownerId) => {
        const { DiaryStore } = await import("/patient/src/diary/store.ts");
        return (await DiaryStore.open())
          .listEntries(ownerId)
          .then((items) => items.filter((item) => item.kind === "mood").length);
      }, PATIENT_A),
    )
    .toBe(1);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("swiping a diary record opens the same explicit hide confirmation", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context);
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  const entryId = crypto.randomUUID();
  await page.evaluate(
    async ({ ownerId, id }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      await (await DiaryStore.open()).enqueueEntry({
        ownerId,
        payload: {
          entry_id: id,
          operation_id: crypto.randomUUID(),
          kind: "text",
          occurred_at: new Date().toISOString(),
          daily_on: null,
          mood_level: null,
          mood_words: [],
          text: "Запись для проверки жеста скрытия.",
          other_medicine_text: null,
        },
      });
    },
    { ownerId: PATIENT_A, id: entryId },
  );
  await page.goto("/patient/diary");
  const card = page.getByRole("link", { name: /Запись для проверки жеста скрытия/ });
  await card.dispatchEvent("pointerdown", { pointerType: "touch", clientX: 310, clientY: 300 });
  await card.dispatchEvent("pointerup", { pointerType: "touch", clientX: 190, clientY: 302 });
  await expect(page.getByRole("alertdialog", { name: "Удалить запись?" })).toBeVisible();
});
