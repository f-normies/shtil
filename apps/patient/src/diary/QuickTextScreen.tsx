import { Alert, Button } from "@compass/ui";
import microphoneIcon from "@compass/ui/assets/ikonka-mikrofon.svg";
import timeIcon from "@compass/ui/assets/ikonka-vremya.svg";
import closeIcon from "@compass/ui/assets/ikonka-zakryt.svg";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePatientSyncState } from "../patient-sync-context";
import type { PatientSession } from "../session";
import { resolveDiaryClock } from "./clock";
import { MOOD_WORDS, MoodScale, type MoodWord } from "./MoodScale";
import { PatientPage } from "./PatientPage";
import { getDiaryStore, useDiaryOverviewQueries } from "./queries";
import { syncAfterLocalWrite } from "./sync-after-write";
import type { DiaryDraftRecord } from "./types";
import { useDiaryLocal } from "./use-diary-local";

function ownerFor(session: PatientSession): string {
  return session.status === "online"
    ? session.user.id
    : session.status === "offline"
      ? session.ownerId
      : "";
}

function displayTime(occurredAt: string, timezone: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
  }).format(new Date(occurredAt));
}

/** Autosaved quick text draft with a durable local create before opening its detail. */
export function QuickTextScreen({ session }: { session: PatientSession }) {
  const ownerId = ownerFor(session);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const overview = useDiaryOverviewQueries(session, queryClient);
  const local = useDiaryLocal(session);
  const { diarySyncNow } = usePatientSyncState();
  const clock = useMemo(() => resolveDiaryClock(overview.cache), [overview.cache]);
  const [draftId, setDraftId] = useState<string>(() => crypto.randomUUID());
  const [entryId, setEntryId] = useState<string>(() => crypto.randomUUID());
  const [operationId, setOperationId] = useState<string>(() => crypto.randomUUID());
  const revisionRef = useRef(0);
  const [text, setText] = useState("");
  const [moodLevel, setMoodLevel] = useState<number | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedDraft, setSavedDraft] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textRef = useRef(text);
  const textAreaRef = useRef<HTMLTextAreaElement>(null);
  const moodRef = useRef<{ level: number | null; words: MoodWord[] }>({ level: null, words: [] });
  const chain = useRef<Promise<DiaryDraftRecord | null>>(Promise.resolve(null));
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (hydrated || local.drafts.isPending) return;
    const restored = local.drafts.data?.find(
      (item) => item.mode === "quick" && item.entry_kind === "text",
    );
    if (restored) {
      setDraftId(restored.draft_id);
      setEntryId(restored.entry_id);
      setOperationId(restored.operation_id);
      revisionRef.current = restored.revision;
      setText(restored.text);
      textRef.current = restored.text;
      const words = restored.mood_words.filter((word): word is MoodWord =>
        MOOD_WORDS.includes(word as MoodWord),
      );
      setMoodLevel(restored.mood_level);
      moodRef.current = { level: restored.mood_level, words };
      setSavedDraft(true);
    }
    setHydrated(true);
  }, [hydrated, local.drafts.data, local.drafts.isPending]);

  useEffect(() => {
    if (hydrated) textAreaRef.current?.focus({ preventScroll: true });
  }, [hydrated]);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const persistDraft = useCallback((): Promise<DiaryDraftRecord> => {
    chain.current = chain.current
      .catch(() => null)
      .then(async () => {
        const result = await (await getDiaryStore()).saveDraft({
          ownerId,
          draftId,
          entryId,
          operationId,
          expectedRevision: revisionRef.current,
          mode: "quick",
          entryKind: "text",
          occurredAt: clock.occurredAt,
          step: "story",
          moodLevel: moodRef.current.level,
          moodWords: moodRef.current.words,
          text: textRef.current,
          surveyStatus: "unknown",
        });
        if (result.status !== "saved") throw new Error("Черновик изменился в другой вкладке.");
        revisionRef.current = result.draft.revision;
        setSavedDraft(true);
        await local.refresh();
        return result.draft;
      })
      .catch((cause: unknown) => {
        setError(
          cause instanceof Error ? cause.message : "Не удалось сохранить черновик на устройстве.",
        );
        throw cause;
      });
    return chain.current.then((draft) => {
      if (!draft) throw new Error("Черновик ещё не создан.");
      return draft;
    });
  }, [clock.occurredAt, draftId, entryId, local.refresh, operationId, ownerId]);

  useEffect(() => {
    if (!hydrated || !ownerId) return;
    void persistDraft().catch(() => undefined);
  }, [hydrated, ownerId, persistDraft]);

  function scheduleDraftSave(): void {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      void persistDraft()
        .then(() => setError(null))
        .catch(() => undefined);
    }, 250);
  }

  async function save(): Promise<void> {
    const value = textRef.current.trim();
    if (!value) {
      setError("Добавьте текст, чтобы сохранить запись.");
      return;
    }
    textRef.current = value;
    setText(value);
    setSaving(true);
    setError(null);
    try {
      if (timer.current !== null) window.clearTimeout(timer.current);
      const latestDraft = await persistDraft();
      const store = await getDiaryStore();
      const result = await store.finalizeQuickText({
        ownerId,
        draftId,
        expectedDraftRevision: latestDraft.revision,
        payload: {
          entry_id: entryId,
          operation_id: operationId,
          kind: "text",
          occurred_at: clock.occurredAt,
          daily_on: null,
          mood_level: moodRef.current.level,
          mood_words: moodRef.current.level === null ? [] : moodRef.current.words,
          text: value,
          other_medicine_text: null,
        },
      });
      if (result.status !== "queued" && result.status !== "replayed")
        throw new Error("Запись изменилась. Черновик сохранён для восстановления.");
      await Promise.all([
        local.refresh(),
        queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "outbox"] }),
        queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "server-entries"] }),
      ]);
      void syncAfterLocalWrite({ syncNow: diarySyncNow, refreshLocal: local.refresh });
      await navigate({ to: "/diary/$entryId", params: { entryId } });
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Не удалось сохранить запись на устройстве.",
      );
    } finally {
      setSaving(false);
    }
  }

  function selectMood(level: number): void {
    setMoodLevel(level);
    const next = { level, words: moodRef.current.words };
    moodRef.current = next;
    scheduleDraftSave();
  }

  return (
    <PatientPage navigation={false} className="patient-quick-text">
      <header className="patient-subpage-header">
        <button type="button" aria-label="Закрыть" onClick={() => void navigate({ to: "/diary" })}>
          <img src={closeIcon} alt="" />
        </button>
        <h1>Новая заметка</h1>
        <span className="patient-subpage-header__spacer" />
      </header>
      {!hydrated ? (
        <p className="patient-screen-loading" role="status">
          Открываем черновик…
        </p>
      ) : (
        <main className="patient-quick-text__content">
          <p className="patient-date-chip">
            <img src={timeIcon} alt="" /> Сегодня,{" "}
            {displayTime(clock.occurredAt, clock.timezone).split(", ").at(-1)}
          </p>
          <label className="patient-quick-text__field">
            <span className="sr-only">Текст записи</span>
            <textarea
              ref={textAreaRef}
              maxLength={20_000}
              value={text}
              placeholder="Что хочется отметить?"
              onChange={(event) => {
                const value = event.currentTarget.value;
                setText(value);
                textRef.current = value;
                scheduleDraftSave();
              }}
            />
            <small>{text.length} из 20 000</small>
          </label>
          <section className="patient-quick-text__mood" aria-labelledby="quick-text-mood-title">
            <h2 id="quick-text-mood-title">Настроение, по желанию</h2>
            <MoodScale context="quick" selected={moodLevel} onSelect={selectMood} />
          </section>
          {error && <Alert tone="error">{error}</Alert>}
          <p className="patient-save-status" role="status">
            {savedDraft ? "Черновик сохранён на устройстве" : "Сохраняем черновик…"}
          </p>
        </main>
      )}
      <footer className="patient-quick-text__actions">
        <Link to="/diary/new/voice" aria-label="Перейти к голосовой записи">
          <img src={microphoneIcon} alt="" />
        </Link>
        <Button
          className="patient-checkin-primary"
          disabled={!text.trim()}
          loading={saving}
          onClick={() => void save()}
        >
          Сохранить
        </Button>
      </footer>
    </PatientPage>
  );
}
