import { denyOfflineAssessmentAccess } from "../assessments/offline-access";
import { withOwnerSyncLock } from "../assessments/owner-sync-lock";
import type { AssessmentStore } from "../assessments/store";
import type { PatientIdentity } from "../assessments/types";
import { digestDiaryAudio } from "./outbox-command-helpers";
import { classifyDiaryResponse } from "./queue-policy";
import type { DiaryStore } from "./store";
import {
  type DiaryCommandResponse,
  type DiaryEntryFetchResponse,
  type DiarySyncAdapter,
  diarySyncAdapter,
  fetchedEntry,
  type IntakeCommandResponse,
} from "./sync-api";
import type { DiaryOutboxRecord, IntakeOutboxRecord } from "./types";

export type DiarySyncOutcome = {
  status: "complete" | "waiting" | "authentication-required" | "owner-mismatch" | "session-changed";
  sessionGeneration: number | null;
  submitted: number;
  blocked: number;
  confirmedOperationIds: string[];
};

function makeOutcome(
  status: DiarySyncOutcome["status"],
  sessionGeneration: number | null,
  confirmedOperationIds: string[] = [],
  blocked = 0,
): DiarySyncOutcome {
  return {
    status,
    sessionGeneration,
    submitted: confirmedOperationIds.length,
    blocked,
    confirmedOperationIds,
  };
}

/** Run shared owner verification, then drain explicit intake marks before diary entries. */
async function synchronize(
  diary: DiaryStore,
  assessments: AssessmentStore,
  adapter: DiarySyncAdapter,
  ownerId: string,
  sessionGeneration: number | null,
): Promise<DiarySyncOutcome> {
  const result = (status: DiarySyncOutcome["status"], confirmed: string[] = [], blocked = 0) =>
    makeOutcome(status, sessionGeneration, confirmed, blocked);
  const isCurrentSession = () => {
    const currentGeneration = adapter.getSessionGeneration?.();
    return (
      currentGeneration === undefined ||
      sessionGeneration === null ||
      currentGeneration === sessionGeneration
    );
  };
  const currentOwnerStatus = async (): Promise<DiarySyncOutcome["status"] | null> => {
    if (!isCurrentSession()) return "session-changed";
    const pointer = await assessments.getOwnerPointer();
    if (!isCurrentSession()) return "session-changed";
    if (pointer?.id !== ownerId || pointer.role !== "patient") return "owner-mismatch";
    if (pointer.locked) return "authentication-required";
    return null;
  };
  const lockAfterRejectedRequest = async (
    fallback: "authentication-required" | "owner-mismatch",
  ): Promise<DiarySyncOutcome["status"]> => {
    try {
      await assessments.lockOwnerPointer(ownerId);
    } catch {
      denyOfflineAssessmentAccess();
      return isCurrentSession() ? fallback : "session-changed";
    }
    if (!isCurrentSession()) return "session-changed";
    const pointer = await assessments.getOwnerPointer();
    if (!isCurrentSession()) return "session-changed";
    if (pointer?.id !== ownerId || pointer.role !== "patient") return "owner-mismatch";
    return fallback;
  };
  const previousOwner = await assessments.getOwnerPointer();
  if (!isCurrentSession()) return result("session-changed");
  if (!previousOwner) return result("complete");
  if (previousOwner.id !== ownerId || previousOwner.role !== "patient") {
    return result("owner-mismatch");
  }

  let patient: PatientIdentity | null;
  try {
    patient = await adapter.getCurrentUser();
  } catch {
    return result(isCurrentSession() ? "waiting" : "session-changed");
  }
  if (!isCurrentSession()) return result("session-changed");
  if (!patient) {
    return result(await lockAfterRejectedRequest("authentication-required"));
  }
  if (patient.role !== "patient" || patient.id !== ownerId) return result("owner-mismatch");
  const verifiedOwner = await assessments.rememberOnlinePatientIfUnchanged(patient, previousOwner);
  if (!isCurrentSession()) return result("session-changed");
  if (!verifiedOwner) return result("owner-mismatch");

  const confirmed: string[] = [];
  let blocked = 0;
  let waiting = false;
  const intakeCommands = await diary.getIntakeOutboxForOwner(ownerId);
  for (const command of intakeCommands) {
    if (command.state !== "pending") continue;
    const beforeRequest = await currentOwnerStatus();
    if (beforeRequest) return result(beforeRequest, confirmed, blocked);
    if (!adapter.createIntakeMark) {
      waiting = true;
      continue;
    }
    const response = await callIntake(adapter, command);
    const afterRequest = await currentOwnerStatus();
    if (afterRequest) return result(afterRequest, confirmed, blocked);
    const decision = classifyDiaryResponse(response.status, response.code);
    if (decision.kind === "confirmed") {
      if (!response.mark) {
        waiting = true;
        continue;
      }
      try {
        await diary.confirmIntakeCommand({
          ownerId,
          operationId: command.operation_id,
          mark: response.mark,
        });
      } catch {
        const status = await currentOwnerStatus();
        return result(status ?? "waiting", confirmed, blocked);
      }
      confirmed.push(command.operation_id);
    } else if (decision.kind === "reauthenticate") {
      return result(await lockAfterRejectedRequest("authentication-required"), confirmed, blocked);
    } else if (decision.kind === "owner-mismatch") {
      return result(await lockAfterRejectedRequest("owner-mismatch"), confirmed, blocked);
    } else if (decision.kind === "retry") {
      waiting = true;
    } else {
      try {
        await diary.blockIntakeCommand({
          ownerId,
          operationId: command.operation_id,
          error:
            decision.kind === "conflict"
              ? { status: 409, code: response.code ?? "conflict" }
              : {
                  status: response.status ?? 422,
                  code: response.code ?? "invalid_intake_mark",
                },
        });
      } catch {
        const status = await currentOwnerStatus();
        return result(status ?? "waiting", confirmed, blocked);
      }
      blocked += 1;
    }
  }

  const diaryCommands = await diary.getOutboxForOwner(ownerId);
  const blockedEntryIds = new Set<string>();
  for (const command of diaryCommands) {
    if (blockedEntryIds.has(command.entry_id)) continue;
    if (command.state !== "pending") {
      blockedEntryIds.add(command.entry_id);
      continue;
    }
    const beforeRequest = await currentOwnerStatus();
    if (beforeRequest) return result(beforeRequest, confirmed, blocked);
    let voiceBlob: Blob | undefined;
    if (command.action === "voice") {
      try {
        voiceBlob = (await diary.getAudioBlob(ownerId, command.entry_id)) ?? undefined;
      } catch {
        const ownerStatus = await currentOwnerStatus();
        if (ownerStatus) return result(ownerStatus, confirmed, blocked);
        waiting = true;
        blockedEntryIds.add(command.entry_id);
        continue;
      }
      const afterBlobRead = await currentOwnerStatus();
      if (afterBlobRead) return result(afterBlobRead, confirmed, blocked);
      const audioHash = voiceBlob ? await digestDiaryAudio(voiceBlob) : null;
      const afterDigest = await currentOwnerStatus();
      if (afterDigest) return result(afterDigest, confirmed, blocked);
      if (!voiceBlob || audioHash !== command.audio_sha256) {
        try {
          await diary.blockCommand({
            ownerId,
            operationId: command.operation_id,
            error: { status: 422, code: voiceBlob ? "voice_blob_changed" : "voice_blob_missing" },
          });
        } catch {
          const ownerStatus = await currentOwnerStatus();
          return result(ownerStatus ?? "waiting", confirmed, blocked);
        }
        blocked += 1;
        blockedEntryIds.add(command.entry_id);
        continue;
      }
    }
    const response = await callDiary(adapter, command, voiceBlob);
    const afterRequest = await currentOwnerStatus();
    if (afterRequest) return result(afterRequest, confirmed, blocked);
    const decision = classifyDiaryResponse(response.status, response.code);
    if (decision.kind === "confirmed") {
      const receipt = response.receipt;
      if (
        !receipt ||
        receipt.entry_id !== command.entry_id ||
        receipt.operation_id !== command.operation_id
      ) {
        waiting = true;
        blockedEntryIds.add(command.entry_id);
        continue;
      }
      try {
        await diary.confirmCommand(ownerId, receipt);
      } catch {
        const status = await currentOwnerStatus();
        return result(status ?? "waiting", confirmed, blocked);
      }
      confirmed.push(command.operation_id);
    } else if (decision.kind === "reauthenticate") {
      return result(await lockAfterRejectedRequest("authentication-required"), confirmed, blocked);
    } else if (decision.kind === "owner-mismatch") {
      return result(await lockAfterRejectedRequest("owner-mismatch"), confirmed, blocked);
    } else if (decision.kind === "retry") {
      waiting = true;
      blockedEntryIds.add(command.entry_id);
    } else {
      try {
        await diary.blockCommand({
          ownerId,
          operationId: command.operation_id,
          error:
            decision.kind === "conflict"
              ? { status: 409, code: response.code ?? "conflict" }
              : { status: response.status ?? 422, code: response.code ?? "invalid_entry" },
        });
      } catch {
        const status = await currentOwnerStatus();
        return result(status ?? "waiting", confirmed, blocked);
      }
      blocked += 1;
      blockedEntryIds.add(command.entry_id);
      if (decision.kind === "conflict") {
        const conflictStatus = await attachRemoteSnapshot(
          diary,
          adapter,
          command,
          isCurrentSession,
          currentOwnerStatus,
          lockAfterRejectedRequest,
          ownerId,
        );
        if (conflictStatus) return result(conflictStatus, confirmed, blocked);
      }
    }
  }
  return result(waiting ? "waiting" : "complete", confirmed, blocked);
}

async function attachRemoteSnapshot(
  diary: DiaryStore,
  adapter: DiarySyncAdapter,
  command: DiaryOutboxRecord,
  isCurrentSession: () => boolean,
  currentOwnerStatus: () => Promise<DiarySyncOutcome["status"] | null>,
  lockAfterRejectedRequest: (
    fallback: "authentication-required" | "owner-mismatch",
  ) => Promise<DiarySyncOutcome["status"]>,
  ownerId: string,
): Promise<DiarySyncOutcome["status"] | null> {
  if (!adapter.fetchEntry) return null;
  let raw: DiaryEntryFetchResponse;
  try {
    raw = await adapter.fetchEntry(command.entry_id);
  } catch {
    return (await currentOwnerStatus()) ?? null;
  }
  if (!isCurrentSession()) return "session-changed";
  const ownerStatus = await currentOwnerStatus();
  if (ownerStatus) return ownerStatus;
  const response = fetchedEntry(raw);
  const decision = classifyDiaryResponse(response.status, response.code);
  if (decision.kind === "reauthenticate") {
    return lockAfterRejectedRequest("authentication-required");
  }
  if (decision.kind === "owner-mismatch") {
    return lockAfterRejectedRequest("owner-mismatch");
  }
  const snapshot = response.entry;
  if (decision.kind === "confirmed" && snapshot?.entry_id === command.entry_id) {
    try {
      await diary.blockCommand({
        ownerId,
        operationId: command.operation_id,
        error: { status: 409, code: "conflict" },
        remoteSnapshot: snapshot,
      });
    } catch {
      return (await currentOwnerStatus()) ?? "waiting";
    }
  }
  return null;
}

async function callDiary(
  adapter: DiarySyncAdapter,
  command: DiaryOutboxRecord,
  voiceBlob?: Blob,
): Promise<DiaryCommandResponse> {
  try {
    if (command.action === "create") return await adapter.createEntry(command.payload);
    if (command.action === "update") {
      return adapter.updateEntry
        ? await adapter.updateEntry(command.entry_id, command.payload)
        : { status: null };
    }
    if (command.action === "hide") {
      return adapter.hideEntry
        ? await adapter.hideEntry(command.entry_id, command.payload)
        : { status: null };
    }
    return adapter.uploadVoice && voiceBlob
      ? await adapter.uploadVoice(command.payload, voiceBlob)
      : { status: null };
  } catch {
    return { status: null };
  }
}

async function callIntake(
  adapter: DiarySyncAdapter,
  command: IntakeOutboxRecord,
): Promise<IntakeCommandResponse> {
  const createIntakeMark = adapter.createIntakeMark;
  if (!createIntakeMark) return { status: null };
  try {
    return await createIntakeMark(command.payload);
  } catch {
    return { status: null };
  }
}

/** Create a single-flight sync runner that serializes with assessment outbox drains. */
export function createDiarySyncEngine(
  diary: DiaryStore,
  assessments: AssessmentStore,
  adapter: DiarySyncAdapter = diarySyncAdapter,
): {
  sync: (expectedOwnerId: string, expectedSessionGeneration?: number) => Promise<DiarySyncOutcome>;
} {
  let activeSync:
    | {
        ownerId: string;
        sessionGeneration: number | null;
        promise: Promise<DiarySyncOutcome>;
      }
    | undefined;
  return {
    sync(expectedOwnerId, expectedSessionGeneration) {
      const sessionGeneration =
        expectedSessionGeneration ?? adapter.getSessionGeneration?.() ?? null;
      if (
        activeSync?.ownerId === expectedOwnerId &&
        activeSync.sessionGeneration === sessionGeneration
      ) {
        return activeSync.promise;
      }
      let run: Promise<DiarySyncOutcome>;
      run = (async () => {
        const pointer = await assessments.getOwnerPointer();
        const currentGeneration = adapter.getSessionGeneration?.();
        if (
          currentGeneration !== undefined &&
          sessionGeneration !== null &&
          currentGeneration !== sessionGeneration
        ) {
          return makeOutcome("session-changed", sessionGeneration);
        }
        if (!pointer) return makeOutcome("complete", sessionGeneration);
        if (pointer.id !== expectedOwnerId || pointer.role !== "patient") {
          return makeOutcome("owner-mismatch", sessionGeneration);
        }
        return withOwnerSyncLock(expectedOwnerId, () =>
          synchronize(diary, assessments, adapter, expectedOwnerId, sessionGeneration),
        );
      })().finally(() => {
        if (activeSync?.promise === run) activeSync = undefined;
      });
      activeSync = { ownerId: expectedOwnerId, sessionGeneration, promise: run };
      return run;
    },
  };
}
