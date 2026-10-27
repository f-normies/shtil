import { requestResult } from "./database";

type RevisionMetadata = { key: string; value: number };

function revisionKey(ownerId: string, scaleId: string): string {
  return `draft_revision:${JSON.stringify([ownerId, scaleId])}`;
}

/** Keep a removed draft's last revision so a later draft cannot reuse its identity. */
export async function retainDraftRevision(
  transaction: IDBTransaction,
  ownerId: string,
  scaleId: string,
  revision: number,
): Promise<number> {
  const key = revisionKey(ownerId, scaleId);
  const meta = transaction.objectStore("meta");
  const current = await requestResult(meta.get(key) as IDBRequest<RevisionMetadata | undefined>);
  const value = Math.max(
    current && Number.isSafeInteger(current.value) ? current.value : 0,
    Number.isSafeInteger(revision) ? revision : 0,
  );
  if (value !== current?.value) await requestResult(meta.put({ key, value }));
  return value;
}

/** Allocate and persist the next revision in the same transaction as its draft. */
export async function nextDraftRevision(
  transaction: IDBTransaction,
  ownerId: string,
  scaleId: string,
  currentRevision: number,
): Promise<number> {
  const revision = (await retainDraftRevision(transaction, ownerId, scaleId, currentRevision)) + 1;
  const key = revisionKey(ownerId, scaleId);
  await requestResult(transaction.objectStore("meta").put({ key, value: revision }));
  return revision;
}
