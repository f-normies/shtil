import { openAssessmentDatabase, requestResult, runTransaction } from "./database";
import { nextDraftRevision } from "./draft-revision";
import { enqueueAssessmentDraftInTransaction } from "./enqueue-draft-transaction";
import { publishOwnerChange } from "./owner-events";
import {
  clearOwnerDataAfterConfirmation,
  discardDraftIfUnchanged,
  getOwnerDataCounts,
  requeueBlockedOutboxRecord,
  restoreOutboxToDraft,
} from "./store-local-data";
import {
  lockPatientPointer,
  OWNER_POINTER_KEY,
  ownerFrom,
  rememberPatientPointer,
  rememberPatientPointerIfUnchanged,
  requireOwner,
} from "./store-owner";
import type {
  AssessmentDraft,
  DefinitionSnapshot,
  DiscardDraftResult,
  EnqueueDraftInput,
  EnqueueDraftResult,
  OutboxError,
  OutboxRecord,
  OwnerPointer,
  PatientIdentity,
  RestoreOutboxDraftResult,
  SaveDraftInput,
  SaveDraftResult,
} from "./types";

type StoredDefinition = { scale_id: string; version: number; definition: DefinitionSnapshot };

function validateDefinition(scaleId: string, definition: DefinitionSnapshot): void {
  if (definition.scale.id !== scaleId)
    throw new Error("Definition does not match the requested scale");
}

async function validateKnownDefinition(
  transaction: IDBTransaction,
  definition: DefinitionSnapshot,
): Promise<void> {
  const { id: scaleId, version } = definition.scale;
  const existing = await requestResult(
    transaction.objectStore("definitions").get([scaleId, version]) as IDBRequest<
      StoredDefinition | undefined
    >,
  );
  if (existing && !isSameDefinition(existing.definition, definition)) {
    throw new Error("Definition does not match the cached content for this version");
  }
}

function isSameDefinition(left: DefinitionSnapshot, right: DefinitionSnapshot): boolean {
  return JSON.stringify(canonicalJson(left)) === JSON.stringify(canonicalJson(right));
}

function canonicalJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value).sort(([left], [right]) => left.localeCompare(right));
    return Object.fromEntries(entries.map(([key, nested]) => [key, canonicalJson(nested)]));
  }
  return value;
}

/** Durable, account-scoped local storage for assessment definitions, drafts, and queued submissions. */
export class AssessmentStore {
  private constructor(private readonly database: IDBDatabase) {}

  /** Open the browser database, creating its stores on first use. */
  static async open(): Promise<AssessmentStore> {
    return new AssessmentStore(await openAssessmentDatabase());
  }

  /** Record a patient identity confirmed by a successful online authentication check. */
  async rememberOnlinePatient(patient: PatientIdentity): Promise<void> {
    return rememberPatientPointer(this.database, patient);
  }

  /** Publish a verified identity only if the owner snapshot is still current. */
  async rememberOnlinePatientIfUnchanged(
    patient: PatientIdentity,
    expectedPointer: OwnerPointer | null,
  ): Promise<boolean> {
    return rememberPatientPointerIfUnchanged(this.database, patient, expectedPointer);
  }

  /** Lock local patient access after a 401 while retaining every queued answer. */
  async lockOwnerPointer(ownerId: string): Promise<void> {
    return lockPatientPointer(this.database, ownerId);
  }

  /** Return the minimal last-confirmed identity pointer. */
  async getOwnerPointer(): Promise<OwnerPointer | null> {
    return runTransaction(this.database, ["meta"], "readonly", ownerFrom);
  }

  /** Remove only the identity pointer when the caller has completed a clean logout. */
  async clearOwnerPointer(ownerId: string): Promise<void> {
    await runTransaction(this.database, ["meta"], "readwrite", async (transaction) => {
      requireOwner(await ownerFrom(transaction), ownerId);
      await requestResult(transaction.objectStore("meta").delete(OWNER_POINTER_KEY));
    });
    // The committed pointer removal triggers revalidation in every other patient tab.
    publishOwnerChange();
  }

  /** Cache a catalog definition without allowing same-version content to be replaced. */
  async cacheDefinition(definition: DefinitionSnapshot): Promise<void> {
    const { id: scaleId, version } = definition.scale;
    const record: StoredDefinition = { scale_id: scaleId, version, definition };
    await runTransaction(this.database, ["definitions"], "readwrite", async (transaction) => {
      const definitions = transaction.objectStore("definitions");
      const existing = await requestResult(
        definitions.get([scaleId, version]) as IDBRequest<StoredDefinition | undefined>,
      );
      if (existing && !isSameDefinition(existing.definition, definition)) {
        throw new Error("A scale definition version cannot be replaced with different content");
      }
      if (!existing) await requestResult(definitions.add(record));
    });
  }

  /** Read the exact cached definition version for offline reopening. */
  async getCachedDefinition(scaleId: string, version: number): Promise<DefinitionSnapshot | null> {
    return runTransaction(this.database, ["definitions"], "readonly", async (transaction) => {
      const record = await requestResult(
        transaction.objectStore("definitions").get([scaleId, version]) as IDBRequest<
          StoredDefinition | undefined
        >,
      );
      return record?.definition ?? null;
    });
  }

  /** Return every verified version for the offline catalog and snapshots. */
  async getCachedDefinitions(): Promise<DefinitionSnapshot[]> {
    return runTransaction(this.database, ["definitions"], "readonly", async (transaction) => {
      const records = await requestResult(
        transaction.objectStore("definitions").getAll() as IDBRequest<StoredDefinition[]>,
      );
      return records
        .sort((left, right) =>
          left.scale_id === right.scale_id
            ? left.version - right.version
            : left.scale_id.localeCompare(right.scale_id),
        )
        .map((record) => record.definition);
    });
  }

  /** Save a draft only when the stored revision matches the revision read by the caller. */
  async saveDraft(input: SaveDraftInput): Promise<SaveDraftResult> {
    validateDefinition(input.scaleId, input.definitionSnapshot);
    return runTransaction(
      this.database,
      ["meta", "drafts", "definitions"],
      "readwrite",
      async (transaction) => {
        requireOwner(await ownerFrom(transaction), input.ownerId);
        await validateKnownDefinition(transaction, input.definitionSnapshot);
        const drafts = transaction.objectStore("drafts");
        const key: IDBValidKey = [input.ownerId, input.scaleId];
        const current = await requestResult(
          drafts.get(key) as IDBRequest<AssessmentDraft | undefined>,
        );
        if ((current?.revision ?? 0) !== input.expectedRevision) {
          return { status: "conflict", currentDraft: current ?? null };
        }
        const revision = await nextDraftRevision(
          transaction,
          input.ownerId,
          input.scaleId,
          current?.revision ?? 0,
        );
        const draft: AssessmentDraft = {
          owner_id: input.ownerId,
          scale_id: input.scaleId,
          revision,
          answers: { ...input.answers },
          definition_snapshot: input.definitionSnapshot,
          updated_at: new Date().toISOString(),
        };
        await requestResult(drafts.put(draft));
        return { status: "saved", draft };
      },
    );
  }

  /** Delete an old-version draft only if no other tab has changed its revision. */
  async discardDraft(input: {
    ownerId: string;
    scaleId: string;
    expectedRevision: number;
  }): Promise<DiscardDraftResult> {
    return discardDraftIfUnchanged(this.database, input);
  }

  /** Read one draft belonging to the currently unlocked patient. */
  async getDraft(input: { ownerId: string; scaleId: string }): Promise<AssessmentDraft | null> {
    return runTransaction(this.database, ["meta", "drafts"], "readonly", async (transaction) => {
      requireOwner(await ownerFrom(transaction), input.ownerId);
      const key: IDBValidKey = [input.ownerId, input.scaleId];
      const draft = await requestResult(
        transaction.objectStore("drafts").get(key) as IDBRequest<AssessmentDraft | undefined>,
      );
      return draft ?? null;
    });
  }

  /** Atomically queue the saved revision and delete its draft after the transaction commits. */
  async enqueueDraft(input: EnqueueDraftInput): Promise<EnqueueDraftResult> {
    const snapshot = { ...input, payload: structuredClone(input.payload) };
    return runTransaction(
      this.database,
      ["meta", "drafts", "outbox"],
      "readwrite",
      async (transaction) => {
        requireOwner(await ownerFrom(transaction), snapshot.ownerId);
        return enqueueAssessmentDraftInTransaction(transaction, snapshot);
      },
    );
  }

  /** Return this patient's queued and blocked submissions in their saved order. */
  async getOutboxForOwner(ownerId: string): Promise<OutboxRecord[]> {
    return runTransaction(this.database, ["meta", "outbox"], "readonly", async (transaction) => {
      requireOwner(await ownerFrom(transaction), ownerId);
      const range = IDBKeyRange.bound([ownerId, ""], [ownerId, "\uffff"]);
      return requestResult(
        transaction.objectStore("outbox").index("owner_created").getAll(range) as IDBRequest<
          OutboxRecord[]
        >,
      );
    });
  }

  /** Remove a submission after the API confirms it was saved. */
  async removeOutboxRecord(ownerId: string, submissionId: string): Promise<void> {
    await runTransaction(this.database, ["meta", "outbox"], "readwrite", async (transaction) => {
      requireOwner(await ownerFrom(transaction), ownerId);
      await requestResult(transaction.objectStore("outbox").delete([ownerId, submissionId]));
    });
  }

  /** Preserve the immutable payload and definition while marking a permanent response. */
  async blockOutboxRecord(
    ownerId: string,
    submissionId: string,
    error: OutboxError,
  ): Promise<void> {
    await runTransaction(this.database, ["meta", "outbox"], "readwrite", async (transaction) => {
      requireOwner(await ownerFrom(transaction), ownerId);
      const outbox = transaction.objectStore("outbox");
      const key: IDBValidKey = [ownerId, submissionId];
      const record = await requestResult(outbox.get(key) as IDBRequest<OutboxRecord | undefined>);
      if (!record) return;
      await requestResult(outbox.put({ ...record, state: "blocked", last_error: error }));
    });
  }

  /** Queue a temporarily blocked submission again after the patient asks to resend it. */
  async requeueBlockedOutboxRecord(ownerId: string, submissionId: string): Promise<boolean> {
    return requeueBlockedOutboxRecord(this.database, ownerId, submissionId);
  }

  /** Count this patient's unsent drafts and submissions before showing logout choices. */
  async getOwnerDataCounts(
    ownerId: string,
  ): Promise<{ drafts: number; outbox: number; audio: number }> {
    return getOwnerDataCounts(this.database, ownerId);
  }

  /** Return a blocked validation response to its saved form without replacing a draft. */
  async restoreOutboxToDraft(
    ownerId: string,
    submissionId: string,
  ): Promise<RestoreOutboxDraftResult> {
    return restoreOutboxToDraft(this.database, ownerId, submissionId);
  }

  /** Delete this patient's answers after confirmation; revoke may clear a locked owner. */
  async clearOwnerDataAfterConfirmation(ownerId: string, allowLocked = false): Promise<void> {
    await clearOwnerDataAfterConfirmation(this.database, ownerId, allowLocked);
    publishOwnerChange();
  }
}
