import type { Schemas } from "@compass/api-client";
import { fetchClient, fetchCurrentUser, getSessionGeneration } from "@compass/api-client";
import { denyOfflineAssessmentAccess } from "./offline-access";
import { withOwnerSyncLock } from "./owner-sync-lock";
import { classifySubmissionResponse } from "./queue-policy";
import type { AssessmentStore } from "./store";
import type { AssessmentPayload, OutboxRecord, PatientIdentity } from "./types";

export type AssessmentSyncResponse = {
  status: number | null;
  code?: string;
  data?: Schemas["AssessmentCreatedOut"];
};

export type AssessmentSyncAdapter = {
  getCurrentUser: () => Promise<PatientIdentity | null>;
  submitAssessment: (payload: AssessmentPayload) => Promise<AssessmentSyncResponse>;
  getSessionGeneration?: () => number;
};

export type AssessmentSyncOutcome = {
  status: "complete" | "waiting" | "authentication-required" | "owner-mismatch" | "session-changed";
  sessionGeneration: number | null;
  submitted: number;
  blocked: number;
  confirmedSubmissionIds: string[];
  /** Подтверждённые отправки, по которым сервер создал сигнал `kind: alert` (спека #57). */
  alertRaisedSubmissionIds: string[];
};

/** Adapter for the generated API client; callers can inject a test or app-specific adapter. */
export const assessmentSyncAdapter: AssessmentSyncAdapter = {
  getCurrentUser: () => fetchCurrentUser(fetchClient),
  getSessionGeneration: () => getSessionGeneration(fetchClient),
  async submitAssessment(payload) {
    const { data, error, response } = await fetchClient.POST("/api/v1/assessments", {
      body: payload,
    });
    const code = problemCode(error);
    return {
      status: response.status,
      ...(code ? { code } : {}),
      ...(data ? { data } : {}),
    };
  },
};

function problemCode(error: unknown): string | undefined {
  if (error && typeof error === "object" && "code" in error && typeof error.code === "string") {
    return error.code;
  }
  return undefined;
}

function outcome(
  status: AssessmentSyncOutcome["status"],
  sessionGeneration: number | null,
  confirmedSubmissionIds: string[] = [],
  blocked = 0,
  alertRaisedSubmissionIds: string[] = [],
): AssessmentSyncOutcome {
  return {
    status,
    sessionGeneration,
    submitted: confirmedSubmissionIds.length,
    blocked,
    confirmedSubmissionIds,
    alertRaisedSubmissionIds,
  };
}

async function lockOwnerOrDenyOffline(store: AssessmentStore, ownerId: string): Promise<void> {
  try {
    await store.lockOwnerPointer(ownerId);
  } catch {
    denyOfflineAssessmentAccess();
  }
}

async function synchronize(
  store: AssessmentStore,
  adapter: AssessmentSyncAdapter,
  expectedOwnerId?: string,
  sessionGeneration: number | null = null,
): Promise<AssessmentSyncOutcome> {
  const alertRaisedSubmissionIds: string[] = [];
  const result = (
    status: AssessmentSyncOutcome["status"],
    confirmedSubmissionIds: string[] = [],
    blocked = 0,
  ) =>
    outcome(status, sessionGeneration, confirmedSubmissionIds, blocked, alertRaisedSubmissionIds);
  const isCurrentSession = () => {
    const currentGeneration = adapter.getSessionGeneration?.();
    return (
      currentGeneration === undefined ||
      sessionGeneration === null ||
      currentGeneration === sessionGeneration
    );
  };

  const previousOwner = await store.getOwnerPointer();
  if (!isCurrentSession()) return result("session-changed");
  if (!previousOwner) return result("complete");
  if (expectedOwnerId && previousOwner.id !== expectedOwnerId) return result("owner-mismatch");

  let patient: PatientIdentity | null;
  try {
    patient = await adapter.getCurrentUser();
  } catch {
    return result(isCurrentSession() ? "waiting" : "session-changed");
  }
  if (!isCurrentSession()) return result("session-changed");
  if (!patient) {
    await lockOwnerOrDenyOffline(store, previousOwner.id);
    return result("authentication-required");
  }
  if (patient.role !== "patient") {
    await lockOwnerOrDenyOffline(store, previousOwner.id);
    return result("owner-mismatch");
  }
  if (expectedOwnerId && patient.id !== expectedOwnerId) return result("owner-mismatch");
  const ownerUnchanged = await store.rememberOnlinePatientIfUnchanged(patient, previousOwner);
  if (!isCurrentSession()) return result("session-changed");
  if (!ownerUnchanged) return result("owner-mismatch");
  if (patient.id !== previousOwner.id) {
    return result("owner-mismatch");
  }

  const records = await store.getOutboxForOwner(patient.id);
  if (!isCurrentSession()) return result("session-changed");
  const confirmedSubmissionIds: string[] = [];
  let blocked = 0;
  for (const record of records) {
    if (record.state !== "pending") continue;
    const currentOwner = await store.getOwnerPointer();
    if (currentOwner?.id !== patient.id || currentOwner.role !== "patient" || currentOwner.locked) {
      return result("owner-mismatch", confirmedSubmissionIds, blocked);
    }
    const response = await submit(adapter, record);
    if (!isCurrentSession()) return result("session-changed", confirmedSubmissionIds, blocked);
    const ownerAfterResponse = await store.getOwnerPointer();
    if (!isCurrentSession()) return result("session-changed", confirmedSubmissionIds, blocked);
    if (
      ownerAfterResponse?.id !== patient.id ||
      ownerAfterResponse.role !== "patient" ||
      ownerAfterResponse.locked
    ) {
      return result("owner-mismatch", confirmedSubmissionIds, blocked);
    }
    const decision = classifySubmissionResponse(response.status, response.code);
    if (decision.kind === "confirmed") {
      await store.removeOutboxRecord(patient.id, record.submission_id);
      confirmedSubmissionIds.push(record.submission_id);
      if (response.data?.alert_raised) alertRaisedSubmissionIds.push(record.submission_id);
    } else if (decision.kind === "retry") {
      return result("waiting", confirmedSubmissionIds, blocked);
    } else if (decision.kind === "reauthenticate") {
      await lockOwnerOrDenyOffline(store, patient.id);
      return result("authentication-required", confirmedSubmissionIds, blocked);
    } else if (decision.kind === "owner-mismatch") {
      await lockOwnerOrDenyOffline(store, patient.id);
      return result("owner-mismatch", confirmedSubmissionIds, blocked);
    } else {
      await store.blockOutboxRecord(patient.id, record.submission_id, decision.error);
      blocked += 1;
    }
  }
  return result("complete", confirmedSubmissionIds, blocked);
}

async function submit(
  adapter: AssessmentSyncAdapter,
  record: OutboxRecord,
): Promise<AssessmentSyncResponse> {
  try {
    return await adapter.submitAssessment(record.payload);
  } catch {
    return { status: null };
  }
}

/** Create a single-flight queue runner that verifies the current online patient before sending. */
export function createAssessmentSyncEngine(
  store: AssessmentStore,
  adapter: AssessmentSyncAdapter = assessmentSyncAdapter,
): {
  sync: (
    expectedOwnerId?: string,
    expectedSessionGeneration?: number,
  ) => Promise<AssessmentSyncOutcome>;
} {
  let activeSync:
    | {
        expectedOwnerId?: string;
        sessionGeneration: number | null;
        promise: Promise<AssessmentSyncOutcome>;
      }
    | undefined;
  return {
    sync(expectedOwnerId, expectedSessionGeneration) {
      const sessionGeneration =
        expectedSessionGeneration ?? adapter.getSessionGeneration?.() ?? null;
      if (
        activeSync?.sessionGeneration === sessionGeneration &&
        activeSync.expectedOwnerId === expectedOwnerId
      ) {
        return activeSync.promise;
      }
      let run: Promise<AssessmentSyncOutcome>;
      run = (async () => {
        const owner = await store.getOwnerPointer();
        const currentGeneration = adapter.getSessionGeneration?.();
        if (
          currentGeneration !== undefined &&
          sessionGeneration !== null &&
          currentGeneration !== sessionGeneration
        ) {
          return outcome("session-changed", sessionGeneration);
        }
        if (!owner) return outcome("complete", sessionGeneration);
        if (expectedOwnerId && owner.id !== expectedOwnerId) {
          return outcome("owner-mismatch", sessionGeneration);
        }
        return withOwnerSyncLock(owner.id, () =>
          synchronize(store, adapter, owner.id, sessionGeneration),
        );
      })().finally(() => {
        if (activeSync?.promise === run) activeSync = undefined;
      });
      activeSync = { expectedOwnerId, sessionGeneration, promise: run };
      return run;
    },
  };
}
