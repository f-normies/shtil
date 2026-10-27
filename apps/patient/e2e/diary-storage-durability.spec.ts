import { expect, test } from "@playwright/test";
import { mockAssessmentAuth, openPatientPage } from "./assessment-test-helpers";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

test("rolls back the local entry when its outbox write aborts", async ({ page }) => {
  await openPatientPage(page);
  await page.evaluate(async () => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({
      id: "00000000-0000-4000-8000-0000000000a1",
      role: "patient",
    });
  });

  const result = await page.evaluate(async () => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    const objectStore = IDBObjectStore.prototype;
    const nativeAdd = objectStore.add;
    objectStore.add = function (...args) {
      const request = Reflect.apply(nativeAdd, this, args) as IDBRequest;
      if (this.name === "diaryOutbox") {
        request.addEventListener("success", () => request.transaction?.abort(), { once: true });
      }
      return request;
    };
    let failure = "";
    try {
      await store.enqueueEntry({
        ownerId: "00000000-0000-4000-8000-0000000000a1",
        payload: {
          entry_id: "00000000-0000-4000-8000-0000000000b2",
          operation_id: "00000000-0000-4000-8000-0000000000c2",
          kind: "text",
          occurred_at: "2026-10-23T10:00:00.000Z",
          text: "Не должен сохраниться",
        },
      });
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    } finally {
      objectStore.add = nativeAdd;
    }
    return {
      failure,
      entries: await store.listEntries("00000000-0000-4000-8000-0000000000a1"),
      commands: await store.getOutboxForOwner("00000000-0000-4000-8000-0000000000a1"),
    };
  });

  expect(result.failure).toMatch(/abort/i);
  expect(result.entries).toEqual([]);
  expect(result.commands).toEqual([]);
});

test("restores stopped audio after reload and denies it after the owner changes", async ({
  page,
}) => {
  await openPatientPage(page);
  await page.evaluate(async () => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({
      id: "00000000-0000-4000-8000-0000000000a1",
      role: "patient",
    });
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    await (await DiaryStore.open()).saveAudioBlob(
      "00000000-0000-4000-8000-0000000000a1",
      "00000000-0000-4000-8000-0000000000b3",
      new Blob(["finished recording"], { type: "audio/webm" }),
    );
  });

  await page.reload();
  const restored = await page.evaluate(async () => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const blob = await (await DiaryStore.open()).getAudioBlob(
      "00000000-0000-4000-8000-0000000000a1",
      "00000000-0000-4000-8000-0000000000b3",
    );
    return blob ? { type: blob.type, text: await blob.text() } : null;
  });
  expect(restored).toEqual({ type: "audio/webm", text: "finished recording" });

  await page.evaluate(async () => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({
      id: "00000000-0000-4000-8000-0000000000a2",
      role: "patient",
    });
  });
  const switchedOwnerAccess = await page.evaluate(async () => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    let previousOwnerDenied = false;
    try {
      await store.getAudioBlob(
        "00000000-0000-4000-8000-0000000000a1",
        "00000000-0000-4000-8000-0000000000b3",
      );
    } catch {
      previousOwnerDenied = true;
    }
    return {
      previousOwnerDenied,
      newOwnerBlob: await store.getAudioBlob(
        "00000000-0000-4000-8000-0000000000a2",
        "00000000-0000-4000-8000-0000000000b3",
      ),
      newOwnerEntries: await store.listEntries("00000000-0000-4000-8000-0000000000a2"),
    };
  });
  expect(switchedOwnerAccess).toEqual({
    previousOwnerDenied: true,
    newOwnerBlob: null,
    newOwnerEntries: [],
  });
});
