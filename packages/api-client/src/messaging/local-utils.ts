import {
  createMessagingApi,
  type Message,
  type MessagingConversation,
  type MessagingOwner,
  type MessagingPair,
} from "./api";
import type { MessagingOutboxItem } from "./local";
import { MessagingLocalError } from "./local-error";

export function leaseOwner(lease: Pick<MessagingOutboxItem, "ownerId" | "role">): MessagingOwner {
  return { ownerId: lease.ownerId, role: lease.role };
}

export function pairKey(ownerId: string, pair: MessagingConversation): string {
  return `${ownerId}/${pair.patientId}/${pair.expectedDoctorId}`;
}

export function errorParts(error: unknown): { status?: number; code?: string } {
  if (!error || typeof error !== "object") return {};
  return {
    status: "status" in error && typeof error.status === "number" ? error.status : undefined,
    code: "code" in error && typeof error.code === "string" ? error.code : undefined,
  };
}

export async function sendQueuedMessage(item: MessagingOutboxItem): Promise<Message> {
  const api = createMessagingApi();
  const pair: MessagingPair = {
    ownerId: item.ownerId,
    role: item.role,
    patientId: item.patientId,
    expectedDoctorId: item.expectedDoctorId,
  };
  if (item.kind === "text") {
    return api.sendText(pair, { clientMessageId: item.clientMessageId, text: item.text ?? "" });
  }
  if (!item.blob) throw new MessagingLocalError("messaging_voice_missing");
  return api.sendVoice(pair, {
    clientMessageId: item.clientMessageId,
    blob: item.blob,
    fileName: item.fileName,
  });
}
