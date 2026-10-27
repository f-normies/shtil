import { expect, it, vi } from "vitest";
import { persistAssessmentDraft } from "./persisted-callback";
import type { DefinitionSnapshot, EnqueueDraftInput, EnqueueDraftResult } from "./types";

const PATIENT_A = "00000000-0000-4000-8000-0000000000a1";
const SCALE_ID = "synthetic-scale";
const payload = () => ({
  patient_id: PATIENT_A,
  submission_id: "00000000-0000-4000-8000-000000000011",
  scale_id: SCALE_ID,
  scale_version: 1,
  completed_at: "2026-10-23T10:00:00.000Z",
  answers: { q1: 1 },
});
const definitionSnapshot = {
  scale: { id: SCALE_ID, version: 1 },
} as DefinitionSnapshot;

const input: EnqueueDraftInput = {
  ownerId: PATIENT_A,
  scaleId: SCALE_ID,
  expectedRevision: 2,
  payload: payload(),
};

it("notifies after the durable assessment queue write resolves", async () => {
  let commit: (result: EnqueueDraftResult) => void = () => {
    throw new Error("queue write was not started");
  };
  const store = {
    enqueueDraft: vi.fn(
      () =>
        new Promise<EnqueueDraftResult>((resolve) => {
          commit = resolve;
        }),
    ),
  };
  const onPersisted = vi.fn();
  const pending = persistAssessmentDraft(store, input, onPersisted);

  expect(onPersisted).not.toHaveBeenCalled();
  commit({
    status: "queued",
    record: {
      owner_id: PATIENT_A,
      submission_id: input.payload.submission_id,
      payload: input.payload,
      definition_snapshot: definitionSnapshot,
      state: "pending",
      last_error: null,
      created_at: "2026-10-23T10:00:00.000Z",
    },
  });
  await expect(pending).resolves.toMatchObject({ status: "queued" });
  expect(onPersisted).toHaveBeenCalledTimes(1);
  expect(onPersisted).toHaveBeenCalledWith({
    submission_id: input.payload.submission_id,
    scale_id: SCALE_ID,
    scale_version: input.payload.scale_version,
    answers: input.payload.answers,
    queue_status: "queued",
  });
});

it("does not notify when enqueue fails or returns a stale-revision conflict", async () => {
  const onPersisted = vi.fn();
  await expect(
    persistAssessmentDraft(
      { enqueueDraft: vi.fn().mockRejectedValue(new Error("IndexedDB abort")) },
      input,
      onPersisted,
    ),
  ).rejects.toThrow("IndexedDB abort");
  await expect(
    persistAssessmentDraft(
      { enqueueDraft: vi.fn().mockResolvedValue({ status: "conflict", currentDraft: null }) },
      input,
      onPersisted,
    ),
  ).resolves.toEqual({ status: "conflict", currentDraft: null });
  expect(onPersisted).not.toHaveBeenCalled();
});

it("waits for the containing flow to persist its receipt before advancing", async () => {
  let finishReceipt: () => void = () => {
    throw new Error("receipt persistence did not start");
  };
  let settled = false;
  const onPersisted = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finishReceipt = resolve;
      }),
  );
  const pending = persistAssessmentDraft(
    {
      enqueueDraft: vi.fn().mockResolvedValue({
        status: "queued",
        record: {
          owner_id: PATIENT_A,
          submission_id: input.payload.submission_id,
          payload: input.payload,
          definition_snapshot: definitionSnapshot,
          state: "pending",
          last_error: null,
          created_at: "2026-10-23T10:00:00.000Z",
        },
      }),
    },
    input,
    onPersisted,
  ).then((result) => {
    settled = true;
    return result;
  });

  await new Promise<void>((resolve) => setTimeout(resolve, 0));
  expect(onPersisted).toHaveBeenCalledTimes(1);
  expect(settled).toBe(false);
  finishReceipt();
  await expect(pending).resolves.toMatchObject({ status: "queued" });
  expect(settled).toBe(true);
});
