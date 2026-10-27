import type { AssessmentStore } from "./store";
import type { EnqueueDraftInput, EnqueueDraftResult } from "./types";

export type AssessmentPersistedReceipt = {
  submission_id: string;
  scale_id: string;
  scale_version: number;
  answers: Record<string, number>;
  queue_status: "queued";
};

/** Notify the containing flow only after the assessment queue transaction commits. */
export async function persistAssessmentDraft(
  store: Pick<AssessmentStore, "enqueueDraft">,
  input: EnqueueDraftInput,
  onPersisted?: (receipt: AssessmentPersistedReceipt) => void | Promise<void>,
): Promise<EnqueueDraftResult> {
  const result = await store.enqueueDraft(input);
  if (result.status === "queued") {
    await onPersisted?.({
      submission_id: result.record.submission_id,
      scale_id: result.record.payload.scale_id,
      scale_version: result.record.payload.scale_version,
      answers: { ...result.record.payload.answers },
      queue_status: "queued",
    });
  }
  return result;
}
