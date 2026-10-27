import { describe, expect, it } from "vitest";
import {
  diaryEntryStatusLabel,
  shouldFetchServerEntry,
  transcriptRefetchInterval,
} from "./entry-status";
import type { DiaryEntryRecord } from "./types";

function record(overrides: Partial<DiaryEntryRecord>): DiaryEntryRecord {
  return {
    owner_id: "owner-a",
    entry_id: "entry-1",
    kind: "voice",
    occurred_at: "2026-10-27T10:00:00.000Z",
    daily_on: null,
    mood_level: null,
    mood_words: [],
    text: null,
    other_medicine_text: null,
    audio: null,
    revision: 1,
    server_revision: 0,
    sync_state: "pending",
    remote_snapshot: null,
    ...overrides,
  };
}

describe("shouldFetchServerEntry", () => {
  it("does not ask the server for a record that has never been accepted", () => {
    expect(shouldFetchServerEntry(record({}), true)).toBe(false);
  });
  it("asks once the create was confirmed, even with a later edit still queued", () => {
    expect(shouldFetchServerEntry(record({ server_revision: 1, revision: 2 }), true)).toBe(true);
    expect(shouldFetchServerEntry(record({ sync_state: "synced" }), true)).toBe(true);
  });
  it("asks when the record is unknown locally and stays quiet offline", () => {
    expect(shouldFetchServerEntry(null, true)).toBe(true);
    expect(shouldFetchServerEntry(null, false)).toBe(false);
  });
});

describe("transcriptRefetchInterval", () => {
  const audio = { duration_ms: 1000, id: "a", transcript: null, transcript_source_id: null };
  it("polls while the server transcription is not final", () => {
    expect(
      transcriptRefetchInterval(
        record({ sync_state: "synced", audio: { ...audio, status: "pending" } }),
      ),
    ).toBe(3000);
    expect(
      transcriptRefetchInterval(
        record({ sync_state: "synced", audio: { ...audio, status: "processing" } }),
      ),
    ).toBe(3000);
  });
  it("stops when the transcription is final or the record is not on the server", () => {
    expect(
      transcriptRefetchInterval(
        record({ sync_state: "synced", audio: { ...audio, status: "done", transcript: "x" } }),
      ),
    ).toBe(false);
    expect(transcriptRefetchInterval(record({}))).toBe(false);
    expect(transcriptRefetchInterval(null)).toBe(false);
  });
});

describe("diaryEntryStatusLabel", () => {
  const audio = { duration_ms: 1000, id: "a", transcript: null, transcript_source_id: null };
  it("says the record is being sent while online and waits for a network offline", () => {
    expect(diaryEntryStatusLabel(record({}), true)).toBe("Отправляется…");
    expect(diaryEntryStatusLabel(record({}), false)).toBe("Отправится, когда появится сеть");
  });
  it("shows the server transcription state after delivery", () => {
    expect(
      diaryEntryStatusLabel(
        record({ sync_state: "synced", audio: { ...audio, status: "processing" } }),
        true,
      ),
    ).toBe("Расшифровывается…");
    expect(
      diaryEntryStatusLabel(
        record({ sync_state: "synced", audio: { ...audio, status: "done", transcript: "x" } }),
        true,
      ),
    ).toBeNull();
    expect(
      diaryEntryStatusLabel(
        record({ sync_state: "synced", audio: { ...audio, status: "failed" } }),
        true,
      ),
    ).toBe("Не удалось расшифровать запись");
  });
});
