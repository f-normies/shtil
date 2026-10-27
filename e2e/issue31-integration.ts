import { type Browser, expect, type Page, type TestInfo } from "@playwright/test";
import {
  installSyntheticWebmRecorder,
  SYNTHETIC_WEBM_BASE64,
} from "../apps/patient/e2e/synthetic-webm.js";

const APP_ORIGIN = "http://127.0.0.1:5173";
const TEST_PASSWORD = "Compass-E2E-21";

type Schedule = {
  today: string;
  items: Array<{ scale_id: string; scale_title: string; due_on: string }>;
};
type AssessmentReceipt = { id: string; submission_id: string; scale_id: string };
type LocalDiaryEntry = {
  entry_id: string;
  kind: string;
  daily_on: string | null;
  mood_level: number | null;
  sync_state: string;
  survey_status?: string;
  survey_submission_ids?: string[];
};

async function signInPatient(page: Page): Promise<void> {
  await page.goto(`${APP_ORIGIN}/patient/login`);
  await page.getByLabel("Электронная почта", { exact: true }).fill("patient4@example.com");
  await page.getByLabel("Пароль", { exact: true }).fill(TEST_PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await page.getByRole("button", { name: "Пропустить", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Как вы сейчас?" })).toBeVisible();
}

async function localSurveySubmissionIds(page: Page, ownerId: string): Promise<string[]> {
  return page.evaluate(async (patientId) => {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("clin-compass-assessments", 2);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const transaction = database.transaction("diaryDrafts", "readonly");
    const request = transaction.objectStore("diaryDrafts").index("owner_id").getAll(patientId);
    const drafts = await new Promise<Array<Record<string, unknown>>>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result as Array<Record<string, unknown>>);
      request.onerror = () => reject(request.error);
    });
    database.close();
    const draft = drafts.find((candidate) => candidate.mode === "daily");
    const ids = draft?.survey_submission_ids;
    return Array.isArray(ids) ? ids.filter((id): id is string => typeof id === "string") : [];
  }, ownerId);
}

async function localDailyEntry(
  page: Page,
  ownerId: string,
  entryId: string,
): Promise<LocalDiaryEntry | null> {
  return page.evaluate(
    async ({ patientId, id }) => {
      const database = await new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open("clin-compass-assessments", 2);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      const transaction = database.transaction("diaryEntries", "readonly");
      const request = transaction.objectStore("diaryEntries").index("owner_id").getAll(patientId);
      const entries = await new Promise<Array<Record<string, unknown>>>((resolve, reject) => {
        request.onsuccess = () => resolve(request.result as Array<Record<string, unknown>>);
        request.onerror = () => reject(request.error);
      });
      database.close();
      return (
        (entries.find((entry) => entry.entry_id === id) as LocalDiaryEntry | undefined) ?? null
      );
    },
    { patientId: ownerId, id: entryId },
  );
}

async function submitActiveScale(page: Page, expectedScaleId: string): Promise<AssessmentReceipt> {
  const fields = page.locator(".patient-assessment-embedded fieldset");
  const count = await fields.count();
  expect(count, `${expectedScaleId} should use its loaded knowledge definition`).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    await fields.nth(index).locator('input[type="radio"]').first().check();
  }

  const submissionResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" && response.url().endsWith("/api/v1/assessments"),
    { timeout: 30_000 },
  );
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  const response = await submissionResponse;
  expect(response.status()).toBe(201);
  const receipt = (await response.json()) as AssessmentReceipt;
  expect(receipt.scale_id).toBe(expectedScaleId);
  expect(receipt.submission_id).toMatch(/^[0-9a-f-]{36}$/i);
  return receipt;
}

/** Extend the preserved M1 run with live issue #31 acceptance evidence after its measured boundary. */
export async function runIssue31IntegrationEvidence(
  browser: Browser,
  testInfo: TestInfo,
): Promise<void> {
  const context = await browser.newContext({
    viewport: { width: 360, height: 800 },
    serviceWorkers: "block",
  });
  const evidence: Record<string, unknown> = { status: "in-progress" };
  try {
    const page = await context.newPage();
    await installSyntheticWebmRecorder(page);
    await signInPatient(page);

    const userResponse = await context.request.get(`${APP_ORIGIN}/api/v1/auth/me`);
    expect(userResponse.status()).toBe(200);
    const user = (await userResponse.json()) as { id: string; role: string };
    expect(user.role).toBe("patient");

    const scheduleResponse = await context.request.get(`${APP_ORIGIN}/api/v1/assessments/schedule`);
    expect(scheduleResponse.status()).toBe(200);
    const schedule = (await scheduleResponse.json()) as Schedule;
    const dueItems = schedule.items
      .filter((item) => item.due_on <= schedule.today)
      .sort((left, right) => left.scale_id.localeCompare(right.scale_id));
    const dueScaleIds = dueItems.map((item) => item.scale_id);
    expect(dueScaleIds).toEqual(["gad-7", "phq-9"]);
    evidence.schedule = {
      today: schedule.today,
      due: dueItems.map(({ scale_id, scale_title, due_on }) => ({
        scale_id,
        scale_title,
        due_on,
      })),
    };

    await page.getByRole("button", { name: "Начать ежедневную отметку: Ровно" }).click();
    await expect(page.getByRole("heading", { name: "Как вы сейчас?" })).toBeVisible();
    await page.getByRole("button", { name: "Дальше" }).click();
    await expect(page.getByRole("heading", { name: "Как прошёл день?" })).toBeVisible();
    await page.getByRole("button", { name: "Начать запись" }).click();
    await expect(page.getByRole("button", { name: "Остановить запись" })).toBeVisible();
    await page.waitForTimeout(60);
    await page.getByRole("button", { name: "Остановить запись" }).click();
    await expect(page.getByText(/Голосовая запись сохранена на устройстве/)).toBeVisible();
    await page.getByRole("button", { name: "Дальше" }).click();
    await expect(page.getByRole("heading", { name: "Лекарства на сегодня" })).toBeVisible();
    await page.getByRole("button", { name: "Дальше" }).click();
    await expect(page.locator(".patient-survey-step-label")).toContainText("1 из 2");

    const submissions: AssessmentReceipt[] = [];
    for (const [index, scaleId] of dueScaleIds.entries()) {
      await expect(page.locator(".patient-survey-step-label")).toContainText(
        `${index + 1} из ${dueScaleIds.length}`,
      );
      submissions.push(await submitActiveScale(page, scaleId));
      await expect
        .poll(() => localSurveySubmissionIds(page, user.id))
        .toEqual(submissions.map((receipt) => receipt.submission_id));
    }
    evidence.assessments = submissions.map(({ id, submission_id, scale_id }) => ({
      id,
      submission_id,
      scale_id,
    }));

    await expect(page.getByRole("heading", { name: "Опросы сохранены" })).toBeVisible();
    const voiceUploadResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith("/api/v1/diary/entries/voice"),
      { timeout: 30_000 },
    );
    await page.getByRole("button", { name: "Сохранить отметку" }).click();
    await expect(page.getByRole("heading", { name: "Спасибо, отметка сохранена" })).toBeVisible();
    const upload = await voiceUploadResponse;
    expect(upload.status()).toBe(201);
    const uploadContentType = await upload.request().headerValue("content-type");
    expect(uploadContentType).toMatch(/^multipart\/form-data;\s*boundary=/i);
    const voiceReceipt = (await upload.json()) as { entry_id: string; operation_id: string };
    expect(voiceReceipt.entry_id).toMatch(/^[0-9a-f-]{36}$/i);

    await expect
      .poll(() => localDailyEntry(page, user.id, voiceReceipt.entry_id))
      .toMatchObject({
        entry_id: voiceReceipt.entry_id,
        kind: "daily",
        sync_state: "synced",
        survey_status: "queued",
        survey_submission_ids: submissions.map((receipt) => receipt.submission_id),
      });
    const localEntry = await localDailyEntry(page, user.id, voiceReceipt.entry_id);
    expect(localEntry).toMatchObject({ kind: "daily", daily_on: schedule.today, mood_level: 3 });

    const serverEntryResponse = await context.request.get(
      `${APP_ORIGIN}/api/v1/diary/entries/${voiceReceipt.entry_id}`,
    );
    expect(serverEntryResponse.status()).toBe(200);
    const serverEntry = (await serverEntryResponse.json()) as {
      entry_id: string;
      kind: string;
      daily_on: string;
      mood_level: number;
      audio: { status: string; duration_ms: number } | null;
    };
    expect(serverEntry).toMatchObject({
      entry_id: voiceReceipt.entry_id,
      kind: "daily",
      daily_on: schedule.today,
      mood_level: 3,
      audio: { status: "pending" },
    });
    expect(serverEntry.audio?.duration_ms).toBeGreaterThan(0);

    const audioResponse = await context.request.get(
      `${APP_ORIGIN}/api/v1/diary/entries/${voiceReceipt.entry_id}/audio`,
    );
    expect(audioResponse.status()).toBe(200);
    expect(audioResponse.headers()["content-type"]).toContain("audio/webm");
    expect((await audioResponse.body()).equals(Buffer.from(SYNTHETIC_WEBM_BASE64, "base64"))).toBe(
      true,
    );
    evidence.voice = {
      entry_id: voiceReceipt.entry_id,
      operation_id: voiceReceipt.operation_id,
      upload_status: upload.status(),
      multipart_content_type: uploadContentType,
      server_audio_status: serverEntry.audio?.status,
      server_duration_ms: serverEntry.audio?.duration_ms,
      audio_download_status: audioResponse.status(),
      audio_bytes_match_synthetic_fixture: true,
    };
    evidence.status = "passed";
  } finally {
    await testInfo.attach("issue31-integration-evidence", {
      body: Buffer.from(JSON.stringify(evidence, null, 2)),
      contentType: "application/json",
    });
    await context.close();
  }
}
