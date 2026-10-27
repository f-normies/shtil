// Один компонент: прокрутка к низу, якорь при подгрузке старых сообщений, отметка прочитанного,
// «печатает…» и поле ввода делят одни и те же ref и состояния, поэтому файл длиннее 400 строк.
import {
  type FormEvent,
  Fragment,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Avatar } from "../Avatar";
import { ChatIcon } from "./ChatIcons";
import { ChatMessageItem, type ChatRenderItem } from "./ChatMessageItem";
import { chatDayLabel } from "./days";
import type { ChatViewProps } from "./types";
import { VoiceRecorder } from "./VoiceRecorder";

const MAX_TEXT_LENGTH = 20_000;
const NEAR_BOTTOM_PX = 48;

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return (
    words
      .slice(0, 2)
      .map((word) => word[0]?.toLocaleUpperCase("ru-RU") ?? "")
      .join("") || "?"
  );
}

function itemTimestamp(item: ChatRenderItem): number {
  const time = new Date(item.message.createdAt).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function isPromise(value: void | Promise<void>): value is Promise<void> {
  return Boolean(value && typeof (value as Promise<void>).then === "function");
}

/** Управляемый чат для кабинетов врача и пациента. */
export function ChatView({
  role,
  peerName,
  peerStatus,
  messages,
  localItems = [],
  draft,
  onDraftChange,
  onSendText,
  onReadThrough,
  readThroughSeq = 0,
  peerReadThroughSeq = 0,
  hasOlderMessages = false,
  olderLoading = false,
  onLoadOlder,
  loadState = "ready",
  loadError = "Не удалось загрузить сообщения.",
  onRetryLoad,
  isPeerTyping = false,
  onTypingChange,
  onRetryLocal,
  onDeleteLocal,
  onSendVoice,
  inputLabel = role === "doctor" ? "Сообщение пациентке" : "Сообщение врачу",
  disabled = false,
}: ChatViewProps) {
  const logRef = useRef<HTMLDivElement>(null);
  const didInitialScrollRef = useRef(false);
  const previousCountRef = useRef(0);
  const nearBottomRef = useRef(true);
  const prependAnchorRef = useRef<{ count: number; top: number; height: number } | null>(null);
  const lastReadSentRef = useRef(readThroughSeq);
  const readThroughRef = useRef(readThroughSeq);
  const onReadThroughRef = useRef(onReadThrough);
  const draftRef = useRef(draft);
  const typingCallbackRef = useRef(onTypingChange);
  const typingTimerRef = useRef<number | null>(null);
  const lastTypingAtRef = useRef(0);
  const typingActiveRef = useRef(false);
  const [showNewMessages, setShowNewMessages] = useState(false);
  const [sendingText, setSendingText] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  draftRef.current = draft;
  readThroughRef.current = readThroughSeq;
  onReadThroughRef.current = onReadThrough;
  typingCallbackRef.current = onTypingChange;

  const items = useMemo<ChatRenderItem[]>(() => {
    const combined: ChatRenderItem[] = [
      ...messages.map((message) => ({ kind: "server" as const, message })),
      ...localItems.map((message) => ({ kind: "local" as const, message })),
    ];
    return combined.sort((left, right) => itemTimestamp(left) - itemTimestamp(right));
  }, [messages, localItems]);
  const messageSignature = items
    .filter((item): item is Extract<ChatRenderItem, { kind: "server" }> => item.kind === "server")
    .map(({ message }) => `${message.id}:${message.seq}:${message.sender}`)
    .join("|");

  const scrollToBottom = useCallback(() => {
    const log = logRef.current;
    if (!log) return;
    log.scrollTop = log.scrollHeight;
    nearBottomRef.current = true;
    setShowNewMessages(false);
  }, []);

  useLayoutEffect(() => {
    const log = logRef.current;
    if (!log) return;
    const count = items.length;
    if (!didInitialScrollRef.current) {
      if (count > 0 || loadState !== "loading") {
        scrollToBottom();
        didInitialScrollRef.current = true;
      }
    } else {
      const anchor = prependAnchorRef.current;
      if (anchor && !olderLoading) {
        if (count > anchor.count) {
          log.scrollTop = anchor.top + (log.scrollHeight - anchor.height);
        }
        prependAnchorRef.current = null;
      } else if (!anchor && count > previousCountRef.current) {
        if (nearBottomRef.current) scrollToBottom();
        else setShowNewMessages(true);
      }
    }
    previousCountRef.current = count;
  }, [items.length, olderLoading, loadState, scrollToBottom]);

  useEffect(() => {
    const log = logRef.current;
    if (
      !messageSignature ||
      !log ||
      !onReadThroughRef.current ||
      typeof IntersectionObserver === "undefined"
    )
      return;
    const nodes = [...log.querySelectorAll<HTMLElement>("[data-incoming-seq]")];
    if (nodes.length === 0) return;

    const reportVisible = (entries: IntersectionObserverEntry[]) => {
      if (document.visibilityState !== "visible") return;
      const visibleSeqs = entries
        .filter((entry) => entry.isIntersecting && entry.intersectionRatio >= 0.55)
        .map((entry) => Number((entry.target as HTMLElement).dataset.incomingSeq))
        .filter(
          (seq) =>
            Number.isFinite(seq) && seq > Math.max(readThroughRef.current, lastReadSentRef.current),
        );
      const maxSeq = Math.max(...visibleSeqs);
      if (visibleSeqs.length && Number.isFinite(maxSeq)) {
        lastReadSentRef.current = maxSeq;
        onReadThroughRef.current?.(maxSeq);
      }
    };

    const observer = new IntersectionObserver(reportVisible, { root: log, threshold: [0.55] });
    nodes.forEach((node) => {
      observer.observe(node);
    });
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        nodes.forEach((node) => {
          observer.unobserve(node);
          observer.observe(node);
        });
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      observer.disconnect();
    };
  }, [messageSignature]);

  useEffect(() => {
    lastReadSentRef.current = Math.max(lastReadSentRef.current, readThroughSeq);
  }, [readThroughSeq]);

  useEffect(() => {
    return () => {
      if (typingTimerRef.current !== null) window.clearTimeout(typingTimerRef.current);
      if (typingActiveRef.current) typingCallbackRef.current?.(false);
    };
  }, []);

  const stopTyping = () => {
    if (typingTimerRef.current !== null) {
      window.clearTimeout(typingTimerRef.current);
      typingTimerRef.current = null;
    }
    if (typingActiveRef.current) {
      typingActiveRef.current = false;
      typingCallbackRef.current?.(false);
    }
  };

  const notifyTyping = () => {
    const now = Date.now();
    if (!typingActiveRef.current || now - lastTypingAtRef.current >= 2_000) {
      typingCallbackRef.current?.(true);
      lastTypingAtRef.current = now;
    }
    typingActiveRef.current = true;
    if (typingTimerRef.current !== null) window.clearTimeout(typingTimerRef.current);
    typingTimerRef.current = window.setTimeout(stopTyping, 2_000);
  };

  const sendText = async () => {
    const text = draftRef.current;
    if (!text.trim() || text.length > MAX_TEXT_LENGTH || sendingText || disabled) return;
    setSendingText(true);
    setSendError(null);
    stopTyping();
    try {
      const saved = onSendText(text.trim());
      if (isPromise(saved)) await saved;
      if (draftRef.current === text) onDraftChange("");
    } catch {
      setSendError("Не удалось сохранить сообщение. Текст остался в поле.");
    } finally {
      setSendingText(false);
    }
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void sendText();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing &&
      event.nativeEvent.keyCode !== 229
    ) {
      event.preventDefault();
      void sendText();
    }
  };

  const loadOlder = async () => {
    const log = logRef.current;
    if (!log || !onLoadOlder || olderLoading) return;
    prependAnchorRef.current = {
      count: items.length,
      top: log.scrollTop,
      height: log.scrollHeight,
    };
    try {
      const result = onLoadOlder();
      if (isPromise(result)) await result;
    } catch {
      prependAnchorRef.current = null;
    }
  };

  const handleScroll = () => {
    const log = logRef.current;
    if (!log) return;
    nearBottomRef.current = log.scrollHeight - log.scrollTop - log.clientHeight <= NEAR_BOTTOM_PX;
    if (nearBottomRef.current) setShowNewMessages(false);
  };

  return (
    <section className="ui-chat" data-role={role} aria-label={`Чат с ${peerName}`}>
      <header className="ui-chat__header">
        {role === "patient" && <Avatar initials={initials(peerName)} size={36} decorative />}
        <div className="ui-chat__peer">
          <strong className="ui-chat__peer-name" title={peerName}>
            {peerName}
          </strong>
          {peerStatus && (
            <span className="ui-chat__peer-status" role="status">
              {peerStatus.online && <span className="ui-chat__online-dot" aria-hidden="true" />}
              {peerStatus.label}
            </span>
          )}
        </div>
      </header>

      <div
        ref={logRef}
        className="ui-chat__log"
        role="log"
        aria-live="polite"
        aria-relevant="additions text"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users must be able to focus and scroll the log.
        tabIndex={0}
        onScroll={handleScroll}
      >
        {hasOlderMessages && loadState !== "error" && (
          <div className="ui-chat__older">
            <button type="button" disabled={olderLoading} onClick={() => void loadOlder()}>
              {olderLoading ? "Загружаем…" : "Загрузить более ранние сообщения"}
            </button>
          </div>
        )}
        {loadState === "loading" && (
          <p className="ui-chat__state" role="status">
            Загружаем сообщения…
          </p>
        )}
        {loadState === "error" && (
          <div className="ui-chat__state ui-chat__state--error" role="alert">
            <span>{loadError}</span>
            {onRetryLoad && (
              <button type="button" onClick={onRetryLoad}>
                Повторить
              </button>
            )}
          </div>
        )}
        {loadState === "ready" && items.length === 0 && (
          <p className="ui-chat__empty">Сообщений пока нет</p>
        )}
        {items.map((item, index) => {
          const day = chatDayLabel(item.message.createdAt);
          const previous = items[index - 1];
          const showDay =
            day !== "" && (!previous || chatDayLabel(previous.message.createdAt) !== day);
          return (
            <Fragment key={`${item.kind}-${item.message.id}`}>
              {showDay && (
                <div className="ui-chat__day">
                  <span>{day}</span>
                </div>
              )}
              <ChatMessageItem
                item={item}
                peerReadThroughSeq={peerReadThroughSeq}
                onRetryLocal={onRetryLocal}
                onDeleteLocal={onDeleteLocal}
              />
            </Fragment>
          );
        })}
        {isPeerTyping && (
          <div className="ui-chat__typing" role="status" aria-live="off">
            <span aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <span>{peerName} печатает…</span>
          </div>
        )}
        {showNewMessages && (
          <button type="button" className="ui-chat__new-messages" onClick={scrollToBottom}>
            Новое сообщение ↓
          </button>
        )}
      </div>

      <form className="ui-chat__composer" onSubmit={submit}>
        <label className="ui-visually-hidden" htmlFor={`ui-chat-draft-${role}`}>
          {inputLabel}
        </label>
        <textarea
          id={`ui-chat-draft-${role}`}
          className="ui-chat__input"
          value={draft}
          onChange={(event) => {
            onDraftChange(event.currentTarget.value);
            notifyTyping();
          }}
          onKeyDown={handleKeyDown}
          onBlur={stopTyping}
          placeholder={inputLabel}
          maxLength={MAX_TEXT_LENGTH}
          rows={role === "patient" ? 1 : 2}
          disabled={disabled || sendingText}
          aria-describedby={sendError ? `ui-chat-error-${role}` : `ui-chat-limit-${role}`}
        />
        <div className="ui-chat__composer-actions">
          <span id={`ui-chat-limit-${role}`} className="ui-chat__limit">
            До 20 000 символов
          </span>
          {onSendVoice && (
            <VoiceRecorder
              onSend={onSendVoice}
              disabled={disabled}
              doctorMode={role === "doctor"}
              triggerHidden={role === "patient" && Boolean(draft.trim())}
            />
          )}
          <button
            type="submit"
            className="ui-chat__send"
            aria-label="Отправить"
            disabled={disabled || sendingText || !draft.trim() || draft.length > MAX_TEXT_LENGTH}
          >
            <ChatIcon name="send" />
            {role === "doctor" && <span>{sendingText ? "Сохраняем…" : "Отправить"}</span>}
          </button>
        </div>
        {sendError && (
          <p id={`ui-chat-error-${role}`} className="ui-chat__send-error" role="alert">
            {sendError}
          </p>
        )}
      </form>
    </section>
  );
}
