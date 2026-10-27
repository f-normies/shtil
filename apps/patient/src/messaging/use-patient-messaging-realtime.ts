import { encodeTypingCommand, expireSession, fetchClient } from "@compass/api-client";
import { refreshMessaging } from "@compass/ui";
import type { QueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";

type MessagingPair = { patientId: string; expectedDoctorId: string };
type SendTyping = (pair: MessagingPair, active: boolean) => void;
type EventEnvelope = { type: string; data?: Record<string, unknown> };

function socketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/api/v1/ws`;
}

function parseEvent(data: unknown): EventEnvelope | null {
  try {
    const event: unknown = JSON.parse(String(data));
    if (event && typeof event === "object" && "type" in event && typeof event.type === "string") {
      const eventData = "data" in event ? event.data : undefined;
      return {
        type: event.type,
        data:
          eventData && typeof eventData === "object" && !Array.isArray(eventData)
            ? (eventData as Record<string, unknown>)
            : undefined,
      };
    }
  } catch {
    // Ignore malformed frames; REST refresh and the next socket connection can recover.
  }
  return null;
}

/** One patient chat WebSocket while /chat is open; a shared shell can own it later. */
export function usePatientMessagingRealtime(
  patientId: string | null,
  queryClient: QueryClient,
): SendTyping {
  const socketRef = useRef<WebSocket | null>(null);

  const sendTyping = useCallback<SendTyping>((pair, active) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(encodeTypingCommand(pair, active));
  }, []);

  useEffect(() => {
    if (!patientId) return;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let stopped = false;

    function connect(): void {
      if (stopped) return;
      socket = new WebSocket(socketUrl());
      socketRef.current = socket;
      socket.onmessage = (message) => {
        if (stopped) return;
        const event = parseEvent(message.data);
        if (!event) return;
        if (event.type === "ready") attempt = 0;
        if (event.type === "ready" || event.type.startsWith("message.")) {
          try {
            void Promise.resolve(refreshMessaging(queryClient, event.type, event.data)).catch(
              () => undefined,
            );
          } catch {
            // A later event or normal query refresh can recover if refresh cannot start.
          }
        }
      };
      socket.onclose = (event) => {
        if (stopped) return;
        if (socketRef.current === socket) socketRef.current = null;
        if (event.code === 4401) {
          stopped = true;
          void expireSession(fetchClient);
          return;
        }
        const delay = Math.min(1_000 * 2 ** attempt, 30_000);
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
    }

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      const activeSocket = socket;
      if (activeSocket && socketRef.current === activeSocket) socketRef.current = null;
      activeSocket?.close();
    };
  }, [patientId, queryClient]);

  return sendTyping;
}
