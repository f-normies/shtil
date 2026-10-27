// Kept together because these regressions share the diary owner setup, indexedDB seeding and API receipt fixtures.
import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { PATIENT_A, SCALE_ID } from "./assessment-fixtures";
import { mockDiaryJourneyApi, waitForDiaryOwner } from "./diary-journey-api-mock";

const TODAY = "2026-10-20";
const NOW = `${TODAY}T12:00:00.000Z`;

const DAILY_IDENTITY = {
  draftId: "30000000-0000-4000-8000-000000000001",
  entryId: "30000000-0000-4000-8000-000000000002",
  operationId: "30000000-0000-4000-8000-000000000003",
};

async function mockDueSchedule(context: BrowserContext): Promise<void> {
  const schedule = {
    today: TODAY,
    items: [{ due_on: TODAY, scale_id: SCALE_ID, scale_title: "Synthetic wellbeing check" }],
  } satisfies Schemas["ScheduleOut"];
  await context.route("**/api/v1/assessments/schedule", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(schedule),
    });
  });
}

async function seedDailyDraft(page: Page): Promise<void> {
  await page.evaluate(
    async ({ ownerId, identity }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const store = await DiaryStore.open();
      const saved = await store.saveDraft({
        ownerId,
        draftId: identity.draftId,
        expectedRevision: 0,
        mode: "daily",
        entryKind: "daily",
        entryId: identity.entryId,
        operationId: identity.operationId,
        occurredAt: "2026-10-20T12:00:00.000Z",
        dailyOn: "2026-10-20",
        step: "survey",
        moodLevel: 3,
        moodWords: [],
        text: "",
        scheduleSnapshot: null,
        scheduleNeedsRefresh: true,
        surveyStatus: "unknown",
      });
      if (saved.status !== "saved") throw new Error("Could not seed the daily survey draft");
    },
    { ownerId: PATIENT_A, identity: DAILY_IDENTITY },
  );
}

async function seedFinalizedUnknownSurvey(page: Page): Promise<void> {
  await page.evaluate(
    async ({ ownerId, identity }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const store = await DiaryStore.open();
      const saved = await store.saveDraft({
        ownerId,
        draftId: identity.draftId,
        expectedRevision: 0,
        mode: "daily",
        entryKind: "daily",
        entryId: identity.entryId,
        operationId: identity.operationId,
        occurredAt: "2026-10-20T12:00:00.000Z",
        dailyOn: "2026-10-20",
        step: "survey",
        moodLevel: 3,
        moodWords: [],
        text: "",
        scheduleSnapshot: null,
        scheduleNeedsRefresh: true,
        surveyStatus: "unknown",
      });
      if (saved.status !== "saved") throw new Error("Could not seed the daily survey draft");
      const result = await store.finalizeDailyCheckIn({
        ownerId,
        draftId: identity.draftId,
        expectedDraftRevision: saved.draft.revision,
        payload: {
          entry_id: identity.entryId,
          operation_id: identity.operationId,
          kind: "daily",
          daily_on: "2026-10-20",
          occurred_at: "2026-10-20T12:00:00.000Z",
          mood_level: 3,
          mood_words: [],
          text: null,
          other_medicine_text: null,
        },
        surveyStatus: "unknown",
        surveySubmissionIds: [],
        assessmentDrafts: [],
      });
      if (result.status !== "queued" && result.status !== "replayed") {
        throw new Error("Could not seed the saved daily entry and continuation draft");
      }
    },
    { ownerId: PATIENT_A, identity: DAILY_IDENTITY },
  );
}

test("resuming a daily survey draft uses the fresh schedule response", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context, { serverNow: NOW });
  await mockDueSchedule(context);
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  await seedDailyDraft(page);

  await page.goto("/patient/check-in");

  await expect(page.getByText("I felt calm.")).toBeVisible();
  // Одна шкала — без подписи «· 1 из 1» (P2-16); заголовок шкалы берётся из свежего расписания.
  await expect(page.getByRole("heading", { name: "Synthetic wellbeing check" })).toBeVisible();
  await expect(page.getByText("· 1 из 1")).toHaveCount(0);
});

test("Today continues an unresolved survey without changing its daily entry", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context, { serverNow: NOW });
  await mockDueSchedule(context);
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  await seedFinalizedUnknownSurvey(page);

  await page.goto("/patient/");

  await expect(page.getByRole("heading", { name: "Отметка сделана" })).toBeVisible();
  await expect(page.getByText("Опрос не завершён")).toBeVisible();
  await expect(page.locator(".patient-month-count .patient-bead-row i")).toHaveCount(1);
  await page.getByRole("link", { name: "Продолжить опрос" }).click();
  await expect(page.getByText("I felt calm.")).toBeVisible();

  const saved = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    return { drafts: await store.listDrafts(ownerId), entries: await store.listEntries(ownerId) };
  }, PATIENT_A);
  expect(saved.entries.filter((entry) => entry.kind === "daily")).toHaveLength(1);
  expect(saved.entries[0]?.entry_id).toBe(DAILY_IDENTITY.entryId);
  expect(saved.drafts).toHaveLength(1);
  expect(saved.drafts[0]?.entry_id).toBe(DAILY_IDENTITY.entryId);
});

test("daily completion counts its acknowledged UTC-boundary entry in the system month once", async ({
  page,
  context,
}) => {
  const boundaryNow = "2026-10-31T21:30:00.000Z";
  await mockDiaryJourneyApi(context, { serverNow: boundaryNow, monthEntryCount: 1 });
  let serverMonthCount = 1;
  let receipt: Schemas["DiaryCommandReceipt"] | null = null;
  await context.route("**/api/v1/diary/context", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        daily_status: "missing",
        month_entry_count: serverMonthCount,
        observation_from: "2026-11-01",
        server_now: boundaryNow,
        timezone: "Europe/Moscow",
        today: "2026-11-01",
      } satisfies Schemas["DiaryContextOut"]),
    });
  });
  await context.route("**/api/v1/diary/entries", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    const payload = route.request().postDataJSON() as Schemas["DiaryEntryCreateIn"];
    receipt = {
      entry_id: payload.entry_id,
      operation_id: payload.operation_id,
      hidden: false,
      revision: 1,
      received_at: boundaryNow,
    };
    serverMonthCount = 1;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(receipt),
    });
  });
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  await page.evaluate(async () => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const original = DiaryStore.prototype.finalizeDailyCheckIn;
    const acknowledged: typeof original = async function (
      this: import("../src/diary/store").DiaryStore,
      input,
    ) {
      const result = await original.call(this, input);
      if (result.status === "queued" || result.status === "replayed") {
        const response = await fetch("/api/v1/diary/entries", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(result.command.payload),
        });
        if (!response.ok) throw new Error("Synthetic diary server did not acknowledge the entry");
        const receipt = (await response.json()) as Schemas["DiaryCommandReceipt"];
        await this.confirmCommand(input.ownerId, receipt);
        return {
          ...result,
          entry: {
            ...result.entry,
            sync_state: "synced" as const,
            server_revision: receipt.revision,
            received_at: receipt.received_at,
          },
        };
      }
      return result;
    };
    DiaryStore.prototype.finalizeDailyCheckIn = acknowledged;
  });

  await page.getByRole("button", { name: /Начать ежедневную отметку.*Ровно/ }).click();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Как прошёл день?" })).toBeVisible();
  await page.getByRole("button", { name: "Пропустить" }).click();
  await expect(page.getByRole("heading", { name: "Лекарства" })).toBeVisible();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Спасибо, отметка сохранена" })).toBeVisible();
  await expect.poll(() => receipt?.entry_id).not.toBeNull();
  expect(serverMonthCount).toBe(1);
  await expect
    .poll(async () =>
      page.evaluate(async (ownerId) => {
        const { DiaryStore } = await import("/patient/src/diary/store.ts");
        const entries = await (await DiaryStore.open()).listEntries(ownerId);
        return entries.find((entry) => entry.kind === "daily")?.sync_state ?? null;
      }, PATIENT_A),
    )
    .toBe("synced");
  await expect(
    page.locator(".patient-completion-month .patient-bead-row i[data-new='true']"),
  ).toHaveCount(1);
  await expect(page.locator(".patient-completion-month strong")).toHaveText("1");
  await expect(page.locator(".patient-completion-month .patient-bead-row i")).toHaveCount(1);
});

test("Today omits a server bead while its durable hide command is pending", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context, { serverNow: NOW, monthEntryCount: 1 });
  const identity = {
    entryId: "40000000-0000-4000-8000-000000000001",
    operationId: "40000000-0000-4000-8000-000000000002",
    hideOperationId: "40000000-0000-4000-8000-000000000003",
  };
  const serverEntry = {
    audio: null,
    daily_on: null,
    entry_id: identity.entryId,
    kind: "mood",
    mood_level: 3,
    mood_words: [],
    occurred_at: NOW,
    other_medicine_text: null,
    received_at: NOW,
    revision: 1,
    source_statuses: [],
    text: null,
  } satisfies Schemas["DiaryEntryOut"];
  const unaffectedServerEntry = {
    ...serverEntry,
    entry_id: "40000000-0000-4000-8000-000000000004",
    mood_level: 4,
  } satisfies Schemas["DiaryEntryOut"];
  let diaryEntryGetCount = 0;
  await context.route("**/api/v1/diary/entries**", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    diaryEntryGetCount += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [serverEntry, unaffectedServerEntry], next_cursor: null }),
    });
  });

  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  const hiddenMoodBead = page.locator('.patient-week-day__dots i[data-level="3"]');
  const weekMoodBeads = page.locator(".patient-week-day__dots i[data-level]");
  await expect.poll(() => diaryEntryGetCount).toBeGreaterThan(0);
  await expect(hiddenMoodBead).toHaveCount(1);
  await expect(weekMoodBeads).toHaveCount(2);
  const seeded = await page.evaluate(
    async ({ ownerId, ids }) => {
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      const store = await DiaryStore.open();
      const created = await store.enqueueEntry({
        ownerId,
        payload: {
          entry_id: ids.entryId,
          operation_id: ids.operationId,
          kind: "mood",
          occurred_at: "2026-10-20T12:00:00.000Z",
          daily_on: null,
          mood_level: 3,
          mood_words: [],
          text: null,
          other_medicine_text: null,
        },
      });
      if (created.status !== "queued" && created.status !== "replayed") {
        throw new Error("Could not seed the diary entry");
      }
      await store.confirmCommand(ownerId, {
        entry_id: created.entry.entry_id,
        operation_id: created.command.operation_id,
        hidden: false,
        revision: created.entry.revision,
        received_at: "2026-10-20T12:00:00.000Z",
      });
      const [syncedEntry] = await store.listEntries(ownerId);
      if (syncedEntry?.sync_state !== "synced") {
        throw new Error("The server entry fixture was not acknowledged locally");
      }
      const hidden = await store.enqueueHide({
        ownerId,
        entryId: ids.entryId,
        payload: {
          operation_id: ids.hideOperationId,
          expected_revision: syncedEntry.revision,
        },
      });
      if (hidden.status !== "queued" && hidden.status !== "replayed") {
        throw new Error("Could not seed the pending hide command");
      }
      return {
        entries: await store.listEntries(ownerId),
        outbox: await store.getOutboxForOwner(ownerId),
      };
    },
    { ownerId: PATIENT_A, ids: identity },
  );
  expect(seeded.entries).toEqual([]);
  expect(seeded.outbox.some((command) => command.action === "hide")).toBe(true);

  const readsBeforeReload = diaryEntryGetCount;
  await page.reload();
  await expect.poll(() => diaryEntryGetCount).toBeGreaterThanOrEqual(readsBeforeReload + 2);

  await expect(hiddenMoodBead).toHaveCount(0);
  await expect(weekMoodBeads).toHaveCount(1);
});

test("closing and reopening mood sheet during save starts a new entry", async ({
  page,
  context,
}) => {
  await mockDiaryJourneyApi(context, { serverNow: NOW });
  await page.goto("/patient/");
  await waitForDiaryOwner(page);
  await page.goto("/patient/diary");

  await page.evaluate(async () => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    type MoodSaveControl = Window & {
      __diaryMoodSaveStarted?: boolean;
      __holdDiaryMoodSave?: boolean;
      __releaseDiaryMoodSave?: () => void;
    };
    const control = window as MoodSaveControl;
    control.__holdDiaryMoodSave = true;
    control.__diaryMoodSaveStarted = false;
    const original = DiaryStore.prototype.enqueueEntry;
    const delayed: typeof original = async function (
      this: import("../src/diary/store").DiaryStore,
      input,
    ) {
      if (control.__holdDiaryMoodSave) {
        control.__holdDiaryMoodSave = false;
        control.__diaryMoodSaveStarted = true;
        await new Promise<void>((resolve) => {
          control.__releaseDiaryMoodSave = resolve;
        });
      }
      return original.call(this, input);
    };
    DiaryStore.prototype.enqueueEntry = delayed;
  });

  const opener = page.getByRole("button", { name: /Настроение/ }).first();
  await opener.click();
  let sheet = page.getByRole("dialog", { name: "Как вы сейчас?" });
  await sheet.getByRole("button", { name: "Ровно" }).click();
  await expect
    .poll(() =>
      page.evaluate(
        () => (window as Window & { __diaryMoodSaveStarted?: boolean }).__diaryMoodSaveStarted,
      ),
    )
    .toBe(true);
  await sheet.getByRole("button", { name: "Закрыть" }).click();

  await opener.click();
  sheet = page.getByRole("dialog", { name: "Как вы сейчас?" });
  await expect(sheet).toBeVisible();
  await page.evaluate(() => {
    const control = window as Window & { __releaseDiaryMoodSave?: () => void };
    control.__releaseDiaryMoodSave?.();
  });
  await expect
    .poll(async () =>
      page.evaluate(async (ownerId) => {
        const { DiaryStore } = await import("/patient/src/diary/store.ts");
        return (await DiaryStore.open()).listEntries(ownerId).then((entries) => entries.length);
      }, PATIENT_A),
    )
    .toBe(1);

  await sheet.getByRole("button", { name: "Хорошо" }).click();
  await expect
    .poll(async () =>
      page.evaluate(async (ownerId) => {
        const { DiaryStore } = await import("/patient/src/diary/store.ts");
        return (await DiaryStore.open()).listEntries(ownerId).then((entries) => entries.length);
      }, PATIENT_A),
    )
    .toBe(2);
  const entries = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    return (await DiaryStore.open()).listEntries(ownerId);
  }, PATIENT_A);
  expect(entries.map((entry) => entry.entry_id)).toHaveLength(2);
  expect(new Set(entries.map((entry) => entry.entry_id)).size).toBe(2);
});
