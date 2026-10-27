import { type QueryClient, type QueryKey, queryOptions } from "@tanstack/react-query";
import type { Client } from "openapi-fetch";
import { shouldRetryApiQuery } from "../auth";
import { fetchClient } from "../index";
import type { components, paths } from "../schema";

type Schemas = components["schemas"];
export type MessagingRole = "doctor" | "patient";
export type MessagingOwner = { ownerId: string; role: MessagingRole };
export type MessagingConversation = { patientId: string; expectedDoctorId: string };
export type MessagingPair = MessagingOwner & MessagingConversation;
export type MessagingHistoryPage = { beforeSeq?: number; afterSeq?: number; limit?: number };
export type Message = Schemas["MessageOut"];
export type MessageHistory = Schemas["MessageHistoryOut"];
export type MessagingStatus = Schemas["MessagingStatusOut"];
export type UnreadConversation = Schemas["UnreadOut"];

export class MessagingApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly detail?: string;

  constructor(status: number, code?: string, detail?: string) {
    super(detail ?? code ?? `Messaging request failed (${status})`);
    this.name = "MessagingApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (!result.response.ok || result.error !== undefined) {
    const problem = result.error;
    const code =
      problem &&
      typeof problem === "object" &&
      "code" in problem &&
      typeof problem.code === "string"
        ? problem.code
        : undefined;
    const detail =
      problem &&
      typeof problem === "object" &&
      "detail" in problem &&
      typeof problem.detail === "string"
        ? problem.detail
        : undefined;
    throw new MessagingApiError(result.response.status, code, detail);
  }
  if (result.data === undefined) {
    throw new MessagingApiError(result.response.status, undefined, "Messaging response is empty");
  }
  return result.data;
}

export const messagingKeys = {
  owner: (owner: MessagingOwner) => ["messaging", owner.ownerId, owner.role] as const,
  history: (pair: MessagingPair) =>
    [...messagingKeys.owner(pair), "history", pair.patientId, pair.expectedDoctorId] as const,
  historyPage: (pair: MessagingPair, page: MessagingHistoryPage = {}) =>
    [
      ...messagingKeys.history(pair),
      page.beforeSeq ?? null,
      page.afterSeq ?? null,
      page.limit ?? 50,
    ] as const,
  status: (pair: MessagingPair) =>
    [...messagingKeys.owner(pair), "status", pair.patientId, pair.expectedDoctorId] as const,
  unread: (owner: MessagingOwner) => [...messagingKeys.owner(owner), "unread"] as const,
};

export type MessagingApi = ReturnType<typeof createMessagingApi>;

export function createMessagingApi(client: Client<paths> = fetchClient) {
  async function getHistory(
    pair: MessagingPair,
    page: MessagingHistoryPage = {},
    signal?: AbortSignal,
  ): Promise<MessageHistory> {
    const result = await client.GET("/api/v1/patients/{patient_id}/messages", {
      params: {
        path: { patient_id: pair.patientId },
        query: {
          expected_doctor_id: pair.expectedDoctorId,
          before_seq: page.beforeSeq,
          after_seq: page.afterSeq,
          limit: page.limit ?? 50,
        },
      },
      signal,
    });
    return unwrap(result);
  }

  async function getStatus(pair: MessagingPair, signal?: AbortSignal): Promise<MessagingStatus> {
    const result = await client.GET("/api/v1/patients/{patient_id}/messaging-status", {
      params: { path: { patient_id: pair.patientId } },
      signal,
    });
    const status = unwrap(result);
    if (status.patient_id !== pair.patientId || status.doctor_id !== pair.expectedDoctorId) {
      throw new MessagingApiError(409, "conversation_changed", "Conversation changed");
    }
    return status;
  }

  async function getUnread(
    owner: MessagingOwner,
    signal?: AbortSignal,
  ): Promise<UnreadConversation[]> {
    const result = await client.GET("/api/v1/messaging/unread", { signal });
    const unread = unwrap(result);
    const ownerField = owner.role === "patient" ? "patient_id" : "doctor_id";
    if (unread.some((conversation) => conversation[ownerField] !== owner.ownerId)) {
      throw new MessagingApiError(403, "messaging_owner_mismatch");
    }
    return unread;
  }

  async function sendText(
    pair: MessagingPair,
    input: { clientMessageId: string; text: string },
  ): Promise<Message> {
    const result = await client.POST("/api/v1/patients/{patient_id}/messages", {
      params: { path: { patient_id: pair.patientId } },
      body: {
        client_message_id: input.clientMessageId,
        expected_doctor_id: pair.expectedDoctorId,
        text: input.text,
      },
    });
    return unwrap(result);
  }

  async function sendVoice(
    pair: MessagingPair,
    input: { clientMessageId: string; blob: Blob; fileName?: string },
  ): Promise<Message> {
    const form = new FormData();
    form.append("expected_doctor_id", pair.expectedDoctorId);
    form.append("client_message_id", input.clientMessageId);
    form.append("file", input.blob, input.fileName ?? "voice");
    const result = await client.POST("/api/v1/patients/{patient_id}/messages/voice", {
      params: { path: { patient_id: pair.patientId } },
      // openapi-fetch 0.17 preserves FormData and lets Fetch set the boundary header.
      body: form as unknown as {
        client_message_id: string;
        expected_doctor_id: string;
        file: string;
      },
    });
    return unwrap(result);
  }

  async function markRead(pair: MessagingPair, throughSeq: number) {
    const result = await client.POST("/api/v1/patients/{patient_id}/messages/read", {
      params: { path: { patient_id: pair.patientId } },
      body: { expected_doctor_id: pair.expectedDoctorId, through_seq: throughSeq },
    });
    return unwrap(result);
  }

  function historyOptions(pair: MessagingPair, page: MessagingHistoryPage = {}) {
    return queryOptions({
      queryKey: messagingKeys.historyPage(pair, page),
      queryFn: ({ signal }) => getHistory(pair, page, signal),
      retry: shouldRetryApiQuery,
      staleTime: 15_000,
    });
  }

  function statusOptions(pair: MessagingPair) {
    return queryOptions({
      queryKey: messagingKeys.status(pair),
      queryFn: ({ signal }) => getStatus(pair, signal),
      retry: shouldRetryApiQuery,
      staleTime: 20_000,
    });
  }

  function unreadOptions(owner: MessagingOwner) {
    return queryOptions({
      queryKey: messagingKeys.unread(owner),
      queryFn: ({ signal }) => getUnread(owner, signal),
      retry: shouldRetryApiQuery,
      staleTime: 15_000,
    });
  }

  return {
    getHistory,
    getStatus,
    getUnread,
    sendText,
    sendVoice,
    markRead,
    historyOptions,
    statusOptions,
    unreadOptions,
  };
}

/** Combine newest-first history windows, keeping the latest copy of overlapping rows. */
export function mergeMessagePages(pages: readonly (readonly Message[])[]): Message[] {
  const byId = new Map<string, Message>();
  for (const page of [...pages].reverse()) {
    for (const item of page) byId.set(item.id, item);
  }
  return [...byId.values()].sort((left, right) => left.seq - right.seq);
}

export function messagingEventQueryKeys(
  eventType: string,
  pair: MessagingPair,
): Array<{ queryKey: QueryKey }> {
  if (eventType !== "ready" && !eventType.startsWith("message.")) return [];
  return [
    { queryKey: messagingKeys.history(pair) },
    { queryKey: messagingKeys.status(pair) },
    { queryKey: messagingKeys.unread(pair) },
  ];
}

export async function invalidateMessagingEvent(
  queryClient: QueryClient,
  eventType: string,
  pair: MessagingPair,
): Promise<void> {
  await Promise.all(
    messagingEventQueryKeys(eventType, pair).map(({ queryKey }) =>
      queryClient.invalidateQueries({ queryKey }),
    ),
  );
}

export function encodeTypingCommand(pair: MessagingConversation, active: boolean): string {
  return JSON.stringify({
    type: "message.typing",
    data: { patient_id: pair.patientId, expected_doctor_id: pair.expectedDoctorId, active },
  });
}

export function createTypingRateLimiter(
  intervalMs = 2_000,
): (active: boolean, now?: number) => boolean {
  let lastActiveAt = Number.NEGATIVE_INFINITY;
  return (active, now = Date.now()) => {
    if (!active) {
      lastActiveAt = Number.NEGATIVE_INFINITY;
      return true;
    }
    if (now - lastActiveAt < intervalMs) return false;
    lastActiveAt = now;
    return true;
  };
}
