import { Button } from "@compass/ui";
import { VoiceCapture } from "./VoiceCapture";

const TOPICS = [
  "Как спали",
  "Что тревожило",
  "Что порадовало",
  "Новые лекарства или побочные эффекты",
];

export function CheckInStoryStep({
  mode,
  text,
  audioBlob,
  audioDurationMs,
  recording,
  onModeChange,
  onTextChange,
  onVoiceComplete,
  onVoiceSaved,
  onDurationKnown,
  onRecordingChange,
  onNext,
}: {
  mode: "voice" | "text";
  text: string;
  audioBlob: Blob | null;
  audioDurationMs: number | null;
  recording: boolean;
  onModeChange: (mode: "voice" | "text") => void;
  onTextChange: (text: string) => void;
  onVoiceComplete: (blob: Blob, durationMs: number) => Promise<void>;
  onVoiceSaved: (blob: Blob, durationMs: number) => void;
  onDurationKnown: (durationMs: number) => void;
  onRecordingChange: (recording: boolean) => void;
  onNext: () => void;
}) {
  return (
    <>
      <div className="patient-checkin-main patient-checkin-main--story">
        <h1 className="type-patient-question">Как прошёл день?</h1>
        <p className="type-patient-body patient-checkin-subtitle">
          Расскажите своими словами. Хватит и одной фразы.
        </p>
        <p className="patient-story-label">О чём можно рассказать</p>
        <div className="patient-story-topics">
          {TOPICS.map((topic) => (
            <button type="button" key={topic}>
              {topic}
            </button>
          ))}
        </div>
        {mode === "voice" ? (
          <VoiceCapture
            onComplete={onVoiceComplete}
            onSavedBlob={onVoiceSaved}
            onDurationKnown={onDurationKnown}
            onRecordingChange={onRecordingChange}
            savedBlob={audioBlob}
            savedDurationMs={audioDurationMs}
            allowDelete
          />
        ) : (
          <label className="patient-story-text-field">
            <span className="sr-only">Расскажите о своём дне</span>
            <textarea
              aria-label="Расскажите о своём дне"
              value={text}
              maxLength={20_000}
              rows={5}
              placeholder="Напишите, что хочется отметить…"
              onChange={(event) => onTextChange(event.currentTarget.value)}
            />
            <small>{text.length} из 20 000</small>
          </label>
        )}
      </div>
      {!recording && (
        <div className="patient-checkin-actions patient-checkin-actions--split">
          <Button variant="link" onClick={() => onModeChange(mode === "voice" ? "text" : "voice")}>
            {mode === "voice" ? "Написать текстом" : "Записать голосом"}
          </Button>
          {/* «Пропустить» — текстовая, как на кадре 15; заливная только «Дальше». */}
          {(mode === "text" ? text.trim() : audioBlob) ? (
            <Button className="patient-checkin-primary" onClick={onNext}>
              Дальше
            </Button>
          ) : (
            <Button variant="link" className="patient-checkin-skip" onClick={onNext}>
              Пропустить
            </Button>
          )}
        </div>
      )}
    </>
  );
}
