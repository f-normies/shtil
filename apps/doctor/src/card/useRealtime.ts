/**
 * Одно соединение с WebSocket на весь кабинет врача (спека #13, раздел 6). Логика
 * решений — в `realtime.ts`; здесь только их исполнение.
 */
import {
  createTypingRateLimiter,
  encodeTypingCommand,
  expireSession,
  fetchClient,
  fetchCurrentUser,
} from "@compass/api-client";
import { refreshMessaging } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { retryOnce } from "./delivery";
import { refreshDiary } from "./diaryQueries";
import { notifyUrgent } from "./notify";
import { refreshAlerts, refreshAssessments, refreshUnread } from "./queries";
import { type Action, onClose, onMessage } from "./realtime";

export type RealtimeState = {
  /** Соединение установлено и сервер прислал `ready`. */
  connected: boolean;
  /** Растёт с каждым событием сигнала: плашка снова показывается после «Скрыть». */
  alertEvents: number;
  sendTyping: (pair: { patientId: string; expectedDoctorId: string }, active: boolean) => void;
};

export const RealtimeContext = createContext<RealtimeState>({
  connected: true,
  alertEvents: 0,
  sendTyping: () => undefined,
});

export function useRealtimeState(): RealtimeState {
  return useContext(RealtimeContext);
}

function socketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/api/v1/ws`;
}

/** Отметить доставку с одним повтором. Не бросает: результат — прошла ли отметка. */
function markDelivered(alertId: string, via: "ws" | "rest"): Promise<boolean> {
  return retryOnce(() =>
    fetchClient.POST("/api/v1/alerts/{alert_id}/delivered", {
      params: { path: { alert_id: alertId }, query: { via } },
    }),
  );
}

/** Уведомить, если новый или поднятый сигнал — «Обязательно»: в событии только id. */
async function notifyIfMandatory(alertId: string): Promise<void> {
  const { data } = await fetchClient.GET("/api/v1/alerts", {
    params: { query: { status: "open" } },
  });
  if ((data ?? []).some((a) => a.id === alertId && a.level === "mandatory")) {
    notifyUrgent();
  }
}

/** Отметить доставку недоставленных открытых сигналов после подключения (спека #12). */
async function catchUp(): Promise<void> {
  const { data } = await fetchClient.GET("/api/v1/alerts", {
    params: { query: { status: "open" } },
  });
  await Promise.all(
    // Сигнал, переданный заместителю (#56), доставляется ведущему врачу, не нам.
    (data ?? [])
      .filter((a) => a.delivered_at === null && !a.as_deputy)
      .map((a) => markDelivered(a.id, "rest")),
  );
}

export function useRealtime(): RealtimeState {
  const queryClient = useQueryClient();
  const [state, setState] = useState<RealtimeState>({
    connected: true,
    alertEvents: 0,
    sendTyping: () => undefined,
  });
  const socketRef = useRef<WebSocket | null>(null);
  const typingLimiters = useRef(new Map<string, ReturnType<typeof createTypingRateLimiter>>());
  const sendTyping = useCallback(
    (pair: { patientId: string; expectedDoctorId: string }, active: boolean) => {
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      const key = `${pair.patientId}:${pair.expectedDoctorId}`;
      let limiter = typingLimiters.current.get(key);
      if (!limiter) {
        limiter = createTypingRateLimiter();
        typingLimiters.current.set(key, limiter);
      }
      if (limiter(active)) socket.send(encodeTypingCommand(pair, active));
    },
    [],
  );

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let ready = false;
    let stopped = false;

    // The shared handler cancels requests, clears the cache and replaces the page with login.
    const toLogin = () => {
      stopped = true;
      void expireSession(fetchClient);
    };

    const run = (actions: Action[]) => {
      for (const action of actions) {
        switch (action.kind) {
          case "connected":
            ready = true;
            attempt = 0;
            setState((s) => ({ ...s, connected: true }));
            break;
          case "disconnected":
            setState((s) => ({ ...s, connected: false }));
            break;
          case "catchUp":
            // Неудачная отметка повторится при следующем подключении: сигнал остаётся открытым.
            catchUp()
              .catch(() => undefined)
              .finally(() => refreshAlerts(queryClient));
            break;
          case "markDelivered":
            // Не прошла и после повтора — сигнал остаётся недоставленным до catchUp
            // при следующем подключении.
            void markDelivered(action.alertId, "ws");
            break;
          case "refresh":
            if (action.what === "alerts") {
              setState((s) => ({ ...s, alertEvents: s.alertEvents + 1 }));
              void refreshAlerts(queryClient);
            } else if (action.what === "unread") {
              void refreshUnread(queryClient);
            } else {
              void refreshAssessments(queryClient);
            }
            break;
          case "refreshMessaging":
            void refreshMessaging(queryClient, action.eventType, action.data);
            break;
          case "refreshDiary":
            void refreshDiary(queryClient, action.patientId);
            break;
          case "notify":
            notifyUrgent();
            break;
          case "notifyIfMandatory":
            // Не удалось узнать уровень — без уведомления: плашка и так покажет сигнал.
            notifyIfMandatory(action.alertId).catch(() => undefined);
            break;
          case "toLogin":
            toLogin();
            break;
          case "checkSession":
            timer = setTimeout(() => {
              fetchCurrentUser(fetchClient)
                .then((user) => (user ? connect() : toLogin()))
                .catch(connect);
            }, action.delayMs);
            break;
          case "reconnect":
            timer = setTimeout(connect, action.delayMs);
            break;
        }
      }
    };

    function connect() {
      if (stopped) {
        return;
      }
      ready = false;
      const currentSocket = new WebSocket(socketUrl());
      socket = currentSocket;
      socketRef.current = currentSocket;
      currentSocket.onmessage = (message) => {
        let event: unknown;
        try {
          event = JSON.parse(String(message.data));
        } catch {
          // Не JSON — не событие сервера; onMessage такое и так пропускает.
          event = null;
        }
        run(onMessage(event));
      };
      currentSocket.onclose = (close) => {
        if (socketRef.current === currentSocket) socketRef.current = null;
        if (stopped) {
          return;
        }
        const actions = onClose({ code: close.code, wasReady: ready, attempt });
        attempt += 1;
        run(actions);
      };
    }

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      if (socketRef.current === socket) socketRef.current = null;
      socket?.close();
    };
  }, [queryClient]);

  return { ...state, sendTyping };
}
