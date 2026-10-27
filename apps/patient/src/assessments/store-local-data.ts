import { requestResult, runTransaction } from "./database";
import { nextDraftRevision, retainDraftRevision } from "./draft-revision";
import { canRetryBlockedSubmission } from "./queue-policy";
import { OWNER_POINTER_KEY, ownerFrom, requireOwner } from "./store-owner";
import type {
  AssessmentDraft,
  DiscardDraftResult,
  OutboxRecord,
  RestoreOutboxDraftResult,
} from "./types";

/** Delete a version-mismatched draft only when its confirmed revision is still current. */
export function discardDraftIfUnchanged(
  database: IDBDatabase,
  input: { ownerId: string; scaleId: string; expectedRevision: number },
): Promise<DiscardDraftResult> {
  return runTransaction(database, ["meta", "drafts"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), input.ownerId);
    const drafts = transaction.objectStore("drafts");
    const key: IDBValidKey = [input.ownerId, input.scaleId];
    const current = await requestResult(drafts.get(key) as IDBRequest<AssessmentDraft | undefined>);
    if (!current || current.revision !== input.expectedRevision) {
      return { status: "conflict", currentDraft: current ?? null };
    }
    await retainDraftRevision(transaction, input.ownerId, input.scaleId, current.revision);
    await requestResult(drafts.delete(key));
    return { status: "discarded" };
  });
}

/** Count current-owner drafts and unsent submissions for a logout warning. */
export function getOwnerDataCounts(
  database: IDBDatabase,
  ownerId: string,
): Promise<{ drafts: number; outbox: number; audio: number }> {
  return runTransaction(
    database,
    ["meta", "drafts", "outbox", "diaryDrafts", "diaryOutbox", "intakeOutbox", "diaryAudio"],
    "readonly",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), ownerId);
      const ownerKey = IDBKeyRange.only(ownerId);
      const [assessmentDrafts, diaryDrafts, assessmentOutbox, diaryOutbox, intakeOutbox, audio] =
        await Promise.all([
          requestResult(transaction.objectStore("drafts").index("owner_id").count(ownerKey)),
          requestResult(transaction.objectStore("diaryDrafts").index("owner_id").count(ownerKey)),
          requestResult(transaction.objectStore("outbox").index("owner_id").count(ownerKey)),
          requestResult(transaction.objectStore("diaryOutbox").index("owner_id").count(ownerKey)),
          requestResult(transaction.objectStore("intakeOutbox").index("owner_id").count(ownerKey)),
          requestResult(transaction.objectStore("diaryAudio").index("owner_id").count(ownerKey)),
        ]);
      return {
        drafts: assessmentDrafts + diaryDrafts,
        outbox: assessmentOutbox + diaryOutbox + intakeOutbox,
        audio,
      };
    },
  );
}

/** Move only a blocked 422 payload back to a draft, atomically preserving its source form. */
export function restoreOutboxToDraft(
  database: IDBDatabase,
  ownerId: string,
  submissionId: string,
): Promise<RestoreOutboxDraftResult> {
  return runTransaction(
    database,
    ["meta", "drafts", "outbox"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), ownerId);
      const outbox = transaction.objectStore("outbox");
      const key: IDBValidKey = [ownerId, submissionId];
      const record = await requestResult(outbox.get(key) as IDBRequest<OutboxRecord | undefined>);
      if (
        !record ||
        record.owner_id !== ownerId ||
        record.payload.patient_id !== ownerId ||
        record.state !== "blocked" ||
        record.last_error?.status !== 422 ||
        record.payload.scale_id !== record.definition_snapshot.scale.id ||
        record.payload.scale_version !== record.definition_snapshot.scale.version
      ) {
        return { status: "unavailable" };
      }
      const drafts = transaction.objectStore("drafts");
      const draftKey: IDBValidKey = [ownerId, record.payload.scale_id];
      const current = await requestResult(
        drafts.get(draftKey) as IDBRequest<AssessmentDraft | undefined>,
      );
      if (current) return { status: "draft-exists", currentDraft: current };
      const revision = await nextDraftRevision(transaction, ownerId, record.payload.scale_id, 0);
      const draft: AssessmentDraft = {
        owner_id: ownerId,
        scale_id: record.payload.scale_id,
        revision,
        answers: { ...record.payload.answers },
        definition_snapshot: record.definition_snapshot,
        updated_at: new Date().toISOString(),
      };
      await Promise.all([requestResult(drafts.add(draft)), requestResult(outbox.delete(key))]);
      return { status: "restored", draft };
    },
  );
}

/** Return a temporarily blocked submission to the queue with its original payload and key. */
export function requeueBlockedOutboxRecord(
  database: IDBDatabase,
  ownerId: string,
  submissionId: string,
): Promise<boolean> {
  return runTransaction(database, ["meta", "outbox"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const outbox = transaction.objectStore("outbox");
    const key: IDBValidKey = [ownerId, submissionId];
    const record = await requestResult(outbox.get(key) as IDBRequest<OutboxRecord | undefined>);
    if (record?.state !== "blocked" || !canRetryBlockedSubmission(record.last_error)) {
      return false;
    }
    await requestResult(outbox.put({ ...record, state: "pending", last_error: null }));
    return true;
  });
}

/** Clear this owner's answers after confirmation; revoke may allow an expired session. */
export function clearOwnerDataAfterConfirmation(
  database: IDBDatabase,
  ownerId: string,
  allowLocked = false,
): Promise<void> {
  return runTransaction(
    database,
    [
      "meta",
      "drafts",
      "outbox",
      "diaryDrafts",
      "diaryEntries",
      "diaryOutbox",
      "diaryAudio",
      "intakeCache",
      "intakeOutbox",
      "diaryContext",
    ],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), ownerId, allowLocked);
      const drafts = transaction.objectStore("drafts");
      const outbox = transaction.objectStore("outbox");
      const ownerKey = IDBKeyRange.only(ownerId);
      const [
        draftRecords,
        outboxKeys,
        diaryKeys,
        diaryEntryKeys,
        diaryOutboxKeys,
        audioKeys,
        intakeKeys,
        intakeOutboxKeys,
      ] = await Promise.all([
        requestResult(
          drafts.index("owner_id").getAll(IDBKeyRange.only(ownerId)) as IDBRequest<
            AssessmentDraft[]
          >,
        ),
        requestResult(outbox.index("owner_id").getAllKeys(IDBKeyRange.only(ownerId))),
        requestResult(
          transaction.objectStore("diaryDrafts").index("owner_id").getAllKeys(ownerKey),
        ),
        requestResult(
          transaction.objectStore("diaryEntries").index("owner_id").getAllKeys(ownerKey),
        ),
        requestResult(
          transaction.objectStore("diaryOutbox").index("owner_id").getAllKeys(ownerKey),
        ),
        requestResult(transaction.objectStore("diaryAudio").index("owner_id").getAllKeys(ownerKey)),
        requestResult(
          transaction.objectStore("intakeCache").index("owner_id").getAllKeys(ownerKey),
        ),
        requestResult(
          transaction.objectStore("intakeOutbox").index("owner_id").getAllKeys(ownerKey),
        ),
      ]);
      for (const draft of draftRecords) {
        await retainDraftRevision(transaction, ownerId, draft.scale_id, draft.revision);
      }
      await Promise.all([
        ...draftRecords.map((draft) =>
          requestResult(drafts.delete([draft.owner_id, draft.scale_id])),
        ),
        ...outboxKeys.map((key) => requestResult(outbox.delete(key))),
        ...diaryKeys.map((key) =>
          requestResult(transaction.objectStore("diaryDrafts").delete(key)),
        ),
        ...diaryEntryKeys.map((key) =>
          requestResult(transaction.objectStore("diaryEntries").delete(key)),
        ),
        ...diaryOutboxKeys.map((key) =>
          requestResult(transaction.objectStore("diaryOutbox").delete(key)),
        ),
        ...audioKeys.map((key) => requestResult(transaction.objectStore("diaryAudio").delete(key))),
        ...intakeKeys.map((key) =>
          requestResult(transaction.objectStore("intakeCache").delete(key)),
        ),
        ...intakeOutboxKeys.map((key) =>
          requestResult(transaction.objectStore("intakeOutbox").delete(key)),
        ),
        requestResult(transaction.objectStore("meta").delete(OWNER_POINTER_KEY)),
        requestResult(transaction.objectStore("diaryContext").delete(ownerId)),
      ]);
    },
  );
}
