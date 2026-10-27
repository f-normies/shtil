import { Button } from "@compass/ui";
import closeIcon from "@compass/ui/assets/ikonka-zakryt.svg";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { usePatientSyncState } from "../patient-sync-context";
import type { PatientSession } from "../session";
import { resolveDiaryClock } from "./clock";
import { PatientPage } from "./PatientPage";
import { getDiaryStore, useDiaryOverviewQueries } from "./queries";
import { syncAfterLocalWrite } from "./sync-after-write";
import { useDiaryLocal } from "./use-diary-local";
import { VoiceCapture } from "./VoiceCapture";

const PROMPTS = ["Как прошёл день", "Что тревожило", "Как спали"];

function ownerFor(session: PatientSession): string {
  return session.status === "online"
    ? session.user.id
    : session.status === "offline"
      ? session.ownerId
      : "";
}

/** Stop-then-queue quick voice flow; the voice command and Blob commit atomically. */
export function QuickVoiceScreen({ session }: { session: PatientSession }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const overview = useDiaryOverviewQueries(session, queryClient);
  const ownerId = ownerFor(session);
  const local = useDiaryLocal(session);
  const { diarySyncNow } = usePatientSyncState();
  const clock = resolveDiaryClock(overview.cache);
  const [recording, setRecording] = useState(false);
  const [entryId] = useState(() => crypto.randomUUID());
  const [operationId] = useState(() => crypto.randomUUID());

  async function saveVoice(blob: Blob, durationMs: number): Promise<void> {
    const result = await (await getDiaryStore()).enqueueVoiceEntry({
      ownerId,
      blob,
      payload: {
        entry_id: entryId,
        operation_id: operationId,
        kind: "voice",
        occurred_at: clock.occurredAt,
        daily_on: null,
        mood_level: null,
        mood_words: [],
        other_medicine_text: null,
        duration_ms: durationMs,
      },
    });
    if (result.status === "conflict") throw new Error("Не удалось сохранить голосовую запись.");
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "local-entries"] }),
      queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "outbox"] }),
    ]);
    queryClient.setQueryData(["diary", ownerId, "saved-voice-presentation", entryId], true);
    void syncAfterLocalWrite({ syncNow: diarySyncNow, refreshLocal: local.refresh });
    await navigate({ to: "/diary/$entryId", params: { entryId } });
  }

  return (
    <PatientPage navigation={false} className="patient-voice-screen">
      <header className="patient-subpage-header">
        <button type="button" aria-label="Закрыть" onClick={() => void navigate({ to: "/diary" })}>
          <img src={closeIcon} alt="" />
        </button>
        <h1>Голосовая запись</h1>
        <span className="patient-subpage-header__spacer" />
      </header>
      <main className="patient-voice-screen__content">
        <VoiceCapture
          onComplete={saveVoice}
          onRecordingChange={setRecording}
          allowPause
          allowDelete
          autoStart
        />
        <section className="patient-voice-prompts" aria-label="Подсказки для рассказа">
          <p>Если не знаете, с чего начать</p>
          <div>
            {PROMPTS.map((prompt) => (
              <span key={prompt}>{prompt}</span>
            ))}
          </div>
        </section>
        {!recording && (
          <p className="patient-voice-fallback">
            Если микрофон недоступен, можно <Link to="/diary/new/text">записать текстом</Link>.
          </p>
        )}
        {!recording && (
          <p className="patient-voice-save-note">
            После остановки аудио сохранится на устройстве. Расшифровка появится после отправки.
          </p>
        )}
      </main>
      {recording && (
        <p className="sr-only" role="status">
          Запись продолжается. Остановите её перед выходом.
        </p>
      )}
      {!recording && (
        <footer className="patient-voice-screen__footer">
          <Button variant="link" onClick={() => void navigate({ to: "/diary" })}>
            Закрыть
          </Button>
        </footer>
      )}
    </PatientPage>
  );
}
