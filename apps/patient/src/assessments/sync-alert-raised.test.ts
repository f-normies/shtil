import { describe, expect, it, vi } from "vitest";
import {
  DEFINITION_V1,
  PATIENT_A,
  payload,
  SUBMISSION_A,
  SUBMISSION_B,
} from "../../e2e/assessment-fixtures";
import type { AssessmentStore } from "./store";
import { createAssessmentSyncEngine } from "./sync";
import type { OutboxRecord, OwnerPointer } from "./types";

function record(submissionId: string): OutboxRecord {
  return {
    owner_id: PATIENT_A,
    submission_id: submissionId,
    payload: payload(PATIENT_A, submissionId),
    definition_snapshot: DEFINITION_V1,
    state: "pending",
    last_error: null,
    created_at: "2026-10-25T00:00:00.000Z",
  };
}

function storeWith(outbox: OutboxRecord[]): AssessmentStore {
  const pointer: OwnerPointer = { id: PATIENT_A, role: "patient", locked: false };
  return {
    getOwnerPointer: vi.fn(async () => ({ ...pointer })),
    rememberOnlinePatientIfUnchanged: vi.fn(async () => true),
    lockOwnerPointer: vi.fn(),
    getOutboxForOwner: vi.fn(async () => [...outbox]),
    removeOutboxRecord: vi.fn(),
    blockOutboxRecord: vi.fn(),
  } as unknown as AssessmentStore;
}

describe("assessment sync alert flag", () => {
  it("reports confirmed submissions whose answer raised a safety alert", async () => {
    // Спека #57, раздел 5.1: «Мы сообщили вашему врачу» — только по alert_raised сервера.
    const store = storeWith([record(SUBMISSION_A), record(SUBMISSION_B)]);
    const adapter = {
      getCurrentUser: vi.fn(async () => ({ id: PATIENT_A, role: "patient" as const })),
      submitAssessment: vi.fn(async (body: { submission_id: string }) => ({
        status: 201,
        data: { alert_raised: body.submission_id === SUBMISSION_B } as never,
      })),
    };

    const outcome = await createAssessmentSyncEngine(store, adapter).sync();

    expect(outcome.confirmedSubmissionIds).toEqual([SUBMISSION_A, SUBMISSION_B]);
    expect(outcome.alertRaisedSubmissionIds).toEqual([SUBMISSION_B]);
  });
});
