import type { MessagingRole } from "@compass/api-client";
import {
  createMessagingLocalQueue,
  fetchClient,
  getSessionGeneration,
  type MessagingLocalQueue,
} from "@compass/api-client";

let localQueue: MessagingLocalQueue | undefined;
let confirmed: { ownerId: string; role: MessagingRole; generation: number } | undefined;

function queue(): MessagingLocalQueue {
  localQueue ??= createMessagingLocalQueue();
  return localQueue;
}

/** Confirm local messaging access after the application has verified the signed-in user. */
export function confirmMessagingOwner(ownerId: string, role: MessagingRole): void {
  const generation = getSessionGeneration(fetchClient);
  if (
    confirmed?.ownerId === ownerId &&
    confirmed.role === role &&
    confirmed.generation === generation
  ) {
    return;
  }
  queue().confirmOwner({ ownerId, role }, generation);
  confirmed = { ownerId, role, generation };
}

/** Lock persisted messaging data when the verified session ends. */
export function lockMessagingAccess(): void {
  queue().lock();
  confirmed = undefined;
}

/** Return the signed-in owner's local draft and unsent-message counts. */
export function getMessagingLocalCounts(): Promise<{ drafts: number; unsent: number }> {
  return queue().counts();
}

/** Delete this owner's local messaging data after an explicit user confirmation. */
export async function clearMessagingOwnerAfterConfirmation(ownerId: string): Promise<void> {
  await queue().clearOwnerAfterConfirmation(ownerId, true);
  if (confirmed?.ownerId === ownerId) confirmed = undefined;
}

export function getMessagingQueue(): MessagingLocalQueue {
  return queue();
}

export function getConfirmedMessagingOwner():
  | { ownerId: string; role: MessagingRole; generation: number }
  | undefined {
  return confirmed;
}
