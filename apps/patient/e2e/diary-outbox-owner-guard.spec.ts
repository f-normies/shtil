import { expect, test } from "@playwright/test";
import { mockAssessmentAuth, openPatientPage } from "./assessment-test-helpers";

const OWNER_ID = "00000000-0000-4000-8000-0000000000a1";

test.beforeEach(async ({ context }) => mockAssessmentAuth(context));

test("does not upload a voice Blob if ownership changes while its digest is computed", async ({
  page,
}) => {
  await openPatientPage(page);
  await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    await (await AssessmentStore.open()).rememberOnlinePatient({ id: ownerId, role: "patient" });
  }, OWNER_ID);

  const result = await page.evaluate(async (ownerId) => {
    const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const { createDiarySyncEngine } = await import("/patient/src/diary/sync.ts");
    const assessments = await AssessmentStore.open();
    const diary = await DiaryStore.open();
    await diary.enqueueVoiceEntry({
      ownerId,
      payload: {
        operation_id: "00000000-0000-4000-8000-0000000000ce",
        entry_id: "00000000-0000-4000-8000-0000000000b8",
        kind: "voice",
        daily_on: null,
        mood_level: null,
        other_medicine_text: null,
        occurred_at: "2026-10-23T10:00:00.000Z",
        duration_ms: 1200,
        mood_words: [],
      },
      blob: new Blob(["owner-bound recording"], { type: "audio/webm" }),
    });

    let announceDigestStarted: () => void = () => {};
    let continueDigest: () => void = () => {};
    const digestStarted = new Promise<void>((resolve) => {
      announceDigestStarted = resolve;
    });
    const digestPaused = new Promise<void>((resolve) => {
      continueDigest = resolve;
    });
    const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
    const digestDescriptor = Object.getOwnPropertyDescriptor(crypto.subtle, "digest");
    Object.defineProperty(crypto.subtle, "digest", {
      configurable: true,
      value: async (algorithm: AlgorithmIdentifier, data: BufferSource) => {
        announceDigestStarted();
        await digestPaused;
        return originalDigest(algorithm, data);
      },
    });

    let uploadCalls = 0;
    const engine = createDiarySyncEngine(diary, assessments, {
      getCurrentUser: async () => ({ id: ownerId, role: "patient" }),
      getSessionGeneration: () => 7,
      createEntry: async () => ({ status: null }),
      uploadVoice: async () => {
        uploadCalls += 1;
        return { status: 201 };
      },
    });
    const syncing = engine.sync(ownerId, 7);
    await digestStarted;
    await assessments.rememberOnlinePatient({
      id: "00000000-0000-4000-8000-0000000000a2",
      role: "patient",
    });
    continueDigest();
    const outcome = await syncing;
    if (digestDescriptor) Object.defineProperty(crypto.subtle, "digest", digestDescriptor);
    else Reflect.deleteProperty(crypto.subtle, "digest");
    return { outcome, uploadCalls };
  }, OWNER_ID);

  expect(result.outcome.status).toBe("owner-mismatch");
  expect(result.uploadCalls).toBe(0);
});
