import type { Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import editIcon from "@compass/ui/assets/ikonka-karandash.svg";
import trashIcon from "@compass/ui/assets/ikonka-korzina.svg";
import microphoneIcon from "@compass/ui/assets/ikonka-mikrofon.svg";
import backIcon from "@compass/ui/assets/ikonka-vlevo.svg";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePatientSyncState } from "../patient-sync-context";
import type { PatientSession } from "../session";
import { fetchDiaryEntry, retryDiaryTranscription } from "./api";
import {
  diaryEntryStatusLabel,
  shouldFetchServerEntry,
  transcriptRefetchInterval,
} from "./entry-status";
import { MOOD_WORDS, MoodScale, type MoodWord, moodLabel } from "./MoodScale";
import { mergeDiaryPage } from "./merge";
import { PatientPage } from "./PatientPage";
import { getDiaryStore, useDiaryOverviewQueries } from "./queries";
import { syncAfterLocalWrite } from "./sync-after-write";
import type { DiaryEntryRecord } from "./types";
import { useDiaryLocal } from "./use-diary-local";
import { VoicePlayback } from "./VoicePlayback";
import { VoiceSavedPresentation } from "./VoiceSavedPresentation";

function ownerFor(session: PatientSession): string {
  return session.status === "online"
    ? session.user.id
    : session.status === "offline"
      ? session.ownerId
      : "";
}

function recordText(entry: DiaryEntryRecord): string {
  return entry.text ?? entry.audio?.transcript ?? "";
}

function timestampLabel(entry: DiaryEntryRecord, timezone: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    weekday: "long",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
  }).format(new Date(entry.occurred_at));
}

/** Read, edit and soft-hide one owner-visible diary record. */
export function DiaryEntryScreen({
  session,
  entryId,
}: {
  session: PatientSession;
  entryId: string;
}) {
  const ownerId = ownerFor(session);
  const online = session.status === "online";
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { diarySyncNow } = usePatientSyncState();
  const savedVoicePresentationKey = [
    "diary",
    ownerFor(session),
    "saved-voice-presentation",
    entryId,
  ];
  const [showSavedVoicePresentation, setShowSavedVoicePresentation] = useState(
    () => queryClient.getQueryData<boolean>(savedVoicePresentationKey) === true,
  );
  const overview = useDiaryOverviewQueries(session, queryClient);
  const local = useDiaryLocal(session);
  const timezone = overview.cache?.context?.timezone ?? "Europe/Moscow";
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [editMood, setEditMood] = useState<number | null>(null);
  const [editWords, setEditWords] = useState<MoodWord[]>([]);
  const [saving, setSaving] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmHide, setConfirmHide] = useState(false);
  const [conflictChoice, setConflictChoice] = useState<"local" | "remote">("local");
  const hideDialogRef = useRef<HTMLDialogElement>(null);
  const localEntry = local.entries.data?.find((entry) => entry.entry_id === entryId) ?? null;
  // A record whose create command is still queued gives 404 on the server: wait for the receipt.
  const serverKnown = !local.entries.isPending && shouldFetchServerEntry(localEntry, online);
  const entryQuery = useQuery({
    queryKey: ["diary", ownerId, "entry", entryId],
    enabled: serverKnown && Boolean(entryId),
    staleTime: 30_000,
    retry: false,
    refetchInterval: (query) =>
      transcriptRefetchInterval(
        query.state.data
          ? (mergeDiaryPage(ownerId, localEntry ? [localEntry] : [], [query.state.data])[0] ?? null)
          : localEntry,
      ),
    queryFn: () => fetchDiaryEntry(entryId),
  });
  const audioQuery = useQuery({
    queryKey: ["diary", ownerId, "audio", entryId],
    enabled: Boolean(ownerId && entryId),
    retry: false,
    networkMode: "always",
    queryFn: async () => (await getDiaryStore()).getAudioBlob(ownerId, entryId),
  });
  const audioUrl = useMemo(() => {
    if (audioQuery.data) return URL.createObjectURL(audioQuery.data);
    if (entryQuery.data?.audio) return `/api/v1/diary/entries/${encodeURIComponent(entryId)}/audio`;
    return null;
  }, [audioQuery.data, entryId, entryQuery.data?.audio]);
  useEffect(
    () => () => {
      if (audioUrl?.startsWith("blob:")) URL.revokeObjectURL(audioUrl);
    },
    [audioUrl],
  );
  useEffect(() => {
    const dialog = hideDialogRef.current;
    if (!dialog) return;
    if (confirmHide && !dialog.open) dialog.showModal();
    if (!confirmHide && dialog.open) dialog.close();
  }, [confirmHide]);

  const pendingHidden = (local.outbox.data ?? []).some(
    (item) => item.action === "hide" && item.entry_id === entryId,
  );
  const entry = pendingHidden
    ? null
    : (mergeDiaryPage(
        ownerId,
        localEntry ? [localEntry] : [],
        entryQuery.data ? [entryQuery.data] : [],
      )[0] ?? localEntry);
  const conflict = entry?.remote_snapshot ?? null;
  const busy =
    local.entries.isPending ||
    local.outbox.isPending ||
    (serverKnown && entryQuery.isPending && !localEntry) ||
    (showSavedVoicePresentation && audioQuery.isPending);
  function sendOutbox(): void {
    void syncAfterLocalWrite({ syncNow: diarySyncNow, refreshLocal: local.refresh });
  }

  async function saveEdit(): Promise<void> {
    if (!entry) return;
    setSaving(true);
    setError(null);
    try {
      const words = [...editWords];
      const payload: Schemas["DiaryEntryPatchIn"] = {
        operation_id: crypto.randomUUID(),
        expected_revision: entry.revision,
        ...(editMood !== entry.mood_level ? { mood_level: editMood } : {}),
        ...(JSON.stringify(words) !== JSON.stringify(entry.mood_words)
          ? { mood_words: words }
          : {}),
        ...(editText.trim() !== recordText(entry) ? { text: editText.trim() || null } : {}),
      };
      if (Object.keys(payload).length === 2) {
        setEditing(false);
        return;
      }
      const result = await (await getDiaryStore()).enqueueUpdate({ ownerId, entryId, payload });
      if (result.status === "conflict")
        throw new Error("Запись уже изменилась. Обновите экран и сравните версии.");
      await Promise.all([
        local.refresh(),
        queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "entry", entryId] }),
        queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "server-entries"] }),
      ]);
      setEditing(false);
      sendOutbox();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Не удалось сохранить изменения на устройстве.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function hideEntry(): Promise<void> {
    if (!entry) return;
    setError(null);
    try {
      const result = await (await getDiaryStore()).enqueueHide({
        ownerId,
        entryId,
        payload: { operation_id: crypto.randomUUID(), expected_revision: entry.revision },
      });
      if (result.status === "conflict")
        throw new Error("Запись изменилась. Обновите страницу и попробуйте ещё раз.");
      await local.refresh();
      sendOutbox();
      await navigate({ to: "/diary", replace: true });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось скрыть запись.");
      setConfirmHide(false);
    }
  }

  async function retryTranscription(): Promise<void> {
    setRetrying(true);
    setError(null);
    try {
      await retryDiaryTranscription(entryId, crypto.randomUUID());
      await queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "entry", entryId] });
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Не удалось поставить расшифровку в очередь.",
      );
    } finally {
      setRetrying(false);
    }
  }

  async function saveVoicePresentationMood(moodLevel: number | null): Promise<void> {
    if (entry?.kind !== "voice") return;
    if (moodLevel !== entry.mood_level) {
      const result = await (await getDiaryStore()).enqueueUpdate({
        ownerId,
        entryId,
        payload: {
          operation_id: crypto.randomUUID(),
          expected_revision: entry.revision,
          mood_level: moodLevel,
          mood_words: [],
        },
      });
      if (result.status === "conflict")
        throw new Error("Запись изменилась. Настроение не сохранено.");
      await local.refresh();
      sendOutbox();
    }
    queryClient.removeQueries({ queryKey: savedVoicePresentationKey, exact: true });
    setShowSavedVoicePresentation(false);
  }

  function closeSavedVoicePresentation(): void {
    queryClient.removeQueries({ queryKey: savedVoicePresentationKey, exact: true });
    setShowSavedVoicePresentation(false);
    void navigate({ to: "/diary" });
  }

  function openEdit(): void {
    if (!entry) return;
    setEditText(recordText(entry));
    setEditMood(entry.mood_level);
    setEditWords(
      entry.mood_words.filter((word): word is MoodWord => MOOD_WORDS.includes(word as MoodWord)),
    );
    setEditing(true);
  }

  if (busy)
    return (
      <PatientPage navigation={false}>
        <p className="patient-screen-loading" role="status">
          Открываем запись…
        </p>
      </PatientPage>
    );
  if (!entry)
    return (
      <PatientPage navigation={false}>
        <main className="patient-empty-card">
          <h1>Запись недоступна</h1>
          <p>
            {entryQuery.isError
              ? "Не удалось загрузить эту запись. Проверьте подключение или вернитесь в дневник."
              : "Этой записи нет среди сохранённых данных."}
          </p>
          <Button onClick={() => void navigate({ to: "/diary" })}>Дневник</Button>
        </main>
      </PatientPage>
    );

  if (showSavedVoicePresentation && entry.kind === "voice") {
    const voiceCommand = local.outbox.data?.find(
      (command) => command.action === "voice" && command.entry_id === entryId,
    );
    const durationMs =
      entry.audio?.duration_ms ??
      (voiceCommand?.action === "voice" ? voiceCommand.payload.duration_ms : 1);
    return (
      <VoiceSavedPresentation
        entry={entry}
        online={online}
        audioDurationMs={durationMs}
        audioUrl={audioUrl}
        onClose={closeSavedVoicePresentation}
        onSave={saveVoicePresentationMood}
      />
    );
  }

  return (
    <PatientPage navigation={false} className="patient-entry-detail">
      <header className="patient-subpage-header patient-subpage-header--back">
        <button
          type="button"
          aria-label="Назад в дневник"
          onClick={() => void navigate({ to: "/diary" })}
        >
          <img src={backIcon} alt="" />
        </button>
        <button
          type="button"
          className="patient-subpage-header__text"
          onClick={() => void navigate({ to: "/diary" })}
        >
          Дневник
        </button>
        <span className="patient-subpage-header__spacer" />
      </header>
      <main className="patient-entry-detail__content">
        <h1 className="type-patient-question">{timestampLabel(entry, timezone)}</h1>
        {entryQuery.isError && localEntry && (
          <p className="patient-data-note" role="status">
            Не удалось обновить запись. Показана сохранённая версия.
          </p>
        )}
        <div className="patient-entry-tags">
          {entry.mood_level !== null && (
            <span>
              <i data-mood={entry.mood_level} />
              {moodLabel(entry.mood_level)}
            </span>
          )}
          {entry.kind === "voice" && (
            <span>
              <img src={microphoneIcon} alt="" />
              Голосовая,{" "}
              {entry.audio
                ? `${Math.floor(entry.audio.duration_ms / 60_000)}:${String(Math.floor((entry.audio.duration_ms % 60_000) / 1_000)).padStart(2, "0")}`
                : "запись"}
            </span>
          )}
        </div>
        {entry.kind === "voice" && audioUrl && (
          <VoicePlayback src={audioUrl} durationMs={entry.audio?.duration_ms ?? 1} />
        )}
        {entry.kind === "voice" && !audioUrl && (
          <p className="patient-data-note">Аудиозапись доступна после подключения.</p>
        )}
        {diaryEntryStatusLabel(entry, online) && (
          <p className="patient-diary-status" role="status">
            {diaryEntryStatusLabel(entry, online)}
          </p>
        )}
        {entry.audio?.status === "failed" && session.status === "online" && (
          <Button variant="secondary" loading={retrying} onClick={() => void retryTranscription()}>
            Повторить расшифровку
          </Button>
        )}
        {conflict && (
          <section className="patient-entry-conflict" aria-label="Сравнение версий записи">
            <h2>Версии записи</h2>
            <div className="patient-conflict-choices">
              <button
                type="button"
                aria-pressed={conflictChoice === "local"}
                onClick={() => setConflictChoice("local")}
              >
                Моя версия
              </button>
              <button
                type="button"
                aria-pressed={conflictChoice === "remote"}
                onClick={() => setConflictChoice("remote")}
              >
                На сервере
              </button>
            </div>
            <p>
              <strong>Настроение:</strong>{" "}
              {moodLabel(conflictChoice === "remote" ? conflict.mood_level : entry.mood_level) ??
                "Без отметки настроения"}
            </p>
            <p>
              {conflictChoice === "remote"
                ? (conflict.text ?? conflict.audio?.transcript ?? "В серверной версии нет текста.")
                : recordText(entry) || "В вашей версии нет текста."}
            </p>
            {(conflictChoice === "remote" ? conflict.mood_words : entry.mood_words).length > 0 && (
              <div className="patient-word-tags">
                {(conflictChoice === "remote" ? conflict.mood_words : entry.mood_words).map(
                  (word) => (
                    <span className="patient-word-tag" key={word}>
                      {word}
                    </span>
                  ),
                )}
              </div>
            )}
            <p>Обе версии сохранены. Изменения можно перенести вручную.</p>
          </section>
        )}
        {!editing ? (
          <>
            {entry.audio?.transcript && (
              <section className="patient-entry-text">
                <h2>Расшифровка</h2>
                <p>{entry.audio.transcript}</p>
              </section>
            )}
            {entry.text && (entry.kind !== "voice" || entry.text !== entry.audio?.transcript) && (
              <section className="patient-entry-text">
                <h2>{entry.kind === "voice" ? "Текст записи" : "Текст"}</h2>
                <p>{entry.text}</p>
              </section>
            )}
            {entry.other_medicine_text && (
              <section className="patient-entry-text">
                <h2>Другое лекарство</h2>
                <p>{entry.other_medicine_text}</p>
              </section>
            )}
            {entry.mood_words.length > 0 && (
              <section className="patient-entry-text">
                <h2>Слова</h2>
                <div className="patient-word-tags">
                  {entry.mood_words.map((word) => (
                    <span className="patient-word-tag" key={word}>
                      {word}
                    </span>
                  ))}
                </div>
              </section>
            )}
          </>
        ) : (
          <section className="patient-entry-edit" aria-label="Изменить запись">
            <label>
              <span>{entry.kind === "voice" ? "Исправленный текст записи" : "Текст записи"}</span>
              <textarea
                maxLength={20_000}
                rows={6}
                value={editText}
                onChange={(event) => setEditText(event.currentTarget.value)}
              />
            </label>
            <p className="patient-entry-edit__hint">
              Исходная расшифровка голосовой записи остаётся доступна выше.
            </p>
            <div className="patient-entry-edit__mood">
              <h2>Настроение, по желанию</h2>
              <MoodScale context="quick" selected={editMood} onSelect={setEditMood} />
              {editMood !== null && (
                <button
                  type="button"
                  className="patient-entry-edit__clear-mood"
                  onClick={() => {
                    setEditMood(null);
                    setEditWords([]);
                  }}
                >
                  Убрать отметку настроения
                </button>
              )}
            </div>
            {editMood !== null && (
              <fieldset className="patient-word-tags">
                <legend className="sr-only">Слова о самочувствии</legend>
                {MOOD_WORDS.map((word) => (
                  <button
                    key={word}
                    type="button"
                    className="patient-word-tag"
                    aria-pressed={editWords.includes(word)}
                    data-selected={editWords.includes(word) || undefined}
                    onClick={() =>
                      setEditWords((current) =>
                        current.includes(word)
                          ? current.filter((item) => item !== word)
                          : [...current, word],
                      )
                    }
                  >
                    {word}
                  </button>
                ))}
              </fieldset>
            )}
          </section>
        )}
        {error && <Alert tone="error">{error}</Alert>}
      </main>
      <footer className="patient-entry-detail__actions">
        {editing ? (
          <>
            <Button variant="secondary" onClick={() => setEditing(false)}>
              Отмена
            </Button>
            <Button loading={saving} onClick={() => void saveEdit()}>
              Сохранить
            </Button>
          </>
        ) : (
          <>
            <Button variant="secondary" disabled={Boolean(conflict)} onClick={openEdit}>
              <img src={editIcon} alt="" />
              Изменить
            </Button>
            <Button variant="link" onClick={() => setConfirmHide(true)}>
              <img src={trashIcon} alt="" />
              Удалить
            </Button>
          </>
        )}
      </footer>
      <dialog
        ref={hideDialogRef}
        className="patient-confirm-dialog"
        role="alertdialog"
        aria-labelledby="entry-hide-title"
        onCancel={(event) => {
          event.preventDefault();
          setConfirmHide(false);
        }}
        onClose={() => setConfirmHide(false)}
      >
        <h2 id="entry-hide-title" className="type-patient-title">
          Удалить запись?
        </h2>
        <p className="patient-body-small">Она пропадёт из дневника.</p>
        {entry.sync_state === "pending" && !online && (
          <p className="patient-offline-note">Скроется у врача, когда появится сеть.</p>
        )}
        <div className="patient-confirm-dialog__actions">
          <Button variant="secondary" onClick={() => setConfirmHide(false)}>
            Оставить
          </Button>
          <Button onClick={() => void hideEntry()}>Удалить</Button>
        </div>
      </dialog>
    </PatientPage>
  );
}
