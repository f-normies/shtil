import { requestResult, runTransaction } from "./database";
import { publishOwnerChange } from "./owner-events";
import type { OwnerPointer, PatientIdentity } from "./types";

type Metadata<T> = { key: string; value: T };

export const OWNER_POINTER_KEY = "owner_pointer";

export async function rememberPatientPointer(
  database: IDBDatabase,
  patient: PatientIdentity,
): Promise<void> {
  if (patient.role !== "patient" || !patient.id) {
    throw new Error("Only an authenticated patient can own local assessments");
  }
  const changed = await runTransaction(database, ["meta"], "readwrite", async (transaction) => {
    const previous = await ownerFrom(transaction);
    if (previous?.id === patient.id && previous.role === "patient" && !previous.locked) {
      return false;
    }
    await requestResult(
      transaction.objectStore("meta").put({
        key: OWNER_POINTER_KEY,
        value: { id: patient.id, role: "patient", locked: false } satisfies OwnerPointer,
      }),
    );
    return true;
  });
  if (changed) publishOwnerChange();
}

export async function rememberPatientPointerIfUnchanged(
  database: IDBDatabase,
  patient: PatientIdentity,
  expectedPointer: OwnerPointer | null,
): Promise<boolean> {
  if (patient.role !== "patient" || !patient.id) {
    throw new Error("Only an authenticated patient can own local assessments");
  }
  let changed = false;
  const accepted = await runTransaction(database, ["meta"], "readwrite", async (transaction) => {
    const currentPointer = await ownerFrom(transaction);
    if (
      currentPointer?.id === patient.id &&
      currentPointer.role === "patient" &&
      !currentPointer.locked
    ) {
      return true;
    }
    const expectedMatches =
      currentPointer?.id === expectedPointer?.id &&
      currentPointer?.role === expectedPointer?.role &&
      currentPointer?.locked === expectedPointer?.locked;
    if (!expectedMatches) return false;
    await requestResult(
      transaction.objectStore("meta").put({
        key: OWNER_POINTER_KEY,
        value: { id: patient.id, role: "patient", locked: false } satisfies OwnerPointer,
      }),
    );
    changed = true;
    return true;
  });
  if (changed) publishOwnerChange();
  return accepted;
}

export async function lockPatientPointer(database: IDBDatabase, ownerId: string): Promise<void> {
  const changed = await runTransaction(database, ["meta"], "readwrite", async (transaction) => {
    const pointer = await ownerFrom(transaction);
    if (pointer?.id === ownerId && !pointer.locked) {
      await requestResult(
        transaction.objectStore("meta").put({
          key: OWNER_POINTER_KEY,
          value: { ...pointer, locked: true },
        }),
      );
      return true;
    }
    return false;
  });
  if (changed) publishOwnerChange();
}

export function ownerFrom(transaction: IDBTransaction): Promise<OwnerPointer | null> {
  const request = transaction.objectStore("meta").get(OWNER_POINTER_KEY) as IDBRequest<
    Metadata<OwnerPointer> | undefined
  >;
  return requestResult(request).then((record) => record?.value ?? null);
}

export function requireOwner(
  pointer: OwnerPointer | null,
  ownerId: string,
  allowLocked = false,
): OwnerPointer {
  if (pointer?.role !== "patient" || pointer.id !== ownerId || (!allowLocked && pointer.locked)) {
    throw new Error("Local assessment data is unavailable for this patient");
  }
  return pointer;
}
