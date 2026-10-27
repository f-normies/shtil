import type { DiaryEntryRecord } from "./types";

const TRANSCRIPT_POLL_MS = 3_000;

/** The server knows a record once its create command was accepted or it came from the server. */
export function isKnownToServer(entry: DiaryEntryRecord | null): boolean {
  if (!entry) return false;
  return entry.sync_state === "synced" || (entry.server_revision ?? 0) > 0;
}

/** Ask the server for a record only when it can exist there; a queued create would get 404. */
export function shouldFetchServerEntry(entry: DiaryEntryRecord | null, online: boolean): boolean {
  if (!online) return false;
  if (!entry) return true;
  return isKnownToServer(entry);
}

/** Poll the server copy while its transcription is still running. */
export function transcriptRefetchInterval(entry: DiaryEntryRecord | null): number | false {
  if (!entry || !isKnownToServer(entry)) return false;
  const status = entry.audio?.status;
  return status === "pending" || status === "processing" ? TRANSCRIPT_POLL_MS : false;
}

/** One neutral status line for the record: delivery first, then server transcription. */
export function diaryEntryStatusLabel(entry: DiaryEntryRecord, online: boolean): string | null {
  if (entry.sync_state === "pending") {
    return online ? "Отправляется…" : "Отправится, когда появится сеть";
  }
  if (entry.sync_state === "blocked") {
    return "Запись не отправлена. Проверьте подключение и попробуйте ещё раз.";
  }
  if (entry.sync_state === "conflict") return "Запись изменилась на сервере. Сохранены обе версии.";
  const status = entry.audio?.status;
  if (status === "pending" || status === "processing") return "Расшифровывается…";
  if (status === "failed") return "Не удалось расшифровать запись";
  return null;
}
