import { describe, expect, it, vi } from "vitest";
import {
  DEFINITION_V1,
  PATIENT_A,
  PATIENT_B,
  payload,
  SUBMISSION_B,
} from "../../e2e/assessment-fixtures";
import type { AssessmentStore } from "./store";
import { createAssessmentSyncEngine } from "./sync";
import type { OutboxRecord, OwnerPointer, PatientIdentity } from "./types";

function sameOwnerPointer(left: OwnerPointer | null, right: OwnerPointer | null): boolean {
  return left?.id === right?.id && left?.role === right?.role && left?.locked === right?.locked;
}

describe("assessment sync owner publication", () => {
  it("does not publish or send after another tab changes the owner during session verification", async () => {
    let pointer: OwnerPointer | null = { id: PATIENT_B, role: "patient", locked: false };
    let outbox: OutboxRecord[] = [
      {
        owner_id: PATIENT_B,
        submission_id: SUBMISSION_B,
        payload: payload(PATIENT_B, SUBMISSION_B),
        definition_snapshot: DEFINITION_V1,
        state: "pending",
        last_error: null,
        created_at: "2026-10-18T00:00:00.000Z",
      },
    ];
    const rememberOnlinePatientIfUnchanged = vi.fn(
      async (patient: PatientIdentity, expected: OwnerPointer | null) => {
        if (!sameOwnerPointer(pointer, expected)) return false;
        pointer = { id: patient.id, role: "patient", locked: false };
        return true;
      },
    );
    const store = {
      getOwnerPointer: vi.fn(async () => (pointer ? { ...pointer } : null)),
      rememberOnlinePatient: vi.fn(async (patient: PatientIdentity) => {
        pointer = { id: patient.id, role: "patient", locked: false };
      }),
      rememberOnlinePatientIfUnchanged,
      lockOwnerPointer: vi.fn(async (ownerId: string) => {
        if (pointer?.id === ownerId) pointer = { ...pointer, locked: true };
      }),
      getOutboxForOwner: vi.fn(async (ownerId: string) => {
        if (pointer?.id !== ownerId || pointer.locked) throw new Error("Owner mismatch");
        return [...outbox];
      }),
      removeOutboxRecord: vi.fn(async (ownerId: string, submissionId: string) => {
        if (pointer?.id === ownerId) {
          outbox = outbox.filter((record) => record.submission_id !== submissionId);
        }
      }),
      blockOutboxRecord: vi.fn(),
    } as unknown as AssessmentStore & {
      rememberOnlinePatientIfUnchanged: typeof rememberOnlinePatientIfUnchanged;
    };

    let releaseSession: (patient: PatientIdentity | null) => void = () => {};
    let signalSessionRead: () => void = () => {};
    const sessionReadStarted = new Promise<void>((resolve) => {
      signalSessionRead = resolve;
    });
    const heldSession = new Promise<PatientIdentity | null>((resolve) => {
      releaseSession = resolve;
    });
    const adapter = {
      getCurrentUser: vi.fn(() => {
        signalSessionRead();
        return heldSession;
      }),
      submitAssessment: vi.fn(async () => ({ status: 201 })),
    };
    const engine = createAssessmentSyncEngine(store, adapter);

    const syncPromise = engine.sync();
    await sessionReadStarted;
    pointer = { id: PATIENT_A, role: "patient", locked: false };
    releaseSession({ id: PATIENT_B, role: "patient" });

    const result = await syncPromise;
    expect.soft(result.status).toBe("owner-mismatch");
    expect.soft(pointer).toEqual({ id: PATIENT_A, role: "patient", locked: false });
    expect.soft(adapter.submitAssessment).not.toHaveBeenCalled();
    expect
      .soft(store.rememberOnlinePatientIfUnchanged)
      .toHaveBeenCalledWith(
        { id: PATIENT_B, role: "patient" },
        { id: PATIENT_B, role: "patient", locked: false },
      );
  });

  it("does not lock an owner when the auth check belongs to an older session generation", async () => {
    let pointer: OwnerPointer | null = { id: PATIENT_A, role: "patient", locked: false };
    let sessionGeneration = 7;
    let releaseSession: (patient: PatientIdentity | null) => void = () => {};
    let signalSessionRead: () => void = () => {};
    const sessionReadStarted = new Promise<void>((resolve) => {
      signalSessionRead = resolve;
    });
    const heldSession = new Promise<PatientIdentity | null>((resolve) => {
      releaseSession = resolve;
    });
    const store = {
      getOwnerPointer: vi.fn(async () => (pointer ? { ...pointer } : null)),
      rememberOnlinePatientIfUnchanged: vi.fn(async () => true),
      lockOwnerPointer: vi.fn(async (ownerId: string) => {
        if (pointer?.id === ownerId) pointer = { ...pointer, locked: true };
      }),
      getOutboxForOwner: vi.fn(async () => []),
      removeOutboxRecord: vi.fn(),
      blockOutboxRecord: vi.fn(),
    } as unknown as AssessmentStore;
    const adapter = {
      getCurrentUser: vi.fn(() => {
        signalSessionRead();
        return heldSession;
      }),
      getSessionGeneration: () => sessionGeneration,
      submitAssessment: vi.fn(async () => ({ status: 201 })),
    };
    const engine = createAssessmentSyncEngine(store, adapter);

    const syncPromise = engine.sync(PATIENT_A, sessionGeneration);
    await sessionReadStarted;
    sessionGeneration += 1;
    releaseSession(null);

    const result = await syncPromise;
    expect(result.status).toBe("session-changed");
    expect(result.sessionGeneration).toBe(7);
    expect(pointer).toEqual({ id: PATIENT_A, role: "patient", locked: false });
    expect(store.lockOwnerPointer).not.toHaveBeenCalled();
  });

  it("rechecks the owner after reading the outbox and before sending a snapshot", async () => {
    let pointer: OwnerPointer | null = { id: PATIENT_A, role: "patient", locked: false };
    const record: OutboxRecord = {
      owner_id: PATIENT_A,
      submission_id: SUBMISSION_B,
      payload: payload(PATIENT_A, SUBMISSION_B),
      definition_snapshot: DEFINITION_V1,
      state: "pending",
      last_error: null,
      created_at: "2026-10-18T00:00:00.000Z",
    };
    let signalOutboxRead: () => void = () => {};
    const outboxReadStarted = new Promise<void>((resolve) => {
      signalOutboxRead = resolve;
    });
    let releaseOutboxRead: (records: OutboxRecord[]) => void = () => {};
    const heldOutbox = new Promise<OutboxRecord[]>((resolve) => {
      releaseOutboxRead = resolve;
    });
    const store = {
      getOwnerPointer: vi.fn(async () => (pointer ? { ...pointer } : null)),
      rememberOnlinePatientIfUnchanged: vi.fn(
        async (patient: PatientIdentity, expected: OwnerPointer | null) => {
          if (!sameOwnerPointer(pointer, expected)) return false;
          pointer = { id: patient.id, role: "patient", locked: false };
          return true;
        },
      ),
      lockOwnerPointer: vi.fn(),
      getOutboxForOwner: vi.fn(() => {
        signalOutboxRead();
        return heldOutbox;
      }),
      removeOutboxRecord: vi.fn(),
      blockOutboxRecord: vi.fn(),
    } as unknown as AssessmentStore;
    const adapter = {
      getCurrentUser: vi.fn(async () => ({ id: PATIENT_A, role: "patient" as const })),
      submitAssessment: vi.fn(async () => ({ status: 201 })),
    };
    const engine = createAssessmentSyncEngine(store, adapter);

    const syncPromise = engine.sync();
    await outboxReadStarted;
    pointer = { id: PATIENT_B, role: "patient", locked: false };
    releaseOutboxRead([record]);

    const result = await syncPromise;
    expect.soft(result.status).toBe("owner-mismatch");
    expect.soft(adapter.submitAssessment).not.toHaveBeenCalled();
    expect.soft(store.getOutboxForOwner).toHaveBeenCalledTimes(1);
    expect.soft(store.removeOutboxRecord).not.toHaveBeenCalled();
    expect.soft(pointer).toEqual({ id: PATIENT_B, role: "patient", locked: false });
  });
});
