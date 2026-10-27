import {
  createMessagingApi,
  fetchClient,
  MessagingApiError,
  MessagingLocalError,
  type MessagingPair,
  type MessagingRole,
  type MessagingStatus,
  mergeMessagePages,
  messagingKeys,
} from "@compass/api-client";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatView } from "../components/chat/ChatView";
import type { ChatLocalItem } from "../components/chat/types";
import { getMessagingQueue } from "./access";
import { toChatLocalItem, toChatMessage } from "./format";

export type MessagingChatProps = {
  ownerId: string;
  role: MessagingRole;
  patientId: string;
  doctorId?: string;
  sendTyping?: (pair: { patientId: string; expectedDoctorId: string }, active: boolean) => void;
};

/** Connected shared chat: API history, local draft/outbox, read cursors, and presence. */
export function MessagingChat({
  ownerId,
  role,
  patientId,
  doctorId,
  sendTyping,
}: MessagingChatProps) {
  const queryClient = useQueryClient();
  const api = useMemo(() => createMessagingApi(), []);
  const queue = getMessagingQueue();
  const owner = useMemo(() => ({ ownerId, role }), [ownerId, role]);
  const doctorStatusKey = useMemo(
    () => messagingKeys.status({ ...owner, patientId, expectedDoctorId: doctorId ?? "" }),
    [doctorId, owner, patientId],
  );
  const bootstrapKey = useMemo(
    () => [...messagingKeys.owner(owner), "bootstrap-status", patientId] as const,
    [owner, patientId],
  );
  const doctorStatus = useQuery({
    queryKey: doctorStatusKey,
    queryFn: ({ signal }) => readStatus(patientId, signal),
    enabled: role === "doctor" && Boolean(doctorId),
    retry: false,
    staleTime: 20_000,
    refetchInterval: 20_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
  });
  const patientStatus = useQuery({
    queryKey: bootstrapKey,
    queryFn: ({ signal }) => readStatus(patientId, signal),
    enabled: role === "patient",
    retry: false,
    staleTime: 20_000,
    refetchInterval: 20_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
  });
  const statusQuery = role === "doctor" ? doctorStatus : patientStatus;
  const status = statusQuery.data;
  const pair = useMemo<MessagingPair | undefined>(() => {
    const expectedDoctorId = role === "doctor" ? doctorId : status?.doctor_id;
    return expectedDoctorId ? { ...owner, patientId, expectedDoctorId } : undefined;
  }, [doctorId, owner, patientId, role, status?.doctor_id]);
  const pairKey = pair ? `${pair.patientId}:${pair.expectedDoctorId}` : "";
  const pairChanged = role === "doctor" && Boolean(status && status.doctor_id !== doctorId);
  const pairReady = Boolean(pair && status && !pairChanged);
  const [draft, setDraft] = useState("");
  const [outbox, setOutbox] = useState<Awaited<ReturnType<typeof queue.listOutbox>>>([]);
  const [localReady, setLocalReady] = useState(false);
  const [localError, setLocalError] = useState(false);
  const [localItems, setLocalItems] = useState<ChatLocalItem[]>([]);
  const draftWrite = useRef<Promise<unknown>>(Promise.resolve());
  const draftRevision = useRef(0);
  const activePairKey = useRef("");

  const syncLocal = useCallback(async () => {
    if (!pair || !pairReady) return;
    const expectedKey = pairKey;
    const expectedDraftRevision = draftRevision.current;
    try {
      await draftWrite.current;
      const [savedDraft, items] = await Promise.all([queue.getDraft(pair), queue.listOutbox(pair)]);
      if (activePairKey.current !== expectedKey) return;
      if (draftRevision.current === expectedDraftRevision) setDraft(savedDraft?.text ?? "");
      setOutbox(items);
      setLocalReady(true);
      setLocalError(false);
    } catch (error) {
      if (activePairKey.current !== expectedKey) return;
      if (error instanceof MessagingLocalError && error.code === "messaging_locked") {
        draftRevision.current += 1;
        setDraft("");
        setOutbox([]);
        setLocalItems([]);
        setLocalReady(false);
        setLocalError(false);
      } else {
        setLocalError(true);
      }
    }
  }, [pair, pairKey, pairReady, queue]);

  const refreshChat = useCallback(async () => {
    if (!pair || !pairReady) return;
    const [accepted] = await Promise.all([
      queue.flush(pair).catch(() => []),
      queryClient.invalidateQueries({ queryKey: messagingKeys.status(pair) }),
      queryClient.invalidateQueries({ queryKey: messagingKeys.history(pair) }),
    ]);
    await syncLocal();
    if (accepted.length > 0) {
      await queryClient.invalidateQueries({ queryKey: messagingKeys.history(pair) });
    }
  }, [pair, pairReady, queryClient, queue, syncLocal]);

  useEffect(() => {
    activePairKey.current = pairReady ? pairKey : "";
    draftRevision.current += 1;
    setDraft("");
    setOutbox([]);
    setLocalReady(false);
    setLocalError(false);
    if (pairReady && pair) {
      void syncLocal();
      void queue
        .flush(pair)
        .then(syncLocal)
        .catch(() => syncLocal());
    }
    return () => {
      activePairKey.current = "";
    };
  }, [pair, pairKey, pairReady, queue, syncLocal]);

  useEffect(() => {
    const onAccessChange = () => void syncLocal();
    return queue.subscribe(onAccessChange);
  }, [queue, syncLocal]);

  useEffect(() => {
    if (!pairReady) return;
    const onReturn = () => {
      if (document.visibilityState === "visible") void refreshChat();
    };
    window.addEventListener("focus", onReturn);
    window.addEventListener("online", onReturn);
    document.addEventListener("visibilitychange", onReturn);
    return () => {
      window.removeEventListener("focus", onReturn);
      window.removeEventListener("online", onReturn);
      document.removeEventListener("visibilitychange", onReturn);
    };
  }, [pairReady, refreshChat]);

  useEffect(() => {
    const urls: string[] = [];
    setLocalItems(
      outbox.map((item) => {
        let url: string | undefined;
        if (item.kind === "voice" && item.blob && typeof URL.createObjectURL === "function") {
          url = URL.createObjectURL(item.blob);
          urls.push(url);
        }
        return toChatLocalItem(item, url);
      }),
    );
    return () => {
      urls.forEach((url) => {
        URL.revokeObjectURL(url);
      });
    };
  }, [outbox]);

  const history = useInfiniteQuery({
    queryKey: pair
      ? messagingKeys.history(pair)
      : [...messagingKeys.owner(owner), "history-disabled"],
    queryFn: ({ pageParam, signal }) =>
      pair ? api.getHistory(pair, { beforeSeq: pageParam, limit: 50 }, signal) : Promise.reject(),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.next_before_seq ?? undefined,
    enabled: pairReady,
    retry: false,
    staleTime: 15_000,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
  });
  const messages = useMemo(() => {
    if (!pair || pairChanged) return [];
    return mergeMessagePages(history.data?.pages.map((page) => page.items) ?? []).map((message) =>
      toChatMessage(message, ownerId, patientId),
    );
  }, [history.data?.pages, ownerId, pair, pairChanged, patientId]);
  const latestPage = history.data?.pages[0];
  const hasOlderMessages = Boolean(history.data?.pages.at(-1)?.next_before_seq);

  const saveDraft = (next: string) => {
    draftRevision.current += 1;
    setDraft(next);
    if (pair && pairReady) {
      draftWrite.current = queue.saveDraft(pair, next).catch(() => setLocalError(true));
    }
  };

  const sendText = async (text: string) => {
    if (!pair || !pairReady) throw new Error("Чат пока недоступен.");
    await draftWrite.current;
    await queue.enqueueText(pair, text);
    draftRevision.current += 1;
    setDraft("");
    await queue.saveDraft(pair, "");
    await syncLocal();
    const accepted = await queue.flush(pair);
    await syncLocal();
    if (accepted.length > 0) {
      await queryClient.invalidateQueries({ queryKey: messagingKeys.history(pair) });
    }
  };

  const sendVoice = async (audio: Blob) => {
    if (!pair || !pairReady) throw new Error("Чат пока недоступен.");
    const item = await queue.enqueueVoice(pair, audio, "voice.webm");
    setOutbox((current) => [...current, item]);
    const accepted = await queue.flush(pair);
    await syncLocal();
    if (accepted.length > 0) {
      await queryClient.invalidateQueries({ queryKey: messagingKeys.history(pair) });
    }
  };

  const markRead = (seq: number) => {
    if (!pair || !pairReady) return;
    void api
      .markRead(pair, seq)
      .then(() =>
        Promise.all([
          queryClient.invalidateQueries({ queryKey: messagingKeys.history(pair) }),
          queryClient.invalidateQueries({ queryKey: messagingKeys.unread(owner) }),
          queryClient.invalidateQueries({
            predicate: ({ queryKey }) => queryKey[1] === "/api/v1/messaging/unread",
          }),
        ]),
      )
      .catch(() => undefined);
  };

  const retryLocal = async (id: string) => {
    if (!pair || !pairReady) return;
    await queue.retry(id);
    const accepted = await queue.flush(pair);
    await syncLocal();
    if (accepted.length > 0) {
      await queryClient.invalidateQueries({ queryKey: messagingKeys.history(pair) });
    }
  };

  const deleteLocal = async (id: string) => {
    await queue.deleteUnsent(id);
    await syncLocal();
  };

  let loadState: "loading" | "ready" | "error" = "ready";
  let loadError = "Не удалось загрузить сообщения. Проверьте связь и попробуйте ещё раз.";
  if (role === "doctor" && !doctorId) {
    loadState = "error";
    loadError = "Не удалось определить врача для переписки.";
  } else if (statusQuery.isPending || !pairReady || history.isPending) {
    loadState = "loading";
  } else if (pairChanged) {
    loadState = "error";
    loadError = "Лечащий врач пациента изменился. Обновите карточку перед отправкой сообщения.";
  } else if (statusQuery.isError || history.isError) {
    loadState = "error";
  }
  const peerName = status?.peer_full_name ?? (role === "doctor" ? "пациентом" : "врачом");
  const peerStatus = status
    ? { label: status.online ? "в сети" : "не в сети", online: status.online }
    : undefined;
  const isPeerTyping = Boolean(
    status?.typing_until && new Date(status.typing_until).getTime() > Date.now(),
  );
  // The lease arrives by socket and is a few seconds long: re-render once it expires.
  const [, setTypingTick] = useState(0);
  useEffect(() => {
    if (!status?.typing_until) return;
    const remaining = new Date(status.typing_until).getTime() - Date.now();
    if (remaining <= 0) return;
    const timer = window.setTimeout(() => setTypingTick((tick) => tick + 1), remaining + 50);
    return () => window.clearTimeout(timer);
  }, [status?.typing_until]);
  const ownReadThrough = latestPage?.read_through_seq ?? 0;
  const peerReadThrough = latestPage?.peer_read_through_seq ?? 0;
  const disabled = !pairReady || !localReady || pairChanged;
  const displayLocalItems = pairChanged ? [] : localItems;

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-2">
      {localError && (
        <p role="alert" className="text-sm text-danger">
          Не удалось сохранить сообщение на этом устройстве.
        </p>
      )}
      <ChatView
        role={role}
        peerName={peerName}
        peerStatus={peerStatus}
        messages={messages}
        localItems={displayLocalItems}
        draft={draft}
        onDraftChange={saveDraft}
        onSendText={sendText}
        onSendVoice={sendVoice}
        onReadThrough={markRead}
        readThroughSeq={ownReadThrough}
        peerReadThroughSeq={peerReadThrough}
        hasOlderMessages={hasOlderMessages}
        olderLoading={history.isFetchingNextPage}
        onLoadOlder={() => history.fetchNextPage().then(() => undefined)}
        loadState={loadState}
        loadError={loadError}
        onRetryLoad={() => void Promise.all([statusQuery.refetch(), history.refetch()])}
        isPeerTyping={isPeerTyping}
        onTypingChange={(active) => {
          if (pair)
            sendTyping?.(
              { patientId: pair.patientId, expectedDoctorId: pair.expectedDoctorId },
              active,
            );
        }}
        onRetryLocal={(id) => void retryLocal(id)}
        onDeleteLocal={(id) => void deleteLocal(id)}
        disabled={disabled}
      />
    </div>
  );
}

async function readStatus(patientId: string, signal?: AbortSignal): Promise<MessagingStatus> {
  const result = await fetchClient.GET("/api/v1/patients/{patient_id}/messaging-status", {
    params: { path: { patient_id: patientId } },
    signal,
  });
  if (!result.response.ok || result.error !== undefined || !result.data) {
    throw new MessagingApiError(
      result.response.status,
      undefined,
      "Не удалось загрузить статус переписки",
    );
  }
  if (result.data.patient_id !== patientId) {
    throw new MessagingApiError(409, "conversation_changed");
  }
  return result.data;
}
