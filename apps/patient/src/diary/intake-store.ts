import type { Schemas } from "@compass/api-client";
import { requestResult, runTransaction } from "../assessments/database";
import { ownerFrom, requireOwner } from "../assessments/store-owner";
import type { EnqueueIntakeMarkResult, IntakeCommand, IntakeOutboxRecord } from "./types";

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value).sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, nested]) => `${JSON.stringify(key)}:${canonicalJson(nested)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function validateCommand(payload: IntakeCommand): void {
  if (!payload.operation_id || !payload.prescription_id) {
    throw new Error("Intake operation and prescription IDs are required");
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(payload.scheduled_on)) {
    throw new Error("Intake date must use YYYY-MM-DD");
  }
  if (!Number.isInteger(payload.dose_ordinal) || payload.dose_ordinal < 0) {
    throw new Error("Intake dose ordinal must be a non-negative integer");
  }
  if (!Number.isFinite(Date.parse(payload.occurred_at))) {
    throw new Error("Intake event time must be valid");
  }
}

/** Add an explicit patient dose mark to the immutable local command queue. */
export function enqueueIntakeMark(
  database: IDBDatabase,
  input: { ownerId: string; payload: IntakeCommand },
): Promise<EnqueueIntakeMarkResult> {
  validateCommand(input.payload);
  const payload = structuredClone(input.payload);
  return runTransaction(database, ["meta", "intakeOutbox"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), input.ownerId);
    const outbox = transaction.objectStore("intakeOutbox");
    const key: IDBValidKey = [input.ownerId, payload.operation_id];
    const existing = await requestResult(
      outbox.get(key) as IDBRequest<IntakeOutboxRecord | undefined>,
    );
    if (existing && canonicalJson(existing.payload) === canonicalJson(payload)) {
      return { status: "replayed", command: existing };
    }
    if (existing) {
      const candidates = existing.payload_conflicts ?? [];
      const known = candidates.some(
        (candidate) => canonicalJson(candidate) === canonicalJson(payload),
      );
      const command: IntakeOutboxRecord = {
        ...existing,
        state: "conflict",
        last_error: { status: 409, code: "local_operation_conflict" },
        payload_conflicts: known ? candidates : [...candidates, payload],
      };
      await requestResult(outbox.put(command));
      return { status: "conflict", command };
    }
    const command: IntakeOutboxRecord = {
      owner_id: input.ownerId,
      operation_id: payload.operation_id,
      payload,
      state: "pending",
      last_error: null,
      payload_conflicts: [],
      created_at: new Date().toISOString(),
    };
    await requestResult(outbox.add(command));
    return { status: "queued", command };
  });
}

/** Return pending and blocked explicit marks in their original queue order. */
export function listIntakeOutbox(
  database: IDBDatabase,
  ownerId: string,
): Promise<IntakeOutboxRecord[]> {
  return runTransaction(database, ["meta", "intakeOutbox"], "readonly", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const range = IDBKeyRange.bound([ownerId, ""], [ownerId, "\uffff"]);
    return requestResult(
      transaction.objectStore("intakeOutbox").index("owner_created").getAll(range) as IDBRequest<
        IntakeOutboxRecord[]
      >,
    );
  });
}

/** Confirm one command and cache its server-assigned snapshot in the same transaction. */
export function confirmIntakeCommand(
  database: IDBDatabase,
  input: {
    ownerId: string;
    operationId: string;
    mark: Schemas["IntakeMarkOut"];
  },
): Promise<void> {
  return runTransaction(
    database,
    ["meta", "intakeOutbox", "intakeCache"],
    "readwrite",
    async (transaction) => {
      requireOwner(await ownerFrom(transaction), input.ownerId);
      const outbox = transaction.objectStore("intakeOutbox");
      const key: IDBValidKey = [input.ownerId, input.operationId];
      const command = await requestResult(
        outbox.get(key) as IDBRequest<IntakeOutboxRecord | undefined>,
      );
      if (!command) return;
      const payload = command.payload;
      if (
        input.mark.prescription_id !== payload.prescription_id ||
        input.mark.scheduled_on !== payload.scheduled_on ||
        input.mark.dose_ordinal !== payload.dose_ordinal ||
        input.mark.status !== payload.status
      ) {
        throw new Error("The intake receipt does not match its queued command");
      }
      const cache = transaction.objectStore("intakeCache");
      await Promise.all([
        requestResult(
          cache.put({
            owner_id: input.ownerId,
            prescription_id: input.mark.prescription_id,
            scheduled_on: input.mark.scheduled_on,
            dose_ordinal: input.mark.dose_ordinal,
            mark: structuredClone(input.mark),
            fetched_at: new Date().toISOString(),
          }),
        ),
        requestResult(outbox.delete(key)),
      ]);
    },
  );
}

/** Keep a rejected immutable payload for explicit later recovery. */
export function blockIntakeCommand(
  database: IDBDatabase,
  input: {
    ownerId: string;
    operationId: string;
    error: { status: number; code: string };
  },
): Promise<void> {
  return runTransaction(database, ["meta", "intakeOutbox"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), input.ownerId);
    const outbox = transaction.objectStore("intakeOutbox");
    const key: IDBValidKey = [input.ownerId, input.operationId];
    const command = await requestResult(
      outbox.get(key) as IDBRequest<IntakeOutboxRecord | undefined>,
    );
    if (command) {
      await requestResult(
        outbox.put({
          ...command,
          state: input.error.status === 409 ? "conflict" : "blocked",
          last_error: input.error,
        } satisfies IntakeOutboxRecord),
      );
    }
  });
}

/** Cache only a successful, authoritative day response from the API. */
export function cacheIntakeMarks(
  database: IDBDatabase,
  ownerId: string,
  scheduledOn: string,
  marks: readonly Schemas["IntakeMarkOut"][],
): Promise<void> {
  return runTransaction(database, ["meta", "intakeCache"], "readwrite", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const cache = transaction.objectStore("intakeCache");
    const range = IDBKeyRange.bound([ownerId, scheduledOn], [ownerId, scheduledOn]);
    const keys = await requestResult(cache.index("owner_date").getAllKeys(range));
    await Promise.all(keys.map((key) => requestResult(cache.delete(key))));
    const fetchedAt = new Date().toISOString();
    await Promise.all(
      marks
        .filter((mark) => mark.scheduled_on === scheduledOn)
        .map((mark) =>
          requestResult(
            cache.put({
              owner_id: ownerId,
              prescription_id: mark.prescription_id,
              scheduled_on: mark.scheduled_on,
              dose_ordinal: mark.dose_ordinal,
              mark: structuredClone(mark),
              fetched_at: fetchedAt,
            }),
          ),
        ),
    );
  });
}

/** Read only this owner's last successfully fetched explicit marks for a day. */
export function getCachedIntakeMarks(
  database: IDBDatabase,
  ownerId: string,
  scheduledOn: string,
): Promise<Schemas["IntakeMarkOut"][]> {
  return runTransaction(database, ["meta", "intakeCache"], "readonly", async (transaction) => {
    requireOwner(await ownerFrom(transaction), ownerId);
    const range = IDBKeyRange.bound([ownerId, scheduledOn], [ownerId, scheduledOn]);
    const records = await requestResult(
      transaction.objectStore("intakeCache").index("owner_date").getAll(range) as IDBRequest<
        Array<{ mark: Schemas["IntakeMarkOut"] }>
      >,
    );
    return records.map((record) => record.mark);
  });
}
