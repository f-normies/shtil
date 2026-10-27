import type { ChatContent, ChatLocalItem, ChatMessage } from "./types";
import { VoiceMessagePlayer } from "./VoiceMessagePlayer";

export type ChatRenderItem =
  | { kind: "server"; message: ChatMessage }
  | { kind: "local"; message: ChatLocalItem };

export interface ChatMessageItemProps {
  item: ChatRenderItem;
  peerReadThroughSeq: number;
  onRetryLocal?: (id: string) => void;
  onDeleteLocal?: (id: string) => void;
}

function formatMessageTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(date);
}

function Transcript({ content }: { content: Extract<ChatContent, { type: "voice" }> }) {
  if (!content.transcript) return null;
  switch (content.transcript.status) {
    case "queued":
      return <p className="ui-chat-message__transcript">Расшифровывается</p>;
    case "done":
      return <p className="ui-chat-message__transcript">{content.transcript.text}</p>;
    case "empty":
      return <p className="ui-chat-message__transcript">Речь не обнаружена</p>;
    case "error":
      return <p className="ui-chat-message__transcript">Не удалось расшифровать</p>;
  }
}

function Content({ content }: { content: ChatContent }) {
  if (content.type === "text") return <p className="ui-chat-message__text">{content.text}</p>;
  return (
    <div className="ui-chat-message__voice">
      <VoiceMessagePlayer src={content.src} />
      <Transcript content={content} />
    </div>
  );
}

export function ChatMessageItem({
  item,
  peerReadThroughSeq,
  onRetryLocal,
  onDeleteLocal,
}: ChatMessageItemProps) {
  const local = item.kind === "local" ? item.message : null;
  const message = item.message;
  const serverMessage = item.kind === "server" ? item.message : null;
  const sender = serverMessage?.sender ?? "self";
  const seq = serverMessage?.seq ?? null;
  const read = sender === "self" && seq !== null && seq <= peerReadThroughSeq;

  return (
    <article
      className={`ui-chat-message ui-chat-message--${sender}${local ? " ui-chat-message--local" : ""}`}
      data-incoming-seq={sender === "peer" && seq !== null ? seq : undefined}
      aria-label={sender === "peer" ? "Сообщение собеседника" : "Ваше сообщение"}
    >
      <div className="ui-chat-message__bubble">
        <Content content={message.content} />
      </div>
      <div className="ui-chat-message__meta">
        <time dateTime={message.createdAt}>{formatMessageTime(message.createdAt)}</time>
        {local?.status === "queued" && (
          <span className="ui-chat-message__delivery">
            <span aria-hidden="true">◷</span> Отправится при подключении
          </span>
        )}
        {local?.status === "failed" && (
          <span className="ui-chat-message__delivery ui-chat-message__delivery--error">
            <span aria-hidden="true">!</span> Не отправлено
          </span>
        )}
        {!local && sender === "self" && (
          <span className="ui-chat-message__delivery">
            <span aria-hidden="true">{read ? "✓✓" : "✓"}</span>
            {read ? "Прочитано" : "Отправлено"}
          </span>
        )}
      </div>
      {local?.status === "failed" && (
        <div className="ui-chat-message__local-actions">
          {onRetryLocal && (
            <button type="button" onClick={() => onRetryLocal(local.id)}>
              Повторить
            </button>
          )}
          {onDeleteLocal && (
            <button type="button" onClick={() => onDeleteLocal(local.id)}>
              Удалить сообщение
            </button>
          )}
        </div>
      )}
    </article>
  );
}
