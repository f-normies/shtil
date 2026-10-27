import type { Schemas } from "@compass/api-client";
import type { DiaryEntryRecord } from "./types";

export type { DiaryEntryRecord } from "./types";

/** Merge one complete local view with a server page by stable entry ID. */
export function mergeDiaryPage(
  ownerId: string,
  localEntries: readonly DiaryEntryRecord[],
  serverEntries: readonly Schemas["DiaryEntryOut"][],
): DiaryEntryRecord[] {
  const merged = new Map<string, DiaryEntryRecord>();
  for (const entry of localEntries) {
    if (entry.owner_id === ownerId) merged.set(entry.entry_id, entry);
  }

  for (const serverEntry of serverEntries) {
    const localEntry = merged.get(serverEntry.entry_id);
    if (!localEntry) {
      merged.set(serverEntry.entry_id, {
        ...serverEntry,
        owner_id: ownerId,
        sync_state: "synced",
        remote_snapshot: null,
        hidden: false,
      });
      continue;
    }
    if (localEntry.sync_state === "synced") {
      merged.set(serverEntry.entry_id, {
        ...localEntry,
        ...serverEntry,
        owner_id: ownerId,
        sync_state: "synced",
        remote_snapshot: null,
        hidden: localEntry.hidden ?? false,
      });
      continue;
    }
    if (
      localEntry.sync_state !== "conflict" &&
      serverEntry.revision <= (localEntry.server_revision ?? 0)
    ) {
      // The server holds only what this device already sent; a queued edit is not a conflict.
      // Transcription and processing results still come from the server copy.
      merged.set(serverEntry.entry_id, {
        ...localEntry,
        audio: serverEntry.audio ?? localEntry.audio,
        source_statuses: serverEntry.source_statuses,
        remote_snapshot: null,
      });
      continue;
    }
    merged.set(serverEntry.entry_id, { ...localEntry, remote_snapshot: serverEntry });
  }

  return [...merged.values()].sort(
    (left, right) =>
      right.occurred_at.localeCompare(left.occurred_at) ||
      left.entry_id.localeCompare(right.entry_id),
  );
}
