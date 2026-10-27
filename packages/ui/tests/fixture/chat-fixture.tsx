import { type ComponentType, useState } from "react";
import * as UI from "../../src";

type LooseComponent = ComponentType<Record<string, unknown>>;
const ChatView = (UI as unknown as Record<string, LooseComponent | undefined>).ChatView;

export function ChatFixture() {
  const params = new URLSearchParams(window.location.search);
  const role = params.get("forma") === "patsient" ? "patient" : "doctor";
  const paginationMode = params.get("pagination") === "1";
  const voiceControls = params.get("voice-controls") === "1";
  const peerName = role === "patient" ? "Ирина Соколова" : "Марина Лебедева";
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState("");
  const [readThrough, setReadThrough] = useState(0);
  const [disabled, setDisabled] = useState(false);
  const [voiceCount, setVoiceCount] = useState(0);
  const [olderLoading, setOlderLoading] = useState(false);
  const [messages, setMessages] = useState(() =>
    paginationMode
      ? Array.from({ length: 18 }, (_, index) => ({
          id: `history-${index + 1}`,
          seq: index + 1,
          sender: index % 2 === 0 ? "peer" : "self",
          createdAt: new Date(Date.UTC(2026, 9, 25, 6, 15 + index)).toISOString(),
          content: {
            type: "text",
            text: `Историческое сообщение ${index + 1}. ${"Текст для прокрутки. ".repeat(5)}`,
          },
        }))
      : [
          {
            id: "message-1",
            seq: 1,
            sender: "peer",
            createdAt: "2026-10-25T09:15:00+03:00",
            content: { type: "text", text: "Добрый вечер, как вы себя чувствуете?" },
          },
          {
            id: "message-2",
            seq: 2,
            sender: "self",
            createdAt: "2026-10-25T09:19:00+03:00",
            content: { type: "text", text: "Здравствуйте, Марина." },
          },
        ],
  );

  if (!ChatView) return <p role="status">Компонент чата пока не подключён</p>;

  const saveText = async (text: string) => {
    await new Promise<void>((resolve) => window.setTimeout(resolve, 180));
    if (text === "Не сохраняй") throw new Error("local storage is unavailable");
    setSaved(text);
  };

  const loadOlder = async () => {
    setOlderLoading(true);
    await new Promise<void>((resolve) => window.setTimeout(resolve, 40));
    setMessages((current) => [
      {
        id: "older-message",
        seq: 0,
        sender: "peer",
        createdAt: "2026-10-25T08:00:00+03:00",
        content: { type: "text", text: "Старое сообщение для проверки позиции прокрутки." },
      },
      ...current,
    ]);
    await new Promise<void>((resolve) => window.setTimeout(resolve, 140));
    setOlderLoading(false);
  };

  const saveVoice = async () => {
    setVoiceCount((count) => count + 1);
  };

  return (
    <main style={{ width: "min(100%, 320px)", height: 640, margin: "0 auto" }}>
      <ChatView
        role={role}
        peerName={peerName}
        peerStatus={{ label: "в сети", online: true }}
        messages={messages}
        localItems={[]}
        draft={draft}
        onDraftChange={setDraft}
        onSendText={saveText}
        readThroughSeq={0}
        peerReadThroughSeq={2}
        onReadThrough={setReadThrough}
        onSendVoice={saveVoice}
        disabled={disabled}
        hasOlderMessages={paginationMode}
        olderLoading={olderLoading}
        onLoadOlder={loadOlder}
        isPeerTyping
      />
      <output data-testid="saved-message">{saved}</output>
      <output data-testid="read-through">{readThrough}</output>
      {voiceControls && (
        <>
          <button type="button" onClick={() => setDisabled((value) => !value)}>
            {disabled ? "Включить чат" : "Отключить чат"}
          </button>
          <output data-testid="voice-count">{voiceCount}</output>
        </>
      )}
      {paginationMode && <output data-testid="older-loading">{String(olderLoading)}</output>}
    </main>
  );
}
