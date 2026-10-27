import { expect, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { DEFINITION_FORM, PATIENT_A } from "./assessment-fixtures";
import { mockDiaryJourneyApi } from "./diary-journey-api-mock";
import { appendSyntheticWorkerRevision, restoreWorkerArtifact } from "./pwa-worker-artifact.mjs";
import { installSyntheticWebmRecorder, SYNTHETIC_WEBM_BASE64 } from "./synthetic-webm";

test("production service worker keeps the app shell offline and never caches API data", async ({
  page,
  context,
}) => {
  const api = await mockAssessmentApi(page.context());
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/patient/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);

  await page.getByRole("link", { name: "Заполнить" }).click();
  await page
    .getByRole("group", { name: "I felt calm." })
    .getByRole("radio", { name: "Sometimes" })
    .check();
  await page
    .getByRole("group", { name: "I could rest." })
    .getByRole("radio", { name: "Often" })
    .check();
  api.setNetworkAvailable(false);
  await context.setOffline(true);
  await page.getByRole("button", { name: "Отправить ответы" }).click();
  await expect(
    page.getByText("Сохранено на устройстве. Отправим, когда появится связь."),
  ).toBeVisible();
  expect(api.submissions).toHaveLength(0);

  await page.reload();
  await expect(page.getByRole("heading", { name: DEFINITION_FORM.scale.title })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);
  const apiResponse = await page.evaluate(async () => {
    try {
      const response = await fetch("/api/v1/auth/me");
      return response.status;
    } catch {
      return null;
    }
  });
  expect(apiResponse).toBeNull();

  await page.getByRole("link", { name: "Назад" }).click();
  await expect(page.getByText(/Офлайн-режим/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toBeVisible();
  await expect(page.getByText("I felt calm. — Sometimes")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Динамика" })).toHaveCount(0);

  api.setNetworkAvailable(true);
  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => api.acknowledgedSubmissions.length).toBe(1);
  await expect(page.getByRole("heading", { name: "Ожидают отправки" })).toHaveCount(0);
  expect(api.submissions).toHaveLength(1);
  expect(api.acknowledgedSubmissions[0]?.answers).toEqual({ calm: 2, rest: 4 });
});

test("production PWA keeps an offline daily voice entry and its audio after reload", async ({
  page,
  context,
}) => {
  const api = await mockDiaryJourneyApi(context);
  await installSyntheticWebmRecorder(page);
  await page.setViewportSize({ width: 360, height: 800 });
  const scheduleResponse = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/assessments/schedule"),
  );
  await page.goto("/patient/");
  await scheduleResponse;
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);
  await expect(page.getByRole("heading", { name: "Как вы сейчас?" })).toBeVisible();

  api.setNetworkAvailable(false);
  await context.setOffline(true);
  await page.getByRole("button", { name: /Начать ежедневную отметку.*Ровно/ }).click();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Как прошёл день?" })).toBeVisible();
  await page.getByRole("button", { name: "Начать запись" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Идёт запись" })).toBeVisible();
  await page.waitForTimeout(40);
  await page.getByRole("button", { name: "Остановить запись" }).click();
  await expect(page.getByText(/Голосовая запись сохранена на устройстве/)).toBeVisible();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Лекарства" })).toBeVisible();
  await page.getByRole("button", { name: "Дальше" }).click();
  await expect(page.getByRole("heading", { name: "Отметка сохранена" })).toBeVisible();

  await page.getByRole("button", { name: "Готово" }).click();
  await expect(page.getByRole("heading", { name: "Отметка сделана" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Отметка сделана" })).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);

  const stored = await page.evaluate(async (ownerId) => {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("clin-compass-assessments", 2);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const requestResult = <T>(request: IDBRequest<T>) =>
      new Promise<T>((resolve, reject) => {
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
    const entryTransaction = database.transaction("diaryEntries", "readonly");
    const entryRequest = entryTransaction
      .objectStore("diaryEntries")
      .index("owner_id")
      .getAll(ownerId);
    const entries = (await requestResult(entryRequest)) as Array<Record<string, unknown>>;
    const entry = entries.find((candidate) => candidate.kind === "daily");
    if (!entry || typeof entry.entry_id !== "string")
      throw new Error("Daily entry was not durable");
    const audioTransaction = database.transaction(["diaryAudio", "diaryOutbox"], "readonly");
    const audioRequest = audioTransaction.objectStore("diaryAudio").get([ownerId, entry.entry_id]);
    const outboxRequest = audioTransaction
      .objectStore("diaryOutbox")
      .index("owner_id")
      .getAll(ownerId);
    const [audio, outbox] = await Promise.all([
      requestResult(audioRequest),
      requestResult(outboxRequest),
    ]);
    if (!audio || typeof audio !== "object" || !("blob" in audio)) {
      throw new Error("Daily audio was not durable");
    }
    const durableAudio = audio as { blob: Blob; sha256: string };
    const bytes = new Uint8Array(await durableAudio.blob.arrayBuffer());
    const base64 = btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""));
    return {
      entry: { entry_id: entry.entry_id, kind: entry.kind, sync_state: entry.sync_state },
      audio: {
        type: durableAudio.blob.type,
        size: durableAudio.blob.size,
        sha256: durableAudio.sha256,
        base64,
      },
      outbox: (outbox as Array<Record<string, unknown>>).map((command) => ({
        action: command.action,
        state: command.state,
      })),
    };
  }, PATIENT_A);
  expect(stored.entry).toMatchObject({ kind: "daily", sync_state: "pending" });
  expect(stored.audio).toMatchObject({
    type: "audio/webm;codecs=opus",
    base64: SYNTHETIC_WEBM_BASE64,
  });
  expect(stored.audio.size).toBeGreaterThan(0);
  expect(stored.audio.sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(stored.outbox).toContainEqual({ action: "voice", state: "pending" });

  const apiResponse = await page.evaluate(async () => {
    try {
      return (await fetch("/api/v1/auth/me")).status;
    } catch {
      return null;
    }
  });
  expect(apiResponse).toBeNull();
  const cachedApiKeys = await page.evaluate(async () => {
    const names = await caches.keys();
    const keys = await Promise.all(
      names.map(async (name) =>
        (await (await caches.open(name)).keys()).map((request) => request.url),
      ),
    );
    return keys.flat().filter((url) => new URL(url).pathname.startsWith("/api/"));
  });
  expect(cachedApiKeys).toEqual([]);
});

test("a production worker update waits for an explicit patient action", async ({ page }) => {
  await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);
  let documentLoads = 0;
  page.on("domcontentloaded", () => {
    documentLoads += 1;
  });
  const documentLoadsBeforeUpdate = documentLoads;

  const originalWorker = await appendSyntheticWorkerRevision();
  try {
    await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.getRegistration("/patient/");
      await registration?.update();
    });
    await expect
      .poll(() =>
        page.evaluate(async () => {
          const registration = await navigator.serviceWorker.getRegistration("/patient/");
          return registration?.waiting?.state ?? null;
        }),
      )
      .toBe("installed");
    await expect(page.getByRole("button", { name: "Обновить приложение" })).toBeVisible({
      timeout: 10_000,
    });
    expect(documentLoads).toBe(documentLoadsBeforeUpdate);

    await page.getByRole("link", { name: "Заполнить" }).click();
    await expect(page.getByRole("group", { name: "I felt calm." })).toBeVisible();
    expect(documentLoads).toBe(documentLoadsBeforeUpdate);
    await page.evaluate(() => {
      const prototype = IDBRequest.prototype as unknown as {
        addEventListener: (
          type: string,
          listener: EventListenerOrEventListenerObject | null,
          options?: boolean | AddEventListenerOptions,
        ) => void;
      };
      const nativeAddEventListener = prototype.addEventListener;
      let releaseRequested = false;
      let held = false;
      (window as Window & { releaseHeldLocalWrite?: () => void }).releaseHeldLocalWrite = () => {
        releaseRequested = true;
      };
      prototype.addEventListener = function (this: IDBRequest, type, listener, options) {
        if (!held && type === "success" && listener && this.transaction && this.source) {
          held = true;
          const source = this.source as IDBObjectStore;
          const transaction = this.transaction;
          const keepTransactionAlive = () => {
            const heartbeat = transaction.objectStore(source.name).get("__keepalive__");
            heartbeat.addEventListener(
              "success",
              () => {
                if (releaseRequested) {
                  if (typeof listener === "function") listener.call(this, new Event("success"));
                  else listener.handleEvent(new Event("success"));
                  return;
                }
                keepTransactionAlive();
              },
              { once: true },
            );
          };
          keepTransactionAlive();
          return;
        }
        return nativeAddEventListener.call(this, type, listener, options);
      };
    });
    await page
      .getByRole("group", { name: "I felt calm." })
      .getByRole("radio", { name: "Sometimes" })
      .check();
    const update = page.getByRole("button", { name: "Обновить приложение" });
    await expect(update).toBeDisabled();
    expect(documentLoads).toBe(documentLoadsBeforeUpdate);

    await page.evaluate(() =>
      (window as Window & { releaseHeldLocalWrite?: () => void }).releaseHeldLocalWrite?.(),
    );
    await expect(page.getByText("Ответы сохранены на устройстве.")).toBeVisible();
    await expect(update).toBeEnabled();
    await update.click();
    await expect.poll(() => documentLoads).toBeGreaterThan(documentLoadsBeforeUpdate);
    await expect(
      page.getByRole("heading", { name: DEFINITION_FORM.scale.title, level: 1 }),
    ).toBeVisible();
    await expect(
      page.getByRole("group", { name: "I felt calm." }).getByRole("radio", { name: "Sometimes" }),
    ).toBeChecked();
  } finally {
    await restoreWorkerArtifact(originalWorker);
  }
});
