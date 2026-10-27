import { requestResult } from "./database";
import { retainDraftRevision } from "./draft-revision";
import type { AssessmentDraft, EnqueueDraftInput, EnqueueDraftResult, OutboxRecord } from "./types";

const LAST_OUTBOX_TIME_KEY = "last_outbox_created_at";
type Metadata<T> = { key: string; value: T };

function answersMatch(left: Record<string, number>, right: Record<string, number>): boolean {
  const keys = Object.keys(left);
  return (
    keys.length === Object.keys(right).length &&
    keys.every((key) => Object.hasOwn(right, key) && left[key] === right[key])
  );
}

function validatePayload(
  ownerId: string,
  scaleId: string,
  draft: AssessmentDraft,
  payload: EnqueueDraftInput["payload"],
): void {
  if (
    payload.patient_id !== ownerId ||
    payload.scale_id !== scaleId ||
    payload.scale_version !== draft.definition_snapshot.scale.version
  ) {
    throw new Error("Assessment payload does not match its owner and definition snapshot");
  }
  if (!answersMatch(payload.answers, draft.answers)) {
    throw new Error("Assessment payload answers do not match the saved draft");
  }
}

function monotonicTimestamp(previous: string | undefined): string {
  const previousMs = previous ? Date.parse(previous) : Number.NEGATIVE_INFINITY;
  return new Date(Math.max(Date.now(), previousMs + 1)).toISOString();
}

/** Queue an assessment draft using the caller's existing atomic IndexedDB transaction. */
export async function enqueueAssessmentDraftInTransaction(
  transaction: IDBTransaction,
  input: EnqueueDraftInput,
): Promise<EnqueueDraftResult> {
  const drafts = transaction.objectStore("drafts");
  const key: IDBValidKey = [input.ownerId, input.scaleId];
  const current = await requestResult(drafts.get(key) as IDBRequest<AssessmentDraft | undefined>);
  if (!current || current.revision !== input.expectedRevision) {
    return { status: "conflict", currentDraft: current ?? null };
  }
  validatePayload(input.ownerId, input.scaleId, current, input.payload);

  const outbox = transaction.objectStore("outbox");
  const receiptKey: IDBValidKey = [input.ownerId, input.payload.submission_id];
  const existing = await requestResult(
    outbox.get(receiptKey) as IDBRequest<OutboxRecord | undefined>,
  );
  if (existing) {
    if (JSON.stringify(existing.payload) !== JSON.stringify(input.payload)) {
      throw new Error("Assessment submission ID cannot be reused with a different payload");
    }
    await requestResult(drafts.delete(key));
    return { status: "queued", record: existing };
  }

  await retainDraftRevision(transaction, input.ownerId, input.scaleId, current.revision);
  const meta = transaction.objectStore("meta");
  const lastTime = await requestResult(
    meta.get(LAST_OUTBOX_TIME_KEY) as IDBRequest<Metadata<string> | undefined>,
  );
  const createdAt = monotonicTimestamp(lastTime?.value);
  const record: OutboxRecord = {
    owner_id: input.ownerId,
    submission_id: input.payload.submission_id,
    payload: structuredClone(input.payload),
    definition_snapshot: current.definition_snapshot,
    state: "pending",
    last_error: null,
    created_at: createdAt,
  };
  await Promise.all([
    requestResult(outbox.add(record)),
    requestResult(drafts.delete(key)),
    requestResult(meta.put({ key: LAST_OUTBOX_TIME_KEY, value: createdAt })),
  ]);
  return { status: "queued", record };
}
