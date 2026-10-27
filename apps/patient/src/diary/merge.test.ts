import type { Schemas } from "@compass/api-client";
import { expect, it } from "vitest";
import { mergeDiaryPage } from "./merge";

function serverEntry(entryId: string, text: string): Schemas["DiaryEntryOut"] {
  return {
    audio: null,
    daily_on: null,
    entry_id: entryId,
    kind: "text",
    mood_level: null,
    mood_words: [],
    occurred_at: "2026-10-22T10:00:00.000Z",
    other_medicine_text: null,
    received_at: "2026-10-22T10:01:00.000Z",
    revision: 1,
    source_statuses: [],
    text,
  };
}

it("merges by entry ID and keeps local records that a server page omits", () => {
  const pending = {
    owner_id: "owner-a",
    entry_id: "pending-entry",
    kind: "text" as const,
    occurred_at: "2026-10-22T12:00:00.000Z",
    daily_on: null,
    mood_level: null,
    mood_words: [],
    text: "Local text awaiting delivery",
    other_medicine_text: null,
    audio: null,
    revision: 2,
    sync_state: "pending" as const,
    remote_snapshot: null,
  };
  const confirmed = {
    ...pending,
    entry_id: "confirmed-entry",
    text: "Old cached text",
    sync_state: "synced" as const,
  };
  const offlineOnly = { ...pending, entry_id: "offline-only-entry" };

  const merged = mergeDiaryPage(
    "owner-a",
    [pending, confirmed, offlineOnly],
    [
      serverEntry("pending-entry", "Server copy after a lost response"),
      serverEntry("confirmed-entry", "Updated server text"),
      serverEntry("server-only-entry", "Server-only record"),
    ],
  );

  expect(merged).toHaveLength(4);
  expect(merged.filter((entry) => entry.entry_id === "pending-entry")).toHaveLength(1);
  expect(merged.find((entry) => entry.entry_id === "pending-entry")).toMatchObject({
    text: "Local text awaiting delivery",
    sync_state: "pending",
    remote_snapshot: { text: "Server copy after a lost response" },
  });
  expect(merged.find((entry) => entry.entry_id === "confirmed-entry")).toMatchObject({
    text: "Updated server text",
    sync_state: "synced",
  });
  expect(merged.find((entry) => entry.entry_id === "offline-only-entry")).toMatchObject({
    owner_id: "owner-a",
    text: "Local text awaiting delivery",
  });
  expect(merged.find((entry) => entry.entry_id === "server-only-entry")).toMatchObject({
    owner_id: "owner-a",
    text: "Server-only record",
    sync_state: "synced",
  });
});

it("keeps a pending edit without a conflict when the server copy is the revision it already knows", () => {
  const edited = {
    owner_id: "owner-a",
    entry_id: "edited-entry",
    kind: "text" as const,
    occurred_at: "2026-10-22T12:00:00.000Z",
    daily_on: null,
    mood_level: null,
    mood_words: [],
    text: "Edited locally, update still queued",
    other_medicine_text: null,
    audio: null,
    revision: 2,
    server_revision: 1,
    sync_state: "pending" as const,
    remote_snapshot: null,
  };
  const [merged] = mergeDiaryPage(
    "owner-a",
    [edited],
    [serverEntry("edited-entry", "Server rev 1")],
  );
  expect(merged).toMatchObject({
    text: "Edited locally, update still queued",
    sync_state: "pending",
    remote_snapshot: null,
  });
});

it("takes server-side transcription results into a locally pending voice record", () => {
  const voice = {
    owner_id: "owner-a",
    entry_id: "voice-entry",
    kind: "voice" as const,
    occurred_at: "2026-10-22T12:00:00.000Z",
    daily_on: null,
    mood_level: null,
    mood_words: [],
    text: null,
    other_medicine_text: null,
    audio: null,
    revision: 2,
    server_revision: 1,
    sync_state: "pending" as const,
    remote_snapshot: null,
  };
  const server = {
    ...serverEntry("voice-entry", ""),
    kind: "voice" as const,
    text: null,
    audio: {
      duration_ms: 8000,
      id: "audio-1",
      status: "done" as const,
      transcript: "Сегодня спала лучше",
      transcript_source_id: null,
    },
  };
  const [merged] = mergeDiaryPage("owner-a", [voice], [server]);
  expect(merged?.audio).toMatchObject({ status: "done", transcript: "Сегодня спала лучше" });
  expect(merged?.remote_snapshot).toBeNull();
});
