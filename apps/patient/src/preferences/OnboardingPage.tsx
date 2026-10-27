import { Alert, Button } from "@compass/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { patientPreferencesQueryKey, savePatientPreferences } from "./api";
import type { AccessiblePatientSession } from "./hooks";
import {
  patientOwnerId,
  useApplyPatientPreferences,
  usePatientPreferences,
  usePatientPrescriptions,
} from "./hooks";
import { OnboardingDone } from "./OnboardingDone";
import { OnboardingFrame, OnboardingLoading } from "./OnboardingFrame";
import {
  changedPreferences,
  defaultDisplayName,
  type EditablePreferences,
  getOnboardingSteps,
  type OnboardingStepId,
  resolveOnboardingStep,
  skipOnboardingStep,
} from "./onboarding-flow";
import { PillAppearanceEditor } from "./PillAppearanceEditor";
import { PreferenceField } from "./PreferenceFields";
import { type PillDraft, pillDraftFor, pillDraftReady } from "./pill-appearance";
import { usePillAppearanceSaver } from "./pill-appearance-saver";

const fields: Partial<Record<OnboardingStepId, keyof EditablePreferences>> = {
  name: "display_name",
  reminder: "reminder_time",
  mood: "mood_palette",
  story: "story_mode",
  text: "text_size",
};

const screenText: Partial<
  Record<OnboardingStepId, { eyebrow: string; title: string; description: string }>
> = {
  name: {
    eyebrow: "Знакомство",
    title: "Как к вам обращаться?",
    description: "Так мы будем здороваться. В карте у врача останутся ваши ФИО.",
  },
  reminder: {
    eyebrow: "Напоминание",
    title: "Когда удобно отмечать, как прошёл день?",
    description: "Пришлём одно тихое напоминание. Время можно поменять в профиле.",
  },
  mood: {
    eyebrow: "Шкала настроения",
    title: "Что для вас значат цвета?",
    description: "Ими вы будете отмечать настроение.",
  },
  story: {
    eyebrow: "Рассказ о дне",
    title: "Как удобнее рассказать о дне?",
    description: "Способ можно изменить в любой момент.",
  },
  pills: {
    eyebrow: "Лекарства",
    title: "Как выглядят ваши таблетки?",
    description: "Вид взяли из инструкции. Если ваша таблетка другая, поправьте.",
  },
  text: {
    eyebrow: "Текст",
    title: "Какой размер текста удобнее?",
    description: "Размер можно изменить позже в профиле.",
  },
};

function editableValues(
  preferences: NonNullable<ReturnType<typeof usePatientPreferences>["data"]>,
  fullName: string,
): EditablePreferences {
  // На первом проходе имя по умолчанию — имя из ФИО, а не полное ФИО (кадр 05).
  const firstRun = !preferences.onboarding_completed_at;
  const suggested = firstRun && preferences.display_name === fullName && fullName.trim();
  return {
    display_name: suggested ? defaultDisplayName(fullName) : preferences.display_name,
    reminder_time: preferences.reminder_time,
    mood_palette: preferences.mood_palette,
    story_mode: preferences.story_mode,
    text_size: preferences.text_size,
  };
}

export function OnboardingPage({
  session,
  requestedStep,
}: {
  session: AccessiblePatientSession;
  requestedStep?: OnboardingStepId;
}) {
  const ownerId = patientOwnerId(session);
  const fullName = session.status === "online" ? session.user.full_name : "";
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const preferences = usePatientPreferences(session);
  const prescriptions = usePatientPrescriptions(session, session.status === "online");
  const serverValues = preferences.data
    ? {
        display_name: preferences.data.display_name,
        reminder_time: preferences.data.reminder_time,
        mood_palette: preferences.data.mood_palette,
        story_mode: preferences.data.story_mode,
        text_size: preferences.data.text_size,
      }
    : null;
  const [draftValues, setDraftValues] = useState<EditablePreferences | null>(
    preferences.data ? editableValues(preferences.data, fullName) : null,
  );
  const [pillDrafts, setPillDrafts] = useState<Record<string, PillDraft>>({});
  const pills = usePillAppearanceSaver(ownerId);
  const complete = useMutation({
    mutationFn: (values: EditablePreferences) =>
      savePatientPreferences(
        ownerId,
        preferences.data?.revision ?? 0,
        changedPreferences(serverValues ?? values, values),
        true,
      ),
    onSuccess: async (saved) => {
      queryClient.setQueryData(patientPreferencesQueryKey(ownerId), saved);
      await navigate({ to: "/", replace: true });
    },
  });
  useApplyPatientPreferences(
    preferences.data && draftValues ? { ...preferences.data, ...draftValues } : preferences.data,
  );

  if (preferences.isPending || !preferences.data || !draftValues || !serverValues) {
    return <OnboardingLoading error={preferences.error} retry={() => void preferences.refetch()} />;
  }
  const initial = serverValues;
  const draft = draftValues;
  const hasActivePrescription = prescriptions.data?.length ? prescriptions.data.length > 0 : false;
  const steps = getOnboardingSteps(hasActivePrescription);
  // Шаг живёт в адресе (`?step=…`): «назад» браузера и обновление страницы его сохраняют.
  const step = resolveOnboardingStep(requestedStep, steps);
  const stepIndex = steps.indexOf(step);
  const completed = Boolean(preferences.data.onboarding_completed_at);

  function setStep(next: OnboardingStepId): void {
    void navigate({ to: "/onboarding", search: next === "welcome" ? {} : { step: next } });
  }

  if (prescriptions.isError && step === "pills") {
    return (
      <OnboardingFrame onBack={() => setStep("story")}>
        <h1 className="text-2xl font-semibold">Не удалось загрузить назначения</h1>
        <p className="text-ink-muted">
          Повторите загрузку. Ошибка не означает, что назначений нет.
        </p>
        <Button
          variant="secondary"
          onClick={() => void prescriptions.refetch()}
          disabled={prescriptions.isFetching}
        >
          Повторить
        </Button>
      </OnboardingFrame>
    );
  }

  const prescriptionItems = prescriptions.data ?? [];
  const pillsReady = prescriptionItems.every((item) =>
    pillDraftReady(pillDrafts[item.prescription_id] ?? pillDraftFor(item)),
  );
  const canAdvance =
    (step !== "name" || Boolean(draft.display_name.trim())) && (step !== "pills" || pillsReady);
  const busy = complete.isPending || pills.pending;
  const prescriptionLoadFailed = prescriptions.isError && prescriptions.data === undefined;

  function setField<K extends keyof EditablePreferences>(
    field: K,
    value: EditablePreferences[K],
  ): void {
    setDraftValues((current) => (current ? { ...current, [field]: value } : current));
  }

  function finish(values: EditablePreferences): void {
    if (complete.isPending) return;
    complete.mutate(values);
  }

  function skipCurrent(): void {
    const result = skipOnboardingStep({
      currentStep: step,
      steps,
      initialValues: initial,
      draftValues: draft,
      onboardingCompleted: completed,
    });
    setDraftValues(result.values);
    if (step === "pills") setPillDrafts({});
    if (result.kind === "complete") finish(result.values);
    else setStep(result.step);
  }

  async function advance(): Promise<void> {
    if (step === "done") {
      finish(draft);
      return;
    }
    if (step === "pills" && !(await pills.saveAll(prescriptionItems, pillDrafts))) return;
    const next = steps[stepIndex + 1];
    if (next) setStep(next);
  }

  function goBack(): void {
    const previous = steps[stepIndex - 1];
    if (previous) setStep(previous);
  }

  const fieldForStep = fields[step];
  const text = screenText[step];
  const content =
    step === "welcome" ? (
      <div className="flex flex-1 flex-col gap-3">
        <h1 className="text-[30px] font-semibold leading-9 tracking-tight">
          Добро пожаловать в Штиль
        </h1>
        <p className="text-[17px] leading-6 text-ink-muted">
          Короткая отметка раз в день, и врач видит, как идёт лечение. Сначала настроим всё под вас,
          это займёт минуту.
        </p>
        {prescriptionLoadFailed && (
          <Alert tone="error">
            <span className="block">
              Не удалось загрузить назначения. Это не означает, что действующих назначений нет.
            </span>
            <Button
              className="mt-3"
              variant="secondary"
              onClick={() => void prescriptions.refetch()}
              disabled={prescriptions.isFetching}
            >
              Повторить загрузку назначений
            </Button>
          </Alert>
        )}
        <div className="mt-auto pt-6">
          <Button
            className="w-full rounded-full"
            onClick={() => void advance()}
            disabled={prescriptions.isPending || prescriptionLoadFailed}
          >
            {prescriptions.isPending ? "Загружаем…" : "Начать"}
          </Button>
        </div>
      </div>
    ) : step === "done" ? (
      <OnboardingDone
        draft={draft}
        timezone={preferences.data.timezone}
        prescriptions={prescriptionItems}
        busy={busy || pills.failed}
        saving={complete.isPending}
        onOpenToday={() => void advance()}
      />
    ) : (
      <div className="flex flex-col gap-5">
        <div>
          {text && (
            <p className="text-[13px] font-medium leading-[18px] text-primary-text">
              {text.eyebrow}
            </p>
          )}
          <h1 className="mt-2 text-2xl font-semibold leading-[30px] tracking-tight">
            {text?.title}
          </h1>
          <p className="mt-2 text-[15px] leading-5 text-ink-muted">{text?.description}</p>
        </div>
        {step === "pills" ? (
          prescriptions.isPending ? (
            <p role="status" className="text-sm text-ink-muted">
              Загружаем назначения…
            </p>
          ) : (
            <PillAppearanceEditor
              items={prescriptionItems}
              drafts={pillDrafts}
              states={pills.states}
              disabled={session.status !== "online" || pills.pending}
              onChange={(id, value) => setPillDrafts((current) => ({ ...current, [id]: value }))}
            />
          )
        ) : (
          fieldForStep && (
            <PreferenceField
              field={fieldForStep}
              values={draft}
              fullName={step === "name" ? fullName : undefined}
              onChange={(field, value) => setField(field, value)}
            />
          )
        )}
      </div>
    );

  return (
    <OnboardingFrame
      onBack={stepIndex > 0 ? goBack : undefined}
      completed={completed}
      scene={step === "welcome" ? "sunrise" : step === "done" ? "sunset" : undefined}
      stepNumber={stepIndex + 1}
      stepCount={steps.length}
      stepIds={steps}
      onSkip={step === "done" ? undefined : skipCurrent}
      skipDisabled={busy}
    >
      {complete.isError && (
        <Alert tone="error">
          {complete.error instanceof Error &&
          "status" in complete.error &&
          complete.error.status === 409
            ? "Настройки изменились на другом устройстве. Перечитайте значения и сохраните выбор ещё раз."
            : "Не удалось сохранить настройки. Проверьте подключение и повторите."}
        </Alert>
      )}
      {content}
      {step !== "welcome" && step !== "done" && (
        <div className="mt-2 flex flex-col gap-2">
          <Button onClick={() => void advance()} disabled={!canAdvance || busy}>
            {pills.pending ? "Сохраняем…" : "Дальше"}
          </Button>
          <Button variant="secondary" onClick={skipCurrent} disabled={busy}>
            Пропустить
          </Button>
        </div>
      )}
      {complete.isError && (
        <Button
          variant="secondary"
          onClick={() => void preferences.refetch()}
          disabled={preferences.isFetching}
        >
          Перечитать настройки
        </Button>
      )}
    </OnboardingFrame>
  );
}
