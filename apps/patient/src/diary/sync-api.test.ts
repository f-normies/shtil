import { expect, it } from "vitest";
import { createDiaryVoiceUploadFormData } from "./sync-api";

it("serializes voice metadata and the original recording as retryable multipart fields", async () => {
  const metadata = {
    operation_id: "00000000-0000-4000-8000-0000000000ca",
    entry_id: "00000000-0000-4000-8000-0000000000b5",
    kind: "voice" as const,
    daily_on: null,
    mood_level: null,
    other_medicine_text: null,
    occurred_at: "2026-10-23T10:00:00.000Z",
    duration_ms: 1200,
    mood_words: [],
  };
  const recording = new Blob(["stable voice bytes"], { type: "audio/webm" });

  const form = createDiaryVoiceUploadFormData(metadata, recording);
  const metadataPart = form.get("metadata");
  const filePart = form.get("file");

  expect(metadataPart).toBe(JSON.stringify(metadata));
  expect(filePart).toMatchObject({ name: "diary-audio.webm", type: "audio/webm" });
  expect(filePart).toBeInstanceOf(Blob);
  await expect((filePart as Blob).text()).resolves.toBe("stable voice bytes");
});
