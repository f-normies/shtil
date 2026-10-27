/**
 * События администратора по WebSocket (спека #56, раздел 5): `escalation.created` и
 * `escalation.updated` несут только id сигнала. Новая эскалация — плашка, любое событие —
 * обновить таблицу. Закрытие — по правилу кабинета врача (`onClose`): конец сессии — вход,
 * обрыв — переподключение с растущей паузой; пропущенное дочитается по REST.
 */
import { $api, expireSession, fetchClient, fetchCurrentUser } from "@compass/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { onClose } from "../card/realtime";
import { ESCALATIONS } from "./EscalationsSection";

/** Id последней эскалации до администратора (шаг 3) или null. */
export function useAdminEvents(): [string | null, () => void] {
  const queryClient = useQueryClient();
  const [created, setCreated] = useState<string | null>(null);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let ready = false;
    let stopped = false;
    const toLogin = () => {
      stopped = true;
      void expireSession(fetchClient);
    };
    const catchUp = async () => {
      const all = await queryClient.fetchQuery({
        ...$api.queryOptions("get", ESCALATIONS, { params: { query: { status: "all" } } }),
        staleTime: 0,
      });
      await queryClient.invalidateQueries({
        queryKey: $api.queryOptions("get", ESCALATIONS, { params: { query: { status: "open" } } })
          .queryKey,
      });
      if (!stopped) {
        // Пропущенное событие шага 3 восстанавливаем из REST. В плашке только врач и срок.
        setCreated(
          all.find((e) => e.step === 3 && !e.reviewed_at && !e.acknowledged_at && !e.resolved_at)
            ?.alert_id ?? null,
        );
      }
    };
    const connect = () => {
      if (stopped) {
        return;
      }
      ready = false;
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/api/v1/ws`);
      socket.onmessage = (message) => {
        let event: { type?: unknown; data?: { alert_id?: unknown } } | null = null;
        try {
          event = JSON.parse(String(message.data));
        } catch {
          return;
        }
        const type = event?.type;
        if (type === "ready") {
          ready = true;
          attempt = 0;
          void catchUp().catch(() => undefined);
          return;
        }
        if (type !== "escalation.created" && type !== "escalation.updated") {
          return;
        }
        void queryClient.invalidateQueries({
          predicate: (q) => String(q.queryKey[1]) === ESCALATIONS,
        });
        const id = event?.data?.alert_id;
        if (type === "escalation.created" && typeof id === "string") {
          setCreated(id);
        }
      };
      socket.onclose = (close) => {
        if (stopped) {
          return;
        }
        const actions = onClose({ code: close.code, wasReady: ready, attempt });
        attempt += 1;
        for (const action of actions) {
          if (action.kind === "toLogin") {
            toLogin();
          } else if (action.kind === "checkSession") {
            timer = setTimeout(() => {
              fetchCurrentUser(fetchClient)
                .then((user) => (user ? connect() : toLogin()))
                .catch(connect);
            }, action.delayMs);
          } else if (action.kind === "reconnect") {
            timer = setTimeout(connect, action.delayMs);
          }
        }
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, [queryClient]);

  return [created, () => setCreated(null)];
}
