import { Alert, Button } from "@compass/ui";
import closeIcon from "@compass/ui/assets/ikonka-zakryt.svg";
import { useState } from "react";
import { diaryEntryStatusLabel } from "./entry-status";
import { MoodScale } from "./MoodScale";
import { PatientPage } from "./PatientPage";
import type { DiaryEntryRecord } from "./types";
import { VoicePlayback } from "./VoicePlayback";

function transcriptionStatus(entry: DiaryEntryRecord, online: boolean): string {
  if (entry.audio?.status === "done") return "Расшифровка готова";
  return diaryEntryStatusLabel(entry, online) ?? "Расшифровывается…";
}

/** Post-save voice view keeps the committed recording visible while transcription is pending. */
export function VoiceSavedPresentation({
  entry,
  online,
  audioDurationMs,
  audioUrl,
  onClose,
  onSave,
}: {
  entry: DiaryEntryRecord;
  online: boolean;
  audioDurationMs: number;
  audioUrl: string | null;
  onClose: () => void;
  onSave: (moodLevel: number | null) => Promise<void>;
}) {
  const [moodLevel, setMoodLevel] = useState<number | null>(entry.mood_level);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      await onSave(moodLevel);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось сохранить настроение.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <PatientPage className="patient-voice-saved" navigation={false}>
      <header className="patient-subpage-header">
        <button type="button" aria-label="Закрыть" onClick={onClose}>
          <img src={closeIcon} alt="" />
        </button>
        <h1>Голосовая запись</h1>
        <span className="patient-subpage-header__spacer" />
      </header>
      <main className="patient-voice-saved__content">
        {audioUrl ? (
          <VoicePlayback src={audioUrl} durationMs={audioDurationMs} />
        ) : (
          <p className="patient-screen-loading" role="status">
            Открываем сохранённую запись…
          </p>
        )}
        <section className="patient-transcription-card" aria-labelledby="voice-transcription-title">
          <h2 id="voice-transcription-title">Расшифровка</h2>
          <p className="patient-transcription-card__status" role="status">
            {transcriptionStatus(entry, online)}
          </p>
          {entry.audio?.transcript ? (
            <p className="patient-transcription-card__text">{entry.audio.transcript}</p>
          ) : (
            <div className="patient-transcription-skeleton" aria-hidden="true">
              <i />
              <i />
              <i />
              <i />
            </div>
          )}
          <p className="patient-transcription-card__note">
            Можно закрыть экран. Текст появится в дневнике, когда будет готов.
          </p>
        </section>
        <section className="patient-voice-saved__mood">
          <p>Настроение, по желанию</p>
          <MoodScale context="quick" selected={moodLevel} onSelect={setMoodLevel} />
        </section>
        {error && <Alert tone="error">{error}</Alert>}
      </main>
      <footer className="patient-voice-saved__footer">
        <Button loading={saving} onClick={() => void save()}>
          Сохранить
        </Button>
      </footer>
    </PatientPage>
  );
}
