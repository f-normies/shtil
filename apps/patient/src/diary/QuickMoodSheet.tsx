import { Button } from "@compass/ui";
import { useEffect, useRef, useState } from "react";
import { resolveDiaryClock } from "./clock";
import { MOOD_WORDS, MoodScale, type MoodWord } from "./MoodScale";
import { getDiaryStore } from "./queries";
import type { DiaryContextCacheRecord, DiaryEntryRecord } from "./types";

/** A quick mood entry is created on its first level tap and edited for this sheet's lifetime. */
export function QuickMoodSheet({
  open,
  ownerId,
  cache,
  onClose,
  onSaved,
}: {
  open: boolean;
  ownerId: string;
  cache: DiaryContextCacheRecord | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const entryRef = useRef<DiaryEntryRecord | null>(null);
  const writeChain = useRef<Promise<void>>(Promise.resolve());
  const sessionRef = useRef(0);
  const [level, setLevel] = useState<number | null>(null);
  const [words, setWords] = useState<MoodWord[]>([]);
  const [saved, setSaved] = useState(false);
  const [savedTime, setSavedTime] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function persist(nextLevel: number | null, nextWords: MoodWord[]): void {
    const session = sessionRef.current;
    setLevel(nextLevel);
    setWords(nextWords);
    setError(null);
    writeChain.current = writeChain.current
      .catch(() => undefined)
      .then(async () => {
        if (session !== sessionRef.current) return;
        const store = await getDiaryStore();
        const entry = entryRef.current;
        if (!entry) {
          if (nextLevel === null) return;
          const clock = resolveDiaryClock(cache);
          const result = await store.enqueueEntry({
            ownerId,
            payload: {
              entry_id: crypto.randomUUID(),
              operation_id: crypto.randomUUID(),
              kind: "mood",
              mood_level: nextLevel,
              mood_words: nextWords,
              occurred_at: clock.occurredAt,
              daily_on: null,
              other_medicine_text: null,
              text: null,
            },
          });
          if (result.status === "daily-exists" || result.status === "conflict") {
            throw new Error("Не удалось сохранить отметку настроения.");
          }
          if (session !== sessionRef.current) {
            onSaved();
            return;
          }
          entryRef.current = result.entry;
        } else {
          const result = await store.enqueueUpdate({
            ownerId,
            entryId: entry.entry_id,
            payload: {
              operation_id: crypto.randomUUID(),
              expected_revision: entry.revision,
              mood_level: nextLevel,
              mood_words: nextWords,
            },
          });
          if (result.status === "conflict") throw new Error("Запись изменилась в другой вкладке.");
          if (session !== sessionRef.current) {
            onSaved();
            return;
          }
          entryRef.current = result.entry;
        }
        setSaved(true);
        const savedEntry = entryRef.current;
        if (savedEntry) {
          setSavedTime(
            new Intl.DateTimeFormat("ru-RU", {
              hour: "2-digit",
              minute: "2-digit",
              timeZone: resolveDiaryClock(cache).timezone,
            }).format(new Date(savedEntry.occurred_at)),
          );
        }
        onSaved();
      })
      .catch(() => {
        if (session !== sessionRef.current) return;
        setSaved(false);
        setSavedTime(null);
        setError("Не удалось сохранить отметку на устройстве. Попробуйте ещё раз.");
      });
  }

  function toggleWord(word: MoodWord): void {
    const next = words.includes(word) ? words.filter((item) => item !== word) : [...words, word];
    persist(level, next);
  }

  function handleClose(): void {
    sessionRef.current += 1;
    setLevel(null);
    setWords([]);
    setSaved(false);
    setSavedTime(null);
    setError(null);
    entryRef.current = null;
    writeChain.current = Promise.resolve();
    onClose();
  }

  return (
    <dialog
      ref={dialogRef}
      className="patient-sheet"
      aria-labelledby="quick-mood-title"
      onCancel={(event) => {
        event.preventDefault();
        handleClose();
      }}
      onClose={() => {
        if (open) handleClose();
      }}
    >
      <div className="patient-sheet__handle" aria-hidden="true" />
      <div className="patient-sheet__header">
        <div>
          <h2 id="quick-mood-title" className="type-patient-question">
            Как вы сейчас?
          </h2>
          <p className="patient-sheet__subtitle">Одно касание, и отметка сохранится</p>
        </div>
        <button
          className="patient-icon-button"
          type="button"
          aria-label="Закрыть"
          onClick={handleClose}
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <MoodScale context="quick" selected={level} onSelect={(next) => persist(next, words)} />
      <fieldset className="patient-word-tags">
        <legend className="sr-only">Слова о самочувствии</legend>
        {MOOD_WORDS.map((word) => (
          <button
            key={word}
            type="button"
            className="patient-word-tag"
            aria-pressed={words.includes(word)}
            data-selected={words.includes(word) || undefined}
            onClick={() => toggleWord(word)}
          >
            {word}
          </button>
        ))}
      </fieldset>
      {error && (
        <p role="alert" className="patient-inline-error">
          {error}
        </p>
      )}
      <p role="status" aria-live="polite" className="patient-save-status">
        {saved
          ? `Сохранено в дневник, ${savedTime ?? "сейчас"}`
          : "Слова можно добавить по желанию."}
      </p>
      <Button variant="secondary" className="patient-sheet__done" onClick={handleClose}>
        Готово
      </Button>
    </dialog>
  );
}
