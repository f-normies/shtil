import type { Message, MessagingOutboxItem } from "@compass/api-client";
import type { ChatContent, ChatLocalItem, ChatMessage } from "../components/chat/types";

export function toChatMessage(message: Message, ownerId: string, patientId: string): ChatMessage {
  const content: ChatContent =
    message.kind === "text"
      ? { type: "text", text: message.text ?? "" }
      : {
          type: "voice",
          src: `/api/v1/patients/${encodeURIComponent(patientId)}/messages/${encodeURIComponent(message.id)}/audio`,
          transcript: transcriptOf(message),
        };
  return {
    id: message.id,
    seq: message.seq,
    sender: message.sender_id === ownerId ? "self" : "peer",
    createdAt: message.created_at,
    content,
  };
}

export function toChatLocalItem(item: MessagingOutboxItem, audioUrl?: string): ChatLocalItem {
  const content: ChatContent =
    item.kind === "text"
      ? { type: "text", text: item.text ?? "" }
      : { type: "voice", src: audioUrl ?? "" };
  return {
    id: item.clientMessageId,
    createdAt: item.createdAt,
    content,
    status: item.status,
  };
}

function transcriptOf(message: Message): Extract<ChatContent, { type: "voice" }>["transcript"] {
  switch (message.transcription_status) {
    case "queued":
    case "processing":
      return { status: "queued" };
    case "done":
      return message.transcription_text
        ? { status: "done", text: message.transcription_text }
        : { status: "empty" };
    case "failed":
      return { status: "error" };
    default:
      return undefined;
  }
}
