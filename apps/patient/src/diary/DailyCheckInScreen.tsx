// Kept as one flow because each step shares a draft revision, survey schedule and atomic finalize boundary.
import { Alert, Button } from "@compass/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { AssessmentForm } from "../assessments/AssessmentForm";
import type { AssessmentPersistedReceipt } from "../assessments/persisted-callback";
import type { SubmissionOutcome } from "../help/submission-outcome";
import { usePatientSyncState } from "../patient-sync-context";
import type { PatientSession } from "../session";
import skyMedallionNight from "./assets/sky-medallion-night.svg";
import { CheckInHeader } from "./CheckInHeader";
import { CheckInIntakeStep } from "./CheckInIntakeStep";
import { CheckInMoodStep } from "./CheckInMoodStep";
import { CheckInStoryStep } from "./CheckInStoryStep";
import { checkInEntryMode } from "./checkin-access";
import { type DiaryClock, diaryDateInTimezone, resolveDiaryClock } from "./clock";
import { MOOD_WORDS, type MoodWord } from "./MoodScale";
import { dailyCheckInMonthCount } from "./month-entry-count";
import { PatientPage } from "./PatientPage";
import { getDiaryStore, useDiaryOverviewQueries } from "./queries";
import { entriesNoun, monthInPrepositional } from "./russian-text";
import { resolveDiarySchedule } from "./survey-schedule";
import { activeSurveyScaleId, surveySubmittedNotice } from "./survey-step";
import type {
  DiaryDraftRecord,
  DiaryDraftStep,
  DiaryEntryRecord,
  DiarySurveyStatus,
} from "./types";
import { useDiaryLocal } from "./use-diary-local";

type StoryMode = "voice" | "text";
type CheckInForm = {
  moodLevel: number | null;
  moodWords: MoodWord[];
  text: string;
  otherMedicineText: string;
};
const EMPTY_FORM: CheckInForm = { moodLevel: null, moodWords: [], text: "", otherMedicineText: "" };

function validMood(value: number | undefined): number | null {
  return value !== undefined && Number.isInteger(value) && value >= 1 && value <= 5 ? value : null;
}

function ownerFor(session: PatientSession): string {
  return session.status === "online"
    ? session.user.id
    : session.status === "offline"
      ? session.ownerId
      : "";
}

export function DailyCheckInScreen({
  session,
  initialMood,
}: {
  session: PatientSession;
  initialMood?: number;
}) {
  const ownerId = ownerFor(session);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { diarySyncNow } = usePatientSyncState();
  const overview = useDiaryOverviewQueries(session, queryClient);
  const local = useDiaryLocal(session);
  const initialClock = resolveDiaryClock(overview.cache);
  const [flowClock, setFlowClock] = useState<DiaryClock | null>(null);
  const [capturedSchedule, setCapturedSchedule] = useState<
    typeof overview.schedule.data | null | undefined
  >(undefined);
  const [hydrated, setHydrated] = useState(false);
  const [draft, setDraft] = useState<DiaryDraftRecord | null>(null);
  const draftRef = useRef<DiaryDraftRecord | null>(null);
  const [draftId, setDraftId] = useState<string>(
    () => `daily-${initialClock.today}-${crypto.randomUUID()}`,
  );
  const [entryId, setEntryId] = useState<string>(() => crypto.randomUUID());
  const [operationId, setOperationId] = useState<string>(() => crypto.randomUUID());
  const [form, setForm] = useState<CheckInForm>(() => ({
    ...EMPTY_FORM,
    moodLevel: validMood(initialMood),
  }));
  const formRef = useRef(form);
  const [step, setStep] = useState<DiaryDraftStep>("mood");
  const stepRef = useRef<DiaryDraftStep>("mood");
  const [storyMode, setStoryMode] = useState<StoryMode>("voice");
  const [recording, setRecording] = useState(false);
  const [audioDurationMs, setAudioDurationMs] = useState<number | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [pendingMode, setPendingMode] = useState<StoryMode | null>(null);
  const [surveyUnavailable, setSurveyUnavailable] = useState(false);
  // Отправленная шкала остаётся на экране до «Вернуться к отметке» (P2-01).
  const [heldScaleId, setHeldScaleId] = useState<string | null>(null);
  const [lastSurveyOutcome, setLastSurveyOutcome] = useState<SubmissionOutcome | null>(null);
  const [restoredDraft, setRestoredDraft] = useState(false);
  const [completionEntry, setCompletionEntry] = useState<DiaryEntryRecord | null>(null);
  const [completionSettled, setCompletionSettled] = useState(false);
  const modeDialogRef = useRef<HTMLDialogElement>(null);
  const writeChain = useRef<Promise<void>>(Promise.resolve());
  const saveTimer = useRef<number | null>(null);

  const dailyAudioQuery = useQuery({
    queryKey: ["diary", ownerId, "audio", entryId],
    enabled: Boolean(ownerId && entryId),
    retry: false,
    networkMode: "always",
    queryFn: async () => (await getDiaryStore()).getAudioBlob(ownerId, entryId),
  });
  const audioBlob = dailyAudioQuery.data ?? null;
  // Отметка на сегодня уже сохранена — повторную не открываем (P2-21).
  const dailyEntryQuery = useQuery({
    queryKey: ["diary", ownerId, "daily-entry", flowClock?.today ?? ""],
    enabled: Boolean(ownerId && flowClock?.today),
    retry: false,
    networkMode: "always",
    queryFn: async () => (await getDiaryStore()).getDailyEntry(ownerId, flowClock?.today ?? ""),
  });

  useEffect(() => {
    if (flowClock) return;
    if (session.status === "online" && (overview.context.isPending || overview.schedule.isPending))
      return;
    setFlowClock(resolveDiaryClock(overview.cache));
    if (session.status === "online" && overview.schedule.isError) {
      setCapturedSchedule(overview.cache?.schedule_snapshot ?? null);
    } else if (session.status !== "online" || !overview.schedule.isPending) {
      setCapturedSchedule(overview.schedule.data ?? overview.cache?.schedule_snapshot ?? null);
    }
  }, [
    flowClock,
    overview.cache,
    overview.context.isPending,
    overview.schedule.data,
    overview.schedule.isError,
    overview.schedule.isPending,
    session.status,
  ]);

  useEffect(() => {
    if (hydrated || local.drafts.isPending || !flowClock) return;
    const restored = local.drafts.data?.find((item) => item.mode === "daily") ?? null;
    if (restored) {
      setRestoredDraft(true);
      draftRef.current = restored;
      setDraft(restored);
      setDraftId(restored.draft_id);
      setEntryId(restored.entry_id);
      setOperationId(restored.operation_id);
      const knownWords = new Set<string>(MOOD_WORDS);
      formRef.current = {
        moodLevel: restored.mood_level,
        moodWords: restored.mood_words.filter((word): word is MoodWord => knownWords.has(word)),
        text: restored.text,
        otherMedicineText: restored.other_medicine_text ?? "",
      };
      setForm(formRef.current);
      stepRef.current = restored.step;
      setStep(restored.step);
      setStoryMode(restored.text ? "text" : "voice");
      setFlowClock({
        ...resolveDiaryClock(overview.cache),
        occurredAt: restored.occurred_at,
        today: restored.daily_on ?? flowClock.today,
      });
      setCapturedSchedule(restored.schedule_snapshot ?? null);
    }
    setHydrated(true);
  }, [flowClock, hydrated, local.drafts.data, local.drafts.isPending, overview.cache]);

  useEffect(
    () => () => {
      if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    },
    [],
  );

  useEffect(() => {
    const dialog = modeDialogRef.current;
    if (!dialog) return;
    if (pendingMode && !dialog.open) dialog.showModal();
    if (!pendingMode && dialog.open) dialog.close();
  }, [pendingMode]);

  useEffect(() => {
    if (step !== "complete") return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setCompletionSettled(true);
      return;
    }
    setCompletionSettled(false);
    const timer = window.setTimeout(() => setCompletionSettled(true), 600);
    return () => window.clearTimeout(timer);
  }, [step]);

  const scheduleSnapshot =
    overview.schedule.data ?? draft?.schedule_snapshot ?? capturedSchedule ?? null;
  const cachedScaleIds = local.definitions.data?.map((definition) => definition.scale.id) ?? [];
  const resolvedSchedule = resolveDiarySchedule(
    scheduleSnapshot,
    flowClock?.today ?? null,
    cachedScaleIds,
    { latestRequestFailed: overview.schedule.isError },
  );
  const dueScaleIds = [...resolvedSchedule.dueScaleIds].sort((left, right) =>
    left.localeCompare(right),
  );
  const hasSurveyStep = dueScaleIds.length > 0;
  const stepCount = hasSurveyStep ? 5 : 4;
  const stepIndex: Record<DiaryDraftStep, number> = {
    mood: 1,
    story: 2,
    intake: 3,
    survey: 4,
    complete: stepCount,
  };
  const allDueFormsAvailable =
    dueScaleIds.length > 0 &&
    (session.status === "online" || dueScaleIds.every((id) => cachedScaleIds.includes(id)));
  const submittedIds = draft?.survey_submission_ids ?? [];
  const activeScaleId =
    allDueFormsAvailable && !surveyUnavailable
      ? activeSurveyScaleId({ dueScaleIds, submittedIds, heldScaleId })
      : undefined;
  const prescriptions =
    overview.prescriptions.data ?? overview.cache?.prescriptions_snapshot ?? null;

  function updateForm(patch: Partial<CheckInForm>): void {
    const next = { ...formRef.current, ...patch };
    formRef.current = next;
    setForm(next);
  }

  function queueDraftSave(targetStep: DiaryDraftStep = stepRef.current): Promise<void> {
    if (saveTimer.current !== null) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    writeChain.current = writeChain.current
      .catch(() => undefined)
      .then(async () => {
        const fields = formRef.current;
        if (!fields.moodLevel || !flowClock) return;
        setSaveError(null);
        const current = draftRef.current;
        const schedule =
          overview.schedule.data ?? current?.schedule_snapshot ?? capturedSchedule ?? null;
        const state = resolveDiarySchedule(schedule, flowClock.today, cachedScaleIds, {
          latestRequestFailed: overview.schedule.isError,
        });
        const surveyStatus: DiarySurveyStatus =
          current?.survey_status ??
          (state.status === "not_due" && !state.needsRefresh ? "not_due" : state.status);
        const saved = await (await getDiaryStore()).saveDraft({
          ownerId,
          draftId: current?.draft_id ?? draftId,
          expectedRevision: current?.revision ?? 0,
          entryId: current?.entry_id ?? entryId,
          operationId: current?.operation_id ?? operationId,
          mode: "daily",
          entryKind: "daily",
          occurredAt: current?.occurred_at ?? flowClock.occurredAt,
          dailyOn: current?.daily_on ?? flowClock.today,
          step: targetStep,
          moodLevel: fields.moodLevel,
          moodWords: fields.moodWords,
          text: fields.text,
          otherMedicineText: fields.otherMedicineText || null,
          scheduleSnapshot: schedule,
          scheduleNeedsRefresh: state.needsRefresh,
          surveyStatus,
          surveySubmissionIds: current?.survey_submission_ids ?? [],
        });
        if (saved.status !== "saved")
          throw new Error("Черновик изменился в другой вкладке. Перезагрузите отметку.");
        draftRef.current = saved.draft;
        setDraft(saved.draft);
        setDraftId(saved.draft.draft_id);
        setEntryId(saved.draft.entry_id);
        setOperationId(saved.draft.operation_id);
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "drafts"] }),
          queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "local-entries"] }),
        ]);
      })
      .catch((error: unknown) => {
        setSaveError(
          error instanceof Error ? error.message : "Не удалось сохранить черновик на устройстве.",
        );
        throw error;
      });
    return writeChain.current;
  }

  function saveDraftSoon(): void {
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void queueDraftSave().catch(() => undefined);
    }, 250);
  }

  function changeStep(nextStep: DiaryDraftStep): void {
    stepRef.current = nextStep;
    setStep(nextStep);
  }

  async function advanceFromMood(): Promise<void> {
    if (!formRef.current.moodLevel) return;
    setSaving(true);
    try {
      await queueDraftSave("story");
      changeStep("story");
    } catch {
      // The step remains editable; the failure is shown above the action.
    } finally {
      setSaving(false);
    }
  }

  async function advanceFromStory(): Promise<void> {
    if (recording) return;
    setSaving(true);
    try {
      await queueDraftSave(hasSurveyStep ? "intake" : "intake");
      changeStep("intake");
    } catch {
      // A saved draft is required before the next step can open.
    } finally {
      setSaving(false);
    }
  }

  async function advanceFromIntake(): Promise<void> {
    setSaving(true);
    try {
      await queueDraftSave(hasSurveyStep ? "survey" : "intake");
      if (hasSurveyStep) {
        changeStep("survey");
      } else {
        await finalize(
          resolvedSchedule.status === "not_due" && !resolvedSchedule.needsRefresh
            ? "not_due"
            : "unknown",
        );
      }
    } catch {
      // Keep the form open for a retry.
    } finally {
      setSaving(false);
    }
  }

  async function persistAssessmentReceipt(receipt: AssessmentPersistedReceipt): Promise<void> {
    const current = draftRef.current;
    if (!current || current.survey_submission_ids.includes(receipt.submission_id)) return;
    if (current.mood_level === null) throw new Error("Сначала выберите настроение для дневника.");
    setHeldScaleId(receipt.scale_id);
    const ids = [...current.survey_submission_ids, receipt.submission_id];
    const saved = await (await getDiaryStore()).saveDraft({
      ownerId,
      draftId: current.draft_id,
      expectedRevision: current.revision,
      entryId: current.entry_id,
      operationId: current.operation_id,
      mode: "daily",
      entryKind: "daily",
      occurredAt: current.occurred_at,
      dailyOn: current.daily_on ?? current.occurred_at.slice(0, 10),
      step: "survey",
      moodLevel: current.mood_level,
      moodWords: current.mood_words.filter((word): word is MoodWord =>
        MOOD_WORDS.includes(word as MoodWord),
      ),
      text: current.text,
      otherMedicineText: current.other_medicine_text,
      scheduleSnapshot: current.schedule_snapshot,
      scheduleNeedsRefresh: current.schedule_needs_refresh,
      surveyStatus: "pending",
      surveySubmissionIds: ids,
    });
    if (saved.status !== "saved")
      throw new Error("Ответ сохранён, но отметку не удалось обновить. Повторите переход.");
    draftRef.current = saved.draft;
    setDraft(saved.draft);
    await queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "drafts"] });
  }

  async function finalize(status: DiarySurveyStatus): Promise<void> {
    const current = draftRef.current;
    const moodLevel = formRef.current.moodLevel;
    if (!current || !flowClock || moodLevel === null) {
      setSaveError("Сначала выберите настроение, чтобы сохранить отметку.");
      return;
    }
    setSaveError(null);
    setSaving(true);
    try {
      await queueDraftSave();
      const latest = draftRef.current ?? current;
      let finalizedEntry: DiaryEntryRecord | null = null;
      const base = {
        entry_id: latest.entry_id,
        operation_id: latest.operation_id,
        kind: "daily" as const,
        daily_on: latest.daily_on ?? flowClock.today,
        mood_level: moodLevel,
        mood_words: formRef.current.moodWords,
        occurred_at: latest.occurred_at,
        other_medicine_text: formRef.current.otherMedicineText || null,
      };
      const blob = storyMode === "voice" ? audioBlob : null;
      if (blob && (!audioDurationMs || audioDurationMs < 1)) {
        throw new Error("Загружаем длительность сохранённой аудиозаписи. Попробуйте ещё раз.");
      }
      if (blob) {
        const payload = { ...base, duration_ms: audioDurationMs ?? 0 };
        const result = await (await getDiaryStore()).finalizeDailyCheckIn({
          ownerId,
          draftId: latest.draft_id,
          expectedDraftRevision: latest.revision,
          payload,
          voiceBlob: blob,
          surveyStatus: status,
          surveySubmissionIds: latest.survey_submission_ids,
          assessmentDrafts: [],
        });
        if (
          result.status === "daily-exists" ||
          result.status === "conflict" ||
          result.status === "draft-conflict"
        ) {
          throw new Error("На эту дату уже есть другая отметка. Черновик сохранён.");
        }
        finalizedEntry = result.entry;
      } else {
        const payload = { ...base, text: formRef.current.text || null };
        const result = await (await getDiaryStore()).finalizeDailyCheckIn({
          ownerId,
          draftId: latest.draft_id,
          expectedDraftRevision: latest.revision,
          payload,
          surveyStatus: status,
          surveySubmissionIds: latest.survey_submission_ids,
          assessmentDrafts: [],
        });
        if (
          result.status === "daily-exists" ||
          result.status === "conflict" ||
          result.status === "draft-conflict"
        ) {
          throw new Error("На эту дату уже есть другая отметка. Черновик сохранён.");
        }
        finalizedEntry = result.entry;
      }
      setCompletionEntry(finalizedEntry);
      setCompletionSettled(false);
      stepRef.current = "complete";
      setStep("complete");
      await local.refresh();
      void diarySyncNow();
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : "Не удалось сохранить отметку на устройстве. Попробуйте ещё раз.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleVoiceComplete(blob: Blob, durationMs: number): Promise<void> {
    await (await getDiaryStore()).saveAudioBlob(ownerId, entryId, blob);
    queryClient.setQueryData(["diary", ownerId, "audio", entryId], blob);
    setAudioDurationMs(durationMs);
    await queueDraftSave("story");
  }

  function changeStoryMode(next: StoryMode): void {
    if (next === storyMode) return;
    const hasText = formRef.current.text.trim().length > 0;
    if (hasText || audioBlob) {
      setPendingMode(next);
      return;
    }
    setStoryMode(next);
  }

  async function confirmStoryMode(): Promise<void> {
    const next = pendingMode;
    if (!next) return;
    try {
      if (audioBlob && next === "text") {
        const updated = await (await getDiaryStore()).clearDraftAudio({
          ownerId,
          draftId: draftRef.current?.draft_id ?? draftId,
          expectedRevision: draftRef.current?.revision ?? 0,
        });
        draftRef.current = updated;
        setDraft(updated);
        queryClient.setQueryData(["diary", ownerId, "audio", entryId], null);
      }
      if (next === "voice") updateForm({ text: "" });
      setStoryMode(next);
      setPendingMode(null);
      await queueDraftSave("story");
    } catch {
      setSaveError("Не удалось изменить способ рассказа. Сохранённые данные не удалены.");
    }
  }

  if (!flowClock || !hydrated || capturedSchedule === undefined || dailyEntryQuery.isPending) {
    return (
      <PatientPage navigation={false}>
        <p className="patient-screen-loading" role="status">
          Подготавливаем ежедневную отметку…
        </p>
      </PatientPage>
    );
  }

  const savedToday =
    step !== "complete" &&
    Boolean(
      dailyEntryQuery.data ||
        local.entries.data?.some(
          (entry) => entry.kind === "daily" && entry.daily_on === flowClock.today && !entry.hidden,
        ),
    );
  if (
    checkInEntryMode({ hasDailyEntryToday: savedToday, hasDailyDraft: restoredDraft }) ===
    "already-saved"
  ) {
    return (
      <PatientPage navigation={false} className="patient-checkin">
        <CheckInHeader
          step="complete"
          index={stepCount}
          count={stepCount}
          onClose={() => void navigate({ to: "/" })}
        />
        <main className="patient-checkin-complete">
          <h1 className="type-patient-question">Отметка на сегодня уже сохранена</h1>
          <p>На эту дату уже есть отметка. Что-то изменилось — сделайте быструю запись.</p>
          <div className="patient-checkin-complete__actions">
            <Button onClick={() => void navigate({ to: "/" })}>На главную</Button>
          </div>
        </main>
      </PatientPage>
    );
  }

  const surveySubmitted = allDueFormsAvailable && submittedIds.length >= dueScaleIds.length;
  const online = session.status === "online";
  const submittedNotice = surveySubmittedNotice(lastSurveyOutcome, online);
  const canSaveWithoutSurvey = hasSurveyStep && (!allDueFormsAvailable || surveyUnavailable);
  const dailySurveyStatus: DiarySurveyStatus =
    scheduleSnapshot && resolvedSchedule.status === "not_due" && !resolvedSchedule.needsRefresh
      ? "not_due"
      : surveySubmitted
        ? "queued"
        : hasSurveyStep
          ? "unknown"
          : "unknown";

  const knownMonthCount =
    overview.context.data?.month_entry_count ?? overview.cache?.context?.month_entry_count;
  const currentMonth = flowClock.today.slice(0, 7);
  const monthEntries = local.entries.data ?? [];
  const visibleMonthEntries = monthEntries.filter(
    (entry) =>
      !entry.hidden &&
      diaryDateInTimezone(entry.occurred_at, flowClock.timezone).startsWith(
        flowClock.today.slice(0, 7),
      ),
  );
  const completionCount = dailyCheckInMonthCount({
    entries: monthEntries,
    completionEntry,
    today: flowClock.today,
    timezone: flowClock.timezone,
    serverCount: knownMonthCount,
  });
  const monthLabel = monthInPrepositional(currentMonth);
  const completionBeads = [
    ...visibleMonthEntries.filter((entry) => entry.entry_id !== completionEntry?.entry_id),
    ...(completionEntry ? [completionEntry] : []),
  ].sort((left, right) => right.occurred_at.localeCompare(left.occurred_at));
  const visibleCompletionBeads = (
    completionSettled || !completionEntry
      ? completionBeads
      : completionBeads.filter((entry) => entry.entry_id !== completionEntry.entry_id)
  )
    .slice(0, 12)
    .reverse();
  const shownCompletionCount =
    completionSettled || !completionEntry ? completionCount : Math.max(0, completionCount - 1);

  return (
    <PatientPage navigation={false} className="patient-checkin">
      <CheckInHeader
        step={step}
        index={stepIndex[step]}
        count={stepCount}
        disabled={recording || saving}
        onBack={
          step !== "mood" && step !== "complete"
            ? () => changeStep(previousStep(step, hasSurveyStep))
            : undefined
        }
        onClose={() => void navigate({ to: "/" })}
      />
      {saveError && (
        <div className="patient-checkin-error">
          <Alert tone="error">{saveError}</Alert>
        </div>
      )}
      {step === "mood" && (
        <CheckInMoodStep
          level={form.moodLevel}
          onSelect={(moodLevel) => {
            updateForm({ moodLevel });
            saveDraftSoon();
          }}
          onNext={() => void advanceFromMood()}
        />
      )}
      {step === "story" && (
        <CheckInStoryStep
          mode={storyMode}
          text={form.text}
          audioBlob={audioBlob}
          audioDurationMs={audioDurationMs}
          recording={recording}
          onModeChange={changeStoryMode}
          onTextChange={(text) => {
            updateForm({ text });
            saveDraftSoon();
          }}
          onVoiceComplete={handleVoiceComplete}
          onVoiceSaved={(blob, duration) => {
            queryClient.setQueryData(["diary", ownerId, "audio", entryId], blob);
            setAudioDurationMs(duration);
          }}
          onDurationKnown={setAudioDurationMs}
          onRecordingChange={setRecording}
          onNext={() => void advanceFromStory()}
        />
      )}
      {step === "intake" && (
        <>
          <CheckInIntakeStep
            ownerId={ownerId}
            prescriptions={prescriptions}
            cache={overview.cache}
            scheduledOn={flowClock.today}
            online={session.status === "online"}
            otherMedicineText={form.otherMedicineText}
            onOtherMedicineText={(otherMedicineText) => {
              updateForm({ otherMedicineText });
              saveDraftSoon();
            }}
          />
          <div className="patient-checkin-actions">
            <Button
              className="patient-checkin-primary"
              loading={saving}
              onClick={() => void advanceFromIntake()}
            >
              Дальше
            </Button>
          </div>
        </>
      )}
      {step === "survey" && (
        <div className="patient-checkin-survey">
          {resolvedSchedule.needsRefresh && (
            <p className="patient-schedule-note">
              План опросов сохранён. После подключения расписание обновится.
            </p>
          )}
          {activeScaleId ? (
            <>
              {dueScaleIds.length > 1 && (
                <p className="patient-survey-step-label">
                  {scheduleSnapshot?.items.find((item) => item.scale_id === activeScaleId)
                    ?.scale_title ?? activeScaleId}{" "}
                  · {Math.min(dueScaleIds.indexOf(activeScaleId) + 1, dueScaleIds.length)} из{" "}
                  {dueScaleIds.length}
                </p>
              )}
              <AssessmentForm
                key={`${ownerId}:${activeScaleId}`}
                ownerId={ownerId}
                scaleId={activeScaleId}
                session={session}
                embedded
                onPersisted={persistAssessmentReceipt}
                onFinished={(outcome) => {
                  setLastSurveyOutcome(outcome);
                  setHeldScaleId(null);
                }}
                onUnavailable={() => setSurveyUnavailable(true)}
              />
            </>
          ) : canSaveWithoutSurvey ? (
            <section className="patient-survey-unavailable">
              <h1 className="type-patient-question">Опрос станет доступен после подключения</h1>
              <p>
                Положенная шкала не сохранена на этом устройстве. Отметка настроения и дневник
                сохранятся отдельно.
              </p>
              {dueScaleIds.map((id) => (
                <p key={id}>
                  {scheduleSnapshot?.items.find((item) => item.scale_id === id)?.scale_title ?? id}
                </p>
              ))}
            </section>
          ) : surveySubmitted ? (
            <section className="patient-survey-unavailable">
              <h1 className="type-patient-question">{submittedNotice.title}</h1>
              <p>{submittedNotice.text}</p>
            </section>
          ) : (
            <section className="patient-survey-unavailable">
              <h1 className="type-patient-question">Расписание опросов неизвестно</h1>
              <p>Дневник сохранится. После подключения проверим, положен ли сегодня опрос.</p>
            </section>
          )}
          <div className="patient-checkin-actions">
            {!activeScaleId &&
              (surveySubmitted || canSaveWithoutSurvey || !allDueFormsAvailable) && (
                <Button
                  className="patient-checkin-primary"
                  loading={saving}
                  onClick={() => void finalize(dailySurveyStatus)}
                >
                  {canSaveWithoutSurvey ? "Сохранить отметку без опроса" : "Сохранить отметку"}
                </Button>
              )}
          </div>
        </div>
      )}
      {step === "complete" && (
        <main className="patient-checkin-complete">
          <img
            className="patient-completion-sky"
            src={skyMedallionNight}
            alt=""
            aria-hidden="true"
          />
          <h1 className="type-patient-question">Спасибо, отметка сохранена</h1>
          <p>
            {online
              ? "Ваш врач увидит её сегодня."
              : "Сохранено на устройстве. Запись отправится, когда появится сеть."}
          </p>
          <section className="patient-completion-month" aria-label="Отметки за месяц">
            <p>
              <strong>{shownCompletionCount}</strong> {entriesNoun(shownCompletionCount)}{" "}
              {monthLabel}
            </p>
            <span className="patient-bead-row" aria-hidden="true">
              {visibleCompletionBeads.map((entry) => (
                <i
                  key={entry.entry_id}
                  data-level={entry.mood_level ?? 0}
                  data-new={
                    completionSettled && entry.entry_id === completionEntry?.entry_id
                      ? "true"
                      : undefined
                  }
                />
              ))}
            </span>
            <p>Каждая запись помогает врачу видеть, как вы себя чувствуете между визитами.</p>
          </section>
          {dailySurveyStatus === "unknown" && (
            <p className="patient-schedule-note">
              Опрос станет доступен после подключения. Дневник сохранён отдельно.
            </p>
          )}
          {dailySurveyStatus === "queued" && (
            <p>
              {online
                ? "Ответы опроса тоже отправлены."
                : "Ответы опроса тоже сохранены на устройстве."}
            </p>
          )}
          <p className="patient-checkin-next">Следующая отметка завтра в 21:00</p>
          <div className="patient-checkin-complete__actions">
            <Button onClick={() => void navigate({ to: "/" })}>Готово</Button>
          </div>
        </main>
      )}
      {step !== "mood" &&
        step !== "story" &&
        step !== "intake" &&
        step !== "survey" &&
        step !== "complete" && (
          <p className="patient-screen-loading" role="status">
            Загружаем шаг…
          </p>
        )}
      {pendingMode && (
        <dialog
          ref={modeDialogRef}
          className="patient-confirm-dialog"
          role="alertdialog"
          aria-labelledby="story-mode-confirm-title"
          onCancel={(event) => {
            event.preventDefault();
            setPendingMode(null);
          }}
          onClose={() => setPendingMode(null)}
        >
          <h2 id="story-mode-confirm-title" className="type-patient-title">
            Переключить способ рассказа?
          </h2>
          <p className="patient-body-small">
            Текущий рассказ будет удалён. Можно оставить прежний способ.
          </p>
          <div className="patient-confirm-dialog__actions">
            <Button variant="secondary" onClick={() => setPendingMode(null)}>
              Оставить
            </Button>
            <Button onClick={() => void confirmStoryMode()}>
              Удалить запись и перейти к {pendingMode === "text" ? "тексту" : "голосу"}
            </Button>
          </div>
        </dialog>
      )}
    </PatientPage>
  );
}

function previousStep(step: DiaryDraftStep, survey: boolean): DiaryDraftStep {
  if (step === "story") return "mood";
  if (step === "intake") return "story";
  if (step === "survey") return "intake";
  if (step === "complete") return survey ? "survey" : "intake";
  return "mood";
}
